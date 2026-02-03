from __future__ import annotations

from datetime import date

from foundryplan.data.repository import Repository
from foundryplan.planner.extract import prepare_planner_inputs
from foundryplan.planner.model import PlannerOrder, PlannerPart, PlannerResource
from foundryplan.planner.solve import solve_planner, solve_planner_heuristic


def prepare_and_sync(
    repo: Repository,
    *,
    asof_date: date,
    scenario_name: str = "default",
    horizon_buffer_days: int = 10,
) -> dict:
    """Prepare planner inputs and return summary counts."""
    return prepare_planner_inputs(
        repo,
        scenario_name=scenario_name,
        asof_date=asof_date,
        horizon_buffer_days=horizon_buffer_days,
    )


def calculate_suggested_horizon(orders_rows: list[dict], workdays: list[date]) -> int | None:
    """Calculate suggested horizon based on last due date.
    
    Returns the index of workday that contains the last due_date + 5 days buffer.
    If no due dates found, returns None (use full calendar).
    """
    if not orders_rows or not workdays:
        return None
    
    due_dates = []
    for r in orders_rows:
        due_str = str(r.get("due_date") or "").strip()
        if due_str:
            try:
                due_dates.append(date.fromisoformat(due_str))
            except (ValueError, TypeError):
                continue
    
    if not due_dates:
        return None
    
    last_due = max(due_dates)
    # Find index of workday >= last_due + 5 days buffer
    buffer_date = last_due  # You can add buffer here if needed
    
    for idx, wd in enumerate(workdays):
        if wd >= buffer_date:
            # Add 10% buffer beyond this date
            return min(len(workdays) - 1, idx + max(1, int(idx * 0.1)))
    
    return len(workdays)  # All workdays needed


