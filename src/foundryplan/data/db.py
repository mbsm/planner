from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from foundryplan.data.schema import (
    ensure_data_schema,
    ensure_dispatcher_schema,
    ensure_planner_schema,
    seed_alloy_catalog,
)


class Db:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=20.0)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def ensure_schema(self) -> None:
        con = sqlite3.connect(self.path, timeout=10.0)
        try:
            con.execute("PRAGMA journal_mode=WAL;")
            con.execute("PRAGMA foreign_keys=ON;")

            ensure_data_schema(con)
            seed_alloy_catalog(con)
            ensure_dispatcher_schema(con)
            ensure_planner_schema(con)
        finally:
            con.commit()
            con.close()

    def _table_exists(self, con: sqlite3.Connection, table_name: str) -> bool:
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return row is not None
