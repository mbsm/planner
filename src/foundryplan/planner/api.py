from __future__ import annotations

from datetime import date

from foundryplan.data.repository_views import PlannerRepository
from foundryplan.planner.extract import prepare_planner_inputs
from foundryplan.planner.model import PlannerOrder, PlannerPart
from foundryplan.planner.persist import save_schedule_result, delete_old_schedule_results
from foundryplan.planner.solve import solve_planner_heuristic


def prepare_and_sync(
    repo: PlannerRepository,
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
    repo: PlannerRepository,
    *,
    asof_date: date,
    scenario_name: str = "default",
    horizon_days: int | None = None,
    horizon_buffer_days: int = 10,
) -> dict:
    """Run planner using the heuristic engine only.
    
    If horizon_days is None, calculates suggested horizon based on last due_date.
    """
    scenario_id = repo.ensure_planner_scenario(name=scenario_name)

    # Ensure inputs are present
    prepare_planner_inputs(repo, scenario_name=scenario_name, asof_date=asof_date, horizon_buffer_days=horizon_buffer_days)

    orders_rows = repo.get_planner_orders_rows(scenario_id=scenario_id)
    parts_rows = repo.get_planner_parts_rows(scenario_id=scenario_id)
    calendar_rows = repo.get_planner_calendar_rows(scenario_id=scenario_id)
    progress_rows = repo.get_planner_initial_order_progress_rows(scenario_id=scenario_id, asof_date=asof_date)
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
            flask_type=str(r.get("flask_type") or "").upper(),
            cool_hours=float(r["cool_hours"] or 0.0),
            finish_days=int(r.get("finish_days") or 0),  # Almacenado directamente como días
            min_finish_days=int(r.get("min_finish_days") or 0),  # Almacenado directamente como días
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

    initial_patterns_loaded = {
        str(r["order_id"]) for r in patterns_rows if int(r.get("is_loaded") or 0) == 1
    }

    # Daily resources (single source of truth)
    daily_rows = repo.get_daily_resources_rows(scenario_id=scenario_id)
    day_to_idx = {d.isoformat(): idx for idx, d in enumerate(workdays)}
    daily_resources: dict[int, dict] = {}
    for row in daily_rows:
        idx = day_to_idx.get(str(row.get("day") or ""))
        if idx is None or idx >= len(workdays):
            continue
        res_day = daily_resources.setdefault(
            idx,
            {
                "molding_capacity": 0,
                "same_mold_capacity": 0,
                "pouring_tons_available": 0.0,
                "flask_available": {},
            },
        )
        res_day["molding_capacity"] = int(row.get("molding_capacity_per_day") or res_day["molding_capacity"])
        res_day["same_mold_capacity"] = int(row.get("same_mold_capacity_per_day") or res_day["same_mold_capacity"])
        res_day["pouring_tons_available"] = float(row.get("pouring_tons_available") or res_day["pouring_tons_available"])
        flask_type = str(row.get("flask_type") or "").upper()
        if flask_type:
            res_day["flask_available"][flask_type] = int(row.get("available_qty") or 0)

    # Heuristic-only mode (solver removed by request)
    # Build result wrapper with suggested horizon info
    suggested_horizon = calculate_suggested_horizon(orders_rows, [date.fromisoformat(r["date"]) for r in calendar_rows])
    
    result_base = {
        "suggested_horizon_days": suggested_horizon,
        "actual_horizon_days": len(workdays),
    }
    
    pour_lag_days = res.get("pour_lag_days")
    shakeout_lag_days = res.get("shakeout_lag_days")
    max_placement_search_days = res.get("max_placement_search_days")
    allow_molding_gaps = res.get("allow_molding_gaps")

    result = solve_planner_heuristic(
        orders=orders,
        parts=parts,
        workdays=workdays,
        daily_resources=daily_resources,
        initial_patterns_loaded=initial_patterns_loaded,
        max_horizon_days=len(workdays),
        pour_lag_days=int(pour_lag_days) if pour_lag_days is not None else 1,
        shakeout_lag_days=int(shakeout_lag_days) if shakeout_lag_days is not None else 1,
        max_placement_search_days=int(max_placement_search_days) if max_placement_search_days is not None else 365,
        allow_molding_gaps=bool(allow_molding_gaps) if allow_molding_gaps is not None else False,
    )
    
    # Persist result to database
    full_result = {**result_base, **result}
    save_schedule_result(
        repo._repo.db,
        scenario_id=scenario_id,
        asof_date=asof_date.isoformat(),
        result=full_result,
    )
    
    # Clean up old results (keep last 10)
    delete_old_schedule_results(repo._repo.db, scenario_id=scenario_id, keep_last_n=10)
    
    return full_result


