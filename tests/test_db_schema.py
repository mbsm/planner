from __future__ import annotations

from pathlib import Path

from foundryplanner.data.db import Db


def test_schema_v5_tables_created(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    db = Db(db_path)
    db.ensure_schema()

    with db.connect() as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    # Dispatch/shared tables
    for name in {"orders", "parts", "programa", "last_program", "schema_version"}:
        assert name in tables

    # Strategic input/output tables
    expected_plan_tables = {
        "plan_orders_weekly",
        "plan_parts_routing",
        "plan_molding_lines_config",
        "plan_flasks_inventory",
        "plan_capacities_weekly",
        "plan_global_capacities_weekly",
        "plan_initial_flask_usage",
        "plan_molding",
        "plan_pouring",
        "plan_shakeout",
        "plan_completion",
        "order_results",
    }
    assert expected_plan_tables.issubset(tables)

    with db.connect() as con:
        row = con.execute("SELECT version FROM schema_version ORDER BY rowid DESC LIMIT 1").fetchone()
    assert row is not None
    assert int(row[0]) >= 5
