from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

from foundryplanner.data.repository import Repository


class StrategyDataBridge:
    """ETL from SAP/internal tables into planning input tables for the weekly solver.
    
    The foundry_planner_engine expects specific table names and column schemas that
    conflict with our app's tables. We use a separate database file (engine.db) for
    the engine's input/output tables.
    """

    def __init__(self, repo: Repository):
        self.repo = repo
        # Engine uses a separate database file in the same directory as main db
        self.engine_db_path = self.repo.db.path.parent / "engine.db"

    def _get_int_cfg(self, key: str, default: int) -> int:
        raw = self.repo.get_config(key=key, default=str(default))
        try:
            v = int(float(str(raw).strip()))
        except Exception:
            v = default
        return v

    def _get_float_cfg(self, key: str, default: float) -> float:
        raw = self.repo.get_config(key=key, default=str(default))
        try:
            v = float(str(raw).strip())
        except Exception:
            v = default
        return v

    def _get_str_cfg(self, key: str, default: str) -> str:
        raw = self.repo.get_config(key=key, default=default)
        return str(raw) if raw is not None else default

    def _parse_holidays(self) -> set[date]:
        raw = self._get_str_cfg("strategy_holidays", "")
        if not raw.strip():
            return set()

        out: set[date] = set()
        parts = [p.strip() for p in raw.replace(",", "\n").splitlines()]
        for p in parts:
            if not p:
                continue
            try:
                out.add(date.fromisoformat(p))
            except Exception:
                continue
        return out

    @staticmethod
    def _workday_weekday_set(working_days_per_week: int) -> set[int]:
        # Assume workweek starts Monday.
        n = max(0, min(7, int(working_days_per_week)))
        return set(range(n))

    def _effective_workdays_for_week(self, week_id: int) -> int:
        """Calculate effective work days for a week considering holidays.
        
        Uses a default of 5 working days per week (Mon-Fri).
        Capacity per center is now driven by shifts_per_week × molds_per_shift,
        but global pouring capacity still needs workdays calculation.
        """
        today = date.today()
        week_zero = today - timedelta(days=today.weekday())
        start = week_zero + timedelta(days=7 * int(week_id))

        # Default to 5-day work week for pouring calculations
        working_days_per_week = self._get_int_cfg("strategy_working_days_per_week", 5)
        workdays = self._workday_weekday_set(working_days_per_week)
        holidays = self._parse_holidays()

        eff = 0
        for d in range(7):
            day = start + timedelta(days=d)
            if day.weekday() not in workdays:
                continue
            if day in holidays:
                continue
            eff += 1
        return max(0, eff)

    def _ensure_engine_schema(self, con: sqlite3.Connection) -> None:
        """Create engine-compatible tables."""
        con.executescript("""
            -- Input Tables
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                part_number TEXT NOT NULL,
                molding_line_id TEXT NOT NULL,
                due_date_week_id INTEGER NOT NULL,
                qty_requested_parts INTEGER NOT NULL,
                confirmed_molds INTEGER DEFAULT 0,
                scrapped_molds INTEGER DEFAULT 0,
                priority_weight REAL DEFAULT 1.0
            );

            CREATE TABLE IF NOT EXISTS parts (
                part_number TEXT PRIMARY KEY,
                parts_per_mold INTEGER NOT NULL,
                gross_weight_per_part_tons REAL NOT NULL,
                flask_size TEXT NOT NULL,
                cooling_time_hours REAL NOT NULL,
                max_molds_per_week INTEGER DEFAULT 9999,
                post_molding_lead_time_weeks INTEGER DEFAULT 0,
                pouring_delay_weeks INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS molding_lines_config (
                molding_line_id TEXT PRIMARY KEY,
                working_hours_per_week REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS flasks_inventory (
                molding_line_id TEXT NOT NULL,
                flask_size TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                PRIMARY KEY (molding_line_id, flask_size)
            );

            CREATE TABLE IF NOT EXISTS capacities_weekly (
                week_id INTEGER NOT NULL,
                molding_line_id TEXT NOT NULL,
                max_molds INTEGER NOT NULL,
                PRIMARY KEY (week_id, molding_line_id)
            );

            CREATE TABLE IF NOT EXISTS global_capacities_weekly (
                week_id INTEGER PRIMARY KEY,
                pour_tons_cap REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS initial_flask_usage (
                molding_line_id TEXT NOT NULL,
                flask_size TEXT NOT NULL,
                weeks_into_future INTEGER NOT NULL,
                quantity_flasks INTEGER NOT NULL,
                PRIMARY KEY (molding_line_id, flask_size, weeks_into_future)
            );

            -- Output Tables (engine writes to these)
            CREATE TABLE IF NOT EXISTS plan_molding (
                order_id TEXT NOT NULL,
                week_id INTEGER NOT NULL,
                molds_planned INTEGER NOT NULL,
                PRIMARY KEY (order_id, week_id)
            );

            CREATE TABLE IF NOT EXISTS order_results (
                order_id TEXT PRIMARY KEY,
                molds_to_plan INTEGER NOT NULL,
                molds_total_required INTEGER NOT NULL,
                start_week_id INTEGER,
                completion_week_id INTEGER,
                delivery_week_id INTEGER,
                due_week_id INTEGER NOT NULL,
                is_late BOOLEAN NOT NULL,
                weeks_late INTEGER NOT NULL,
                buffer_consumed_weeks INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS plan_pouring (
                week_id INTEGER NOT NULL,
                order_id TEXT NOT NULL,
                molds_poured INTEGER NOT NULL,
                weight_poured_tons REAL NOT NULL,
                PRIMARY KEY (week_id, order_id)
            );

            CREATE TABLE IF NOT EXISTS plan_shakeout (
                week_id INTEGER NOT NULL,
                order_id TEXT NOT NULL,
                molds_shaken INTEGER NOT NULL,
                PRIMARY KEY (week_id, order_id)
            );

            CREATE TABLE IF NOT EXISTS plan_completion (
                week_id INTEGER NOT NULL,
                order_id TEXT NOT NULL,
                molds_completed INTEGER NOT NULL,
                PRIMARY KEY (week_id, order_id)
            );

            CREATE TABLE IF NOT EXISTS run_status (
                run_id TEXT PRIMARY KEY,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL,
                details TEXT
            );
        """)

    def _connect_engine_db(self) -> sqlite3.Connection:
        """Open connection to engine database (creates it if needed)."""
        con = sqlite3.connect(str(self.engine_db_path))
        con.row_factory = sqlite3.Row
        self._ensure_engine_schema(con)
        return con

    def populate_orders(self, process: str = "terminaciones") -> int:
        """Build `orders` table for engine from shared orders table (MB52 + Visión filters).

        Engine schema:
        - order_id TEXT PRIMARY KEY
        - part_number TEXT NOT NULL
        - molding_line_id TEXT NOT NULL
        - due_date_week_id INTEGER NOT NULL
        - qty_requested_parts INTEGER NOT NULL
        - confirmed_molds INTEGER DEFAULT 0
        - scrapped_molds INTEGER DEFAULT 0
        - priority_weight REAL DEFAULT 1.0
        """
        orders = self.repo.get_orders_model(process=process)
        priority_set = self.repo.get_priority_orderpos_set()
        lines = self.repo.get_lines_model(process=process)
        
        # Compute reference date (week 0 = current Monday)
        today = date.today()
        week_zero = today - timedelta(days=today.weekday())
        
        # Get default line (first configured line)
        default_line = f"L{lines[0].line_id}" if lines else "L1"
        
        # Aggregate by (pedido, posicion) to create unique order_id
        aggregated: dict[str, dict] = {}
        for order in orders:
            order_id = f"{order.pedido}_{order.posicion}"
            if order_id in aggregated:
                aggregated[order_id]["qty_requested_parts"] += order.cantidad
            else:
                delta_days = (order.fecha_entrega - week_zero).days
                due_week = max(0, delta_days // 7)
                
                if order.is_test:
                    priority_weight = 2.0
                elif (order.pedido, order.posicion) in priority_set:
                    priority_weight = 1.5
                else:
                    priority_weight = 1.0
                
                aggregated[order_id] = {
                    "part_number": order.numero_parte,
                    "molding_line_id": default_line,
                    "due_date_week_id": due_week,
                    "qty_requested_parts": order.cantidad,
                    "confirmed_molds": 0,
                    "scrapped_molds": 0,
                    "priority_weight": priority_weight,
                }
        
        rows_to_insert = [
            (order_id, data["part_number"], data["molding_line_id"], data["due_date_week_id"],
             data["qty_requested_parts"], data["confirmed_molds"], data["scrapped_molds"], data["priority_weight"])
            for order_id, data in aggregated.items()
        ]
        
        with self._connect_engine_db() as con:
            con.execute("DELETE FROM orders")
            con.executemany(
                "INSERT INTO orders(order_id, part_number, molding_line_id, due_date_week_id, "
                "qty_requested_parts, confirmed_molds, scrapped_molds, priority_weight) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                rows_to_insert,
            )
            con.commit()
        
        return len(rows_to_insert)

    def populate_parts(self, process: str = "terminaciones") -> int:
        """Build `parts` table for engine from internal part master.

        Engine schema:
        - part_number TEXT PRIMARY KEY
        - parts_per_mold INTEGER NOT NULL
        - gross_weight_per_part_tons REAL NOT NULL
        - flask_size TEXT NOT NULL
        - cooling_time_hours REAL NOT NULL
        - max_molds_per_week INTEGER DEFAULT 9999
        - post_molding_lead_time_weeks INTEGER DEFAULT 0
        - pouring_delay_weeks INTEGER DEFAULT 0
        """
        parts = self.repo.get_parts_model()
        
        rows_to_insert = []
        for part in parts:
            vulc = part.vulcanizado_dias or 0
            mec = part.mecanizado_dias or 0
            insp = part.inspeccion_externa_dias or 0
            lead_time_weeks = max(0, (vulc + mec + insp) // 7)
            
            parts_per_mold = 2
            gross_weight = part.peso_ton or 0.003
            flask_size = "120"
            cooling_time_hours = 72.0
            max_molds_per_week = 9999
            pouring_delay_weeks = 0
            
            rows_to_insert.append((
                part.numero_parte,
                parts_per_mold,
                gross_weight,
                flask_size,
                cooling_time_hours,
                max_molds_per_week,
                lead_time_weeks,
                pouring_delay_weeks,
            ))
        
        with self._connect_engine_db() as con:
            con.execute("DELETE FROM parts")
            con.executemany(
                "INSERT INTO parts(part_number, parts_per_mold, gross_weight_per_part_tons, "
                "flask_size, cooling_time_hours, max_molds_per_week, post_molding_lead_time_weeks, "
                "pouring_delay_weeks) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                rows_to_insert,
            )
            con.commit()
        
        return len(rows_to_insert)

    def populate_molding_lines_config(self, process: str = "terminaciones") -> int:
        """Build `molding_lines_config` table for engine."""
        lines = self.repo.get_lines_model(process=process)

        default_hours_per_week = self._get_float_cfg("strategy_working_hours_per_week", 120.0)
        
        rows_to_insert = [(f"L{line.line_id}", default_hours_per_week) for line in lines]
        
        with self._connect_engine_db() as con:
            con.execute("DELETE FROM molding_lines_config")
            con.executemany(
                "INSERT INTO molding_lines_config(molding_line_id, working_hours_per_week) VALUES(?, ?)",
                rows_to_insert,
            )
            con.commit()
        
        return len(rows_to_insert)

    def populate_flasks_inventory(self, process: str = "terminaciones") -> int:
        """Build `flasks_inventory` table for engine."""
        lines = self.repo.get_lines_model(process=process)

        flask_sizes_raw = self._get_str_cfg("strategy_flask_sizes", "120,105,146")
        flask_sizes = [s.strip() for s in flask_sizes_raw.split(",") if s.strip()]
        if not flask_sizes:
            flask_sizes = ["120", "105", "146"]

        default_qty = self._get_int_cfg("strategy_flasks_qty_per_line", 25)
        
        rows_to_insert = []
        for line in lines:
            line_id = f"L{line.line_id}"
            for flask_size in flask_sizes:
                rows_to_insert.append((line_id, flask_size, default_qty))
        
        with self._connect_engine_db() as con:
            con.execute("DELETE FROM flasks_inventory")
            con.executemany(
                "INSERT INTO flasks_inventory(molding_line_id, flask_size, quantity) VALUES(?, ?, ?)",
                rows_to_insert,
            )
            con.commit()
        
        return len(rows_to_insert)

    def populate_capacities_weekly(self, process: str = "terminaciones", week_range: tuple[int, int] = (0, 40)) -> int:
        """Build `capacities_weekly` table for engine.
        
        Capacity per week per center = shifts_per_week × molds_per_shift.
        Uses molding_centers table instead of global config.
        """
        centers = self.repo.list_molding_centers()
        
        rows_to_insert = []
        for week_id in range(week_range[0], week_range[1]):
            for c in centers:
                cid = c["center_id"]
                spw = c.get("shifts_per_week", 10)
                mps = c.get("molds_per_shift", 25)
                max_molds = max(0, int(spw) * int(mps))
                line_id = f"C{cid}"  # Use center ID as line ID
                rows_to_insert.append((week_id, line_id, max_molds))
        
        # If no centers defined, fall back to dispatcher lines with default capacity
        if not centers:
            lines = self.repo.get_lines_model(process=process)
            default_molds_week = 10 * 25  # 10 shifts × 25 molds
            for week_id in range(week_range[0], week_range[1]):
                for line in lines:
                    line_id = f"L{line.line_id}"
                    rows_to_insert.append((week_id, line_id, default_molds_week))
        
        with self._connect_engine_db() as con:
            con.execute(
                "DELETE FROM capacities_weekly WHERE week_id >= ? AND week_id < ?",
                (week_range[0], week_range[1]),
            )
            con.executemany(
                "INSERT INTO capacities_weekly(week_id, molding_line_id, max_molds) VALUES(?, ?, ?)",
                rows_to_insert,
            )
            con.commit()
        
        return len(rows_to_insert)

    def populate_global_capacities(self, week_range: tuple[int, int] = (0, 40)) -> int:
        """Build `global_capacities_weekly` table for engine."""
        pour_tons_per_day = self._get_float_cfg("strategy_pour_tons_per_day", 100.0)
        rows_to_insert = []
        for week_id in range(week_range[0], week_range[1]):
            eff_days = self._effective_workdays_for_week(week_id)
            rows_to_insert.append((week_id, float(pour_tons_per_day) * float(eff_days)))
        
        with self._connect_engine_db() as con:
            con.execute(
                "DELETE FROM global_capacities_weekly WHERE week_id >= ? AND week_id < ?",
                (week_range[0], week_range[1]),
            )
            con.executemany(
                "INSERT INTO global_capacities_weekly(week_id, pour_tons_cap) VALUES(?, ?)",
                rows_to_insert,
            )
            con.commit()
        
        return len(rows_to_insert)

    def populate_initial_flask_usage(self) -> int:
        """Build `initial_flask_usage` table for engine (empty by default)."""
        with self._connect_engine_db() as con:
            con.execute("DELETE FROM initial_flask_usage")
            con.commit()
        return 0

    def populate_all(self, process: str = "terminaciones", week_range: tuple[int, int] = (0, 40)) -> dict:
        """Populate all engine input tables.

        Returns summary statistics for validation and diagnostics.
        """
        stats = {}
        
        stats["orders"] = self.populate_orders(process=process)
        stats["parts"] = self.populate_parts(process=process)
        stats["lines"] = self.populate_molding_lines_config(process=process)
        stats["flasks"] = self.populate_flasks_inventory(process=process)
        stats["capacities"] = self.populate_capacities_weekly(process=process, week_range=week_range)
        stats["global_caps"] = self.populate_global_capacities(week_range=week_range)
        stats["initial_usage"] = self.populate_initial_flask_usage()
        
        return stats

    def get_engine_db_path(self) -> Path:
        """Return the path to the engine database file."""
        return self.engine_db_path
