from __future__ import annotations

import time
import json
import re
from datetime import date, datetime
from uuid import uuid4

from foundryplan.core.models import AuditEntry, Job, Line, Order, Part
from foundryplan.data.db import Db
from foundryplan.data.excel_io import coerce_date, coerce_float, normalize_columns, parse_int_strict, read_excel_bytes, to_int01


class Repository:
    def __init__(self, db: Db):
        self.db = db

        # Process keys used across config, derived orders, and cached programs.
        self.processes: dict[str, dict[str, str]] = {
            # Toma de dureza: pieces in Terminaciones warehouse but NOT available
            # (i.e., not Libre utilización and/or in Control de calidad).
            "toma_de_dureza": {"almacen_key": "sap_almacen_toma_dureza", "label": "Toma de dureza"},
            "terminaciones": {"almacen_key": "sap_almacen_terminaciones", "label": "Terminaciones"},
            "mecanizado": {"almacen_key": "sap_almacen_mecanizado", "label": "Mecanizado"},
            "mecanizado_externo": {"almacen_key": "sap_almacen_mecanizado_externo", "label": "Mecanizado externo"},
            "inspeccion_externa": {"almacen_key": "sap_almacen_inspeccion_externa", "label": "Inspección externa"},
            "por_vulcanizar": {"almacen_key": "sap_almacen_por_vulcanizar", "label": "Por vulcanizar"},
            "en_vulcanizado": {"almacen_key": "sap_almacen_en_vulcanizado", "label": "En vulcanizado"},
        }

    def log_audit(self, category: str, message: str, details: str | None = None) -> None:
        """Record a business event in the audit log."""
        try:
            with self.db.connect() as con:
                con.execute(
                    "INSERT INTO audit_log (category, message, details) VALUES (?, ?, ?)",
                    (category, message, details),
                )
        except Exception as e:
            # Fallback for audit failures (don't crash the app, but log to stderr)
            print(f"FAILED TO WRITE AUDIT LOG: {e}")

    def get_recent_audit_entries(self, limit: int = 100) -> list[AuditEntry]:
        with self.db.connect() as con:
            rows = con.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [
                AuditEntry(
                    id=row["id"],
                    timestamp=row["timestamp"],
                    category=row["category"],
                    message=row["message"],
                    details=row["details"],
                )
                for row in rows
            ]

    def _mb52_availability_predicate_sql(self, *, process: str) -> str:
        """Process-specific MB52 availability predicate.

        Default processes use only available stock:
          libre_utilizacion=1 AND en_control_calidad=0

        Toma de dureza uses the opposite (not available):
          libre_utilizacion=0 OR en_control_calidad=1
        """
        if process == "toma_de_dureza":
            return "(COALESCE(libre_utilizacion, 0) = 0 OR COALESCE(en_control_calidad, 0) = 1)"
        return "(COALESCE(libre_utilizacion, 0) = 1 AND COALESCE(en_control_calidad, 0) = 0)"

    def _normalize_process(self, process: str | None) -> str:
        p = str(process or "terminaciones").strip().lower()
        if p not in self.processes:
            raise ValueError(f"process no soportado: {process!r}")
        return p

    def _almacen_for_process(self, process: str | None) -> str:
        p = self._normalize_process(process)
        key = self.processes[p]["almacen_key"]
        raw = str(self.get_config(key=key, default="") or "").strip()
        return self._normalize_sap_key(raw) or raw

    @staticmethod
    def _normalize_sap_key(value) -> str | None:
        """Normalize SAP numeric identifiers loaded through Excel.

        Excel often turns values like 000010 into 10.0; we normalize both sides
        to a canonical string without decimals and without leading zeros.
        """
        if value is None:
            return None
        s = str(value).strip()
        if not s or s.lower() == "nan":
            return None
        try:
            n = parse_int_strict(value, field="sap_key")
            return str(int(n))
        except Exception:
            return s

    @staticmethod
    def _lote_to_int(value) -> int:
        """Coerce MB52 lote into an integer correlativo.

        Some SAP exports include alphanumeric lotes (e.g. '0030PD0674').
        For Terminaciones test lotes, the correlativo is the numeric prefix
        (digits before letters). We keep the scheduling logic numeric by
        extracting the first digit group.
        """
        try:
            return int(parse_int_strict(value, field="Lote"))
        except Exception:
            s = "" if value is None else str(value)
            m = re.search(r"\d+", s)
            if not m:
                raise
            return int(m.group(0))

    @staticmethod
    def _is_lote_test(lote: str) -> bool:
        """Determine if a lote is a production test (alphanumeric).
        
        Business rule: alphanumeric lotes are production tests and must be prioritized.
        """
        if not lote:
            return False
        return bool(re.search(r"[A-Za-z]", str(lote)))

    @staticmethod
    def _lote_to_int_last4(value) -> int:
        """Legacy helper (kept for compatibility).

        Historically this extracted the last 4 digits, but for Terminaciones
        test lotes we need the numeric prefix (digits before letters). We now
        delegate to :meth:`_lote_to_int`.
        """
        return Repository._lote_to_int(value)

    def get_sap_rebuild_diagnostics(self, *, process: str = "terminaciones") -> dict:
        """Counters to debug why ranges might be 0."""
        process = self._normalize_process(process)
        centro = (self.get_config(key="sap_centro", default="4000") or "").strip()
        almacen = self._almacen_for_process(process)
        avail_sql = self._mb52_availability_predicate_sql(process=process)
        if not centro or not almacen:
            return {
                "process": process,
                "centro": centro,
                "almacen": almacen,
                "usable_total": 0,
                "usable_with_keys": 0,
                "usable_with_keys_and_vision": 0,
                "distinct_orderpos": 0,
                "distinct_orderpos_missing_vision": 0,
            }

        with self.db.connect() as con:
            usable_total = int(
                con.execute(
                                        f"""
                                        SELECT COUNT(*)
                                        FROM sap_mb52_snapshot
                                        WHERE centro = ?
                                            AND almacen = ?
                                            AND {avail_sql}
                                        """.strip(),
                    (centro, almacen),
                ).fetchone()[0]
            )

            usable_with_keys = int(
                con.execute(
                                        f"""
                                        SELECT COUNT(*)
                                        FROM sap_mb52_snapshot
                                        WHERE centro = ?
                                            AND almacen = ?
                                            AND {avail_sql}
                                            AND documento_comercial IS NOT NULL AND TRIM(documento_comercial) <> ''
                                            AND posicion_sd IS NOT NULL AND TRIM(posicion_sd) <> ''
                                            AND lote IS NOT NULL AND TRIM(lote) <> ''
                                        """.strip(),
                    (centro, almacen),
                ).fetchone()[0]
            )

            usable_with_keys_and_vision = int(
                con.execute(
                                        f"""
                                        SELECT COUNT(*)
                                        FROM sap_mb52_snapshot m
                                        JOIN sap_vision v
                                            ON v.pedido = m.documento_comercial
                                         AND v.posicion = m.posicion_sd
                                        WHERE m.centro = ?
                                            AND m.almacen = ?
                                            AND {avail_sql.replace('libre_utilizacion', 'm.libre_utilizacion').replace('en_control_calidad', 'm.en_control_calidad')}
                                            AND m.documento_comercial IS NOT NULL AND TRIM(m.documento_comercial) <> ''
                                            AND m.posicion_sd IS NOT NULL AND TRIM(m.posicion_sd) <> ''
                                            AND m.lote IS NOT NULL AND TRIM(m.lote) <> ''
                                        """.strip(),
                    (centro, almacen),
                ).fetchone()[0]
            )

            distinct_orderpos = int(
                con.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM (
                        SELECT DISTINCT documento_comercial, posicion_sd
                        FROM sap_mb52_snapshot
                        WHERE centro = ?
                          AND almacen = ?
                          AND {avail_sql}
                          AND documento_comercial IS NOT NULL AND TRIM(documento_comercial) <> ''
                          AND posicion_sd IS NOT NULL AND TRIM(posicion_sd) <> ''
                          AND lote IS NOT NULL AND TRIM(lote) <> ''
                    )
                    """.strip(),
                    (centro, almacen),
                ).fetchone()[0]
            )

            distinct_orderpos_missing_vision = int(
                con.execute(
                                        f"""
                                        SELECT COUNT(*)
                                        FROM (
                                                SELECT DISTINCT m.documento_comercial AS pedido, m.posicion_sd AS posicion
                                                FROM sap_mb52_snapshot m
                                                LEFT JOIN sap_vision v
                                                    ON v.pedido = m.documento_comercial
                                                 AND v.posicion = m.posicion_sd
                                                WHERE m.centro = ?
                                                    AND m.almacen = ?
                                                    AND {avail_sql.replace('libre_utilizacion', 'm.libre_utilizacion').replace('en_control_calidad', 'm.en_control_calidad')}
                                                    AND m.documento_comercial IS NOT NULL AND TRIM(m.documento_comercial) <> ''
                                                    AND m.posicion_sd IS NOT NULL AND TRIM(m.posicion_sd) <> ''
                                                    AND m.lote IS NOT NULL AND TRIM(m.lote) <> ''
                                                    AND v.pedido IS NULL
                                        )
                                        """.strip(),
                    (centro, almacen),
                ).fetchone()[0]
            )

        return {
            "process": process,
            "centro": centro,
            "almacen": almacen,
            "usable_total": usable_total,
            "usable_with_keys": usable_with_keys,
            "usable_with_keys_and_vision": usable_with_keys_and_vision,
            "distinct_orderpos": distinct_orderpos,
            "distinct_orderpos_missing_vision": distinct_orderpos_missing_vision,
        }

    def get_sap_non_usable_with_orderpos_rows(self, *, limit: int = 200) -> list[dict]:
        """MB52 rows that have pedido/posición but are not usable for building orders.

        A row is considered usable when it matches the configured centro/almacén,
        is libre_utilizacion=1, en_control_calidad=0, and has lote.
        """
        centro_cfg = (self.get_config(key="sap_centro", default="4000") or "").strip()
        almacen_cfg = (self.get_config(key="sap_almacen_terminaciones", default="4035") or "").strip()
        if not centro_cfg or not almacen_cfg:
            return []

        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT material, texto_breve, centro, almacen, lote,
                       COALESCE(libre_utilizacion, 0) AS libre,
                       COALESCE(en_control_calidad, 0) AS qc,
                       documento_comercial, posicion_sd
                FROM sap_mb52_snapshot
                WHERE documento_comercial IS NOT NULL AND TRIM(documento_comercial) <> ''
                  AND posicion_sd IS NOT NULL AND TRIM(posicion_sd) <> ''
                        AND centro = ?
                        AND almacen = ?
                        AND (
                                COALESCE(libre_utilizacion, 0) <> 1
                            OR COALESCE(en_control_calidad, 0) <> 0
                        )
                ORDER BY documento_comercial, posicion_sd, material
                LIMIT ?
                """,
                (centro_cfg, almacen_cfg, int(limit)),
            ).fetchall()

        out: list[dict] = []
        for r in rows:
            material = str(r[0])
            texto_breve = str(r[1]) if r[1] is not None else ""
            centro = str(r[2]) if r[2] is not None else ""
            almacen = str(r[3]) if r[3] is not None else ""
            lote = str(r[4]) if r[4] is not None else ""
            libre = int(r[5] or 0)
            qc = int(r[6] or 0)
            pedido = str(r[7]) if r[7] is not None else ""
            posicion = str(r[8]) if r[8] is not None else ""

            reasons: list[str] = []
            if libre != 1:
                reasons.append("No libre utilización")
            if qc != 0:
                reasons.append("En control de calidad")

            out.append(
                {
                    "pedido": pedido,
                    "posicion": posicion,
                    "material": material,
                    "texto_breve": texto_breve,
                    "centro": centro,
                    "almacen": almacen,
                    "lote": lote,
                    "libre": libre,
                    "qc": qc,
                    "motivo": "; ".join(reasons) if reasons else "No usable",
                }
            )

        return out

    def get_sap_orderpos_missing_vision_rows(self, *, limit: int = 200) -> list[dict]:
        """Usable MB52 pieces (with pedido/pos/lote) that don't match any Vision row."""
        centro = (self.get_config(key="sap_centro", default="4000") or "").strip()
        almacen = (self.get_config(key="sap_almacen_terminaciones", default="4035") or "").strip()
        if not centro or not almacen:
            return []

        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT m.documento_comercial AS pedido,
                       m.posicion_sd AS posicion,
                       m.material,
                       COALESCE(MAX(m.texto_breve), '') AS texto_breve,
                       COUNT(*) AS piezas,
                       MIN(m.lote) AS lote_min,
                       MAX(m.lote) AS lote_max
                FROM sap_mb52_snapshot m
                LEFT JOIN sap_vision v
                  ON v.pedido = m.documento_comercial
                 AND v.posicion = m.posicion_sd
                WHERE m.centro = ?
                  AND m.almacen = ?
                  AND COALESCE(m.libre_utilizacion, 0) = 1
                  AND COALESCE(m.en_control_calidad, 0) = 0
                  AND m.documento_comercial IS NOT NULL AND TRIM(m.documento_comercial) <> ''
                  AND m.posicion_sd IS NOT NULL AND TRIM(m.posicion_sd) <> ''
                  AND m.lote IS NOT NULL AND TRIM(m.lote) <> ''
                  AND v.pedido IS NULL
                GROUP BY m.documento_comercial, m.posicion_sd, m.material
                ORDER BY piezas DESC, pedido, posicion, m.material
                LIMIT ?
                """,
                (centro, almacen, int(limit)),
            ).fetchall()

        return [
            {
                "pedido": str(r[0]),
                "posicion": str(r[1]),
                "material": str(r[2]),
                "texto_breve": str(r[3]) if r[3] is not None else "",
                "piezas": int(r[4]),
                "lote_min": str(r[5]) if r[5] is not None else "",
                "lote_max": str(r[6]) if r[6] is not None else "",
            }
            for r in rows
        ]

    # ---------- App config ----------
    def get_config(self, *, key: str, default: str | None = None) -> str | None:
        key = str(key).strip()
        if not key:
            raise ValueError("config key vacío")
        with self.db.connect() as con:
            row = con.execute("SELECT config_value FROM app_config WHERE config_key = ?", (key,)).fetchone()
        if row is None:
            return default
        return str(row[0])

    def set_config(self, *, key: str, value: str) -> None:
        key = str(key).strip()
        if not key:
            raise ValueError("config key vacío")

        with self.db.connect() as con:
            # Audit config change
            old_val_row = con.execute("SELECT config_value FROM app_config WHERE config_key = ?", (key,)).fetchone()
            old_val = old_val_row[0] if old_val_row else "(none)"
        
        self.log_audit("CONFIG", f"Updated '{key}'", f"From '{old_val}' to '{value}'")
        
        with self.db.connect() as con:
            # FASE 3.3: Handle priority map changes (recalculate job priorities)
            if key == "job_priority_map":
                try:
                    row = con.execute("SELECT config_value FROM app_config WHERE config_key = ?", (key,)).fetchone()
                    old_str = row["config_value"] if row else '{"prueba": 1, "urgente": 2, "normal": 3}'
                    
                    try:
                        old_map = json.loads(old_str)
                    except Exception:
                        old_map = {"prueba": 1, "urgente": 2, "normal": 3}
                        
                    try:
                        new_map = json.loads(str(value))
                    except Exception:
                        new_map = {}

                    case_parts = []
                    case_params = []
                    affected_olds = []
                    
                    for k in ["prueba", "urgente", "normal"]:
                        old_p = int(old_map.get(k, 0))
                        new_p = int(new_map.get(k, 0))
                        if old_p != new_p and old_p > 0 and new_p > 0:
                            case_parts.append("WHEN ? THEN ?")
                            case_params.extend([old_p, new_p])
                            affected_olds.append(old_p)
                            
                    if affected_olds:
                        # Construct single query to handle swaps correctly
                        placeholders = ','.join(['?']*len(affected_olds))
                        sql = f"UPDATE job SET priority = CASE priority {' '.join(case_parts)} ELSE priority END WHERE priority IN ({placeholders})"
                        con.execute(sql, (*case_params, *affected_olds))     
                except Exception:
                    pass

            con.execute(
                "INSERT INTO app_config(config_key, config_value) VALUES(?, ?) ON CONFLICT(config_key) DO UPDATE SET config_value=excluded.config_value",
                (key, str(value).strip()),
            )
            # Warehouse/filters affect derived orders and programs.
            con.execute("DELETE FROM orders")
            con.execute("DELETE FROM last_program")

    # ---------- Families catalog ----------
    def list_families(self) -> list[str]:
        with self.db.connect() as con:
            rows = con.execute("SELECT family_id FROM family_catalog ORDER BY family_id").fetchall()
        return [str(r[0]) for r in rows]

    def get_families_rows(self) -> list[dict]:
        """Rows for UI: family name + how many parts are assigned to it."""
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT f.family_id AS family_id, COUNT(p.material) AS parts_count
                FROM family_catalog f
                LEFT JOIN material_master p ON p.family_id = f.family_id
                GROUP BY f.family_id
                ORDER BY f.family_id
                """
            ).fetchall()
        return [{"family_id": str(r["family_id"]), "parts_count": int(r["parts_count"])} for r in rows]

    def add_family(self, *, name: str) -> None:
        name = str(name).strip()
        if not name:
            raise ValueError("nombre de family_id vacío")
        with self.db.connect() as con:
            con.execute("INSERT OR IGNORE INTO family_catalog(family_id, label) VALUES(?, ?)", (name, name))
        
        self.log_audit("MASTER_DATA", "Add Family", f"Family: {name}")

    def rename_family(self, *, old: str, new: str) -> None:
        old = str(old).strip()
        new = str(new).strip()
        if not old or not new:
            raise ValueError("family_id inválida")
        with self.db.connect() as con:
            # Ensure new exists
            con.execute("INSERT OR IGNORE INTO family_catalog(family_id, label) VALUES(?, ?)", (new, new))
            # UPDATE material_master mappings
            con.execute("UPDATE material_master SET family_id = ? WHERE family_id = ?", (new, old))

            # Update line_config allowed families JSON
            rows = con.execute("SELECT process, line_id, families_json FROM line_config").fetchall()
            for r in rows:
                families = json.loads(r["families_json"])
                updated = [new if f == old else f for f in families]
                updated = sorted(set(updated))
                con.execute(
                    "UPDATE line_config SET families_json = ? WHERE process = ? AND line_id = ?",
                    (json.dumps(updated), str(r["process"]), int(r["line_id"])),
                )

            # Remove old from catalog
            con.execute("DELETE FROM family_catalog WHERE family_id = ?", (old,))

            # Invalidate any previously generated program
            con.execute("DELETE FROM last_program")
        
        self.log_audit("MASTER_DATA", "Rename Family", f"{old} -> {new}")

    def delete_family(self, *, name: str, force: bool = False) -> None:
        name = str(name).strip()
        if not name:
            raise ValueError("family_id inválida")
        with self.db.connect() as con:
            in_use = int(con.execute("SELECT COUNT(*) FROM material_master WHERE family_id = ?", (name,)).fetchone()[0])
            if in_use and force:
                # Keep mappings: move affected parts to 'Otros'
                con.execute("INSERT OR IGNORE INTO family_catalog(family_id, label) VALUES('Otros', 'Otros')")
                con.execute("UPDATE material_master SET family_id='Otros' WHERE family_id = ?", (name,))
            elif in_use and not force:
                # Default behavior: remove mappings so affected parts become "missing" and must be reassigned.
                con.execute("DELETE FROM material_master WHERE family_id = ?", (name,))

            # Update line_config allowed families JSON (remove or replace)
            rows = con.execute("SELECT process, line_id, families_json FROM line_config").fetchall()
            for r in rows:
                families = json.loads(r["families_json"])
                if force:
                    updated = ["Otros" if f == name else f for f in families]
                else:
                    updated = [f for f in families if f != name]
                updated = sorted(set(updated))
                con.execute(
                    "UPDATE line_config SET families_json = ? WHERE process = ? AND line_id = ?",
                    (json.dumps(updated), str(r["process"]), int(r["line_id"])),
                )

            con.execute("DELETE FROM family_catalog WHERE family_id = ?", (name,))

            # Invalidate any previously generated program
            con.execute("DELETE FROM last_program")
        
        self.log_audit("MASTER_DATA", "Delete Family", f"Name: {name}, Force: {force}")

    # ---------- Lines ----------
    def upsert_line(
        self,
        *,
        process: str = "terminaciones",
        line_id: int,
        families: list[str],
        line_name: str | None = None,
    ) -> None:
        process = self._normalize_process(process)
        families_json = json.dumps(sorted(set(families)))
        name = None
        if line_name is not None:
            name = str(line_name).strip() or None
        if name is None:
            name = f"Línea {int(line_id)}"
        with self.db.connect() as con:
            con.execute(
                "INSERT INTO line_config(process, line_id, line_name, families_json) VALUES(?, ?, ?, ?) "
                "ON CONFLICT(process, line_id) DO UPDATE SET "
                "families_json=excluded.families_json, "
                "line_name=COALESCE(excluded.line_name, line_config.line_name)",
                (process, int(line_id), name, families_json),
            )

            # Invalidate cached program for this process
            con.execute("DELETE FROM last_program WHERE process = ?", (process,))
        
        self.log_audit("CONFIG", "Upsert Line", f"Proc: {process}, ID: {line_id}, Name: {name}, Fams: {len(families)}")

    def delete_line(self, *, process: str = "terminaciones", line_id: int) -> None:
        process = self._normalize_process(process)
        with self.db.connect() as con:
            con.execute("DELETE FROM line_config WHERE process = ? AND line_id = ?", (process, int(line_id)))
            con.execute("DELETE FROM last_program WHERE process = ?", (process,))
        
        self.log_audit("CONFIG", "Delete Line", f"Proc: {process}, ID: {line_id}")

    def get_lines(self, *, process: str = "terminaciones") -> list[dict]:
        process = self._normalize_process(process)
        with self.db.connect() as con:
            rows = con.execute(
                "SELECT line_id, line_name, families_json FROM line_config WHERE process = ? ORDER BY line_id",
                (process,),
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            line_id = int(r["line_id"])
            name = str(r["line_name"] or "").strip() if ("line_name" in r.keys()) else ""
            out.append(
                {
                    "line_id": line_id,
                    "line_name": name or f"Línea {line_id}",
                    "families": json.loads(r["families_json"]),
                }
            )
        return out

    def get_lines_model(self, *, process: str = "terminaciones") -> list[Line]:
        # Map legacy 'families' list to 'family_id' constraint
        return [
            Line(line_id=str(r["line_id"]), constraints={"family_id": set(r["families"])}) 
            for r in self.get_lines(process=process)
        ]

    def upsert_part(self, *, material: str, family_id: str) -> None:
        material = str(material).strip()
        family_id = str(family_id).strip()
        if not material:
            raise ValueError("material vacío")
        if not family_id:
            raise ValueError("family_id vacía")
        # Ensure family exists in catalog
        self.add_family(name=family_id)
        with self.db.connect() as con:
            con.execute(
                "INSERT INTO material_master(material, family_id) VALUES(?, ?) "
                "ON CONFLICT(material) DO UPDATE SET family_id=excluded.family_id",
                (material, family_id),
            )

            # Invalidate any previously generated program
            con.execute("DELETE FROM last_program")
        
        self.log_audit("MASTER_DATA", "Set Family", f"{material} -> {family_id}")

    def upsert_part_master(
        self,
        *,
        material: str,
        family_id: str,
        vulcanizado_dias: int | None = None,
        mecanizado_dias: int | None = None,
        inspeccion_externa_dias: int | None = None,
        peso_unitario_ton: float | None = None,
        mec_perf_inclinada: bool = False,
        sobre_medida_mecanizado: bool = False,
        aleacion: str | None = None,
        piezas_por_molde: float | None = None,
        peso_bruto_ton: float | None = None,
        tiempo_enfriamiento_molde_dias: int | None = None,
    ) -> None:
        """Upsert a part master row including family and optional process times."""
        material = str(material).strip()
        family_id = str(family_id).strip()
        if not material:
            raise ValueError("material vacío")
        if not family_id:
            raise ValueError("family_id vacía")

        def _coerce_days(value, *, field: str) -> int | None:
            if value is None:
                return None
            v = int(value)
            if v < 0:
                raise ValueError(f"{field} no puede ser negativo")
            return v

        v = _coerce_days(vulcanizado_dias, field="vulcanizado_dias")
        m = _coerce_days(mecanizado_dias, field="mecanizado_dias")
        i = _coerce_days(inspeccion_externa_dias, field="inspeccion_externa_dias")
        t_enfr = _coerce_days(tiempo_enfriamiento_molde_dias, field="tiempo_enfriamiento_molde_dias")

        pt: float | None = None
        if peso_unitario_ton is not None:
            pt = float(peso_unitario_ton)
            if pt < 0:
                raise ValueError("peso_unitario_ton no puede ser negativo")

        pb: float | None = None
        if peso_bruto_ton is not None:
            pb = float(peso_bruto_ton)
            if pb < 0:
                raise ValueError("peso_bruto_ton no puede ser negativo")
        
        ppm: float | None = None
        if piezas_por_molde is not None:
            ppm = float(piezas_por_molde)
            if ppm < 0:
                raise ValueError("piezas_por_molde no puede ser negativo")

        mec_perf = 1 if bool(mec_perf_inclinada) else 0
        sm = 1 if bool(sobre_medida_mecanizado) else 0
        aleacion_val = str(aleacion).strip() if aleacion else None

        # Ensure family exists in catalog
        self.add_family(name=family_id)

        with self.db.connect() as con:
            con.execute(
                "INSERT INTO material_master("
                "material, family_id, vulcanizado_dias, mecanizado_dias, inspeccion_externa_dias, peso_unitario_ton, "
                "mec_perf_inclinada, sobre_medida_mecanizado, aleacion, piezas_por_molde, peso_bruto_ton, tiempo_enfriamiento_molde_dias"
                ") "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(material) DO UPDATE SET "
                "family_id=excluded.family_id, "
                "vulcanizado_dias=excluded.vulcanizado_dias, "
                "mecanizado_dias=excluded.mecanizado_dias, "
                "inspeccion_externa_dias=excluded.inspeccion_externa_dias, "
                "peso_unitario_ton=excluded.peso_unitario_ton, "
                "mec_perf_inclinada=excluded.mec_perf_inclinada, "
                "sobre_medida_mecanizado=excluded.sobre_medida_mecanizado, "
                "aleacion=excluded.aleacion, "
                "piezas_por_molde=excluded.piezas_por_molde, "
                "peso_bruto_ton=excluded.peso_bruto_ton, "
                "tiempo_enfriamiento_molde_dias=excluded.tiempo_enfriamiento_molde_dias",
                (material, family_id, v, m, i, pt, mec_perf, sm, aleacion_val, ppm, pb, t_enfr),
            )

            # Invalidate any previously generated program
            con.execute("DELETE FROM last_program")
        
        self.log_audit("MASTER_DATA", "Upsert Part", f"Material: {material} Family: {family_id}")

    def update_part_process_times(
        self,
        *,
        material: str,
        vulcanizado_dias: int,
        mecanizado_dias: int,
        inspeccion_externa_dias: int,
    ) -> None:
        material = str(material).strip()
        if not material:
            raise ValueError("material vacío")
        for col_name, value in (
            ("vulcanizado_dias", vulcanizado_dias),
            ("mecanizado_dias", mecanizado_dias),
            ("inspeccion_externa_dias", inspeccion_externa_dias),
        ):
            if int(value) < 0:
                raise ValueError(f"{col_name} no puede ser negativo")

        with self.db.connect() as con:
            exists = con.execute("SELECT 1 FROM material_master WHERE material = ?", (material,)).fetchone()
            if exists is None:
                raise ValueError(
                    f"No existe maestro para material={material}. Asigna family_id primero en /family_ids."
                )
            con.execute(
                """
                UPDATE material_master
                SET vulcanizado_dias = ?, mecanizado_dias = ?, inspeccion_externa_dias = ?
                WHERE material = ?
                """,
                (int(vulcanizado_dias), int(mecanizado_dias), int(inspeccion_externa_dias), material),
            )

            # Invalidate any previously generated program
            con.execute("DELETE FROM last_program")
        
        self.log_audit(
            "MASTER_DATA",
            "Update Times",
            f"Mat {material}: V={vulcanizado_dias}, M={mecanizado_dias}, I={inspeccion_externa_dias}"
        )

    def delete_part(self, *, material: str) -> None:
        with self.db.connect() as con:
            con.execute("DELETE FROM material_master WHERE material = ?", (str(material).strip(),))
            con.execute("DELETE FROM last_program")
        
        self.log_audit("MASTER_DATA", "Delete Part", f"Material: {material}")

    def delete_all_parts(self) -> None:
        with self.db.connect() as con:
            con.execute("DELETE FROM material_master")
            con.execute("DELETE FROM last_program")
        
        self.log_audit("MASTER_DATA", "Delete All Parts", "Cleared all material master data")

    def get_parts_rows(self) -> list[dict]:
        """Return the part master as UI-friendly dict rows."""
        with self.db.connect() as con:
            rows = con.execute(
                "SELECT material, family_id, vulcanizado_dias, mecanizado_dias, inspeccion_externa_dias, peso_unitario_ton, mec_perf_inclinada, sobre_medida_mecanizado FROM material_master ORDER BY material"
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- Dashboard helpers ----------
    def upsert_vision_kpi_daily(self, *, snapshot_date: date | None = None) -> dict:
        """Persist a daily KPI snapshot based on current Orders + Visión.

        Metrics:
        - tons_por_entregar: pending tons across all (pedido,posicion) present in `orders`
        - tons_atrasadas: subset of pending tons where `fecha_entrega` < snapshot_date

        One row per day (upsert by `snapshot_date`).
        """

        d0 = snapshot_date or date.today()
        d0_iso = d0.isoformat()
        now_iso = datetime.now().isoformat(timespec="seconds")

        with self.db.connect() as con:
            row = con.execute(
                """
                WITH v AS (
                    SELECT
                        pedido,
                        posicion,
                        MAX(COALESCE(cod_material, '')) AS cod_material,
                        MAX(COALESCE(fecha_de_pedido, '')) AS fecha_de_pedido,
                        MAX(COALESCE(fecha_entrega, '')) AS fecha_entrega,
                        MAX(COALESCE(solicitado, 0)) AS solicitado,
                        MAX(COALESCE(bodega, 0)) AS bodega,
                        MAX(COALESCE(despachado, 0)) AS despachado,
                        MAX(peso_unitario_ton) AS peso_unitario_ton
                    FROM sap_vision_snapshot
                    -- We trust sap_vision_snapshot contains only valid/filtered rows (Active, date > 2023, valid families/ZTLH)
                    GROUP BY pedido, posicion
                ), joined AS (
                    SELECT
                        v.fecha_entrega AS fecha_entrega,
                        CASE
                            WHEN (v.solicitado - v.bodega - v.despachado) < 0 THEN 0
                            ELSE (v.solicitado - v.bodega - v.despachado)
                        END AS pendientes,
                        COALESCE(p.peso_unitario_ton, v.peso_unitario_ton, 0.0) AS peso_unitario_ton
                    FROM v
                    LEFT JOIN material_master p
                      ON p.material = v.cod_material
                )
                SELECT
                    COALESCE(SUM(pendientes * peso_unitario_ton), 0.0) AS tons_por_entregar,
                    COALESCE(SUM(CASE WHEN fecha_entrega < ? THEN (pendientes * peso_unitario_ton) ELSE 0.0 END), 0.0) AS tons_atrasadas
                FROM joined
                """,
                (d0_iso,),
            ).fetchone()

            tons_por_entregar = float(row["tons_por_entregar"] or 0.0) if row is not None else 0.0
            tons_atrasadas = float(row["tons_atrasadas"] or 0.0) if row is not None else 0.0

            con.execute(
                """
                INSERT INTO vision_kpi_daily(snapshot_date, snapshot_at, tons_por_entregar, tons_atrasadas)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(snapshot_date) DO UPDATE SET
                    snapshot_at = excluded.snapshot_at,
                    tons_por_entregar = excluded.tons_por_entregar,
                    tons_atrasadas = excluded.tons_atrasadas
                """,
                (d0_iso, now_iso, tons_por_entregar, tons_atrasadas),
            )

        return {
            "snapshot_date": d0_iso,
            "snapshot_at": now_iso,
            "tons_por_entregar": tons_por_entregar,
            "tons_atrasadas": tons_atrasadas,
        }

    def get_vision_kpi_daily_rows(self, *, limit: int = 120) -> list[dict]:
        lim = max(1, min(int(limit or 120), 2000))
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT snapshot_date, snapshot_at, tons_por_entregar, tons_atrasadas
                FROM vision_kpi_daily
                ORDER BY snapshot_date ASC
                LIMIT ?
                """,
                (lim,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_orders_overdue_rows(self, *, today: date | None = None, limit: int = 200) -> list[dict]:
        """Orders with fecha_entrega < today across all processes.
        NOTE: Uses fecha_de_pedido as the valid date, aliased to fecha_entrega.
        """
        d0 = today or date.today()
        lim = max(1, min(int(limit or 200), 2000))
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT
                    v.pedido AS pedido,
                    v.posicion AS posicion,
                    COALESCE(v.cod_material, '') AS material,
                    COALESCE(v.solicitado, 0) AS solicitado,
                    COALESCE(v.bodega, 0) AS bodega,
                    v.fecha_de_pedido AS fecha_entrega,
                    COALESCE(v.cliente, '') AS cliente,
                    CASE
                        WHEN (COALESCE(v.solicitado, 0) - COALESCE(v.bodega, 0) - COALESCE(v.despachado, 0)) < 0 THEN 0
                        ELSE (COALESCE(v.solicitado, 0) - COALESCE(v.bodega, 0) - COALESCE(v.despachado, 0))
                    END AS pendientes,
                    (
                        CASE
                            WHEN (COALESCE(v.solicitado, 0) - COALESCE(v.bodega, 0) - COALESCE(v.despachado, 0)) < 0 THEN 0
                            ELSE (COALESCE(v.solicitado, 0) - COALESCE(v.bodega, 0) - COALESCE(v.despachado, 0))
                        END
                        * COALESCE(p.peso_unitario_ton, v.peso_unitario_ton, 0.0)
                    ) AS tons,
                    (COALESCE(v.bodega, 0) * COALESCE(p.peso_unitario_ton, v.peso_unitario_ton, 0.0)) AS tons_dispatch
                FROM (
                    SELECT
                        pedido,
                        posicion,
                        MAX(cliente) AS cliente,
                        MAX(cod_material) AS cod_material,
                        MAX(COALESCE(fecha_de_pedido, '9999-12-31')) AS fecha_de_pedido,
                        MAX(COALESCE(solicitado, 0)) AS solicitado,
                        MAX(COALESCE(bodega, 0)) AS bodega,
                        MAX(COALESCE(despachado, 0)) AS despachado,
                        MAX(peso_unitario_ton) AS peso_unitario_ton
                    FROM sap_vision_snapshot
                    GROUP BY pedido, posicion
                    HAVING MAX(COALESCE(fecha_de_pedido, '9999-12-31')) < ?
                ) v
                LEFT JOIN material_master p
                  ON p.material = v.cod_material
                ORDER BY v.fecha_de_pedido ASC, v.pedido, v.posicion
                LIMIT ?
                """,
                (d0.isoformat(), lim),
            ).fetchall()

        out: list[dict] = []
        for r in rows:
            fe = date.fromisoformat(str(r["fecha_entrega"]))
            atraso = (d0 - fe).days
            row_id = f"{r['pedido']}|{r['posicion']}"
            out.append(
                {
                    "_row_id": row_id,
                    "pedido": str(r["pedido"]),
                    "posicion": str(r["posicion"]),
                    "material": str(r["material"]),
                    "solicitado": int(r["solicitado"] or 0),
                    "bodega": int(r["bodega"] or 0),
                    "pendientes": int(r["pendientes"] or 0),
                    "fecha_entrega": fe.isoformat(),
                    "dias": int(atraso),
                    "cliente": str(r["cliente"] or "").strip(),
                    "tons": float(r["tons"] or 0.0),
                    "tons_dispatch": float(r["tons_dispatch"] or 0.0),
                }
            )
        return out

    def get_orders_due_soon_rows(
        self,
        *,
        today: date | None = None,
        days: int = 14,
        limit: int = 200,
    ) -> list[dict]:
        """Orders with fecha_entrega between today and today+days (inclusive)."""
        d0 = today or date.today()
        horizon = d0.toordinal() + int(days)
        d1 = date.fromordinal(horizon)
        lim = max(1, min(int(limit or 200), 2000))
        with self.db.connect() as con:
            rows = con.execute(
                """
                WITH orderpos AS (
                    SELECT pedido, posicion, MIN(COALESCE(fecha_de_pedido, '9999-12-31')) AS fecha_entrega
                    FROM sap_vision_snapshot
                    GROUP BY pedido, posicion
                    HAVING MIN(COALESCE(fecha_de_pedido, '9999-12-31')) >= ?
                       AND MIN(COALESCE(fecha_de_pedido, '9999-12-31')) <= ?
                )
                SELECT
                    op.pedido AS pedido,
                    op.posicion AS posicion,
                    COALESCE(v.cod_material, '') AS material,
                    COALESCE(v.solicitado, 0) AS solicitado,
                    op.fecha_entrega AS fecha_entrega,
                    COALESCE(v.cliente, '') AS cliente,
                    CASE
                        WHEN (COALESCE(v.solicitado, 0) - COALESCE(v.bodega, 0) - COALESCE(v.despachado, 0)) < 0 THEN 0
                        ELSE (COALESCE(v.solicitado, 0) - COALESCE(v.bodega, 0) - COALESCE(v.despachado, 0))
                    END AS pendientes,
                    (
                        CASE
                            WHEN (COALESCE(v.solicitado, 0) - COALESCE(v.bodega, 0) - COALESCE(v.despachado, 0)) < 0 THEN 0
                            ELSE (COALESCE(v.solicitado, 0) - COALESCE(v.bodega, 0) - COALESCE(v.despachado, 0))
                        END
                        * COALESCE(p.peso_unitario_ton, v.peso_unitario_ton, 0.0)
                    ) AS tons
                FROM orderpos op
                LEFT JOIN (
                    SELECT
                        pedido,
                        posicion,
                        MAX(cliente) AS cliente,
                        MAX(cod_material) AS cod_material,
                        MAX(COALESCE(solicitado, 0)) AS solicitado,
                        MAX(COALESCE(bodega, 0)) AS bodega,
                        MAX(COALESCE(despachado, 0)) AS despachado,
                        MAX(peso_unitario_ton) AS peso_unitario_ton
                    FROM sap_vision_snapshot
                    GROUP BY pedido, posicion
                ) v
                  ON v.pedido = op.pedido
                 AND v.posicion = op.posicion
                LEFT JOIN material_master p
                  ON p.material = v.cod_material
                ORDER BY op.fecha_entrega ASC, op.pedido, op.posicion
                LIMIT ?
                """,
                (d0.isoformat(), d1.isoformat(), lim),
            ).fetchall()

        out: list[dict] = []
        for r in rows:
            fe = date.fromisoformat(str(r["fecha_entrega"]))
            restantes = (fe - d0).days
            row_id = f"{r['pedido']}|{r['posicion']}"
            out.append(
                {
                    "_row_id": row_id,
                    "pedido": str(r["pedido"]),
                    "posicion": str(r["posicion"]),
                    "material": str(r["material"]),
                    "solicitado": int(r["solicitado"] or 0),
                    "pendientes": int(r["pendientes"] or 0),
                    "fecha_entrega": fe.isoformat(),
                    "dias": int(restantes),
                    "cliente": str(r["cliente"] or "").strip(),
                    "tons": float(r["tons"] or 0.0),
                }
            )
        return out

    def get_process_load_rows(self) -> list[dict]:
        """Load summary per process/almacen: pieces + tons (tons from Vision: peso_neto/solicitado)."""
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT
                    o.process AS process,
                    o.almacen AS almacen,
                    COALESCE(SUM(o.cantidad), 0) AS piezas,
                    COALESCE(SUM(o.cantidad * COALESCE(v.peso_unitario_ton, 0.0)), 0.0) AS tons,
                    COALESCE(SUM(CASE WHEN v.peso_unitario_ton IS NULL THEN o.cantidad ELSE 0 END), 0) AS piezas_sin_peso,
                    COUNT(DISTINCT (o.pedido || '/' || o.posicion)) AS orderpos
                FROM orders o
                LEFT JOIN sap_vision v
                  ON v.pedido = o.pedido
                 AND v.posicion = o.posicion
                GROUP BY o.process, o.almacen
                ORDER BY o.process, o.almacen
                """
            ).fetchall()

        out: list[dict] = []
        for r in rows:
            proc = str(r["process"])
            label = (self.processes.get(proc, {}) or {}).get("label", proc)
            almacen = str(r["almacen"])
            out.append(
                {
                    "_row_id": f"{proc}|{almacen}",
                    "process": proc,
                    "proceso": label,
                    "almacen": almacen,
                    "piezas": int(r["piezas"] or 0),
                    "tons": float(r["tons"] or 0.0),
                    "piezas_sin_peso": int(r["piezas_sin_peso"] or 0),
                    "orderpos": int(r["orderpos"] or 0),
                }
            )
        return out

    def get_missing_parts_from_orders(self, *, process: str = "terminaciones") -> list[str]:
        process = self._normalize_process(process)
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT DISTINCT o.material
                FROM orders o
                LEFT JOIN material_master p ON p.material = o.material
                WHERE o.process = ?
                  AND p.material IS NULL
                ORDER BY o.material
                """,
                (process,),
            ).fetchall()
        return [str(r[0]) for r in rows]

    def get_missing_process_times_from_orders(self, *, process: str = "terminaciones") -> list[str]:
        """Distinct material referenced by orders that has a master row but missing any process time."""
        process = self._normalize_process(process)
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT DISTINCT o.material
                FROM orders o
                JOIN material_master p ON p.material = o.material
                WHERE o.process = ?
                  AND (
                       p.vulcanizado_dias IS NULL
                    OR p.mecanizado_dias IS NULL
                    OR p.inspeccion_externa_dias IS NULL
                  )
                ORDER BY o.material
                """,
                (process,),
            ).fetchall()
        return [str(r[0]) for r in rows]

    def count_missing_process_times_from_orders(self, *, process: str = "terminaciones") -> int:
        process = self._normalize_process(process)
        with self.db.connect() as con:
            row = con.execute(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT DISTINCT o.material
                    FROM orders o
                    JOIN material_master p ON p.material = o.material
                    WHERE o.process = ?
                      AND (
                           p.vulcanizado_dias IS NULL
                        OR p.mecanizado_dias IS NULL
                        OR p.inspeccion_externa_dias IS NULL
                      )
                )
                """,
                (process,),
            ).fetchone()
        return int(row[0])

    def count_missing_parts_from_orders(self, *, process: str = "terminaciones") -> int:
        process = self._normalize_process(process)
        with self.db.connect() as con:
            row = con.execute(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT DISTINCT o.material
                    FROM orders o
                    LEFT JOIN material_master p ON p.material = o.material
                    WHERE o.process = ?
                      AND p.material IS NULL
                )
                """,
                (process,),
            ).fetchone()
        return int(row[0])

    # ---------- Counts ----------
    def count_orders(self, *, process: str = "terminaciones") -> int:
        process = self._normalize_process(process)
        with self.db.connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM orders WHERE process = ?", (process,)).fetchone()[0])

    def count_sap_mb52(self) -> int:
        with self.db.connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM sap_mb52_snapshot").fetchone()[0])

    def count_sap_vision(self) -> int:
        with self.db.connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM sap_vision_snapshot").fetchone()[0])

    def count_usable_pieces(self, *, process: str = "terminaciones") -> int:
        process = self._normalize_process(process)
        centro = (self.get_config(key="sap_centro", default="4000") or "").strip()
        almacen = self._almacen_for_process(process)
        avail_sql = self._mb52_availability_predicate_sql(process=process)
        if not centro or not almacen:
            return 0
        with self.db.connect() as con:
            return int(
                con.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM sap_mb52_snapshot
                    WHERE centro = ?
                      AND almacen = ?
                      AND {avail_sql}
                    """.strip(),
                    (centro, almacen),
                ).fetchone()[0]
            )

    # ---------- Pedido priority master ----------
    def get_pedidos_master_rows(self) -> list[dict]:
        """Rows for UI: distinct (pedido,posicion) currently present in orders + priority flag.

        Priority is stored primarily in `orderpos_priority` (pedido+posicion). For backward
        compatibility with earlier versions, we also read `order_priority` (pedido only) as
        a fallback.
        """
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT
                    o.pedido AS pedido,
                    o.posicion AS posicion,
                    COALESCE(opp.is_priority, op.is_priority, 0) AS is_priority,
                    COALESCE(opp.kind, '') AS priority_kind,
                    COALESCE(MAX(v.cliente), '') AS cliente,
                    COALESCE(MAX(v.cod_material), '') AS cod_material,
                    COALESCE(MAX(v.descripcion_material), '') AS descripcion_material,
                  MIN(COALESCE(v.fecha_de_pedido, o.fecha_entrega)) AS fecha_de_pedido,
                  COALESCE(MAX(v.solicitado), 0) AS solicitado,
                  COALESCE(MAX(v.peso_neto), 0.0) AS peso_neto,
                  COALESCE(MAX(v.bodega), 0) AS bodega,
                  COALESCE(MAX(v.despachado), 0) AS despachado
                FROM orders o
                LEFT JOIN orderpos_priority opp
                       ON opp.pedido = o.pedido AND opp.posicion = o.posicion
                LEFT JOIN order_priority op
                       ON op.pedido = o.pedido
                LEFT JOIN sap_vision v
                       ON v.pedido = o.pedido AND v.posicion = o.posicion
                GROUP BY o.pedido, o.posicion
                ORDER BY COALESCE(opp.is_priority, op.is_priority, 0) DESC, fecha_de_pedido, o.pedido, o.posicion
                """
            ).fetchall()

        out: list[dict] = []
        for r in rows:
            solicitado = int(r["solicitado"] or 0)
            bodega = int(r["bodega"] or 0)
            despachado = int(r["despachado"] or 0)
            pendientes = solicitado - bodega - despachado
            out.append(
                {
                    "pedido": str(r["pedido"]),
                    "posicion": str(r["posicion"]),
                    "is_priority": int(r["is_priority"] or 0),
                    "priority_kind": str(r["priority_kind"] or ""),
                    "cliente": str(r["cliente"] or ""),
                    "cod_material": str(r["cod_material"] or ""),
                    "descripcion_material": str(r["descripcion_material"] or ""),
                    "fecha_de_pedido": str(r["fecha_de_pedido"] or ""),
                    "solicitado": solicitado,
                    "peso_neto": float(r["peso_neto"] or 0.0),
                    "bodega": bodega,
                    "despachado": despachado,
                    "pendientes": int(pendientes),
                }
            )
        return out

    def set_pedido_priority(self, *, pedido: str, posicion: str, is_priority: bool) -> None:
        ped = str(pedido or "").strip()
        pos = str(posicion or "").strip()
        if not ped or not pos:
            raise ValueError("pedido/posición vacío")
        flag = 1 if bool(is_priority) else 0
        with self.db.connect() as con:
            if flag == 1:
                existing = con.execute(
                    "SELECT kind FROM orderpos_priority WHERE pedido=? AND posicion=?",
                    (ped, pos),
                ).fetchone()
                if existing is not None and str(existing[0] or "").strip().lower() == "test":
                    con.execute(
                        "UPDATE orderpos_priority SET is_priority=1 WHERE pedido=? AND posicion=?",
                        (ped, pos),
                    )
                else:
                    con.execute(
                        "INSERT INTO orderpos_priority(pedido, posicion, is_priority, kind) VALUES(?, ?, 1, 'manual') "
                        "ON CONFLICT(pedido, posicion) DO UPDATE SET is_priority=1, kind='manual'",
                        (ped, pos),
                    )
            else:
                # Do not allow disabling production tests (lote alfanumérico): they must remain priority.
                con.execute(
                    "UPDATE orderpos_priority SET is_priority=0 "
                    "WHERE pedido=? AND posicion=? AND COALESCE(kind,'') <> 'test'",
                    (ped, pos),
                )
            # Invalidate any previously generated program
            con.execute("DELETE FROM last_program")
        
        self.log_audit(
            "PRIORITY",
            "Set Priority" if is_priority else "Unset Priority",
            f"Pedido: {ped}, Pos: {pos}"
        )

    def delete_all_pedido_priorities(self, *, keep_tests: bool = True) -> None:
        """Clear all pedido/posición priority flags.

        By default we keep automatically-detected production tests (kind='test'),
        since they must remain prioritized.
        """
        with self.db.connect() as con:
            if keep_tests:
                con.execute("DELETE FROM orderpos_priority WHERE COALESCE(kind,'') <> 'test'")
            else:
                con.execute("DELETE FROM orderpos_priority")
            # Legacy pedido-only priority table.
            con.execute("DELETE FROM order_priority")
            con.execute("DELETE FROM last_program")

    def list_priority_orderpos(self) -> list[tuple[str, str]]:
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT pedido, posicion
                FROM orderpos_priority
                WHERE COALESCE(is_priority, 0) = 1
                ORDER BY pedido, posicion
                """
            ).fetchall()
        return [(str(r[0]), str(r[1])) for r in rows]

    def get_priority_orderpos_set(self) -> set[tuple[str, str]]:
        """Priority keys for scheduling: (pedido, posicion).

        Uses `orderpos_priority` and also applies legacy pedido-only priority (`order_priority`)
        to all positions currently present in `orders`.
        """
        with self.db.connect() as con:
            direct = con.execute(
                """
                SELECT pedido, posicion
                FROM orderpos_priority
                WHERE COALESCE(is_priority, 0) = 1
                """
            ).fetchall()

            legacy = con.execute(
                """
                SELECT DISTINCT o.pedido, o.posicion
                FROM orders o
                INNER JOIN order_priority op ON op.pedido = o.pedido
                WHERE COALESCE(op.is_priority, 0) = 1
                """
            ).fetchall()

        out: set[tuple[str, str]] = set()
        for r in direct:
            out.add((str(r[0]).strip(), str(r[1]).strip()))
        for r in legacy:
            out.add((str(r[0]).strip(), str(r[1]).strip()))
        return out

    def get_manual_priority_orderpos_set(self) -> set[tuple[str, str]]:
        """Manual (non-test) priorities as (pedido, posicion)."""
        with self.db.connect() as con:
            direct = con.execute(
                """
                SELECT pedido, posicion
                FROM orderpos_priority
                WHERE COALESCE(is_priority, 0) = 1
                  AND COALESCE(kind, '') <> 'test'
                """
            ).fetchall()

            legacy = con.execute(
                """
                SELECT DISTINCT o.pedido, o.posicion
                FROM orders o
                INNER JOIN order_priority op ON op.pedido = o.pedido
                WHERE COALESCE(op.is_priority, 0) = 1
                """
            ).fetchall()

        out: set[tuple[str, str]] = set()
        for r in direct:
            out.add((str(r[0]).strip(), str(r[1]).strip()))
        for r in legacy:
            out.add((str(r[0]).strip(), str(r[1]).strip()))
        return out

    def get_test_orderpos_set(self) -> set[tuple[str, str]]:
        """Production test order positions (lote alfanumérico) as (pedido, posicion)."""
        centro = (self.get_config(key="sap_centro", default="4000") or "").strip()
        almacen = (self.get_config(key="sap_almacen_terminaciones", default="4035") or "").strip()

        with self.db.connect() as con:
            from_priority = con.execute(
                """
                SELECT pedido, posicion
                FROM orderpos_priority
                WHERE COALESCE(is_priority, 0) = 1
                  AND COALESCE(kind, '') = 'test'
                """
            ).fetchall()

            from_mb52 = []
            if centro and almacen:
                from_mb52 = con.execute(
                    """
                    SELECT DISTINCT documento_comercial AS pedido, posicion_sd AS posicion
                    FROM sap_mb52_snapshot
                    WHERE centro = ?
                      AND almacen = ?
                      AND COALESCE(libre_utilizacion, 0) = 1
                      AND COALESCE(en_control_calidad, 0) = 0
                      AND lote IS NOT NULL AND TRIM(lote) <> ''
                      AND (lote GLOB '*[A-Za-z]*')
                      AND documento_comercial IS NOT NULL AND TRIM(documento_comercial) <> ''
                      AND posicion_sd IS NOT NULL AND TRIM(posicion_sd) <> ''
                    """,
                    (centro, almacen),
                ).fetchall()

        out: set[tuple[str, str]] = set()
        for r in from_priority:
            out.add((str(r[0]).strip(), str(r[1]).strip()))
        for r in from_mb52:
            out.add((str(r[0]).strip(), str(r[1]).strip()))
        return out

    def list_priority_pedidos(self) -> list[str]:
        """Backward-compatible API (pedido-only priorities)."""
        with self.db.connect() as con:
            rows = con.execute(
                "SELECT pedido FROM order_priority WHERE COALESCE(is_priority, 0) = 1 ORDER BY pedido"
            ).fetchall()
        return [str(r[0]) for r in rows]

    def get_orders_rows(self, limit: int = 200) -> list[dict]:
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT pedido, posicion, material, cantidad, fecha_entrega, primer_correlativo, ultimo_correlativo
                FROM orders
                ORDER BY fecha_entrega, pedido, posicion, primer_correlativo
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [
            {
                "pedido": str(r[0]),
                "posicion": str(r[1]),
                "material": str(r[2]),
                "cantidad": int(r[3]),
                "fecha_entrega": str(r[4]),
                "primer_correlativo": int(r[5]),
                "ultimo_correlativo": int(r[6]),
            }
            for r in rows
        ]

    def count_parts(self) -> int:
        with self.db.connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM material_master").fetchone()[0])

    def get_missing_parts_from_mb52(self) -> list[dict]:
        """Backward-compatible (Terminaciones) missing master detection."""
        return self.get_missing_parts_from_mb52_for(process="terminaciones")

    def get_missing_parts_from_mb52_for(self, *, process: str = "terminaciones", limit: int = 500) -> list[dict]:
        """Distinct materials in MB52 for a process almacen not present in the local parts master.

        Returns a list of dicts with keys: material, texto_breve.
        """
        process = self._normalize_process(process)
        centro = (self.get_config(key="sap_centro", default="4000") or "").strip()
        almacen = self._almacen_for_process(process)
        avail_sql = self._mb52_availability_predicate_sql(process=process)
        if not centro or not almacen:
            return []
        lim = int(limit or 500)
        lim = max(1, min(lim, 5000))
        with self.db.connect() as con:
            rows = con.execute(
                                f"""
                SELECT
                    m.material,
                    COALESCE(MAX(m.texto_breve), '') AS texto_breve,
                    MAX(p.family_id) as family_id,
                    MAX(p.vulcanizado_dias) as vulcanizado_dias,
                    MAX(p.mecanizado_dias) as mecanizado_dias,
                    MAX(p.inspeccion_externa_dias) as inspeccion_externa_dias,
                    MAX(p.mec_perf_inclinada) as mec_perf_inclinada,
                    MAX(p.sobre_medida_mecanizado) as sobre_medida_mecanizado,
                    MAX(p.aleacion) as aleacion,
                    MAX(p.piezas_por_molde) as piezas_por_molde,
                    MAX(p.peso_bruto_ton) as peso_bruto_ton,
                    MAX(p.tiempo_enfriamiento_molde_dias) as tiempo_enfriamiento_molde_dias
                FROM sap_mb52_snapshot m
                LEFT JOIN material_master p ON p.material = m.material
                WHERE m.material IS NOT NULL AND TRIM(m.material) <> ''
                  AND m.centro = ?
                  AND m.almacen = ?
                                    AND {avail_sql.replace('libre_utilizacion', 'm.libre_utilizacion').replace('en_control_calidad', 'm.en_control_calidad')}
                  AND m.documento_comercial IS NOT NULL AND TRIM(m.documento_comercial) <> ''
                  AND m.posicion_sd IS NOT NULL AND TRIM(m.posicion_sd) <> ''
                  AND m.lote IS NOT NULL AND TRIM(m.lote) <> ''
                  AND (
                      p.material IS NULL
                      OR p.family_id IS NULL OR TRIM(p.family_id) = ''
                      OR p.vulcanizado_dias IS NULL
                      OR p.mecanizado_dias IS NULL
                      OR p.inspeccion_externa_dias IS NULL
                  )
                GROUP BY m.material
                ORDER BY m.material
                LIMIT ?
                                """.strip(),
                (centro, almacen, lim),
            ).fetchall()
        return [
            {
                "material": str(r["material"]),
                "texto_breve": str(r["texto_breve"] or ""),
                "family_id": r["family_id"],
                "vulcanizado_dias": r["vulcanizado_dias"],
                "mecanizado_dias": r["mecanizado_dias"],
                "inspeccion_externa_dias": r["inspeccion_externa_dias"],
                "mec_perf_inclinada": r["mec_perf_inclinada"],
                "sobre_medida_mecanizado": r["sobre_medida_mecanizado"],
                "aleacion": r["aleacion"],
                "piezas_por_molde": r["piezas_por_molde"],
                "peso_bruto_ton": r["peso_bruto_ton"],
                "tiempo_enfriamiento_molde_dias": r["tiempo_enfriamiento_molde_dias"],
            }
            for r in rows
        ]

    def get_missing_parts_from_vision_for(self, *, limit: int = 500) -> list[dict]:
        """Distinct materials in Visión Planta not present in the local parts master.
        
        Note: Visión Planta is global, not per-process (it's customer orders).
        So we return ANY material in Vision that is missing from master.
        """
        lim = int(limit or 500)
        lim = max(1, min(lim, 5000))
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT
                    m.cod_material,
                    COALESCE(MAX(m.descripcion_material), '') AS descripcion_material,
                    MAX(p.family_id) as family_id,
                    MAX(p.vulcanizado_dias) as vulcanizado_dias,
                    MAX(p.mecanizado_dias) as mecanizado_dias,
                    MAX(p.inspeccion_externa_dias) as inspeccion_externa_dias,
                    MAX(p.mec_perf_inclinada) as mec_perf_inclinada,
                    MAX(p.sobre_medida_mecanizado) as sobre_medida_mecanizado,
                    MAX(p.aleacion) as aleacion,
                    MAX(p.piezas_por_molde) as piezas_por_molde,
                    MAX(p.peso_bruto_ton) as peso_bruto_ton,
                    MAX(p.tiempo_enfriamiento_molde_dias) as tiempo_enfriamiento_molde_dias
                FROM sap_vision_snapshot m
                LEFT JOIN material_master p ON p.material = m.cod_material
                WHERE m.cod_material IS NOT NULL AND TRIM(m.cod_material) <> ''
                  AND (
                      p.material IS NULL
                      OR p.family_id IS NULL OR TRIM(p.family_id) = ''
                      OR p.vulcanizado_dias IS NULL
                      OR p.mecanizado_dias IS NULL
                      OR p.inspeccion_externa_dias IS NULL
                  )
                GROUP BY m.cod_material
                ORDER BY m.cod_material
                LIMIT ?
                """.strip(),
                (lim,),
            ).fetchall()
        return [
            {
                "material": str(r["cod_material"]),
                "texto_breve": str(r["descripcion_material"] or ""),
                "family_id": r["family_id"],
                "vulcanizado_dias": r["vulcanizado_dias"],
                "mecanizado_dias": r["mecanizado_dias"],
                "inspeccion_externa_dias": r["inspeccion_externa_dias"],
                "mec_perf_inclinada": r["mec_perf_inclinada"],
                "sobre_medida_mecanizado": r["sobre_medida_mecanizado"],
                "aleacion": r["aleacion"],
                "piezas_por_molde": r["piezas_por_molde"],
                "peso_bruto_ton": r["peso_bruto_ton"],
                "tiempo_enfriamiento_molde_dias": r["tiempo_enfriamiento_molde_dias"],
            }
            for r in rows
        ]

    def get_mb52_texto_breve(self, *, material: str) -> str:
        """Returns the latest known short description for a material from MB52."""
        mat = str(material or "").strip()
        if not mat:
            return ""
        with self.db.connect() as con:
            row = con.execute(
                """
                SELECT COALESCE(MAX(texto_breve), '')
                FROM sap_mb52_snapshot
                WHERE material = ?
                """,
                (mat,),
            ).fetchone()
        return str((row[0] if row else "") or "")

    # ---------- Import ----------
    def import_excel_bytes(self, *, kind: str, content: bytes) -> None:
        size_kb = len(content) / 1024
        self.log_audit("DATA_LOAD", f"Importing {kind.upper()}", f"Size: {size_kb:.1f} KB")

        read_excel_bytes(content)

        # The app currently supports SAP-driven imports only.
        # Orders are rebuilt by joining MB52 + Visión.

        if kind in {"mb52", "sap_mb52"}:
            self.import_sap_mb52_bytes(content=content, mode="replace")
            return

        if kind in {"vision", "vision_planta", "sap_vision"}:
            self.import_sap_vision_bytes(content=content)
            return

        raise ValueError(f"kind no soportado: {kind}")

    def clear_imported_data(self) -> None:
        with self.db.connect() as con:
            con.execute("DELETE FROM orders")
            con.execute("DELETE FROM sap_mb52_snapshot")
            con.execute("DELETE FROM sap_vision_snapshot")
            # parts (family_ids) are managed manually in-app; keep them.
            con.execute("DELETE FROM last_program")

    # ---------- SAP Import + rebuild ----------
    def import_sap_mb52_bytes(self, *, content: bytes, mode: str = "replace") -> None:
        """Import MB52 data.

        Modes:
        - replace: clears the whole sap_mb52 table, then inserts rows
        - merge: replaces rows only for the (centro, almacen) pairs present in this upload
        """
        mode = str(mode or "replace").strip().lower()
        if mode not in {"replace", "merge"}:
            raise ValueError(f"mode no soportado: {mode}")

        df_raw = read_excel_bytes(content)
        df = normalize_columns(df_raw)

        required = {
            "material",
            "centro",
            "almacen",
            "lote",
            "libre_utilizacion",
            "documento_comercial",
            "posicion_sd",
            "en_control_calidad",
        }
        self._validate_columns(df.columns, required)

        rows: list[tuple] = []
        centro_almacen_pairs: set[tuple[str, str]] = set()

        prefixes_raw = str(self.get_config(key="sap_material_prefixes", default="436") or "").strip()
        prefixes = [p.strip() for p in prefixes_raw.split(",") if p.strip()]
        keep_all_materials = (not prefixes) or ("*" in prefixes)

        rows_snapshot: list[tuple] = []  # For sap_mb52_snapshot (v0.2 only, no legacy)

        for _, r in df.iterrows():
            material = str(r.get("material", "")).strip()
            if not material:
                continue
            # Business rule (configurable): keep only selected material prefixes.
            if not keep_all_materials and not any(material.startswith(p) for p in prefixes):
                continue
            texto_breve = str(r.get("texto_breve_de_material", "") or r.get("texto_breve", "") or "").strip() or None
            centro = self._normalize_sap_key(r.get("centro"))
            almacen = self._normalize_sap_key(r.get("almacen"))
            if centro and almacen:
                centro_almacen_pairs.add((str(centro), str(almacen)))
            lote = str(r.get("lote", "")).strip() or None
            pb_almacen = float(r.get("pb_a_nivel_de_almacen", 0) or r.get("pb_almacen", 0) or 0) or None
            libre = to_int01(r.get("libre_utilizacion"))
            doc = self._normalize_sap_key(r.get("documento_comercial"))
            pos = self._normalize_sap_key(r.get("posicion_sd"))
            qc = to_int01(r.get("en_control_calidad"))
            
            # Derive fields for snapshot table
            correlativo_int = self._lote_to_int(lote) if lote else None
            is_test = 1 if (lote and self._is_lote_test(lote)) else 0
            
            # Snapshot table (v0.2) - includes derived fields
            rows_snapshot.append((
                material, texto_breve, centro, almacen, lote, pb_almacen,
                libre, doc, pos, qc, correlativo_int, is_test
            ))

        with self.db.connect() as con:
            if mode == "replace":
                con.execute("DELETE FROM sap_mb52_snapshot")
            else:
                # Merge mode: replace only the centro/almacen subsets present in this file.
                for c, a in sorted(centro_almacen_pairs):
                    con.execute("DELETE FROM sap_mb52_snapshot WHERE centro = ? AND almacen = ?", (c, a))
            
            # Insert into snapshot table (v0.2 only)
            con.executemany(
                """
                INSERT INTO sap_mb52_snapshot(
                    material, texto_breve, centro, almacen, lote, pb_almacen,
                    libre_utilizacion, documento_comercial, posicion_sd, en_control_calidad,
                    correlativo_int, is_test
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows_snapshot,
            )

            # Imported SAP data invalidates all derived orders/programs.
            con.execute("DELETE FROM orders")
            con.execute("DELETE FROM last_program")
            
            # FASE 3.1: Create jobs automatically from MB52 for all configured processes
            self._create_jobs_from_mb52(con=con)

    def _create_jobs_from_mb52(self, *, con) -> None:
        """Create/update jobs from MB52 snapshot for all active processes.
        
        Called automatically after MB52 import. Creates 1 job per (process_id, pedido, posicion, material)
        for each active process that has stock in MB52.
        """
        from uuid import uuid4
        
        # Get job_priority_map config
        priority_map_str = self.get_config(key="job_priority_map", default='{"prueba": 1, "urgente": 2, "normal": 3}')
        try:
            priority_map = json.loads(priority_map_str) if isinstance(priority_map_str, str) else priority_map_str
        except Exception:
            priority_map = {"prueba": 1, "urgente": 2, "normal": 3}
        
        priority_normal = int(priority_map.get("normal", 3))
        priority_prueba = int(priority_map.get("prueba", 1))
        
        # Get all active processes
        processes = con.execute(
            "SELECT process_id, sap_almacen FROM process WHERE is_active = 1 AND sap_almacen IS NOT NULL"
        ).fetchall()
        
        centro_config = self.get_config(key="sap_centro", default="4000") or "4000"
        centro_normalized = self._normalize_sap_key(centro_config) or centro_config
        
        for proc_row in processes:
            process_id = str(proc_row["process_id"])
            almacen = str(proc_row["sap_almacen"])
            
            # Track which jobs get updated during this import
            # Jobs NOT in this set will be reset to qty_total=0 at the end
            updated_job_ids: set[str] = set()
            
            # Filter MB52 by almacen and availability predicate
            avail_sql = self._mb52_availability_predicate_sql(process=process_id)
            
            # Group by (pedido, posicion, material) and aggregate lotes
            rows = con.execute(
                f"""
                SELECT 
                    documento_comercial AS pedido,
                    posicion_sd AS posicion,
                    material,
                    COUNT(*) AS qty_total,
                    MAX(CASE WHEN is_test = 1 THEN 1 ELSE 0 END) AS has_test_lotes
                FROM sap_mb52_snapshot
                WHERE centro = ?
                  AND almacen = ?
                  AND {avail_sql}
                  AND documento_comercial IS NOT NULL AND TRIM(documento_comercial) <> ''
                  AND posicion_sd IS NOT NULL AND TRIM(posicion_sd) <> ''
                  AND material IS NOT NULL AND TRIM(material) <> ''
                GROUP BY documento_comercial, posicion_sd, material
                """,
                (str(centro_normalized), almacen),
            ).fetchall()
            
            for r in rows:
                pedido = str(r["pedido"]).strip()
                posicion = str(r["posicion"]).strip()
                material = str(r["material"]).strip()
                qty_total = int(r["qty_total"])
                is_test = int(r["has_test_lotes"])
                
                if not pedido or not posicion or not material:
                    continue
                
                # Check if jobs exist (may have multiple splits)
                existing_jobs = con.execute(
                    """
                    SELECT job_id, qty_total
                    FROM job
                    WHERE process_id = ? AND pedido = ? AND posicion = ? AND material = ?
                    ORDER BY qty_total ASC
                    """,
                    (process_id, pedido, posicion, material),
                ).fetchall()
                
                # Determine priority: tests use "prueba", otherwise "normal"
                priority = priority_prueba if is_test else priority_normal
                
                # FASE 3.2 FIX: Split Retention Logic
                # We must map existing lotes to their current jobs to preserve splits.
                current_lote_map: dict[str, str] = {}
                target_job_id: str | None = None
                
                if existing_jobs:
                    # Check if all existing are "dead" (qty=0)
                    all_zero = all(int(j["qty_total"]) == 0 for j in existing_jobs)
                    
                    if not all_zero:
                        # We have active jobs. 
                        # 1. Build map of current lotes to preserve them
                        job_ids = [str(j["job_id"]) for j in existing_jobs]
                        placeholders = ','.join('?' * len(job_ids))
                        unit_rows = con.execute(
                            f"SELECT lote, job_id FROM job_unit WHERE job_id IN ({placeholders})",
                            job_ids
                        ).fetchall()
                        for u in unit_rows:
                            if u["lote"]:
                                current_lote_map[str(u["lote"]).strip()] = str(u["job_id"])
                        
                        # 2. Pick target for NEW lotes (emptiest job)
                        target_job_id = str(existing_jobs[0]["job_id"])
                
                # If no active job found/selected, create a new one
                if not target_job_id:
                    new_job_id = f"job_{process_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"
                    con.execute(
                        """
                        INSERT INTO job(
                            job_id, process_id, pedido, posicion, material,
                            qty_total, qty_remaining, priority, is_test, state,
                            created_at, updated_at
                        )
                        VALUES(?, ?, ?, ?, ?, 0, 0, ?, ?, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        """,
                        (new_job_id, process_id, pedido, posicion, material, priority, is_test),
                    )
                    target_job_id = new_job_id

                # Get all current MB52 lotes for this key
                lotes_rows = con.execute(
                    f"""
                    SELECT lote, correlativo_int
                    FROM sap_mb52_snapshot
                    WHERE centro = ?
                      AND almacen = ?
                      AND documento_comercial = ?
                      AND posicion_sd = ?
                      AND material = ?
                      AND {avail_sql}
                      AND lote IS NOT NULL AND TRIM(lote) <> ''
                    """,
                    (str(centro_normalized), almacen, pedido, posicion, material),
                ).fetchall()
                
                # Allocation Plan: job_id -> list of (lote, corr)
                allocations: dict[str, list[tuple[str, int | None]]] = {}
                
                for lote_row in lotes_rows:
                    lote = str(lote_row["lote"]).strip()
                    corr = lote_row["correlativo_int"]
                    
                    if not lote:
                        continue
                        
                    # Rule: Keep in existing job if mapped, else go to target
                    assigned_job_id = current_lote_map.get(lote, target_job_id)
                    # Safety check: if mapped job is not in our known list (shouldn't happen), fall back
                    if not assigned_job_id: 
                         assigned_job_id = target_job_id
                         
                    allocations.setdefault(str(assigned_job_id), []).append((lote, corr))
                
                # Apply allocations to DB
                for job_id, items in allocations.items():
                    qty = len(items)
                    # Update job header
                    con.execute(
                        """
                        UPDATE job
                        SET qty_total = ?,
                            qty_remaining = ?,
                            is_test = ?,
                            priority = ?,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE job_id = ?
                        """,
                        (qty, qty, is_test, priority, job_id)
                    )
                    
                    # Replace job units
                    con.execute("DELETE FROM job_unit WHERE job_id = ?", (job_id,))
                    
                    for lote, corr in items:
                        job_unit_id = f"ju_{job_id}_{uuid4().hex[:8]}"
                        con.execute(
                            """
                            INSERT INTO job_unit(
                                job_unit_id, job_id, lote, correlativo_int, qty, status,
                                created_at, updated_at
                            )
                            VALUES(?, ?, ?, ?, 1, 'available', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                            """,
                            (job_unit_id, job_id, lote, corr),
                        )
                        
                    updated_job_ids.add(job_id)
            
            # Reset jobs that were NOT updated in this import (splits without new stock, etc.)
            # This ensures qty_total reflects current MB52 state
            if updated_job_ids:
                placeholders = ','.join('?' * len(updated_job_ids))
                con.execute(
                    f"""
                    DELETE FROM job_unit
                    WHERE job_id IN (
                        SELECT job_id FROM job
                        WHERE process_id = ?
                          AND job_id NOT IN ({placeholders})
                    )
                    """,
                    (process_id, *updated_job_ids)
                )
                con.execute(
                    f"""
                    UPDATE job
                    SET qty_total = 0, updated_at = CURRENT_TIMESTAMP
                    WHERE process_id = ?
                      AND job_id NOT IN ({placeholders})
                    """,
                    (process_id, *updated_job_ids)
                )
            else:
                # No jobs were updated, reset all for this process
                con.execute(
                    "DELETE FROM job_unit WHERE job_id IN (SELECT job_id FROM job WHERE process_id = ?)",
                    (process_id,)
                )
                con.execute(
                    "UPDATE job SET qty_total = 0, updated_at = CURRENT_TIMESTAMP WHERE process_id = ?",
                    (process_id,)
                )
            
            # Cleanup: Delete jobs with qty=0 to keep the table clean.
            # This respects the "SAP is source of truth" principle: if stock is 0, job is gone.
            con.execute(
                "DELETE FROM job WHERE process_id = ? AND qty_total = 0",
                (process_id,)
            )





    def get_sap_mb52_almacen_counts(self, *, centro: str | None = None, limit: int = 50) -> list[dict]:
        """Return counts per almacen in sap_mb52 (optionally filtered by centro)."""
        lim = int(limit or 50)
        lim = max(1, min(lim, 500))
        centro_n = None
        if centro is not None:
            centro_s = str(centro).strip()
            centro_n = self._normalize_sap_key(centro_s) or centro_s
        with self.db.connect() as con:
            if centro_n:
                rows = con.execute(
                    "SELECT almacen, COUNT(*) c FROM sap_mb52_snapshot WHERE centro = ? GROUP BY almacen ORDER BY c DESC LIMIT ?",
                    (centro_n, lim),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT almacen, COUNT(*) c FROM sap_mb52_snapshot GROUP BY almacen ORDER BY c DESC LIMIT ?",
                    (lim,),
                ).fetchall()
        return [{"almacen": str(r[0] or ""), "count": int(r[1] or 0)} for r in rows]

    def get_vision_stage_breakdown(self, *, pedido: str, posicion: str) -> dict:
        """Return Visión Planta stage counts for a pedido/posición.

        This is a best-effort reader: if a column is missing or NULL, it will be
        returned as None.
        """
        ped = self._normalize_sap_key(pedido) or str(pedido or "").strip()
        pos = self._normalize_sap_key(posicion) or str(posicion or "").strip()
        if not ped or not pos:
            raise ValueError("pedido/posición vacío")

        with self.db.connect() as con:
            row = con.execute(
                """
                SELECT
                    pedido, posicion,
                    COALESCE(cliente, '') AS cliente,
                    COALESCE(cod_material, '') AS cod_material,
                    COALESCE(descripcion_material, '') AS descripcion_material,
                    COALESCE(fecha_entrega, '') AS fecha_entrega,
                    solicitado,
                    x_programar, programado, x_fundir, desmoldeo, tt, terminacion,
                    mecanizado_interno, mecanizado_externo, vulcanizado, insp_externa,
                    en_vulcaniz, pend_vulcanizado, rech_insp_externa, lib_vulcaniz_de,
                    bodega, despachado, rechazo
                FROM sap_vision_snapshot
                WHERE pedido = ? AND posicion = ?
                LIMIT 1
                """,
                (ped, pos),
            ).fetchone()

        if row is None:
            return {
                "pedido": ped,
                "posicion": pos,
                "found": 0,
                "stages": [],
            }

        def _opt_int(v) -> int | None:
            try:
                return int(v) if v is not None else None
            except Exception:
                return None

        # Sumar Lib. Vulcanizado (DE) a En Vulcanizado
        en_vulcanizado_total = (_opt_int(row["en_vulcaniz"]) or 0) + (_opt_int(row["lib_vulcaniz_de"]) or 0) if "en_vulcaniz" in row.keys() and "lib_vulcaniz_de" in row.keys() else (_opt_int(row.get("en_vulcaniz")) if "en_vulcaniz" in row.keys() else None)

        stages = [
            ("x_programar", "Por programar en la planta"),
            ("programado", "Por Moldear"),
            ("x_fundir", "Por Fundir"),
            ("desmoldeo", "En enfriamiento"),
            ("tt", "En Tratamientos Térmicos"),
            ("terminacion", "En Terminaciones"),
            ("pend_vulcanizado", "Por Vulcanizar"),
            ("en_vulcanizado_computed", "En Vulcanizado"),
            ("insp_externa", "Insp. Externa"),
            ("mecanizado_interno", "Mecanizado Interno"),
            ("mecanizado_externo", "Mecanizado Externo"),
            ("bodega", "Bodega"),
            ("despachado", "Despachado"),
        ]

        quality_stages = [
            ("rechazo", "Rechazo"),
            ("rech_insp_externa", "Rech. Insp. Externa"),
        ]

        out_rows: list[dict] = []
        for key, label in stages:
            if key == "en_vulcanizado_computed":
                # Usar el total sumado
                out_rows.append(
                    {
                        "_row_id": "en_vulcanizado",
                        "estado": label,
                        "piezas": en_vulcanizado_total,
                    }
                )
            else:
                out_rows.append(
                    {
                        "_row_id": key,
                        "estado": label,
                        "piezas": _opt_int(row[key]) if key in row.keys() else None,
                    }
                )

        quality_rows: list[dict] = []
        for key, label in quality_stages:
            quality_rows.append(
                {
                    "_row_id": key,
                    "estado": label,
                    "piezas": _opt_int(row[key]) if key in row.keys() else None,
                }
            )

        return {
            "pedido": str(row["pedido"]),
            "posicion": str(row["posicion"]),
            "cliente": str(row["cliente"] or ""),
            "cod_material": str(row["cod_material"] or ""),
            "descripcion_material": str(row["descripcion_material"] or ""),
            "fecha_entrega": str(row["fecha_entrega"] or ""),
            "solicitado": _opt_int(row["solicitado"]),
            "found": 1,
            "stages": out_rows,
            "quality_stages": quality_rows,
        }

    def import_sap_vision_bytes(self, *, content: bytes) -> None:
        df_raw = read_excel_bytes(content)
        df = normalize_columns(df_raw)

        # Canonicalize a couple of common header variants
        if "pos" in df.columns and "posicion" not in df.columns:
            df = df.rename(columns={"pos": "posicion"})
        if "pos_oc" in df.columns and "posoc" not in df.columns:
            df = df.rename(columns={"pos_oc": "posoc"})
        # some exports might call it 'fecha_de_pedido'
        if "fecha_de_pedido" not in df.columns and "fecha_de_pedido" in df.columns:
            df = df.rename(columns={"fecha_de_pedido": "fecha_de_pedido"})

        # Tipo posicion variants
        if "tipo_posicion" not in df.columns:
            for c in list(df.columns):
                if str(c).lower() in ["tip_pos", "tippos", "tipo_pos"]:
                    df = df.rename(columns={c: "tipo_posicion"})
                    break

        # Status comercial variants
        if "status_comercial" not in df.columns:
            for c in list(df.columns):
                if str(c).lower() in ["status_comercial", "status_comerc", "statuscomercial", "stat_comerc"]:
                    df = df.rename(columns={c: "status_comercial"})
                    break

        # Weight column variants (optional)
        if "peso_neto" not in df.columns:
            for c in list(df.columns):
                if str(c).startswith("peso_neto"):
                    df = df.rename(columns={c: "peso_neto"})
                    break

        # Progress column variants (optional)
        if "bodega" not in df.columns:
            for c in list(df.columns):
                if str(c).startswith("bodega") or str(c).startswith("en_bodega"):
                    df = df.rename(columns={c: "bodega"})
                    break
        if "despachado" not in df.columns:
            for c in list(df.columns):
                if str(c).startswith("despachado"):
                    df = df.rename(columns={c: "despachado"})
                    break

        # Optional per-stage piece count columns (normalize common variants)
        stage_aliases: dict[str, list[str]] = {
            "x_programar": ["x_programar", "por_programar", "a_programar", "sin_programar"],
            "programado": ["programado"],
            "por_fundir": ["por_fundir", "porfundir", "x_fundir", "fundir", "fundida"],
            "desmoldeo": ["desmoldeo"],
            "tt": ["tt", "tratamiento_termico", "tratamiento_termico_tt"],
            "terminaciones": ["terminaciones", "terminacion", "terminacio", "terminacin"],
            "mecanizado_interno": ["mecanizado_interno", "mec_interno", "mecanizado_int"],
            "mecanizado_externo": ["mecanizado_externo", "mec_externo", "mecanizado_ext"],
            "vulcanizado": ["vulcanizado", "vulc"],
            "insp_externa": ["insp_externa", "inspeccion_externa", "insp_ext"],
            "en_vulcanizado": ["en_vulcanizado", "en_vulcaniz", "en_vulc", "en_vulcaniz"],
            "pend_vulcanizado": ["pend_vulcanizado", "pend_vulc", "pend_vulcaniz", "pendiente_vulcanizado"],
            "rech_insp_externa": ["rech_insp_externa", "rech_insp_ext", "rechazo_insp_externa", "rech_insp"],
            "lib_vulcanizado_de": ["lib_vulcanizado_de", "lib_vulcaniz_de", "lib_vulc_de", "liberado_vulcanizado"],
            "rechazo": ["rechazo", "rechazado"],
        }
        for canonical, candidates in stage_aliases.items():
            if canonical in df.columns:
                continue
            for cand in candidates:
                if cand in df.columns:
                    df = df.rename(columns={cand: canonical})
                    break

        self._validate_columns(df.columns, {"pedido", "posicion", "cod_material", "fecha_de_pedido"})

        rows: list[tuple] = []
        for _, r in df.iterrows():
            pedido = self._normalize_sap_key(r.get("pedido")) or ""
            posicion = self._normalize_sap_key(r.get("posicion")) or ""
            if not pedido or not posicion:
                continue

            tipo_posicion = str(r.get("tipo_posicion", "") or "").strip() or None
            cod_material = self._normalize_sap_key(r.get("cod_material"))

            # Filter: Material family 402/403/404, or explicit ZTLH exception
            is_valid_mat = cod_material and (cod_material.startswith("402") or cod_material.startswith("403") or cod_material.startswith("404"))
            is_ztlh = (tipo_posicion == "ZTLH")

            if not is_valid_mat and not is_ztlh:
                continue

            fecha_de_pedido = coerce_date(r.get("fecha_de_pedido"))
            # Filter: Date > 2023-12-31
            if not fecha_de_pedido or fecha_de_pedido <= "2023-12-31":
                continue

            # Filter: Status comercial (Active only)
            # We use case-insensitive check to be robust against Excel variations
            status_comercial = str(r.get("status_comercial", "") or "").strip() or None
            if status_comercial and status_comercial.lower() != "activo":
                continue

            desc = str(r.get("descripcion_material", "")).strip() or None
            fecha_entrega = None
            if "fecha_entrega" in df.columns:
                raw = r.get("fecha_entrega")
                if raw is not None and str(raw).strip() and str(raw).strip().lower() != "nan":
                    try:
                        fecha_entrega = coerce_date(raw)
                    except Exception:
                        fecha_entrega = None
            solicitado = None
            if "solicitado" in df.columns:
                raw = r.get("solicitado")
                try:
                    solicitado = int(float(raw)) if raw is not None and str(raw).strip() and str(raw).strip().lower() != "nan" else None
                except Exception:
                    solicitado = None

            def _coerce_opt_int(col: str) -> int | None:
                if col not in df.columns:
                    return None
                raw = r.get(col)
                try:
                    return int(float(raw)) if raw is not None and str(raw).strip() and str(raw).strip().lower() != "nan" else None
                except Exception:
                    return None

            x_programar = _coerce_opt_int("x_programar")
            programado = _coerce_opt_int("programado")
            por_fundir = _coerce_opt_int("por_fundir")
            desmoldeo = _coerce_opt_int("desmoldeo")
            tt = _coerce_opt_int("tt")
            terminaciones = _coerce_opt_int("terminaciones")
            mecanizado_interno = _coerce_opt_int("mecanizado_interno")
            mecanizado_externo = _coerce_opt_int("mecanizado_externo")
            vulcanizado = _coerce_opt_int("vulcanizado")
            insp_externa = _coerce_opt_int("insp_externa")
            en_vulcanizado = _coerce_opt_int("en_vulcanizado")
            pend_vulcanizado = _coerce_opt_int("pend_vulcanizado")
            rech_insp_externa = _coerce_opt_int("rech_insp_externa")
            lib_vulcanizado_de = _coerce_opt_int("lib_vulcanizado_de")
            rechazo = _coerce_opt_int("rechazo")

            bodega = None
            if "bodega" in df.columns:
                raw = r.get("bodega")
                try:
                    bodega = int(float(raw)) if raw is not None and str(raw).strip() and str(raw).strip().lower() != "nan" else None
                except Exception:
                    bodega = None

            despachado = None
            if "despachado" in df.columns:
                raw = r.get("despachado")
                try:
                    despachado = int(float(raw)) if raw is not None and str(raw).strip() and str(raw).strip().lower() != "nan" else None
                except Exception:
                    despachado = None

            # Visión Planta provides weights in kg; the app uses tons.
            # We store `peso_neto` in tons and `peso_unitario_ton` as tons per piece.
            peso_neto = None
            peso_unitario_ton = None
            if "peso_neto" in df.columns:
                peso_neto_kg = coerce_float(r.get("peso_neto"))
                if peso_neto_kg is not None:
                    try:
                        peso_neto = float(peso_neto_kg) / 1000.0
                    except Exception:
                        peso_neto = None

                if peso_neto is not None and solicitado is not None and int(solicitado) > 0:
                    try:
                        peso_unitario_ton = float(peso_neto) / float(int(solicitado))
                    except Exception:
                        peso_unitario_ton = None

            cliente = str(r.get("cliente", "")).strip() or None
            oc_cliente = str(r.get("n_oc_cliente", "") or "").strip() or None
            tipo_posicion = str(r.get("tipo_posicion", "") or "").strip() or None
            status_comercial = str(r.get("status_comercial", "") or "").strip() or None
            rows.append(
                (
                    pedido,
                    posicion,
                    cod_material,
                    desc,
                    fecha_de_pedido,
                    fecha_entrega,
                    solicitado,
                    x_programar,
                    programado,
                    por_fundir,
                    desmoldeo,
                    tt,
                    terminaciones,
                    mecanizado_interno,
                    mecanizado_externo,
                    vulcanizado,
                    insp_externa,
                    en_vulcanizado,
                    pend_vulcanizado,
                    rech_insp_externa,
                    lib_vulcanizado_de,
                    cliente,
                    oc_cliente,
                    peso_neto,
                    peso_unitario_ton,
                    bodega,
                    despachado,
                    rechazo,
                    tipo_posicion,
                    status_comercial,
                )
            )

        with self.db.connect() as con:
            # v0.2 only: sap_vision_snapshot
            con.execute("DELETE FROM sap_vision_snapshot")
            con.executemany(
                """
                INSERT INTO sap_vision_snapshot(
                    pedido, posicion, cod_material, descripcion_material, fecha_de_pedido, fecha_entrega,
                    solicitado,
                    x_programar, programado, x_fundir, desmoldeo, tt, terminacion,
                    mecanizado_interno, mecanizado_externo, vulcanizado, insp_externa,
                    en_vulcaniz, pend_vulcanizado, rech_insp_externa, lib_vulcaniz_de,
                    cliente, n_oc_cliente, peso_neto_ton, peso_unitario_ton, bodega, despachado, rechazo, tipo_posicion, status_comercial
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

            # Update materials master weights (peso_unitario_ton) by iterating the master:
            # for each material, pick the first pedido/pos in Vision (ordered) and
            # use its peso_unitario_ton (tons per piece; from (peso_neto_kg/1000)/solicitado).
            # v0.2: Update material_master (was 'parts')
            con.execute(
                """
                UPDATE material_master
                SET peso_unitario_ton = COALESCE(
                    (
                        SELECT v.peso_unitario_ton
                        FROM sap_vision_snapshot v
                        WHERE v.cod_material = material_master.material
                          AND v.peso_unitario_ton IS NOT NULL
                          AND v.peso_unitario_ton >= 0
                        ORDER BY v.fecha_de_pedido ASC, v.pedido ASC, v.posicion ASC
                        LIMIT 1
                    ),
                    peso_unitario_ton
                )
                WHERE EXISTS (
                    SELECT 1
                    FROM sap_vision_snapshot v2
                    WHERE v2.cod_material = material_master.material
                      AND v2.peso_unitario_ton IS NOT NULL
                      AND v2.peso_unitario_ton >= 0
                )
                """
            )

            # Build progress report for Visión Planta salidas (dispatched/removed orders)
            # (Legacy feature 'Avance' has been removed)

            con.execute("DELETE FROM orders")
            con.execute("DELETE FROM last_program")
            
            # FASE 3.1: Update existing jobs with fecha_entrega from Visión
            self._update_jobs_from_vision(con=con)

    def _update_jobs_from_vision(self, *, con) -> None:
        """Update existing jobs with fecha_entrega from Vision snapshot.
        
        Called automatically after Visión import. Updates fecha_entrega only.
        (qty_total comes from MB52 lote count; lotes disappear from MB52 when completed)
        """
        # Update fecha_entrega for all jobs from Vision
        con.execute(
            """
            UPDATE job
            SET fecha_entrega = (
                    SELECT v.fecha_entrega
                    FROM sap_vision_snapshot v
                    WHERE v.pedido = job.pedido
                      AND v.posicion = job.posicion
                    LIMIT 1
                ),
                updated_at = CURRENT_TIMESTAMP
            WHERE EXISTS (
                SELECT 1
                FROM sap_vision_snapshot v2
                WHERE v2.pedido = job.pedido
                  AND v2.posicion = job.posicion
            )
            """
        )

    def split_job(self, *, job_id: str, qty_split: int) -> tuple[str, str]:
        """Split a job into two jobs.
        
        Args:
            job_id: ID of the job to split
            qty_split: Number of lotes to assign to the first job (original keeps this qty)
            
        Returns:
            Tuple of (original_job_id, new_job_id)
            
        Raises:
            ValueError: If job not found, qty_split invalid, or job has insufficient qty
        """
        from uuid import uuid4
        
        with self.db.connect() as con:
            # Get original job
            original = con.execute(
                """
                SELECT job_id, process_id, pedido, posicion, material, qty_total,
                       priority, is_test, state, fecha_entrega, notes
                FROM job
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
            
            if not original:
                raise ValueError(f"Job {job_id} not found")
            
            original_qty = int(original["qty_total"])
            
            if qty_split <= 0:
                raise ValueError(f"qty_split must be positive, got {qty_split}")
            if qty_split >= original_qty:
                raise ValueError(f"qty_split ({qty_split}) must be less than job qty_total ({original_qty})")
            
            # Calculate new job quantity
            new_qty = original_qty - qty_split
            
            # Create new job with same attributes
            process_id = str(original["process_id"])
            new_job_id = f"job_{process_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"
            
            con.execute(
                """
                INSERT INTO job(
                    job_id, process_id, pedido, posicion, material,
                    qty_total, qty_remaining, priority, is_test, state, fecha_entrega, notes,
                    created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    new_job_id,
                    process_id,
                    original["pedido"],
                    original["posicion"],
                    original["material"],
                    new_qty,
                    new_qty,
                    original["priority"],
                    original["is_test"],
                    original["state"],
                    original["fecha_entrega"],
                    original["notes"],
                ),
            )
            
            # Update original job quantity
            con.execute(
                """
                UPDATE job
                SET qty_total = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ?
                """,
                (qty_split, job_id),
            )
            
            # Get all job_units from original job
            job_units = con.execute(
                """
                SELECT lote, correlativo_int, qty, status
                FROM job_unit
                WHERE job_id = ?
                ORDER BY correlativo_int, lote
                """,
                (job_id,),
            ).fetchall()
            
            # Split job_units: first qty_split stay with original, rest go to new job
            units_to_move = job_units[qty_split:]
            
            for unit in units_to_move:
                # Delete from original job
                con.execute(
                    "DELETE FROM job_unit WHERE job_id = ? AND lote = ?",
                    (job_id, unit["lote"]),
                )
                
                # Create in new job
                new_unit_id = f"ju_{new_job_id}_{uuid4().hex[:8]}"
                con.execute(
                    """
                    INSERT INTO job_unit(
                        job_unit_id, job_id, lote, correlativo_int, qty, status,
                        created_at, updated_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        new_unit_id,
                        new_job_id,
                        unit["lote"],
                        unit["correlativo_int"],
                        unit["qty"],
                        unit["status"],
                    ),
                )
            
            return (job_id, new_job_id)

    def _get_priority_map_values(self) -> dict[str, int]:
        priority_map_str = self.get_config(key="job_priority_map", default='{"prueba": 1, "urgente": 2, "normal": 3}')
        try:
            priority_map = json.loads(priority_map_str) if isinstance(priority_map_str, str) else priority_map_str
        except Exception:
            priority_map = {"prueba": 1, "urgente": 2, "normal": 3}
        return {k: int(v) for k, v in priority_map.items()}

    def mark_job_urgent(self, job_id: str) -> None:
        """Mark a job as urgent."""
        with self.db.connect() as con:
            row = con.execute("SELECT is_test FROM job WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                raise ValueError(f"Job not found: {job_id}")
            if row["is_test"]:
                raise ValueError("Cannot change priority of a test job")
                
            priorities = self._get_priority_map_values()
            urgent_prio = priorities.get("urgente", 2)
            
            con.execute("UPDATE job SET priority = ?, updated_at = CURRENT_TIMESTAMP WHERE job_id = ?", (urgent_prio, job_id))

    def unmark_job_urgent(self, job_id: str) -> None:
        """Unmark a job as urgent (return to normal)."""
        with self.db.connect() as con:
            row = con.execute("SELECT is_test FROM job WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                raise ValueError(f"Job not found: {job_id}")
            if row["is_test"]:
                raise ValueError("Cannot change priority of a test job")
                
            priorities = self._get_priority_map_values()
            normal_prio = priorities.get("normal", 3)
            
            con.execute("UPDATE job SET priority = ?, updated_at = CURRENT_TIMESTAMP WHERE job_id = ?", (normal_prio, job_id))

    def rebuild_orders_from_sap(self) -> int:
        """Backwards-compatible Terminaciones rebuild."""
        return self.rebuild_orders_from_sap_for(process="terminaciones")

    def rebuild_orders_from_sap_for(self, *, process: str = "terminaciones") -> int:
        """Build orders table from usable pieces in MB52 + fecha_de_pedido in Vision.

        Returns how many order-rows were created.
        """
        process = self._normalize_process(process)
        centro = (self.get_config(key="sap_centro", default="4000") or "").strip()
        almacen = self._almacen_for_process(process)
        avail_sql = self._mb52_availability_predicate_sql(process=process)
        if not centro:
            raise ValueError("Config faltante: sap_centro")
        if not almacen:
            raise ValueError(f"Config faltante: {self.processes[process]['almacen_key']}")

        with self.db.connect() as con:
            mb_rows = con.execute(
                                f"""
                SELECT material, documento_comercial, posicion_sd, lote
                FROM sap_mb52_snapshot
                WHERE centro = ?
                  AND almacen = ?
                                    AND {avail_sql}
                  AND documento_comercial IS NOT NULL AND TRIM(documento_comercial) <> ''
                  AND posicion_sd IS NOT NULL AND TRIM(posicion_sd) <> ''
                  AND lote IS NOT NULL AND TRIM(lote) <> ''
                                """.strip(),
                (centro, almacen),
            ).fetchall()

            if not mb_rows:
                con.execute("DELETE FROM orders WHERE process = ?", (process,))
                con.execute("DELETE FROM last_program WHERE process = ?", (process,))
                return 0

            # Vision lookup: (pedido,posicion) -> (fecha_pedido_iso, cod_material, cliente)
            vision_rows = con.execute(
                "SELECT pedido, posicion, fecha_de_pedido, cod_material, cliente FROM sap_vision_snapshot"
            ).fetchall()
            vision_by_key: dict[tuple[str, str], tuple[str, str | None, str | None]] = {}
            for r in vision_rows:
                vision_by_key[(str(r[0]).strip(), str(r[1]).strip())] = (
                    str(r[2]).strip(),
                    (str(r[3]).strip() if r[3] is not None else None),
                    (str(r[4]).strip() if r[4] is not None else None)
                )

        # Group pieces by (pedido,posicion,material,is_test)
        # Use the raw lote string for uniqueness so alphanumeric lotes don't collide
        # after digit extraction.
        pieces: dict[tuple[str, str, str, int], set[str]] = {}
        auto_priority_orderpos: set[tuple[str, str]] = set()
        missing_vision: set[tuple[str, str]] = set()
        bad_lotes: list[str] = []
        for r in mb_rows:
            material = str(r[0]).strip()
            pedido = str(r[1]).strip()
            posicion = str(r[2]).strip()
            lote_raw = r[3]
            key = (pedido, posicion)
            if key not in vision_by_key:
                missing_vision.add(key)
                continue
            lote_s = str(lote_raw).strip()
            if not lote_s:
                continue

            # Business rule: alphanumeric lotes in Terminaciones are production tests
            # and must be prioritized.
            is_test = 1 if re.search(r"[A-Za-z]", lote_s) else 0
            if is_test:
                auto_priority_orderpos.add((pedido, posicion))

            try:
                # Validate it contains digits we can use as correlativo bounds.
                _ = self._lote_to_int(lote_s)
            except Exception:
                if len(bad_lotes) < 20:
                    bad_lotes.append(str(lote_raw))
                continue

            pieces.setdefault((pedido, posicion, material, is_test), set()).add(lote_s)

        if bad_lotes:
            raise ValueError(
                "Hay lotes no numéricos o inválidos (ejemplos: " + ", ".join(bad_lotes[:20]) + ")."
            )
        # If some pedido/posición are missing in Visión, we still rebuild orders
        # for the subset that matches (missing ones remain visible via diagnostics).

        # Validate: each (pedido,posicion) maps to only one material
        material_by_orderpos: dict[tuple[str, str], set[str]] = {}
        for pedido, posicion, material, _is_test in pieces.keys():
            material_by_orderpos.setdefault((pedido, posicion), set()).add(material)
        multi = [(k, sorted(v)) for k, v in material_by_orderpos.items() if len(v) > 1]
        if multi:
            k, mats = multi[0]
            raise ValueError(f"Pedido/posición {k[0]}/{k[1]} tiene múltiples materiales: {mats}")

        # Build one order row per (pedido,posicion,material), using cantidad from stock.
        # Keep correlativos as min/max only (not used for planning UI at this stage).
        order_rows: list[tuple] = []
        for (pedido, posicion, material, is_test), lotes in pieces.items():
            fecha_pedido_iso, _, cliente = vision_by_key[(pedido, posicion)]
            cantidad = int(len(lotes))
            lote_ints = [self._lote_to_int(ls) for ls in lotes]
            corr_inicio = int(min(lote_ints))
            corr_fin = int(max(lote_ints))
            order_rows.append((pedido, posicion, material, cantidad, fecha_pedido_iso, corr_inicio, corr_fin, None, int(is_test), cliente))

        # Deterministic order
        order_rows.sort(key=lambda t: (t[4], t[0], t[1], -int(t[8] or 0), t[2]))

        with self.db.connect() as con:
            con.execute("DELETE FROM orders WHERE process = ?", (process,))
            con.executemany(
                """
                INSERT INTO orders(process, almacen, pedido, posicion, material, cantidad, fecha_entrega, primer_correlativo, ultimo_correlativo, tiempo_proceso_min, is_test, cliente)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [(process, almacen, *row) for row in order_rows],
            )

            if auto_priority_orderpos:
                con.executemany(
                    """
                    INSERT INTO orderpos_priority(pedido, posicion, is_priority, kind)
                    VALUES(?, ?, 1, 'test')
                    ON CONFLICT(pedido, posicion) DO UPDATE SET is_priority=1, kind='test'
                    """,
                    sorted(list(auto_priority_orderpos)),
                )

            # --- V0.2 Job Sync ---
            # Sync orders -> job table (preserving job_id and prio for existing, deleting obsolete)
            existing_jobs = con.execute("SELECT job_id, pedido, posicion, is_test FROM job WHERE process_id = ?", (process,)).fetchall()
            existing_map = {(r["pedido"], r["posicion"], int(r["is_test"])): r["job_id"] for r in existing_jobs}
            seen_existing_ids = set()

            prio_vals = self._get_priority_map_values()
            def_prio = prio_vals.get("normal", 3)

            for row in order_rows:
                # row: (pedido, posicion, material, cantidad, fecha_pedido_iso, corr_inicio, corr_fin, tpm, is_test, cliente)
                # Note: row[8] is is_test, row[9] is cliente
                key = (row[0], row[1], int(row[8]))
                
                if key in existing_map:
                    jid = existing_map[key]
                    seen_existing_ids.add(jid)
                    con.execute(
                        "UPDATE job SET qty_total=?, qty_remaining=?, material=?, fecha_entrega=?, corr_min=?, corr_max=?, cliente=?, updated_at=CURRENT_TIMESTAMP WHERE job_id=?",
                        (int(row[3]), int(row[3]), str(row[2]), str(row[4]), int(row[5]), int(row[6]), str(row[9]) if row[9] else None, jid)
                    )
                else:
                    new_jid = str(uuid4())
                    con.execute(
                        "INSERT INTO job(job_id, process_id, pedido, posicion, material, qty_total, qty_remaining, priority, is_test, state, fecha_entrega, corr_min, corr_max, cliente) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
                        (new_jid, process, str(row[0]), str(row[1]), str(row[2]), int(row[3]), int(row[3]), def_prio, int(row[8]), str(row[4]), int(row[5]), int(row[6]), str(row[9]) if row[9] else None)
                    )

            # Delete jobs not in current MB52 snapshot
            to_del = [jid for jid in existing_map.values() if jid not in seen_existing_ids]
            if to_del:
                # SQLite limit is usually 999 vars
                chunk_s = 900
                for i in range(0, len(to_del), chunk_s):
                    chunk = to_del[i:i+chunk_s]
                    qs = ",".join("?" * len(chunk))
                    con.execute(f"DELETE FROM job WHERE job_id IN ({qs})", chunk)

            con.execute("DELETE FROM last_program WHERE process = ?", (process,))

        return len(order_rows)

    def try_rebuild_orders_from_sap(self) -> bool:
        """Attempt to rebuild orders; returns False if missing prerequisites."""
        return self.try_rebuild_orders_from_sap_for(process="terminaciones")

    def try_rebuild_orders_from_sap_for(self, *, process: str = "terminaciones") -> bool:
        process = self._normalize_process(process)
        if self.count_sap_mb52() == 0 or self.count_sap_vision() == 0:
            return False
        centro = (self.get_config(key="sap_centro", default="4000") or "").strip()
        almacen = self._almacen_for_process(process)
        if not centro or not almacen:
            return False
        return self.rebuild_orders_from_sap_for(process=process) > 0

    @staticmethod
    def _validate_columns(columns, required: set[str]) -> None:
        cols = {str(c).strip() for c in columns}
        missing = sorted(required - cols)
        if missing:
            raise ValueError(f"Faltan columnas: {missing}. Columnas detectadas: {sorted(cols)}")

    # ---------- Models ----------
    def get_orders_model(self, *, process: str = "terminaciones") -> list[Order]:
        process = self._normalize_process(process)
        with self.db.connect() as con:
            rows = con.execute(
                "SELECT pedido, posicion, material, cantidad, fecha_entrega, primer_correlativo, ultimo_correlativo, tiempo_proceso_min, is_test, cliente FROM orders WHERE process = ?",
                (process,),
            ).fetchall()
        out: list[Order] = []
        for pedido, posicion, material, cantidad, fecha_entrega, primer, ultimo, tpm, is_test, cliente in rows:
            out.append(
                Order(
                    pedido=str(pedido),
                    posicion=str(posicion),
                    material=str(material),
                    cantidad=int(cantidad),
                    fecha_entrega=date.fromisoformat(str(fecha_entrega)),
                    primer_correlativo=int(primer),
                    ultimo_correlativo=int(ultimo),
                    tiempo_proceso_min=float(tpm) if tpm is not None else None,
                    is_test=bool(int(is_test or 0)),
                    cliente=str(cliente) if cliente else None,
                )
            )
        return out

    def get_jobs_model(self, *, process: str = "terminaciones") -> list[Job]:
        process = self._normalize_process(process)
        with self.db.connect() as con:
            rows = con.execute(
                "SELECT job_id, pedido, posicion, material, qty_total, priority, fecha_entrega, is_test, notes, corr_min, corr_max, cliente FROM job WHERE process_id = ?",
                (process,),
            ).fetchall()
        
        out: list[Job] = []
        for r in rows:
            out.append(
                Job(
                    job_id=r["job_id"],
                    pedido=r["pedido"],
                    posicion=r["posicion"],
                    material=r["material"],
                    qty_total=r["qty_total"],
                    priority=r["priority"],
                    fecha_entrega=date.fromisoformat(r["fecha_entrega"]) if r["fecha_entrega"] else None,
                    is_test=bool(r["is_test"]),
                    notes=r["notes"],
                    corr_min=r["corr_min"],
                    corr_max=r["corr_max"],
                    cliente=r["cliente"]
                )
            )
        return out

    def get_parts_model(self) -> list[Part]:
        with self.db.connect() as con:
            rows = con.execute(
                "SELECT material, family_id, vulcanizado_dias, mecanizado_dias, inspeccion_externa_dias, peso_unitario_ton, mec_perf_inclinada, sobre_medida_mecanizado FROM material_master"
            ).fetchall()
        return [
            Part(
                material=str(r[0]),
                family_id=str(r[1]),
                vulcanizado_dias=r[2],
                mecanizado_dias=r[3],
                inspeccion_externa_dias=r[4],
                peso_unitario_ton=(float(r[5]) if r[5] is not None else None),
                mec_perf_inclinada=bool(int(r[6] or 0)),
                sobre_medida_mecanizado=bool(int(r[7] or 0)),
            )
            for r in rows
        ]

    # ---------- Program persistence ----------
    @staticmethod
    def _row_key_from_program_row(row: dict) -> tuple[str, str, int] | None:
        try:
            pedido = str(row.get("pedido") or "").strip()
            posicion = str(row.get("posicion") or "").strip()
            if not pedido or not posicion:
                return None
            # Tests are encoded as prio_kind='test' in the scheduling output.
            prio_kind = str(row.get("prio_kind") or "").strip().lower()
            is_test = 1 if prio_kind == "test" or int(row.get("is_test") or 0) == 1 else 0
            return (pedido, posicion, int(is_test))
        except Exception:
            return None

    @staticmethod
    def _order_key(*, pedido: str, posicion: str, is_test: int) -> tuple[str, str, int]:
        return (str(pedido).strip(), str(posicion).strip(), int(is_test or 0))

    def list_in_progress_locks(self, *, process: str = "terminaciones") -> list[dict]:
        """Rows pinned to a given line, ordered by marked_at.

        Split-aware: returns one row per split_id.
        """
        process = self._normalize_process(process)
        with self.db.connect() as con:
            try:
                rows = con.execute(
                    "SELECT process, pedido, posicion, is_test, split_id, line_id, qty, marked_at FROM program_in_progress_item WHERE process=? ORDER BY marked_at ASC",
                    (process,),
                ).fetchall()
                return [
                    {
                        "process": str(r[0]),
                        "pedido": str(r[1]),
                        "posicion": str(r[2]),
                        "is_test": int(r[3] or 0),
                        "split_id": int(r[4] or 1),
                        "line_id": int(r[5]),
                        "qty": int(r[6] or 0),
                        "marked_at": str(r[7]),
                    }
                    for r in rows
                ]
            except Exception:
                # Backward-compatible fallback (older DBs).
                rows = con.execute(
                    "SELECT process, pedido, posicion, is_test, line_id, marked_at FROM program_in_progress WHERE process=? ORDER BY marked_at ASC",
                    (process,),
                ).fetchall()
        return [
            {
                "process": str(r[0]),
                "pedido": str(r[1]),
                "posicion": str(r[2]),
                "is_test": int(r[3] or 0),
                "split_id": 1,
                "line_id": int(r[4]),
                "qty": 0,
                "marked_at": str(r[5]),
            }
            for r in rows
        ]

    def _refresh_program_with_locks(self, process: str) -> None:
        """Update last_program in-place with current locks, avoiding full regen."""
        last = self.load_last_program(process=process)

        if last is None:
            # No cache to update; delete to ensure next load generates fresh
            with self.db.connect() as con:
                con.execute("DELETE FROM last_program WHERE process = ?", (process,))
            return

        program = last["program"]
        errors = last.get("errors") or []
        
        # Re-apply locks. This respects the current DB state of locks (added/removed/moved).
        # It removes locked items from their old positions and inserts them intotheir new locked positions.
        new_prog, new_errors = self._apply_in_progress_locks(process=process, program=program, errors=errors)
        
        now = datetime.now().isoformat(timespec="seconds")
        payload = json.dumps({"program": new_prog, "errors": new_errors})
        
        with self.db.connect() as con:
            con.execute(
                "INSERT INTO last_program(process, program_json, generated_on) VALUES(?, ?, ?) "
                "ON CONFLICT(process) DO UPDATE SET program_json=excluded.program_json, generated_on=excluded.generated_on",
                (process, payload, now)
            )

    def mark_in_progress(
        self,
        *,
        process: str = "terminaciones",
        pedido: str,
        posicion: str,
        is_test: int = 0,
        line_id: int,
    ) -> None:
        process = self._normalize_process(process)
        pedido_s = str(pedido).strip()
        posicion_s = str(posicion).strip()
        is_test_i = int(is_test or 0)
        if not pedido_s or not posicion_s:
            raise ValueError("Pedido/posición inválidos")
        marked_at = datetime.now().isoformat(timespec="seconds")
        
        with self.db.connect() as con:
            # Split-aware default: create/update split_id=1 with qty=0 (auto).
            try:
                con.execute(
                    "INSERT INTO program_in_progress_item(process, pedido, posicion, is_test, split_id, line_id, qty, marked_at) VALUES(?, ?, ?, ?, 1, ?, 0, ?) "
                    "ON CONFLICT(process, pedido, posicion, is_test, split_id) DO UPDATE SET "
                    "line_id=excluded.line_id, marked_at=program_in_progress_item.marked_at",
                    (process, pedido_s, posicion_s, is_test_i, int(line_id), marked_at),
                )
            except Exception:
                # Backward-compatible fallback.
                con.execute(
                    "INSERT INTO program_in_progress(process, pedido, posicion, is_test, line_id, marked_at) VALUES(?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(process, pedido, posicion, is_test) DO UPDATE SET "
                    "line_id=excluded.line_id, marked_at=program_in_progress.marked_at",
                    (process, pedido_s, posicion_s, is_test_i, int(line_id), marked_at),
                )
            
        self.log_audit(
            "PROGRAM_UPDATE",
            "Mark In-Progress",
            f"Pedido {pedido_s}/{posicion_s} -> Line {line_id} (Test: {is_test_i})"
        )

        # Update cache in-place (fast) instead of invalidating (slow)
        self._refresh_program_with_locks(process=process)

    def unmark_in_progress(
        self,
        *,
        process: str = "terminaciones",
        pedido: str,
        posicion: str,
        is_test: int = 0,
    ) -> None:
        process = self._normalize_process(process)
        with self.db.connect() as con:
            pedido_s = str(pedido).strip()
            posicion_s = str(posicion).strip()
            is_test_i = int(is_test or 0)
            # Split-aware delete (all split_id rows).
            try:
                con.execute(
                    "DELETE FROM program_in_progress_item WHERE process=? AND pedido=? AND posicion=? AND is_test=?",
                    (process, pedido_s, posicion_s, is_test_i),
                )
            except Exception:
                pass

            # Legacy cleanup.
            try:
                con.execute(
                    "DELETE FROM program_in_progress WHERE process=? AND pedido=? AND posicion=? AND is_test=?",
                    (process, pedido_s, posicion_s, is_test_i),
                )
            except Exception:
                pass
            
        self.log_audit(
            "PROGRAM_UPDATE",
            "Unmark In-Progress",
            f"Pedido {pedido_s}/{posicion_s} (Test: {is_test_i})"
        )

        # Update cache in-place (fast) instead of invalidating (slow)
        self._refresh_program_with_locks(process=process)

    def move_in_progress(
        self,
        *,
        process: str = "terminaciones",
        pedido: str,
        posicion: str,
        is_test: int = 0,
        line_id: int,
        split_id: int | None = None,
    ) -> None:
        """Move an in-progress lock to another line.

        If split_id is None, move all splits for the order position.
        """
        process = self._normalize_process(process)
        pedido_s = str(pedido).strip()
        posicion_s = str(posicion).strip()
        is_test_i = int(is_test or 0)
        if not pedido_s or not posicion_s:
            raise ValueError("Pedido/posición inválidos")

        allow = str(self.get_config(key="ui_allow_move_in_progress_line", default="0")).strip()
        if allow != "1":
            raise ValueError("Movimiento manual deshabilitado por configuración (ui_allow_move_in_progress_line)")

        audit_target = None
        audit_details = None

        with self.db.connect() as con:
            try:
                if split_id is None:
                    con.execute(
                        "UPDATE program_in_progress_item SET line_id=? WHERE process=? AND pedido=? AND posicion=? AND is_test=?",
                        (int(line_id), process, pedido_s, posicion_s, is_test_i),
                    )
                else:
                    con.execute(
                        "UPDATE program_in_progress_item SET line_id=? WHERE process=? AND pedido=? AND posicion=? AND is_test=? AND split_id=?",
                        (int(line_id), process, pedido_s, posicion_s, is_test_i, int(split_id)),
                    )
                
                audit_target = "Move Line"
                audit_details = f"Pedido {pedido_s}/{posicion_s} -> Line {line_id} (Split: {split_id or 'ALL'})"
                
            except Exception:
                # Backward-compatible fallback.
                con.execute(
                    "UPDATE program_in_progress SET line_id=? WHERE process=? AND pedido=? AND posicion=? AND is_test=?",
                    (int(line_id), process, pedido_s, posicion_s, is_test_i),
                )
                
                audit_target = "Move Line (Legacy)"
                audit_details = f"Pedido {pedido_s}/{posicion_s} -> Line {line_id}"
        
        if audit_target:
            self.log_audit("PROGRAM_UPDATE", audit_target, audit_details)

        # Outside transaction
        self._refresh_program_with_locks(process=process)

    def create_balanced_split(
        self,
        *,
        process: str = "terminaciones",
        pedido: str,
        posicion: str,
        is_test: int = 0,
    ) -> None:
        """Split an in-progress order position into two balanced parts.

        The split allocates quantities and correlativos sequentially during program merge.
        This method only persists the split (split_id + qty); line movement is handled
        separately (UI can move one split to another line).
        """
        process = self._normalize_process(process)
        pedido_s = str(pedido).strip()
        posicion_s = str(posicion).strip()
        is_test_i = int(is_test or 0)
        if not pedido_s or not posicion_s:
            raise ValueError("Pedido/posición inválidos")

        # Current truth from MB52-derived orders.
        order = None
        for o in self.get_orders_model(process=process):
            if o.pedido == pedido_s and o.posicion == posicion_s and (1 if bool(getattr(o, "is_test", False)) else 0) == is_test_i:
                order = o
                break
        if order is None:
            raise ValueError("No se encontró la orden en SAP (orders)")
        qty_total = int(order.cantidad)
        if qty_total < 2:
            raise ValueError("No se puede dividir: cantidad < 2")

        qty1 = qty_total // 2
        qty2 = qty_total - qty1
        if qty1 <= 0 or qty2 <= 0:
            raise ValueError("Split inválido")

        now = datetime.now().isoformat(timespec="seconds")
        with self.db.connect() as con:
            try:
                # Ensure there is at least split_id=1 (carry its line_id/marked_at).
                row = con.execute(
                    "SELECT line_id, marked_at FROM program_in_progress_item WHERE process=? AND pedido=? AND posicion=? AND is_test=? AND split_id=1",
                    (process, pedido_s, posicion_s, is_test_i),
                ).fetchone()
                if row is None:
                    # If not marked, default to line 1 (UI normally marks first).
                    con.execute(
                        "INSERT OR IGNORE INTO program_in_progress_item(process, pedido, posicion, is_test, split_id, line_id, qty, marked_at) VALUES(?, ?, ?, ?, 1, 1, 0, ?)",
                        (process, pedido_s, posicion_s, is_test_i, now),
                    )
                    row = con.execute(
                        "SELECT line_id, marked_at FROM program_in_progress_item WHERE process=? AND pedido=? AND posicion=? AND is_test=? AND split_id=1",
                        (process, pedido_s, posicion_s, is_test_i),
                    ).fetchone()

                line_id = int(row[0])

                existing = con.execute(
                    "SELECT COUNT(*) FROM program_in_progress_item WHERE process=? AND pedido=? AND posicion=? AND is_test=?",
                    (process, pedido_s, posicion_s, is_test_i),
                ).fetchone()
                if int(existing[0] or 0) != 1:
                    raise ValueError("Ya existe un split (o múltiples partes) para esta fila")

                con.execute(
                    "UPDATE program_in_progress_item SET qty=? WHERE process=? AND pedido=? AND posicion=? AND is_test=? AND split_id=1",
                    (int(qty1), process, pedido_s, posicion_s, is_test_i),
                )
                con.execute(
                    "INSERT INTO program_in_progress_item(process, pedido, posicion, is_test, split_id, line_id, qty, marked_at) VALUES(?, ?, ?, ?, 2, ?, ?, ?)",
                    (process, pedido_s, posicion_s, is_test_i, int(line_id), int(qty2), now),
                )
            except Exception:
                # If split table isn't available, we cannot support splits.
                raise
        
        self.log_audit(
            "PROGRAM_UPDATE",
            "Split Created",
            f"Pedido {pedido_s}/{posicion_s} -> Sizes {qty1}, {qty2}"
        )
        
        # Outside transaction
        self._refresh_program_with_locks(process=process)

    def _apply_in_progress_locks(
        self,
        *,
        process: str,
        program: dict,
        errors: list[dict] | None = None,
    ) -> tuple[dict, list[dict]]:
        """Pin locked rows to their line, keep them at the top by marked_at.

        - Locked rows update quantity and correlativo range from current `orders`.
        - If a locked row disappears from MB52 (i.e. no longer in `orders`), we delete the lock
          and remove it from the program.
        """

        process = self._normalize_process(process)
        program_in = program or {}
        errors_in = list(errors or [])

        # Shallow-copy program/rows so we can mutate safely.
        out: dict = {k: [dict(r) for r in (v or [])] for k, v in dict(program_in).items()}

        # Map of existing rows by (pedido,posicion,is_test) so we can reuse scheduler-computed fields.
        template_by_key: dict[tuple[str, str, int], dict] = {}
        for items in out.values():
            for r in items:
                # Reset visual flag; it will be re-set to 1 if the item matches a current lock.
                r["in_progress"] = 0
                key = self._row_key_from_program_row(r)
                if key is not None and key not in template_by_key:
                    template_by_key[key] = dict(r)

        # Current truth from MB52-derived orders.
        order_by_key: dict[tuple[str, str, int], Order] = {}
        for o in self.get_orders_model(process=process):
            order_by_key[self._order_key(pedido=o.pedido, posicion=o.posicion, is_test=1 if bool(getattr(o, "is_test", False)) else 0)] = o

        manual_set = self.get_manual_priority_orderpos_set()

        def _prio_kind_for(order: Order) -> str:
            if bool(getattr(order, "is_test", False)):
                return "test"
            if (order.pedido, order.posicion) in set(manual_set or set()):
                return "priority"
            return "normal"

        def _find_program_line_key(line_id: int):
            # Program keys might be ints (fresh) or strings (loaded from JSON).
            for k in out.keys():
                try:
                    if int(k) == int(line_id):
                        return k
                except Exception:
                    continue
            return int(line_id)

        locks = self.list_in_progress_locks(process=process)
        locked_keys_present: list[tuple[str, str, int]] = []
        locked_rows_by_line: dict[object, list[dict]] = {}

        def _remove_key_everywhere(key_to_remove: tuple[str, str, int]) -> None:
            for line_k in list(out.keys()):
                out[line_k] = [
                    r
                    for r in (out.get(line_k, []) or [])
                    if (self._row_key_from_program_row(r) != key_to_remove)
                ]

        # Group locks by (pedido,posicion,is_test) so we can expand splits.
        locks_by_key: dict[tuple[str, str, int], list[dict]] = {}
        for lk in locks:
            key = self._order_key(pedido=lk["pedido"], posicion=lk["posicion"], is_test=int(lk.get("is_test") or 0))
            locks_by_key.setdefault(key, []).append(dict(lk))

        for key, group in locks_by_key.items():
            o = order_by_key.get(key)
            if o is None:
                # Lock no longer valid: remove it and remove any stale row from the program.
                try:
                    self.unmark_in_progress(process=process, pedido=key[0], posicion=key[1], is_test=key[2])
                except Exception:
                    pass
                _remove_key_everywhere(key)
                continue

            locked_keys_present.append(key)

            base_template = template_by_key.get(key)
            if base_template is None:
                base_template = {
                    "pedido": o.pedido,
                    "posicion": o.posicion,
                    "material": o.material,
                    "family_id": "Otros",
                    "fecha_entrega": o.fecha_entrega.isoformat(),
                    "start_by": o.fecha_entrega.isoformat(),
                    "prio_kind": _prio_kind_for(o),
                }

            # Expand split items in a stable order (marked_at, then split_id).
            items = sorted(group, key=lambda d: (str(d.get("marked_at") or ""), int(d.get("split_id") or 1)))
            total_qty = int(o.cantidad)
            start_corr = int(o.primer_correlativo)

            stored_qtys: list[int] = []
            for it in items:
                q = int(it.get("qty") or 0)
                stored_qtys.append(max(0, q))

            # Decide effective qty per split.
            effective_qtys = list(stored_qtys)
            if len(effective_qtys) >= 1 and any(q <= 0 for q in effective_qtys):
                # Legacy/auto semantics: if qty=0, treat as "take remaining" on the last item.
                pass

            # First, treat qty=0 as unspecified; we'll allocate later.
            # specified = [q for q in effective_qtys[:-1] if q > 0]

            # Start with explicit qty for all but last; last absorbs remaining.
            remaining = total_qty
            for i in range(len(items)):
                is_last = i == (len(items) - 1)
                q_stored = int(stored_qtys[i] or 0)
                if is_last:
                    q_eff = max(0, remaining)
                else:
                    q_eff = max(0, min(q_stored, remaining)) if q_stored > 0 else 0
                effective_qtys[i] = q_eff
                remaining -= q_eff

            # If we still have remaining > 0, distribute to the last split.
            if remaining > 0 and effective_qtys:
                effective_qtys[-1] += remaining
                remaining = 0

            # If remaining < 0 (order shrank), reduce from the end backwards.
            if remaining < 0 and effective_qtys:
                excess = -remaining
                for i in range(len(effective_qtys) - 1, -1, -1):
                    if excess <= 0:
                        break
                    take = min(effective_qtys[i], excess)
                    effective_qtys[i] -= take
                    excess -= take
                remaining = 0

            corr_cursor = start_corr
            for it, q_eff in zip(items, effective_qtys):
                split_id = int(it.get("split_id") or 1)
                line_key = _find_program_line_key(int(it["line_id"]))

                row = dict(base_template)
                row["pedido"] = o.pedido
                row["posicion"] = o.posicion
                row["material"] = o.material
                row["cantidad"] = int(q_eff)
                row["prio_kind"] = _prio_kind_for(o)
                row["is_test"] = 1 if bool(getattr(o, "is_test", False)) else 0
                row["in_progress"] = 1
                row["_pt_split_id"] = int(split_id)

                if q_eff > 0:
                    row["corr_inicio"] = int(corr_cursor)
                    row["corr_fin"] = int(corr_cursor + q_eff - 1)
                    corr_cursor += q_eff
                else:
                    row["corr_inicio"] = int(o.primer_correlativo)
                    row["corr_fin"] = int(o.primer_correlativo)

                # Unique per-split row id.
                row["_row_id"] = (
                    f"{o.pedido}|{o.posicion}|{o.material}|split{split_id}|{int(row['corr_inicio'])}-{int(row['corr_fin'])}"
                )

                locked_rows_by_line.setdefault(line_key, []).append(row)

        # Remove any occurrences of locked rows from all lines (they will be re-inserted pinned).
        locked_key_set = set(locked_keys_present)
        if locked_key_set:
            for line_k in list(out.keys()):
                filtered: list[dict] = []
                for r in out.get(line_k, []) or []:
                    k = self._row_key_from_program_row(r)
                    if k is None or k not in locked_key_set:
                        filtered.append(r)
                out[line_k] = filtered

            # Also remove from errors if present there.
            if errors_in:
                errors_in = [e for e in errors_in if (self._row_key_from_program_row(e) not in locked_key_set)]

        # Prepend locked rows per line.
        for line_k, locked_rows in locked_rows_by_line.items():
            existing = list(out.get(line_k, []) or [])
            out[line_k] = list(locked_rows) + existing

        return out, errors_in

    def save_last_program(self, *, process: str = "terminaciones", program: dict[int, list[dict]], errors: list[dict] | None = None) -> None:
        process = self._normalize_process(process)
        merged_program, merged_errors = self._apply_in_progress_locks(process=process, program=program, errors=list(errors or []))
        payload = json.dumps({"program": merged_program, "errors": list(merged_errors or [])})
        generated_on = datetime.now().isoformat(timespec="seconds")
        with self.db.connect() as con:
            con.execute(
                "INSERT INTO last_program(process, generated_on, program_json) VALUES(?, ?, ?) "
                "ON CONFLICT(process) DO UPDATE SET generated_on=excluded.generated_on, program_json=excluded.program_json",
                (process, generated_on, payload),
            )
        
        # Audit log
        total_items = sum(len(lines) for lines in merged_program.values())
        err_items = len(merged_errors or [])
        self.log_audit(
            "PROGRAM_GEN",
            "Program Saved",
            f"Process: {process}, Scheduled: {total_items}, Errors: {err_items}"
        )

    def load_last_program(self, *, process: str = "terminaciones") -> dict | None:
        process = self._normalize_process(process)
        with self.db.connect() as con:
            row = con.execute("SELECT generated_on, program_json FROM last_program WHERE process=?", (process,)).fetchone()
        if row is None:
            return None
        payload = json.loads(row["program_json"])
        if isinstance(payload, dict) and "program" in payload:
            merged_program, merged_errors = self._apply_in_progress_locks(
                process=process,
                program=payload.get("program") or {},
                errors=list(payload.get("errors") or []),
            )
            return {"generated_on": row["generated_on"], "program": merged_program, "errors": merged_errors}
        # Backward-compatible: older DBs stored only the program dict
        merged_program, merged_errors = self._apply_in_progress_locks(process=process, program=payload, errors=[])
        return {"generated_on": row["generated_on"], "program": merged_program, "errors": merged_errors}






