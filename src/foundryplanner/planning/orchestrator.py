from __future__ import annotations

from foundryplanner.data.repository import Repository
from foundryplanner.planning.engine_adapter import import_engine_solve


class StrategyOrchestrator:
    """Stub: orchestrates weekly solve and dispatch regeneration."""

    def __init__(self, repo: Repository):
        self.repo = repo

    async def solve_weekly_plan(self, *, force: bool = False) -> dict:
        """Run foundry_planner_engine after preparing inputs.

        TODO: implement full workflow; currently only wires import.
        """
        solve = import_engine_solve()
        # Placeholder: caller will supply db path and options when implemented.
        _ = solve  # keep reference for now to avoid unused-variable linters
        raise NotImplementedError

    async def regenerate_dispatch_from_plan(self) -> dict:
        """Rebuild dispatch queues constrained by plan_molding.

        TODO: implement; currently unimplemented.
        """
        raise NotImplementedError
