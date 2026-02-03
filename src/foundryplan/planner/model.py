from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlannerOrder:
    order_id: str
    part_id: str
    qty: int
    due_date: str
    priority: int


@dataclass(frozen=True)
class PlannerPart:
    part_id: str
    flask_size: str  # S/M/L
    cool_hours: float
    finish_hours: float
    min_finish_hours: float
    pieces_per_mold: float
    net_weight_ton: float
    alloy: str | None = None


@dataclass(frozen=True)
class PlannerResource:
    flasks_S: int
    flasks_M: int
    flasks_L: int
    molding_max_per_day: int
    molding_max_same_part_per_day: int
    pour_max_ton_per_day: float
