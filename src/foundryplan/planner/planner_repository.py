"""Planner repository layer - manages planner-specific database operations."""

from __future__ import annotations

import json
import math
import re
from datetime import date, timedelta
from typing import TYPE_CHECKING

from foundryplan.data.repo_utils import logger
from foundryplan.data.material_codes import extract_part_code

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
        
        # Get cancha filter from config (comma-separated list)
        default_canchas = "TCF-L1000,TCF-L1100,TCF-L1200,TCF-L1300,TCF-L1400,TCF-L1500,TCF-L1600,TCF-L1700,TCF-L3000,TDE-D0001,TDE-D0002,TDE-D0003"
        canchas_config = self.data_repo.get_config(key="planner_demolding_cancha", default=default_canchas) or default_canchas
        valid_canchas = tuple(c.strip().upper() for c in canchas_config.split(",") if c.strip())
        
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
        
        # Read core_moldes_por_fundir (WIP molds only, already filtered by valid canchas)
        # No need to filter by cancha here since import already did it
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT
                    m.material,
                    m.flask_id,
                    m.poured_date,
                    m.mold_quantity,
                    m.cooling_hours
                FROM core_moldes_por_fundir m
                """
            ).fetchall()
        
        flask_fractions: dict[str, float] = {}
        
        for r in rows:
            material = str(r["material"] or "").strip()
            flask_id = str(r["flask_id"] or "").strip()
            poured_date_str = str(r["poured_date"] or "").strip()
            mold_qty = float(r["mold_quantity"] or 0.0)
            cooling_hours = float(r["cooling_hours"] or 0.0)
            
            # Skip if mold_qty invalid or missing data
            if mold_qty <= 0 or not flask_id or not poured_date_str:
                continue
            
            try:
                poured_date = date.fromisoformat(poured_date_str)
            except Exception:
                continue
            
            # Calculate expected demolding date from poured_date + cooling_hours
            cooling_days = int(cooling_hours / 24) if cooling_hours > 0 else 0
            expected_demolding = date.fromordinal(poured_date.toordinal() + cooling_days)
            
            # IMPORTANTE: Si fecha de desmoldeo estimada está en el pasado, asumir que es hoy
            if expected_demolding < asof_date:
                expected_demolding = asof_date
            
            # Flask is occupied until expected_demolding + 1 day
            release_date = date.fromordinal(expected_demolding.toordinal() + 1)
            
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
        - Molding capacities (molding_per_shift × shifts_per_day)
        - Same mold capacities (same_mold_per_shift × shifts_per_day)
        - Pouring capacity (pour_per_shift × shifts_per_day)
        
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
                FROM core_sap_vision_snapshot
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
        same_mold_per_shift = int(resource_row["molding_max_same_part_per_day"] or 0)  # Config stores per-shift value
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
            # All capacities are per-shift and get multiplied by shifts_per_day
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
        """Update planner_daily_resources by processing demolding data.
        
        Phases:
        1. Rebuild baseline resources (already done by rebuild_daily_resources_from_config)
        2. Decrement flasks from core_piezas_fundidas (completed pieces with demolding_date)
        3. Mini-program: schedule pouring for core_moldes_por_fundir and decrement flasks/pouring
        
        Call this after importing desmoldeo data.
        """
        import math
        
        today = date.today()
        
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
        
        def _get_flask_type(flask_id: str) -> str:
            """Extract flask type from flask_id using configured codes."""
            flask_code = flask_id[:3] if len(flask_id) >= 3 else flask_id
            for prefix, ftype in sorted_codes:
                if flask_code.startswith(prefix):
                    return ftype
            return flask_code.upper()
        
        # =============================================================================
        # PHASE 2: Decrement flasks from completed pieces (core_piezas_fundidas)
        # =============================================================================
        with self.db.connect() as con:
            piezas_rows = con.execute(
                """
                SELECT
                    flask_id,
                    demolding_date,
                    mold_quantity
                FROM core_piezas_fundidas
                WHERE flask_id IS NOT NULL AND flask_id <> ''
                """
            ).fetchall()
        
        daily_decrements_piezas: dict[tuple[str, str], float] = {}  # (day, flask_type) -> qty
        
        for r in piezas_rows:
            flask_id = str(r["flask_id"] or "").strip()
            demolding_date_str = str(r["demolding_date"] or "").strip()
            mold_qty = float(r["mold_quantity"] or 0.0)
            
            if mold_qty <= 0 or not flask_id or not demolding_date_str:
                continue
            
            try:
                demolding_date = date.fromisoformat(demolding_date_str)
            except Exception:
                continue
            
            # If demolding_date is in the past, treat as today
            if demolding_date < today:
                demolding_date = today
            
            # Flasks occupied from today until demolding_date + 1 (exclusive)
            release_date = date.fromordinal(demolding_date.toordinal() + 1)
            flask_type = _get_flask_type(flask_id)
            
            current_day = today
            while current_day < release_date:
                day_str = current_day.isoformat()
                key = (day_str, flask_type)
                daily_decrements_piezas[key] = daily_decrements_piezas.get(key, 0.0) + mold_qty
                current_day = date.fromordinal(current_day.toordinal() + 1)
        
        # Apply piezas fundidas decrements (ceil fractions)
        updates_piezas = []
        for (day_str, flask_type), total_fraction in daily_decrements_piezas.items():
            flasks_occupied = math.ceil(total_fraction)
            updates_piezas.append((flasks_occupied, day_str, flask_type, scenario_id))
        
        with self.db.connect() as con:
            con.executemany(
                """
                UPDATE planner_daily_resources
                SET available_qty = MAX(0, available_qty - ?)
                WHERE day = ? AND flask_type = ? AND scenario_id = ?
                """,
                updates_piezas,
            )
            con.commit()
        
        logger.info(f"Phase 2: Decremented {len(updates_piezas)} daily flask records from piezas_fundidas")
        
        # =============================================================================
        # PHASE 3: Mini-program for moldes_por_fundir (schedule pouring, decrement resources)
        # =============================================================================
        
        # Get material master data (peso_unitario_ton, tiempo_enfriamiento)
        with self.db.connect() as con:
            material_master = con.execute(
                """
                SELECT part_code, peso_unitario_ton, tiempo_enfriamiento_molde_horas
                FROM core_material_master
                """
            ).fetchall()
        
        material_data = {}
        for row in material_master:
            part_code = str(row["part_code"] or "").strip()
            peso_unitario_ton = float(row["peso_unitario_ton"] or 0.0)
            cooling_hours = float(row["tiempo_enfriamiento_molde_horas"] or 72.0)  # Default 72h
            material_data[part_code] = {"peso_unitario_ton": peso_unitario_ton, "cooling_hours": cooling_hours}
        
        # Get moldes_por_fundir (not yet poured)
        with self.db.connect() as con:
            moldes_rows = con.execute(
                """
                SELECT
                    material,
                    flask_id
                FROM core_moldes_por_fundir
                WHERE material IS NOT NULL AND flask_id IS NOT NULL
                """
            ).fetchall()
        
        # Load current daily resources (we'll update as we schedule)
        with self.db.connect() as con:
            resources_rows = con.execute(
                """
                SELECT day, flask_type, available_qty, pouring_tons_available
                FROM planner_daily_resources
                WHERE scenario_id = ? AND day >= ?
                ORDER BY day ASC
                """,
                (scenario_id, today.isoformat()),
            ).fetchall()
        
        # Build mutable resource state
        pouring_available: dict[str, float] = {}  # day -> tons
        flask_available: dict[tuple[str, str], int] = {}  # (day, flask_type) -> qty
        
        for row in resources_rows:
            day_str = str(row["day"])
            flask_type = str(row["flask_type"])
            pouring_available[day_str] = float(row["pouring_tons_available"])
            flask_available[(day_str, flask_type)] = int(row["available_qty"])
        
        # Get workdays (only days with resources)
        workdays_set = sorted(set(day_str for day_str, _ in flask_available.keys()))
        
        # Schedule each molde_por_fundir
        moldes_scheduled = 0
        daily_decrements_moldes: dict[tuple[str, str], int] = {}  # (day, flask_type) -> count
        pouring_decrements: dict[str, float] = {}  # day -> tons
        
        for r in moldes_rows:
            material = str(r["material"] or "").strip()
            flask_id = str(r["flask_id"] or "").strip()
            
            if not material or not flask_id:
                continue
            
            # Extract part_code from full material
            part_code = extract_part_code(material)
            if not part_code:
                continue
            
            mat_data = material_data.get(part_code)
            if not mat_data:
                continue
            
            peso_ton = mat_data["peso_unitario_ton"]
            cooling_hours = mat_data["cooling_hours"]
            
            if peso_ton <= 0:
                continue
            
            flask_type = _get_flask_type(flask_id)
            
            # Find first workday >= HOY+1 with enough pouring capacity
            tomorrow = date.fromordinal(today.toordinal() + 1)
            poured_date = None
            
            for day_str in workdays_set:
                try:
                    day_date = date.fromisoformat(day_str)
                except Exception:
                    continue
                
                if day_date < tomorrow:
                    continue
                
                # Check if enough pouring capacity available
                available_pour = pouring_available.get(day_str, 0.0) - pouring_decrements.get(day_str, 0.0)
                if available_pour >= peso_ton:
                    poured_date = day_date
                    poured_date_str = day_str
                    break
            
            if not poured_date:
                # No capacity found in horizon, skip this molde
                continue
            
            # Decrement pouring capacity
            pouring_decrements[poured_date_str] = pouring_decrements.get(poured_date_str, 0.0) + peso_ton
            
            # Calculate demolding date: poured_date + ceil(cooling_hours/24) + 1
            cooling_days = math.ceil(cooling_hours / 24.0)
            demolding_date = date.fromordinal(poured_date.toordinal() + cooling_days + 1)
            
            # Occupy 1 flask from today until demolding_date (exclusive)
            current_day = today
            while current_day < demolding_date:
                day_str = current_day.isoformat()
                key = (day_str, flask_type)
                daily_decrements_moldes[key] = daily_decrements_moldes.get(key, 0) + 1
                current_day = date.fromordinal(current_day.toordinal() + 1)
            
            moldes_scheduled += 1
        
        # Apply moldes_por_fundir flask decrements
        updates_moldes = []
        for (day_str, flask_type), count in daily_decrements_moldes.items():
            updates_moldes.append((count, day_str, flask_type, scenario_id))
        
        with self.db.connect() as con:
            con.executemany(
                """
                UPDATE planner_daily_resources
                SET available_qty = MAX(0, available_qty - ?)
                WHERE day = ? AND flask_type = ? AND scenario_id = ?
                """,
                updates_moldes,
            )
            con.commit()
        
        # Apply pouring capacity decrements
        updates_pouring = []
        for day_str, tons in pouring_decrements.items():
            updates_pouring.append((tons, day_str, scenario_id))
        
        with self.db.connect() as con:
            con.executemany(
                """
                UPDATE planner_daily_resources
                SET pouring_tons_available = MAX(0.0, pouring_tons_available - ?)
                WHERE day = ? AND scenario_id = ?
                """,
                updates_pouring,
            )
            con.commit()
        
        logger.info(f"Phase 3: Scheduled {moldes_scheduled} moldes_por_fundir, decremented {len(updates_moldes)} flask records, {len(updates_pouring)} pouring records")

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
                    scenario_id, part_id, flask_size, cool_hours, finish_days, min_finish_days,
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
                SELECT part_id, flask_size, cool_hours, finish_days, min_finish_days,
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
                "finish_days": int(r[3] or 0),
                "min_finish_days": int(r[4] or 0),
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
                       molding_max_per_shift, molding_shifts_json, pour_max_ton_per_shift, pour_shifts_json,
                       heats_per_shift, tons_per_heat, max_placement_search_days, allow_molding_gaps
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
            "heats_per_shift": float(row["heats_per_shift"] or 0.0),
            "tons_per_heat": float(row["tons_per_heat"] or 0.0),
            "max_placement_search_days": int(row["max_placement_search_days"] or 365),
            "allow_molding_gaps": bool(row["allow_molding_gaps"] or 0),
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
        heats_per_shift: float | None = None,
        tons_per_heat: float | None = None,
        max_placement_search_days: int | None = None,
        allow_molding_gaps: bool | None = None,
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
                    heats_per_shift,
                    tons_per_heat,
                    max_placement_search_days,
                    allow_molding_gaps,
                    notes
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scenario_id) DO UPDATE SET
                    molding_max_per_day=COALESCE(excluded.molding_max_per_day, molding_max_per_day),
                    molding_max_same_part_per_day=COALESCE(excluded.molding_max_same_part_per_day, molding_max_same_part_per_day),
                    pour_max_ton_per_day=COALESCE(excluded.pour_max_ton_per_day, pour_max_ton_per_day),
                    molding_max_per_shift=COALESCE(excluded.molding_max_per_shift, molding_max_per_shift),
                    molding_shifts_json=COALESCE(excluded.molding_shifts_json, molding_shifts_json),
                    pour_max_ton_per_shift=COALESCE(excluded.pour_max_ton_per_shift, pour_max_ton_per_shift),
                    pour_shifts_json=COALESCE(excluded.pour_shifts_json, pour_shifts_json),
                    heats_per_shift=COALESCE(excluded.heats_per_shift, heats_per_shift),
                    tons_per_heat=COALESCE(excluded.tons_per_heat, tons_per_heat),
                    max_placement_search_days=COALESCE(excluded.max_placement_search_days, max_placement_search_days),
                    allow_molding_gaps=COALESCE(excluded.allow_molding_gaps, allow_molding_gaps),
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
                    float(heats_per_shift) if heats_per_shift is not None else None,
                    float(tons_per_heat) if tons_per_heat is not None else None,
                    int(max_placement_search_days) if max_placement_search_days is not None else None,
                    1 if allow_molding_gaps else 0 if allow_molding_gaps is not None else None,
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
        """Update core_material_master.flask_size based on observed usage in Demolding + Configured Codes."""
        if not flask_codes_map:
            return
            
        sorted_codes = sorted(flask_codes_map.items(), key=lambda x: len(x[0]), reverse=True)
        
        with self.db.connect() as con:
            # Get data from both core_moldes_por_fundir and core_piezas_fundidas
            rows = con.execute(
                """
                SELECT material, flask_id FROM core_moldes_por_fundir WHERE flask_id IS NOT NULL AND flask_id <> ''
                UNION
                SELECT material, flask_id FROM core_piezas_fundidas WHERE flask_id IS NOT NULL AND flask_id <> ''
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
            # Extract part_codes from materials
            updates_with_part_code: list[tuple[str, str]] = []
            for mat, size in updates.items():
                part_code = extract_part_code(mat)
                if part_code:
                    updates_with_part_code.append((size, part_code))
            
            if updates_with_part_code:
                with self.db.connect() as con:
                    con.executemany(
                        "UPDATE core_material_master SET flask_size = COALESCE(flask_size, ?) WHERE part_code = ?",
                        updates_with_part_code
                    )

    # ---------- Initial conditions helpers ----------
    def get_planner_initial_order_progress(self, *, asof_date: date) -> list[dict]:
        """Compute remaining molds per order from Visión + master data.

        remaining_molds = ceil(x_fundir / piezas_por_molde); if piezas_por_molde <= 0 use 1.
        """
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT
                    v.pedido,
                    v.posicion,
                    COALESCE(v.solicitado, 0) AS qty_fundir,
                    COALESCE(mm.piezas_por_molde, 0) AS ppm
                FROM core_sap_vision_snapshot v
                LEFT JOIN core_material_master mm ON mm.part_code = (CASE
                    WHEN substr(v.cod_material, 1, 2) = '40' AND substr(v.cod_material, 5, 2) = '00' THEN substr(v.cod_material, 7, 5)
                    WHEN substr(v.cod_material, 1, 4) = '4310' AND substr(v.cod_material, 10, 2) = '01' THEN substr(v.cod_material, 5, 5)
                    WHEN substr(v.cod_material, 1, 3) = '435' AND substr(v.cod_material, 6, 1) = '0' THEN substr(v.cod_material, 7, 5)
                    WHEN substr(v.cod_material, 1, 3) = '436' AND substr(v.cod_material, 6, 1) = '0' THEN substr(v.cod_material, 7, 5)
                END)
                WHERE v.pedido IS NOT NULL AND TRIM(v.pedido) <> ''
                  AND v.posicion IS NOT NULL AND TRIM(v.posicion) <> ''
                GROUP BY v.pedido, v.posicion
                """,
            ).fetchall()

        out: list[dict] = []
        asof_iso = asof_date.isoformat()
        for r in rows:
            pedido = str(r["pedido"]).strip()
            posicion = str(r["posicion"]).strip()
            if not pedido or not posicion:
                continue
            qty = float(r["qty_fundir"] or 0.0)
            ppm = float(r["ppm"] or 0.0)
            if ppm <= 0:
                ppm = 1.0
            remaining_molds = int(math.ceil(qty / ppm)) if qty > 0 else 0
            out.append(
                {
                    "asof_date": asof_iso,
                    "order_id": f"{pedido}/{posicion}",
                    "remaining_molds": remaining_molds,
                }
            )
        return out

    def replace_planner_initial_order_progress(self, *, scenario_id: int, rows: list[tuple]) -> None:
        with self.db.connect() as con:
            con.execute("DELETE FROM planner_initial_order_progress WHERE scenario_id = ?", (scenario_id,))
            con.executemany(
                """
                INSERT INTO planner_initial_order_progress (scenario_id, asof_date, order_id, remaining_molds)
                VALUES (?, ?, ?, ?)
                """,
                rows,
            )

    def get_planner_initial_order_progress_rows(self, *, scenario_id: int, asof_date) -> list[dict]:
        asof_iso = asof_date.isoformat() if hasattr(asof_date, "isoformat") else str(asof_date)
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT asof_date, order_id, remaining_molds
                FROM planner_initial_order_progress
                WHERE scenario_id = ?
                ORDER BY order_id
                """,
                (scenario_id,),
            ).fetchall()

        if not rows:
            # Fallback: compute on the fly (non-persistent) so planner still runs
            return self.get_planner_initial_order_progress(asof_date=asof_date)

        return [
            {
                "asof_date": str(r["asof_date"] or asof_iso),
                "order_id": str(r["order_id"]),
                "remaining_molds": int(r["remaining_molds"] or 0),
            }
            for r in rows
        ]

    def get_planner_initial_patterns_loaded(self, *, scenario_id: int, asof_date) -> list[dict]:
        """Return currently loaded patterns (patterns already on line).
        
        Currently returns empty - future enhancement could read from line state table.
        Used by heuristic to prioritize continuation of in-progress orders.
        """
        # TODO: Implement line state tracking if needed
        # For now, return empty list (all orders start fresh)
        return []

    def replace_planner_initial_patterns_loaded(self, *, scenario_id: int, rows: list[tuple]) -> None:
        """Persist loaded patterns state (stub for future line state tracking)."""
        # Currently no-op since we don't track line state yet
        pass

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
                FROM core_sap_vision_snapshot v
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
            # Alineado con dispatcher: 1=test, 2=urgente, 3=normal
            if kind == "test":
                priority = 1
            elif kind:
                priority = 2
            else:
                priority = 3
            orders_out.append((scenario_id, order_id, material, qty, due, priority))
            try:
                d = date.fromisoformat(due)
                if max_due is None or d > max_due:
                    max_due = d
            except Exception:
                pass

        if not orders_out:
            raise ValueError("No hay órdenes válidas en Visión para planificar")

        # Parts from core_material_master for referenced materials
        materials = sorted({o[2] for o in orders_out})
        
        # Extract part_codes from materials
        material_to_part_code = {}
        part_codes = set()
        for mat in materials:
            pc = extract_part_code(mat)
            if pc:
                material_to_part_code[mat] = pc
                part_codes.add(pc)
        
        with self.db.connect() as con:
            rows = con.execute(
                f"""
                SELECT
                    part_code,
                    flask_size,
                    tiempo_enfriamiento_molde_horas,
                    peso_unitario_ton,
                    aleacion,
                    piezas_por_molde,
                    finish_hours,
                    min_finish_hours
                FROM core_material_master
                WHERE part_code IN ({','.join(['?'] * len(part_codes))})
                """,
                sorted(part_codes),
            ).fetchall()

        # Build map from material (11-digit) to row data via part_code
        part_code_map = {str(r[0]): r for r in rows}
        part_map = {}
        for mat in materials:
            pc = material_to_part_code.get(mat)
            if pc and pc in part_code_map:
                part_map[mat] = part_code_map[pc]
        
        missing_parts: list[str] = []
        parts_out: list[tuple] = []
        max_lag_days = 0
        for mat in materials:
            r = part_map.get(mat)
            if not r:
                missing_parts.append(mat)
                continue
            flask_size = str(r[1] or "").strip().upper()
            cool_hours = float(r[2] or 0.0)  # Stored as hours directly
            weight = float(r[3] or 0.0)
            alloy = str(r[4] or "").strip() or None
            pieces_per_mold = float(r[5] or 0.0)
            finish_days = int(r[6] or 0)  # Stored as days (NO conversion)
            min_finish_days = int(r[7] or 0)  # Stored as days (NO conversion)
            
            # NO aplicar defaults - si falta dato, se marcará en planner como inválido
            # Validación mínima solo para evitar crashes
            if min_finish_days > finish_days and finish_days > 0:
                min_finish_days = finish_days

            parts_out.append(
                (
                    scenario_id,
                    mat,
                    flask_size,
                    cool_hours,
                    finish_days,  # Almacenado como días
                    min_finish_days,  # Almacenado como días
                    pieces_per_mold,
                    weight,
                    alloy,
                )
            )
            # Calcular lag máximo usando días directamente
            lag_days = 1 + int(math.ceil(cool_hours / 24.0)) + 1 + finish_days + 1
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

        # Persist all (flask/pour state now managed by planner_daily_resources)
        self.replace_planner_parts(scenario_id=scenario_id, rows=parts_out)
        self.replace_planner_orders(scenario_id=scenario_id, rows=orders_out)
        self.replace_planner_calendar(scenario_id=scenario_id, rows=workdays)
        self.replace_planner_initial_order_progress(scenario_id=scenario_id, rows=progress_out)

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
    
    def get_flask_usage_breakdown(self, *, scenario_id: int) -> dict:
        """Get breakdown of flask usage by source (WIP molds vs completed pieces).
        
        Returns dict with structure:
        {
            'L10': {'wip_molds': 5, 'completed': 3, 'total_occupied': 8, 'wip_tons': 2.5},
            'L14': {'wip_molds': 2, 'completed': 1, 'total_occupied': 3, 'wip_tons': 1.2},
            ...
        }
        - wip_molds: cajas ocupadas por moldes en proceso (core_moldes_por_fundir)
        - completed: fracción de cajas ocupadas por piezas fundidas pendientes de desmoldeo
        - total_occupied: suma de wip_molds + completed
        - wip_tons: toneladas a fundir asociadas a moldes WIP (usa peso_unitario_ton)
        """
        today = date.today()

        # Build prefix map from planner_flask_types (same logic as daily resources update)
        with self.db.connect() as con:
            flask_config = con.execute(
                """
                SELECT flask_type, codes_csv
                FROM planner_flask_types
                WHERE scenario_id = ?
                """,
                (scenario_id,),
            ).fetchall()

            # Count flasks from WIP molds (core_moldes_por_fundir)
            wip_rows = con.execute(
                """
                SELECT flask_id, COUNT(*) as qty
                FROM core_moldes_por_fundir
                WHERE flask_id IS NOT NULL AND TRIM(flask_id) <> ''
                GROUP BY flask_id
                """
            ).fetchall()

            # Toneladas por fundir (WIP) usando peso_unitario_ton del maestro de materiales (por molde)
            wip_tons_rows = con.execute(
                """
                SELECT w.flask_id, SUM(COALESCE(mm.peso_unitario_ton, 0.0)) AS tons
                FROM core_moldes_por_fundir w
                LEFT JOIN core_material_master mm ON mm.part_code = (CASE
                    WHEN substr(w.material, 1, 2) = '40' AND substr(w.material, 5, 2) = '00' THEN substr(w.material, 7, 5)
                    WHEN substr(w.material, 1, 4) = '4310' AND substr(w.material, 10, 2) = '01' THEN substr(w.material, 5, 5)
                    WHEN substr(w.material, 1, 3) = '435' AND substr(w.material, 6, 1) = '0' THEN substr(w.material, 7, 5)
                    WHEN substr(w.material, 1, 3) = '436' AND substr(w.material, 6, 1) = '0' THEN substr(w.material, 7, 5)
                END)
                WHERE w.flask_id IS NOT NULL AND TRIM(w.flask_id) <> ''
                GROUP BY w.flask_id
                """
            ).fetchall()

            # Count flasks from completed pieces (core_piezas_fundidas)
            completed_rows = con.execute(
                """
                SELECT flask_id, demolding_date, COALESCE(mold_quantity, 1.0) AS qty
                FROM core_piezas_fundidas
                WHERE flask_id IS NOT NULL AND TRIM(flask_id) <> ''
                """
            ).fetchall()

        # Build prefix mapping
        flask_codes_map: dict[str, str] = {}
        for row in flask_config:
            ftype = str(row["flask_type"] or "").strip().upper()
            codes_csv = str(row["codes_csv"] or "").strip()
            if codes_csv:
                for code in codes_csv.split(","):
                    code = code.strip()
                    if code:
                        flask_codes_map[code] = ftype

        sorted_codes = sorted(flask_codes_map.items(), key=lambda x: len(x[0]), reverse=True)

        # Helper to extract flask type from flask_id using configured prefixes; fallback to regex
        def _get_flask_type(flask_id: str) -> str:
            flask_code = flask_id[:3] if flask_id else ""
            for prefix, ftype in sorted_codes:
                if flask_code.startswith(prefix):
                    return ftype
            match = re.search(r"L(\d+)", str(flask_id), re.IGNORECASE)
            return f"L{match.group(1)}" if match else flask_code.upper() or "Unknown"
        
        def _ensure_bucket(breakdown: dict, flask_type: str) -> None:
            if flask_type not in breakdown:
                breakdown[flask_type] = {
                    'wip_molds': 0,
                    'completed': 0.0,
                    'total_occupied': 0.0,
                    'wip_tons': 0.0,
                }
        
        # Aggregate by flask type
        breakdown: dict[str, dict] = {}
        
        for row in wip_rows:
            flask_type = _get_flask_type(str(row["flask_id"]))
            qty = int(row["qty"])
            _ensure_bucket(breakdown, flask_type)
            breakdown[flask_type]['wip_molds'] += qty
            breakdown[flask_type]['total_occupied'] += qty

        for row in wip_tons_rows:
            flask_type = _get_flask_type(str(row["flask_id"]))
            tons = float(row["tons"] or 0.0)
            _ensure_bucket(breakdown, flask_type)
            breakdown[flask_type]['wip_tons'] += tons
        
        for row in completed_rows:
            flask_type = _get_flask_type(str(row["flask_id"]))
            qty = float(row["qty"])
            demolding_raw = str(row["demolding_date"] or "").strip()

            # Release date logic:
            # - If demolding_date in future: occupied until demolding_date + 1 day
            # - If demolding_date in past/empty: assume desmoldeo mañana => release = today + 1
            try:
                demolding_dt = date.fromisoformat(demolding_raw) if demolding_raw else None
            except Exception:
                demolding_dt = None

            base_date = demolding_dt if demolding_dt and demolding_dt > today else today
            release_date = base_date + timedelta(days=1)

            # Only count if still occupied today
            if today >= release_date:
                continue

            _ensure_bucket(breakdown, flask_type)
            breakdown[flask_type]['completed'] += qty
            breakdown[flask_type]['total_occupied'] += qty
        
        return breakdown

    def get_latest_schedule_result(self, *, scenario_id: int | None = None) -> dict | None:
        """Get the latest saved planner schedule result from database.
        
        Args:
            scenario_id: Planner scenario ID (defaults to 1)
            
        Returns:
            Dict with schedule result or None if no result found
        """
        from foundryplan.planner.persist import get_latest_schedule_result
        
        if scenario_id is None:
            scenario_id = self.ensure_planner_scenario(name="default")
        
        return get_latest_schedule_result(self.db, scenario_id=scenario_id)
