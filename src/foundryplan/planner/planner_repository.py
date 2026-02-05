"""Planner repository layer - manages planner-specific database operations."""

from __future__ import annotations

import json
import math
import re
from datetime import date
from typing import TYPE_CHECKING

from foundryplan.data.repo_utils import logger

if TYPE_CHECKING:
    from foundryplan.data.db import Db
    from foundryplan.data.data_repository import DataRepositoryImpl


class PlannerRepositoryImpl:
    """Planner data access: scenarios, orders/parts/resources, calendar, schedules."""

    def __init__(self, db: Db, data_repo: DataRepositoryImpl) -> None:
        self.db = db
        self.data_repo = data_repo

    # ---------- Planner helpers ----------
    def _planner_moldeo_almacen(self) -> str:
        raw = str(self.data_repo.get_config(key="sap_almacen_moldeo", default="4032") or "").strip()
        return self.data_repo._normalize_sap_key(raw) or raw

    def _planner_holidays(self) -> set[date]:
        raw = str(self.data_repo.get_config(key="planner_holidays", default="") or "")
        tokens = [t.strip() for t in re.split(r"[,\n; ]+", raw) if t.strip()]
        out: set[date] = set()
        for tok in tokens:
            try:
                out.add(date.fromisoformat(tok))
            except Exception:
                continue
        return out

    def _get_working_days_from_shifts(self, molding_shifts: dict, pour_shifts: dict) -> set[int]:
        """Extract working weekdays from shift configuration.
        
        Returns set of weekday integers (0=Mon, 6=Sun) that have ANY shifts configured.
        Defaults to Mon-Fri (0-4) if no configuration found.
        """
        working_days = set()
        
        # Days of week mapping
        day_names = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]
        
        for idx, day_name in enumerate(day_names):
            molding_shifts_count = molding_shifts.get(day_name, 0)
            pour_shifts_count = pour_shifts.get(day_name, 0)
            
            # Day is working if it has at least one shift (molding OR pour)
            if molding_shifts_count > 0 or pour_shifts_count > 0:
                working_days.add(idx)
        
        # Default to Mon-Fri if nothing configured
        if not working_days:
            working_days = {0, 1, 2, 3, 4}
        
        return working_days

    def get_flasks_in_use_from_demolding(self, *, asof_date: date) -> dict[str, int]:
        """Read flasks currently occupied from desmoldeo snapshot.
        
        Returns dict of {flask_type: qty_occupied} for flasks still cooling.
        
        Logic:
        - Filter by cancha (configured in planner settings)
        - Read first 3 characters of flask_id (Caja) to determine flask_type
        - Use Fecha Desmoldeo (NOT Fecha a desmoldear)
        - Flask is occupied from today until demolding_date + 1 day
        - mold_quantity es la fracción de caja que usa UNA pieza
        - Se acumulan fracciones y se redondea hacia arriba (ceil)
        """
        import math
        
        # Get cancha filter from config
        cancha_filter = self.data_repo.get_config(key="planner_demolding_cancha", default="TCF-L1400") or "TCF-L1400"
        
        # Get flask type configuration (codes for prefix matching)
        with self.db.connect() as con:
            flask_config = con.execute(
                """
                SELECT flask_type, codes_csv
                FROM planner_flask_types
                WHERE scenario_id = 1
                """,
            ).fetchall()
        
        flask_codes_map: dict[str, str] = {}
        for row in flask_config:
            flask_type = str(row["flask_type"] or "").strip().upper()
            codes_csv = str(row["codes_csv"] or "").strip()
            if codes_csv:
                for code in codes_csv.split(","):
                    code = code.strip()
                    if code:
                        flask_codes_map[code] = flask_type
        
        # Sort codes by length descending (match longest prefix first)
        sorted_codes = sorted(flask_codes_map.items(), key=lambda x: len(x[0]), reverse=True)
        
        # Read demolding snapshot filtered by cancha
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT
                    d.material,
                    d.flask_id,
                    d.demolding_date,
                    d.mold_quantity,
                    d.cooling_hours
                FROM sap_demolding_snapshot d
                WHERE d.cancha = ?
                """,
                (cancha_filter,),
            ).fetchall()
        
        flask_fractions: dict[str, float] = {}
        
        for r in rows:
            material = str(r["material"] or "").strip()
            flask_id = str(r["flask_id"] or "").strip()
            demolding_date_str = str(r["demolding_date"] or "").strip()
            mold_qty = float(r["mold_quantity"] or 0.0)
            
            # Skip if mold_qty invalid or missing data
            if mold_qty <= 0 or not flask_id or not demolding_date_str:
                continue
            
            try:
                demolding_date = date.fromisoformat(demolding_date_str)
            except Exception:
                continue
            
            # IMPORTANTE: Si fecha de desmoldeo está en el pasado, asumir que es hoy
            if demolding_date < asof_date:
                demolding_date = asof_date
            
            # Flask is occupied until demolding_date + 1 day
            release_date = date.fromordinal(demolding_date.toordinal() + 1)
            
            # Only count flasks still occupied (release_date > asof_date)
            if release_date <= asof_date:
                continue
            
            # Extract first 3 characters as flask type code
            flask_code = flask_id[:3] if len(flask_id) >= 3 else flask_id
            
            # Determine flask type: first try prefix match, then use code directly
            flask_type = None
            if sorted_codes:
                for prefix, ftype in sorted_codes:
                    if flask_code.startswith(prefix):
                        flask_type = ftype
                        break
            
            if not flask_type:
                flask_type = flask_code.upper()
            
            flask_fractions[flask_type] = flask_fractions.get(flask_type, 0.0) + mold_qty
        
        # Convert fractions to integer counts (ceil)
        flask_counts = {ftype: math.ceil(fraction) for ftype, fraction in flask_fractions.items()}
        
        return flask_counts

    def rebuild_daily_resources_from_config(self, *, scenario_id: int = 1) -> None:
        """Regenerate planner_daily_resources table from configuration.
        
        Creates daily availability baseline for:
        - Flask types (from planner_flask_types config)
        - Molding capacities (from molding_per_shift × turnos_día)
        - Same mold capacities (from same_mold_per_shift × turnos_día)
        - Pouring capacity (from pour_per_shift × turnos_día)
        
        Uses shift configuration and holidays to determine working days.
        Horizon is min(config_horizon, days_to_cover_all_vision_orders).
        
        Called automatically when saving planner config or importing desmoldeo.
        
        Args:
            scenario_id: Planner scenario (default 1)
        """
        # Get horizon configuration
        horizon_config = int(self.data_repo.get_config(key="planner_horizon_days", default="180") or 180)
        
        # Calculate max due date from Vision orders to determine minimum required horizon
        with self.db.connect() as con:
            max_date_row = con.execute(
                """
                SELECT MAX(fecha_de_pedido) as max_fecha
                FROM sap_vision_snapshot
                WHERE fecha_de_pedido IS NOT NULL
                """
            ).fetchone()
        
        today = date.today()
        horizon_days = horizon_config  # Default to config
        
        if max_date_row and max_date_row["max_fecha"]:
            try:
                max_vision_date = date.fromisoformat(str(max_date_row["max_fecha"]))
                days_to_max_vision = (max_vision_date.toordinal() - today.toordinal()) + 1
                # Use minimum between config and required to cover all orders
                horizon_days = min(horizon_config, max(days_to_max_vision, 30))  # At least 30 days
            except Exception:
                pass
        
        logger.info(f"Using horizon_days={horizon_days} (config={horizon_config})")
        
        # Get flask configuration
        with self.db.connect() as con:
            flask_config = con.execute(
                """
                SELECT flask_type, qty_total
                FROM planner_flask_types
                WHERE scenario_id = ?
                ORDER BY flask_type
                """,
                (scenario_id,),
            ).fetchall()
        
        # Get resource/shift configuration
        with self.db.connect() as con:
            resource_row = con.execute(
                """
                SELECT 
                    molding_max_per_shift,
                    molding_max_same_part_per_day,
                    molding_shifts_json,
                    pour_max_ton_per_shift,
                    pour_shifts_json
                FROM planner_resources
                WHERE scenario_id = ?
                """,
                (scenario_id,),
            ).fetchone()
        
        if not resource_row:
            logger.warning(f"No resource configuration found for scenario {scenario_id}")
            return
        
        # Parse shift configurations
        molding_shifts_json = resource_row["molding_shifts_json"] or "{}"
        pour_shifts_json = resource_row["pour_shifts_json"] or "{}"
        
        try:
            molding_shifts = json.loads(molding_shifts_json)
        except Exception:
            molding_shifts = {}
        
        try:
            pour_shifts = json.loads(pour_shifts_json)
        except Exception:
            pour_shifts = {}
        
        # Get working days from shifts
        working_days = self._get_working_days_from_shifts(molding_shifts, pour_shifts)
        
        # Get holidays
        holidays = self._planner_holidays()
        
        # Get molding/pouring capacities per shift
        molding_per_shift = int(resource_row["molding_max_per_shift"] or 0)
        same_mold_per_shift = int(resource_row["molding_max_same_part_per_day"] or 0)
        pour_max_per_shift = float(resource_row["pour_max_ton_per_shift"] or 0.0)
        
        # Generate daily records for horizon
        rows_to_insert = []
        
        for day_offset in range(horizon_days):
            current_date = date.fromordinal(today.toordinal() + day_offset)
            weekday = current_date.weekday()
            
            # Skip non-working days (weekends/holidays)
            if weekday not in working_days or current_date in holidays:
                continue
            
            day_str = current_date.isoformat()
            
            # Get shift count for this day
            day_names = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]
            day_name = day_names[weekday]
            molding_shifts_count = molding_shifts.get(day_name, 0)
            pour_shifts_count = pour_shifts.get(day_name, 0)
            
            # Calculate daily capacities (capacity_per_shift × number_of_shifts)
            daily_molding_capacity = molding_per_shift * molding_shifts_count
            daily_same_mold_capacity = same_mold_per_shift * molding_shifts_count
            daily_pour_tons = pour_max_per_shift * pour_shifts_count
            
            # Insert flask availability (all types have full capacity initially)
            for flask_row in flask_config:
                flask_type = str(flask_row["flask_type"])
                qty_total = int(flask_row["qty_total"] or 0)
                
                rows_to_insert.append((
                    scenario_id,
                    day_str,
                    flask_type,
                    qty_total,  # available_qty
                    daily_molding_capacity,  # molding_capacity_per_day
                    daily_same_mold_capacity,  # same_mold_capacity_per_day
                    daily_pour_tons,  # pouring_tons_available
                ))
        
        # Replace entire table (fresh rebuild)
        with self.db.connect() as con:
            con.execute("DELETE FROM planner_daily_resources WHERE scenario_id = ?", (scenario_id,))
            con.executemany(
                """
                INSERT INTO planner_daily_resources 
                (scenario_id, day, flask_type, available_qty, molding_capacity_per_day, same_mold_capacity_per_day, pouring_tons_available)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                rows_to_insert,
            )
            con.commit()
        
        logger.info(f"Rebuilt {len(rows_to_insert)} daily resource records for scenario {scenario_id} (horizon={horizon_days})")

    def update_daily_resources_from_demolding(self, *, scenario_id: int = 1) -> None:
        """Update planner_daily_resources by subtracting occupied flasks from demolding.
        
        For each flask in desmoldeo snapshot (filtered by cancha):
        - Decrement available_qty from today until demolding_date + 1
        - Handles past dates by treating them as today
        - Applies cancha filter from configuration
        
        Call this after importing desmoldeo data.
        """
        # Get cancha filter from config
        cancha_filter = self.data_repo.get_config(key="planner_demolding_cancha", default="TCF-L1400") or "TCF-L1400"
        
        # Get flask type configuration (codes for prefix matching)
        with self.db.connect() as con:
            flask_config = con.execute(
                """
                SELECT flask_type, codes_csv
                FROM planner_flask_types
                WHERE scenario_id = ?
                """,
                (scenario_id,),
            ).fetchall()
        
        flask_codes_map: dict[str, str] = {}
        for row in flask_config:
            flask_type = str(row["flask_type"] or "").strip().upper()
            codes_csv = str(row["codes_csv"] or "").strip()
            if codes_csv:
                for code in codes_csv.split(","):
                    code = code.strip()
                    if code:
                        flask_codes_map[code] = flask_type
        
        # Sort codes by length descending (match longest prefix first)
        sorted_codes = sorted(flask_codes_map.items(), key=lambda x: len(x[0]), reverse=True)
        
        # Read demolding snapshot filtered by cancha
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT
                    d.flask_id,
                    d.demolding_date,
                    d.mold_quantity
                FROM sap_demolding_snapshot d
                WHERE d.cancha = ?
                """,
                (cancha_filter,),
            ).fetchall()
        
        today = date.today()
        # Acumular fracciones por día/flask_type antes de actualizar
        # mold_quantity es la fracción de caja que usa UNA pieza
        daily_decrements: dict[tuple[str, str], float] = {}  # (day, flask_type) -> qty_to_decrement
        
        for r in rows:
            flask_id = str(r["flask_id"] or "").strip()
            demolding_date_str = str(r["demolding_date"] or "").strip()
            mold_qty = float(r["mold_quantity"] or 0.0)
            
            # Skip si mold_qty es 0 o negativo (datos inválidos)
            if mold_qty <= 0:
                continue
            
            # Skip if missing critical data
            if not flask_id or not demolding_date_str:
                continue
            
            try:
                demolding_date = date.fromisoformat(demolding_date_str)
            except Exception:
                continue
            
            # IMPORTANTE: Si fecha de desmoldeo está en el pasado, usar hoy
            if demolding_date < today:
                demolding_date = today
            
            # Flask is occupied until demolding_date + 1 day
            release_date = date.fromordinal(demolding_date.toordinal() + 1)
            
            # Extract first 3 characters as flask type code
            flask_code = flask_id[:3] if len(flask_id) >= 3 else flask_id
            
            # Determine flask type: first try prefix match, then use code directly
            flask_type = None
            if sorted_codes:
                for prefix, ftype in sorted_codes:
                    if flask_code.startswith(prefix):
                        flask_type = ftype
                        break
            
            if not flask_type:
                flask_type = flask_code.upper()
            
            # Acumular fracciones para cada día desde hoy hasta release_date (exclusive)
            current_day = today
            while current_day < release_date:
                day_str = current_day.isoformat()
                key = (day_str, flask_type)
                daily_decrements[key] = daily_decrements.get(key, 0.0) + mold_qty
                current_day = date.fromordinal(current_day.toordinal() + 1)
        
        # Apply decrements to database (convertir fracciones acumuladas a enteros con ceil)
        import math
        updates = []
        for (day_str, flask_type), total_fraction in daily_decrements.items():
            # Redondear hacia arriba: si usamos 0.5 cajas, ocupamos 1 caja completa
            flasks_occupied = math.ceil(total_fraction)
            updates.append((flasks_occupied, day_str, flask_type, scenario_id))
        
        with self.db.connect() as con:
            con.executemany(
                """
                UPDATE planner_daily_resources
                SET available_qty = MAX(0, available_qty - ?)
                WHERE day = ? AND flask_type = ? AND scenario_id = ?
                """,
                updates,
            )
            con.commit()
        
        logger.info(f"Updated {len(updates)} daily resource records from demolding for scenario {scenario_id} (processed {len(rows)} demolding records)")

    # ---------- Planner DB helpers ----------
    def ensure_planner_scenario(self, *, name: str | None = None) -> int:
        scenario_name = str(name or "default").strip() or "default"
        with self.db.connect() as con:
            row = con.execute(
                "SELECT scenario_id FROM planner_scenarios WHERE name = ?",
                (scenario_name,),
            ).fetchone()
            if row:
                return int(row[0])
            con.execute("INSERT INTO planner_scenarios(name) VALUES(?)", (scenario_name,))
            return int(con.execute("SELECT last_insert_rowid()").fetchone()[0])

    def replace_planner_parts(self, *, scenario_id: int, rows: list[tuple]) -> None:
        with self.db.connect() as con:
            con.execute("DELETE FROM planner_parts WHERE scenario_id = ?", (int(scenario_id),))
            con.executemany(
                """
                INSERT INTO planner_parts(
                    scenario_id, part_id, flask_size, cool_hours, finish_hours, min_finish_hours,
                    pieces_per_mold, net_weight_ton, alloy
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def replace_planner_orders(self, *, scenario_id: int, rows: list[tuple]) -> None:
        with self.db.connect() as con:
            con.execute("DELETE FROM planner_orders WHERE scenario_id = ?", (int(scenario_id),))
            con.executemany(
                """
                INSERT INTO planner_orders(
                    scenario_id, order_id, part_id, qty, due_date, priority
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def get_planner_orders_rows(self, *, scenario_id: int) -> list[dict]:
        """Return planner orders for UI selection (patterns loaded)."""
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT order_id, part_id, qty, due_date, priority
                FROM planner_orders
                WHERE scenario_id = ?
                ORDER BY priority ASC, due_date ASC, order_id ASC
                """,
                (int(scenario_id),),
            ).fetchall()
        return [
            {
                "order_id": str(r[0]),
                "part_id": str(r[1]),
                "qty": int(r[2] or 0),
                "due_date": str(r[3] or ""),
                "priority": int(r[4] or 0),
            }
            for r in rows
        ]

    def get_planner_parts_rows(self, *, scenario_id: int) -> list[dict]:
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT part_id, flask_size, cool_hours, finish_hours, min_finish_hours,
                       pieces_per_mold, net_weight_ton, alloy
                FROM planner_parts
                WHERE scenario_id = ?
                """,
                (int(scenario_id),),
            ).fetchall()
        return [
            {
                "part_id": str(r[0]),
                "flask_type": str(r[1] or ""),
                "cool_hours": float(r[2] or 0.0),
                "finish_hours": float(r[3] or 0.0),
                "min_finish_hours": float(r[4] or 0.0),
                "pieces_per_mold": float(r[5] or 0.0),
                "net_weight_ton": float(r[6] or 0.0),
                "alloy": str(r[7]) if r[7] is not None else None,
            }
            for r in rows
        ]

    def get_planner_calendar_rows(self, *, scenario_id: int) -> list[dict]:
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT workday_index, date
                FROM planner_calendar_workdays
                WHERE scenario_id = ?
                ORDER BY workday_index ASC
                """,
                (int(scenario_id),),
            ).fetchall()
        return [
            {"workday_index": int(r[0]), "date": str(r[1])}
            for r in rows
        ]

    def replace_planner_calendar(self, *, scenario_id: int, rows: list[tuple]) -> None:
        with self.db.connect() as con:
            con.execute("DELETE FROM planner_calendar_workdays WHERE scenario_id = ?", (int(scenario_id),))
            con.executemany(
                """
                INSERT INTO planner_calendar_workdays(
                    scenario_id, workday_index, date, week_index
                ) VALUES(?, ?, ?, ?)
                """,
                rows,
            )

    def get_planner_resources(self, *, scenario_id: int) -> dict | None:
        with self.db.connect() as con:
            row = con.execute(
                """
                SELECT molding_max_per_day, molding_max_same_part_per_day, pour_max_ton_per_day, notes,
                       molding_max_per_shift, molding_shifts_json, pour_max_ton_per_shift, pour_shifts_json
                FROM planner_resources
                WHERE scenario_id = ?
                """,
                (int(scenario_id),),
            ).fetchone()
            flask_rows = con.execute(
                """
                SELECT flask_type, qty_total, codes_csv, label, notes
                FROM planner_flask_types
                WHERE scenario_id = ?
                ORDER BY flask_type
                """,
                (int(scenario_id),),
            ).fetchall()
        if not row:
            return None
        
        # Parse shift configuration
        import json
        molding_shifts_json = str(row["molding_shifts_json"] or "")
        pour_shifts_json = str(row["pour_shifts_json"] or "")
        
        molding_shifts = {}
        pour_shifts = {}
        try:
            if molding_shifts_json:
                molding_shifts = json.loads(molding_shifts_json)
        except Exception:
            pass
        try:
            if pour_shifts_json:
                pour_shifts = json.loads(pour_shifts_json)
        except Exception:
            pass
        
        return {
            "molding_max_per_day": int(row["molding_max_per_day"] or 0),
            "molding_max_same_part_per_day": int(row["molding_max_same_part_per_day"] or 0),
            "pour_max_ton_per_day": float(row["pour_max_ton_per_day"] or 0.0),
            "molding_max_per_shift": int(row["molding_max_per_shift"] or 0),
            "molding_shifts": molding_shifts,
            "pour_max_ton_per_shift": float(row["pour_max_ton_per_shift"] or 0.0),
            "pour_shifts": pour_shifts,
            "notes": str(row["notes"] or ""),
            "flask_types": [
                {
                    "flask_type": str(r["flask_type"] or ""),
                    "qty_total": int(r["qty_total"] or 0),
                    "codes_csv": str(r["codes_csv"] or ""),
                    "label": str(r["label"] or ""),
                    "notes": str(r["notes"] or ""),
                }
                for r in flask_rows
            ],
        }

    def list_planner_flask_types(self, *, scenario_id: int) -> list[dict]:
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT flask_type, qty_total, codes_csv, label, notes
                FROM planner_flask_types
                WHERE scenario_id = ?
                ORDER BY flask_type
                """,
                (int(scenario_id),),
            ).fetchall()
        return [
            {
                "flask_type": str(r["flask_type"] or ""),
                "qty_total": int(r["qty_total"] or 0),
                "codes_csv": str(r["codes_csv"] or ""),
                "label": str(r["label"] or ""),
                "notes": str(r["notes"] or ""),
            }
            for r in rows
        ]

    def upsert_planner_resources(
        self,
        *,
        scenario_id: int,
        molding_max_per_day: int | None = None,
        molding_max_same_part_per_day: int | None = None,
        pour_max_ton_per_day: float | None = None,
        molding_max_per_shift: int | None = None,
        molding_shifts: dict | None = None,
        pour_max_ton_per_shift: float | None = None,
        pour_shifts: dict | None = None,
        notes: str | None = None,
    ) -> None:
        import json
        
        # Convert shift dicts to JSON
        molding_shifts_json = json.dumps(molding_shifts) if molding_shifts is not None else None
        pour_shifts_json = json.dumps(pour_shifts) if pour_shifts is not None else None
        
        with self.db.connect() as con:
            con.execute(
                """
                INSERT INTO planner_resources(
                    scenario_id,
                    molding_max_per_day,
                    molding_max_same_part_per_day,
                    pour_max_ton_per_day,
                    molding_max_per_shift,
                    molding_shifts_json,
                    pour_max_ton_per_shift,
                    pour_shifts_json,
                    notes
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scenario_id) DO UPDATE SET
                    molding_max_per_day=COALESCE(excluded.molding_max_per_day, molding_max_per_day),
                    molding_max_same_part_per_day=COALESCE(excluded.molding_max_same_part_per_day, molding_max_same_part_per_day),
                    pour_max_ton_per_day=COALESCE(excluded.pour_max_ton_per_day, pour_max_ton_per_day),
                    molding_max_per_shift=COALESCE(excluded.molding_max_per_shift, molding_max_per_shift),
                    molding_shifts_json=COALESCE(excluded.molding_shifts_json, molding_shifts_json),
                    pour_max_ton_per_shift=COALESCE(excluded.pour_max_ton_per_shift, pour_max_ton_per_shift),
                    pour_shifts_json=COALESCE(excluded.pour_shifts_json, pour_shifts_json),
                    notes=excluded.notes
                """,
                (
                    int(scenario_id),
                    int(molding_max_per_day) if molding_max_per_day is not None else None,
                    int(molding_max_same_part_per_day) if molding_max_same_part_per_day is not None else None,
                    float(pour_max_ton_per_day) if pour_max_ton_per_day is not None else None,
                    int(molding_max_per_shift) if molding_max_per_shift is not None else None,
                    molding_shifts_json,
                    float(pour_max_ton_per_shift) if pour_max_ton_per_shift is not None else None,
                    pour_shifts_json,
                    str(notes).strip() if notes else None,
                ),
            )

    def upsert_planner_flask_type(
        self,
        *,
        scenario_id: int,
        flask_type: str,
        qty_total: int,
        codes_csv: str | None = None,
        label: str | None = None,
        notes: str | None = None,
    ) -> None:
        ftype = str(flask_type or "").strip().upper()
        if not ftype:
            raise ValueError("flask_type vacío")
        with self.db.connect() as con:
            con.execute(
                """
                INSERT INTO planner_flask_types(
                    scenario_id, flask_type, qty_total, codes_csv, label, notes
                ) VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(scenario_id, flask_type) DO UPDATE SET
                    qty_total=excluded.qty_total,
                    codes_csv=excluded.codes_csv,
                    label=excluded.label,
                    notes=excluded.notes
                """,
                (
                    int(scenario_id),
                    ftype,
                    int(qty_total),
                    str(codes_csv).strip() if codes_csv else None,
                    str(label).strip() if label else ftype,
                    str(notes).strip() if notes else None,
                ),
            )

    def delete_planner_flask_type(self, *, scenario_id: int, flask_type: str) -> None:
        ftype = str(flask_type or "").strip().upper()
        if not ftype:
            return
        with self.db.connect() as con:
            con.execute(
                "DELETE FROM planner_flask_types WHERE scenario_id = ? AND flask_type = ?",
                (int(scenario_id), ftype),
            )

    def update_master_flasks_from_history(self, flask_codes_map: dict[str, str] | None) -> None:
        """Update material_master.flask_size based on observed usage in Demolding + Configured Codes."""
        if not flask_codes_map:
            return
            
        sorted_codes = sorted(flask_codes_map.items(), key=lambda x: len(x[0]), reverse=True)
        
        with self.db.connect() as con:
            # Only look at materials where we have data
            rows = con.execute(
                """
                SELECT material, flask_id 
                FROM sap_demolding_snapshot 
                WHERE flask_id IS NOT NULL AND flask_id <> ''
                GROUP BY material, flask_id
                """
            ).fetchall()
        
        updates: dict[str, str] = {}
        for r in rows:
            mat = str(r["material"]).strip()
            fid = str(r["flask_id"]).strip()
            
            size = None
            for prefix, s in sorted_codes:
                if fid.startswith(prefix):
                    size = s
                    break
            
            if size:
                updates[mat] = size
        
        if updates:
            with self.db.connect() as con:
                con.executemany(
                    "UPDATE material_master SET flask_size = COALESCE(flask_size, ?) WHERE material = ?",
                    [(size, mat) for mat, size in updates.items()]
                )

    def sync_planner_inputs_from_sap(
        self,
        *,
        scenario_id: int,
        asof_date: date,
        horizon_buffer_days: int = 10,
    ) -> dict:
        """Build planner inputs from current SAP snapshots and master data.

        Returns summary stats.
        """
        asof_iso = asof_date.isoformat()
        
        # 1. Fetch resources and auto-update material master flask info from demolding history
        planner_res = self.get_planner_resources(scenario_id=scenario_id)
        flask_codes_map: dict[str, str] = {}
        max_pour = 100.0

        if planner_res:
            max_pour = float(planner_res.get("pour_max_ton_per_day", 100.0))
            for ft in planner_res.get("flask_types", []) or []:
                ftype = str(ft.get("flask_type") or "").strip().upper()
                codes_str = str(ft.get("codes_csv") or "")
                if codes_str:
                    for code in codes_str.split(","):
                        c = code.strip()
                        if c:
                            flask_codes_map[c] = ftype
        
        self.update_master_flasks_from_history(flask_codes_map)

        # Orders from Vision
        with self.db.connect() as con:
            orders_rows = con.execute(
                """
                SELECT
                    v.pedido,
                    v.posicion,
                    MAX(COALESCE(v.cod_material, '')) AS material,
                    MAX(COALESCE(v.fecha_de_pedido, '')) AS fecha_de_pedido,
                    MAX(COALESCE(v.solicitado, 0)) AS solicitado
                FROM sap_vision_snapshot v
                GROUP BY v.pedido, v.posicion
                HAVING MAX(COALESCE(v.fecha_de_pedido, '')) <> ''
                """,
            ).fetchall()

            prio_rows = con.execute(
                """
                SELECT pedido, posicion, COALESCE(kind,'') AS kind
                FROM orderpos_priority
                WHERE COALESCE(is_priority, 0) = 1
                """,
            ).fetchall()

            prio_map: dict[tuple[str, str], str] = {}
            for r in prio_rows:
                prio_map[(str(r[0]).strip(), str(r[1]).strip())] = str(r[2] or "").strip().lower()

        orders_out: list[tuple] = []
        max_due = None
        for r in orders_rows:
            pedido = str(r["pedido"]).strip()
            posicion = str(r["posicion"]).strip()
            material = str(r["material"]).strip()
            due = str(r["fecha_de_pedido"]).strip()
            qty = int(r["solicitado"] or 0)
            if not pedido or not posicion or not material or not due:
                continue
            order_id = f"{pedido}/{posicion}"
            kind = prio_map.get((pedido, posicion), "")
            if kind == "test":
                priority = 1
            elif kind:
                priority = 10
            else:
                priority = 100
            orders_out.append((scenario_id, order_id, material, qty, due, priority))
            try:
                d = date.fromisoformat(due)
                if max_due is None or d > max_due:
                    max_due = d
            except Exception:
                pass

        if not orders_out:
            raise ValueError("No hay órdenes válidas en Visión para planificar")

        # Parts from material_master for referenced materials
        materials = sorted({o[2] for o in orders_out})
        with self.db.connect() as con:
            rows = con.execute(
                f"""
                SELECT
                    material,
                    flask_size,
                    tiempo_enfriamiento_molde_dias,
                    peso_unitario_ton,
                    aleacion,
                    piezas_por_molde,
                    finish_hours,
                    min_finish_hours
                FROM material_master
                WHERE material IN ({','.join(['?'] * len(materials))})
                """,
                materials,
            ).fetchall()

        part_map = {str(r[0]): r for r in rows}
        missing_parts: list[str] = []
        parts_out: list[tuple] = []
        max_lag_days = 0
        for mat in materials:
            r = part_map.get(mat)
            if not r:
                missing_parts.append(mat)
                continue
            flask_size = str(r[1] or "").strip().upper()
            cool_hours = float(r[2] or 0.0)  # Now stored as hours directly
            weight = float(r[3] or 0.0)
            alloy = str(r[4] or "").strip() or None
            pieces_per_mold = float(r[5] or 0.0)
            finish_hours = float(r[6] or 0.0) * 24.0  # Convert days to hours
            min_finish_hours = float(r[7] or 0.0) * 24.0  # Convert days to hours
            
            # Apply defaults for missing/invalid data to avoid skipping orders
            if not flask_size:
                flask_size = "UNKNOWN"
            if cool_hours <= 0:
                cool_hours = 24.0
            if pieces_per_mold <= 0:
                pieces_per_mold = 1.0
            if finish_hours <= 0:
                finish_hours = 24.0
            if min_finish_hours <= 0:
                min_finish_hours = 24.0
            if min_finish_hours > finish_hours:
                min_finish_hours = finish_hours

            parts_out.append(
                (
                    scenario_id,
                    mat,
                    flask_size,
                    cool_hours,
                    finish_hours,
                    min_finish_hours,
                    pieces_per_mold,
                    weight,
                    alloy,
                )
            )
            lag_days = 1 + int(math.ceil(cool_hours / 24.0)) + 1 + int(math.ceil(finish_hours / 24.0)) + 1
            if lag_days > max_lag_days:
                max_lag_days = lag_days
        
        # Valid parts set for filtering orders
        valid_parts = {p[1] for p in parts_out}

        # Filter out orders for missing parts
        filtered_orders_out = [o for o in orders_out if o[2] in valid_parts]
        skipped_orders_count = len(orders_out) - len(filtered_orders_out)
        orders_out = filtered_orders_out

        missing_parts_list = sorted(set(missing_parts))

        # Build calendar_workdays based on shift configuration
        holidays = self._planner_holidays()
        
        # Get working days from shift configuration
        molding_shifts = planner_res.get("molding_shifts", {}) if planner_res else {}
        pour_shifts = planner_res.get("pour_shifts", {}) if planner_res else {}
        working_weekdays = self._get_working_days_from_shifts(molding_shifts, pour_shifts)
        
        if max_due is None:
            max_due = asof_date
        target_end = max_due.toordinal() + max_lag_days + int(horizon_buffer_days)
        workdays: list[tuple] = []
        d = asof_date
        idx = 0
        while d.toordinal() <= target_end:
            # Check if day is working (configured weekday and not a holiday)
            if d.weekday() in working_weekdays and d not in holidays:
                week_index = idx // 5  # Keep week_index for compatibility
                workdays.append((scenario_id, idx, d.isoformat(), week_index))
                idx += 1
            d = date.fromordinal(d.toordinal() + 1)

        # Initial order progress (credit)
        progress_rows = self.get_planner_initial_order_progress(asof_date=asof_date)
        progress_out = [(scenario_id, r["asof_date"], r["order_id"], int(r["remaining_molds"])) for r in progress_rows]

        # Initial flask in use: from Reporte Desmoldeo (required - tracks actual shakeout dates)
        flask_rows = self.get_planner_initial_flask_inuse_from_demolding(
            asof_date=asof_date,
            flask_codes_map=flask_codes_map,
        )

        flask_out = [
            (
                scenario_id,
                r["asof_date"],
                r.get("flask_type") or r.get("flask_size"),
                int(r["release_workday_index"]),
                int(r["qty_inuse"]),
            )
            for r in flask_rows
        ]

        # Initial pour load: forward-fill WIP molds from MB52
        wip_molds = self.get_planner_initial_pour_load(asof_date=asof_date)

        # Forward-fill: allocate WIP to earliest workdays
        pour_load_by_day: dict[int, float] = {}
        day_idx = 0
        for mold_info in wip_molds:
            metal = float(mold_info["metal_per_mold"])
            cnt = int(mold_info["cnt"])
            total_metal = metal * cnt
            while total_metal > 0:
                capacity_left = max_pour - pour_load_by_day.get(day_idx, 0.0)
                allocated = min(total_metal, capacity_left)
                pour_load_by_day[day_idx] = pour_load_by_day.get(day_idx, 0.0) + allocated
                total_metal -= allocated
                if total_metal > 0:
                    day_idx += 1

        pour_out = [
            (scenario_id, asof_date.isoformat(), idx, tons)
            for idx, tons in sorted(pour_load_by_day.items())
        ]

        # Persist all
        self.replace_planner_parts(scenario_id=scenario_id, rows=parts_out)
        self.replace_planner_orders(scenario_id=scenario_id, rows=orders_out)
        self.replace_planner_calendar(scenario_id=scenario_id, rows=workdays)
        self.replace_planner_initial_order_progress(scenario_id=scenario_id, rows=progress_out)
        self.replace_planner_initial_flask_inuse(scenario_id=scenario_id, rows=flask_out)
        self.replace_planner_initial_pour_load(scenario_id=scenario_id, rows=pour_out)

        return {
            "scenario_id": int(scenario_id),
            "orders": len(orders_out),
            "parts": len(parts_out),
            "workdays": len(workdays),
            "missing_parts": missing_parts_list,
            "skipped_orders": skipped_orders_count,
        }

    def get_daily_resources_rows(self, *, scenario_id: int) -> list[dict]:
        """Get all daily resources for a scenario (for UI display)."""
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT day, flask_type, available_qty, molding_capacity_per_day, 
                       same_mold_capacity_per_day, pouring_tons_available
                FROM planner_daily_resources
                WHERE scenario_id = ?
                ORDER BY day ASC
                """,
                (scenario_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_daily_resources_for_today(self, *, scenario_id: int) -> list[dict]:
        """Get daily resources for today only (for initial conditions calculation)."""
        today_str = date.today().isoformat()
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT day, flask_type, available_qty
                FROM planner_daily_resources
                WHERE scenario_id = ? AND day = ?
                """,
                (scenario_id, today_str),
            ).fetchall()
        return [dict(r) for r in rows]
