from __future__ import annotations

from datetime import date

from foundryplan.data.repository_views import PlannerRepository


def prepare_planner_inputs(
    repo: PlannerRepository,
    *,
    scenario_name: str = "default",
    asof_date: date,
    horizon_buffer_days: int = 10,
) -> dict:
    """Populate planner_* tables from current SAP snapshots + master data.

    Returns a summary dict with counts.
    """
    scenario_id = repo.ensure_planner_scenario(name=scenario_name)
    return repo.sync_planner_inputs_from_sap(
        scenario_id=scenario_id,
        asof_date=asof_date,
        horizon_buffer_days=horizon_buffer_days,
    )
