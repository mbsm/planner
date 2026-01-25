from pathlib import Path

import foundryplanner.planning.orchestrator as orch_mod
from foundryplanner.planning.orchestrator import StrategyOrchestrator


class _FakeBridge:
    def populate_all(self, *, process: str, week_range: tuple[int, int]):
        return {"orders": 1, "parts": 1, "lines": 1, "flasks": 1, "capacities": 1, "global_caps": 1}

    def get_engine_db_path(self) -> Path:
        return Path("/tmp/engine.db")


class _FakeRepo:
    def __init__(self, *, has_orders: bool):
        self._has_orders = has_orders
        self.rebuild_calls = 0

    def count_orders(self, *, process: str = "terminaciones") -> int:
        return 1 if self._has_orders else 0

    def try_rebuild_orders_from_sap_for(self, *, process: str = "terminaciones") -> bool:
        self.rebuild_calls += 1
        # Simulate that the rebuild produced orders.
        self._has_orders = True
        return True

    def get_orders_model(self, *, process: str = "terminaciones"):
        return [object()] if self._has_orders else []

    def count_sap_mb52(self) -> int:
        return 1

    def count_sap_vision(self) -> int:
        return 1

    def get_parts_model(self):
        return [object()]

    def get_lines(self, *, process: str = "terminaciones"):
        return ["L1"]

    def get_strategy_data_bridge(self):
        return _FakeBridge()

    def get_config(self, *, key: str, default: str | None = None):
        # Minimal solver config needed.
        return default


def test_force_replan_does_not_rebuild_when_orders_exist(monkeypatch):
    repo = _FakeRepo(has_orders=True)

    def _fake_import_engine_solve():
        def _solve(_db_path: str, *, options: dict):
            return {"status": "SUCCESS"}

        return _solve

    monkeypatch.setattr(orch_mod, "import_engine_solve", _fake_import_engine_solve)

    o = StrategyOrchestrator(repo)  # type: ignore[arg-type]
    result = o._solve_weekly_plan_sync(force=True)

    assert result["status"] == "success"
    assert repo.rebuild_calls == 0


def test_force_replan_rebuilds_when_no_orders(monkeypatch):
    repo = _FakeRepo(has_orders=False)

    def _fake_import_engine_solve():
        def _solve(_db_path: str, *, options: dict):
            return {"status": "SUCCESS"}

        return _solve

    monkeypatch.setattr(orch_mod, "import_engine_solve", _fake_import_engine_solve)

    o = StrategyOrchestrator(repo)  # type: ignore[arg-type]
    result = o._solve_weekly_plan_sync(force=True)

    assert result["status"] == "success"
    assert repo.rebuild_calls == 1
