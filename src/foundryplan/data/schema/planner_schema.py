from __future__ import annotations

import sqlite3


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS planner_scenarios (
            scenario_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS planner_parts (
            scenario_id INTEGER NOT NULL,
            part_id TEXT NOT NULL,
            flask_size TEXT,
            cool_hours REAL,
            finish_hours REAL,
            min_finish_hours REAL,
            pieces_per_mold REAL,
            net_weight_ton REAL,
            alloy TEXT,
            PRIMARY KEY (scenario_id, part_id)
        );

        CREATE TABLE IF NOT EXISTS planner_orders (
            scenario_id INTEGER NOT NULL,
            order_id TEXT NOT NULL,
            part_id TEXT NOT NULL,
            qty INTEGER,
            due_date TEXT,
            priority INTEGER DEFAULT 100,
            PRIMARY KEY (scenario_id, order_id)
        );

        CREATE TABLE IF NOT EXISTS planner_resources (
            scenario_id INTEGER PRIMARY KEY,
            molding_max_per_day INTEGER,
            molding_max_same_part_per_day INTEGER,
            pour_max_ton_per_day REAL,
            molding_max_per_shift INTEGER,
            molding_shifts_json TEXT,
            pour_max_ton_per_shift REAL,
            pour_shifts_json TEXT,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS planner_flask_types (
            scenario_id INTEGER NOT NULL,
            flask_type TEXT NOT NULL,
            qty_total INTEGER NOT NULL DEFAULT 0,
            codes_csv TEXT,
            label TEXT,
            notes TEXT,
            PRIMARY KEY (scenario_id, flask_type)
        );

        CREATE TABLE IF NOT EXISTS planner_calendar_workdays (
            scenario_id INTEGER NOT NULL,
            workday_index INTEGER NOT NULL,
            date TEXT NOT NULL,
            week_index INTEGER NOT NULL,
            PRIMARY KEY (scenario_id, workday_index),
            UNIQUE (scenario_id, date)
        );

        CREATE TABLE IF NOT EXISTS planner_daily_resources (
            scenario_id INTEGER NOT NULL DEFAULT 1,
            day TEXT NOT NULL,
            flask_type TEXT NOT NULL,
            available_qty INTEGER NOT NULL DEFAULT 0,
            molding_capacity_per_day INTEGER NOT NULL DEFAULT 0,
            same_mold_capacity_per_day INTEGER NOT NULL DEFAULT 0,
            pouring_tons_available REAL NOT NULL DEFAULT 0.0,
            PRIMARY KEY (scenario_id, day, flask_type)
        );
        """
    )
    
    # Add shift columns if they don't exist
    try:
        con.execute("ALTER TABLE planner_resources ADD COLUMN molding_max_per_shift INTEGER")
    except Exception:
        pass
    try:
        con.execute("ALTER TABLE planner_resources ADD COLUMN molding_shifts_json TEXT")
    except Exception:
        pass
    try:
        con.execute("ALTER TABLE planner_resources ADD COLUMN pour_max_ton_per_shift REAL")
    except Exception:
        pass
    try:
        con.execute("ALTER TABLE planner_resources ADD COLUMN pour_shifts_json TEXT")
    except Exception:
        pass