def run_planner(
    repo: Repository,
    *,
    asof_date: date,
    scenario_name: str = "default",
    method: str = "solver",
    horizon_days: int | None = None,
    horizon_buffer_days: int = 10,
) -> dict:
    """Run planner using heuristic, solver, or combined method.
    
    If horizon_days is None, calculates suggested horizon based on last due_date.
    """
    scenario_id = repo.ensure_planner_scenario(name=scenario_name)

    # Ensure inputs are present
    prepare_planner_inputs(repo, scenario_name=scenario_name, asof_date=asof_date, horizon_buffer_days=horizon_buffer_days)

    orders_rows = repo.get_planner_orders_rows(scenario_id=scenario_id)
    parts_rows = repo.get_planner_parts_rows(scenario_id=scenario_id)
    calendar_rows = repo.get_planner_calendar_rows(scenario_id=scenario_id)
    progress_rows = repo.get_planner_initial_order_progress_rows(scenario_id=scenario_id, asof_date=asof_date)
    flask_rows = repo.get_planner_initial_flask_inuse_rows(scenario_id=scenario_id, asof_date=asof_date)
    pour_rows = repo.get_planner_initial_pour_load_rows(scenario_id=scenario_id, asof_date=asof_date)
    patterns_rows = repo.get_planner_initial_patterns_loaded(scenario_id=scenario_id, asof_date=asof_date)
    res = repo.get_planner_resources(scenario_id=scenario_id)
    if not res:
        raise ValueError("Config planner_resources faltante")

    workdays = [date.fromisoformat(r["date"]) for r in calendar_rows]
    
    # Auto-calculate horizon if not provided
    if horizon_days is None:
        suggested = calculate_suggested_horizon(orders_rows, workdays)
        horizon_days = suggested if suggested is not None else len(workdays)
    
    if horizon_days and horizon_days > 0:
        workdays = workdays[: int(horizon_days)]

    parts = {
        r["part_id"]: PlannerPart(
            part_id=str(r["part_id"]),
            flask_size=str(r["flask_size"] or "").upper(),
            cool_hours=float(r["cool_hours"] or 0.0),
            finish_hours=float(r["finish_hours"] or 0.0),
            min_finish_hours=float(r["min_finish_hours"] or 0.0),
            pieces_per_mold=float(r["pieces_per_mold"] or 0.0),
            net_weight_ton=float(r["net_weight_ton"] or 0.0),
            alloy=str(r["alloy"]) if r.get("alloy") is not None else None,
        )
        for r in parts_rows
    }

    remaining_map = {str(r["order_id"]): int(r["remaining_molds"] or 0) for r in progress_rows}
    orders: list[PlannerOrder] = []
    for r in orders_rows:
        order_id = str(r["order_id"])
        part_id = str(r["part_id"])
        qty_raw = int(r["qty"] or 0)
        remaining = remaining_map.get(order_id)
        if remaining is None:
            part = parts.get(part_id)
            if part and part.pieces_per_mold > 0:
                remaining = int((qty_raw + part.pieces_per_mold - 1) // part.pieces_per_mold)
            else:
                remaining = qty_raw
        orders.append(
            PlannerOrder(
                order_id=order_id,
                part_id=part_id,
                qty=int(remaining or 0),
                due_date=str(r["due_date"] or ""),
                priority=int(r["priority"] or 0),
            )
        )

    resources = PlannerResource(
        flasks_S=int(res.get("flasks_S") or 0),
        flasks_M=int(res.get("flasks_M") or 0),
        flasks_L=int(res.get("flasks_L") or 0),
        molding_max_per_day=int(res.get("molding_max_per_day") or 0),
        molding_max_same_part_per_day=int(res.get("molding_max_same_part_per_day") or 0),
        pour_max_ton_per_day=float(res.get("pour_max_ton_per_day") or 0.0),
    )

    initial_pour_load = {int(r["workday_index"]): float(r["tons_committed"] or 0.0) for r in pour_rows}

    # Convert release_day rows into busy-by-day map
    initial_flask_busy: dict[tuple[str, int], int] = {}
    for r in flask_rows:
        size = str(r["flask_size"] or "").upper()
        release_idx = int(r["release_workday_index"] or 0)
        qty = int(r["qty_inuse"] or 0)
        for d in range(min(release_idx, len(workdays))):
            key = (size, d)
            initial_flask_busy[key] = initial_flask_busy.get(key, 0) + qty

    initial_patterns_loaded = {
        str(r["order_id"]) for r in patterns_rows if int(r.get("is_loaded") or 0) == 1
    }

    weights = {
        "late_days": float(repo.get_config(key="planner_weight_late_days", default="1000") or 1000),
        "finish_reduction": float(repo.get_config(key="planner_weight_finish_reduction", default="50") or 50),
        "pattern_changes": float(repo.get_config(key="planner_weight_pattern_changes", default="100") or 100),
    }

    solver_params = {
        "num_search_workers": int(repo.get_config(key="planner_solver_num_workers", default="0") or 0),
        "relative_gap_limit": float(repo.get_config(key="planner_solver_relative_gap", default="0.01") or 0.01),
        "log_search_progress": str(repo.get_config(key="planner_solver_log_progress", default="0") or "0").strip() == "1",
    }

    time_limit = int(repo.get_config(key="planner_solver_time_limit", default="30") or 30)

    method = str(method or "solver").strip().lower()
    
    # Build result wrapper with suggested horizon info
    suggested_horizon = calculate_suggested_horizon(orders_rows, [date.fromisoformat(r["date"]) for r in calendar_rows])
    
    result_base = {
        "suggested_horizon_days": suggested_horizon,
        "actual_horizon_days": len(workdays),
    }
    
    if method == "heuristico":
        result = solve_planner_heuristic(
            orders=orders,
            parts=parts,
            resources=resources,
            workdays=workdays,
            initial_flask_inuse=initial_flask_busy,
            initial_pour_load=initial_pour_load,
            initial_patterns_loaded=initial_patterns_loaded,
            max_horizon_days=len(workdays),
        )
        return {**result_base, **result}
    if method == "combinado":
        heur = solve_planner_heuristic(
            orders=orders,
            parts=parts,
            resources=resources,
            workdays=workdays,
            initial_flask_inuse=initial_flask_busy,
            initial_pour_load=initial_pour_load,
            initial_patterns_loaded=initial_patterns_loaded,
            max_horizon_days=len(workdays),
        )
        hints: dict[tuple[str, int], int] = {}
        for oid, day_map in (heur.get("molds_schedule") or {}).items():
            for day_idx, qty in (day_map or {}).items():
                hints[(str(oid), int(day_idx))] = int(qty)
        result = solve_planner(
            orders=orders,
            parts=parts,
            resources=resources,
            workdays=workdays,
            initial_flask_inuse=initial_flask_busy,
            initial_pour_load=initial_pour_load,
            initial_patterns_loaded=initial_patterns_loaded,
            weights=weights,
            time_limit_seconds=time_limit,
            solver_params=solver_params,
            hints=hints,
        )
        return {**result_base, **result}

    result = solve_planner(
        orders=orders,
        parts=parts,
        resources=resources,
        workdays=workdays,
        initial_flask_inuse=initial_flask_busy,
        initial_pour_load=initial_pour_load,
        initial_patterns_loaded=initial_patterns_loaded,
        weights=weights,
        time_limit_seconds=time_limit,
        solver_params=solver_params,
    )
    return {**result_base, **result}
