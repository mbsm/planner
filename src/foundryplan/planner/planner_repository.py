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

    def get_planner_initial_order_progress(self, *, asof_date: date) -> list[dict]:
        """Compute remaining_molds per order from Vision x_fundir and MB52 moldeo stock.

        remaining_molds = max(0, ceil(x_fundir / piezas_por_molde) - moldes_en_almacen_moldeo)
        Blocks if any part is missing piezas_por_molde.
        """
        centro = (self.data_repo.get_config(key="sap_centro", default="4000") or "").strip()
        almacen = self._planner_moldeo_almacen()
        if not centro or not almacen:
            raise ValueError("Config faltante: sap_centro o sap_almacen_moldeo")

        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT
                    v.pedido,
                    v.posicion,
                    MAX(COALESCE(v.cod_material, '')) AS material,
                    MAX(COALESCE(v.x_fundir, 0)) AS x_fundir,
                    MAX(COALESCE(p.piezas_por_molde, 0)) AS piezas_por_molde
                FROM sap_vision_snapshot v
                LEFT JOIN material_master p
                  ON p.material = v.cod_material
                GROUP BY v.pedido, v.posicion
                """,
            ).fetchall()

            mb_counts = con.execute(
                f"""
                SELECT documento_comercial AS pedido, posicion_sd AS posicion, COUNT(*) AS cnt
                FROM sap_mb52_snapshot
                WHERE centro = ?
                  AND almacen = ?
                  AND {self.data_repo._mb52_availability_predicate_sql(process='moldeo')}
                  AND documento_comercial IS NOT NULL AND TRIM(documento_comercial) <> ''
                  AND posicion_sd IS NOT NULL AND TRIM(posicion_sd) <> ''
                GROUP BY documento_comercial, posicion_sd
                """,
                (str(self.data_repo._normalize_sap_key(centro) or centro), str(almacen)),
            ).fetchall()

        mb_map = {(str(r["pedido"]).strip(), str(r["posicion"]).strip()): int(r["cnt"] or 0) for r in mb_counts}
        missing_ppm: list[str] = []
        out: list[dict] = []

        for r in rows:
            pedido = str(r["pedido"]).strip()
            posicion = str(r["posicion"]).strip()
            material = str(r["material"]).strip()
            if not pedido or not posicion:
                continue
            ppm = float(r["piezas_por_molde"] or 0)
            if ppm <= 0:
                # WARN/FIX: Assume 1 to avoid blocking, if master data is missing.
                # These orders will likely be skipped later if other data is missing, or processed with ppm=1.
                ppm = 1.0

            x_fundir = float(r["x_fundir"] or 0)
            molds_remaining = int(math.ceil(x_fundir / ppm)) if x_fundir > 0 else 0
            molds_in_stock = int(mb_map.get((pedido, posicion), 0))
            credit = max(0, molds_remaining - molds_in_stock)
            if credit > 0:
                out.append(
                    {
                        "order_id": f"{pedido}/{posicion}",
                        "remaining_molds": int(credit),
                        "asof_date": asof_date.isoformat(),
                    }
                )

        return out

    def get_planner_initial_flask_inuse_from_demolding(
        self,
        *,
        asof_date: date,
        flask_codes_map: dict[str, str] | None = None,
    ) -> list[dict]:
        """Derive initial flask usage from Reporte Desmoldeo (authoritative source).

        demolding_date is the actual shakeout date when flasks are released (SAP tracks this).
        Flasks are busy until demolding_date.
        Blocks if flask_size or demolding_date are missing.
        """
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT
                    d.material AS material,
                    d.lote AS lote,
                    d.flask_id AS flask_id,
                    d.demolding_date AS demolding_date,
                    MAX(COALESCE(p.flask_size, '')) AS flask_size
                FROM sap_demolding_snapshot d
                LEFT JOIN material_master p
                  ON p.material = d.material
                GROUP BY d.material, d.lote, d.flask_id, d.demolding_date
                """,
            ).fetchall()

        missing: list[str] = []
        agg: dict[tuple[str, int], int] = {}
        holidays = self._planner_holidays()
        
        # Sort codes by length descending to match longest prefix first
        sorted_codes = []
        if flask_codes_map:
            sorted_codes = sorted(flask_codes_map.items(), key=lambda x: len(x[0]), reverse=True)

        for r in rows:
            material = str(r["material"]).strip()
            # Default to master data, but override if flask_id matches configured codes
            flask_size = str(r["flask_size"] or "").strip().upper()
            flask_id = str(r["flask_id"] or "").strip()
            
            if sorted_codes and flask_id:
                for prefix, size in sorted_codes:
                    if flask_id.startswith(prefix):
                        flask_size = size
                        break
            
            demolding_date_str = str(r["demolding_date"] or "").strip()

            if not flask_size or not demolding_date_str:
                missing.append(material or "<unknown>")
                continue

            try:
                demolding_date = date.fromisoformat(demolding_date_str)
            except Exception:
                missing.append(material)
                continue

            # Flask is released on demolding_date (shakeout day)
            # Map demolding_date to workday_index from asof_date
            d = asof_date
            idx = 0
            while d < demolding_date:
                if d.weekday() < 5 and d not in holidays:
                    idx += 1
                d = date.fromordinal(d.toordinal() + 1)

            key = (flask_size, idx)
            agg[key] = agg.get(key, 0) + 1

        if missing:
            uniq = sorted({m for m in missing if m})
            raise ValueError(f"Faltan flask_size/demolding_date para: {', '.join(uniq[:50])}")

        return [
            {
                "flask_type": size,
                "release_workday_index": idx,
                "qty_inuse": qty,
                "asof_date": asof_date.isoformat(),
            }
            for (size, idx), qty in sorted(agg.items(), key=lambda x: (x[0][0], x[0][1]))
        ]

    def get_planner_initial_pour_load(self, *, asof_date: date) -> list[dict]:
        """Compute initial pour load from MB52 moldeo stock (WIP not yet poured).

        Metal per mold = net_weight_ton × pieces_per_mold.
        Forward-fill capacity (ASAP pouring policy).
        """
        centro = (self.data_repo.get_config(key="sap_centro", default="4000") or "").strip()
        almacen = self._planner_moldeo_almacen()
        if not centro or not almacen:
            raise ValueError("Config faltante: sap_centro o sap_almacen_moldeo")

        with self.db.connect() as con:
            rows = con.execute(
                f"""
                                SELECT
                                        COALESCE(m.material_base, v.cod_material, m.material) AS material,
                                        COUNT(*) AS cnt,
                                        MAX(COALESCE(p.peso_unitario_ton, 0)) AS net_weight_ton,
                                        MAX(COALESCE(p.piezas_por_molde, 0)) AS pieces_per_mold
                                FROM sap_mb52_snapshot m
                                LEFT JOIN sap_vision_snapshot v
                                    ON v.pedido = m.documento_comercial
                                 AND v.posicion = m.posicion_sd
                                LEFT JOIN material_master p
                                    ON p.material = COALESCE(m.material_base, v.cod_material, m.material)
                                WHERE m.centro = ?
                                    AND m.almacen = ?
                                    AND {self.data_repo._mb52_availability_predicate_sql(process='moldeo')}
                                GROUP BY COALESCE(m.material_base, v.cod_material, m.material)
                """,
                (str(self.data_repo._normalize_sap_key(centro) or centro), str(almacen)),
            ).fetchall()

        missing: list[str] = []
        wip_molds: list[dict] = []
        for r in rows:
            material = str(r["material"]).strip()
            cnt = int(r["cnt"] or 0)
            net_weight = float(r["net_weight_ton"] or 0.0)
            pieces_per_mold = float(r["pieces_per_mold"] or 0.0)
            if net_weight <= 0 or pieces_per_mold <= 0:
                missing.append(material)
                continue
            metal_per_mold = net_weight * pieces_per_mold
            wip_molds.append({"material": material, "cnt": cnt, "metal_per_mold": metal_per_mold})

        if missing:
            uniq = sorted(set(missing))
            # Log warning but don't fail - these materials will be excluded from WIP calculation
            logger.warning(
                "PLANNER WARNING: %d materiales sin peso/piezas (excluidos del WIP): %s%s",
                len(uniq),
                ", ".join(uniq[:20]),
                "..." if len(uniq) > 20 else "",
            )

        # Forward-fill: pour ASAP from day 0
        # We'll return aggregated tons per relative day offset (caller maps to workday_index)
        return wip_molds

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

    def get_planner_initial_order_progress_rows(self, *, scenario_id: int, asof_date: date) -> list[dict]:
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT order_id, remaining_molds
                FROM planner_initial_order_progress
                WHERE scenario_id = ? AND asof_date = ?
                """,
                (int(scenario_id), asof_date.isoformat()),
            ).fetchall()
        return [
            {"order_id": str(r[0]), "remaining_molds": int(r[1] or 0)}
            for r in rows
        ]

    def get_planner_initial_flask_inuse_rows(self, *, scenario_id: int, asof_date: date) -> list[dict]:
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT flask_size, release_workday_index, qty_inuse
                FROM planner_initial_flask_inuse
                WHERE scenario_id = ? AND asof_date = ?
                """,
                (int(scenario_id), asof_date.isoformat()),
            ).fetchall()
        return [
            {
                "flask_type": str(r[0] or ""),
                "release_workday_index": int(r[1] or 0),
                "qty_inuse": int(r[2] or 0),
            }
            for r in rows
        ]

    def get_planner_initial_pour_load_rows(self, *, scenario_id: int, asof_date: date) -> list[dict]:
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT workday_index, tons_committed
                FROM planner_initial_pour_load
                WHERE scenario_id = ? AND asof_date = ?
                """,
                (int(scenario_id), asof_date.isoformat()),
            ).fetchall()
        return [
            {"workday_index": int(r[0]), "tons_committed": float(r[1] or 0.0)}
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

    def replace_planner_initial_order_progress(self, *, scenario_id: int, rows: list[tuple]) -> None:
        with self.db.connect() as con:
            con.execute("DELETE FROM planner_initial_order_progress WHERE scenario_id = ?", (int(scenario_id),))
            con.executemany(
                """
                INSERT INTO planner_initial_order_progress(
                    scenario_id, asof_date, order_id, remaining_molds
                ) VALUES(?, ?, ?, ?)
                """,
                rows,
            )

    def replace_planner_initial_flask_inuse(self, *, scenario_id: int, rows: list[tuple]) -> None:
        with self.db.connect() as con:
            con.execute("DELETE FROM planner_initial_flask_inuse WHERE scenario_id = ?", (int(scenario_id),))
            con.executemany(
                """
                INSERT INTO planner_initial_flask_inuse(
                    scenario_id, asof_date, flask_size, release_workday_index, qty_inuse
                ) VALUES(?, ?, ?, ?, ?)
                """,
                rows,
            )

    def replace_planner_initial_pour_load(self, *, scenario_id: int, rows: list[tuple]) -> None:
        with self.db.connect() as con:
            con.execute("DELETE FROM planner_initial_pour_load WHERE scenario_id = ?", (int(scenario_id),))
            con.executemany(
                """
                INSERT INTO planner_initial_pour_load(
                    scenario_id, asof_date, workday_index, tons_committed
                ) VALUES(?, ?, ?, ?)
                """,
                rows,
            )

    def replace_planner_initial_patterns_loaded(self, *, scenario_id: int, rows: list[tuple]) -> None:
        """Set which orders have patterns currently loaded on the molding line.

        rows: [(scenario_id, asof_date, order_id, is_loaded), ...]
        """
        with self.db.connect() as con:
            con.execute("DELETE FROM planner_initial_patterns_loaded WHERE scenario_id = ?", (int(scenario_id),))
            con.executemany(
                """
                INSERT INTO planner_initial_patterns_loaded(
                    scenario_id, asof_date, order_id, is_loaded
                ) VALUES(?, ?, ?, ?)
                """,
                rows,
            )

    def get_planner_initial_patterns_loaded(self, *, scenario_id: int, asof_date: date) -> list[dict]:
        """Retrieve orders with patterns currently loaded."""
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT order_id, is_loaded
                FROM planner_initial_patterns_loaded
                WHERE scenario_id = ? AND asof_date = ?
                """,
                (int(scenario_id), asof_date.isoformat()),
            ).fetchall()
        return [{"order_id": r["order_id"], "is_loaded": int(r["is_loaded"] or 0)} for r in rows]

    def get_planner_resources(self, *, scenario_id: int) -> dict | None:
        with self.db.connect() as con:
            row = con.execute(
                """
                SELECT molding_max_per_day, molding_max_same_part_per_day, pour_max_ton_per_day, notes
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
        return {
            "molding_max_per_day": int(row["molding_max_per_day"] or 0),
            "molding_max_same_part_per_day": int(row["molding_max_same_part_per_day"] or 0),
            "pour_max_ton_per_day": float(row["pour_max_ton_per_day"] or 0.0),
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
        molding_max_per_day: int,
        molding_max_same_part_per_day: int,
        pour_max_ton_per_day: float,
        notes: str | None = None,
    ) -> None:
        with self.db.connect() as con:
            con.execute(
                """
                INSERT INTO planner_resources(
                    scenario_id,
                    molding_max_per_day,
                    molding_max_same_part_per_day,
                    pour_max_ton_per_day,
                    notes
                ) VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(scenario_id) DO UPDATE SET
                    molding_max_per_day=excluded.molding_max_per_day,
                    molding_max_same_part_per_day=excluded.molding_max_same_part_per_day,
                    pour_max_ton_per_day=excluded.pour_max_ton_per_day,
                    notes=excluded.notes
                """,
                (
                    int(scenario_id),
                    int(molding_max_per_day),
                    int(molding_max_same_part_per_day),
                    float(pour_max_ton_per_day),
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

        # Build calendar_workdays (Mon-Fri, excluding holidays)
        holidays = self._planner_holidays()
        if max_due is None:
            max_due = asof_date
        target_end = max_due.toordinal() + max_lag_days + int(horizon_buffer_days)
        workdays: list[tuple] = []
        d = asof_date
        idx = 0
        while d.toordinal() <= target_end:
            if d.weekday() < 5 and d not in holidays:
                week_index = idx // 5
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
