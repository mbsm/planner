from __future__ import annotations

from foundryplanner.data.repository import Repository


class StrategyResultReader:
    """Stub: Reads planning outputs (plan_molding, order_results) for UI/dispatch."""

    def __init__(self, repo: Repository):
        self.repo = repo

    def get_order_results(self, order_id: str) -> dict:
        """Fetch KPIs for a single order from order_results.

        TODO: implement lookup; currently unimplemented.
        """
        raise NotImplementedError

    def get_line_allocation(self, line_id: int, week_id: int | None = None) -> list[dict]:
        """Fetch plan_molding rows for a given line (optionally filtered by week).

        TODO: implement lookup; currently unimplemented.
        """
        raise NotImplementedError

    def get_plan_summary(self) -> dict:
        """High-level summary for UI (utilization, lateness KPIs, etc.).

        TODO: implement aggregation; currently unimplemented.
        """
        raise NotImplementedError
