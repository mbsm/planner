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
    flask_type: str  # dynamic flask type code
    cool_hours: float  # horas de enfriamiento en molde
    finish_days: int  # días de terminación nominal (almacenado como días, NO horas)
    min_finish_days: int  # días mínimos de terminación (compresión máxima permitida)
    pieces_per_mold: float
    net_weight_ton: float
    alloy: str | None = None


@dataclass(frozen=True)
class PlannerResource:
    flask_capacity: dict[str, int]
    flask_codes: dict[str, list[str]]
    molding_max_per_day: int
    molding_max_same_part_per_day: int
    pour_max_ton_per_day: float
