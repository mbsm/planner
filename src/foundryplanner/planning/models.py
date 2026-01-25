"""UI and data models for strategic planning layer."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WeeklyPlan:
    """Weekly molding allocation from strategic solver."""
    process: str
    line_id: int
    pedido: str
    posicion: str
    week_id: int
    molds_planned: int


@dataclass(frozen=True)
class OrderResultsKPI:
    """Strategic plan KPI summary per order."""
    process: str
    pedido: str
    posicion: str
    molds_to_plan: int
    start_week: int | None
    delivery_week: int | None
    is_late: bool
    weeks_late: int


@dataclass(frozen=True)
class LineUtilization:
    """Line capacity utilization summary."""
    process: str
    line_id: int
    week_id: int
    molds_capacity: int
    molds_planned: int
    utilization_pct: float


@dataclass(frozen=True)
class LatenessSummary:
    """Aggregate lateness metrics."""
    process: str
    total_orders: int
    on_time_count: int
    late_count: int
    on_time_pct: float
    avg_weeks_late: float
    max_weeks_late: int
