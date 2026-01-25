from __future__ import annotations

from foundryplanner.data.repository import Repository


class StrategyDataBridge:
    """Stub: ETL from SAP/internal tables into planning input tables for the weekly solver."""

    def __init__(self, repo: Repository):
        self.repo = repo

    def populate_plan_orders_weekly(self, process: str = "terminaciones") -> int:
        """Build plan_orders_weekly from SAP (MB52 + Visión) and internal master.

        Uses the same filtered `orders` table built for dispatching (MB52 + Visión + process/almacén
        filters + test detection). Ensures planning consumes the identical dataset as the dispatcher.

        TODO: implement ETL into plan_orders_weekly; currently unimplemented beyond input read.
        """
        orders = self.repo.get_orders_model(process=process)
        return len(orders)

    def populate_plan_parts_routing(self, process: str = "terminaciones") -> int:
        """Build plan_parts_routing from internal part master.

        TODO: implement ETL; currently unimplemented.
        """
        raise NotImplementedError

    def populate_plan_capacities_weekly(self, week_range: tuple[int, int] = (0, 40)) -> int:
        """Build plan_capacities_weekly from config and maintenance windows.

        TODO: implement ETL; currently unimplemented.
        """
        raise NotImplementedError

    def populate_plan_molding_lines_config(self) -> int:
        """Build plan_molding_lines_config from configured lines/capacities.

        TODO: implement ETL; currently unimplemented.
        """
        raise NotImplementedError

    def populate_plan_flasks_inventory(self) -> int:
        """Build plan_flasks_inventory from config/inventory.

        TODO: implement ETL; currently unimplemented.
        """
        raise NotImplementedError

    def populate_plan_global_capacities(self, week_range: tuple[int, int] = (0, 40)) -> int:
        """Build plan_global_capacities_weekly (melt deck tonnage caps).

        TODO: implement ETL; currently unimplemented.
        """
        raise NotImplementedError

    def populate_plan_initial_flask_usage(self) -> int:
        """Build plan_initial_flask_usage (current WIP occupying flasks).

        TODO: implement ETL; currently unimplemented.
        """
        raise NotImplementedError

    def populate_all(self, process: str = "terminaciones", week_range: tuple[int, int] = (0, 40)) -> dict:
        """Placeholder aggregate that will call all populate_* methods.

        TODO: implement orchestration; currently unimplemented.
        """
        raise NotImplementedError
