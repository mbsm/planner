from __future__ import annotations

from datetime import date

from foundryplan.data.repository import Repository
from foundryplan.planner.extract import prepare_planner_inputs


def prepare_and_sync(repo: Repository, *, asof_date: date, scenario_name: str = "default") -> dict:
    """Prepare planner inputs and return summary counts."""
    return prepare_planner_inputs(repo, scenario_name=scenario_name, asof_date=asof_date)
