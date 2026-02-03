from __future__ import annotations

from datetime import date
from ortools.sat.python import cp_model

from foundryplan.planner.model import PlannerOrder, PlannerPart, PlannerResource


def solve_planner(
    *,
    orders: list[PlannerOrder],
    parts: dict[str, PlannerPart],
    resources: PlannerResource,
    workdays: list[date],
    initial_flask_inuse: dict[tuple[str, int], int],
    initial_pour_load: dict[int, float],
    initial_patterns_loaded: set[str],
    weights: dict[str, float] | None = None,
    time_limit_seconds: int = 30,
    solver_params: dict[str, float | int | bool] | None = None,
) -> dict:
    """Solve molding plan using OR-Tools CP-SAT.
    
    Args:
        orders: List of orders to schedule (with remaining_molds)
        parts: Part metadata by part_id
        resources: Molding/pouring/flask capacities
        workdays: List of workday dates (indexed 0..N-1)
        initial_flask_inuse: {(flask_size, release_day): qty_busy}
        initial_pour_load: {workday_idx: tons_committed}
        initial_patterns_loaded: {order_id} of currently loaded patterns
        weights: Objective weights (late_days, finish_reduction, pattern_changes)
        time_limit_seconds: Solver time limit
        solver_params: Optional CP-SAT parameters (num_search_workers, relative_gap_limit, log_search_progress)
        
    Returns:
        {
            "status": str,
            "molds_schedule": {order_id: {day_idx: qty}},
            "finish_hours": {order_id: hours},
            "completion_days": {order_id: day_idx},
            "late_days": {order_id: days},
            "objective": float
        }
    """
    if weights is None:
        weights = {"late_days": 1000.0, "finish_reduction": 50.0, "pattern_changes": 100.0}
    
    model = cp_model.CpModel()
    horizon = len(workdays)
    
    # Variables: molds[order_id, day_idx]
    molds = {}
    for order in orders:
        part = parts.get(order.part_id)
        if part is None:
            continue
        for d in range(horizon):
            molds[(order.order_id, d)] = model.NewIntVar(0, order.qty, f"molds_{order.order_id}_{d}")
    
    # Variables: finish_hours_real[order_id]
    finish_hours_real = {}
    for order in orders:
        part = parts.get(order.part_id)
        if part is None:
            continue
        min_h = int(part.min_finish_hours)
        nom_h = int(part.finish_hours)
        finish_hours_real[order.order_id] = model.NewIntVar(min_h, nom_h, f"finish_{order.order_id}")
    
    # Variables: completion_day[order_id] (last day with molds > 0)
    completion_day = {}
    for order in orders:
        if order.part_id not in parts:
            continue
        completion_day[order.order_id] = model.NewIntVar(0, horizon - 1, f"comp_{order.order_id}")
    
    # Variables: late_days[order_id] (max(0, completion - due_day))
    late_days = {}
    for order in orders:
        part = parts.get(order.part_id)
        if part is None or order.due_day is None:
            continue
        late_days[order.order_id] = model.NewIntVar(0, horizon, f"late_{order.order_id}")
    
    # Variables: pattern_active[order_id, day_idx] (binary: pattern loaded on day d)
    pattern_active = {}
    for order in orders:
        if order.part_id not in parts:
            continue
        for d in range(horizon):
            pattern_active[(order.order_id, d)] = model.NewBoolVar(f"pat_{order.order_id}_{d}")
    
    # Constraints: coverage (all remaining molds must be scheduled)
    for order in orders:
        part = parts.get(order.part_id)
        if part is None:
            continue
        model.Add(sum(molds.get((order.order_id, d), 0) for d in range(horizon)) == order.qty)
    
    # Constraints: molding capacity per day
    for d in range(horizon):
        model.Add(
            sum(molds.get((order.order_id, d), 0) for order in orders if order.part_id in parts)
            <= resources.molding_max_per_day
        )
    
    # Constraints: molding capacity per part per day
    for order in orders:
        part = parts.get(order.part_id)
        if part is None:
            continue
        for d in range(horizon):
            model.Add(molds[(order.order_id, d)] <= resources.molding_max_same_part_per_day)
    
    # Constraints: pouring capacity (metal per day)
    for d in range(horizon):
        initial_committed = initial_pour_load.get(d, 0.0)
        # Metal = sum(molds * net_weight * pieces_per_mold)
        # Linearize by scaling to integers (tons * 1000 = kg)
        metal_kg = []
        for order in orders:
            part = parts.get(order.part_id)
            if part is None:
                continue
            metal_per_mold_kg = int(part.net_weight_ton * part.pieces_per_mold * 1000)
            metal_kg.append(molds[(order.order_id, d)] * metal_per_mold_kg)
        
        max_pour_kg = int((resources.pour_max_ton_per_day - initial_committed) * 1000)
        if metal_kg:
            model.Add(sum(metal_kg) <= max_pour_kg)
    
    # Constraints: flask availability (CRITICAL - plant bottleneck)
    # Group orders by flask_size
    orders_by_flask = {}
    for order in orders:
        part = parts.get(order.part_id)
        if part is None:
            continue
        flask_size = part.flask_size
        if flask_size not in orders_by_flask:
            orders_by_flask[flask_size] = []
        orders_by_flask[flask_size].append(order)
    
    for flask_size, flask_orders in orders_by_flask.items():
        max_flasks = resources.flask_inventory.get(flask_size, 0)
        
        for d in range(horizon):
            # Flasks busy on day d = initial_busy[d] + sum(new molds still cooling)
            initial_busy = initial_flask_inuse.get((flask_size, d), 0)
            
            # For each order, count molds that are cooling on day d
            cooling_terms = []
            for order in flask_orders:
                part = parts[order.part_id]
                # Cooling days = ceil(finish_hours_real / 24)
                # Linearize: cooling_days_int >= finish_hours / 24
                cooling_days = model.NewIntVar(0, horizon, f"cool_{order.order_id}")
                # cooling_days * 24 >= finish_hours_real
                model.Add(cooling_days * 24 >= finish_hours_real[order.order_id])
                
                # Molds poured on day p are busy on days [p, p+cooling_days)
                for p in range(horizon):
                    if p <= d:
                        # If poured on day p, busy until day p + cooling_days - 1
                        # Use indicator: molds_busy[p,d] = molds[p] if d < p + cooling_days else 0
                        busy = model.NewIntVar(0, order.qty, f"busy_{order.order_id}_{p}_{d}")
                        # busy = molds[p] if d - p < cooling_days else 0
                        # Linearize with boolean: is_cooling = (d - p < cooling_days)
                        is_cooling = model.NewBoolVar(f"is_cool_{order.order_id}_{p}_{d}")
                        model.Add(cooling_days > d - p).OnlyEnforceIf(is_cooling)
                        model.Add(cooling_days <= d - p).OnlyEnforceIf(is_cooling.Not())
                        model.Add(busy == molds[(order.order_id, p)]).OnlyEnforceIf(is_cooling)
                        model.Add(busy == 0).OnlyEnforceIf(is_cooling.Not())
                        cooling_terms.append(busy)
            
            total_busy = initial_busy + sum(cooling_terms) if cooling_terms else initial_busy
            model.Add(total_busy <= max_flasks)
    
    # Constraints: completion_day is the last day with molds > 0
    for order in orders:
        if order.part_id not in parts:
            continue
        # completion_day >= d if molds[o,d] > 0
        for d in range(horizon):
            has_molds = model.NewBoolVar(f"has_{order.order_id}_{d}")
            model.Add(molds[(order.order_id, d)] > 0).OnlyEnforceIf(has_molds)
            model.Add(molds[(order.order_id, d)] == 0).OnlyEnforceIf(has_molds.Not())
            model.Add(completion_day[order.order_id] >= d).OnlyEnforceIf(has_molds)
    
    # Constraints: late_days = max(0, completion_day - due_day)
    for order in orders:
        part = parts.get(order.part_id)
        if part is None or order.due_day is None:
            continue
        if order.due_day < horizon:
            model.Add(late_days[order.order_id] >= completion_day[order.order_id] - order.due_day)
            model.Add(late_days[order.order_id] >= 0)
    
    # Constraints: pattern_active[o,d] = 1 if molds[o,d] > 0
    for order in orders:
        if order.part_id not in parts:
            continue
        for d in range(horizon):
            model.Add(molds[(order.order_id, d)] > 0).OnlyEnforceIf(pattern_active[(order.order_id, d)])
            model.Add(molds[(order.order_id, d)] == 0).OnlyEnforceIf(pattern_active[(order.order_id, d)].Not())
    
    # Objective: minimize (w_late * late_days + w_finish * finish_reduction + w_pattern * pattern_changes)
    
    # 1. Late days penalty
    total_late_days = sum(late_days.values()) if late_days else 0
    
    # 2. Finish reduction penalty
    finish_reduction_terms = []
    for order in orders:
        part = parts.get(order.part_id)
        if part is None:
            continue
        nominal = int(part.finish_hours)
        reduction = model.NewIntVar(0, nominal, f"red_{order.order_id}")
        model.Add(reduction == nominal - finish_hours_real[order.order_id])
        finish_reduction_terms.append(reduction)
    total_reduction = sum(finish_reduction_terms) if finish_reduction_terms else 0
    
    # 3. Pattern changes penalty (count switches: pattern_active[o,d] && !pattern_active[o,d-1])
    pattern_switches = []
    for order in orders:
        if order.part_id not in parts:
            continue
        # First day: switch if active and not in initial_patterns_loaded
        if order.order_id not in initial_patterns_loaded:
            pattern_switches.append(pattern_active[(order.order_id, 0)])
        
        # Subsequent days: switch if active today and not active yesterday
        for d in range(1, horizon):
            switch = model.NewBoolVar(f"switch_{order.order_id}_{d}")
            # switch = pattern_active[o,d] && !pattern_active[o,d-1]
            model.AddBoolAnd([pattern_active[(order.order_id, d)], pattern_active[(order.order_id, d-1)].Not()]).OnlyEnforceIf(switch)
            model.AddBoolOr([pattern_active[(order.order_id, d)].Not(), pattern_active[(order.order_id, d-1)]]).OnlyEnforceIf(switch.Not())
            pattern_switches.append(switch)
    total_switches = sum(pattern_switches) if pattern_switches else 0
    
    # Weighted objective
    w_late = int(weights["late_days"])
    w_red = int(weights["finish_reduction"])
    w_switch = int(weights["pattern_changes"])
    
    model.Minimize(w_late * total_late_days + w_red * total_reduction + w_switch * total_switches)
    
    # Solve
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_seconds
    solver.parameters.log_search_progress = False
    if solver_params:
        if "num_search_workers" in solver_params:
            solver.parameters.num_search_workers = int(solver_params["num_search_workers"])
        if "relative_gap_limit" in solver_params:
            solver.parameters.relative_gap_limit = float(solver_params["relative_gap_limit"])
        if "log_search_progress" in solver_params:
            solver.parameters.log_search_progress = bool(solver_params["log_search_progress"])
    status = solver.Solve(model)
    
    # Extract solution
    result = {
        "status": solver.StatusName(status),
        "molds_schedule": {},
        "finish_hours": {},
        "completion_days": {},
        "late_days": {},
        "objective": solver.ObjectiveValue() if status in [cp_model.OPTIMAL, cp_model.FEASIBLE] else None,
    }
    
    if status in [cp_model.OPTIMAL, cp_model.FEASIBLE]:
        for order in orders:
            if order.part_id not in parts:
                continue
            schedule = {}
            for d in range(horizon):
                qty = solver.Value(molds[(order.order_id, d)])
                if qty > 0:
                    schedule[d] = qty
            if schedule:
                result["molds_schedule"][order.order_id] = schedule
            
            result["finish_hours"][order.order_id] = solver.Value(finish_hours_real[order.order_id])
            result["completion_days"][order.order_id] = solver.Value(completion_day[order.order_id])
            
            if order.order_id in late_days:
                result["late_days"][order.order_id] = solver.Value(late_days[order.order_id])
    
    return result
