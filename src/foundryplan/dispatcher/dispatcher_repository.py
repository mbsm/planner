"""Dispatcher repository implementation.

Extracted from _RepositoryImpl to separate dispatcher concerns from data layer.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from foundryplan.data.db import Db
from foundryplan.dispatcher.models import Job, Line, Order, Part

if TYPE_CHECKING:
    from foundryplan.data.data_repository import DataRepositoryImpl


class DispatcherRepositoryImpl:
    """Dispatcher-specific repository operations.
    
    Handles:
    - Job/Order/Part models
    - Line configuration
    - In-progress locks and splits
    - Program persistence
    """

    def __init__(self, db: Db, data_repo: DataRepositoryImpl) -> None:
        self.db = db
        self.data_repo = data_repo

    # ---------- Models ----------

    def get_orders_model(self, *, process: str = "terminaciones") -> list[Order]:
        process = self.data_repo._normalize_process(process)
        with self.db.connect() as con:
            rows = con.execute(
                "SELECT pedido, posicion, material, cantidad, fecha_de_pedido, primer_correlativo, ultimo_correlativo, tiempo_proceso_min, is_test, cliente FROM core_orders WHERE process = ?",
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
                    fecha_de_pedido=date.fromisoformat(str(fecha_entrega)),
                    primer_correlativo=int(primer),
                    ultimo_correlativo=int(ultimo),
                    tiempo_proceso_min=float(tpm) if tpm is not None else None,
                    is_test=bool(int(is_test or 0)),
                    cliente=str(cliente) if cliente else None,
                )
            )
        return out

    def get_jobs_model(self, *, process: str = "terminaciones") -> list[Job]:
        process = self.data_repo._normalize_process(process)
        with self.db.connect() as con:
            rows = con.execute(
                "SELECT job_id, pedido, posicion, material, qty, priority, fecha_de_pedido, is_test, notes, corr_min, corr_max, cliente FROM dispatcher_job WHERE process_id = ?",
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
                    qty=r["qty"],
                    priority=r["priority"],
                    fecha_de_pedido=date.fromisoformat(r["fecha_de_pedido"]) if r["fecha_de_pedido"] else None,
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
                "SELECT material, family_id, vulcanizado_dias, mecanizado_dias, inspeccion_externa_dias, peso_unitario_ton, mec_perf_inclinada, sobre_medida_mecanizado FROM core_material_master"
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

    # ---------- Lines Management ----------

    @staticmethod
    def _parse_constraint_value(rule_type: str | None, rule_value_json: str | None):
        if rule_value_json is None:
            return None
        try:
            value = json.loads(rule_value_json)
        except Exception:
            value = rule_value_json

        rule = (rule_type or "").strip().lower()
        if rule in {"set", "in", "enum", "list"}:
            if isinstance(value, (list, tuple, set)):
                return set(value)
            return {value}
        if rule in {"bool", "boolean"}:
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "si", "sí", "yes"}
            return bool(value)
        if rule in {"number_range", "range"}:
            if isinstance(value, dict):
                return {"min": value.get("min"), "max": value.get("max")}
        if isinstance(value, list):
            return set(value)
        return value

    def get_resources_model(self, *, process: str = "terminaciones") -> list[Line]:
        process = self.data_repo._normalize_process(process)
        with self.db.connect() as con:
            resources = con.execute(
                """
                SELECT resource_id, sort_order
                FROM resource
                WHERE process_id = ? AND COALESCE(is_active, 1) = 1
                ORDER BY COALESCE(sort_order, 9999), resource_id
                """,
                (process,),
            ).fetchall()
            if not resources:
                return []
            rows = con.execute(
                """
                SELECT resource_id, attr_key, rule_type, rule_value_json
                FROM resource_constraint
                WHERE resource_id IN (
                    SELECT resource_id FROM resource WHERE process_id = ? AND COALESCE(is_active, 1) = 1
                )
                """,
                (process,),
            ).fetchall()

        constraints_by_resource: dict[str, dict[str, object]] = {}
        for r in rows:
            res_id = str(r["resource_id"])
            attr_key = str(r["attr_key"])
            val = self._parse_constraint_value(r["rule_type"], r["rule_value_json"])
            if val is None:
                continue
            constraints_by_resource.setdefault(res_id, {})[attr_key] = val

        return [
            Line(line_id=str(r["resource_id"]), constraints=constraints_by_resource.get(str(r["resource_id"]), {}))
            for r in resources
        ]

    def get_lines_model(self, *, process: str = "terminaciones") -> list[Line]:
        # Map legacy 'families' list to 'family_id' constraint + boolean restrictions
        lines = []
        for r in self.get_dispatch_lines_rows(process=process):
            constraints = {"family_id": set(r["families"])}
            # Add boolean constraints explicitly (both True and False)
            constraints["mec_perf_inclinada"] = r.get("mec_perf_inclinada", False)
            constraints["sobre_medida_mecanizado"] = r.get("sobre_medida_mecanizado", False)
            lines.append(Line(line_id=str(r["line_id"]), constraints=constraints))
        return lines

    def get_dispatch_lines_model(self, *, process: str = "terminaciones") -> list[Line]:
        """Use resource table if configured, otherwise fallback to line_config."""
        try:
            resources = self.get_resources_model(process=process)
            if resources:
                return resources
        except Exception:
            pass
        return self.get_lines_model(process=process)

    def get_dispatch_lines_rows(self, *, process: str = "terminaciones") -> list[dict]:
        process = self.data_repo._normalize_process(process)
        with self.db.connect() as con:
            rows = con.execute(
                "SELECT line_id, line_name, families_json, mec_perf_inclinada, sobre_medida_mecanizado FROM dispatcher_line_config WHERE process = ? ORDER BY line_id",
                (process,),
            ).fetchall()
        out: list[dict] = []
        for r in rows:
            line_id = int(r["line_id"])
            name = str(r["line_name"] or "").strip() if ("line_name" in r.keys()) else ""
            # Try to get new constraint columns (may not exist in older DBs)
            try:
                mec_perf = bool(int(r["mec_perf_inclinada"] or 0))
            except (KeyError, IndexError):
                mec_perf = False
            try:
                sobre_medida = bool(int(r["sobre_medida_mecanizado"] or 0))
            except (KeyError, IndexError):
                sobre_medida = False
            
            out.append(
                {
                    "line_id": line_id,
                    "line_name": name or f"Línea {line_id}",
                    "families": json.loads(r["families_json"]),
                    "mec_perf_inclinada": mec_perf,
                    "sobre_medida_mecanizado": sobre_medida,
                }
            )
        return out

    def upsert_dispatch_line(
        self,
        *,
        process: str = "terminaciones",
        line_id: int,
        families: list[str],
        line_name: str | None = None,
        mec_perf_inclinada: bool = False,
        sobre_medida_mecanizado: bool = False,
    ) -> None:
        process = self.data_repo._normalize_process(process)
        families_json = json.dumps(sorted(set(families)))
        name = None
        if line_name is not None:
            name = str(line_name).strip() or None
        if name is None:
            name = f"Línea {int(line_id)}"
        with self.db.connect() as con:
            con.execute(
                "INSERT INTO dispatcher_line_config(process, line_id, line_name, families_json, mec_perf_inclinada, sobre_medida_mecanizado) "
                "VALUES(?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(process, line_id) DO UPDATE SET "
                "families_json=excluded.families_json, "
                "line_name=COALESCE(excluded.line_name, dispatcher_line_config.line_name), "
                "mec_perf_inclinada=excluded.mec_perf_inclinada, "
                "sobre_medida_mecanizado=excluded.sobre_medida_mecanizado",
                (process, int(line_id), name, families_json, int(mec_perf_inclinada), int(sobre_medida_mecanizado)),
            )

            # Invalidate cached program for this process
            con.execute("DELETE FROM dispatcher_last_program WHERE process = ?", (process,))
        
        self.data_repo.log_audit("CONFIG", "Upsert Line", f"Proc: {process}, ID: {line_id}, Name: {name}, Fams: {len(families)}")

    def delete_dispatch_line(self, *, process: str = "terminaciones", line_id: int) -> None:
        process = self.data_repo._normalize_process(process)
        with self.db.connect() as con:
            con.execute("DELETE FROM dispatcher_line_config WHERE process = ? AND line_id = ?", (process, int(line_id)))
            con.execute("DELETE FROM dispatcher_last_program WHERE process = ?", (process,))
        
        self.data_repo.log_audit("CONFIG", "Delete Line", f"Proc: {process}, ID: {line_id}")

    # ---------- In-Progress Management ----------

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
        process = self.data_repo._normalize_process(process)
        with self.db.connect() as con:
            try:
                rows = con.execute(
                    "SELECT process, pedido, posicion, is_test, split_id, line_id, qty, marked_at FROM dispatcher_program_in_progress_item WHERE process=? ORDER BY marked_at ASC",
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
                    "SELECT process, pedido, posicion, is_test, line_id, marked_at FROM dispatcher_program_in_progress WHERE process=? ORDER BY marked_at ASC",
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

    def build_pinned_program_seed(
        self,
        *,
        process: str = "terminaciones",
        jobs: list[Job] | None = None,
        parts: list[Part] | None = None,
    ) -> tuple[dict[int, list[dict]], list[Job]]:
        """Build a pinned (in-progress) program seed and filter remaining jobs.

        This is used to improve dispatch quality: pinned items contribute to the initial
        line loads, so the scheduler balances the remaining jobs around the real execution.

        Returns:
            (pinned_program, remaining_jobs)
        """
        process = self.data_repo._normalize_process(process)

        jobs_in = list(jobs) if jobs is not None else self.get_jobs_model(process=process)
        parts_in = list(parts) if parts is not None else self.get_parts_model()

        parts_by_material: dict[str, Part] = {p.material: p for p in parts_in if getattr(p, "material", None)}

        locks = self.list_in_progress_locks(process=process)
        if not locks:
            return {}, jobs_in

        manual_set = set(self.data_repo.get_manual_priority_orderpos_set() or set())

        def _prio_kind_for(*, is_test: int, pedido: str, posicion: str) -> str:
            if int(is_test or 0) == 1:
                return "test"
            if (str(pedido), str(posicion)) in manual_set:
                return "priority"
            return "normal"

        def _key(pedido: str, posicion: str, is_test: int) -> tuple[str, str, int]:
            return (str(pedido).strip(), str(posicion).strip(), int(is_test or 0))

        locked_key_set = {_key(lk["pedido"], lk["posicion"], int(lk.get("is_test") or 0)) for lk in locks}
        remaining_jobs = [
            j
            for j in jobs_in
            if _key(j.pedido, j.posicion, 1 if bool(getattr(j, "is_test", False)) else 0) not in locked_key_set
        ]

        # Group jobs by lock key, so pinned quantities reflect current job universe.
        jobs_by_key: dict[tuple[str, str, int], list[Job]] = {}
        for j in jobs_in:
            k = _key(j.pedido, j.posicion, 1 if bool(getattr(j, "is_test", False)) else 0)
            jobs_by_key.setdefault(k, []).append(j)

        # Group locks by key so we can expand split_id.
        locks_by_key: dict[tuple[str, str, int], list[dict]] = {}
        for lk in locks:
            k = _key(lk["pedido"], lk["posicion"], int(lk.get("is_test") or 0))
            locks_by_key.setdefault(k, []).append(dict(lk))

        pinned_program: dict[int, list[dict]] = {}
        for k, group in locks_by_key.items():
            group_sorted = sorted(group, key=lambda d: (str(d.get("marked_at") or ""), int(d.get("split_id") or 1)))

            js = jobs_by_key.get(k) or []
            if not js:
                # If there is no job left for this key, we keep the lock in DB (legacy behavior)
                # but do not pre-seed anything.
                continue

            # Aggregate current truth for the order position.
            material = str(js[0].material)
            total_qty = int(sum(int(j.qty or 0) for j in js))
            cliente = str(js[0].cliente or "") if hasattr(js[0], "cliente") else ""
            fecha = None
            for j in js:
                if j.fecha_de_pedido is not None:
                    fecha = j.fecha_de_pedido
                    break
            # Corr range: best-effort min corr_min.
            corr_start = None
            corr_candidates = [int(j.corr_min) for j in js if j.corr_min is not None]
            if corr_candidates:
                corr_start = min(corr_candidates)
            if corr_start is None:
                corr_start = 1

            part = parts_by_material.get(material)
            family_id = (part.family_id if part else "Otros")

            # start_by consistent with scheduler formula (best-effort).
            start_by_iso = None
            fecha_iso = fecha.isoformat() if fecha else None
            if fecha and part:
                days = (part.vulcanizado_dias or 0) + (part.mecanizado_dias or 0) + (part.inspeccion_externa_dias or 0)
                start_by_iso = (fecha - timedelta(days=days)).isoformat()
            elif fecha:
                start_by_iso = fecha.isoformat()

            # Decide effective qty per split (qty=0 means "auto" and the last absorbs remaining).
            stored_qtys: list[int] = [max(0, int(it.get("qty") or 0)) for it in group_sorted]
            effective_qtys: list[int] = [0] * len(stored_qtys)
            remaining = total_qty
            for i in range(len(group_sorted)):
                is_last = i == (len(group_sorted) - 1)
                q_stored = stored_qtys[i]
                if is_last:
                    q_eff = max(0, remaining)
                else:
                    q_eff = max(0, min(q_stored, remaining)) if q_stored > 0 else 0
                effective_qtys[i] = q_eff
                remaining -= q_eff
            if remaining > 0 and effective_qtys:
                effective_qtys[-1] += remaining

            corr_cursor = int(corr_start)
            prio_kind = _prio_kind_for(is_test=k[2], pedido=k[0], posicion=k[1])
            for it, q_eff in zip(group_sorted, effective_qtys):
                line_id = int(it.get("line_id") or 0)
                split_id = int(it.get("split_id") or 1)

                row: dict = {
                    "pedido": k[0],
                    "posicion": k[1],
                    "cliente": cliente,
                    "material": material,
                    "numero_parte": material[-5:] if len(material) >= 5 else material,
                    "cantidad": int(q_eff),
                    "prio_kind": prio_kind,
                    "is_test": int(k[2]),
                    "in_progress": 1,
                    "family_id": family_id,
                    "familia": family_id,
                    "fecha_de_pedido": fecha_iso,
                    "start_by": start_by_iso,
                    "_pt_split_id": int(split_id),
                }

                if q_eff > 0:
                    row["corr_inicio"] = int(corr_cursor)
                    row["corr_fin"] = int(corr_cursor + int(q_eff) - 1)
                    corr_cursor += int(q_eff)
                else:
                    row["corr_inicio"] = int(corr_cursor)
                    row["corr_fin"] = int(corr_cursor)

                row["_row_id"] = (
                    f"{k[0]}|{k[1]}|{material}|split{split_id}|{int(row['corr_inicio'])}-{int(row['corr_fin'])}"
                )

                pinned_program.setdefault(line_id, []).append(row)

        return pinned_program, remaining_jobs

    def _refresh_program_with_locks(self, process: str) -> None:
        """Update dispatcher_last_program in-place with current locks, avoiding full regen."""
        last = self.load_last_program(process=process)

        if last is None:
            # No cache to update; delete to ensure next load generates fresh
            with self.db.connect() as con:
                con.execute("DELETE FROM dispatcher_last_program WHERE process = ?", (process,))
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
                "INSERT INTO dispatcher_last_program(process, program_json, generated_on) VALUES(?, ?, ?) "
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
        split_id: int | None = None,
        qty: int | None = None,
    ) -> None:
        process = self.data_repo._normalize_process(process)
        pedido_s = str(pedido).strip()
        posicion_s = str(posicion).strip()
        is_test_i = int(is_test or 0)
        if not pedido_s or not posicion_s:
            raise ValueError("Pedido/posición inválidos")
        marked_at = datetime.now().isoformat(timespec="seconds")
        
        # Use provided split_id or default to 1
        split_id_final = int(split_id) if split_id is not None else 1
        qty_final = int(qty) if qty is not None else 0
        
        with self.db.connect() as con:
            # Split-aware: create/update with provided split_id and qty
            try:
                con.execute(
                    "INSERT INTO dispatcher_program_in_progress_item(process, pedido, posicion, is_test, split_id, line_id, qty, marked_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(process, pedido, posicion, is_test, split_id) DO UPDATE SET "
                    "line_id=excluded.line_id, qty=excluded.qty, marked_at=dispatcher_program_in_progress_item.marked_at",
                    (process, pedido_s, posicion_s, is_test_i, split_id_final, int(line_id), qty_final, marked_at),
                )
            except Exception:
                # Backward-compatible fallback.
                con.execute(
                    "INSERT INTO dispatcher_program_in_progress(process, pedido, posicion, is_test, line_id, marked_at) VALUES(?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(process, pedido, posicion, is_test) DO UPDATE SET "
                    "line_id=excluded.line_id, marked_at=dispatcher_program_in_progress.marked_at",
                    (process, pedido_s, posicion_s, is_test_i, int(line_id), marked_at),
                )
            
        self.data_repo.log_audit(
            "PROGRAM_UPDATE",
            "Mark In-Progress",
            f"Pedido {pedido_s}/{posicion_s} -> Line {line_id} (Test: {is_test_i}, Split: {split_id_final})"
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
        split_id: int | None = None,
    ) -> None:
        process = self.data_repo._normalize_process(process)
        with self.db.connect() as con:
            pedido_s = str(pedido).strip()
            posicion_s = str(posicion).strip()
            is_test_i = int(is_test or 0)
            
            if split_id is not None:
                # Delete only specific split
                try:
                    con.execute(
                        "DELETE FROM dispatcher_program_in_progress_item WHERE process=? AND pedido=? AND posicion=? AND is_test=? AND split_id=?",
                        (process, pedido_s, posicion_s, is_test_i, int(split_id)),
                    )
                except Exception:
                    pass
            else:
                # Delete all splits for this order/position
                try:
                    con.execute(
                        "DELETE FROM dispatcher_program_in_progress_item WHERE process=? AND pedido=? AND posicion=? AND is_test=?",
                        (process, pedido_s, posicion_s, is_test_i),
                    )
                except Exception:
                    pass

                # Legacy cleanup.
                try:
                    con.execute(
                        "DELETE FROM dispatcher_program_in_progress WHERE process=? AND pedido=? AND posicion=? AND is_test=?",
                        (process, pedido_s, posicion_s, is_test_i),
                    )
                except Exception:
                    pass
            
        self.data_repo.log_audit(
            "PROGRAM_UPDATE",
            "Unmark In-Progress",
            f"Pedido {pedido_s}/{posicion_s} (Test: {is_test_i}, Split: {split_id or 'all'})"
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
        process = self.data_repo._normalize_process(process)
        pedido_s = str(pedido).strip()
        posicion_s = str(posicion).strip()
        is_test_i = int(is_test or 0)
        if not pedido_s or not posicion_s:
            raise ValueError("Pedido/posición inválidos")

        allow = str(self.data_repo.get_config(key="ui_allow_move_in_progress_line", default="0")).strip()
        if allow != "1":
            raise ValueError("Movimiento manual deshabilitado por configuración (ui_allow_move_in_progress_line)")

        audit_target = None
        audit_details = None

        with self.db.connect() as con:
            try:
                if split_id is None:
                    con.execute(
                        "UPDATE dispatcher_program_in_progress_item SET line_id=? WHERE process=? AND pedido=? AND posicion=? AND is_test=?",
                        (int(line_id), process, pedido_s, posicion_s, is_test_i),
                    )
                else:
                    con.execute(
                        "UPDATE dispatcher_program_in_progress_item SET line_id=? WHERE process=? AND pedido=? AND posicion=? AND is_test=? AND split_id=?",
                        (int(line_id), process, pedido_s, posicion_s, is_test_i, int(split_id)),
                    )
                
                audit_target = "Move Line"
                audit_details = f"Pedido {pedido_s}/{posicion_s} -> Line {line_id} (Split: {split_id or 'ALL'})"
                
            except Exception:
                # Backward-compatible fallback.
                con.execute(
                    "UPDATE dispatcher_program_in_progress SET line_id=? WHERE process=? AND pedido=? AND posicion=? AND is_test=?",
                    (int(line_id), process, pedido_s, posicion_s, is_test_i),
                )
                
                audit_target = "Move Line (Legacy)"
                audit_details = f"Pedido {pedido_s}/{posicion_s} -> Line {line_id}"
        
        if audit_target:
            self.data_repo.log_audit("PROGRAM_UPDATE", audit_target, audit_details)

        # Outside transaction
        self._refresh_program_with_locks(process=process)

    def create_balanced_split(
        self,
        *,
        process: str = "terminaciones",
        pedido: str,
        posicion: str,
        is_test: int = 0,
        line_id: int | None = None,
        qty: int | None = None,
    ) -> None:
        """Split an in-progress order position into two balanced parts.

        The split allocates quantities and correlativos sequentially during program merge.
        This method only persists the split (split_id + qty); line movement is handled
        separately (UI can move one split to another line).
        """
        process = self.data_repo._normalize_process(process)
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
        qty = int(order.cantidad)
        if qty < 2:
            raise ValueError("No se puede dividir: cantidad < 2")

        qty1 = qty // 2
        qty2 = qty - qty1
        if qty1 <= 0 or qty2 <= 0:
            raise ValueError("Split inválido")

        now = datetime.now().isoformat(timespec="seconds")
        with self.db.connect() as con:
            try:
                # Ensure there is at least split_id=1 (carry its line_id/marked_at).
                row = con.execute(
                    "SELECT line_id, marked_at FROM dispatcher_program_in_progress_item WHERE process=? AND pedido=? AND posicion=? AND is_test=? AND split_id=1",
                    (process, pedido_s, posicion_s, is_test_i),
                ).fetchone()
                if row is None:
                    # If not marked, default to line 1 (UI normally marks first).
                    con.execute(
                        "INSERT OR IGNORE INTO dispatcher_program_in_progress_item(process, pedido, posicion, is_test, split_id, line_id, qty, marked_at) VALUES(?, ?, ?, ?, 1, 1, 0, ?)",
                        (process, pedido_s, posicion_s, is_test_i, now),
                    )
                    row = con.execute(
                        "SELECT line_id, marked_at FROM dispatcher_program_in_progress_item WHERE process=? AND pedido=? AND posicion=? AND is_test=? AND split_id=1",
                        (process, pedido_s, posicion_s, is_test_i),
                    ).fetchone()

                line_id = int(row[0])

                existing = con.execute(
                    "SELECT COUNT(*) FROM dispatcher_program_in_progress_item WHERE process=? AND pedido=? AND posicion=? AND is_test=?",
                    (process, pedido_s, posicion_s, is_test_i),
                ).fetchone()
                if int(existing[0] or 0) != 1:
                    raise ValueError("Ya existe un split (o múltiples partes) para esta fila")

                con.execute(
                    "UPDATE dispatcher_program_in_progress_item SET qty=? WHERE process=? AND pedido=? AND posicion=? AND is_test=? AND split_id=1",
                    (int(qty1), process, pedido_s, posicion_s, is_test_i),
                )
                con.execute(
                    "INSERT INTO dispatcher_program_in_progress_item(process, pedido, posicion, is_test, split_id, line_id, qty, marked_at) VALUES(?, ?, ?, ?, 2, ?, ?, ?)",
                    (process, pedido_s, posicion_s, is_test_i, int(line_id), int(qty2), now),
                )
            except Exception:
                # If split table isn't available, we cannot support splits.
                raise
        
        self.data_repo.log_audit(
            "PROGRAM_UPDATE",
            "Split Created",
            f"Pedido {pedido_s}/{posicion_s} -> Sizes {qty1}, {qty2}"
        )
        
        # Outside transaction
        self._refresh_program_with_locks(process=process)

    def delete_balanced_split(
        self,
        *,
        process: str = "terminaciones",
        pedido: str,
        posicion: str,
        is_test: int = 0,
        split_id: int,
    ) -> None:
        """Delete a specific split. If only one split remains, it becomes the unsplit default."""
        process = self.data_repo._normalize_process(process)
        pedido_s = str(pedido).strip()
        posicion_s = str(posicion).strip()
        is_test_i = int(is_test or 0)

        with self.db.connect() as con:
            try:
                con.execute(
                    "DELETE FROM dispatcher_program_in_progress_item WHERE process=? AND pedido=? AND posicion=? AND is_test=? AND split_id=?",
                    (process, pedido_s, posicion_s, is_test_i, int(split_id)),
                )
            except Exception:
                pass
        
        self.data_repo.log_audit(
            "PROGRAM_UPDATE",
            "Split Deleted",
            f"Pedido {pedido_s}/{posicion_s} Split {split_id}"
        )
        
        self._refresh_program_with_locks(process=process)

    def set_split_qty(
        self,
        *,
        process: str = "terminaciones",
        pedido: str,
        posicion: str,
        is_test: int = 0,
        split_id: int,
        qty: int,
    ) -> None:
        """Manually set qty for a split. Set qty=0 to auto-fill remaining."""
        process = self.data_repo._normalize_process(process)
        pedido_s = str(pedido).strip()
        posicion_s = str(posicion).strip()
        is_test_i = int(is_test or 0)
        
        with self.db.connect() as con:
            try:
                con.execute(
                    "UPDATE dispatcher_program_in_progress_item SET qty=? WHERE process=? AND pedido=? AND posicion=? AND is_test=? AND split_id=?",
                    (int(qty), process, pedido_s, posicion_s, is_test_i, int(split_id)),
                )
            except Exception:
                raise ValueError("No se pudo actualizar split qty")
        
        self.data_repo.log_audit(
            "PROGRAM_UPDATE",
            "Set Split Qty",
            f"Pedido {pedido_s}/{posicion_s} Split {split_id} -> Qty {qty}"
        )
        
        self._refresh_program_with_locks(process=process)

    # ---------- Program Persistence ----------

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

        process = self.data_repo._normalize_process(process)
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

        manual_set = self.data_repo.get_manual_priority_orderpos_set()

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
                    "fecha_de_pedido": o.fecha_de_pedido.isoformat(),
                    "start_by": o.fecha_de_pedido.isoformat(),
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
        process = self.data_repo._normalize_process(process)
        merged_program, merged_errors = self._apply_in_progress_locks(process=process, program=program, errors=list(errors or []))
        payload = json.dumps({"program": merged_program, "errors": list(merged_errors or [])})
        generated_on = datetime.now().isoformat(timespec="seconds")
        with self.db.connect() as con:
            con.execute(
                "INSERT INTO dispatcher_last_program(process, generated_on, program_json) VALUES(?, ?, ?) "
                "ON CONFLICT(process) DO UPDATE SET generated_on=excluded.generated_on, program_json=excluded.program_json",
                (process, generated_on, payload),
            )
        
        # Audit log
        total_items = sum(len(lines) for lines in merged_program.values())
        err_items = len(merged_errors or [])
        self.data_repo.log_audit(
            "PROGRAM_GEN",
            "Program Saved",
            f"Process: {process}, Scheduled: {total_items}, Errors: {err_items}"
        )

    def load_last_program(self, *, process: str = "terminaciones") -> dict | None:
        process = self.data_repo._normalize_process(process)
        with self.db.connect() as con:
            row = con.execute("SELECT generated_on, program_json FROM dispatcher_last_program WHERE process=?", (process,)).fetchone()
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

    def split_job(self, *, job_id: str, qty_split: int) -> tuple[str, str]:
        """Split a job into two jobs."""
        from uuid import uuid4
        
        with self.db.connect() as con:
            original = con.execute(
                """
                SELECT job_id, process_id, pedido, posicion, material, qty,
                       priority, is_test, state, fecha_de_pedido, notes
                FROM dispatcher_job
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
            
            if not original:
                raise ValueError(f"Job {job_id} not found")
            
            original_qty = int(original["qty"])
            
            if qty_split <= 0:
                raise ValueError(f"qty_split must be positive, got {qty_split}")
            if qty_split >= original_qty:
                raise ValueError(f"qty_split ({qty_split}) must be less than job qty ({original_qty})")
            
            new_qty = original_qty - qty_split
            
            process_id = str(original["process_id"])
            new_job_id = f"job_{process_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:8]}"
            
            con.execute(
                """
                INSERT INTO dispatcher_job(
                    job_id, process_id, pedido, posicion, material,
                    qty, priority, is_test, state, fecha_de_pedido, notes,
                    created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    new_job_id,
                    process_id,
                    original["pedido"],
                    original["posicion"],
                    original["material"],
                    new_qty,
                    original["priority"],
                    original["is_test"],
                    original["state"],
                    original["fecha_de_pedido"],
                    original["notes"],
                ),
            )
            
            con.execute(
                """
                UPDATE dispatcher_job
                SET qty = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = ?
                """,
                (qty_split, job_id),
            )
            
            job_units = con.execute(
                """
                SELECT lote, correlativo_int, qty, status
                FROM dispatcher_job_unit
                WHERE job_id = ?
                ORDER BY correlativo_int, lote
                """,
                (job_id,),
            ).fetchall()
            
            units_to_move = job_units[qty_split:]
            
            for unit in units_to_move:
                con.execute(
                    "DELETE FROM dispatcher_job_unit WHERE job_id = ? AND lote = ?",
                    (job_id, unit["lote"]),
                )
                
                new_unit_id = f"ju_{new_job_id}_{uuid4().hex[:8]}"
                con.execute(
                    """
                    INSERT INTO dispatcher_job_unit(
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
        priority_map_str = self.data_repo.get_config(key="job_priority_map", default='{"prueba": 1, "urgente": 2, "normal": 3}')
        try:
            priority_map = json.loads(priority_map_str) if isinstance(priority_map_str, str) else priority_map_str
        except Exception:
            priority_map = {"prueba": 1, "urgente": 2, "normal": 3}
        return {k: int(v) for k, v in priority_map.items()}

    def mark_job_urgent(self, job_id: str) -> None:
        """Mark a job as urgent."""
        with self.db.connect() as con:
            row = con.execute("SELECT is_test FROM dispatcher_job WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                raise ValueError(f"Job not found: {job_id}")
            if row["is_test"]:
                raise ValueError("Cannot change priority of a test job")
                
            priorities = self._get_priority_map_values()
            urgent_prio = priorities.get("urgente", 2)
            
            con.execute("UPDATE dispatcher_job SET priority = ?, updated_at = CURRENT_TIMESTAMP WHERE job_id = ?", (urgent_prio, job_id))

    def unmark_job_urgent(self, job_id: str) -> None:
        """Unmark a job as urgent (return to normal)."""
        with self.db.connect() as con:
            row = con.execute("SELECT is_test FROM dispatcher_job WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                raise ValueError(f"Job not found: {job_id}")
            if row["is_test"]:
                raise ValueError("Cannot change priority of a test job")
                
            priorities = self._get_priority_map_values()
            normal_prio = priorities.get("normal", 3)
            
            con.execute("UPDATE dispatcher_job SET priority = ?, updated_at = CURRENT_TIMESTAMP WHERE job_id = ?", (normal_prio, job_id))
