from __future__ import annotations

import math
from datetime import date

from foundryplan.planner.model import PlannerOrder, PlannerPart


def _build_due_day_map(workdays: list[date]) -> dict[str, int]:
    return {d.isoformat(): idx for idx, d in enumerate(workdays)}


def solve_planner_heuristic(
    *,
    orders: list[PlannerOrder],
    parts: dict[str, PlannerPart],
    workdays: list[date],
    daily_resources: dict[int, dict[str, any]],  # day_idx -> {molding_capacity, same_mold_capacity, pouring_tons_available, flask_available: {flask_type -> qty}}
    initial_patterns_loaded: set[str],
    max_horizon_days: int = 365,
) -> dict:
    """Greedy heuristic planner using daily_resources from planner_daily_resources table.
    
    Reads capacities day-by-day instead of using global PlannerResource.
    
    Calculates start_by per order as:
      start_by = due_date - (
          ceil(remaining_molds / same_mold_capacity_avg) +          # Molding time
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

    # Verify all required flask types have capacity
    required_flasks = {parts[o.part_id].flask_type for o in orders if o.part_id in parts}
    for ft in required_flasks:
        has_capacity = any(
            daily_resources.get(d, {}).get("flask_available", {}).get(ft, 0) > 0
            for d in range(horizon)
        )
        if not has_capacity:
            raise ValueError(f"Flask type {ft} sin capacidad disponible en ningún día del horizonte")

    # Calculate average same_mold_capacity for start_by estimation
    same_mold_capacity_avg = sum(
        daily_resources.get(d, {}).get("same_mold_capacity", 0)
        for d in range(horizon)
    ) / max(1, horizon)

    def _calculate_start_by(order: PlannerOrder, part: PlannerPart) -> int:
        """Calculate start_by day index for order using resource capacity estimates."""
        remaining_molds = remaining.get(order.order_id, 0)
        if remaining_molds <= 0:
            return horizon + 999
        
        # Molding days: ceil(remaining_molds / same_mold_capacity_avg)
        molding_days = math.ceil(remaining_molds / float(same_mold_capacity_avg or 1))
        
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
        day_res = daily_resources.get(d, {})
        
        remaining_capacity = int(day_res.get("molding_capacity", 0))
        same_mold_capacity = int(day_res.get("same_mold_capacity", 0))
        pour_capacity_tons = float(day_res.get("pouring_tons_available", 0.0))
        flask_available = day_res.get("flask_available", {})
        
        if remaining_capacity <= 0:
            continue  # No molding capacity this day
        
        part_usage: dict[str, int] = {}

        for order in ordered:
            if remaining_capacity <= 0:
                break
            qty_left = remaining.get(order.order_id, 0)
            if qty_left <= 0:
                continue

            part = parts.get(order.part_id)
            if part is None:
                continue

            # Check same_mold constraint
            max_by_part = same_mold_capacity - part_usage.get(order.part_id, 0)
            if max_by_part <= 0:
                continue

            # Check pouring capacity constraint
            metal_per_mold_ton = float(part.net_weight_ton * part.pieces_per_mold)
            if metal_per_mold_ton <= 0:
                continue
            max_by_pour = int(pour_capacity_tons / metal_per_mold_ton) if metal_per_mold_ton > 0 else 0
            if max_by_pour <= 0:
                continue

            # Check flask availability (already decremented by demolding)
            flask_type = str(part.flask_type or "").upper()
            max_by_flasks = int(flask_available.get(flask_type, 0))
            if max_by_flasks <= 0:
                continue

            # Determine max qty for this order
            max_qty = min(qty_left, remaining_capacity, max_by_part, max_by_pour, max_by_flasks)
            if max_qty <= 0:
                continue

            # Schedule this qty
            schedule.setdefault(order.order_id, {})[d] = max_qty
            remaining[order.order_id] = qty_left - max_qty
            remaining_capacity -= max_qty
            part_usage[order.part_id] = part_usage.get(order.part_id, 0) + max_qty
            pour_capacity_tons -= max_qty * metal_per_mold_ton
            flask_available[flask_type] = max_by_flasks - max_qty

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