def build_weekly_view(
    molds_schedule: dict[str, dict[int, int]] | None,
    workdays: list[date],
    orders_rows: list[dict],
    parts: dict[str, object],
    initial_flask_inuse: list[dict] | None = None,
    initial_pour_load: list[dict] | None = None,
) -> dict:

    suggested_horizon = calculate_suggested_horizon(orders_rows, [date.fromisoformat(r["date"]) for r in calendar_rows])
    result_base = {
        "suggested_horizon_days": suggested_horizon,
        "actual_horizon_days": len(workdays),
    }

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
    order_due_week: dict[str, int] = {}
    for order_row in orders_rows:
        order_id = str(order_row.get("order_id", ""))
        due_date_str = str(order_row.get("due_date") or "")
        
        try:
            due_date = date.fromisoformat(due_date_str)
            # Find which week this due_date falls in
            for day_idx, d in enumerate(workdays):
                if d >= due_date:
                    week_idx = day_to_week_idx.get(day_idx, 0)
                    order_due_week[order_id] = week_idx
                    break
            else:
                # Due date after all workdays
                order_due_week[order_id] = max(day_to_week_idx.values()) if day_to_week_idx else 0
        except Exception:
            pass
    
    # Calculate weekly totals
    weekly_totals: dict[int, dict] = {}
    for w_idx in sorted(week_dates.keys()):
        total_molds = 0
        total_tons = 0.0
        flask_util: dict[str, int] = {}

        # --- Initial Conditions: Pouring (Metal Throughput) ---
        if initial_pour_load:
            # Find range of day indices for this week
            week_days = [d_idx for d_idx, w in day_to_week_idx.items() if w == w_idx]
            if week_days:
                min_idx, max_idx = min(week_days), max(week_days)
                for r in initial_pour_load:
                    idx = int(r.get("workday_index") or -1)
                    if min_idx <= idx <= max_idx:
                        total_tons += float(r.get("tons_committed") or 0.0)

        # --- Initial Conditions: Flasks (Demolding / Occupancy) ---
        if initial_flask_inuse:
             week_days = [d_idx for d_idx, w in day_to_week_idx.items() if w == w_idx]
             if week_days:
                min_idx, max_idx = min(week_days), max(week_days)
                max_busy_week: dict[str, int] = {}
                
                # Check daily occupancy from initial conditions within this week
                for d in range(min_idx, max_idx + 1):
                    daily_busy: dict[str, int] = {}
                    for r in initial_flask_inuse:
                        release = int(r.get("release_workday_index") or 0)
                        # Flask is busy IF current day < release day
                        if d < release:
                            s = str(r.get("flask_type") or r.get("flask_size") or "").upper()
                            q = int(r.get("qty_inuse") or 0)
                            if s:
                                daily_busy[s] = daily_busy.get(s, 0) + q
                    
                    for s, q in daily_busy.items():
                        max_busy_week[s] = max(max_busy_week.get(s, 0), q)
                
                for s, q in max_busy_week.items():
                    flask_util[s] = flask_util.get(s, 0) + q
        
        # --- Planned Production ---
        for order_id, week_map in weekly_molds.items():
            qty_molds = week_map.get(w_idx, 0)
            if qty_molds > 0:
                total_molds += qty_molds
                
                # Find part for this order to get weight
                part_obj = None
                for ord_row in orders_rows:
                    if str(ord_row.get("order_id", "")) == order_id:
                        part_id = str(ord_row.get("part_id", ""))
                        part_obj = parts.get(part_id)
                        break
                
                if part_obj and hasattr(part_obj, 'net_weight_ton') and hasattr(part_obj, 'pieces_per_mold'):
                    tons_per_mold = float(part_obj.net_weight_ton or 0) * float(part_obj.pieces_per_mold or 0)
                    total_tons += tons_per_mold * qty_molds
                
                # Flask utilization
                if part_obj and hasattr(part_obj, 'flask_type'):
                    flask_type = str(part_obj.flask_type or "").upper()
                    if flask_type:
                        flask_util[flask_type] = flask_util.get(flask_type, 0) + qty_molds
        
        weekly_totals[w_idx] = {
            "molds": total_molds,
            "tons": round(total_tons, 2),
            "flask_util": flask_util,
        }
    
    return {
        "weeks": weeks,
        "weekly_molds": weekly_molds,
        "weekly_totals": weekly_totals,
        "order_completion": order_completion,
        "order_due_week": order_due_week,
    }


