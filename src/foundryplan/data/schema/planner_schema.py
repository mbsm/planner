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
            heats_per_shift REAL,
            tons_per_heat REAL,
            max_placement_search_days INTEGER,
            allow_molding_gaps INTEGER,
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

        CREATE TABLE IF NOT EXISTS planner_initial_order_progress (
            scenario_id INTEGER NOT NULL,
            asof_date TEXT NOT NULL,
            order_id TEXT NOT NULL,
            remaining_molds INTEGER NOT NULL,
            PRIMARY KEY (scenario_id, order_id)
        );

        CREATE TABLE IF NOT EXISTS planner_schedule_results (
            scenario_id INTEGER NOT NULL,
            run_timestamp TEXT NOT NULL,
            asof_date TEXT NOT NULL,
            status TEXT NOT NULL,
            suggested_horizon_days INTEGER,
            actual_horizon_days INTEGER NOT NULL,
            skipped_orders INTEGER NOT NULL DEFAULT 0,
            horizon_exceeded INTEGER NOT NULL DEFAULT 0,
            molds_schedule_json TEXT,
            pour_days_json TEXT,
            shakeout_days_json TEXT,
            completion_days_json TEXT,
            finish_days_json TEXT,
            late_days_json TEXT,
            errors_json TEXT,
            objective REAL,
            PRIMARY KEY (scenario_id, run_timestamp)
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
    
    # Add pouring breakdown columns
    try:
        con.execute("ALTER TABLE planner_resources ADD COLUMN heats_per_shift REAL")
    except Exception:
        pass
    try:
        con.execute("ALTER TABLE planner_resources ADD COLUMN tons_per_heat REAL")
    except Exception:
        pass
    
    # Add heuristic configuration columns
    try:
        con.execute("ALTER TABLE planner_resources ADD COLUMN max_placement_search_days INTEGER DEFAULT 365")
    except Exception:
        pass
    try:
        con.execute("ALTER TABLE planner_resources ADD COLUMN allow_molding_gaps INTEGER DEFAULT 0")
    except Exception:
        pass
    
    # Add lag configuration columns
    try:
        con.execute("ALTER TABLE planner_resources ADD COLUMN pour_lag_days INTEGER DEFAULT 1")
    except Exception:
        pass
    try:
        con.execute("ALTER TABLE planner_resources ADD COLUMN shakeout_lag_days INTEGER DEFAULT 1")
    except Exception:
        pass
    
    # Migrate finish_hours to finish_days (add new columns, keep old for compatibility)
    try:
        con.execute("ALTER TABLE planner_parts ADD COLUMN finish_days INTEGER")
    except Exception:
        pass
    try:
        con.execute("ALTER TABLE planner_parts ADD COLUMN min_finish_days INTEGER")
    except Exception:
        pass
    
    # Migrate data if finish_days is NULL but finish_hours exists
    try:
        con.execute("""
            UPDATE planner_parts 
            SET finish_days = CAST(ROUND(finish_hours / 24.0) AS INTEGER)
            WHERE finish_days IS NULL AND finish_hours IS NOT NULL
        """)
        con.execute("""
            UPDATE planner_parts 
            SET min_finish_days = CAST(ROUND(min_finish_hours / 24.0) AS INTEGER)
            WHERE min_finish_days IS NULL AND min_finish_hours IS NOT NULL
        """)
    except Exception:
        pass
