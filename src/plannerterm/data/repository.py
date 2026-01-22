from __future__ import annotations

import json
import re
from datetime import date, datetime

from plannerterm.core.models import Line, Order, Part
from plannerterm.data.db import Db
from plannerterm.data.excel_io import coerce_date, coerce_float, normalize_columns, parse_int_strict, read_excel_bytes, to_int01


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
                                        FROM sap_mb52
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
                                        FROM sap_mb52
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
                                        FROM sap_mb52 m
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
                        FROM sap_mb52
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
                                                FROM sap_mb52 m
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
                FROM sap_mb52
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
                FROM sap_mb52 m
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
            row = con.execute("SELECT value FROM app_config WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        return str(row[0])

    def set_config(self, *, key: str, value: str) -> None:
        key = str(key).strip()
        if not key:
            raise ValueError("config key vacío")
        with self.db.connect() as con:
            con.execute(
                "INSERT INTO app_config(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value).strip()),
            )
            # Warehouse/filters affect derived orders and programs.
            con.execute("DELETE FROM orders")
            con.execute("DELETE FROM last_program")

    # ---------- Families catalog ----------
    def list_families(self) -> list[str]:
        with self.db.connect() as con:
            rows = con.execute("SELECT name FROM families ORDER BY name").fetchall()
        return [str(r[0]) for r in rows]

    def get_families_rows(self) -> list[dict]:
        """Rows for UI: family name + how many parts are assigned to it."""
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT f.name AS familia, COUNT(p.numero_parte) AS parts_count
                FROM families f
                LEFT JOIN parts p ON p.familia = f.name
                GROUP BY f.name
                ORDER BY f.name
                """
            ).fetchall()
        return [{"familia": str(r["familia"]), "parts_count": int(r["parts_count"])} for r in rows]

    def add_family(self, *, name: str) -> None:
        name = str(name).strip()
        if not name:
            raise ValueError("nombre de familia vacío")
        with self.db.connect() as con:
            con.execute("INSERT OR IGNORE INTO families(name) VALUES(?)", (name,))

    def rename_family(self, *, old: str, new: str) -> None:
        old = str(old).strip()
        new = str(new).strip()
        if not old or not new:
            raise ValueError("familia inválida")
        with self.db.connect() as con:
            # Ensure new exists
            con.execute("INSERT OR IGNORE INTO families(name) VALUES(?)", (new,))
            # Update parts mappings
            con.execute("UPDATE parts SET familia = ? WHERE familia = ?", (new, old))

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
            con.execute("DELETE FROM families WHERE name = ?", (old,))

            # Invalidate any previously generated program
            con.execute("DELETE FROM last_program")

    def delete_family(self, *, name: str, force: bool = False) -> None:
        name = str(name).strip()
        if not name:
            raise ValueError("familia inválida")
        with self.db.connect() as con:
            in_use = int(con.execute("SELECT COUNT(*) FROM parts WHERE familia = ?", (name,)).fetchone()[0])
            if in_use and force:
                # Keep mappings: move affected parts to 'Otros'
                con.execute("INSERT OR IGNORE INTO families(name) VALUES('Otros')")
                con.execute("UPDATE parts SET familia='Otros' WHERE familia = ?", (name,))
            elif in_use and not force:
                # Default behavior: remove mappings so affected parts become "missing" and must be reassigned.
                con.execute("DELETE FROM parts WHERE familia = ?", (name,))

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

            con.execute("DELETE FROM families WHERE name = ?", (name,))

            # Invalidate any previously generated program
            con.execute("DELETE FROM last_program")

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

    def delete_line(self, *, process: str = "terminaciones", line_id: int) -> None:
        process = self._normalize_process(process)
        with self.db.connect() as con:
            con.execute("DELETE FROM line_config WHERE process = ? AND line_id = ?", (process, int(line_id)))
            con.execute("DELETE FROM last_program WHERE process = ?", (process,))

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
        return [Line(line_id=r["line_id"], allowed_families=set(r["families"])) for r in self.get_lines(process=process)]

    def upsert_part(self, *, numero_parte: str, familia: str) -> None:
        numero_parte = str(numero_parte).strip()
        familia = str(familia).strip()
        if not numero_parte:
            raise ValueError("numero_parte vacío")
        if not familia:
            raise ValueError("familia vacía")
        # Ensure family exists in catalog
        self.add_family(name=familia)
        with self.db.connect() as con:
            con.execute(
                "INSERT INTO parts(numero_parte, familia) VALUES(?, ?) "
                "ON CONFLICT(numero_parte) DO UPDATE SET familia=excluded.familia",
                (numero_parte, familia),
            )

            # Invalidate any previously generated program
            con.execute("DELETE FROM last_program")

    def upsert_part_master(
        self,
        *,
        numero_parte: str,
        familia: str,
        vulcanizado_dias: int | None = None,
        mecanizado_dias: int | None = None,
        inspeccion_externa_dias: int | None = None,
        peso_ton: float | None = None,
        mec_perf_inclinada: bool = False,
        sobre_medida: bool = False,
    ) -> None:
        """Upsert a part master row including family and optional process times."""
        numero_parte = str(numero_parte).strip()
        familia = str(familia).strip()
        if not numero_parte:
            raise ValueError("numero_parte vacío")
        if not familia:
            raise ValueError("familia vacía")

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

        pt: float | None = None
        if peso_ton is not None:
            pt = float(peso_ton)
            if pt < 0:
                raise ValueError("peso_ton no puede ser negativo")

        mec_perf = 1 if bool(mec_perf_inclinada) else 0
        sm = 1 if bool(sobre_medida) else 0

        # Ensure family exists in catalog
        self.add_family(name=familia)

        with self.db.connect() as con:
            con.execute(
                "INSERT INTO parts(numero_parte, familia, vulcanizado_dias, mecanizado_dias, inspeccion_externa_dias, peso_ton, mec_perf_inclinada, sobre_medida) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(numero_parte) DO UPDATE SET "
                "familia=excluded.familia, "
                "vulcanizado_dias=excluded.vulcanizado_dias, "
                "mecanizado_dias=excluded.mecanizado_dias, "
                "inspeccion_externa_dias=excluded.inspeccion_externa_dias, "
                "peso_ton=excluded.peso_ton, "
                "mec_perf_inclinada=excluded.mec_perf_inclinada, "
                "sobre_medida=excluded.sobre_medida",
                (numero_parte, familia, v, m, i, pt, mec_perf, sm),
            )

            # Invalidate any previously generated program
            con.execute("DELETE FROM last_program")

    def update_part_process_times(
        self,
        *,
        numero_parte: str,
        vulcanizado_dias: int,
        mecanizado_dias: int,
        inspeccion_externa_dias: int,
    ) -> None:
        numero_parte = str(numero_parte).strip()
        if not numero_parte:
            raise ValueError("numero_parte vacío")
        for col_name, value in (
            ("vulcanizado_dias", vulcanizado_dias),
            ("mecanizado_dias", mecanizado_dias),
            ("inspeccion_externa_dias", inspeccion_externa_dias),
        ):
            if int(value) < 0:
                raise ValueError(f"{col_name} no puede ser negativo")

        with self.db.connect() as con:
            exists = con.execute("SELECT 1 FROM parts WHERE numero_parte = ?", (numero_parte,)).fetchone()
            if exists is None:
                raise ValueError(
                    f"No existe maestro para numero_parte={numero_parte}. Asigna familia primero en /familias."
                )
            con.execute(
                """
                UPDATE parts
                SET vulcanizado_dias = ?, mecanizado_dias = ?, inspeccion_externa_dias = ?
                WHERE numero_parte = ?
                """,
                (int(vulcanizado_dias), int(mecanizado_dias), int(inspeccion_externa_dias), numero_parte),
            )

            # Invalidate any previously generated program
            con.execute("DELETE FROM last_program")

    def delete_part(self, *, numero_parte: str) -> None:
        with self.db.connect() as con:
            con.execute("DELETE FROM parts WHERE numero_parte = ?", (str(numero_parte).strip(),))
            con.execute("DELETE FROM last_program")

    def delete_all_parts(self) -> None:
        with self.db.connect() as con:
            con.execute("DELETE FROM parts")
            con.execute("DELETE FROM last_program")

    def get_parts_rows(self) -> list[dict]:
        """Return the part master as UI-friendly dict rows."""
        with self.db.connect() as con:
            rows = con.execute(
                "SELECT numero_parte, familia, vulcanizado_dias, mecanizado_dias, inspeccion_externa_dias, peso_ton, mec_perf_inclinada, sobre_medida FROM parts ORDER BY numero_parte"
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
                WITH orderpos AS (
                    SELECT pedido, posicion, MIN(fecha_entrega) AS fecha_entrega
                    FROM orders
                    GROUP BY pedido, posicion
                ), v AS (
                    SELECT
                        pedido,
                        posicion,
                        MAX(COALESCE(cod_material, '')) AS cod_material,
                        MAX(COALESCE(solicitado, 0)) AS solicitado,
                        MAX(COALESCE(bodega, 0)) AS bodega,
                        MAX(COALESCE(despachado, 0)) AS despachado,
                        MAX(peso_unitario_ton) AS peso_unitario_ton
                    FROM sap_vision
                    GROUP BY pedido, posicion
                ), joined AS (
                    SELECT
                        op.fecha_entrega AS fecha_entrega,
                        CASE
                            WHEN (v.solicitado - v.bodega - v.despachado) < 0 THEN 0
                            ELSE (v.solicitado - v.bodega - v.despachado)
                        END AS pendientes,
                        COALESCE(p.peso_ton, v.peso_unitario_ton, 0.0) AS peso_ton
                    FROM orderpos op
                    LEFT JOIN v
                      ON v.pedido = op.pedido
                     AND v.posicion = op.posicion
                    LEFT JOIN parts p
                      ON p.numero_parte = v.cod_material
                )
                SELECT
                    COALESCE(SUM(pendientes * peso_ton), 0.0) AS tons_por_entregar,
                    COALESCE(SUM(CASE WHEN fecha_entrega < ? THEN (pendientes * peso_ton) ELSE 0.0 END), 0.0) AS tons_atrasadas
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
        """Orders with fecha_entrega < today across all processes."""
        d0 = today or date.today()
        lim = max(1, min(int(limit or 200), 2000))
        with self.db.connect() as con:
            rows = con.execute(
                """
                WITH orderpos AS (
                    SELECT pedido, posicion, MIN(fecha_entrega) AS fecha_entrega
                    FROM orders
                    WHERE fecha_entrega < ?
                    GROUP BY pedido, posicion
                )
                SELECT
                    op.pedido AS pedido,
                    op.posicion AS posicion,
                    COALESCE(v.cod_material, '') AS numero_parte,
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
                        * COALESCE(p.peso_ton, v.peso_unitario_ton, 0.0)
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
                    FROM sap_vision
                    GROUP BY pedido, posicion
                ) v
                  ON v.pedido = op.pedido
                 AND v.posicion = op.posicion
                LEFT JOIN parts p
                  ON p.numero_parte = v.cod_material
                ORDER BY op.fecha_entrega ASC, op.pedido, op.posicion
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
                    "numero_parte": str(r["numero_parte"]),
                    "solicitado": int(r["solicitado"] or 0),
                    "pendientes": int(r["pendientes"] or 0),
                    "fecha_entrega": fe.isoformat(),
                    "dias": int(atraso),
                    "cliente": str(r["cliente"] or "").strip(),
                    "tons": float(r["tons"] or 0.0),
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
                    SELECT pedido, posicion, MIN(fecha_entrega) AS fecha_entrega
                    FROM orders
                    WHERE fecha_entrega >= ? AND fecha_entrega <= ?
                    GROUP BY pedido, posicion
                )
                SELECT
                    op.pedido AS pedido,
                    op.posicion AS posicion,
                    COALESCE(v.cod_material, '') AS numero_parte,
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
                        * COALESCE(p.peso_ton, v.peso_unitario_ton, 0.0)
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
                    FROM sap_vision
                    GROUP BY pedido, posicion
                ) v
                  ON v.pedido = op.pedido
                 AND v.posicion = op.posicion
                LEFT JOIN parts p
                  ON p.numero_parte = v.cod_material
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
                    "numero_parte": str(r["numero_parte"]),
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
                SELECT DISTINCT o.numero_parte
                FROM orders o
                LEFT JOIN parts p ON p.numero_parte = o.numero_parte
                WHERE o.process = ?
                  AND p.numero_parte IS NULL
                ORDER BY o.numero_parte
                """,
                (process,),
            ).fetchall()
        return [str(r[0]) for r in rows]

    def get_missing_process_times_from_orders(self, *, process: str = "terminaciones") -> list[str]:
        """Distinct numero_parte referenced by orders that has a master row but missing any process time."""
        process = self._normalize_process(process)
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT DISTINCT o.numero_parte
                FROM orders o
                JOIN parts p ON p.numero_parte = o.numero_parte
                WHERE o.process = ?
                  AND (
                       p.vulcanizado_dias IS NULL
                    OR p.mecanizado_dias IS NULL
                    OR p.inspeccion_externa_dias IS NULL
                  )
                ORDER BY o.numero_parte
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
                    SELECT DISTINCT o.numero_parte
                    FROM orders o
                    JOIN parts p ON p.numero_parte = o.numero_parte
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
                    SELECT DISTINCT o.numero_parte
                    FROM orders o
                    LEFT JOIN parts p ON p.numero_parte = o.numero_parte
                    WHERE o.process = ?
                      AND p.numero_parte IS NULL
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
            return int(con.execute("SELECT COUNT(*) FROM sap_mb52").fetchone()[0])

    def count_sap_vision(self) -> int:
        with self.db.connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM sap_vision").fetchone()[0])

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
                    FROM sap_mb52
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
                  MIN(COALESCE(v.fecha_pedido, o.fecha_entrega)) AS fecha_pedido,
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
                ORDER BY COALESCE(opp.is_priority, op.is_priority, 0) DESC, fecha_pedido, o.pedido, o.posicion
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
                    "fecha_pedido": str(r["fecha_pedido"] or ""),
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
                    FROM sap_mb52
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
                SELECT pedido, posicion, numero_parte, cantidad, fecha_entrega, primer_correlativo, ultimo_correlativo
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
                "numero_parte": str(r[2]),
                "cantidad": int(r[3]),
                "fecha_entrega": str(r[4]),
                "primer_correlativo": int(r[5]),
                "ultimo_correlativo": int(r[6]),
            }
            for r in rows
        ]

    def count_parts(self) -> int:
        with self.db.connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM parts").fetchone()[0])

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
                SELECT m.material, COALESCE(MAX(m.texto_breve), '') AS texto_breve
                FROM sap_mb52 m
                LEFT JOIN parts p ON p.numero_parte = m.material
                WHERE m.material IS NOT NULL AND TRIM(m.material) <> ''
                  AND m.centro = ?
                  AND m.almacen = ?
                                    AND {avail_sql.replace('libre_utilizacion', 'm.libre_utilizacion').replace('en_control_calidad', 'm.en_control_calidad')}
                  AND m.documento_comercial IS NOT NULL AND TRIM(m.documento_comercial) <> ''
                  AND m.posicion_sd IS NOT NULL AND TRIM(m.posicion_sd) <> ''
                  AND m.lote IS NOT NULL AND TRIM(m.lote) <> ''
                  AND p.numero_parte IS NULL
                GROUP BY m.material
                ORDER BY m.material
                LIMIT ?
                                """.strip(),
                (centro, almacen, lim),
            ).fetchall()
        return [{"material": str(r[0]), "texto_breve": str(r[1] or "")} for r in rows]

    def get_mb52_texto_breve(self, *, material: str) -> str:
        """Returns the latest known short description for a material from MB52."""
        mat = str(material or "").strip()
        if not mat:
            return ""
        with self.db.connect() as con:
            row = con.execute(
                """
                SELECT COALESCE(MAX(texto_breve), '')
                FROM sap_mb52
                WHERE material = ?
                """,
                (mat,),
            ).fetchone()
        return str((row[0] if row else "") or "")

    # ---------- Import ----------
    def import_excel_bytes(self, *, kind: str, content: bytes) -> None:
        df = read_excel_bytes(content)

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
            con.execute("DELETE FROM sap_mb52")
            con.execute("DELETE FROM sap_vision")
            # parts (familias) are managed manually in-app; keep them.
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
            libre = to_int01(r.get("libre_utilizacion"))
            doc = self._normalize_sap_key(r.get("documento_comercial"))
            pos = self._normalize_sap_key(r.get("posicion_sd"))
            qc = to_int01(r.get("en_control_calidad"))
            rows.append((material, texto_breve, centro, almacen, lote, libre, doc, pos, qc))

        # Progress report (Terminaciones): compute salidas brutas vs previous MB52 when replacing.
        # We compute it BEFORE invalidating cached programs.
        last_program_term = None
        try:
            last_program_term = self.load_last_program(process="terminaciones")
        except Exception:
            last_program_term = None

        with self.db.connect() as con:
            prev_keys_term: set[tuple[str, str, str]] | None = None
            prev_items_term: list[tuple[str, str, str, int]] | None = None
            if mode == "replace":
                try:
                    centro_term = (self.get_config(key="sap_centro", default="4000") or "").strip()
                    almacen_term = (self.get_config(key="sap_almacen_terminaciones", default="4035") or "").strip()
                    centro_term = self._normalize_sap_key(centro_term) or centro_term
                    almacen_term = self._normalize_sap_key(almacen_term) or almacen_term
                    avail_sql = self._mb52_availability_predicate_sql(process="terminaciones")
                    rows_prev = con.execute(
                        f"""
                        SELECT documento_comercial AS pedido, posicion_sd AS posicion, lote
                        FROM sap_mb52
                        WHERE centro = ?
                          AND almacen = ?
                          AND {avail_sql}
                          AND documento_comercial IS NOT NULL AND TRIM(documento_comercial) <> ''
                          AND posicion_sd IS NOT NULL AND TRIM(posicion_sd) <> ''
                          AND lote IS NOT NULL AND TRIM(lote) <> ''
                        """.strip(),
                        (str(centro_term), str(almacen_term)),
                    ).fetchall()

                    prev_items_term = []
                    for r in rows_prev:
                        pedido = str(r["pedido"] or "").strip()
                        posicion = str(r["posicion"] or "").strip()
                        lote = str(r["lote"] or "").strip()
                        if not pedido or not posicion or not lote:
                            continue
                        corr = self._lote_to_int(lote)
                        prev_items_term.append((pedido, posicion, lote, int(corr)))
                    prev_keys_term = {(p, pos, lote) for (p, pos, lote, _) in prev_items_term}
                except Exception:
                    prev_keys_term = None
                    prev_items_term = None

            if mode == "replace":
                con.execute("DELETE FROM sap_mb52")
            else:
                # Merge mode: replace only the centro/almacen subsets present in this file.
                for c, a in sorted(centro_almacen_pairs):
                    con.execute("DELETE FROM sap_mb52 WHERE centro = ? AND almacen = ?", (c, a))
            con.executemany(
                """
                INSERT INTO sap_mb52(material, texto_breve, centro, almacen, lote, libre_utilizacion, documento_comercial, posicion_sd, en_control_calidad)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

            if mode == "replace" and last_program_term and prev_keys_term is not None and prev_items_term is not None:
                try:
                    centro_term = (self.get_config(key="sap_centro", default="4000") or "").strip()
                    almacen_term = (self.get_config(key="sap_almacen_terminaciones", default="4035") or "").strip()
                    centro_term = self._normalize_sap_key(centro_term) or centro_term
                    almacen_term = self._normalize_sap_key(almacen_term) or almacen_term
                    avail_sql = self._mb52_availability_predicate_sql(process="terminaciones")
                    rows_curr = con.execute(
                        f"""
                        SELECT documento_comercial AS pedido, posicion_sd AS posicion, lote
                        FROM sap_mb52
                        WHERE centro = ?
                          AND almacen = ?
                          AND {avail_sql}
                          AND documento_comercial IS NOT NULL AND TRIM(documento_comercial) <> ''
                          AND posicion_sd IS NOT NULL AND TRIM(posicion_sd) <> ''
                          AND lote IS NOT NULL AND TRIM(lote) <> ''
                        """.strip(),
                        (str(centro_term), str(almacen_term)),
                    ).fetchall()
                    curr_keys_term: set[tuple[str, str, str]] = set()
                    for r in rows_curr:
                        pedido = str(r["pedido"] or "").strip()
                        posicion = str(r["posicion"] or "").strip()
                        lote = str(r["lote"] or "").strip()
                        if not pedido or not posicion or not lote:
                            continue
                        curr_keys_term.add((pedido, posicion, lote))

                    report = self._build_mb52_progress_report_terminaciones(
                        last_program=last_program_term,
                        prev_items=prev_items_term,
                        prev_keys=prev_keys_term,
                        curr_keys=curr_keys_term,
                    )
                    self._save_mb52_progress_last(con=con, process="terminaciones", report=report)
                except Exception:
                    # Best-effort: never block MB52 import.
                    pass

            # Imported SAP data invalidates all derived orders/programs.
            con.execute("DELETE FROM orders")
            con.execute("DELETE FROM last_program")

    def _save_mb52_progress_last(self, *, con, process: str, report: dict) -> None:
        payload = json.dumps(report)
        generated_on = str(report.get("generated_on") or datetime.now().isoformat(timespec="seconds"))
        con.execute(
            "INSERT INTO mb52_progress_last(process, generated_on, report_json) VALUES(?, ?, ?) "
            "ON CONFLICT(process) DO UPDATE SET generated_on=excluded.generated_on, report_json=excluded.report_json",
            (process, generated_on, payload),
        )

    def load_mb52_progress_last(self, *, process: str = "terminaciones") -> dict | None:
        process = self._normalize_process(process)
        with self.db.connect() as con:
            row = con.execute(
                "SELECT generated_on, report_json FROM mb52_progress_last WHERE process=?",
                (process,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["report_json"])
        if isinstance(payload, dict):
            payload.setdefault("generated_on", row["generated_on"])
        return payload

    def _build_mb52_progress_report_terminaciones(
        self,
        *,
        last_program: dict,
        prev_items: list[tuple[str, str, str, int]],
        prev_keys: set[tuple[str, str, str]],
        curr_keys: set[tuple[str, str, str]],
    ) -> dict:
        """Build a progress report for Terminaciones.

        Salidas brutas: items present in prev MB52 but not present in current MB52.
        Items are identified by (pedido,posicion,lote). We map each salida to a program row
        if its correlativo (digits extracted from lote) falls within corr_inicio..corr_fin.
        Unmatched salidas are reported in a separate table.
        """

        program_generated_on = str(last_program.get("generated_on") or "")
        program = last_program.get("program") or {}

        # Index program rows by (pedido,posicion)
        by_orderpos: dict[tuple[str, str], list[dict]] = {}
        row_by_row_id: dict[str, dict] = {}
        line_by_row_id: dict[str, int] = {}

        for raw_line_id, items in (program or {}).items():
            try:
                line_id = int(raw_line_id)
            except Exception:
                continue
            for r in list(items or []):
                try:
                    if int(r.get("is_test") or 0) == 1:
                        continue
                except Exception:
                    pass

                pedido = str(r.get("pedido") or "").strip()
                posicion = str(r.get("posicion") or "").strip()
                if not pedido or not posicion:
                    continue
                try:
                    a = int(r.get("corr_inicio"))
                    b = int(r.get("corr_fin"))
                except Exception:
                    continue
                row_id = str(r.get("_row_id") or f"{pedido}|{posicion}|{a}-{b}|line{line_id}")
                rec = {
                    "_row_id": row_id,
                    "pedido": pedido,
                    "posicion": posicion,
                    "numero_parte": str(r.get("numero_parte") or "").strip(),
                    "familia": str(r.get("familia") or "").strip(),
                    "cantidad": int(r.get("cantidad") or 0),
                    "fecha_entrega": str(r.get("fecha_entrega") or "").strip(),
                    "corr_inicio": a,
                    "corr_fin": b,
                    "prio_kind": str(r.get("prio_kind") or "").strip(),
                    "is_test": 0,
                }
                by_orderpos.setdefault((pedido, posicion), []).append(rec)
                row_by_row_id[row_id] = rec
                line_by_row_id[row_id] = int(line_id)

        # Sort ranges to speed up matching
        for k in list(by_orderpos.keys()):
            by_orderpos[k].sort(key=lambda rr: (int(rr.get("corr_inicio") or 0), int(rr.get("corr_fin") or 0)))

        exited = []
        for pedido, posicion, lote, corr in prev_items:
            if (pedido, posicion, lote) not in curr_keys:
                exited.append((pedido, posicion, lote, int(corr)))

        # Count salidas per program row
        salio_by_row_id: dict[str, int] = {}
        unplanned_by_orderpos: dict[tuple[str, str], int] = {}

        for pedido, posicion, _lote, corr in exited:
            if corr <= 0:
                # Cannot map to a correlativo range reliably
                unplanned_by_orderpos[(pedido, posicion)] = unplanned_by_orderpos.get((pedido, posicion), 0) + 1
                continue

            candidates = by_orderpos.get((pedido, posicion)) or []
            matched_row_id: str | None = None
            for rr in candidates:
                if int(rr["corr_inicio"]) <= corr <= int(rr["corr_fin"]):
                    matched_row_id = str(rr["_row_id"])
                    break

            if matched_row_id is None:
                unplanned_by_orderpos[(pedido, posicion)] = unplanned_by_orderpos.get((pedido, posicion), 0) + 1
            else:
                salio_by_row_id[matched_row_id] = salio_by_row_id.get(matched_row_id, 0) + 1

        # Build per-line report rows
        lines_out: dict[int, list[dict]] = {}
        for row_id, salio in salio_by_row_id.items():
            if salio <= 0:
                continue
            base = dict(row_by_row_id.get(row_id) or {"_row_id": row_id})
            base["salio"] = int(salio)
            line_id = int(line_by_row_id.get(row_id) or 0)
            lines_out.setdefault(line_id, []).append(base)

        for line_id in list(lines_out.keys()):
            lines_out[line_id].sort(key=lambda r: (str(r.get("pedido") or ""), str(r.get("posicion") or ""), int(r.get("corr_inicio") or 0)))

        # Unplanned rows (aggregate by orderpos)
        unplanned_rows: list[dict] = []
        for (pedido, posicion), n in sorted(unplanned_by_orderpos.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            unplanned_rows.append(
                {
                    "_row_id": f"{pedido}|{posicion}",
                    "pedido": pedido,
                    "posicion": posicion,
                    "salio": int(n),
                }
            )

        return {
            "process": "terminaciones",
            "generated_on": datetime.now().isoformat(timespec="seconds"),
            "program_generated_on": program_generated_on,
            "mb52_prev_count": int(len(prev_keys)),
            "mb52_curr_count": int(len(curr_keys)),
            "lines": lines_out,
            "unplanned": unplanned_rows,
        }

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
                    "SELECT almacen, COUNT(*) c FROM sap_mb52 WHERE centro = ? GROUP BY almacen ORDER BY c DESC LIMIT ?",
                    (centro_n, lim),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT almacen, COUNT(*) c FROM sap_mb52 GROUP BY almacen ORDER BY c DESC LIMIT ?",
                    (lim,),
                ).fetchall()
        return [{"almacen": str(r[0] or ""), "count": int(r[1] or 0)} for r in rows]

    def import_sap_vision_bytes(self, *, content: bytes) -> None:
        df_raw = read_excel_bytes(content)
        df = normalize_columns(df_raw)

        # Canonicalize a couple of common header variants
        if "pos" in df.columns and "posicion" not in df.columns:
            df = df.rename(columns={"pos": "posicion"})
        if "pos_oc" in df.columns and "posoc" not in df.columns:
            df = df.rename(columns={"pos_oc": "posoc"})
        # some exports might call it 'fecha_pedido'
        if "fecha_de_pedido" not in df.columns and "fecha_pedido" in df.columns:
            df = df.rename(columns={"fecha_pedido": "fecha_de_pedido"})

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

        self._validate_columns(df.columns, {"pedido", "posicion", "cod_material", "fecha_de_pedido"})

        rows: list[tuple] = []
        for _, r in df.iterrows():
            pedido = self._normalize_sap_key(r.get("pedido")) or ""
            posicion = self._normalize_sap_key(r.get("posicion")) or ""
            if not pedido or not posicion:
                continue
            cod_material = self._normalize_sap_key(r.get("cod_material"))
            desc = str(r.get("descripcion_material", "")).strip() or None
            fecha_pedido = coerce_date(r.get("fecha_de_pedido"))
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
            rows.append(
                (
                    pedido,
                    posicion,
                    cod_material,
                    desc,
                    fecha_pedido,
                    fecha_entrega,
                    solicitado,
                    cliente,
                    oc_cliente,
                    peso_neto,
                    peso_unitario_ton,
                    bodega,
                    despachado,
                )
            )

        with self.db.connect() as con:
            con.execute("DELETE FROM sap_vision")
            con.executemany(
                """
                INSERT INTO sap_vision(
                    pedido, posicion, cod_material, descripcion_material, fecha_pedido, fecha_entrega,
                    solicitado, cliente, oc_cliente, peso_neto, peso_unitario_ton, bodega, despachado
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

            # Update materials master weights (peso_ton) by iterating the master:
            # for each material, pick the first pedido/pos in Vision (ordered) and
            # use its peso_unitario_ton (tons per piece; from (peso_neto_kg/1000)/solicitado).
            con.execute(
                """
                UPDATE parts
                SET peso_ton = COALESCE(
                    (
                        SELECT v.peso_unitario_ton
                        FROM sap_vision v
                        WHERE v.cod_material = parts.numero_parte
                          AND v.peso_unitario_ton IS NOT NULL
                          AND v.peso_unitario_ton >= 0
                        ORDER BY v.fecha_pedido ASC, v.pedido ASC, v.posicion ASC
                        LIMIT 1
                    ),
                    peso_ton
                )
                WHERE EXISTS (
                    SELECT 1
                    FROM sap_vision v2
                    WHERE v2.cod_material = parts.numero_parte
                      AND v2.peso_unitario_ton IS NOT NULL
                      AND v2.peso_unitario_ton >= 0
                )
                """
            )
            con.execute("DELETE FROM orders")
            con.execute("DELETE FROM last_program")

    def rebuild_orders_from_sap(self) -> int:
        """Backwards-compatible Terminaciones rebuild."""
        return self.rebuild_orders_from_sap_for(process="terminaciones")

    def rebuild_orders_from_sap_for(self, *, process: str = "terminaciones") -> int:
        """Build orders table from usable pieces in MB52 + fecha_pedido in Vision.

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
                FROM sap_mb52
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

            # Vision lookup: (pedido,posicion) -> (fecha_pedido_iso, cod_material)
            vision_rows = con.execute(
                "SELECT pedido, posicion, fecha_pedido, cod_material FROM sap_vision"
            ).fetchall()
            vision_by_key: dict[tuple[str, str], tuple[str, str | None]] = {}
            for r in vision_rows:
                vision_by_key[(str(r[0]).strip(), str(r[1]).strip())] = (str(r[2]).strip(), (str(r[3]).strip() if r[3] is not None else None))

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
            fecha_pedido_iso, _ = vision_by_key[(pedido, posicion)]
            cantidad = int(len(lotes))
            lote_ints = [self._lote_to_int(ls) for ls in lotes]
            corr_inicio = int(min(lote_ints))
            corr_fin = int(max(lote_ints))
            order_rows.append((pedido, posicion, material, cantidad, fecha_pedido_iso, corr_inicio, corr_fin, None, int(is_test)))

        # Deterministic order
        order_rows.sort(key=lambda t: (t[4], t[0], t[1], -int(t[8] or 0), t[2]))

        with self.db.connect() as con:
            con.execute("DELETE FROM orders WHERE process = ?", (process,))
            con.executemany(
                """
                INSERT INTO orders(process, almacen, pedido, posicion, numero_parte, cantidad, fecha_entrega, primer_correlativo, ultimo_correlativo, tiempo_proceso_min, is_test)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                "SELECT pedido, posicion, numero_parte, cantidad, fecha_entrega, primer_correlativo, ultimo_correlativo, tiempo_proceso_min, is_test FROM orders WHERE process = ?",
                (process,),
            ).fetchall()
        out: list[Order] = []
        for pedido, posicion, numero_parte, cantidad, fecha_entrega, primer, ultimo, tpm, is_test in rows:
            out.append(
                Order(
                    pedido=str(pedido),
                    posicion=str(posicion),
                    numero_parte=str(numero_parte),
                    cantidad=int(cantidad),
                    fecha_entrega=date.fromisoformat(str(fecha_entrega)),
                    primer_correlativo=int(primer),
                    ultimo_correlativo=int(ultimo),
                    tiempo_proceso_min=float(tpm) if tpm is not None else None,
                    is_test=bool(int(is_test or 0)),
                )
            )
        return out

    def get_parts_model(self) -> list[Part]:
        with self.db.connect() as con:
            rows = con.execute(
                "SELECT numero_parte, familia, vulcanizado_dias, mecanizado_dias, inspeccion_externa_dias, peso_ton, mec_perf_inclinada, sobre_medida FROM parts"
            ).fetchall()
        return [
            Part(
                numero_parte=str(r[0]),
                familia=str(r[1]),
                vulcanizado_dias=r[2],
                mecanizado_dias=r[3],
                inspeccion_externa_dias=r[4],
                peso_ton=(float(r[5]) if r[5] is not None else None),
                mec_perf_inclinada=bool(int(r[6] or 0)),
                sobre_medida=bool(int(r[7] or 0)),
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
                return
            except Exception:
                # Backward-compatible fallback.
                con.execute(
                    "UPDATE program_in_progress SET line_id=? WHERE process=? AND pedido=? AND posicion=? AND is_test=?",
                    (int(line_id), process, pedido_s, posicion_s, is_test_i),
                )

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
                r.setdefault("in_progress", 0)
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
                    "numero_parte": o.numero_parte,
                    "familia": "Otros",
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
            specified = [q for q in effective_qtys[:-1] if q > 0]
            sum_specified = sum(specified)

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
                row["numero_parte"] = o.numero_parte
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
                    f"{o.pedido}|{o.posicion}|{o.numero_parte}|split{split_id}|{int(row['corr_inicio'])}-{int(row['corr_fin'])}"
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
