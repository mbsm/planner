from __future__ import annotations

import json
import re
from datetime import date, datetime

from plannerterm.core.models import Line, Order, Part
from plannerterm.data.db import Db
from plannerterm.data.excel_io import coerce_date, normalize_columns, parse_int_strict, read_excel_bytes, to_int01


class Repository:
    def __init__(self, db: Db):
        self.db = db

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
        We keep the scheduling logic numeric by extracting all digits.
        """
        try:
            return int(parse_int_strict(value, field="Lote"))
        except Exception:
            s = "" if value is None else str(value)
            digits = "".join(re.findall(r"\d+", s))
            if not digits:
                raise
            return int(digits)

    def get_sap_rebuild_diagnostics(self) -> dict:
        """Counters to debug why ranges might be 0."""
        centro = (self.get_config(key="sap_centro", default="4000") or "").strip()
        almacen = (self.get_config(key="sap_almacen_terminaciones", default="4035") or "").strip()
        if not centro or not almacen:
            return {
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
                    """
                    SELECT COUNT(*)
                    FROM sap_mb52
                    WHERE centro = ?
                      AND almacen = ?
                      AND COALESCE(libre_utilizacion, 0) = 1
                      AND COALESCE(en_control_calidad, 0) = 0
                    """,
                    (centro, almacen),
                ).fetchone()[0]
            )

            usable_with_keys = int(
                con.execute(
                    """
                    SELECT COUNT(*)
                    FROM sap_mb52
                    WHERE centro = ?
                      AND almacen = ?
                      AND COALESCE(libre_utilizacion, 0) = 1
                      AND COALESCE(en_control_calidad, 0) = 0
                      AND documento_comercial IS NOT NULL AND TRIM(documento_comercial) <> ''
                      AND posicion_sd IS NOT NULL AND TRIM(posicion_sd) <> ''
                      AND lote IS NOT NULL AND TRIM(lote) <> ''
                    """,
                    (centro, almacen),
                ).fetchone()[0]
            )

            usable_with_keys_and_vision = int(
                con.execute(
                    """
                    SELECT COUNT(*)
                    FROM sap_mb52 m
                    JOIN sap_vision v
                      ON v.pedido = m.documento_comercial
                     AND v.posicion = m.posicion_sd
                    WHERE m.centro = ?
                      AND m.almacen = ?
                      AND COALESCE(m.libre_utilizacion, 0) = 1
                      AND COALESCE(m.en_control_calidad, 0) = 0
                      AND m.documento_comercial IS NOT NULL AND TRIM(m.documento_comercial) <> ''
                      AND m.posicion_sd IS NOT NULL AND TRIM(m.posicion_sd) <> ''
                      AND m.lote IS NOT NULL AND TRIM(m.lote) <> ''
                    """,
                    (centro, almacen),
                ).fetchone()[0]
            )

            distinct_orderpos = int(
                con.execute(
                    """
                    SELECT COUNT(*)
                    FROM (
                        SELECT DISTINCT documento_comercial, posicion_sd
                        FROM sap_mb52
                        WHERE centro = ?
                          AND almacen = ?
                          AND COALESCE(libre_utilizacion, 0) = 1
                          AND COALESCE(en_control_calidad, 0) = 0
                          AND documento_comercial IS NOT NULL AND TRIM(documento_comercial) <> ''
                          AND posicion_sd IS NOT NULL AND TRIM(posicion_sd) <> ''
                          AND lote IS NOT NULL AND TRIM(lote) <> ''
                    )
                    """,
                    (centro, almacen),
                ).fetchone()[0]
            )

            distinct_orderpos_missing_vision = int(
                con.execute(
                    """
                    SELECT COUNT(*)
                    FROM (
                        SELECT DISTINCT m.documento_comercial AS pedido, m.posicion_sd AS posicion
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
                    )
                    """,
                    (centro, almacen),
                ).fetchone()[0]
            )

        return {
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
            rows = con.execute("SELECT line_id, families_json FROM line_config").fetchall()
            for r in rows:
                families = json.loads(r["families_json"])
                updated = [new if f == old else f for f in families]
                updated = sorted(set(updated))
                con.execute(
                    "UPDATE line_config SET families_json = ? WHERE line_id = ?",
                    (json.dumps(updated), int(r["line_id"])),
                )

            # Remove old from catalog
            con.execute("DELETE FROM families WHERE name = ?", (old,))

            # Invalidate any previously generated program
            con.execute("DELETE FROM last_program WHERE id = 1")

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
            rows = con.execute("SELECT line_id, families_json FROM line_config").fetchall()
            for r in rows:
                families = json.loads(r["families_json"])
                if force:
                    updated = ["Otros" if f == name else f for f in families]
                else:
                    updated = [f for f in families if f != name]
                updated = sorted(set(updated))
                con.execute(
                    "UPDATE line_config SET families_json = ? WHERE line_id = ?",
                    (json.dumps(updated), int(r["line_id"])),
                )

            con.execute("DELETE FROM families WHERE name = ?", (name,))

            # Invalidate any previously generated program
            con.execute("DELETE FROM last_program WHERE id = 1")

    # ---------- Lines ----------
    def upsert_line(self, *, line_id: int, families: list[str]) -> None:
        families_json = json.dumps(sorted(set(families)))
        with self.db.connect() as con:
            con.execute(
                "INSERT INTO line_config(line_id, families_json) VALUES(?, ?) "
                "ON CONFLICT(line_id) DO UPDATE SET families_json=excluded.families_json",
                (int(line_id), families_json),
            )

            # Invalidate any previously generated program
            con.execute("DELETE FROM last_program WHERE id = 1")

    def delete_line(self, *, line_id: int) -> None:
        with self.db.connect() as con:
            con.execute("DELETE FROM line_config WHERE line_id = ?", (int(line_id),))
            # Invalidate any previously generated program
            con.execute("DELETE FROM last_program WHERE id = 1")

    def get_lines(self) -> list[dict]:
        with self.db.connect() as con:
            rows = con.execute("SELECT line_id, families_json FROM line_config ORDER BY line_id").fetchall()
        out: list[dict] = []
        for r in rows:
            out.append({"line_id": int(r["line_id"]), "families": json.loads(r["families_json"])})
        return out

    def get_lines_model(self) -> list[Line]:
        return [Line(line_id=r["line_id"], allowed_families=set(r["families"])) for r in self.get_lines()]

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
            con.execute("DELETE FROM last_program WHERE id = 1")

    def upsert_part_master(
        self,
        *,
        numero_parte: str,
        familia: str,
        vulcanizado_dias: int | None = None,
        mecanizado_dias: int | None = None,
        inspeccion_externa_dias: int | None = None,
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

        # Ensure family exists in catalog
        self.add_family(name=familia)

        with self.db.connect() as con:
            con.execute(
                "INSERT INTO parts(numero_parte, familia, vulcanizado_dias, mecanizado_dias, inspeccion_externa_dias) "
                "VALUES(?, ?, ?, ?, ?) "
                "ON CONFLICT(numero_parte) DO UPDATE SET "
                "familia=excluded.familia, "
                "vulcanizado_dias=excluded.vulcanizado_dias, "
                "mecanizado_dias=excluded.mecanizado_dias, "
                "inspeccion_externa_dias=excluded.inspeccion_externa_dias",
                (numero_parte, familia, v, m, i),
            )

            # Invalidate any previously generated program
            con.execute("DELETE FROM last_program WHERE id = 1")

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
            con.execute("DELETE FROM last_program WHERE id = 1")

    def delete_part(self, *, numero_parte: str) -> None:
        with self.db.connect() as con:
            con.execute("DELETE FROM parts WHERE numero_parte = ?", (str(numero_parte).strip(),))
            # Invalidate any previously generated program
            con.execute("DELETE FROM last_program WHERE id = 1")

    def delete_all_parts(self) -> None:
        with self.db.connect() as con:
            con.execute("DELETE FROM parts")
            # Invalidate any previously generated program
            con.execute("DELETE FROM last_program WHERE id = 1")

    def get_parts_rows(self) -> list[dict]:
        with self.db.connect() as con:
            rows = con.execute(
                "SELECT numero_parte, familia, vulcanizado_dias, mecanizado_dias, inspeccion_externa_dias FROM parts ORDER BY numero_parte"
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            out.append(
                {
                    "numero_parte": str(r[0]),
                    "familia": str(r[1]),
                    "vulcanizado_dias": r[2],
                    "mecanizado_dias": r[3],
                    "inspeccion_externa_dias": r[4],
                }
            )
        return out

    def get_missing_parts_from_orders(self) -> list[str]:
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT DISTINCT o.numero_parte
                FROM orders o
                LEFT JOIN parts p ON p.numero_parte = o.numero_parte
                WHERE p.numero_parte IS NULL
                ORDER BY o.numero_parte
                """
            ).fetchall()
        return [str(r[0]) for r in rows]

    def get_missing_process_times_from_orders(self) -> list[str]:
        """Distinct numero_parte referenced by orders that has a master row but missing any process time."""
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT DISTINCT o.numero_parte
                FROM orders o
                JOIN parts p ON p.numero_parte = o.numero_parte
                WHERE p.vulcanizado_dias IS NULL
                   OR p.mecanizado_dias IS NULL
                   OR p.inspeccion_externa_dias IS NULL
                ORDER BY o.numero_parte
                """
            ).fetchall()
        return [str(r[0]) for r in rows]

    def count_missing_process_times_from_orders(self) -> int:
        with self.db.connect() as con:
            row = con.execute(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT DISTINCT o.numero_parte
                    FROM orders o
                    JOIN parts p ON p.numero_parte = o.numero_parte
                    WHERE p.vulcanizado_dias IS NULL
                       OR p.mecanizado_dias IS NULL
                       OR p.inspeccion_externa_dias IS NULL
                )
                """
            ).fetchone()
        return int(row[0])

    def count_missing_parts_from_orders(self) -> int:
        with self.db.connect() as con:
            row = con.execute(
                """
                SELECT COUNT(*)
                FROM (
                    SELECT DISTINCT o.numero_parte
                    FROM orders o
                    LEFT JOIN parts p ON p.numero_parte = o.numero_parte
                    WHERE p.numero_parte IS NULL
                )
                """
            ).fetchone()
        return int(row[0])

    # ---------- Counts ----------
    def count_orders(self) -> int:
        with self.db.connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM orders").fetchone()[0])

    def count_sap_mb52(self) -> int:
        with self.db.connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM sap_mb52").fetchone()[0])

    def count_sap_vision(self) -> int:
        with self.db.connect() as con:
            return int(con.execute("SELECT COUNT(*) FROM sap_vision").fetchone()[0])

    def count_usable_pieces(self) -> int:
        centro = (self.get_config(key="sap_centro", default="4000") or "").strip()
        almacen = (self.get_config(key="sap_almacen_terminaciones", default="4035") or "").strip()
        if not centro or not almacen:
            return 0
        with self.db.connect() as con:
            return int(
                con.execute(
                    """
                    SELECT COUNT(*)
                    FROM sap_mb52
                    WHERE centro = ?
                      AND almacen = ?
                      AND COALESCE(libre_utilizacion, 0) = 1
                      AND COALESCE(en_control_calidad, 0) = 0
                    """,
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
                    MIN(COALESCE(v.fecha_pedido, o.fecha_entrega)) AS fecha_pedido
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
            out.append(
                {
                    "pedido": str(r["pedido"]),
                    "posicion": str(r["posicion"]),
                    "is_priority": int(r["is_priority"] or 0),
                    "priority_kind": str(r["priority_kind"] or ""),
                    "cliente": str(r["cliente"] or ""),
                    "fecha_pedido": str(r["fecha_pedido"] or ""),
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
            con.execute("DELETE FROM last_program WHERE id = 1")

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
        """Distinct materials in MB52 not present in the local parts master.

        Returns a list of dicts with keys: material, texto_breve.
        """
        centro = (self.get_config(key="sap_centro", default="4000") or "").strip()
        almacen = (self.get_config(key="sap_almacen_terminaciones", default="4035") or "").strip()
        if not centro or not almacen:
            return []
        with self.db.connect() as con:
            rows = con.execute(
                """
                SELECT m.material, COALESCE(MAX(m.texto_breve), '') AS texto_breve
                FROM sap_mb52 m
                LEFT JOIN parts p ON p.numero_parte = m.material
                WHERE m.material IS NOT NULL AND TRIM(m.material) <> ''
                  AND m.centro = ?
                  AND m.almacen = ?
                                    AND m.documento_comercial IS NOT NULL AND TRIM(m.documento_comercial) <> ''
                  AND p.numero_parte IS NULL
                GROUP BY m.material
                ORDER BY m.material
                """
                ,
                (centro, almacen),
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

        if kind == "pedidos":
            # Expected columns (allow a couple of aliases for correlativos)
            cols = {str(c).strip() for c in df.columns}
            if "primer_correlativo" not in cols and "corr_inicio" in cols:
                df = df.rename(columns={"corr_inicio": "primer_correlativo"})
                cols.add("primer_correlativo")
            if "ultimo_correlativo" not in cols and "corr_fin" in cols:
                df = df.rename(columns={"corr_fin": "ultimo_correlativo"})
                cols.add("ultimo_correlativo")

            required = {"pedido", "numero_parte", "cantidad", "fecha_entrega", "primer_correlativo", "ultimo_correlativo"}
            self._validate_columns(df.columns, required)

            rows = []
            for _, r in df.iterrows():
                pedido = str(r["pedido"]).strip()
                numero_parte = str(r["numero_parte"]).strip()
                cantidad = int(r["cantidad"])
                fecha_iso = coerce_date(r["fecha_entrega"])
                primer = int(r["primer_correlativo"])
                ultimo = int(r["ultimo_correlativo"])
                if ultimo < primer:
                    raise ValueError(f"Pedido {pedido}: ultimo_correlativo < primer_correlativo")
                if (ultimo - primer + 1) != cantidad:
                    raise ValueError(
                        f"Pedido {pedido}: cantidad ({cantidad}) no coincide con rango de correlativos ({primer}-{ultimo})"
                    )

                tpm = None
                if "tiempo_proceso_min" in df.columns:
                    raw = r.get("tiempo_proceso_min")
                    if raw is not None and str(raw).strip() != "" and str(raw).strip().lower() != "nan":
                        tpm = float(raw)

                # Legacy format has no posicion; keep a fixed placeholder.
                rows.append((pedido, "0000", numero_parte, cantidad, fecha_iso, primer, ultimo, tpm))

            with self.db.connect() as con:
                con.execute("DELETE FROM orders")
                con.executemany(
                    "INSERT INTO orders(pedido, posicion, numero_parte, cantidad, fecha_entrega, primer_correlativo, ultimo_correlativo, tiempo_proceso_min) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                con.execute("DELETE FROM last_program WHERE id = 1")
            return

        if kind in {"mb52", "sap_mb52"}:
            self.import_sap_mb52_bytes(content=content)
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
            con.execute("DELETE FROM last_program WHERE id = 1")

    # ---------- SAP Import + rebuild ----------
    def import_sap_mb52_bytes(self, *, content: bytes) -> None:
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
        for _, r in df.iterrows():
            material = str(r.get("material", "")).strip()
            if not material:
                continue
            # Business rule: only keep material codes for pieces (prefix 436)
            if not material.startswith("436"):
                continue
            texto_breve = str(r.get("texto_breve_de_material", "") or r.get("texto_breve", "") or "").strip() or None
            centro = str(r.get("centro", "")).strip() or None
            almacen = str(r.get("almacen", "")).strip() or None
            lote = str(r.get("lote", "")).strip() or None
            libre = to_int01(r.get("libre_utilizacion"))
            doc = self._normalize_sap_key(r.get("documento_comercial"))
            if not doc:
                # Without Documento comercial we can't build pedido/posición-based orders.
                continue
            pos = self._normalize_sap_key(r.get("posicion_sd"))
            qc = to_int01(r.get("en_control_calidad"))
            rows.append((material, texto_breve, centro, almacen, lote, libre, doc, pos, qc))

        with self.db.connect() as con:
            con.execute("DELETE FROM sap_mb52")
            con.executemany(
                """
                INSERT INTO sap_mb52(material, texto_breve, centro, almacen, lote, libre_utilizacion, documento_comercial, posicion_sd, en_control_calidad)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            con.execute("DELETE FROM last_program WHERE id = 1")

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
            cliente = str(r.get("cliente", "")).strip() or None
            oc_cliente = str(r.get("n_oc_cliente", "") or "").strip() or None
            rows.append((pedido, posicion, cod_material, desc, fecha_pedido, fecha_entrega, solicitado, cliente, oc_cliente))

        with self.db.connect() as con:
            con.execute("DELETE FROM sap_vision")
            con.executemany(
                """
                INSERT INTO sap_vision(pedido, posicion, cod_material, descripcion_material, fecha_pedido, fecha_entrega, solicitado, cliente, oc_cliente)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            con.execute("DELETE FROM last_program WHERE id = 1")

    def rebuild_orders_from_sap(self) -> int:
        """Build orders table from usable pieces in MB52 + fecha_pedido in Vision.

        Returns how many order-rows were created.
        """
        centro = (self.get_config(key="sap_centro", default="4000") or "").strip()
        almacen = (self.get_config(key="sap_almacen_terminaciones", default="4035") or "").strip()
        if not centro:
            raise ValueError("Config faltante: sap_centro")
        if not almacen:
            raise ValueError("Config faltante: sap_almacen_terminaciones")

        with self.db.connect() as con:
            mb_rows = con.execute(
                """
                SELECT material, documento_comercial, posicion_sd, lote
                FROM sap_mb52
                WHERE centro = ?
                  AND almacen = ?
                  AND COALESCE(libre_utilizacion, 0) = 1
                  AND COALESCE(en_control_calidad, 0) = 0
                  AND documento_comercial IS NOT NULL AND TRIM(documento_comercial) <> ''
                  AND posicion_sd IS NOT NULL AND TRIM(posicion_sd) <> ''
                  AND lote IS NOT NULL AND TRIM(lote) <> ''
                """,
                (centro, almacen),
            ).fetchall()

            if not mb_rows:
                con.execute("DELETE FROM orders")
                con.execute("DELETE FROM last_program WHERE id = 1")
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
            con.execute("DELETE FROM orders")
            con.executemany(
                """
                INSERT INTO orders(pedido, posicion, numero_parte, cantidad, fecha_entrega, primer_correlativo, ultimo_correlativo, tiempo_proceso_min, is_test)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                order_rows,
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
            con.execute("DELETE FROM last_program WHERE id = 1")

        return len(order_rows)

    def try_rebuild_orders_from_sap(self) -> bool:
        """Attempt to rebuild orders; returns False if missing prerequisites."""
        if self.count_sap_mb52() == 0 or self.count_sap_vision() == 0:
            return False
        centro = (self.get_config(key="sap_centro", default="4000") or "").strip()
        almacen = (self.get_config(key="sap_almacen_terminaciones", default="4035") or "").strip()
        if not centro or not almacen:
            return False
        return self.rebuild_orders_from_sap() > 0

    @staticmethod
    def _validate_columns(columns, required: set[str]) -> None:
        cols = {str(c).strip() for c in columns}
        missing = sorted(required - cols)
        if missing:
            raise ValueError(f"Faltan columnas: {missing}. Columnas detectadas: {sorted(cols)}")

    # ---------- Models ----------
    def get_orders_model(self) -> list[Order]:
        with self.db.connect() as con:
            rows = con.execute(
                "SELECT pedido, posicion, numero_parte, cantidad, fecha_entrega, primer_correlativo, ultimo_correlativo, tiempo_proceso_min, is_test FROM orders"
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
                "SELECT numero_parte, familia, vulcanizado_dias, mecanizado_dias, inspeccion_externa_dias FROM parts"
            ).fetchall()
        return [
            Part(
                numero_parte=str(r[0]),
                familia=str(r[1]),
                vulcanizado_dias=r[2],
                mecanizado_dias=r[3],
                inspeccion_externa_dias=r[4],
            )
            for r in rows
        ]

    # ---------- Program persistence ----------
    def save_last_program(self, program: dict[int, list[dict]], errors: list[dict] | None = None) -> None:
        payload = json.dumps({"program": program, "errors": list(errors or [])})
        generated_on = datetime.now().isoformat(timespec="seconds")
        with self.db.connect() as con:
            con.execute(
                "INSERT INTO last_program(id, generated_on, program_json) VALUES(1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET generated_on=excluded.generated_on, program_json=excluded.program_json",
                (generated_on, payload),
            )

    def load_last_program(self) -> dict | None:
        with self.db.connect() as con:
            row = con.execute("SELECT generated_on, program_json FROM last_program WHERE id=1").fetchone()
        if row is None:
            return None
        payload = json.loads(row["program_json"])
        if isinstance(payload, dict) and "program" in payload:
            return {
                "generated_on": row["generated_on"],
                "program": payload.get("program") or {},
                "errors": payload.get("errors") or [],
            }
        # Backward-compatible: older DBs stored only the program dict
        return {"generated_on": row["generated_on"], "program": payload, "errors": []}
