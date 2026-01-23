from __future__ import annotations

import asyncio
from datetime import date, datetime

from nicegui import ui

from plannerterm.core.scheduler import generate_program
from plannerterm.data.repository import Repository
from plannerterm.ui.widgets import page_container, render_line_tables, render_nav


def register_pages(repo: Repository) -> None:
    def auto_generate_and_save(*, process: str = "terminaciones", notify: bool = True) -> bool:
        process = str(process or "terminaciones").strip().lower()
        updated = False
        try:
            if repo.count_orders(process=process) == 0:
                return False
            if repo.count_missing_parts_from_orders(process=process) > 0:
                return False
            if repo.count_missing_process_times_from_orders(process=process) > 0:
                return False
            if len(repo.get_lines(process=process)) == 0:
                return False
            lines = repo.get_lines_model(process=process)
            orders = repo.get_orders_model(process=process)
            parts = repo.get_parts_model()
            manual_set = repo.get_manual_priority_orderpos_set()
            program, errors = generate_program(
                lines=lines,
                orders=orders,
                parts=parts,
                priority_orderpos=manual_set,
            )
            repo.save_last_program(process=process, program=program, errors=errors)
            updated = True
            if notify:
                label = (repo.processes.get(process, {}) or {}).get("label", process)
                ui.notify(f"Programa actualizado automáticamente ({label})")
        except Exception as ex:
            ui.notify(f"Error actualizando programa: {ex}", color="negative")
            return False

        return updated

    def auto_generate_and_save_all(*, notify: bool = False) -> list[str]:
        updated: list[str] = []
        for p in list(repo.processes.keys()):
            if auto_generate_and_save(process=p, notify=False):
                updated.append(p)
        if notify and updated:
            labels = [((repo.processes.get(p, {}) or {}).get("label", p)) for p in updated]
            ui.notify(f"Programas actualizados: {', '.join(labels)}")
        return updated

    async def refresh_from_sap_all(*, notify: bool = True) -> None:
        """Best-effort: rebuild orders per process (from current MB52+Visión+almacenes) then regenerate programs."""
        rebuilt: list[str] = []
        updated: list[str] = []
        for p in list(repo.processes.keys()):
            try:
                ok = await asyncio.to_thread(lambda pp=p: repo.try_rebuild_orders_from_sap_for(process=pp))
                if ok:
                    rebuilt.append(p)
            except Exception:
                # Keep going even if one process has missing config.
                continue

        for p in rebuilt:
            try:
                if auto_generate_and_save(process=p, notify=False):
                    updated.append(p)
            except Exception:
                continue

        if notify:
            if updated:
                labels = [((repo.processes.get(p, {}) or {}).get("label", p)) for p in updated]
                ui.notify(f"Programas actualizados: {', '.join(labels)}")
            else:
                ui.notify("Datos SAP actualizados. Programas no regenerados (faltan líneas/maestro/tiempos).", color="warning")

    def kick_refresh_from_sap_all(*, notify: bool = True) -> None:
        async def _runner() -> None:
            await refresh_from_sap_all(notify=notify)

        asyncio.create_task(_runner())

    @ui.page("/")
    def dashboard() -> None:
        render_nav()
        with page_container():
            ui.label("Home").classes("text-2xl font-semibold")
            ui.separator()

            kpi_rows = repo.get_vision_kpi_daily_rows(limit=180)
            dates: list[str] = []
            atrasadas: list[float] = []

            ui.label("Histórico (Visión Planta): toneladas atrasadas").classes("text-lg font-semibold")
            ui.label("Snapshot diario cuando se sube Visión Planta.").classes("text-sm text-slate-600")

            if kpi_rows:
                last = kpi_rows[-1]
                last_at = str(last.get("snapshot_at") or "").strip()
                last_date = str(last.get("snapshot_date") or "").strip()
                if last_at or last_date:
                    ui.label(f"Última actualización Visión Planta: {last_at or last_date}").classes(
                        "text-sm text-slate-600"
                    )

                for r in kpi_rows:
                    r["tons_atrasadas_fmt"] = f"{float(r.get('tons_atrasadas') or 0.0):,.1f}"
                    r["tons_por_entregar_fmt"] = f"{float(r.get('tons_por_entregar') or 0.0):,.1f}"

                dates = [str(r.get("snapshot_date") or "") for r in kpi_rows]
                atrasadas = [float(r.get("tons_atrasadas") or 0.0) for r in kpi_rows]
            else:
                ui.label("Sin datos aún: sube Visión Planta en /actualizar para comenzar el histórico.").classes(
                    "text-sm text-slate-500"
                )

            ui.echart(
                {
                    "tooltip": {"trigger": "axis"},
                    "grid": {"left": 45, "right": 20, "top": 30, "bottom": 45},
                    "xAxis": {"type": "category", "data": dates},
                    "yAxis": {"type": "value", "name": "tons"},
                    "series": [
                        {
                            "name": "Tons atrasadas",
                            "type": "line",
                            "data": atrasadas,
                            "smooth": True,
                            "areaStyle": {},
                        }
                    ],
                }
            ).classes("w-full")

            ui.separator()

            overdue = repo.get_orders_overdue_rows(limit=200)
            due_soon = repo.get_orders_due_soon_rows(days=14, limit=200)

            overdue_tons = sum(float(r.get("tons") or 0.0) for r in overdue)
            due_soon_tons = sum(float(r.get("tons") or 0.0) for r in due_soon)

            # Pre-format tons for display (1 decimal) while keeping numeric `tons` for calculations.
            for r in overdue:
                r["tons_fmt"] = f"{float(r.get('tons') or 0.0):,.1f}"
            for r in due_soon:
                r["tons_fmt"] = f"{float(r.get('tons') or 0.0):,.1f}"

            with ui.row().classes("w-full gap-4 items-stretch"):
                with ui.card().classes("p-4 w-full"):
                    ui.label(f"Pedidos atrasados — Total: {overdue_tons:,.1f} tons").classes("text-lg font-semibold")
                    ui.label("Fecha de entrega anterior a hoy.").classes("text-sm text-slate-600")

                    def _pick_row(args) -> dict | None:
                        # Recursive search for 'row' dict in nested event args.
                        def _walk(obj):
                            if isinstance(obj, dict):
                                yield obj
                                for v_ in obj.values():
                                    yield from _walk(v_)
                            elif isinstance(obj, (list, tuple)):
                                for it in obj:
                                    yield from _walk(it)

                        row_found: dict | None = None
                        if args is not None:
                            for d in _walk(args):
                                if isinstance(d, dict):
                                    if isinstance(d.get("row"), dict):
                                        row_found = d.get("row")
                                        break
                                    # Also check if this dict itself is the row (has pedido/posicion).
                                    if row_found is None and "pedido" in d and "posicion" in d:
                                        row_found = d
                                        break
                        return row_found

                    def _open_vision_breakdown(row: dict) -> None:
                        pedido = str(row.get("pedido") or "").strip()
                        posicion = str(row.get("posicion") or "").strip()
                        if not pedido or not posicion:
                            ui.notify("Fila inválida (sin pedido/posición)", color="warning")
                            return
                        try:
                            data = repo.get_vision_stage_breakdown(pedido=pedido, posicion=posicion)
                        except Exception as ex:
                            ui.notify(f"No se pudo leer Visión: {ex}", color="negative")
                            return

                        dialog = ui.dialog().props("persistent")
                        with dialog:
                            with ui.card().classes("bg-white p-6").style("width: 92vw; max-width: 720px"):
                                title = f"Pedido {pedido} / {posicion}"
                                ui.label(title).classes("text-xl font-semibold")
                                if int(data.get("found") or 0) == 0:
                                    ui.label("No hay datos de Visión Planta para este pedido/posición.").classes(
                                        "text-slate-600"
                                    )
                                else:
                                    meta = " · ".join(
                                        [
                                            p
                                            for p in [
                                                (str(data.get("cliente") or "").strip() or None),
                                                (str(data.get("cod_material") or "").strip() or None),
                                                (str(data.get("fecha_entrega") or "").strip() or None),
                                            ]
                                            if p
                                        ]
                                    )
                                    if meta:
                                        ui.label(meta).classes("text-sm text-slate-600")

                                    stages = list(data.get("stages") or [])
                                    for r in stages:
                                        v = r.get("piezas")
                                        r["piezas_fmt"] = str(int(v or 0))

                                    ui.table(
                                        columns=[
                                            {"name": "estado", "label": "Estado", "field": "estado"},
                                            {"name": "piezas", "label": "Piezas", "field": "piezas_fmt"},
                                        ],
                                        rows=stages,
                                        row_key="_row_id",
                                    ).classes("w-full").props("dense flat bordered")

                                with ui.row().classes("w-full justify-end mt-2"):
                                    ui.button("Cerrar", on_click=dialog.close).props("flat")

                        dialog.open()

                    if overdue:
                        tbl_overdue = ui.table(
                            columns=[
                                {"name": "cliente", "label": "Cliente", "field": "cliente"},
                                {"name": "pedido", "label": "Pedido", "field": "pedido"},
                                {"name": "posicion", "label": "Pos.", "field": "posicion"},
                                {"name": "numero_parte", "label": "Parte", "field": "numero_parte"},
                                {"name": "solicitado", "label": "Solicitado", "field": "solicitado"},
                                {"name": "pendientes", "label": "Pendientes", "field": "pendientes"},
                                {"name": "tons", "label": "Tons por Entregar", "field": "tons_fmt"},
                                {"name": "fecha_entrega", "label": "Entrega", "field": "fecha_entrega"},
                                {"name": "dias", "label": "Días atraso", "field": "dias"},
                            ],
                            rows=overdue,
                            row_key="_row_id",
                        ).classes("w-full").props("dense flat bordered")

                        # Double click to show Visión Planta breakdown by stage.
                        def _on_overdue_dblclick(e) -> None:
                            r = _pick_row(getattr(e, "args", None))
                            if r is not None:
                                _open_vision_breakdown(r)
                            else:
                                ui.notify("No se pudo leer la fila seleccionada", color="negative")

                        tbl_overdue.on("rowDblClick", _on_overdue_dblclick)
                        tbl_overdue.on("rowDblclick", _on_overdue_dblclick)
                    else:
                        ui.label("No hay pedidos atrasados.").classes("text-slate-600")

                with ui.card().classes("p-4 w-full"):
                    ui.label(f"Próximas 2 semanas — Total: {due_soon_tons:,.1f} tons").classes("text-lg font-semibold")
                    ui.label("Pedidos con entrega entre hoy y 14 días.").classes("text-sm text-slate-600")
                    if due_soon:
                        ui.table(
                            columns=[
                                {"name": "cliente", "label": "Cliente", "field": "cliente"},
                                {"name": "pedido", "label": "Pedido", "field": "pedido"},
                                {"name": "posicion", "label": "Pos.", "field": "posicion"},
                                {"name": "numero_parte", "label": "Parte", "field": "numero_parte"},
                                {"name": "solicitado", "label": "Solicitado", "field": "solicitado"},
                                {"name": "pendientes", "label": "Pendientes", "field": "pendientes"},
                                {"name": "tons", "label": "Tons por Entregar", "field": "tons_fmt"},
                                {"name": "fecha_entrega", "label": "Entrega", "field": "fecha_entrega"},
                                {"name": "dias", "label": "Días restantes", "field": "dias"},
                            ],
                            rows=due_soon,
                            row_key="_row_id",
                        ).classes("w-full").props("dense flat bordered")
                    else:
                        ui.label("No hay pedidos dentro de las próximas 2 semanas.").classes("text-slate-600")

            ui.separator()
            ui.label("Carga por almacén (proceso)").classes("text-lg font-semibold")
            ui.label(
                "Piezas desde órdenes derivadas; tons desde Visión Planta ((peso neto kg / 1000) / solicitado)."
            ).classes(
                "text-sm text-slate-600"
            )

            load_rows = repo.get_process_load_rows()
            if load_rows:
                for r in load_rows:
                    r["tons_fmt"] = f"{float(r.get('tons') or 0.0):,.1f}"
                ui.table(
                    columns=[
                        {"name": "proceso", "label": "Proceso", "field": "proceso"},
                        {"name": "almacen", "label": "Almacén", "field": "almacen"},
                        {"name": "orderpos", "label": "Pedidos/Pos", "field": "orderpos"},
                        {"name": "piezas", "label": "Piezas", "field": "piezas"},
                        {"name": "tons", "label": "Tons", "field": "tons_fmt"},
                        {"name": "piezas_sin_peso", "label": "Piezas sin peso", "field": "piezas_sin_peso"},
                    ],
                    rows=load_rows,
                    row_key="_row_id",
                ).classes("w-full").props("dense flat bordered")
            else:
                ui.label("Aún no hay órdenes cargadas.").classes("text-slate-600")

    @ui.page("/avance")
    def avance() -> None:
        render_nav(active="avance")
        with page_container():
            ui.label("Avance (MB52)").classes("text-2xl font-semibold")
            ui.label(
                "Reporte de salidas (brutas) desde MB52 vs la carga anterior, mapeadas al último programa de Terminaciones."
            ).classes("pt-subtitle")
            ui.separator()

            rep = repo.load_mb52_progress_last(process="terminaciones")
            if rep is None:
                ui.label("Aún no hay reporte. Sube MB52 (modo replace) para generarlo.").classes("text-slate-600")
                return

            gen = str(rep.get("generated_on") or "").strip()
            base = str(rep.get("program_generated_on") or "").strip()
            prev_n = int(rep.get("mb52_prev_count") or 0)
            curr_n = int(rep.get("mb52_curr_count") or 0)
            if gen:
                ui.label(f"Última actualización avance: {gen}").classes("text-slate-600")
            if base:
                ui.label(f"Programa base: {base}").classes("text-slate-600")
            ui.label(f"MB52 usable (prev/curr): {prev_n} / {curr_n}").classes("text-slate-600")
            ui.separator()

            lines = rep.get("lines") or {}

            def _format_lotes_range(row: dict) -> str:
                a = row.get("corr_inicio")
                b = row.get("corr_fin")
                try:
                    ai = int(a)
                    bi = int(b)
                except Exception:
                    return ""
                ai_s = str(ai % 10000).zfill(4)
                bi_s = str(bi % 10000).zfill(4)
                if ai_s.startswith("0"):
                    ai_s = ai_s[1:]
                if bi_s.startswith("0"):
                    bi_s = bi_s[1:]
                return ai_s if ai_s == bi_s else f"{ai_s}-{bi_s}"

            # Render by line, like the program.
            with ui.element("div").classes("w-full grid gap-4 grid-cols-1 md:grid-cols-2 xl:grid-cols-4 items-stretch"):
                for raw_line_id, items in sorted(lines.items(), key=lambda kv: int(kv[0]) if str(kv[0]).isdigit() else 10**9):
                    try:
                        line_id = int(raw_line_id)
                    except Exception:
                        continue
                    rows = list(items or [])
                    for r in rows:
                        r["lotes_rango"] = _format_lotes_range(r)

                    with ui.card().classes("w-full h-full flex flex-col"):
                        ui.label(f"Línea {line_id}").classes("text-xl font-semibold")
                        if not rows:
                            ui.label("(sin salidas)").classes("text-gray-500")
                            continue
                        ui.table(
                            columns=[
                                {"name": "prio_kind", "label": "", "field": "prio_kind"},
                                {"name": "pedido", "label": "Pedido", "field": "pedido"},
                                {"name": "posicion", "label": "Pos.", "field": "posicion"},
                                {"name": "lotes_rango", "label": "Lotes", "field": "lotes_rango"},
                                {"name": "numero_parte", "label": "Parte", "field": "numero_parte"},
                                {"name": "cantidad", "label": "Cantidad", "field": "cantidad"},
                                {"name": "salio", "label": "Salió", "field": "salio"},
                                {"name": "fecha_entrega", "label": "Entrega", "field": "fecha_entrega"},
                            ],
                            rows=rows,
                            row_key="_row_id",
                        ).classes("w-full").props("dense flat bordered separator=cell wrap-cells")

            unplanned = list(rep.get("unplanned") or [])
            if unplanned:
                ui.separator()
                ui.label("Salidas no programadas").classes("text-xl font-semibold")
                ui.label(
                    "Salidas MB52 que no calzan con ningún rango del programa (por pedido/posición)."
                ).classes("text-slate-600")
                ui.table(
                    columns=[
                        {"name": "pedido", "label": "Pedido", "field": "pedido"},
                        {"name": "posicion", "label": "Pos.", "field": "posicion"},
                        {"name": "salio", "label": "Salió", "field": "salio"},
                    ],
                    rows=unplanned,
                    row_key="_row_id",
                ).classes("w-full").props("dense flat bordered separator=cell wrap-cells")

    @ui.page("/config")
    def config_lines() -> None:
        render_nav(active="config_lineas")
        with page_container():
            ui.label("Parámetros").classes("text-2xl font-semibold")
            ui.label("Configura Centro/Almacén (SAP) y las familias permitidas por línea.").classes("pt-subtitle")

            ui.separator()
            ui.label("Parámetros SAP").classes("text-lg font-semibold")
            with ui.row().classes("items-end w-full gap-3"):
                centro_in = ui.input(
                    "Centro",
                    value=repo.get_config(key="sap_centro", default="4000") or "4000",
                ).classes("w-40")
                almacen_in = ui.input(
                    "Almacén terminaciones",
                    value=repo.get_config(key="sap_almacen_terminaciones", default="4035") or "4035",
                ).classes("w-64")
                prefixes_in = ui.input(
                    "Prefijos material (MB52)",
                    value=repo.get_config(key="sap_material_prefixes", default="436") or "436",
                    placeholder="Ej: 436  | o '436,437' | o '*' (sin filtro)",
                ).classes("w-80")

            ui.separator()
            ui.label("Almacenes por proceso").classes("text-lg font-semibold")
            ui.label("Se usan para filtrar MB52 al reconstruir rangos por proceso.").classes("text-sm text-slate-600")

            ui.separator()
            ui.label("Parámetros UI").classes("text-lg font-semibold")
            allow_move_line_chk = ui.checkbox(
                "Habilitar mover filas 'en proceso' de línea",
                value=str(repo.get_config(key="ui_allow_move_in_progress_line", default="0") or "0").strip() == "1",
            )

            with ui.row().classes("items-end w-full gap-3 flex-wrap"):
                dura_in = ui.input(
                    "Toma de dureza",
                    value=(
                        repo.get_config(
                            key="sap_almacen_toma_dureza",
                            default=(repo.get_config(key="sap_almacen_terminaciones", default="4035") or "4035"),
                        )
                        or "4035"
                    ),
                ).classes("w-56")
                mec_in = ui.input(
                    "Mecanizado",
                    value=repo.get_config(key="sap_almacen_mecanizado", default="4049") or "4049",
                ).classes("w-56")
                mec_ext_in = ui.input(
                    "Mecanizado externo",
                    value=repo.get_config(key="sap_almacen_mecanizado_externo", default="4050") or "4050",
                ).classes("w-56")
                insp_ext_in = ui.input(
                    "Inspección externa",
                    value=repo.get_config(key="sap_almacen_inspeccion_externa", default="4046") or "4046",
                ).classes("w-56")
                por_vulc_in = ui.input(
                    "Por vulcanizar",
                    value=repo.get_config(key="sap_almacen_por_vulcanizar", default="4047") or "4047",
                ).classes("w-56")
                en_vulc_in = ui.input(
                    "En vulcanizado",
                    value=repo.get_config(key="sap_almacen_en_vulcanizado", default="4048") or "4048",
                ).classes("w-56")

                def save_cfg() -> None:
                    repo.set_config(key="sap_centro", value=str(centro_in.value or "").strip())
                    repo.set_config(key="sap_almacen_terminaciones", value=str(almacen_in.value or "").strip())
                    repo.set_config(key="sap_material_prefixes", value=str(prefixes_in.value or "").strip())
                    repo.set_config(key="sap_almacen_toma_dureza", value=str(dura_in.value or "").strip())
                    repo.set_config(key="sap_almacen_mecanizado", value=str(mec_in.value or "").strip())
                    repo.set_config(key="sap_almacen_mecanizado_externo", value=str(mec_ext_in.value or "").strip())
                    repo.set_config(key="sap_almacen_inspeccion_externa", value=str(insp_ext_in.value or "").strip())
                    repo.set_config(key="sap_almacen_por_vulcanizar", value=str(por_vulc_in.value or "").strip())
                    repo.set_config(key="sap_almacen_en_vulcanizado", value=str(en_vulc_in.value or "").strip())
                    repo.set_config(
                        key="ui_allow_move_in_progress_line",
                        value="1" if bool(allow_move_line_chk.value) else "0",
                    )
                    ui.notify("Configuración guardada")
                    ui.notify("Actualizando rangos/programas...")
                    kick_refresh_from_sap_all(notify=False)
                    ui.navigate.to("/config")

                ui.button("Guardar", on_click=save_cfg).props("unelevated color=primary")

            lines = repo.get_lines()
            families = repo.list_families() or ["Parrillas", "Lifters", "Corazas", "Otros"]

            ui.separator()
            ui.label("Líneas y familias permitidas - Terminaciones").classes("text-lg font-semibold")

            num_lines = ui.number("Número de líneas", value=len(lines) or 8, min=1, max=50, step=1)

            ui.label("Ajusta familias y luego presiona 'Aplicar cambios'.").classes("text-sm text-slate-600")

            rows_container = ui.column().classes("w-full")
            line_selects: dict[int, ui.select] = {}
            line_names: dict[int, ui.input] = {}

            def rebuild_rows(n: int) -> None:
                rows_container.clear()
                line_selects.clear()
                line_names.clear()
                current = {
                    ln["line_id"]: {
                        "families": set(ln["families"]),
                        "name": str(ln.get("line_name") or "").strip(),
                    }
                    for ln in repo.get_lines()
                }
                for line_id in range(1, n + 1):
                    allowed = (current.get(line_id, {}) or {}).get("families", set(families))
                    name_val = (current.get(line_id, {}) or {}).get("name", "") or f"Línea {line_id}"
                    with rows_container:
                        with ui.row().classes("items-center w-full gap-3"):
                            ui.label(f"Línea {line_id}").classes("w-24")
                            nm = ui.input("Nombre", value=name_val).classes("w-64")
                            ms = ui.select(
                                families,
                                value=list(allowed),
                                multiple=True,
                                label="Familias permitidas",
                            ).classes("w-96")
                            line_selects[line_id] = ms
                            line_names[line_id] = nm

            def apply_all() -> None:
                n = int(num_lines.value or 0)
                if n <= 0:
                    ui.notify("Número de líneas inválido", color="negative")
                    return

                # Delete lines above N
                existing_ids = [ln["line_id"] for ln in repo.get_lines()]
                for line_id in sorted(existing_ids):
                    if int(line_id) > n:
                        repo.delete_line(line_id=int(line_id))

                # Upsert 1..N using current UI selections
                for line_id in range(1, n + 1):
                    sel = line_selects.get(line_id)
                    selected_families = list((sel.value if sel else families) or [])
                    nm = line_names.get(line_id)
                    repo.upsert_line(line_id=line_id, line_name=(nm.value if nm else None), families=selected_families)

                updated = auto_generate_and_save(notify=False)
                if updated:
                    ui.notify("Configuración guardada. Programa actualizado.")
                else:
                    ui.notify("Configuración guardada. Programa no actualizado (faltan datos).", color="warning")

            rebuild_rows(int(num_lines.value))

            def on_num_change() -> None:
                rebuild_rows(int(num_lines.value))

            num_lines.on("change", lambda _: on_num_change())

            ui.separator()
            ui.button("Aplicar cambios", on_click=apply_all).props("unelevated color=primary")

            def process_lines_editor(*, process: str, title: str) -> None:
                lines_p = repo.get_lines(process=process)

                with ui.expansion(title, value=False).classes("w-full"):
                    num_lines_p = ui.number(
                        "Número de líneas",
                        value=len(lines_p) or 8,
                        min=1,
                        max=50,
                        step=1,
                    )
                    ui.label("Ajusta familias y presiona 'Aplicar cambios'.").classes("text-sm text-slate-600")

                    rows_container_p = ui.column().classes("w-full")
                    line_selects_p: dict[int, ui.select] = {}
                    line_names_p: dict[int, ui.input] = {}

                    def rebuild_rows_p(n: int) -> None:
                        rows_container_p.clear()
                        line_selects_p.clear()
                        line_names_p.clear()
                        current = {
                            ln["line_id"]: {
                                "families": set(ln["families"]),
                                "name": str(ln.get("line_name") or "").strip(),
                            }
                            for ln in repo.get_lines(process=process)
                        }
                        for line_id in range(1, n + 1):
                            allowed = (current.get(line_id, {}) or {}).get("families", set(families))
                            name_val = (current.get(line_id, {}) or {}).get("name", "") or f"Línea {line_id}"
                            with rows_container_p:
                                with ui.row().classes("items-center w-full gap-3"):
                                    ui.label(f"Línea {line_id}").classes("w-24")
                                    nm = ui.input("Nombre", value=name_val).classes("w-64")
                                    ms = ui.select(
                                        families,
                                        value=list(allowed),
                                        multiple=True,
                                        label="Familias permitidas",
                                    ).classes("w-96")
                                    line_selects_p[line_id] = ms
                                    line_names_p[line_id] = nm

                    def apply_all_p() -> None:
                        n = int(num_lines_p.value or 0)
                        if n <= 0:
                            ui.notify("Número de líneas inválido", color="negative")
                            return

                        existing_ids = [ln["line_id"] for ln in repo.get_lines(process=process)]
                        for line_id in sorted(existing_ids):
                            if int(line_id) > n:
                                repo.delete_line(process=process, line_id=int(line_id))

                        for line_id in range(1, n + 1):
                            sel = line_selects_p.get(line_id)
                            selected_families = list((sel.value if sel else families) or [])
                            nm = line_names_p.get(line_id)
                            repo.upsert_line(
                                process=process,
                                line_id=line_id,
                                line_name=(nm.value if nm else None),
                                families=selected_families,
                            )

                        # If this process hasn't built orders yet, try to rebuild now.
                        try:
                            repo.try_rebuild_orders_from_sap_for(process=process)
                        except Exception:
                            pass

                        updated = auto_generate_and_save(process=process, notify=False)
                        if updated:
                            ui.notify("Configuración guardada. Programa actualizado.")
                        else:
                            ui.notify("Configuración guardada. Programa no actualizado (faltan datos).", color="warning")

                    rebuild_rows_p(int(num_lines_p.value))

                    def on_num_change_p() -> None:
                        rebuild_rows_p(int(num_lines_p.value))

                    num_lines_p.on("change", lambda _: on_num_change_p())
                    ui.button("Aplicar cambios", on_click=apply_all_p).props("unelevated color=primary")

            ui.separator()
            ui.label("Otras líneas de proceso").classes("text-lg font-semibold")
            process_lines_editor(process="toma_de_dureza", title="Toma de dureza")
            process_lines_editor(process="mecanizado", title="Mecanizado")
            process_lines_editor(process="mecanizado_externo", title="Mecanizado externo")
            process_lines_editor(process="inspeccion_externa", title="Inspección externa")
            process_lines_editor(process="por_vulcanizar", title="Por vulcanizar")
            process_lines_editor(process="en_vulcanizado", title="En vulcanizado")

    @ui.page("/actualizar")
    def actualizar_data() -> None:
        render_nav(active="actualizar")
        with page_container():
            ui.label("Actualizar datos SAP").classes("text-2xl font-semibold")
            ui.label("Sube MB52 y Visión Planta. Centro/Almacén se configuran en Parámetros.").classes("pt-subtitle")

            with ui.row().classes("items-center gap-3 pt-2"):
                mb52_merge = ui.checkbox(
                    "MB52: acumular por almacén (no borra otros almacenes)",
                    value=False,
                ).props("dense")

            def uploader(kind: str, label: str):
                async def handle_upload(e):
                    try:
                        content = await e.file.read()
                        if kind in {"mb52", "sap_mb52"}:
                            repo.import_sap_mb52_bytes(content=content, mode=("merge" if mb52_merge.value else "replace"))
                        else:
                            repo.import_excel_bytes(kind=kind, content=content)
                        filename = getattr(e.file, "name", None) or getattr(e.file, "filename", None)
                        extra = f" ({filename})" if filename else ""
                        if kind in {"mb52", "sap_mb52"}:
                            ui.notify(f"Importado: MB52{extra} (filas: {repo.count_sap_mb52()})")

                            missing_by_material: dict[str, dict] = {}
                            for proc in repo.processes.keys():
                                for it in repo.get_missing_parts_from_mb52_for(process=proc):
                                    material = str(it.get("material", "")).strip()
                                    if not material:
                                        continue
                                    rec = missing_by_material.setdefault(
                                        material,
                                        {
                                            "material": material,
                                            "texto_breve": str(it.get("texto_breve", "") or "").strip(),
                                            "processes": set(),
                                        },
                                    )
                                    rec["processes"].add(proc)

                            missing_master = [missing_by_material[k] for k in sorted(missing_by_material.keys())]
                            if missing_master:
                                families = repo.list_families() or ["Parrillas", "Lifters", "Corazas", "Otros"]
                                dialog = ui.dialog()
                                entries: dict[str, dict] = {}
                                with dialog:
                                    with ui.card().classes("w-[min(1100px,95vw)]"):
                                        ui.label("Materiales faltantes en el maestro").classes("text-lg font-semibold")
                                        ui.label(
                                            "Completa familia y tiempos para poder programar."
                                        ).classes("text-slate-600")
                                        ui.separator()

                                        with ui.element("div").classes("max-h-[65vh] overflow-y-auto w-full"):
                                            for it in missing_master:
                                                material = str(it.get("material", "")).strip()
                                                desc = str(it.get("texto_breve", "")).strip()
                                                procs = sorted(list(it.get("processes") or []))
                                                if not material:
                                                    continue

                                                with ui.row().classes("items-end w-full gap-3 py-1"):
                                                    with ui.column().classes("w-64"):
                                                        ui.label(material).classes("font-medium")
                                                        if desc:
                                                            ui.label(desc).classes("text-xs text-slate-600 leading-tight")
                                                        if procs:
                                                            ui.label(", ".join(procs)).classes("text-xs text-slate-500")

                                                    fam = ui.select(families, value="Otros", label="Familia").classes("w-56")
                                                    v = ui.number("Vulc (d)", value=0, min=0, max=365, step=1).classes("w-28")
                                                    m = ui.number("Mec (d)", value=0, min=0, max=365, step=1).classes("w-28")
                                                    i = ui.number("Insp ext (d)", value=0, min=0, max=365, step=1).classes("w-32")
                                                    mpi = ui.checkbox("Mec perf incl.", value=False).props("dense")
                                                    sm = ui.checkbox("Sobre medida", value=False).props("dense")
                                                    entries[material] = {"fam": fam, "v": v, "m": m, "i": i, "mpi": mpi, "sm": sm}

                                        ui.separator()
                                        with ui.row().classes("justify-end w-full gap-3"):
                                            ui.button("Cerrar", on_click=dialog.close).props("flat")

                                            def save_all() -> None:
                                                try:
                                                    for material, w in entries.items():
                                                        fam_val = str(w["fam"].value or "Otros").strip() or "Otros"
                                                        repo.upsert_part_master(
                                                            numero_parte=material,
                                                            familia=fam_val,
                                                            vulcanizado_dias=int(w["v"].value or 0),
                                                            mecanizado_dias=int(w["m"].value or 0),
                                                            inspeccion_externa_dias=int(w["i"].value or 0),
                                                            mec_perf_inclinada=bool(w["mpi"].value),
                                                            sobre_medida=bool(w["sm"].value),
                                                        )

                                                    ui.notify("Maestro actualizado")
                                                    asyncio.create_task(refresh_from_sap_all(notify=False))

                                                    dialog.close()
                                                    ui.navigate.to("/actualizar")
                                                except Exception as ex:
                                                    ui.notify(f"Error guardando maestro: {ex}", color="negative")

                                            ui.button("Guardar todo", on_click=save_all).props("unelevated color=primary")

                                dialog.open()
                                # Don't continue auto generation until user completes master.
                                return
                        elif kind in {"vision", "vision_planta", "sap_vision"}:
                            ui.notify(f"Importado: Visión Planta{extra} (filas: {repo.count_sap_vision()})")
                        else:
                            ui.notify(f"Importado: {kind}{extra}")

                        await refresh_from_sap_all(notify=False)

                        if kind in {"vision", "vision_planta", "sap_vision"}:
                            try:
                                snap = repo.upsert_vision_kpi_daily()
                                ui.notify(
                                    f"KPI guardado ({snap['snapshot_date']}): {float(snap['tons_atrasadas']):,.1f} tons atrasadas / {float(snap['tons_por_entregar']):,.1f} tons por entregar"
                                )
                            except Exception as ex:
                                ui.notify(f"No se pudo guardar KPI: {ex}", color="warning")

                        missing = repo.count_missing_parts_from_orders()
                        if missing:
                            ui.notify(f"Hay {missing} números de parte sin familia. Ve a Config > Familias")

                        missing_proc = repo.count_missing_process_times_from_orders()
                        if missing_proc:
                            ui.notify(f"Hay {missing_proc} números de parte sin tiempos. Ve a Config > Maestro materiales")
                        auto_generate_and_save_all(notify=False)
                        ui.navigate.to("/actualizar")
                    except Exception as ex:
                        ui.notify(f"Error importando {kind}: {ex}", color="negative")

                ui.upload(label=label, on_upload=handle_upload).props("accept=.xlsx max-files=1")

            with ui.row().classes("w-full gap-4 items-stretch"):
                with ui.card().classes("p-4 w-[min(520px,100%)]"):
                    ui.label("MB52").classes("text-lg font-semibold")
                    ui.label("Stock por material/lote. Debe traer Documento comercial, Posición SD y Lote.").classes(
                        "text-slate-600"
                    )
                    uploader("mb52", "Subir MB52 (.xlsx)")
                    ui.label(f"Filas cargadas: {repo.count_sap_mb52()}").classes("text-sm text-slate-500")

                with ui.card().classes("p-4 w-[min(520px,100%)]"):
                    ui.label("Visión Planta").classes("text-lg font-semibold")
                    ui.label("Pedido/posición con fecha de pedido. Debe incluir columnas Pedido, Posición y Fecha de pedido.").classes(
                        "text-slate-600"
                    )
                    uploader("vision", "Subir Visión Planta (.xlsx)")
                    ui.label(f"Filas cargadas: {repo.count_sap_vision()}").classes("text-sm text-slate-500")

            with ui.row().classes("w-full justify-end"):
                def _clear_imported():
                    repo.clear_imported_data()
                    ui.notify("Datos borrados")
                    auto_generate_and_save_all(notify=False)
                    ui.navigate.to("/actualizar")

                ui.button("Borrar datos importados", color="negative", on_click=_clear_imported).props("outline")

            with ui.expansion("Vista previa", value=False).classes("w-full"):
                diag = repo.get_sap_rebuild_diagnostics()
                ui.markdown(
                    f"MB52: **{repo.count_sap_mb52()}** | Visión: **{repo.count_sap_vision()}** | Piezas usables: **{diag['usable_total']}** | Usables con Pedido/Pos/Lote: **{diag['usable_with_keys']}** | Cruzan con Visión: **{diag['usable_with_keys_and_vision']}** | Órdenes: **{repo.count_orders()}**"
                )

                try:
                    centro_cfg = (repo.get_config(key="sap_centro", default="4000") or "").strip()
                    almacenes = repo.get_sap_mb52_almacen_counts(centro=centro_cfg, limit=50)
                    if almacenes:
                        ui.separator()
                        ui.label("MB52: almacenes presentes").classes("text-lg font-semibold")
                        ui.table(
                            columns=[
                                {"name": "almacen", "label": "Almacén", "field": "almacen"},
                                {"name": "count", "label": "Filas", "field": "count"},
                            ],
                            rows=almacenes,
                            row_key="almacen",
                        ).props("dense flat bordered")
                except Exception:
                    pass
                if diag["usable_total"] and diag["usable_with_keys"] == 0:
                    ui.label(
                        "Hay piezas usables, pero MB52 no trae Documento comercial/Posición SD/Lote en esas filas (no se pueden agrupar por pedido/posición)."
                    ).classes("text-amber-700")
                if diag["usable_with_keys"] and diag["usable_with_keys_and_vision"] == 0:
                    ui.label(
                        "MB52 trae pedido/posición, pero no está cruzando con Visión. Revisa que Pedido/Posición estén en el mismo formato (la app normaliza 10.0 vs 000010)."
                    ).classes("text-amber-700")
                if diag["distinct_orderpos_missing_vision"]:
                    ui.label(
                        f"Pedido/posición sin match en Visión: {diag['distinct_orderpos_missing_vision']} (sobre {diag['distinct_orderpos']})."
                    ).classes("text-amber-700")

                missing_vision_rows = repo.get_sap_orderpos_missing_vision_rows(limit=200)
                if missing_vision_rows:
                    ui.separator()
                    ui.label("Usables con Pedido/Posición pero sin match en Visión (top 200)").classes(
                        "text-lg font-semibold"
                    )
                    ui.label(
                        "Estos sí cumplen centro/almacén/libre/QC/lote, pero no existe la fila pedido+posición en Visión Planta."
                    ).classes("text-slate-600")
                    ui.table(
                        columns=[
                            {"name": "pedido", "label": "Pedido", "field": "pedido"},
                            {"name": "posicion", "label": "Pos.", "field": "posicion"},
                            {"name": "material", "label": "Material", "field": "material"},
                            {"name": "texto_breve", "label": "Descripción", "field": "texto_breve"},
                            {"name": "piezas", "label": "Piezas", "field": "piezas"},
                            {"name": "lote_min", "label": "Lote min", "field": "lote_min"},
                            {"name": "lote_max", "label": "Lote max", "field": "lote_max"},
                        ],
                        rows=missing_vision_rows,
                        row_key="material",
                    ).props("dense flat bordered")

                non_usable = repo.get_sap_non_usable_with_orderpos_rows(limit=200)
                if non_usable:
                    ui.separator()
                    ui.label("MB52: con Pedido/Posición pero NO disponibles (top 200)").classes("text-lg font-semibold")
                    ui.label(
                        "Se muestran solo filas del centro/almacén configurado; motivos: no libre utilización o en control de calidad."
                    ).classes("text-slate-600")
                    ui.table(
                        columns=[
                            {"name": "pedido", "label": "Pedido", "field": "pedido"},
                            {"name": "posicion", "label": "Pos.", "field": "posicion"},
                            {"name": "material", "label": "Material", "field": "material"},
                            {"name": "texto_breve", "label": "Descripción", "field": "texto_breve"},
                            {"name": "lote", "label": "Lote", "field": "lote"},
                            {"name": "libre", "label": "Libre", "field": "libre"},
                            {"name": "qc", "label": "QC", "field": "qc"},
                            {"name": "motivo", "label": "Motivo", "field": "motivo"},
                        ],
                        rows=non_usable,
                        row_key="material",
                    ).props("dense flat bordered")

                rows = repo.get_orders_rows(limit=200)
                if not rows:
                    ui.label("Aún no hay órdenes generadas. Sube MB52 + Visión y configura Centro/Almacén.")
                else:
                    ui.table(
                        columns=[
                            {"name": "pedido", "label": "Pedido", "field": "pedido"},
                            {"name": "posicion", "label": "Pos.", "field": "posicion"},
                            {"name": "numero_parte", "label": "Parte", "field": "numero_parte"},
                            {"name": "cantidad", "label": "Stock (MB52)", "field": "cantidad"},
                            {"name": "fecha_entrega", "label": "Fecha pedido", "field": "fecha_entrega"},
                        ],
                        rows=rows,
                        row_key="primer_correlativo",
                    ).props("dense flat bordered")

    @ui.page("/familias")
    def familias() -> None:
        render_nav(active="config_familias")
        with page_container():
            ui.label("Familias").classes("text-2xl font-semibold")
            ui.label("Mantén el catálogo de familias.").classes("pt-subtitle")

            families = repo.list_families() or ["Parrillas", "Lifters", "Corazas", "Otros"]

            ui.separator()
            ui.label("Catálogo").classes("text-lg font-semibold")

            rows_all = repo.get_families_rows()
            q = ui.input("Buscar familia", placeholder="Ej: Parrillas").classes("w-72")

            def filtered_rows() -> list[dict]:
                needle = str(q.value or "").strip().lower()
                if not needle:
                    return list(rows_all)
                return [r for r in rows_all if needle in str(r.get("familia", "")).lower()]

            def refresh_rows() -> None:
                nonlocal rows_all, families
                rows_all = repo.get_families_rows()
                families = repo.list_families() or ["Parrillas", "Lifters", "Corazas", "Otros"]
                tbl.rows = filtered_rows()
                tbl.update()

            tbl = ui.table(
                columns=[
                    {"name": "familia", "label": "Familia", "field": "familia"},
                    {"name": "parts_count", "label": "# Partes asignadas", "field": "parts_count"},
                ],
                rows=filtered_rows(),
                row_key="familia",
            ).props("dense flat bordered")

            q.on(
                "update:model-value",
                lambda *_: (
                    setattr(tbl, "rows", filtered_rows()),
                    tbl.update(),
                ),
            )

            dialog = ui.dialog().props("persistent")
            state = {"mode": "add", "current": ""}

            with dialog:
                with ui.card().classes("bg-white p-6").style("width: 92vw; max-width: 820px;"):
                    mode_label = ui.label("").classes("text-xl font-semibold")
                    current_label = ui.label("").classes("font-mono text-slate-700")
                    ui.separator()

                    fam_name = ui.input("Nombre").classes("w-80")
                    rename_to = ui.input("Renombrar a (opcional)").classes("w-80")
                    fam_name.props("outlined dense")
                    rename_to.props("outlined dense")
                    with ui.row().classes("w-full items-end gap-4"):
                        fam_name
                        rename_to

                    ui.separator()
                    force_reassign = ui.checkbox("Reasignar partes a 'Otros' al eliminar", value=False)

                    with ui.row().classes("w-full justify-end gap-2"):
                        ui.button("Cancelar", on_click=dialog.close).props("flat")

                        def do_delete() -> None:
                            if state["mode"] == "add":
                                ui.notify("No hay nada que eliminar", color="warning")
                                return
                            try:
                                repo.delete_family(name=state["current"], force=bool(force_reassign.value))
                                ui.notify("Familia eliminada")
                            except Exception as ex:
                                ui.notify(f"Error eliminando: {ex}", color="negative")
                                return
                            dialog.close()
                            auto_generate_and_save(notify=False)
                            refresh_rows()

                        ui.button("Eliminar", color="negative", on_click=do_delete).props("outline")

                        def do_save() -> None:
                            name = str(fam_name.value).strip()
                            if not name:
                                ui.notify("Nombre vacío", color="negative")
                                return
                            try:
                                if state["mode"] == "add":
                                    repo.add_family(name=name)
                                    ui.notify("Familia agregada")
                                else:
                                    new_name = str(rename_to.value).strip() or name
                                    if new_name != state["current"]:
                                        repo.rename_family(old=state["current"], new=new_name)
                                        ui.notify("Familia renombrada")
                                    else:
                                        ui.notify("Sin cambios")
                            except Exception as ex:
                                ui.notify(f"Error guardando: {ex}", color="negative")
                                return
                            dialog.close()
                            auto_generate_and_save(notify=False)
                            refresh_rows()

                        ui.button("Guardar", on_click=do_save).props("unelevated color=primary")

            def open_dialog(*, mode: str, current: str | None = None) -> None:
                state["mode"] = mode
                state["current"] = str(current or "").strip()
                if mode == "add":
                    mode_label.text = "Nueva familia"
                    current_label.text = ""
                    fam_name.value = ""
                    rename_to.value = ""
                else:
                    mode_label.text = "Editar familia"
                    current_label.text = state["current"]
                    fam_name.value = state["current"]
                    rename_to.value = ""
                force_reassign.value = False
                dialog.open()

            with ui.row().classes("items-end w-full gap-3"):
                ui.button("Nueva familia", icon="add", on_click=lambda: open_dialog(mode="add")).props(
                    "unelevated color=primary"
                )

            def on_row_event(e):
                args = getattr(e, "args", None)

                def _walk(obj):
                    if isinstance(obj, dict):
                        yield obj
                        for v_ in obj.values():
                            yield from _walk(v_)
                    elif isinstance(obj, (list, tuple)):
                        for it in obj:
                            yield from _walk(it)

                def _pick_row_and_key(obj) -> tuple[dict | None, str | None]:
                    if isinstance(obj, (str, int, float)):
                        s = str(obj).strip()
                        return None, (s if s else None)

                    row_found: dict | None = None
                    key_found: str | None = None

                    for d in _walk(obj):
                        if isinstance(d.get("args"), dict):
                            inner = d.get("args")
                            if isinstance(inner, dict):
                                if isinstance(inner.get("row"), dict) and row_found is None:
                                    row_found = inner.get("row")
                                if key_found is None:
                                    for k in ("key", "rowKey", "id", "row_key"):
                                        if inner.get(k) is not None:
                                            key_found = str(inner.get(k)).strip() or None
                                            break

                        if isinstance(d.get("row"), dict) and row_found is None:
                            row_found = d.get("row")

                        if row_found is None and "familia" in d:
                            row_found = d

                        if key_found is None:
                            for k in ("key", "rowKey", "id", "row_key"):
                                if d.get(k) is not None:
                                    key_found = str(d.get(k)).strip() or None
                                    break

                    return row_found, key_found

                row, key = _pick_row_and_key(args)
                fam = ""
                if isinstance(row, dict):
                    fam = str(row.get("familia") or "").strip()
                elif key is not None:
                    fam = str(key).strip()

                if not fam:
                    ui.notify("No se pudo leer la fila seleccionada", color="negative")
                    return
                open_dialog(mode="edit", current=fam)

            tbl.on("rowDblClick", on_row_event)
            tbl.on("rowDblclick", on_row_event)

    @ui.page("/config/tiempos")
    def config_tiempos() -> None:
        render_nav(active="config_materiales")
        with page_container():
            ui.label("Tiempos de proceso").classes("text-2xl font-semibold")
            ui.label("Esta pantalla fue reemplazada por 'Config > Maestro materiales'.").classes("pt-subtitle")
            with ui.row().classes("gap-2 pt-2"):
                ui.button("Ir a Maestro materiales", on_click=lambda: ui.navigate.to("/config/materiales")).props(
                    "unelevated color=primary"
                )
                ui.button("Ir a Familias", on_click=lambda: ui.navigate.to("/familias")).props("flat color=primary")


    @ui.page("/config/materiales")
    def config_materiales() -> None:
        render_nav(active="config_materiales")
        with page_container():
            ui.label("Maestro de materiales").classes("text-2xl font-semibold")
            ui.label("Edita familia y tiempos por material, o elimina materiales del maestro.").classes("pt-subtitle")

            families = repo.list_families() or ["Parrillas", "Lifters", "Corazas", "Otros"]

            ui.separator()
            with ui.row().classes("items-end w-full gap-3"):
                q = ui.input("Buscar material", placeholder="Ej: 436...").classes("w-72")

                delete_all_dialog = ui.dialog().props("persistent")
                with delete_all_dialog:
                    with ui.card().classes("bg-white p-6").style("width: 92vw; max-width: 820px;"):
                        ui.label("Borrar todo el maestro").classes("text-xl font-semibold")
                        ui.label("Esto elimina TODOS los materiales del maestro (familia + tiempos).").classes(
                            "text-amber-700"
                        )
                        confirm = ui.input("Escribe BORRAR para confirmar").classes("w-80")
                        confirm.props("outlined dense")
                        ui.separator()

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("Cancelar", on_click=delete_all_dialog.close).props("flat")

                            def do_delete_all() -> None:
                                if str(confirm.value).strip().upper() != "BORRAR":
                                    ui.notify("Confirmación incorrecta", color="negative")
                                    return
                                repo.delete_all_parts()
                                delete_all_dialog.close()
                                ui.notify("Maestro borrado")
                                ui.navigate.to("/config/materiales")

                            ui.button("Borrar todo", color="negative", on_click=do_delete_all).props("unelevated")

                def open_delete_all_dialog() -> None:
                    confirm.value = ""
                    confirm.update()
                    delete_all_dialog.open()
                    try:
                        confirm.run_method("focus")
                    except Exception:
                        pass

                ui.button(
                    "Borrar todo",
                    icon="delete_forever",
                    color="negative",
                    on_click=open_delete_all_dialog,
                ).props("outline")

            rows_all = repo.get_parts_rows()
            if not rows_all:
                ui.label("Aún no hay materiales en el maestro.").classes("text-slate-600")
                return

            def _decorate_row(r: dict) -> dict:
                rr = dict(r)
                rr["_missing_times"] = any(
                    rr.get(k) is None for k in ("vulcanizado_dias", "mecanizado_dias", "inspeccion_externa_dias")
                )
                return rr

            def filtered_rows() -> list[dict]:
                needle = str(q.value or "").strip().lower()
                if not needle:
                    return [_decorate_row(r) for r in rows_all]
                out = []
                for r in rows_all:
                    if needle in str(r.get("numero_parte", "")).lower():
                        out.append(_decorate_row(r))
                return out

            tbl = ui.table(
                columns=[
                    {"name": "numero_parte", "label": "Material", "field": "numero_parte"},
                    {"name": "familia", "label": "Familia", "field": "familia"},
                    {"name": "vulcanizado_dias", "label": "Vulc (d)", "field": "vulcanizado_dias"},
                    {"name": "mecanizado_dias", "label": "Mec (d)", "field": "mecanizado_dias"},
                    {"name": "inspeccion_externa_dias", "label": "Insp ext (d)", "field": "inspeccion_externa_dias"},
                    {"name": "peso_ton", "label": "Peso Unitario", "field": "peso_ton"},
                    {"name": "mec_perf_inclinada", "label": "Mec perf incl.", "field": "mec_perf_inclinada"},
                    {"name": "sobre_medida", "label": "Sobre medida", "field": "sobre_medida"},
                ],
                rows=filtered_rows(),
                row_key="numero_parte",
            ).props("dense flat bordered")

            tbl.add_slot(
                "body-cell-numero_parte",
                r"""
<q-td :props="props">
  <span :class="props.row && props.row._missing_times ? 'text-negative font-medium' : ''">{{ props.value }}</span>
</q-td>
""",
            )

            tbl.add_slot(
                "body-cell-peso_ton",
                r"""
<q-td :props="props">
    <span v-if="props.value !== null && props.value !== undefined && String(props.value) !== ''">{{ Number(props.value).toFixed(1) }}</span>
    <span v-else class="text-slate-400">—</span>
</q-td>
""",
            )

            tbl.add_slot(
                "body-cell-mec_perf_inclinada",
                r"""
<q-td :props="props">
    <q-badge v-if="Number(props.value) === 1" color="primary" label="Sí" />
    <span v-else class="text-slate-400">—</span>
</q-td>
""",
            )

            tbl.add_slot(
                "body-cell-sobre_medida",
                r"""
<q-td :props="props">
    <q-badge v-if="Number(props.value) === 1" color="primary" label="Sí" />
    <span v-else class="text-slate-400">—</span>
</q-td>
""",
            )

            def refresh_rows() -> None:
                nonlocal rows_all
                rows_all = repo.get_parts_rows()
                tbl.rows = filtered_rows()
                tbl.update()

            q.on(
                "update:model-value",
                lambda *_: (
                    setattr(tbl, "rows", filtered_rows()),
                    tbl.update(),
                ),
            )

            edit_dialog = ui.dialog().props("persistent")
            delete_one_dialog = ui.dialog().props("persistent")

            with edit_dialog:
                with ui.card().classes("bg-white p-6").style("width: 92vw; max-width: 980px;"):
                    ui.label("Editar material").classes("text-xl font-semibold")
                    np_label = ui.label("").classes("font-mono text-slate-700")
                    np_desc = ui.label("").classes("text-slate-600")

                    ui.separator()

                    with ui.row().classes("w-full items-end gap-4"):
                        fam_sel = ui.select(families, value="Otros", label="Familia").classes("w-72")
                        fam_sel.props("outlined dense use-input")

                    with ui.row().classes("w-full items-end gap-4 pt-2"):
                        v = ui.number("Vulc (d)", value=0, min=0, max=365, step=1).classes("w-40")
                        m = ui.number("Mec (d)", value=0, min=0, max=365, step=1).classes("w-40")
                        i = ui.number("Insp ext (d)", value=0, min=0, max=365, step=1).classes("w-40")
                        pt = ui.number("Peso Unitario", value=0, min=0, step=0.001).classes("w-40")
                        v.props("outlined dense")
                        m.props("outlined dense")
                        i.props("outlined dense")
                        pt.props("outlined dense")

                    with ui.row().classes("w-full items-center gap-6 pt-2"):
                        mpi_chk = ui.checkbox("Mec perf inclinada", value=False).props("dense")
                        sm_chk = ui.checkbox("Sobre medida", value=False).props("dense")

                    ui.separator()

                    with ui.row().classes("w-full justify-end gap-2"):
                        ui.button("Cancelar", on_click=edit_dialog.close).props("flat")

                        def open_delete_one() -> None:
                            delete_one_dialog.open()

                        ui.button("Borrar", color="negative", on_click=open_delete_one).props("outline")

                        def do_save() -> None:
                            try:
                                repo.upsert_part_master(
                                    numero_parte=np_label.text,
                                    familia=str(fam_sel.value),
                                    vulcanizado_dias=int(v.value) if v.value is not None else None,
                                    mecanizado_dias=int(m.value) if m.value is not None else None,
                                    inspeccion_externa_dias=int(i.value) if i.value is not None else None,
                                    peso_ton=float(pt.value) if pt.value is not None else None,
                                    mec_perf_inclinada=bool(mpi_chk.value),
                                    sobre_medida=bool(sm_chk.value),
                                )
                                ui.notify("Guardado")
                            except Exception as ex:
                                ui.notify(f"Error guardando: {ex}", color="negative")
                                return
                            edit_dialog.close()
                            auto_generate_and_save(notify=False)
                            refresh_rows()

                        ui.button("Guardar", on_click=do_save).props("unelevated color=primary")

            with delete_one_dialog:
                with ui.card().classes("bg-white p-6").style("width: 92vw; max-width: 720px;"):
                    ui.label("Eliminar material del maestro").classes("text-xl font-semibold")
                    ui.label("Esto elimina la familia y tiempos de este material.").classes("text-amber-700")
                    ui.label("").bind_text_from(np_label, "text").classes("font-mono text-slate-700")
                    ui.separator()
                    with ui.row().classes("w-full justify-end gap-2"):
                        ui.button("Cancelar", on_click=delete_one_dialog.close).props("flat")

                        def do_delete_one() -> None:
                            repo.delete_part(numero_parte=np_label.text)
                            delete_one_dialog.close()
                            edit_dialog.close()
                            ui.notify("Material eliminado")
                            auto_generate_and_save(notify=False)
                            refresh_rows()

                        ui.button("Eliminar", color="negative", on_click=do_delete_one).props("unelevated")

            def _find_row_by_np(numero_parte: str) -> dict | None:
                np_s = str(numero_parte).strip()
                if not np_s:
                    return None
                for r in rows_all:
                    if str(r.get("numero_parte", "")).strip() == np_s:
                        return r
                return None

            def open_editor(*, numero_parte: str, row: dict | None = None) -> None:
                np_s = str(numero_parte or "").strip()
                if not np_s:
                    ui.notify("Fila inválida", color="negative")
                    return
                row_data = row if isinstance(row, dict) else _find_row_by_np(np_s)
                if row_data is None:
                    ui.notify("Fila inválida", color="negative")
                    return

                np_label.text = np_s
                try:
                    np_desc.text = repo.get_mb52_texto_breve(material=np_s)
                except Exception:
                    np_desc.text = ""
                fam_sel.value = str(row_data.get("familia") or "Otros")
                v.value = row_data.get("vulcanizado_dias") if row_data.get("vulcanizado_dias") is not None else 0
                m.value = row_data.get("mecanizado_dias") if row_data.get("mecanizado_dias") is not None else 0
                i.value = (
                    row_data.get("inspeccion_externa_dias")
                    if row_data.get("inspeccion_externa_dias") is not None
                    else 0
                )
                pt.value = row_data.get("peso_ton") if row_data.get("peso_ton") is not None else 0
                mpi_chk.value = bool(int(row_data.get("mec_perf_inclinada") or 0))
                sm_chk.value = bool(int(row_data.get("sobre_medida") or 0))
                edit_dialog.open()

            def on_row_event(e):
                args = getattr(e, "args", None)

                # NiceGUI/Quasar event payloads vary by version and event type.
                # We accept either a full 'row' dict, or a 'key' (row_key), possibly nested.

                def _walk(obj):
                    if isinstance(obj, dict):
                        yield obj
                        for v_ in obj.values():
                            yield from _walk(v_)
                    elif isinstance(obj, (list, tuple)):
                        for it in obj:
                            yield from _walk(it)

                def _pick_row_and_key(obj) -> tuple[dict | None, str | None, int | None]:
                    row_found: dict | None = None
                    key_found: str | None = None
                    idx_found: int | None = None

                    # Scalar payload (some versions send only the key)
                    if isinstance(obj, (str, int, float)):
                        s = str(obj).strip()
                        return None, (s if s else None), None

                    for d in _walk(obj):
                        # unwrap nested args dict
                        if isinstance(d.get("args"), dict):
                            inner = d.get("args")
                            if isinstance(inner, dict):
                                # direct reads from nested args
                                if isinstance(inner.get("row"), dict) and row_found is None:
                                    row_found = inner.get("row")
                                if key_found is None:
                                    for k in ("key", "rowKey", "id", "row_key"):
                                        if inner.get(k) is not None:
                                            key_found = str(inner.get(k)).strip() or None
                                            break
                                if idx_found is None and inner.get("rowIndex") is not None:
                                    try:
                                        idx_found = int(inner.get("rowIndex"))
                                    except Exception:
                                        idx_found = None

                        # direct row dict
                        if isinstance(d.get("row"), dict) and row_found is None:
                            row_found = d.get("row")

                        # sometimes the payload dict itself is the row
                        if row_found is None and any(k in d for k in ("numero_parte", "material", "Material")):
                            row_found = d

                        if key_found is None:
                            for k in ("key", "rowKey", "id", "row_key"):
                                if d.get(k) is not None:
                                    key_found = str(d.get(k)).strip() or None
                                    break

                        if idx_found is None:
                            for k in ("rowIndex", "row_index"):
                                if d.get(k) is not None:
                                    try:
                                        idx_found = int(d.get(k))
                                    except Exception:
                                        idx_found = None
                                    break

                    return row_found, key_found, idx_found

                row, key, row_index = _pick_row_and_key(args)

                if isinstance(row, dict):
                    np = (
                        row.get("numero_parte")
                        or row.get("material")
                        or row.get("Material")
                        or row.get("numeroParte")
                    )
                    open_editor(numero_parte=str(np or "").strip(), row=row)
                    return

                if key is not None:
                    open_editor(numero_parte=str(key).strip(), row=None)
                    return

                if row_index is not None:
                    try:
                        current_rows = list(getattr(tbl, "rows", []) or [])
                        if 0 <= row_index < len(current_rows) and isinstance(current_rows[row_index], dict):
                            r0 = current_rows[row_index]
                            open_editor(numero_parte=str(r0.get("numero_parte") or "").strip(), row=r0)
                            return
                    except Exception:
                        pass

                # Last resort: show a tiny hint to debug the incoming payload.
                hint = ""
                if isinstance(args, dict):
                    hint = f" keys={sorted(list(args.keys()))[:6]}"
                elif isinstance(args, (list, tuple)):
                    hint = f" len={len(args)}"
                else:
                    hint = f" type={type(args).__name__}"
                ui.notify(f"No se pudo leer la fila seleccionada.{hint}", color="negative")
                return

            # Open editor only on double-click.
            tbl.on("rowDblClick", on_row_event)
            tbl.on("rowDblclick", on_row_event)

    @ui.page("/config/pedidos")
    def config_pedidos() -> None:
        render_nav(active="config_pedidos")
        with page_container():
            ui.label("Pedidos").classes("text-2xl font-semibold")
            ui.label(
                "Marca pedido/posición con prioridad para forzar que entren primero en el programa."
            ).classes(
                "pt-subtitle"
            )

            rows_all = repo.get_pedidos_master_rows()
            for r in rows_all:
                r["_key"] = f"{r.get('pedido','')}|{r.get('posicion','')}"
            if not rows_all:
                ui.label("Aún no hay pedidos cargados. Sube MB52 + Visión Planta en 'Actualizar'.").classes(
                    "text-slate-600"
                )
                return

            with ui.row().classes("items-end w-full gap-3"):
                q = ui.input("Buscar", placeholder="Pedido, posición o cliente...").classes("w-72")

                delete_all_dialog = ui.dialog().props("persistent")
                with delete_all_dialog:
                    with ui.card().classes("bg-white p-6").style("width: 92vw; max-width: 820px;"):
                        ui.label("Borrar todas las prioridades").classes("text-xl font-semibold")
                        ui.label(
                            "Esto elimina TODAS las prioridades marcadas manualmente en pedidos/posición (las pruebas se mantienen)."
                        ).classes("text-amber-700")
                        confirm = ui.input("Escribe BORRAR para confirmar").classes("w-80")
                        confirm.props("outlined dense")
                        ui.separator()

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button("Cancelar", on_click=delete_all_dialog.close).props("flat")

                            def do_delete_all() -> None:
                                if str(confirm.value).strip().upper() != "BORRAR":
                                    ui.notify("Confirmación incorrecta", color="negative")
                                    return
                                repo.delete_all_pedido_priorities(keep_tests=True)
                                delete_all_dialog.close()
                                ui.notify("Prioridades borradas")
                                refresh_rows()

                            ui.button("Borrar todo", color="negative", on_click=do_delete_all).props("unelevated")

                def open_delete_all_dialog() -> None:
                    confirm.value = ""
                    confirm.update()
                    delete_all_dialog.open()
                    try:
                        confirm.run_method("focus")
                    except Exception:
                        pass

                ui.button(
                    "Borrar todo",
                    icon="delete_forever",
                    color="negative",
                    on_click=open_delete_all_dialog,
                ).props("outline")

            def filtered_rows() -> list[dict]:
                needle = str(q.value or "").strip().lower()
                if not needle:
                    return list(rows_all)
                return [
                    r
                    for r in rows_all
                    if needle in str(r.get("pedido", "")).lower()
                    or needle in str(r.get("posicion", "")).lower()
                    or needle in str(r.get("cliente", "")).lower()
                ]

            tbl = ui.table(
                columns=[
                    {"name": "pedido", "label": "Pedido", "field": "pedido"},
                    {"name": "posicion", "label": "Posición", "field": "posicion"},
                    {"name": "cliente", "label": "Cliente", "field": "cliente"},
                    {"name": "cod_material", "label": "Cod. material", "field": "cod_material"},
                    {"name": "descripcion_material", "label": "Descripción", "field": "descripcion_material"},
                    {"name": "fecha_pedido", "label": "Fecha pedido", "field": "fecha_pedido"},
                    {"name": "solicitado", "label": "Cantidad", "field": "solicitado"},
                    {"name": "peso_neto", "label": "Peso neto (tons)", "field": "peso_neto"},
                    {"name": "bodega", "label": "Bodega", "field": "bodega"},
                    {"name": "despachado", "label": "Despachado", "field": "despachado"},
                    {"name": "pendientes", "label": "Pendientes", "field": "pendientes"},
                    {"name": "is_priority", "label": "Prioridad", "field": "is_priority"},
                ],
                rows=filtered_rows(),
                row_key="_key",
            ).props("dense flat bordered")

            tbl.add_slot(
                "body-cell-is_priority",
                r"""
<q-td :props="props">
    <q-badge
        v-if="props.value && Number(props.value) === 1 && (props.row && String(props.row.priority_kind || '').toLowerCase() === 'test')"
        color="purple"
        label="PRUEBA"
    />
    <q-badge
        v-else-if="props.value && Number(props.value) === 1"
        color="negative"
        label="PRIORIDAD"
    />
  <span v-else class="text-slate-400">—</span>
</q-td>
""",
            )

            tbl.add_slot(
                "body-cell-peso_neto",
                r"""
<q-td :props="props">
    <span v-if="props.value !== null && props.value !== undefined && String(props.value) !== ''">{{ Number(props.value).toFixed(1) }}</span>
    <span v-else class="text-slate-400">—</span>
</q-td>
""",
            )

            def refresh_rows() -> None:
                nonlocal rows_all
                rows_all = repo.get_pedidos_master_rows()
                for r in rows_all:
                    r["_key"] = f"{r.get('pedido','')}|{r.get('posicion','')}"
                tbl.rows = filtered_rows()
                tbl.update()

            q.on(
                "update:model-value",
                lambda *_: (
                    setattr(tbl, "rows", filtered_rows()),
                    tbl.update(),
                ),
            )

            dialog = ui.dialog().props("persistent")
            state = {"pedido": "", "posicion": "", "is_priority": 0, "priority_kind": ""}

            with dialog:
                with ui.card().classes("bg-white p-6").style("width: 92vw; max-width: 720px;"):
                    ui.label("Editar pedido/posición").classes("text-xl font-semibold")
                    pedido_label = ui.label("").classes("font-mono text-slate-700")
                    kind_label = ui.label("").classes("text-sm text-slate-600")
                    ui.separator()

                    priority_chk = ui.checkbox("Marcar como prioridad", value=False)

                    ui.separator()
                    with ui.row().classes("w-full justify-end gap-2"):
                        ui.button("Cancelar", on_click=dialog.close).props("flat")

                        def do_save() -> None:
                            try:
                                repo.set_pedido_priority(
                                    pedido=state["pedido"],
                                    posicion=state["posicion"],
                                    is_priority=bool(priority_chk.value),
                                )
                                ui.notify("Guardado")
                            except Exception as ex:
                                ui.notify(f"Error guardando: {ex}", color="negative")
                                return
                            dialog.close()
                            refresh_rows()

                        ui.button("Guardar", on_click=do_save).props("unelevated color=primary")

            def on_row_event(e):
                args = getattr(e, "args", None)

                def _walk(obj):
                    if isinstance(obj, dict):
                        yield obj
                        for v_ in obj.values():
                            yield from _walk(v_)
                    elif isinstance(obj, (list, tuple)):
                        for it in obj:
                            yield from _walk(it)

                def _pick_row_and_key(obj) -> tuple[dict | None, str | None]:
                    if isinstance(obj, (str, int, float)):
                        s = str(obj).strip()
                        return None, (s if s else None)

                    row_found: dict | None = None
                    key_found: str | None = None
                    for d in _walk(obj):
                        if isinstance(d.get("row"), dict) and row_found is None:
                            row_found = d.get("row")
                        if row_found is None and "pedido" in d:
                            row_found = d
                        if key_found is None:
                            for k in ("key", "rowKey", "id", "row_key"):
                                if d.get(k) is not None:
                                    key_found = str(d.get(k)).strip() or None
                                    break
                    return row_found, key_found

                row, key = _pick_row_and_key(args)
                pedido = ""
                posicion = ""
                is_priority = 0
                priority_kind = ""
                if isinstance(row, dict):
                    pedido = str(row.get("pedido") or "").strip()
                    posicion = str(row.get("posicion") or "").strip()
                    try:
                        is_priority = int(row.get("is_priority") or 0)
                    except Exception:
                        is_priority = 0
                    priority_kind = str(row.get("priority_kind") or "").strip().lower()
                elif key is not None:
                    key_s = str(key).strip()
                    for r in rows_all:
                        if str(r.get("_key")) == key_s:
                            pedido = str(r.get("pedido") or "").strip()
                            posicion = str(r.get("posicion") or "").strip()
                            try:
                                is_priority = int(r.get("is_priority") or 0)
                            except Exception:
                                is_priority = 0
                            priority_kind = str(r.get("priority_kind") or "").strip().lower()
                            break

                if not pedido or not posicion:
                    ui.notify("No se pudo leer la fila seleccionada", color="negative")
                    return

                state["pedido"] = pedido
                state["posicion"] = posicion
                state["is_priority"] = is_priority
                state["priority_kind"] = priority_kind
                pedido_label.text = f"{pedido} / {posicion}"
                kind_label.text = "Tipo: PRUEBA (se mantiene priorizada)" if priority_kind == "test" else ""
                priority_chk.value = bool(is_priority)
                dialog.open()

            tbl.on("rowDblClick", on_row_event)
            tbl.on("rowDblclick", on_row_event)

    def _render_program(process: str, *, active_key: str, title: str) -> None:
        process = str(process or "terminaciones").strip().lower()
        render_nav(active=active_key)
        with page_container():
            ui.label(title).classes("text-2xl font-semibold")

            if repo.count_orders(process=process) == 0:
                mb = repo.count_sap_mb52()
                vis = repo.count_sap_vision()
                if mb == 0 or vis == 0:
                    ui.label(
                        f"No hay rangos generados. Carga MB52 + Visión (MB52: {mb}, Visión: {vis})."
                    ).classes("text-amber-700")
                    with ui.row().classes("gap-2 pt-2"):
                        ui.button("Ir a Actualizar", on_click=lambda: ui.navigate.to("/actualizar")).props(
                            "unelevated color=primary"
                        )
                        ui.button("Ir a Config > Parámetros", on_click=lambda: ui.navigate.to("/config")).props(
                            "flat color=primary"
                        )
                    return

                diag = repo.get_sap_rebuild_diagnostics(process=process)
                ui.label("No hay rangos generados desde SAP todavía.").classes("text-amber-700")
                ui.markdown(
                    f"MB52: **{mb}** | Visión: **{vis}** | Piezas usables: **{diag['usable_total']}** | "
                    f"Usables con Pedido/Pos/Lote: **{diag['usable_with_keys']}** | Cruzan con Visión: **{diag['usable_with_keys_and_vision']}**"
                )

                # Helpful hint when MB52 doesn't include the configured almacen for this process.
                try:
                    centro_cfg = (repo.get_config(key="sap_centro", default="4000") or "").strip()
                    almacenes = repo.get_sap_mb52_almacen_counts(centro=centro_cfg, limit=10)
                    present = [a["almacen"] for a in almacenes if a.get("almacen")]
                    if present and str(diag.get("almacen") or "") not in set(present):
                        ui.label(
                            f"MB52 cargado no contiene el almacén configurado ({diag.get('almacen')}). "
                            f"Almacenes presentes (top): {', '.join(present)}"
                        ).classes("text-amber-700")
                        ui.label(
                            "Sugerencia: si exportas MB52 por almacén, activa 'acumular por almacén' en /actualizar y sube los otros MB52 (4049/4050/4046/4047/4048)."
                        ).classes("text-slate-600")
                except Exception:
                    pass
                if diag.get("distinct_orderpos_missing_vision"):
                    ui.label(
                        f"Pedido/posición sin match en Visión: {diag['distinct_orderpos_missing_vision']} (sobre {diag['distinct_orderpos']})."
                    ).classes("text-amber-700")

                async def _rebuild_now() -> None:
                    ui.notify("Reconstruyendo rangos...")
                    try:
                        n = await asyncio.to_thread(lambda: repo.rebuild_orders_from_sap_for(process=process))
                        if n > 0:
                            diag2 = repo.get_sap_rebuild_diagnostics(process=process)
                            extra = (
                                f" | sin match en Visión: {diag2['distinct_orderpos_missing_vision']}"
                                if diag2.get("distinct_orderpos_missing_vision")
                                else ""
                            )
                            ui.notify(f"Rangos generados: {n}{extra}")
                            ui.navigate.reload()
                        else:
                            d = repo.get_sap_rebuild_diagnostics(process=process)
                            ui.notify(
                                f"No se generaron rangos (Cruzan con Visión: {d['usable_with_keys_and_vision']}). Revisa Vista previa.",
                                color="warning",
                            )
                    except Exception as ex:
                        ui.notify(f"Error reconstruyendo rangos: {ex}", color="negative")

                with ui.row().classes("gap-2 pt-2"):
                    ui.button("Reconstruir rangos", on_click=_rebuild_now).props("unelevated color=primary")
                    ui.button("Ver Vista previa", on_click=lambda: ui.navigate.to("/actualizar")).props(
                        "flat color=primary"
                    )
                    ui.button("Config > Parámetros", on_click=lambda: ui.navigate.to("/config")).props(
                        "flat color=primary"
                    )
                return

            missing = repo.count_missing_parts_from_orders(process=process)
            if missing:
                ui.label(f"Hay {missing} partes sin familia. Completa Config > Familias.").classes("text-amber-700")
                return

            missing_proc = repo.count_missing_process_times_from_orders(process=process)
            if missing_proc:
                ui.label(
                    f"Hay {missing_proc} partes sin tiempos. Completa Config > Maestro materiales."
                ).classes("text-amber-700")
                return

            if len(repo.get_lines(process=process)) == 0:
                ui.label(
                    "Falta configurar líneas. Completa Parámetros > Líneas y familias permitidas."
                ).classes("text-amber-700")
                return

            auto_generate_and_save(process=process, notify=False)

            last = repo.load_last_program(process=process)
            if last is None:
                ui.markdown(
                    "_Aún no se ha generado un programa automáticamente. Asegúrate de actualizar pedidos y completar Familias/Config._"
                )
            else:
                ui.separator()
                generated_on = str(last["generated_on"])
                if "T" in generated_on:
                    ts = datetime.fromisoformat(generated_on)
                else:
                    ts = datetime.combine(date.fromisoformat(generated_on), datetime.min.time())
                ui.label(f"Última actualización: {ts.strftime('%d-%m-%Y %H:%M')}").classes("text-slate-600")
                lines_cfg = repo.get_lines(process=process)
                line_families = {ln["line_id"]: list(ln["families"]) for ln in lines_cfg}
                line_names = {ln["line_id"]: str(ln.get("line_name") or "").strip() for ln in lines_cfg}
                grid = (
                    "w-full grid gap-4 grid-cols-1 md:grid-cols-2 xl:grid-cols-4 items-stretch"
                    if process == "terminaciones"
                    else None
                )
                render_line_tables(
                    last["program"],
                    repo=repo,
                    process=process,
                    line_families=line_families,
                    line_names=line_names,
                    grid_classes=grid,
                )

                errors = list((last.get("errors") or []))
                if errors:
                    ui.separator()
                    ui.label("Órdenes no programadas (errores)").classes("text-xl font-semibold")
                    ui.label(
                        "Estas órdenes no se asignaron porque su familia no está permitida en ninguna línea."
                    ).classes("text-slate-600")
                    ui.table(
                        columns=[
                            {"name": "prio_kind", "label": "", "field": "prio_kind"},
                            {"name": "pedido", "label": "Pedido", "field": "pedido"},
                            {"name": "posicion", "label": "Pos.", "field": "posicion"},
                            {"name": "numero_parte", "label": "Parte", "field": "numero_parte"},
                            {"name": "familia", "label": "Familia", "field": "familia"},
                            {"name": "cantidad", "label": "Cantidad", "field": "cantidad"},
                            {"name": "fecha_entrega", "label": "Entrega", "field": "fecha_entrega"},
                            {"name": "error", "label": "Error", "field": "error"},
                        ],
                        rows=errors,
                        row_key="_row_id",
                    ).classes("w-full").props("dense flat bordered separator=cell wrap-cells")

    @ui.page("/programa")
    def programa() -> None:
        _render_program("terminaciones", active_key="programa_term", title="Programa - Terminaciones")

    @ui.page("/programa/toma-de-dureza")
    def programa_toma_de_dureza() -> None:
        _render_program(
            "toma_de_dureza",
            active_key="programa_toma_dureza",
            title="Programa - Toma de dureza",
        )

    @ui.page("/programa/mecanizado")
    def programa_mecanizado() -> None:
        _render_program("mecanizado", active_key="programa_mecanizado", title="Programa - Mecanizado")

    @ui.page("/programa/mecanizado-externo")
    def programa_mecanizado_externo() -> None:
        _render_program(
            "mecanizado_externo",
            active_key="programa_mecanizado_externo",
            title="Programa - Mecanizado Externo",
        )

    @ui.page("/programa/inspeccion-externa")
    def programa_inspeccion_externa() -> None:
        _render_program(
            "inspeccion_externa",
            active_key="programa_inspeccion_externa",
            title="Programa - Inspección Externa",
        )

    @ui.page("/programa/por-vulcanizar")
    def programa_por_vulcanizar() -> None:
        _render_program(
            "por_vulcanizar",
            active_key="programa_por_vulcanizar",
            title="Programa - Por Vulcanizar",
        )

    @ui.page("/programa/en-vulcanizado")
    def programa_en_vulcanizado() -> None:
        _render_program(
            "en_vulcanizado",
            active_key="programa_en_vulcanizado",
            title="Programa - En Vulcanizado",
        )