def build_orders_plan_summary(
    plan_result: dict,
    workdays: list[date],
    orders_rows: list[dict],
    parts: dict[str, object],
) -> list[dict]:
    """Build order planning summary with delivery dates and finish days reduction.
    
    Args:
        plan_result: Output from run_planner with completion_days, finish_days, late_days
        workdays: List of workday dates (indexed)
        orders_rows: Order metadata from DB
        parts: {part_id: PlannerPart} with finish_days and min_finish_days
    
    Returns:
        List of dicts with:
        {
            "order_id": str,
            "due_date": date,
            "completion_date": date or None,
            "planned_delivery_date": date or None,
            "finish_days_nominal": int,
            "finish_days_real": int,
            "finish_reduction_days": int,
            "late_days": int,
            "status": "A tiempo" or "Atrasado",
        }
    """
    result = []
    
    completion_days = plan_result.get("completion_days") or {}
    finish_days_map = plan_result.get("finish_days") or {}
    late_days_map = plan_result.get("late_days") or {}
    
    for order_row in orders_rows:
        order_id = str(order_row.get("order_id", ""))
        part_id = str(order_row.get("part_id", ""))
        due_date_str = str(order_row.get("due_date") or "")
        
        try:
            due_date = date.fromisoformat(due_date_str)
        except Exception:
            continue
        
        # Get part info for nominal finish days
        part_obj = parts.get(part_id)
        finish_days_nominal = int(getattr(part_obj, "finish_days", 0)) if part_obj else 0
        finish_days_real = int(finish_days_map.get(order_id, finish_days_nominal))
        finish_reduction = finish_days_nominal - finish_days_real
        
        # Get completion date from completion_day index
        completion_day_idx = completion_days.get(order_id)
        completion_date = None
        if completion_day_idx is not None and completion_day_idx < len(workdays):
            # Completion day is when last molds are finished; delivery is 1 workday later
            if completion_day_idx + 1 < len(workdays):
                completion_date = workdays[completion_day_idx + 1]
            else:
                completion_date = workdays[completion_day_idx]
        
        late_days = int(late_days_map.get(order_id, 0))
        status = "A tiempo" if late_days == 0 else "Atrasado"
        
        result.append({
            "order_id": order_id,
            "due_date": due_date,
            "completion_date": completion_date,
            "planned_delivery_date": completion_date,
            "finish_days_nominal": finish_days_nominal,
            "finish_days_real": finish_days_real,
            "finish_reduction_days": finish_reduction,
            "late_days": late_days,
            "status": status,
        })
    
    return sorted(result, key=lambda x: x["order_id"])

