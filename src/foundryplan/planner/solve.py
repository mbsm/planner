from __future__ import annotations

import math
from datetime import date
from ortools.sat.python import cp_model

from foundryplan.planner.model import PlannerOrder, PlannerPart, PlannerResource


def _build_due_day_map(workdays: list[date]) -> dict[str, int]:
    return {d.isoformat(): idx for idx, d in enumerate(workdays)}


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
    hints: dict[tuple[str, int], int] | None = None,
    finish_hours_hints: dict[str, int] | None = None,
) -> dict:
    """Solve molding plan using OR-Tools CP-SAT.
    
    Args:
        orders: List of orders to schedule (with remaining_molds)
        parts: Part metadata by part_id
        resources: Molding/pouring/flask capacities
        workdays: List of workday dates (indexed 0..N-1)
        initial_flask_inuse: {(flask_size, day_idx): qty_busy}
        initial_pour_load: {workday_idx: tons_committed}
        initial_patterns_loaded: {order_id} of currently loaded patterns
        weights: Objective weights (late_days, finish_reduction, pattern_changes)
        time_limit_seconds: Solver time limit
        solver_params: Optional CP-SAT parameters (num_search_workers, relative_gap_limit, log_search_progress)
        hints: Optional warm-start hints for molds (order_id, day_idx) -> qty
        finish_hours_hints: Optional warm-start hints for finish_hours (order_id -> hours)
        
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
    
    due_day_map = _build_due_day_map(workdays)

    # Variables: late_days[order_id] (max(0, completion - due_day))
    late_days = {}
    for order in orders:
        part = parts.get(order.part_id)
        due_day = due_day_map.get(str(order.due_date or "").strip())
        if part is None or due_day is None:
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

    # Hints (warm start)
    if hints:
        for (order_id, day_idx), qty in hints.items():
            var = molds.get((order_id, int(day_idx)))
            if var is not None:
                model.AddHint(var, int(qty))
    if finish_hours_hints:
        for order_id, hours in finish_hours_hints.items():
            var = finish_hours_real.get(order_id)
            if var is not None:
                model.AddHint(var, int(hours))
    
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
        size = str(flask_size or "").upper()
        if size == "S":
            max_flasks = int(resources.flasks_S)
        elif size == "M":
            max_flasks = int(resources.flasks_M)
        elif size == "L":
            max_flasks = int(resources.flasks_L)
        else:
            max_flasks = 0
        
        for d in range(horizon):
            # Flasks busy on day d = initial_busy[d] + sum(new molds still cooling)
            initial_busy = initial_flask_inuse.get((flask_size, d), 0)
            
            # For each order, count molds that are cooling on day d
            cooling_terms = []
            for order in flask_orders:
                part = parts[order.part_id]
                # Cooling days = ceil(cool_hours / 24)
                cool_days = int(math.ceil(float(part.cool_hours or 0.0) / 24.0))
                if cool_days <= 0:
                    cool_days = 1

                # Molds poured on day p are busy on days [p, p+cool_days)
                start_p = max(0, d - cool_days + 1)
                for p in range(start_p, d + 1):
                    cooling_terms.append(molds[(order.order_id, p)])
            
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
        due_day = due_day_map.get(str(order.due_date or "").strip())
        if part is None or due_day is None:
            continue
        if due_day < horizon:
            model.Add(late_days[order.order_id] >= completion_day[order.order_id] - due_day)
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


def solve_planner_heuristic(
    *,
    orders: list[PlannerOrder],
    parts: dict[str, PlannerPart],
    resources: PlannerResource,
    workdays: list[date],
    initial_flask_inuse: dict[tuple[str, int], int],
    initial_pour_load: dict[int, float],
    initial_patterns_loaded: set[str],
    max_horizon_days: int = 365,
) -> dict:
    """Greedy heuristic planner.
    
    Calculates start_by per order as:
      start_by = due_date - (
          ceil(remaining_molds / molding_max_same_part_per_day) +  # Molding time
          1 +                                                        # Pouring day
          ceil(cool_hours / 24) +                                   # Cooling days
          ceil(finish_hours / (8*24)) +                             # Finish time (workdays)
          ceil(workdays / 7 * 2)                                    # Weekend approximation (2 days per 7)
      )
    
    Then fills capacity day-by-day, prioritizing:
      1) orders with start_by <= current_day (overdue)
      2) currently loaded patterns
      3) priority (ASC)
      4) start_by (ASC)
    
    Ensures ALL orders are scheduled or raises ValueError if max_horizon exceeded.
    """
    horizon = len(workdays)
    if horizon > max_horizon_days:
        raise ValueError(f"Horizonte de {horizon} días excede máximo de {max_horizon_days}")

    due_day_map = _build_due_day_map(workdays)

    remaining = {o.order_id: int(o.qty or 0) for o in orders}
    schedule: dict[str, dict[int, int]] = {}
    errors: list[str] = []

    # Busy flasks per size/day (seeded by initial in-use)
    busy_by_day: dict[str, dict[int, int]] = {"S": {}, "M": {}, "L": {}}
    for (size, day_idx), qty in initial_flask_inuse.items():
        if size not in busy_by_day:
            busy_by_day[size] = {}
        busy_by_day[size][int(day_idx)] = busy_by_day[size].get(int(day_idx), 0) + int(qty)

    def _can_allocate_flasks(*, size: str, day: int, qty: int, cool_days: int, max_flasks: int) -> bool:
        for d in range(day, min(day + cool_days, horizon)):
            used = busy_by_day.get(size, {}).get(d, 0)
            if used + qty > max_flasks:
                return False
        return True

    def _reserve_flasks(*, size: str, day: int, qty: int, cool_days: int) -> None:
        for d in range(day, min(day + cool_days, horizon)):
            busy_by_day.setdefault(size, {})
            busy_by_day[size][d] = busy_by_day[size].get(d, 0) + qty

    def _calculate_start_by(order: PlannerOrder, part: PlannerPart) -> int:
        """Calculate start_by day index for order using resource capacity estimates."""
        remaining_molds = remaining.get(order.order_id, 0)
        if remaining_molds <= 0:
            return horizon + 999
        
        # Molding weeks: ceil(remaining_molds / molding_max_same_part_per_day)
        molding_days = math.ceil(remaining_molds / float(resources.molding_max_same_part_per_day or 1))
        
        # Pouring: 1 day
        pouring_days = 1
        
        # Cooling: ceil(cool_hours / 24)
        cooling_days = math.ceil(float(part.cool_hours or 0.0) / 24.0)
        if cooling_days <= 0:
            cooling_days = 1
        
        # Finishing: ceil(finish_hours / 8 / 8) -> days assuming 8h workday
        finishing_days = math.ceil(float(part.finish_hours or 0.0) / (8 * 8))
        
        # Total process days
        process_days = molding_days + pouring_days + cooling_days + finishing_days
        
        # Weekend approximation: +2 days per 7 days
        weekend_buffer = math.ceil(process_days / 7.0 * 2.0)
        total_days = process_days + weekend_buffer
        
        # Get due date index
        due_idx = due_day_map.get(str(order.due_date or "").strip(), horizon + 999)
        
        # start_by = due_date - total_days
        start_by = max(0, due_idx - int(total_days))
        return start_by

    # Pre-compute start_by for each order
    order_start_by: dict[str, int] = {}
    for order in orders:
        part = parts.get(order.part_id)
        if part is None:
            continue
        order_start_by[order.order_id] = _calculate_start_by(order, part)

    # Sort orders: overdue first, then loaded patterns, then by priority and start_by
    def _sort_key(o: PlannerOrder) -> tuple[int, int, int, int, str]:
        start_by = order_start_by.get(o.order_id, horizon + 999)
        priority = int(o.priority or 0)
        is_loaded = 0 if o.order_id in initial_patterns_loaded else 1
        return (
            1 if start_by <= 0 else 0,  # Overdue first (descending)
            is_loaded,                   # Loaded patterns second
            priority,                    # Priority ASC
            start_by,                    # Start_by ASC
            str(o.order_id),
        )

    ordered = sorted([o for o in orders if o.part_id in parts], key=_sort_key, reverse=True)

    # Iterate through days filling capacity
    for d in range(horizon):
        remaining_capacity = int(resources.molding_max_per_day)
        part_usage: dict[str, int] = {}
        pour_capacity_kg = int((resources.pour_max_ton_per_day - initial_pour_load.get(d, 0.0)) * 1000)
        if pour_capacity_kg < 0:
            pour_capacity_kg = 0

        for order in ordered:
            if remaining_capacity <= 0:
                break
            qty_left = remaining.get(order.order_id, 0)
            if qty_left <= 0:
                continue

            part = parts.get(order.part_id)
            if part is None:
                continue

            max_by_part = resources.molding_max_same_part_per_day - part_usage.get(order.part_id, 0)
            if max_by_part <= 0:
                continue

            metal_per_mold_kg = int(part.net_weight_ton * part.pieces_per_mold * 1000)
            if metal_per_mold_kg <= 0:
                continue
            max_by_pour = pour_capacity_kg // metal_per_mold_kg if metal_per_mold_kg > 0 else 0
            if max_by_pour <= 0:
                continue

            max_qty = min(qty_left, remaining_capacity, max_by_part, max_by_pour)
            if max_qty <= 0:
                continue

            size = str(part.flask_size or "").upper()
            max_flasks = getattr(resources, f"flasks_{size}", 0) if size in {"S", "M", "L"} else 0
            cool_days = int(math.ceil(float(part.cool_hours or 0.0) / 24.0))
            if cool_days <= 0:
                cool_days = 1

            # Reduce qty until flasks constraint satisfied
            qty = max_qty
            while qty > 0 and not _can_allocate_flasks(
                size=size,
                day=d,
                qty=qty,
                cool_days=cool_days,
                max_flasks=int(max_flasks),
            ):
                qty -= 1

            if qty <= 0:
                continue

            schedule.setdefault(order.order_id, {})[d] = qty
            remaining[order.order_id] = qty_left - qty
            remaining_capacity -= qty
            part_usage[order.part_id] = part_usage.get(order.part_id, 0) + qty
            pour_capacity_kg -= qty * metal_per_mold_kg
            _reserve_flasks(size=size, day=d, qty=qty, cool_days=cool_days)

    # Check if all orders scheduled
    for order_id, qty_left in remaining.items():
        if qty_left > 0:
            errors.append(f"Orden {order_id}: {qty_left} moldes sin schedular (horizonte insuficiente)")

    status = "HEURISTIC" if not errors else "HEURISTIC_INCOMPLETE"
    
    return {
        "status": status,
        "molds_schedule": schedule,
        "finish_hours": {},
        "completion_days": {},
        "late_days": {},
        "objective": None,
        "errors": errors,
        "horizon_exceeded": len(errors) > 0,
    }
