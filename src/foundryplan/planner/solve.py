from __future__ import annotations

import math
from datetime import date

from foundryplan.planner.model import PlannerOrder, PlannerPart, PlannerResource


def _build_due_day_map(workdays: list[date]) -> dict[str, int]:
    return {d.isoformat(): idx for idx, d in enumerate(workdays)}


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

    required_flasks = {parts[o.part_id].flask_type for o in orders if o.part_id in parts}
    missing_flasks = [ft for ft in required_flasks if resources.flask_capacity.get(ft, 0) <= 0]
    if missing_flasks:
        raise ValueError(f"Faltan capacidades para flasks: {', '.join(sorted(missing_flasks))}")

    # Busy flasks per type/day (seeded by initial in-use)
    busy_by_day: dict[str, dict[int, int]] = {ft: {} for ft in resources.flask_capacity.keys()}
    for (size, day_idx), qty in initial_flask_inuse.items():
        busy_by_day.setdefault(size, {})
        busy_by_day[size][int(day_idx)] = busy_by_day[size].get(int(day_idx), 0) + int(qty)

    def _can_allocate_flasks(*, size: str, day: int, qty: int, cool_days: int, max_flasks: int) -> bool:
        # Flask lifecycle: Mold (day) → Pour (day+1) → Cool (day+2..day+1+cool_days) → Shakeout (day+2+cool_days)
        # Lock duration = 2 + cool_days (mold day + pour day + cooling days)
        lock_duration = 2 + cool_days
        for d in range(day, min(day + lock_duration, horizon)):
            used = busy_by_day.get(size, {}).get(d, 0)
            if used + qty > max_flasks:
                return False
        return True

    def _reserve_flasks(*, size: str, day: int, qty: int, cool_days: int) -> None:
        # Flask lifecycle: Mold (day) → Pour (day+1) → Cool (day+2..day+1+cool_days) → Shakeout (day+2+cool_days)
        # Lock duration = 2 + cool_days (mold day + pour day + cooling days)
        lock_duration = 2 + cool_days
        for d in range(day, min(day + lock_duration, horizon)):
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

            size = str(part.flask_type or "").upper()
            max_flasks = int(resources.flask_capacity.get(size, 0))
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
