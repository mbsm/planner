from __future__ import annotations

import asyncio
import inspect
from datetime import date, datetime, timedelta

from nicegui import ui

from foundryplan.dispatcher.scheduler import generate_dispatch_program
from foundryplan.data.repository import Repository
from foundryplan.planner.api import prepare_and_sync, run_planner, build_weekly_view, build_orders_plan_summary
from foundryplan.ui.widgets import page_container, render_line_tables, render_nav


def register_pages(repo: Repository) -> None:
    def _format_date_ddmmyy(date_str: str | None) -> str:
        """Convert ISO date string (YYYY-MM-DD) to dd-mm-yy format."""
        if not date_str:
            return ""
        try:
            d = datetime.fromisoformat(str(date_str).strip())
            return d.strftime("%d-%m-%y")
        except (ValueError, TypeError):
            return str(date_str or "")

    def auto_generate_and_save(*, process: str = "terminaciones", notify: bool = True) -> bool:
        process = str(process or "terminaciones").strip().lower()
        updated = False
        try:
            if repo.data.count_orders(process=process) == 0:
                return False
            if repo.data.count_missing_parts_from_orders(process=process) > 0:
                return False
            # tiempo_proceso_min is legacy field, not used by dispatcher (start_by calculated from Part.vulcanizado_dias/mecanizado_dias/inspeccion_externa_dias)
            lines = repo.dispatcher.get_dispatch_lines_model(process=process)
            if not lines:
                return False
            jobs = repo.dispatcher.get_jobs_model(process=process)
            parts = repo.dispatcher.get_parts_model()
            pinned_program, remaining_jobs = repo.dispatcher.build_pinned_program_seed(process=process, jobs=jobs, parts=parts)
            program, errors = generate_dispatch_program(
                lines=lines,
                jobs=remaining_jobs,
                parts=parts,
                pinned_program=pinned_program,
            )
            repo.dispatcher.save_last_program(process=process, program=program, errors=errors)
            updated = True
            if notify:
                label = (repo.data.processes.get(process, {}) or {}).get("label", process)
                ui.notify(f"Programa actualizado automáticamente ({label})")
        except Exception as ex:
            ui.notify(f"Error actualizando programa: {ex}", color="negative")
            return False

        return updated

    def auto_generate_and_save_all(*, notify: bool = False) -> list[str]:
        updated: list[str] = []
        for p in list(repo.data.processes.keys()):
            if auto_generate_and_save(process=p, notify=False):
                updated.append(p)
        if notify and updated:
            labels = [((repo.data.processes.get(p, {}) or {}).get("label", p)) for p in updated]
            ui.notify(f"Programas actualizados: {', '.join(labels)}")
        return updated

    async def refresh_from_sap_all(*, notify: bool = True) -> None:
        """Best-effort: rebuild orders per process (from current MB52+Visión+almacenes) then regenerate programs."""
        rebuilt: list[str] = []
        updated: list[str] = []
        for p in list(repo.data.processes.keys()):
            try:
                ok = await asyncio.to_thread(lambda pp=p: repo.data.try_rebuild_orders_from_sap_for(process=pp))
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
                labels = [((repo.data.processes.get(p, {}) or {}).get("label", p)) for p in updated]
                ui.notify(f"Programas actualizados: {', '.join(labels)}")
            else:
                ui.notify("Datos SAP actualizados. Programas no regenerados (faltan líneas/maestro/tiempos).", color="warning")

    def kick_refresh_from_sap_all(*, notify: bool = True) -> None:
        async def _runner() -> None:
            await refresh_from_sap_all(notify=notify)

        asyncio.create_task(_runner())

    @ui.page("/")
    def dashboard() -> None:
        render_nav(repo=repo)
        with page_container():
            ui.label("Home").classes("text-2xl font-semibold")
            ui.separator()

            kpi_rows = repo.data.get_vision_kpi_daily_rows(limit=180)
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
                atrasadas = [round(float(r.get("tons_atrasadas") or 0.0), 1) for r in kpi_rows]
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
                            "type": "bar",
                            "data": atrasadas,
                            "label": {
                                "show": True,
                                "position": "top",
                                "formatter": "{c}",
                            },
                        }
                    ],
                }
            ).classes("w-full")

            overdue = repo.data.get_orders_overdue_rows(limit=2000)
            due_soon = repo.data.get_orders_due_soon_rows(days=49, limit=200)

            # Para alinear el título con el último valor del gráfico (KPI diario),
            # usamos la última muestra de `tons_atrasadas` si existe.
            # Fallback: suma de categorías calculadas desde las filas
            ready_tons_total = sum(
                float(r.get("tons_dispatch") or 0.0) for r in overdue if int(r.get("pendientes") or 0) == 0
            )
            to_mfg_tons_total = sum(
                float(r.get("tons") or 0.0) for r in overdue if int(r.get("pendientes") or 0) > 0
            )
            # CAUTION: We force the total to match the sum of displayed subtotals, ignoring potentially stale KPI table
            overdue_tons = ready_tons_total + to_mfg_tons_total
            due_soon_tons = sum(float(r.get("tons") or 0.0) for r in due_soon)

            # Pre-format tons for display (1 decimal) while keeping numeric `tons` for calculations.
            for r in overdue:
                r["tons_fmt"] = f"{float(r.get('tons') or 0.0):,.1f}"
                r["fecha_de_pedido"] = _format_date_ddmmyy(r.get("fecha_de_pedido"))
                # Show only last 5 digits of plano
                plano = str(r.get("material") or "")
                r["numero_parte_fmt"] = plano[-5:] if len(plano) >= 5 else plano
            for r in due_soon:
                r["tons_fmt"] = f"{float(r.get('tons') or 0.0):,.1f}"
                r["fecha_de_pedido"] = _format_date_ddmmyy(r.get("fecha_de_pedido"))
                # Show only last 5 digits of plano
                plano = str(r.get("material") or "")
                r["numero_parte_fmt"] = plano[-5:] if len(plano) >= 5 else plano

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
                            data = repo.data.get_vision_stage_breakdown(pedido=pedido, posicion=posicion)
                        except Exception as ex:
                            ui.notify(f"No se pudo leer Visión: {ex}", color="negative")
                            return

                        dialog = ui.dialog().props("persistent")
                        with dialog:
                            with ui.card().classes("bg-white p-6").style("width: 92vw; max-width: 720px"):
                                solicitado = int(data.get("solicitado") or 0)
                                title = f"Pedido {pedido} / {posicion} — Solicitado: {solicitado}"
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
                                                (str(data.get("fecha_de_pedido") or "").strip() or None),
                                            ]
                                            if p
                                        ]
                                    )
                                    if meta:
                                        ui.label(meta).classes("text-sm text-slate-600")

                                    ui.label("Producción").classes("text-lg font-semibold mt-2")
                                    stages = list(data.get("stages") or [])
                                    # Filtrar "Por programar en la planta"
                                    stages = [s for s in stages if str(s.get("estado", "")).strip().lower() != "por programar en la planta"]
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

                                    # Tabla de Calidad (separada)
                                    quality_stages = list(data.get("quality_stages") or [])
                                    if quality_stages:
                                        ui.separator().classes("my-3")
                                        ui.label("Calidad").classes("text-lg font-semibold mt-2")
                                        for r in quality_stages:
                                            v = r.get("piezas")
                                            r["piezas_fmt"] = str(int(v or 0))

                                        ui.table(
                                            columns=[
                                                {"name": "estado", "label": "Estado", "field": "estado"},
                                                {"name": "piezas", "label": "Piezas", "field": "piezas_fmt"},
                                            ],
                                            rows=quality_stages,
                                            row_key="_row_id",
                                        ).classes("w-full").props("dense flat bordered")

                                ui.separator().classes("my-3")
                                prio_set_current = repo.dispatcher.get_priority_orderpos_set()
                                test_set_current = repo.dispatcher.get_test_orderpos_set()
                                is_prio = (pedido, posicion) in prio_set_current
                                is_test = (pedido, posicion) in test_set_current
                                priority_chk = ui.checkbox("Marcar como prioridad", value=is_prio)
                                if is_test:
                                    priority_chk.props("disable")
                                    ui.label("Prioridad fija por prueba (lote con letras)").classes("text-sm text-slate-600")

                                def _save_priority() -> None:
                                    try:
                                        repo.dispatcher.set_pedido_priority(pedido=pedido, posicion=posicion, is_priority=bool(priority_chk.value))
                                        ui.notify("Guardado")
                                        dialog.close()
                                        # Actualizar dispatcher y recargar página
                                        kick_refresh_from_sap_all(notify=False)
                                        ui.navigate.to("/")
                                    except Exception as ex:
                                        ui.notify(f"Error guardando: {ex}", color="negative")

                                with ui.row().classes("w-full justify-end mt-2"):
                                    ui.button("Cerrar", on_click=dialog.close).props("flat")
                                    ui.button("Guardar", on_click=_save_priority).props("color=primary")

                        dialog.open()

                    if overdue:
                        # Annotate overdue rows with priority flags
                        prio_set = repo.dispatcher.get_priority_orderpos_set()
                        test_set = repo.dispatcher.get_test_orderpos_set()
                        for r in overdue:
                            key = (str(r.get("pedido", "")).strip(), str(r.get("posicion", "")).strip())
                            if key in test_set:
                                r["is_priority"] = 1
                                r["priority_kind"] = "test"
                            elif key in prio_set:
                                r["is_priority"] = 1
                                r["priority_kind"] = "priority"
                            else:
                                r["is_priority"] = 0
                                r["priority_kind"] = ""

                        # Separate overdue orders by pending status
                        ready_to_dispatch = [r for r in overdue if int(r.get("pendientes") or 0) == 0]
                        to_manufacture = [r for r in overdue if int(r.get("pendientes") or 0) > 0]
                        
                        # Pre-format tons_dispatch for display
                        for r in ready_to_dispatch:
                            r["tons_dispatch_fmt"] = f"{float(r.get('tons_dispatch') or 0.0):,.1f}"
                            r["fecha_de_pedido"] = _format_date_ddmmyy(r.get("fecha_de_pedido"))
                        for r in to_manufacture:
                            r["fecha_de_pedido"] = _format_date_ddmmyy(r.get("fecha_de_pedido"))
                        # Calculate tons for each category
                        ready_tons = sum(float(r.get("tons_dispatch") or 0.0) for r in ready_to_dispatch)
                        to_mfg_tons = sum(float(r.get("tons") or 0.0) for r in to_manufacture)
                        
                        columns_ready = [
                            {"name": "is_priority", "label": "", "field": "is_priority"},
                            {"name": "cliente", "label": "Cliente", "field": "cliente"},
                            {"name": "pedido", "label": "Pedido", "field": "pedido"},
                            {"name": "posicion", "label": "Pos.", "field": "posicion"},
                            {"name": "numero_parte", "label": "Plano", "field": "numero_parte_fmt"},
                            {"name": "solicitado", "label": "Cant de Pedido", "field": "solicitado"},
                            {"name": "bodega", "label": "En Bodega", "field": "bodega"},
                            {"name": "tons", "label": "Tons por Despachar", "field": "tons_dispatch_fmt"},
                            {"name": "fecha_de_pedido", "label": "Fecha de Pedido", "field": "fecha_de_pedido"},
                            {"name": "dias", "label": "Días atraso", "field": "dias"},
                        ]
                        
                        columns_to_mfg = [
                            {"name": "is_priority", "label": "", "field": "is_priority"},
                            {"name": "cliente", "label": "Cliente", "field": "cliente"},
                            {"name": "pedido", "label": "Pedido", "field": "pedido"},
                            {"name": "posicion", "label": "Pos.", "field": "posicion"},
                            {"name": "numero_parte", "label": "Plano", "field": "numero_parte_fmt"},
                            {"name": "solicitado", "label": "Cant de Pedido", "field": "solicitado"},
                            {"name": "pendientes", "label": "Pendientes", "field": "pendientes"},
                            {"name": "tons", "label": "Tons por Entregar", "field": "tons_fmt"},
                            {"name": "fecha_de_pedido", "label": "Fecha de Pedido", "field": "fecha_de_pedido"},
                            {"name": "dias", "label": "Días atraso", "field": "dias"},
                        ]
                        
                        # Double click to show Visión Planta breakdown by stage.
                        def _on_overdue_dblclick(e) -> None:
                            r = _pick_row(getattr(e, "args", None))
                            if r is not None:
                                _open_vision_breakdown(r)
                            else:
                                ui.notify("No se pudo leer la fila seleccionada", color="negative")
                        
                        # Pedidos por despachar (sin pendientes)
                        if ready_to_dispatch:
                            ui.label(f"Pendiente por Despachar — {len(ready_to_dispatch)} pedidos, {ready_tons:,.1f} tons").classes("text-md font-semibold mt-4")
                            tbl_ready = ui.table(
                                columns=columns_ready,
                                rows=ready_to_dispatch,
                                row_key="_row_id",
                            ).classes("w-full").props("dense flat bordered")
                            tbl_ready.add_slot(
                                "body-cell-is_priority",
                                r"""
<q-td :props="props">
    <q-icon v-if="Number(props.value) === 1 && String(props.row.priority_kind || '').toLowerCase() === 'test'" name="science" color="warning" size="18px"></q-icon>
    <q-icon v-else-if="Number(props.value) === 1" name="priority_high" color="negative" size="18px"></q-icon>
    <q-icon v-else name="remove" color="grey-5" size="18px"></q-icon>
</q-td>
""",
                            )
                            tbl_ready.on("rowDblClick", _on_overdue_dblclick)
                            tbl_ready.on("rowDblclick", _on_overdue_dblclick)
                        
                        # Por fabricar (con pendientes)
                        if to_manufacture:
                            ui.label(f"Pendiente por Fabricar — {len(to_manufacture)} pedidos, {to_mfg_tons:,.1f} tons").classes("text-md font-semibold mt-4")
                            tbl_to_mfg = ui.table(
                                columns=columns_to_mfg,
                                rows=to_manufacture,
                                row_key="_row_id",
                            ).classes("w-full").props("dense flat bordered")
                            tbl_to_mfg.add_slot(
                                "body-cell-is_priority",
                                r"""
<q-td :props="props">
    <q-icon v-if="Number(props.value) === 1 && String(props.row.priority_kind || '').toLowerCase() === 'test'" name="science" color="warning" size="18px"></q-icon>
    <q-icon v-else-if="Number(props.value) === 1" name="priority_high" color="negative" size="18px"></q-icon>
    <q-icon v-else name="remove" color="grey-5" size="18px"></q-icon>
</q-td>
""",
                            )
                            tbl_to_mfg.on("rowDblClick", _on_overdue_dblclick)
                            tbl_to_mfg.on("rowDblclick", _on_overdue_dblclick)
                    else:
                        ui.label("No hay pedidos atrasados.").classes("text-slate-600")

                with ui.card().classes("p-4 w-full"):
                    week_0: list[dict] = []
                    week_1: list[dict] = []
                    week_2: list[dict] = []
                    week_3: list[dict] = []
                    week_4: list[dict] = []
                    week_5: list[dict] = []

                    if due_soon or overdue:
                        # Obtener el lunes de esta semana como referencia
                        today = date.today()
                        monday_offset = today.weekday()  # 0=Monday, 6=Sunday
                        monday_this_week = today - timedelta(days=monday_offset)
                        
                        # Función helper para obtener semana (0=esta semana, 1=próxima, etc)
                        def get_week_offset(fecha_str: str) -> int | None:
                            """Retorna la semana (0=esta semana lunes-domingo, 1=próxima, etc)
                            Retorna None si no se puede parsear o está fuera de rango."""
                            try:
                                if isinstance(fecha_str, str):
                                    # Intentar múltiples formatos (dd-mm-yyyy o dd-mm-yy)
                                    fecha = None
                                    for fmt in ["%d-%m-%Y", "%d-%m-%y", "%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"]:
                                        try:
                                            fecha = datetime.strptime(fecha_str.strip(), fmt).date()
                                            break
                                        except Exception:
                                            pass
                                    if fecha is None:
                                        return None
                                elif isinstance(fecha_str, date):
                                    fecha = fecha_str
                                else:
                                    return None
                                
                                # Calcular semana basada en lunes de esta semana
                                days_from_monday = (fecha - monday_this_week).days
                                if days_from_monday < 0:
                                    return None  # Fecha anterior al lunes de esta semana
                                if days_from_monday > 49:  # Máximo 7 semanas
                                    return None
                                
                                week_num = days_from_monday // 7
                                return min(week_num, 5)  # Máximo hasta semana 5
                            except Exception:
                                return None
                        
                        # (Lists initialized above)
                        
                        # Marcar prioridades (manual y pruebas) para mostrar icono en tablas
                        prio_set = repo.dispatcher.get_priority_orderpos_set()
                        test_set = repo.dispatcher.get_test_orderpos_set()

                        # Procesar pedidos atrasados (pueden pertenecer a la semana actual)
                        for r in overdue:
                            week_offset = get_week_offset(r.get("fecha_de_pedido", ""))
                            if week_offset is not None and week_offset == 0:
                                # Pedido atrasado pero en la semana actual
                                r["is_overdue"] = True
                                r["completo"] = int(r.get("pendientes", 1)) == 0
                                key = (str(r.get("pedido", "")).strip(), str(r.get("posicion", "")).strip())
                                if key in test_set:
                                    r["is_priority"] = 1
                                    r["priority_kind"] = "test"
                                elif key in prio_set:
                                    r["is_priority"] = 1
                                    r["priority_kind"] = "priority"
                                else:
                                    r["is_priority"] = 0
                                    r["priority_kind"] = ""
                                week_0.append(r)
                        
                        # Procesar pedidos próximos
                        for r in due_soon:
                            week_offset = get_week_offset(r.get("fecha_de_pedido", ""))
                            r["is_overdue"] = False
                            r["completo"] = int(r.get("pendientes", 1)) == 0
                            key = (str(r.get("pedido", "")).strip(), str(r.get("posicion", "")).strip())
                            if key in test_set:
                                r["is_priority"] = 1
                                r["priority_kind"] = "test"
                            elif key in prio_set:
                                r["is_priority"] = 1
                                r["priority_kind"] = "priority"
                            else:
                                r["is_priority"] = 0
                                r["priority_kind"] = ""
                            
                            # Solo asignar si week_offset es válido
                            if week_offset is None:
                                continue
                            
                            if week_offset == 0:
                                week_0.append(r)
                            elif week_offset == 1:
                                week_1.append(r)
                            elif week_offset == 2:
                                week_2.append(r)
                            elif week_offset == 3:
                                week_3.append(r)
                            elif week_offset == 4:
                                week_4.append(r)
                            elif week_offset == 5:
                                week_5.append(r)

                        # Calculate total from the weeks
                        total_upcoming_tons = sum(float(r.get("tons") or 0.0) for r in (*week_0, *week_1, *week_2, *week_3, *week_4, *week_5))

                        ui.label(f"Entrega Pedidos próximas 5 semanas — Total: {total_upcoming_tons:,.1f} tons").classes("text-lg font-semibold")
                        ui.label("Pedidos agrupados por semana ISO (lunes a domingo).").classes("text-sm text-slate-600")
                        
                        columns_due = [
                            {"name": "is_priority", "label": "", "field": "is_priority"},
                            {"name": "cliente", "label": "Cliente", "field": "cliente"},
                            {"name": "pedido", "label": "Pedido", "field": "pedido"},
                            {"name": "posicion", "label": "Pos.", "field": "posicion"},
                            {"name": "numero_parte", "label": "Plano", "field": "numero_parte_fmt"},
                            {"name": "solicitado", "label": "Cant de Pedido", "field": "solicitado"},
                            {"name": "pendientes", "label": "Pendientes", "field": "pendientes"},
                            {"name": "tons", "label": "Tons por Entregar", "field": "tons_fmt"},
                            {"name": "fecha_de_pedido", "label": "Fecha de Pedido", "field": "fecha_de_pedido"},
                            {"name": "dias", "label": "Días restantes", "field": "dias"},
                            {"name": "completo", "label": "", "field": "completo"},
                        ]
                        
                        # Semana en curso (si no hay pedidos, mostrar tabla vacía con 0.0 tons)
                        ui.separator()
                        week_0_tons = sum(float(r.get("tons") or 0.0) for r in week_0)
                        ui.label(f"Semana en curso — {week_0_tons:,.1f} tons").classes("text-md font-semibold mt-2")
                        if not week_0:
                            ui.label("Sin Pedidos").classes("text-slate-600")
                        tbl_week_0 = ui.table(
                                columns=columns_due,
                                rows=week_0,
                                row_key="_row_id",
                            ).classes("w-full").props("dense flat bordered")
                            
                        tbl_week_0.add_slot(
                                "body-cell-is_priority",
                                r"""
<q-td :props="props">
    <q-icon v-if="Number(props.value) === 1 && String(props.row.priority_kind || '').toLowerCase() === 'test'" name="science" color="warning" size="18px"></q-icon>
    <q-icon v-else-if="Number(props.value) === 1" name="priority_high" color="negative" size="18px"></q-icon>
    <q-icon v-else name="remove" color="grey-5" size="18px"></q-icon>
</q-td>
""",
                            )

                        tbl_week_0.add_slot(
                                "body-cell-completo",
                                r"""
<q-td :props="props">
    <q-icon v-if="props.row.is_overdue === true" name="cancel" color="negative" size="20px"></q-icon>
    <q-icon v-else-if="props.value === true" name="check_circle" color="positive" size="20px"></q-icon>
</q-td>
""",
                            )
                            
                        def _on_week_0_dblclick(e) -> None:
                                r = _pick_row(getattr(e, "args", None))
                                if r is not None:
                                    _open_vision_breakdown(r)
                                else:
                                    ui.notify("No se pudo leer la fila seleccionada", color="negative")
                            
                        tbl_week_0.on("rowDblClick", _on_week_0_dblclick)
                        tbl_week_0.on("rowDblclick", _on_week_0_dblclick)
                        
                        # Semana + 1 (si no hay pedidos, mostrar tabla vacía con 0.0 tons)
                        ui.separator()
                        week_1_tons = sum(float(r.get("tons") or 0.0) for r in week_1)
                        ui.label(f"Semana + 1 — {week_1_tons:,.1f} tons").classes("text-md font-semibold mt-2")
                        if not week_1:
                            ui.label("Sin Pedidos").classes("text-slate-600")
                        tbl_week_1 = ui.table(
                                columns=columns_due,
                                rows=week_1,
                                row_key="_row_id",
                            ).classes("w-full").props("dense flat bordered")
                            
                        tbl_week_1.add_slot(
                                "body-cell-is_priority",
                                r"""
<q-td :props="props">
    <q-icon v-if="Number(props.value) === 1 && String(props.row.priority_kind || '').toLowerCase() === 'test'" name="science" color="warning" size="18px"></q-icon>
    <q-icon v-else-if="Number(props.value) === 1" name="priority_high" color="negative" size="18px"></q-icon>
    <q-icon v-else name="remove" color="grey-5" size="18px"></q-icon>
</q-td>
""",
                            )

                        tbl_week_1.add_slot(
                                "body-cell-completo",
                                r"""
<q-td :props="props">
    <q-icon v-if="props.value === true" name="check_circle" color="positive" size="20px"></q-icon>
</q-td>
""",
                            )
                            
                        def _on_week_1_dblclick(e) -> None:
                                r = _pick_row(getattr(e, "args", None))
                                if r is not None:
                                    _open_vision_breakdown(r)
                                else:
                                    ui.notify("No se pudo leer la fila seleccionada", color="negative")
                            
                        tbl_week_1.on("rowDblClick", _on_week_1_dblclick)
                        tbl_week_1.on("rowDblclick", _on_week_1_dblclick)
                        
                        # Semana + 2 (si no hay pedidos, mostrar tabla vacía con 0.0 tons)
                        ui.separator()
                        week_2_tons = sum(float(r.get("tons") or 0.0) for r in week_2)
                        ui.label(f"Semana + 2 — {week_2_tons:,.1f} tons").classes("text-md font-semibold mt-2")
                        if not week_2:
                            ui.label("Sin Pedidos").classes("text-slate-600")
                        tbl_week_2 = ui.table(
                                columns=columns_due,
                                rows=week_2,
                                row_key="_row_id",
                            ).classes("w-full").props("dense flat bordered")
                            
                        tbl_week_2.add_slot(
                                "body-cell-is_priority",
                                r"""
<q-td :props="props">
    <q-icon v-if="Number(props.value) === 1 && String(props.row.priority_kind || '').toLowerCase() === 'test'" name="science" color="warning" size="18px"></q-icon>
    <q-icon v-else-if="Number(props.value) === 1" name="priority_high" color="negative" size="18px"></q-icon>
    <q-icon v-else name="remove" color="grey-5" size="18px"></q-icon>
</q-td>
""",
                            )

                        tbl_week_2.add_slot(
                                "body-cell-completo",
                                r"""
<q-td :props="props">
    <q-icon v-if="props.value === true" name="check_circle" color="positive" size="20px"></q-icon>
</q-td>
""",
                            )
                            
                        def _on_week_2_dblclick(e) -> None:
                                r = _pick_row(getattr(e, "args", None))
                                if r is not None:
                                    _open_vision_breakdown(r)
                                else:
                                    ui.notify("No se pudo leer la fila seleccionada", color="negative")
                            
                        tbl_week_2.on("rowDblClick", _on_week_2_dblclick)
                        tbl_week_2.on("rowDblclick", _on_week_2_dblclick)
                        
                        # Semana + 3 (si no hay pedidos, mostrar tabla vacía con 0.0 tons)
                        ui.separator()
                        week_3_tons = sum(float(r.get("tons") or 0.0) for r in week_3)
                        ui.label(f"Semana + 3 — {week_3_tons:,.1f} tons").classes("text-md font-semibold mt-2")
                        if not week_3:
                            ui.label("Sin Pedidos").classes("text-slate-600")
                        tbl_week_3 = ui.table(
                                columns=columns_due,
                                rows=week_3,
                                row_key="_row_id",
                            ).classes("w-full").props("dense flat bordered")
                            
                        tbl_week_3.add_slot(
                                "body-cell-is_priority",
                                r"""
<q-td :props="props">
    <q-icon v-if="Number(props.value) === 1 && String(props.row.priority_kind || '').toLowerCase() === 'test'" name="science" color="warning" size="18px"></q-icon>
    <q-icon v-else-if="Number(props.value) === 1" name="priority_high" color="negative" size="18px"></q-icon>
    <q-icon v-else name="remove" color="grey-5" size="18px"></q-icon>
</q-td>
""",
                            )

                        tbl_week_3.add_slot(
                                "body-cell-completo",
                                r"""
<q-td :props="props">
    <q-icon v-if="props.value === true" name="check_circle" color="positive" size="20px"></q-icon>
</q-td>
""",
                            )
                            
                        def _on_week_3_dblclick(e) -> None:
                                r = _pick_row(getattr(e, "args", None))
                                if r is not None:
                                    _open_vision_breakdown(r)
                                else:
                                    ui.notify("No se pudo leer la fila seleccionada", color="negative")
                            
                        tbl_week_3.on("rowDblClick", _on_week_3_dblclick)
                        tbl_week_3.on("rowDblclick", _on_week_3_dblclick)
                        
                        # Semana + 4 (si no hay pedidos, mostrar tabla vacía con 0.0 tons)
                        ui.separator()
                        week_4_tons = sum(float(r.get("tons") or 0.0) for r in week_4)
                        ui.label(f"Semana + 4 — {week_4_tons:,.1f} tons").classes("text-md font-semibold mt-2")
                        if not week_4:
                            ui.label("Sin Pedidos").classes("text-slate-600")
                        tbl_week_4 = ui.table(
                                columns=columns_due,
                                rows=week_4,
                                row_key="_row_id",
                            ).classes("w-full").props("dense flat bordered")
                            
                        tbl_week_4.add_slot(
                                "body-cell-is_priority",
                                r"""
<q-td :props="props">
    <q-icon v-if="Number(props.value) === 1 && String(props.row.priority_kind || '').toLowerCase() === 'test'" name="science" color="warning" size="18px"></q-icon>
    <q-icon v-else-if="Number(props.value) === 1" name="priority_high" color="negative" size="18px"></q-icon>
    <q-icon v-else name="remove" color="grey-5" size="18px"></q-icon>
</q-td>
""",
                            )

                        tbl_week_4.add_slot(
                                "body-cell-completo",
                                r"""
<q-td :props="props">
    <q-icon v-if="props.value === true" name="check_circle" color="positive" size="20px"></q-icon>
</q-td>
""",
                            )
                            
                        def _on_week_4_dblclick(e) -> None:
                                r = _pick_row(getattr(e, "args", None))
                                if r is not None:
                                    _open_vision_breakdown(r)
                                else:
                                    ui.notify("No se pudo leer la fila seleccionada", color="negative")
                            
                        tbl_week_4.on("rowDblClick", _on_week_4_dblclick)
                        tbl_week_4.on("rowDblclick", _on_week_4_dblclick)
                        
                        # Semana + 5 (si no hay pedidos, mostrar tabla vacía con 0.0 tons)
                        ui.separator()
                        week_5_tons = sum(float(r.get("tons") or 0.0) for r in week_5)
                        ui.label(f"Semana + 5 — {week_5_tons:,.1f} tons").classes("text-md font-semibold mt-2")
                        if not week_5:
                            ui.label("Sin Pedidos").classes("text-slate-600")
                        tbl_week_5 = ui.table(
                                columns=columns_due,
                                rows=week_5,
                                row_key="_row_id",
                            ).classes("w-full").props("dense flat bordered")
                            
                        tbl_week_5.add_slot(
                                "body-cell-is_priority",
                                r"""
<q-td :props="props">
    <q-icon v-if="Number(props.value) === 1 && String(props.row.priority_kind || '').toLowerCase() === 'test'" name="science" color="warning" size="18px"></q-icon>
    <q-icon v-else-if="Number(props.value) === 1" name="priority_high" color="negative" size="18px"></q-icon>
    <q-icon v-else name="remove" color="grey-5" size="18px"></q-icon>
</q-td>
""",
                            )

                        tbl_week_5.add_slot(
                                "body-cell-completo",
                                r"""
<q-td :props="props">
    <q-icon v-if="props.value === true" name="check_circle" color="positive" size="20px"></q-icon>
</q-td>
""",
                            )
                            
                        def _on_week_5_dblclick(e) -> None:
                                r = _pick_row(getattr(e, "args", None))
                                if r is not None:
                                    _open_vision_breakdown(r)
                                else:
                                    ui.notify("No se pudo leer la fila seleccionada", color="negative")
                            
                        tbl_week_5.on("rowDblClick", _on_week_5_dblclick)
                        tbl_week_5.on("rowDblclick", _on_week_5_dblclick)
                    else:
                        ui.label("No hay pedidos dentro de las próximas 6 semanas.").classes("text-slate-600")

    @ui.page("/plan")
    def planner_page() -> None:
        render_nav(active="plan", repo=repo)
        with page_container():
            ui.label("Planificador de Producción").classes("text-2xl font-semibold")
            ui.label("Preparación de inputs y ejecución del Planner.").classes("text-sm text-slate-600")
            
            # Container for weekly plan summary table (always visible)
            plan_summary_container = ui.column().classes("w-full mt-4")
            
            def _render_plan_summary(scenario_name: str, plan_res: dict | None = None) -> None:
                """Render or update the plan summary table."""
                plan_summary_container.clear()
                
                try:
                    scenario_id = repo.planner.ensure_planner_scenario(name=scenario_name)
                    
                    # Get orders and parts for context
                    orders_rows = repo.planner.get_planner_orders_rows(scenario_id=scenario_id)
                    parts_rows = repo.planner.get_planner_parts_rows(scenario_id=scenario_id)
                    calendar_rows = repo.planner.get_planner_calendar_rows(scenario_id=scenario_id)
                    
                    # Build parts dict
                    parts_dict = {
                        str(r["part_id"]): type('obj', (), {
                            'net_weight_ton': float(r.get("net_weight_ton") or 0),
                            'pieces_per_mold': float(r.get("pieces_per_mold") or 0),
                            'flask_size': str(r.get("flask_size") or "S"),
                        })()
                        for r in parts_rows
                    }
                    
                    workdays = [date.fromisoformat(r["date"]) for r in calendar_rows]
                    
                    # If no plan result provided, verify if we can show initial state from existing plan
                    # If fetching from DB directly is preferred when plan_res is None:
                    molds_schedule = None
                    if plan_res and plan_res.get("molds_schedule"):
                        molds_schedule = plan_res["molds_schedule"]
                    else:
                        # Try to load existing plan from DB for this scenario
                        # This avoids "Ejecutar 'Generar plan'" message if a plan already exists in DB
                        # Or at least show a structure.
                        # For "Initial Condition", we ideally want to show pending specific to initial WIP.
                        # However, build_weekly_view works on a schedule. 
                        # If we just want to show the table populated, we'd need to fetch planner_plan_daily_order
                        # But that is not exposed in Repo efficiently as a dictionary yet.
                        # Let's see if we can just skip the "return" and let build_weekly_view handle empty schedule
                        # This would show an empty table with headers (weeks) if calendar exists.
                        molds_schedule = {}

                    # Build weekly view (even with empty schedule, to show calendar structure)
                    if not workdays:
                        with plan_summary_container:
                            ui.label("Resumen Semanal del Plan").classes("text-lg font-semibold mb-2")
                            ui.label("Sin calendario configurado. Ejecute 'Preparar Inputs'.").classes("text-sm text-slate-500")
                        return
                    
                    # Fetch initial conditions for visualization
                    # We use today's date as default, assuming scenario matches current preparation
                    initial_flask_inuse = repo.planner.get_planner_initial_flask_inuse_rows(scenario_id=scenario_id, asof_date=date.today())
                    initial_pour_load = repo.planner.get_planner_initial_pour_load_rows(scenario_id=scenario_id, asof_date=date.today())

                    weekly_view = build_weekly_view(
                        molds_schedule,
                        workdays,
                        orders_rows,
                        parts_dict,
                        initial_flask_inuse=initial_flask_inuse,
                        initial_pour_load=initial_pour_load,
                    )
                    
                    with plan_summary_container:
                        ui.label("Resumen Semanal del Plan").classes("text-lg font-semibold mb-2")
                        
                        # Build HTML table
                        weeks = weekly_view["weeks"]
                        weekly_molds = weekly_view["weekly_molds"]
                        weekly_totals = weekly_view["weekly_totals"]
                        order_completion = weekly_view["order_completion"]
                        order_due_week = weekly_view["order_due_week"]
                        
                        if not weeks:
                            ui.label("Sin datos de semanas").classes("text-slate-600")
                            return
                        
                        # Build rows for table: header + totals + orders
                        rows = []
                        
                        # Header row (weeks)
                        header_row = {"ped_pos": "Semana", "weeks": [w["label"] for w in weeks]}
                        
                        # Totals row
                        molds_row = {"ped_pos": "Moldes", "weeks": [str(weekly_totals.get(w["index"], {}).get("molds", 0)) for w in weeks]}
                        tons_row = {"ped_pos": "Toneladas", "weeks": [str(round(weekly_totals.get(w["index"], {}).get("tons", 0), 1)) for w in weeks]}
                        
                        # Flask utilization rows
                        flask_sizes = ["S", "M", "L", "JUMBO"]
                        flask_rows_dict = {}
                        for size in flask_sizes:
                            flask_rows_dict[f"Cajas {size}"] = [
                                str(weekly_totals.get(w["index"], {}).get("flask_util", {}).get(size, 0)) 
                                for w in weeks
                            ]
                        
                        # Order rows (with completion and due markers)
                        order_rows_dict = {}
                        for order_id in sorted(weekly_molds.keys()):
                            week_map = weekly_molds[order_id]
                            week_cells = []
                            for w in weeks:
                                w_idx = w["index"]
                                qty = week_map.get(w_idx, 0)
                                cell_text = str(qty) if qty > 0 else ""
                                
                                # Add markers
                                if w_idx == order_completion.get(order_id):
                                    cell_text += " E"  # Completion marker
                                if w_idx == order_due_week.get(order_id):
                                    cell_text += " *"  # Due date marker
                                
                                week_cells.append(cell_text)
                            
                            order_rows_dict[f"Ped {order_id}"] = week_cells
                        
                        # Render HTML table
                        html_rows = []
                        
                        # Header
                        html_rows.append(f"<tr><th style='min-width: 60px'>{'Concepto'}</th>" + "".join(f"<th style='min-width: 60px'>{w}</th>" for w in header_row["weeks"]) + "</tr>")
                        
                        # Totals section
                        html_rows.append(f"<tr><td class='font-semibold' style='min-width: 60px'>{molds_row['ped_pos']}</td>" + "".join(f"<td class='text-center' style='min-width: 60px'>{v}</td>" for v in molds_row["weeks"]) + "</tr>")
                        html_rows.append(f"<tr><td class='font-semibold' style='min-width: 60px'>{tons_row['ped_pos']}</td>" + "".join(f"<td class='text-center' style='min-width: 60px'>{v}</td>" for v in tons_row["weeks"]) + "</tr>")
                        
                        # Flask rows
                        for flask_label, flask_cells in flask_rows_dict.items():
                            html_rows.append(f"<tr><td class='text-sm text-slate-600' style='min-width: 60px'>{flask_label}</td>" + "".join(f"<td class='text-center text-sm' style='min-width: 60px'>{v}</td>" for v in flask_cells) + "</tr>")
                        
                        # Order rows
                        html_rows.append("<tr><td colspan='100%'><hr class='my-2'></td></tr>")
                        for order_label, order_cells in order_rows_dict.items():
                            html_rows.append(f"<tr><td class='font-mono text-sm' style='min-width: 60px'>{order_label}</td>" + "".join(f"<td class='text-center text-sm' style='min-width: 60px'>{v}</td>" for v in order_cells) + "</tr>")
                        
                        html_content = f"""
                        <table class='w-full border-collapse text-sm'>
                            <thead class='bg-slate-100'>
                                {html_rows[0]}
                            </thead>
                            <tbody>
                                {''.join(html_rows[1:])}
                            </tbody>
                        </table>
                        <div class='mt-2 text-xs text-slate-600'>
                            <p><strong>E</strong> = Semana de terminación (último molde)</p>
                            <p><strong>*</strong> = Semana de fecha de pedido</p>
                        </div>
                        """
                        
                        try:
                            ui.html(html_content).classes("overflow-x-auto")
                        except TypeError:
                            # Fallback for versions requiring sanitize
                            from nicegui.elements.html import Html
                            Html(html_content, sanitize=False).classes("overflow-x-auto")
                
                except Exception as ex:
                    with plan_summary_container:
                        ui.label(f"Error renderizando tabla: {ex}").classes("text-red-600 text-sm")
            
            with ui.row().classes("items-center gap-2"):
                asof = ui.input("Asof (dd-mm-yyyy)", value=date.today().strftime("%d-%m-%Y"))
                scenario = ui.input("Scenario", value="default")
                
                # Initial render with empty/placeholder after scenario is defined
                _render_plan_summary(str(scenario.value or "default").strip() or "default")
                
                # Container for suggested horizon
                suggested_horizon_label = ui.label("")
                
                horizon_days = ui.number(
                    "Horizonte (días hábiles)",
                    value=int(repo.data.get_config(key="planner_horizon_days", default="30") or 30),
                    min=1,
                    step=1,
                ).classes("w-48")
                horizon_buffer = ui.number(
                    "Buffer horizonte (días)",
                    value=int(repo.data.get_config(key="planner_horizon_buffer_days", default="10") or 10),
                    min=0,
                    step=1,
                ).classes("w-48")
                method = ui.select(
                    ["heuristico"],
                    value="heuristico",
                    label="Método",
                ).classes("w-40")

                def _update_suggested_horizon() -> None:
                    """Calculate and display suggested horizon based on orders."""
                    try:
                        scenario_name = str(scenario.value or "default").strip() or "default"
                        scenario_id = repo.planner.ensure_planner_scenario(name=scenario_name)
                        orders_rows = repo.planner.get_planner_orders_rows(scenario_id=scenario_id)
                        calendar_rows = repo.planner.get_planner_calendar_rows(scenario_id=scenario_id)
                        workdays = [date.fromisoformat(r["date"]) for r in calendar_rows]
                        
                        from foundryplan.planner.api import calculate_suggested_horizon
                        suggested = calculate_suggested_horizon(orders_rows, workdays)
                        
                        if suggested is not None:
                            suggested_horizon_label.set_text(
                                f"📅 Horizonte sugerido: {suggested} días"
                            )
                            horizon_days.set_value(suggested)
                        else:
                            suggested_horizon_label.set_text("📅 Horizonte sugerido: (todos los días)")
                    except Exception as ex:
                        suggested_horizon_label.set_text(f"⚠️ Error: {ex}")

                def _run_sync() -> None:
                    try:
                        d = datetime.strptime(str(asof.value or "").strip(), "%d-%m-%Y").date()
                        res = prepare_and_sync(
                            repo.planner,
                            asof_date=d,
                            scenario_name=str(scenario.value or "default"),
                            horizon_buffer_days=int(horizon_buffer.value or 0),
                        )
                        
                        msg = f"Inputs listos. Órdenes: {res.get('orders')}, Partes: {res.get('parts')}"
                        missing_parts = res.get("missing_parts", [])
                        skipped = res.get("skipped_orders", 0)
                        
                        if missing_parts:
                            msg += f" | Omitidos: {skipped} órdenes por {len(missing_parts)} partes sin data."
                            ui.notify(msg, type="warning", multi_line=True, timeout=10000)
                            # Could optionally show dialog with missing parts
                        else:
                            ui.notify(msg, type="positive")
                            
                        # Update suggested horizon after sync
                        _update_suggested_horizon()
                    except Exception as ex:
                        ui.notify(f"Error preparando planner: {ex}", color="negative")

                # Auto-run sync on page load
                ui.timer(0.1, lambda: _run_sync(), once=True)

                ui.button(on_click=_run_sync).props("icon=refresh flat round dense").tooltip("Actualizar Inputs")

                def _run_planner() -> None:
                    try:
                        d = datetime.strptime(str(asof.value or "").strip(), "%d-%m-%Y").date()
                        res = run_planner(
                            repo.planner,
                            asof_date=d,
                            scenario_name=str(scenario.value or "default"),
                            horizon_days=int(horizon_days.value or 0),
                            horizon_buffer_days=int(horizon_buffer.value or 0),
                        )
                        status = str(res.get("status") or "OK")
                        obj = res.get("objective")
                        suggested = res.get("suggested_horizon_days", "N/A")
                        actual = res.get("actual_horizon_days", "N/A")
                        errors = res.get("errors", [])
                        
                        msg = f"Planner {method.value}: {status} obj={obj} (sugerido={suggested}, usado={actual})"
                        if errors:
                            msg += f" | Errores: {len(errors)}"
                        
                        ui.notify(msg, type="positive")
                        
                        # Update both plan summary (top) and detailed results (below)
                        scenario_name = str(scenario.value or "default").strip() or "default"
                        _render_plan_summary(scenario_name, res)
                        _render_planner_results(res)
                    except Exception as ex:
                        ui.notify(f"Error ejecutando planner: {ex}", color="negative")

                # Container for planner results
                planner_results_container = ui.column().classes("w-full mt-4")
                
                def _render_planner_results(plan_res: dict) -> None:
                    """Render weekly plan visualization."""
                    planner_results_container.clear()
                    
                    if not plan_res or not plan_res.get("molds_schedule"):
                        return
                    
                    try:
                        scenario_name = str(scenario.value or "default").strip() or "default"
                        scenario_id = repo.planner.ensure_planner_scenario(name=scenario_name)
                        
                        # Get orders and parts for context
                        orders_rows = repo.planner.get_planner_orders_rows(scenario_id=scenario_id)
                        parts_rows = repo.planner.get_planner_parts_rows(scenario_id=scenario_id)
                        calendar_rows = repo.planner.get_planner_calendar_rows(scenario_id=scenario_id)
                        
                        # Build parts dict
                        parts_dict = {
                            str(r["part_id"]): type('obj', (), {
                                'net_weight_ton': float(r.get("net_weight_ton") or 0),
                                'pieces_per_mold': float(r.get("pieces_per_mold") or 0),
                                'flask_size': str(r.get("flask_size") or "S"),
                            })()
                            for r in parts_rows
                        }
                        
                        workdays = [date.fromisoformat(r["date"]) for r in calendar_rows]
                        
                        # Fetch initial conditions (for consistent view)
                        initial_flask_inuse = repo.planner.get_planner_initial_flask_inuse_rows(scenario_id=scenario_id, asof_date=date.today())
                        initial_pour_load = repo.planner.get_planner_initial_pour_load_rows(scenario_id=scenario_id, asof_date=date.today())

                        # Build weekly view
                        weekly_view = build_weekly_view(
                            plan_res["molds_schedule"],
                            workdays,
                            orders_rows,
                            parts_dict,
                            initial_flask_inuse=initial_flask_inuse,
                            initial_pour_load=initial_pour_load,
                        )
                        
                        with planner_results_container:
                            # Add orders summary table
                            ui.separator().classes("my-4")
                            ui.label("Resumen de Pedidos Planificados").classes("text-lg font-semibold mt-4 mb-2")
                            
                            orders_summary = build_orders_plan_summary(
                                plan_res,
                                workdays,
                                orders_rows,
                                parts_dict,
                            )
                            
                            if orders_summary:
                                # Build table rows
                                table_rows = []
                                for order in orders_summary:
                                    due_date_str = order["due_date"].strftime("%Y-%m-%d") if order["due_date"] else ""
                                    delivery_date_str = order["planned_delivery_date"].strftime("%Y-%m-%d") if order["planned_delivery_date"] else "N/A"
                                    status_color = "bg-green-100" if order["status"] == "A tiempo" else "bg-red-100"
                                    late_days_text = f"+{order['late_days']} días" if order["late_days"] > 0 else "0 días"
                                    
                                    table_rows.append({
                                        "order_id": order["order_id"],
                                        "due_date": due_date_str,
                                        "planned_delivery": delivery_date_str,
                                        "finish_reduction_hours": f"{order['finish_reduction']}h",
                                        "status": order["status"],
                                        "late_days": late_days_text,
                                        "status_class": status_color,
                                    })
                                
                                # Render table
                                ui.table(
                                    columns=[
                                        {"name": "order_id", "label": "Ped-Pos", "field": "order_id", "sortable": True, "align": "left"},
                                        {"name": "due_date", "label": "Fecha de Pedido", "field": "due_date", "sortable": True, "align": "center"},
                                        {"name": "planned_delivery", "label": "Fecha Planificada de Entrega", "field": "planned_delivery", "sortable": True, "align": "center"},
                                        {"name": "finish_reduction_hours", "label": "Reducción de Terminación (h)", "field": "finish_reduction_hours", "sortable": True, "align": "center"},
                                        {"name": "status", "label": "Estado", "field": "status", "sortable": True, "align": "center"},
                                        {"name": "late_days", "label": "Atraso (días)", "field": "late_days", "sortable": True, "align": "center"},
                                    ],
                                    rows=table_rows,
                                    pagination=20,
                                ).classes("w-full").props("dense")
                            else:
                                ui.label("Sin datos de pedidos").classes("text-slate-600")
                    
                    except Exception as ex:
                        with planner_results_container:
                            ui.label(f"Error renderizando resultados: {ex}").classes("text-red-600 text-sm")

                ui.button("Generar plan", on_click=_run_planner).props("outline")

            ui.separator().classes("my-4")

            with ui.row().classes("w-full gap-6 items-start"):
                with ui.card().classes("w-full p-4"):
                    with ui.row().classes("items-center gap-2"):
                        ui.label("Modelos cargados").classes("text-lg font-medium text-slate-700")
                        ui.badge("Opcional").classes("text-xs bg-slate-200 text-slate-700")
                    ui.label("Marca órdenes con modelo activo hoy. Esto afecta el costo de cambios de modelo. Si está vacío, el planner optimizará sin preferencia de patrones.").classes(
                        "text-xs text-slate-500 mb-3"
                    )

                    with ui.row().classes("items-end gap-3 mb-3"):
                        asof_in = ui.input("Asof (dd-mm-yyyy)", value=date.today().strftime("%d-%m-%Y")).classes("w-48")

                        def _load_patterns() -> None:
                            """Load current patterns from DB. If none are saved, shows empty list (graceful degradation)."""
                            scenario_name = str(scenario.value or "default").strip() or "default"
                            scenario_id = repo.planner.ensure_planner_scenario(name=scenario_name)
                            try:
                                asof_date = datetime.strptime(str(asof_in.value or "").strip(), "%d-%m-%Y").date()
                            except Exception:
                                ui.notify("Fecha Asof inválida", color="negative")
                                return

                            orders_rows = repo.planner.get_planner_orders_rows(scenario_id=scenario_id)
                            loaded_rows = repo.planner.get_planner_initial_patterns_loaded(
                                scenario_id=scenario_id,
                                asof_date=asof_date,
                            )
                            loaded_set = {str(r["order_id"]) for r in loaded_rows if int(r.get("is_loaded") or 0) == 1}

                            patterns_container.clear()
                            pattern_inputs.clear()

                            if not orders_rows:
                                with patterns_container:
                                    ui.label("No hay órdenes del planner para este escenario.").classes("text-slate-600")
                                return

                            with patterns_container:
                                for r in orders_rows:
                                    order_id = str(r.get("order_id") or "")
                                    part_id = str(r.get("part_id") or "")
                                    qty = int(r.get("qty") or 0)
                                    due_date = str(r.get("due_date") or "")
                                    prio = int(r.get("priority") or 0)

                                    with ui.row().classes("w-full items-center gap-3 p-2 border-b border-slate-100"):
                                        chk = ui.checkbox(
                                            value=order_id in loaded_set,
                                        ).props("dense")
                                        ui.label(order_id).classes("font-mono text-sm w-40")
                                        ui.label(part_id).classes("text-sm w-32 text-slate-600")
                                        ui.label(f"qty={qty}").classes("text-sm w-24 text-slate-600")
                                        ui.label(f"prio={prio}").classes("text-sm w-24 text-slate-600")
                                        ui.label(due_date).classes("text-sm text-slate-500")

                                    pattern_inputs[order_id] = chk

                        def _save_patterns() -> None:
                            """Save checked patterns to DB. Empty selection is allowed (graceful degradation)."""
                            scenario_name = str(scenario.value or "default").strip() or "default"
                            scenario_id = repo.planner.ensure_planner_scenario(name=scenario_name)
                            try:
                                asof_date = datetime.strptime(str(asof_in.value or "").strip(), "%d-%m-%Y").date()
                            except Exception:
                                ui.notify("Fecha Asof inválida", color="negative")
                                return

                            if not pattern_inputs:
                                ui.notify("No hay modelos cargados para guardar.", color="warning")
                                return

                            rows = [
                                (
                                    int(scenario_id),
                                    asof_date.isoformat(),
                                    str(order_id),
                                    1 if bool(chk.value) else 0,
                                )
                                for order_id, chk in pattern_inputs.items()
                            ]
                            repo.planner.replace_planner_initial_patterns_loaded(scenario_id=scenario_id, rows=rows)
                            ui.notify("Modelos cargados guardados", type="positive")

                        ui.button("Cargar", on_click=_load_patterns).props("outline")
                        ui.button("Guardar", on_click=_save_patterns).props("outline")

                    patterns_container = ui.column().classes("w-full max-h-[420px] overflow-y-auto border rounded")
                    pattern_inputs: dict[str, ui.checkbox] = {}

    @ui.page("/audit")
    def audit_log() -> None:
        render_nav(active="audit", repo=repo)
        with page_container():
            ui.label("Auditoría").classes("text-2xl font-semibold")
            ui.separator()
            
            rows = [
                {
                    "id": e.id,
                    "timestamp": e.timestamp,
                    "category": e.category,
                    "message": e.message,
                    "details": e.details or ""
                }
                for e in repo.data.get_recent_audit_entries(limit=500)
            ]
            
            ui.table(
                columns=[
                    {"name": "timestamp", "label": "Fecha/Hora", "field": "timestamp", "sortable": True},
                    {"name": "category", "label": "Categoría", "field": "category", "sortable": True},
                    {"name": "message", "label": "Mensaje", "field": "message", "sortable": True, "align": "left"},
                    {"name": "details", "label": "Detalles", "field": "details", "sortable": False, "align": "left"},
                ],
                rows=rows,
                pagination=20
            ).classes("w-full").props("dense")

    @ui.page("/config")
    def config_lines() -> None:
        render_nav(active="config_lineas", repo=repo)
        with page_container():
            with ui.row().classes("items-center justify-between w-full mb-4"):
                ui.label("Configuración").classes("text-2xl font-semibold text-slate-800")
                ui.label("Parámetros globales y configuración de líneas").classes("text-slate-500")

            # --- Global Configuration Section ---
            with ui.row().classes("w-full gap-6 items-start"):
                # Card 1: General SAP Parameters
                with ui.card().classes("flex-1 min-w-[300px] p-4"):
                    ui.label("Parámetros Generales").classes("text-lg font-medium text-slate-700 mb-2")
                    with ui.column().classes("w-full gap-2"):
                        planta_in = ui.input(
                            "Nombre Planta",
                            value=repo.data.get_config(key="planta", default="Planta Rancagua") or "Planta Rancagua",
                        ).classes("w-full")
                        
                        with ui.row().classes("w-full gap-2"):
                            centro_in = ui.input(
                                "Centro SAP",
                                value=repo.data.get_config(key="sap_centro", default="4000") or "4000",
                            ).classes("flex-1")
                            vision_prefixes_in = ui.input(
                                "Prefijos Material (Visión Planta)",
                                value=repo.data.get_config(key="sap_vision_material_prefixes", default="401,402,403,404") or "401,402,403,404",
                                placeholder="Ej: 401,402,403,404 o *",
                            ).props("hint='Separa con comas. Solo Visión Planta'").classes("flex-[2]")

                        ui.separator().classes("my-2")
                        allow_move_line_chk = ui.checkbox(
                            "UI: Mover filas 'en proceso'",
                            value=str(repo.data.get_config(key="ui_allow_move_in_progress_line", default="0") or "0").strip() == "1",
                        ).classes("text-sm text-slate-600")

                # Card 2: Warehouse Mapping
                with ui.card().classes("flex-[2] min-w-[400px] p-4"):
                    with ui.row().classes("items-center justify-between w-full mb-2"):
                        ui.label("Mapeo de Almacenes SAP").classes("text-lg font-medium text-slate-700")
                        ui.icon("warehouse", size="sm", color="grey-6")
                    
                    ui.label("Códigos de almacén usados para filtrar stock y asignar procesos.").classes("text-xs text-slate-400 mb-3")

                    with ui.grid(columns=3).classes("w-full gap-4"):
                        almacen_in = ui.input("Terminaciones (Main)", value=repo.data.get_config(key="sap_almacen_terminaciones", default="4035") or "4035")
                        dura_in = ui.input("Toma de dureza", value=repo.data.get_config(key="sap_almacen_toma_dureza", default="4035") or "4035")
                        mec_in = ui.input("Mecanizado", value=repo.data.get_config(key="sap_almacen_mecanizado", default="4049") or "4049")
                        mec_ext_in = ui.input("Mec. Externo", value=repo.data.get_config(key="sap_almacen_mecanizado_externo", default="4050") or "4050")
                        insp_ext_in = ui.input("Insp. Externa", value=repo.data.get_config(key="sap_almacen_inspeccion_externa", default="4046") or "4046")
                        por_vulc_in = ui.input("Por Vulcanizar", value=repo.data.get_config(key="sap_almacen_por_vulcanizar", default="4047") or "4047")
                        en_vulc_in = ui.input("En Vulcanizado", value=repo.data.get_config(key="sap_almacen_en_vulcanizado", default="4048") or "4048")
                        moldeo_in = ui.input("Moldeo (Planner)", value=repo.data.get_config(key="sap_almacen_moldeo", default="4032") or "4032")
            
            ui.separator().classes("my-4")
            
            # Global save button (outside cards)
            with ui.row().classes("w-full justify-end"):
                def save_cfg() -> None:
                    repo.data.set_config(key="planta", value=str(planta_in.value or "").strip())
                    repo.data.set_config(key="sap_centro", value=str(centro_in.value or "").strip())
                    repo.data.set_config(key="sap_almacen_terminaciones", value=str(almacen_in.value or "").strip())
                    repo.data.set_config(key="sap_vision_material_prefixes", value=str(vision_prefixes_in.value or "").strip())
                    repo.data.set_config(key="sap_almacen_toma_dureza", value=str(dura_in.value or "").strip())
                    repo.data.set_config(key="sap_almacen_mecanizado", value=str(mec_in.value or "").strip())
                    repo.data.set_config(key="sap_almacen_mecanizado_externo", value=str(mec_ext_in.value or "").strip())
                    repo.data.set_config(key="sap_almacen_inspeccion_externa", value=str(insp_ext_in.value or "").strip())
                    repo.data.set_config(key="sap_almacen_por_vulcanizar", value=str(por_vulc_in.value or "").strip())
                    repo.data.set_config(key="sap_almacen_en_vulcanizado", value=str(en_vulc_in.value or "").strip())
                    repo.data.set_config(key="sap_almacen_moldeo", value=str(moldeo_in.value or "").strip())
                    repo.data.set_config(
                        key="ui_allow_move_in_progress_line",
                        value="1" if bool(allow_move_line_chk.value) else "0",
                    )
                    ui.notify("Configuración guardada", type="positive")
                    ui.notify("Actualizando rangos/programas...")
                    kick_refresh_from_sap_all(notify=False)
                
                ui.button("Guardar Configuración", icon="save", on_click=save_cfg).props("unelevated color=primary")

    @ui.page("/actualizar")
    def actualizar_data() -> None:
        render_nav(active="actualizar", repo=repo)
        with page_container():
            ui.label("Actualizar datos SAP").classes("text-2xl font-semibold")
            ui.label("Sube MB52 y Visión Planta. Centro/Almacén se configuran en Parámetros.").classes("pt-subtitle")

            def uploader(kind: str, label: str):
                async def handle_upload(e):
                    try:
                        content = None
                        # Try 'content' (NiceGUI 2.0+ standard?)
                        if hasattr(e, 'content'):
                            content = e.content.read()
                        # Try 'file' (Old NiceGUI or internal slot)
                        elif hasattr(e, 'file'):
                            f = e.file
                            try:
                                if hasattr(f, 'read'):
                                    if inspect.iscoroutinefunction(f.read):
                                        content = await f.read()
                                    else:
                                        content = f.read()
                                else:
                                    # Fallback: maybe 'file' is the content itself?
                                    content = f
                            except Exception as ex_read:
                                raise

                        if content is None:
                            raise Exception(f"Could not extract file content. Attributes: {dir(e)}")

                        if kind in {"mb52", "sap_mb52"}:
                            repo.data.import_sap_mb52_bytes(content=content, mode="replace")
                        else:
                            repo.data.import_excel_bytes(kind=kind, content=content)
                        
                        # Try to get filename safely
                        filename = getattr(e, 'name', None)
                        if not filename and hasattr(e, 'file') and hasattr(e.file, 'name'):
                             filename = e.file.name
                        extra = f" ({filename})" if filename else ""

                        if kind in {"mb52", "sap_mb52"}:
                            ui.notify(f"Importado: MB52{extra} (filas: {repo.data.count_sap_mb52()})")
                        elif kind in {"vision", "vision_planta", "sap_vision"}:
                            ui.notify(f"Importado: Visión Planta{extra} (filas: {repo.data.count_sap_vision()})")
                        elif kind in {"demolding", "sap_demolding", "desmoldeo", "reporte_desmoldeo"}:
                            ui.notify(f"Importado: Reporte Desmoldeo{extra} (filas: {repo.data.count_sap_demolding()})")
                        else:
                            ui.notify(f"Importado: {kind}{extra}")

                        # Check for missing master data (MB52 or Visión)
                        if kind in {"mb52", "sap_mb52", "vision", "vision_planta", "sap_vision"}:
                            missing_by_material: dict[str, dict] = {}
                            
                            # Check MB52 missing parts
                            for proc in repo.data.processes.keys():
                                for it in repo.data.get_missing_parts_from_mb52_for(process=proc):
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
                                    # Populate existing master data if available
                                    for key in [
                                        "family_id",
                                        "vulcanizado_dias",
                                        "mecanizado_dias",
                                        "inspeccion_externa_dias",
                                        "mec_perf_inclinada",
                                        "sobre_medida_mecanizado",
                                        "aleacion",
                                        "piezas_por_molde",
                                        "peso_unitario_ton",
                                        "tiempo_enfriamiento_molde_dias",
                                    ]:
                                        if key not in rec:
                                            rec[key] = it.get(key)
                            
                            # Check Visión missing parts
                            for it in repo.data.get_missing_parts_from_vision_for():
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
                                rec["processes"].add("Visión")
                                # Populate existing master data if available
                                for key in [
                                    "family_id",
                                    "vulcanizado_dias",
                                    "mecanizado_dias",
                                    "inspeccion_externa_dias",
                                    "mec_perf_inclinada",
                                    "sobre_medida_mecanizado",
                                    "aleacion",
                                    "piezas_por_molde",
                                    "tiempo_enfriamiento_molde_dias",
                                ]:
                                    if key not in rec:
                                        rec[key] = it.get(key)

                            missing_master = [missing_by_material[k] for k in sorted(missing_by_material.keys())]
                            if missing_master:
                                families = repo.data.list_families() or ["Parrillas", "Lifters", "Corazas", "Otros"]
                                # persistent: prevents closing on backdrop click or ESC
                                dialog = ui.dialog().props("persistent backdrop-filter='blur(4px)'")
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

                                                    fam_val = str(it.get("family_id") or "Otros") if it.get("family_id") else "Otros"
                                                    v_val = int(it.get("vulcanizado_dias") or 0)
                                                    m_val = int(it.get("mecanizado_dias") or 0)
                                                    i_val = int(it.get("inspeccion_externa_dias") or 0)
                                                    mpi_val = bool(int(it.get("mec_perf_inclinada") or 0))
                                                    sm_val = bool(int(it.get("sobre_medida_mecanizado") or 0))
                                                    aleacion_val = str(it.get("aleacion") or "")
                                                    ppm_val = float(it.get("piezas_por_molde") or 0.0)
                                                    tenfr_val = int(it.get("tiempo_enfriamiento_molde_dias") or 0)

                                                    # Layout:
                                                    # Row 1: Familia, Aleacion, Piezas/Molde, Enfriamiento
                                                    # Row 2: Vulc, Mec, Insp, Checkboxes
                                                    with ui.column().classes("w-full gap-1"):
                                                        with ui.row().classes("items-end w-full gap-3"):
                                                            fam = ui.select(families, value=fam_val, label="Familia").classes("w-40")
                                                            ale = ui.input("Aleación", value=aleacion_val).classes("w-28")
                                                            flask_val = str(it.get("flask_size") or "").strip().upper()
                                                            flask = ui.select(["S", "M", "L"], value=flask_val or None, label="Flask").classes("w-20")
                                                            ppm = ui.number("Pza/Molde", value=ppm_val, min=0, step=0.1).classes("w-24")
                                                            tenfr = ui.number("Enfr (d)", value=tenfr_val, min=0, step=1).classes("w-20")

                                                        with ui.row().classes("items-end w-full gap-3"):
                                                            v = ui.number("Vulc (d)", value=v_val, min=0, max=365, step=1).classes("w-24")
                                                            m = ui.number("Mec (d)", value=m_val, min=0, max=365, step=1).classes("w-24")
                                                            i = ui.number("Insp (d)", value=i_val, min=0, max=365, step=1).classes("w-24")
                                                            mpi = ui.checkbox("Mec perf incl.", value=mpi_val).props("dense")
                                                            sm = ui.checkbox("Sobre medida", value=sm_val).props("dense")
                                                    
                                                        entries[material] = {
                                                            "fam": fam, "v": v, "m": m, "i": i,
                                                            "mpi": mpi, "sm": sm,
                                                            "ale": ale, "flask": flask, "ppm": ppm, "tenfr": tenfr
                                                        }
                                                ui.separator().classes("my-2")

                                        ui.separator()
                                        with ui.row().classes("justify-end w-full gap-3"):
                                            ui.button("Cerrar", on_click=dialog.close).props("flat")

                                            def save_all() -> None:
                                                try:
                                                    for material, w in entries.items():
                                                        fam_val = str(w["fam"].value or "Otros").strip() or "Otros"
                                                        repo.data.upsert_part_master(
                                                            material=material,
                                                            family_id=fam_val,
                                                            vulcanizado_dias=int(w["v"].value or 0),
                                                            mecanizado_dias=int(w["m"].value or 0),
                                                            inspeccion_externa_dias=int(w["i"].value or 0),
                                                            mec_perf_inclinada=bool(w["mpi"].value),
                                                            sobre_medida_mecanizado=bool(w["sm"].value),
                                                            aleacion=str(w["ale"].value or "").strip(),
                                                            flask_size=str(w["flask"].value or "").strip(),
                                                            piezas_por_molde=float(w["ppm"].value or 0.0),
                                                            tiempo_enfriamiento_molde_dias=int(w["tenfr"].value or 0),
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

                        await refresh_from_sap_all(notify=False)

                        if kind in {"vision", "vision_planta", "sap_vision"}:
                            try:
                                snap = repo.data.upsert_vision_kpi_daily()
                                ui.notify(
                                    f"KPI guardado ({snap['snapshot_date']}): {float(snap['tons_atrasadas']):,.1f} tons atrasadas / {float(snap['tons_por_entregar']):,.1f} tons por entregar"
                                )
                            except Exception as ex:
                                ui.notify(f"No se pudo guardar KPI: {ex}", color="warning")

                        missing = repo.data.count_missing_parts_from_orders()
                        if missing:
                            ui.notify(f"Hay {missing} números de parte sin familia. Ve a Config > Familias")

                        missing_proc = repo.data.count_missing_process_times_from_orders()
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
                    ui.label(f"Filas cargadas: {repo.data.count_sap_mb52()}").classes("text-sm text-slate-500")

                with ui.card().classes("p-4 w-[min(520px,100%)]"):
                    ui.label("Visión Planta").classes("text-lg font-semibold")
                    ui.label("Pedido/posición con fecha de pedido. Debe incluir columnas Pedido, Posición y Fecha de pedido.").classes(
                        "text-slate-600"
                    )
                    uploader("vision", "Subir Visión Planta (.xlsx)")
                    ui.label(f"Filas cargadas: {repo.data.count_sap_vision()}").classes("text-sm text-slate-500")

            with ui.row().classes("w-full gap-4 items-stretch"):
                with ui.card().classes("p-4 w-[min(520px,100%)]"):
                    ui.label("Reporte Desmoldeo").classes("text-lg font-semibold")
                    ui.label("Estado de moldes en enfriamiento. Columnas: Material, Lote, Flask ID, Demolding Date, Cooling Hours.").classes(
                        "text-slate-600"
                    )
                    uploader("demolding", "Subir Reporte Desmoldeo (.xlsx)")
                    ui.label(f"Filas cargadas: {repo.data.count_sap_demolding()}").classes("text-sm text-slate-500")

            with ui.row().classes("w-full justify-end"):
                def _clear_imported():
                    repo.data.clear_imported_data()
                    ui.notify("Datos borrados")
                    auto_generate_and_save_all(notify=False)
                    ui.navigate.to("/actualizar")

                ui.button("Borrar datos importados", color="negative", on_click=_clear_imported).props("outline")

    @ui.page("/familias")
    def familias() -> None:
        render_nav(active="config_familias", repo=repo)
        with page_container():
            ui.label("Familias").classes("text-2xl font-semibold")
            ui.label("Mantén el catálogo de familias.").classes("pt-subtitle")

            families = repo.data.list_families() or ["Parrillas", "Lifters", "Corazas", "Otros"]

            ui.separator()
            ui.label("Catálogo").classes("text-lg font-semibold")

            rows_all = repo.data.get_families_rows()
            q = ui.input("Buscar familia", placeholder="Ej: Parrillas").classes("w-72")

            def filtered_rows() -> list[dict]:
                needle = str(q.value or "").strip().lower()
                if not needle:
                    return list(rows_all)
                # Field changed to family_id in Repo
                return [r for r in rows_all if needle in str(r.get("family_id", "")).lower()]

            def refresh_rows() -> None:
                nonlocal rows_all, families
                rows_all = repo.data.get_families_rows()
                families = repo.data.list_families() or ["Parrillas", "Lifters", "Corazas", "Otros"]
                tbl.rows = filtered_rows()
                tbl.update()

            tbl = ui.table(
                columns=[
                    {"name": "familia", "label": "Familia", "field": "family_id"},
                    {"name": "parts_count", "label": "# Partes asignadas", "field": "parts_count"},
                ],
                rows=filtered_rows(),
                row_key="family_id",
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
                                repo.data.delete_family(name=state["current"], force=bool(force_reassign.value))
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
                                    repo.data.add_family(name=name)
                                    ui.notify("Familia agregada")
                                else:
                                    new_name = str(rename_to.value).strip() or name
                                    if new_name != state["current"]:
                                        repo.data.rename_family(old=state["current"], new=new_name)
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

                        if row_found is None and "family_id" in d:
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
                    fam = str(row.get("family_id") or "").strip()
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
        render_nav(active="config_materiales", repo=repo)
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
        render_nav(active="config_materiales", repo=repo)
        with page_container():
            ui.label("Maestro de materiales").classes("text-2xl font-semibold")
            ui.label("Edita familia y tiempos por material, o elimina materiales del maestro.").classes("pt-subtitle")

            families = repo.data.list_families() or ["Parrillas", "Lifters", "Corazas", "Otros"]

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
                                repo.data.delete_all_parts()
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

            rows_all = repo.data.get_parts_rows()
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
                    if needle in str(r.get("material", "")).lower():
                        out.append(_decorate_row(r))
                return out

            tbl = ui.table(
                columns=[
                    {"name": "material", "label": "Material", "field": "material"},
                    {"name": "familia", "label": "Familia", "field": "family_id"},
                    {"name": "aleacion", "label": "Aleación", "field": "aleacion"},
                    {"name": "flask", "label": "Flask", "field": "flask_size"},
                    {"name": "ppm", "label": "Pza/Molde", "field": "piezas_por_molde"},
                    {"name": "enfr", "label": "Enfr (h)", "field": "tiempo_enfriamiento_molde_dias"},
                    {"name": "finish_d", "label": "Finish (d)", "field": "finish_days"},
                    {"name": "min_finish_d", "label": "Min Finish (d)", "field": "min_finish_days"},
                    {"name": "vulcanizado_dias", "label": "Vulc (d)", "field": "vulcanizado_dias"},
                    {"name": "mecanizado_dias", "label": "Mec (d)", "field": "mecanizado_dias"},
                    {"name": "inspeccion_externa_dias", "label": "Insp ext (d)", "field": "inspeccion_externa_dias"},
                    {"name": "peso_ton", "label": "Peso Unitario", "field": "peso_unitario_ton"},
                    {"name": "mec_perf_inclinada", "label": "Mec perf incl.", "field": "mec_perf_inclinada"},
                    {"name": "sobre_medida", "label": "Sobre medida", "field": "sobre_medida_mecanizado"},
                ],
                rows=filtered_rows(),
                row_key="material",
            ).props("dense flat bordered")

            tbl.add_slot(
                "body-cell-material",
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
                "body-cell-ppm",
                r"""
<q-td :props="props">
    <span v-if="props.value !== null && props.value !== undefined && String(props.value) !== ''">{{ Number(props.value).toFixed(1) }}</span>
    <span v-else class="text-slate-400">—</span>
</q-td>
""",
            )

            tbl.add_slot(
                "body-cell-enfr",
                r"""
<q-td :props="props">
    <span v-if="props.value !== null && props.value !== undefined && String(props.value) !== ''">{{ Number(props.value) }}</span>
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
                rows_all = repo.data.get_parts_rows()
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

                    with ui.row().classes("w-full items-end gap-4 pt-2"):
                        ale = ui.input("Aleación", value="").classes("w-40")
                        flask = ui.select(["S", "M", "L"], value=None, label="Flask").classes("w-24")
                        ppm = ui.number("Pza/Molde", value=0, min=0, step=0.1).classes("w-32")
                        tenfr = ui.number("Enfr (h)", value=0, min=0, step=0.5).classes("w-32")
                        ale.props("outlined dense")
                        flask.props("outlined dense")
                        ppm.props("outlined dense")
                        tenfr.props("outlined dense")

                    with ui.row().classes("w-full items-end gap-4 pt-2"):
                        finish_d = ui.number("Finish (d)", value=15, min=0, step=1).classes("w-32")
                        min_finish_d = ui.number("Min Finish (d)", value=5, min=0, step=1).classes("w-32")
                        finish_d.props("outlined dense")
                        min_finish_d.props("outlined dense")

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
                                repo.data.upsert_part_master(
                                    material=np_label.text,
                                    family_id=str(fam_sel.value),
                                    vulcanizado_dias=int(v.value) if v.value is not None else None,
                                    mecanizado_dias=int(m.value) if m.value is not None else None,
                                    inspeccion_externa_dias=int(i.value) if i.value is not None else None,
                                    peso_unitario_ton=float(pt.value) if pt.value is not None else None,
                                    mec_perf_inclinada=bool(mpi_chk.value),
                                    sobre_medida_mecanizado=bool(sm_chk.value),
                                    aleacion=str(ale.value or "").strip(),
                                    flask_size=str(flask.value or "").strip(),
                                    piezas_por_molde=float(ppm.value) if ppm.value is not None else None,
                                    tiempo_enfriamiento_molde_dias=int(tenfr.value) if tenfr.value is not None else None,
                                    finish_days=int(finish_d.value) if finish_d.value is not None else None,
                                    min_finish_days=int(min_finish_d.value) if min_finish_d.value is not None else None,
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
                            repo.data.delete_part(material=np_label.text)
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
                    if str(r.get("material", "")).strip() == np_s:
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
                    np_desc.text = repo.data.get_mb52_texto_breve(material=np_s)
                except Exception:
                    np_desc.text = ""
                fam_sel.value = str(row_data.get("family_id") or "Otros")
                v.value = row_data.get("vulcanizado_dias") if row_data.get("vulcanizado_dias") is not None else 0
                m.value = row_data.get("mecanizado_dias") if row_data.get("mecanizado_dias") is not None else 0
                i.value = (
                    row_data.get("inspeccion_externa_dias")
                    if row_data.get("inspeccion_externa_dias") is not None
                    else 0
                )
                pt.value = row_data.get("peso_unitario_ton") if row_data.get("peso_unitario_ton") is not None else 0
                ale.value = str(row_data.get("aleacion") or "")
                flask.value = str(row_data.get("flask_size") or "").strip().upper() or None
                ppm.value = row_data.get("piezas_por_molde") if row_data.get("piezas_por_molde") is not None else 0
                tenfr.value = (
                    row_data.get("tiempo_enfriamiento_molde_dias")
                    if row_data.get("tiempo_enfriamiento_molde_dias") is not None
                    else 0
                )
                finish_d.value = row_data.get("finish_days") if row_data.get("finish_days") is not None else 15
                min_finish_d.value = row_data.get("min_finish_days") if row_data.get("min_finish_days") is not None else 5
                mpi_chk.value = bool(int(row_data.get("mec_perf_inclinada") or 0))
                sm_chk.value = bool(int(row_data.get("sobre_medida_mecanizado") or 0))
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
                            np_val = r0.get("material") or r0.get("numero_parte") or ""
                            open_editor(numero_parte=str(np_val).strip(), row=r0)
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

    @ui.page("/config/planner")
    def config_planner() -> None:
        render_nav(active="config_planner", repo=repo)
        with page_container():
            ui.label("Planner (Moldeo)").classes("text-2xl font-semibold")
            ui.label("Capacidades, flasks y calendario de trabajo.").classes("pt-subtitle")

            def _load_resources(name: str) -> tuple[int, dict]:
                scenario_name = str(name or "default").strip() or "default"
                scenario_id = repo.planner.ensure_planner_scenario(name=scenario_name)
                resources = repo.planner.get_planner_resources(scenario_id=scenario_id) or {}
                return scenario_id, resources

            # Define day_names at module level for use in callbacks
            day_names = ["lun", "mar", "mie", "jue", "vie", "sab", "dom"]
            day_labels = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

            with ui.row().classes("items-end w-full gap-3"):
                scenario_in = ui.input("Scenario", value="default").classes("w-56")

                def _load_click() -> None:
                    _, res = _load_resources(str(scenario_in.value or "default"))
                    molding_max.value = int(res.get("molding_max_per_day") or 0)
                    molding_same.value = int(res.get("molding_max_same_part_per_day") or 0)
                    pour_max.value = float(res.get("pour_max_ton_per_day") or 0.0)
                    molding_shift.value = int(res.get("molding_max_per_shift") or 0)
                    pour_shift.value = float(res.get("pour_max_ton_per_shift") or 0.0)
                    
                    # Load shifts
                    molding_shifts_dict = res.get("molding_shifts") or {}
                    pour_shifts_dict = res.get("pour_shifts") or {}
                    for day_name in day_names:
                        molding_shifts_inputs[day_name].value = int(molding_shifts_dict.get(day_name, 0))
                        pour_shifts_inputs[day_name].value = int(pour_shifts_dict.get(day_name, 0))
                    
                    notes.value = str(res.get("notes") or "")
                    flask_rows = res.get("flask_types", []) or []
                    flask_table.rows = flask_rows
                    flask_table.update()
                    holidays.value = str(repo.data.get_config(key="planner_holidays", default="") or "")
                    horizon_days.value = int(repo.data.get_config(key="planner_horizon_days", default="30") or 30)
                    horizon_buffer.value = int(repo.data.get_config(key="planner_horizon_buffer_days", default="10") or 10)
                    for w in (molding_max, molding_same, pour_max, molding_shift, pour_shift, notes, holidays):
                        w.update()
                    for w in (horizon_days, horizon_buffer):
                        w.update()
                    for inp in molding_shifts_inputs.values():
                        inp.update()
                    for inp in pour_shifts_inputs.values():
                        inp.update()

                ui.button("Cargar", on_click=_load_click).props("outline")

            scenario_id, res = _load_resources(str(scenario_in.value or "default"))

            with ui.row().classes("w-full gap-6 items-start pt-3"):
                with ui.card().classes("flex-1 min-w-[320px] p-4"):
                    ui.label("Capacidades").classes("text-lg font-medium text-slate-700 mb-2")
                    with ui.column().classes("w-full gap-2"):
                        molding_max = ui.number("Máx. moldes/día", value=int(res.get("molding_max_per_day") or 0), min=0, step=1)
                        molding_same = ui.number(
                            "Máx. mismo material/día",
                            value=int(res.get("molding_max_same_part_per_day") or 0),
                            min=0,
                            step=1,
                        )
                        pour_max = ui.number(
                            "Máx. colada (ton/día)",
                            value=float(res.get("pour_max_ton_per_day") or 0.0),
                            min=0,
                            step=0.1,
                        )
                        molding_max.props("outlined dense")
                        molding_same.props("outlined dense")
                        pour_max.props("outlined dense")

                with ui.card().classes("flex-1 min-w-[420px] p-4"):
                    ui.label("Turnos (configuración por día)").classes("text-lg font-medium text-slate-700 mb-2")
                    ui.label("Define capacidades por turno y turnos por día de la semana.").classes("text-xs text-slate-500 mb-3")
                    
                    molding_shift = ui.number("Moldes por turno", value=int(res.get("molding_max_per_shift") or 0), min=0, step=1).classes("w-full")
                    pour_shift = ui.number("Toneladas por turno", value=float(res.get("pour_max_ton_per_shift") or 0.0), min=0, step=0.1).classes("w-full")
                    molding_shift.props("outlined dense")
                    pour_shift.props("outlined dense")
                    
                    ui.label("Turnos por día de la semana").classes("text-sm font-medium text-slate-600 mt-3 mb-1")
                    
                    molding_shifts_dict = res.get("molding_shifts") or {}
                    pour_shifts_dict = res.get("pour_shifts") or {}
                    
                    molding_shifts_inputs = {}
                    pour_shifts_inputs = {}
                    
                    with ui.column().classes("w-full gap-1"):
                        for day_name, day_label in zip(day_names, day_labels):
                            with ui.row().classes("w-full items-center gap-2"):
                                ui.label(day_label).classes("w-20 text-sm")
                                molding_shifts_inputs[day_name] = ui.number(
                                    "M",
                                    value=int(molding_shifts_dict.get(day_name, 0)),
                                    min=0,
                                    max=3,
                                    step=1,
                                ).classes("w-16").props("outlined dense")
                                molding_shifts_inputs[day_name].tooltip("Turnos de moldeo")
                                pour_shifts_inputs[day_name] = ui.number(
                                    "F",
                                    value=int(pour_shifts_dict.get(day_name, 0)),
                                    min=0,
                                    max=3,
                                    step=1,
                                ).classes("w-16").props("outlined dense")
                                pour_shifts_inputs[day_name].tooltip("Turnos de fusión")
            
            with ui.row().classes("w-full gap-6 items-start pt-3"):
                    ui.label("Flasks (tipos configurables)").classes("text-lg font-medium text-slate-700 mb-2")
                    flask_table = ui.table(
                        columns=[
                            {"name": "flask_type", "label": "Código", "field": "flask_type", "sortable": True},
                            {"name": "label", "label": "Etiqueta", "field": "label"},
                            {"name": "qty_total", "label": "Cantidad", "field": "qty_total", "align": "right"},
                            {"name": "codes_csv", "label": "Códigos (prefijos)", "field": "codes_csv"},
                            {"name": "notes", "label": "Notas", "field": "notes"},
                        ],
                        rows=res.get("flask_types", []) or [],
                        row_key="flask_type",
                        pagination={"rowsPerPage": 10},
                    ).classes("w-full")

                    flask_type_in = ui.input("Código", placeholder="Ej: JUMBO").classes("w-full")
                    flask_label_in = ui.input("Etiqueta", placeholder="Nombre descriptivo").classes("w-full")
                    flask_qty_in = ui.number("Cantidad total", value=0, min=0, step=1).classes("w-full")
                    flask_codes_in = ui.input("Códigos (prefijos separados por coma)", placeholder="101, 102").classes("w-full")
                    flask_notes_in = ui.input("Notas", placeholder="Opcional").classes("w-full")
                    for w in (flask_type_in, flask_label_in, flask_qty_in, flask_codes_in, flask_notes_in):
                        w.props("outlined dense")

                    def _fill_form(row: dict | None) -> None:
                        if not row:
                            flask_type_in.value = ""
                            flask_label_in.value = ""
                            flask_qty_in.value = 0
                            flask_codes_in.value = ""
                            flask_notes_in.value = ""
                        else:
                            flask_type_in.value = str(row.get("flask_type") or "")
                            flask_label_in.value = str(row.get("label") or "")
                            flask_qty_in.value = int(row.get("qty_total") or 0)
                            flask_codes_in.value = str(row.get("codes_csv") or "")
                            flask_notes_in.value = str(row.get("notes") or "")
                        for w in (flask_type_in, flask_label_in, flask_qty_in, flask_codes_in, flask_notes_in):
                            w.update()

                    def _on_row_click(e) -> None:
                        try:
                            row = (e.args or {}).get("row")
                        except Exception:
                            row = None
                        if isinstance(row, dict):
                            _fill_form(row)

                    flask_table.on("rowClick", _on_row_click)

                    def _save_flask() -> None:
                        sid = repo.planner.ensure_planner_scenario(name=str(scenario_in.value or "default"))
                        repo.planner.upsert_planner_flask_type(
                            scenario_id=sid,
                            flask_type=str(flask_type_in.value or ""),
                            qty_total=int(flask_qty_in.value or 0),
                            codes_csv=str(flask_codes_in.value or ""),
                            label=str(flask_label_in.value or ""),
                            notes=str(flask_notes_in.value or ""),
                        )
                        _load_click()

                    def _delete_flask() -> None:
                        sid = repo.planner.ensure_planner_scenario(name=str(scenario_in.value or "default"))
                        repo.planner.delete_planner_flask_type(
                            scenario_id=sid,
                            flask_type=str(flask_type_in.value or ""),
                        )
                        _fill_form(None)
                        _load_click()

                    with ui.row().classes("gap-2 pt-2"):
                        ui.button("Guardar flask", on_click=_save_flask).props("color=primary")
                        ui.button("Eliminar", on_click=_delete_flask).props("outline")

            with ui.row().classes("w-full gap-6 items-start pt-3"):
                with ui.card().classes("flex-1 min-w-[320px] p-4"):
                    ui.label("Horizonte de planificación").classes("text-lg font-medium text-slate-700 mb-2")
                    with ui.column().classes("w-full gap-2"):
                        horizon_days = ui.number(
                            "Horizonte (días hábiles)",
                            value=int(repo.data.get_config(key="planner_horizon_days", default="30") or 30),
                            min=1,
                            step=1,
                        )
                        horizon_buffer = ui.number(
                            "Buffer horizonte (días)",
                            value=int(repo.data.get_config(key="planner_horizon_buffer_days", default="10") or 10),
                            min=0,
                            step=1,
                        )
                        horizon_days.props("outlined dense")
                        horizon_buffer.props("outlined dense")

            ui.separator().classes("my-4")

            with ui.row().classes("w-full gap-6 items-start"):
                with ui.card().classes("flex-1 min-w-[320px] p-4"):
                    ui.label("Calendario").classes("text-lg font-medium text-slate-700 mb-2")
                    holidays = ui.textarea(
                        "Feriados (YYYY-MM-DD, separados por coma o línea)",
                        value=str(repo.data.get_config(key="planner_holidays", default="") or ""),
                    ).classes("w-full")
                    holidays.props("outlined")
                    ui.label("Los feriados se excluyen del calendario de trabajo.").classes("text-xs text-slate-500")

                with ui.card().classes("flex-1 min-w-[320px] p-4"):
                    ui.label("Notas").classes("text-lg font-medium text-slate-700 mb-2")
                    notes = ui.textarea("Notas del escenario", value=str(res.get("notes") or "")).classes("w-full")
                    notes.props("outlined")

            with ui.row().classes("w-full justify-end gap-2 pt-4"):
                def save_planner_cfg() -> None:
                    scenario_name = str(scenario_in.value or "default").strip() or "default"
                    scenario_id = repo.planner.ensure_planner_scenario(name=scenario_name)
                    
                    # Build shifts dictionaries
                    molding_shifts = {day_name: int(molding_shifts_inputs[day_name].value or 0) for day_name in day_names}
                    pour_shifts = {day_name: int(pour_shifts_inputs[day_name].value or 0) for day_name in day_names}
                    
                    repo.planner.upsert_planner_resources(
                        scenario_id=scenario_id,
                        molding_max_per_day=int(molding_max.value or 0),
                        molding_max_same_part_per_day=int(molding_same.value or 0),
                        pour_max_ton_per_day=float(pour_max.value or 0.0),
                        molding_max_per_shift=int(molding_shift.value or 0),
                        molding_shifts=molding_shifts,
                        pour_max_ton_per_shift=float(pour_shift.value or 0.0),
                        pour_shifts=pour_shifts,
                        notes=str(notes.value or "").strip() or None,
                    )
                    repo.data.set_config(
                        key="planner_holidays",
                        value=str(holidays.value or "").strip(),
                    )
                    repo.data.set_config(
                        key="planner_horizon_days",
                        value=str(horizon_days.value or "0").strip(),
                    )
                    repo.data.set_config(
                        key="planner_horizon_buffer_days",
                        value=str(horizon_buffer.value or "0").strip(),
                    )
                    ui.notify("Configuración del planner guardada", type="positive")

                ui.button("Guardar Configuración", icon="save", on_click=save_planner_cfg).props("unelevated color=primary")
                ui.button("Ir a Plan", on_click=lambda: ui.navigate.to("/plan")).props("flat color=primary")

    @ui.page("/config/dispatcher")
    def config_dispatcher() -> None:
        render_nav(active="config_dispatcher", repo=repo)
        with page_container():
            with ui.row().classes("items-center justify-between w-full mb-4"):
                ui.label("Configuración del Dispatcher").classes("text-2xl font-semibold text-slate-800")
                ui.label("Prioridades, filtros de disponibilidad y líneas de producción").classes("text-slate-500")

            # --- Priority Weights Section ---
            with ui.card().classes("w-full p-4 mb-8"):
                with ui.row().classes("items-center justify-between w-full mb-2"):
                    ui.label("Prioridades de Programación").classes("text-lg font-medium text-slate-700")
                    ui.icon("low_priority", size="sm", color="grey-6")
                
                ui.label("Define los pesos de prioridad para ordenar trabajos (menor = mayor prioridad).").classes("text-xs text-slate-400 mb-3")

                # Get priority map from config
                import json
                priority_map_str = repo.data.get_config(key="job_priority_map", default='{"prueba": 1, "urgente": 2, "normal": 3}')
                try:
                    priority_map = json.loads(priority_map_str) if isinstance(priority_map_str, str) else priority_map_str
                except Exception:
                    priority_map = {"prueba": 1, "urgente": 2, "normal": 3}
                
                priority_inputs: dict[str, ui.number] = {}
                with ui.grid(columns=3).classes("w-full gap-4 items-center max-w-2xl"):
                    ui.label("Tipo").classes("font-bold text-slate-600")
                    ui.label("Peso").classes("font-bold text-slate-600")
                    ui.label("Descripción").classes("font-bold text-slate-600")
                    
                    # Prueba
                    ui.label("Prueba").classes("text-slate-700 font-medium")
                    prueba_inp = ui.number(value=priority_map.get("prueba", 1), min=1, max=100, step=1).props("dense outlined").classes("w-32")
                    ui.label("Lotes de prueba (mayor prioridad)").classes("text-xs text-slate-500 italic")
                    priority_inputs["prueba"] = prueba_inp
                    
                    # Urgente
                    ui.label("Urgente").classes("text-slate-700 font-medium")
                    urgente_inp = ui.number(value=priority_map.get("urgente", 2), min=1, max=100, step=1).props("dense outlined").classes("w-32")
                    ui.label("Pedidos marcados como prioridad").classes("text-xs text-slate-500 italic")
                    priority_inputs["urgente"] = urgente_inp
                    
                    # Normal
                    ui.label("Normal").classes("text-slate-700 font-medium")
                    normal_inp = ui.number(value=priority_map.get("normal", 3), min=1, max=100, step=1).props("dense outlined").classes("w-32")
                    ui.label("Pedidos regulares").classes("text-xs text-slate-500 italic")
                    priority_inputs["normal"] = normal_inp
                
                with ui.row().classes("w-full justify-end mt-4"):
                    def save_priorities():
                        new_map = {
                            "prueba": int(priority_inputs["prueba"].value or 1),
                            "urgente": int(priority_inputs["urgente"].value or 2),
                            "normal": int(priority_inputs["normal"].value or 3),
                        }
                        import json
                        repo.data.set_config(key="job_priority_map", value=json.dumps(new_map))
                        ui.notify("Prioridades guardadas", type="positive")
                        ui.notify("Actualizando programas...")
                        kick_refresh_from_sap_all(notify=False)
                    
                    ui.button("Guardar Prioridades", icon="save", on_click=save_priorities).props("unelevated color=primary")

            ui.separator().classes("my-8")

            # --- Process Availability Filters Section ---
            with ui.card().classes("w-full p-4"):
                with ui.row().classes("items-center justify-between w-full mb-2"):
                    ui.label("Filtros de Disponibilidad por Proceso").classes("text-lg font-medium text-slate-700")
                    ui.icon("filter_alt", size="sm", color="grey-6")
                
                ui.label("Configura qué stock recuperar del MB52 para cada proceso (Libre utilización / Control de calidad).").classes("text-xs text-slate-400 mb-3")

                process_filters: dict[str, dict] = {}
                
                with ui.grid(columns=4).classes("w-full gap-4 items-center"):
                    ui.label("Proceso").classes("font-bold text-slate-600")
                    ui.label("Almacén").classes("font-bold text-slate-600")
                    ui.label("Libre utilización").classes("font-bold text-slate-600")
                    ui.label("En control de calidad").classes("font-bold text-slate-600")
                    
                    process_list = [
                        ("terminaciones", "Terminaciones"),
                        ("toma_de_dureza", "Toma de dureza"),
                        ("mecanizado", "Mecanizado"),
                        ("mecanizado_externo", "Mec. externo"),
                        ("inspeccion_externa", "Insp. externa"),
                        ("por_vulcanizar", "Por vulcanizar"),
                        ("en_vulcanizado", "En vulcanizado"),
                    ]
                    
                    for proc_id, proc_label in process_list:
                        try:
                            proc_cfg = repo.data.get_process_config(process_id=proc_id)
                        except Exception:
                            proc_cfg = {
                                "process_id": proc_id,
                                "label": proc_label,
                                "sap_almacen": "",
                                "libre_utilizacion": 1,
                                "en_control_calidad": 0,
                            }
                        
                        ui.label(proc_label).classes("text-slate-700")
                        alm_in = ui.input(value=proc_cfg.get("sap_almacen", "")).props("dense outlined").classes("w-full")
                        select_options = ["*", "1", "0"]
                        def _coerce_filter_value(val):
                            if val is None or val == "":
                                return None
                            if val in ("0", "1"):
                                return int(val)
                            if isinstance(val, (int, float, bool)):
                                return int(val)
                            try:
                                return int(val)
                            except Exception:
                                return None

                        libre_val = _coerce_filter_value(proc_cfg.get("libre_utilizacion"))
                        qc_val = _coerce_filter_value(proc_cfg.get("en_control_calidad"))

                        def _to_select_value(v):
                            if v is None:
                                return "*"
                            return str(int(v)) if v in (0, 1) else "*"

                        libre_sel = ui.select(
                            options=select_options,
                            value=_to_select_value(libre_val),
                            with_input=False,
                        ).props("dense outlined").classes("w-full")

                        qc_sel = ui.select(
                            options=select_options,
                            value=_to_select_value(qc_val),
                            with_input=False,
                        ).props("dense outlined").classes("w-full")
                        
                        process_filters[proc_id] = {
                            "almacen": alm_in,
                            "libre": libre_sel,
                            "qc": qc_sel,
                        }
                
                with ui.row().classes("w-full justify-end mt-4"):
                    def save_process_filters():
                        def _from_select_value(v):
                            if v in (None, "", "*"):
                                return None
                            try:
                                return int(v)
                            except Exception:
                                return None

                        for proc_id, inputs in process_filters.items():
                            almacen_val = str(inputs["almacen"].value or "").strip()
                            libre_val = _from_select_value(inputs["libre"].value)
                            qc_val = _from_select_value(inputs["qc"].value)
                            
                            repo.data.update_process_config(
                                process_id=proc_id,
                                sap_almacen=almacen_val if almacen_val else None,
                                libre_utilizacion=libre_val,
                                en_control_calidad=qc_val,
                            )
                        
                        ui.notify("Filtros de proceso guardados", type="positive")
                        ui.notify("Actualizando rangos/programas...")
                        kick_refresh_from_sap_all(notify=False)
                    
                    ui.button("Guardar Filtros de Proceso", icon="save", on_click=save_process_filters).props("unelevated color=secondary")

            ui.separator().classes("my-8")

            # --- Line Configuration Section (Tabs) ---
            ui.label("Configuración de Líneas de Producción").classes("text-xl font-semibold text-slate-800 mb-4")
            
            families = repo.data.list_families() or ["Parrillas", "Lifters", "Corazas", "Otros"]
            
            # Helper to render the editor for a specific process
            def render_lines_editor(process_key: str):
                lines = repo.dispatcher.get_lines(process=process_key)
                
                with ui.column().classes("w-full gap-4"):
                    with ui.row().classes("items-center gap-4"):
                        num_lines = ui.number("Cantidad de Líneas", value=len(lines) or 8, min=1, max=50, step=1).classes("w-32")
                        ui.label("Define cuántas líneas físicas existen para este proceso.").classes("text-sm text-slate-500 italic")

                    rows_container = ui.column().classes("w-full gap-2 p-2 bg-slate-50 rounded border border-slate-200")
                    line_inputs: dict[int, dict] = {} # id -> {name_input, families_select}

                    def rebuild_rows() -> None:
                        rows_container.clear()
                        line_inputs.clear()
                        n = int(num_lines.value or 0)
                        
                        # Load current state
                        current = {
                            ln["line_id"]: {
                                "families": set(ln["families"]),
                                "name": str(ln.get("line_name") or "").strip(),
                                "mec_perf_inclinada": ln.get("mec_perf_inclinada", False),
                                "sobre_medida_mecanizado": ln.get("sobre_medida_mecanizado", False),
                            }
                            for ln in repo.dispatcher.get_lines(process=process_key)
                        }

                        with rows_container:
                            for i in range(1, n + 1):
                                allowed = (current.get(i, {}) or {}).get("families", set(families))
                                name_val = (current.get(i, {}) or {}).get("name", "") or f"Línea {i}"
                                mec_perf_val = (current.get(i, {}) or {}).get("mec_perf_inclinada", False)
                                sobre_medida_val = (current.get(i, {}) or {}).get("sobre_medida_mecanizado", False)
                                
                                with ui.column().classes("w-full gap-2 bg-white p-3 rounded shadow-sm border border-slate-200"):
                                    with ui.row().classes("w-full items-center gap-3"):
                                        ui.label(f"#{i}").classes("font-bold text-slate-600 w-8 text-center")
                                        nm = ui.input("Nombre", value=name_val).props("dense outlined").classes("flex-1")
                                        ms = ui.select(
                                            families,
                                            value=list(allowed),
                                            multiple=True,
                                            label="Familias permitidas",
                                        ).props("dense outlined use-chips").classes("flex-[3]")
                                    
                                    with ui.row().classes("w-full items-center gap-4 pl-10"):
                                        mec_perf_chk = ui.checkbox("Mec. Perf. Inclinada", value=mec_perf_val).classes("text-sm")
                                        sobre_medida_chk = ui.checkbox("Sobre Medida Mecanizado", value=sobre_medida_val).classes("text-sm")
                                    
                                    line_inputs[i] = {
                                        "name": nm,
                                        "families": ms,
                                        "mec_perf_inclinada": mec_perf_chk,
                                        "sobre_medida_mecanizado": sobre_medida_chk,
                                    }
                    
                    rebuild_rows()
                    num_lines.on("change", lambda _: rebuild_rows())

                    def apply_changes():
                        n = int(num_lines.value or 0)
                        if n <= 0: return

                        # Prune lines > n
                        existing_ids = [ln["line_id"] for ln in repo.dispatcher.get_lines(process=process_key)]
                        for line_id in existing_ids:
                            if int(line_id) > n:
                                repo.dispatcher.delete_line(process=process_key, line_id=int(line_id))
                        
                        # Upsert 1..N
                        for i, inputs in line_inputs.items():
                            repo.dispatcher.upsert_line(
                                process=process_key,
                                line_id=i, 
                                line_name=inputs["name"].value, 
                                families=inputs["families"].value or [],
                                mec_perf_inclinada=bool(inputs["mec_perf_inclinada"].value),
                                sobre_medida_mecanizado=bool(inputs["sobre_medida_mecanizado"].value),
                            )
                        
                        updated = auto_generate_and_save(process=process_key, notify=False)
                        msg = "Configuración aplicada." + (" Programa actualizado." if updated else " (Programa pendiente de datos)")
                        ui.notify(msg, type="positive" if updated else "warning")

                    ui.button(f"Aplicar a {process_key.replace('_', ' ').capitalize()}", icon="check", on_click=apply_changes).props("unelevated color=secondary").classes("self-end mt-2")

            # Tabs Interface
            with ui.tabs().classes("w-full text-slate-600 bg-white border-b border-slate-200") as tabs:
                t_term = ui.tab("Terminaciones")
                t_mec = ui.tab("Mecanizado")
                t_mec_ext = ui.tab("Mec. Externo")
                t_insp = ui.tab("Insp. Externa")
                t_porv = ui.tab("Por Vulcanizar")
                t_env = ui.tab("En Vulcanizado")
                t_dureza = ui.tab("Toma Dureza")

            with ui.tab_panels(tabs, value=t_term).classes("w-full bg-transparent p-2"):
                with ui.tab_panel(t_term): render_lines_editor("terminaciones")
                with ui.tab_panel(t_mec): render_lines_editor("mecanizado")
                with ui.tab_panel(t_mec_ext): render_lines_editor("mecanizado_externo")
                with ui.tab_panel(t_insp): render_lines_editor("inspeccion_externa")
                with ui.tab_panel(t_porv): render_lines_editor("por_vulcanizar")
                with ui.tab_panel(t_env): render_lines_editor("en_vulcanizado")
                with ui.tab_panel(t_dureza): render_lines_editor("toma_de_dureza")

    @ui.page("/db")
    def db_browser() -> None:
        render_nav(active="db_browser", repo=repo)
        with page_container():
            ui.label("Explorador de Base de Datos").classes("text-2xl font-semibold")
            ui.label("Vista paginada de tablas (100 filas por página).").classes("pt-subtitle")

            try:
                tables = repo.data.list_db_tables()
            except Exception as ex:
                ui.notify(f"No se pudieron listar tablas: {ex}", color="negative")
                tables = []

            if not tables:
                ui.label("No hay tablas disponibles.").classes("text-slate-600")
                return

            state = {"offset": 0, "total": 0, "limit": 100}

            with ui.row().classes("items-end gap-3 pt-3"):
                table_select = ui.select(options=tables, value=tables[0], label="Tabla").classes("w-64")
                offset_label = ui.label("").classes("text-sm text-slate-600")
                ui.button("Refrescar", on_click=lambda: load_page()).props("outline")

            data_table = ui.table(columns=[], rows=[], row_key="__idx__", pagination={"rowsPerPage": state["limit"]}).classes("w-full")

            def load_page() -> None:
                tbl = str(table_select.value or "").strip()
                if not tbl:
                    ui.notify("Selecciona una tabla", color="warning")
                    return
                try:
                    total = repo.data.count_table_rows(table=tbl)
                    state["total"] = total
                    max_offset = max(0, ((total - 1) // state["limit"]) * state["limit"] if total > 0 else 0)
                    state["offset"] = min(state["offset"], max_offset)
                    rows = repo.data.fetch_table_rows(table=tbl, limit=state["limit"], offset=state["offset"])
                except Exception as ex:
                    ui.notify(f"Error leyendo tabla {tbl}: {ex}", color="negative")
                    return

                columns = []
                if rows:
                    first = rows[0]
                    columns = [{"name": c, "label": c, "field": c} for c in first.keys()]
                data_with_idx = []
                for idx, r in enumerate(rows):
                    r_copy = dict(r)
                    r_copy["__idx__"] = state["offset"] + idx + 1
                    data_with_idx.append(r_copy)

                data_table.columns = columns
                data_table.row_key = "__idx__"
                data_table.rows = data_with_idx
                data_table.update()

                start = state["offset"] + 1 if total > 0 else 0
                end = state["offset"] + len(rows)
                offset_label.text = f"{tbl}: filas {start}-{end} de {total}"

            def go_prev() -> None:
                state["offset"] = max(0, state["offset"] - state["limit"])
                load_page()

            def go_next() -> None:
                if state["offset"] + state["limit"] < state.get("total", 0):
                    state["offset"] += state["limit"]
                    load_page()

            with ui.row().classes("gap-2 pt-2"):
                ui.button("← Anterior", on_click=go_prev).props("outline")
                ui.button("Siguiente →", on_click=go_next).props("outline")

            table_select.on("update:model-value", lambda _: (state.update({"offset": 0}), load_page()))
            load_page()

    # @ui.page("/config/pedidos")
    def config_pedidos() -> None:
        render_nav(active="config_pedidos", repo=repo)
        with page_container():
            ui.label("Pedidos").classes("text-2xl font-semibold")
            ui.label(
                "Marca pedido/posición con prioridad para forzar que entren primero en el programa."
            ).classes(
                "pt-subtitle"
            )

            rows_all = repo.data.get_pedidos_master_rows()
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
                                repo.dispatcher.delete_all_pedido_priorities(keep_tests=True)
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
                rows_all = repo.data.get_pedidos_master_rows()
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
                                repo.dispatcher.set_pedido_priority(
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
        render_nav(active=active_key, repo=repo)
        with page_container():
            ui.label(title).classes("text-2xl font-semibold")

            if repo.data.count_orders(process=process) == 0:
                mb = repo.data.count_sap_mb52()
                vis = repo.data.count_sap_vision()
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

                diag = repo.data.get_sap_rebuild_diagnostics(process=process)
                
                with ui.card().classes("w-full bg-amber-50 border border-amber-200 p-6"):
                    with ui.row().classes("items-center gap-2 mb-4"):
                         ui.icon("warning", color="amber-9").classes("text-2xl")
                         ui.label("Programa no generado").classes("text-lg font-bold text-amber-900")
                    
                    ui.label("No se han podido construir rangos de trabajo. Revisa el diagnóstico de cruce de datos:").classes("text-amber-800 mb-4")

                    with ui.grid(columns=5).classes("w-full gap-4"):
                        def _metric(label: str, val: int, color="slate"):
                            with ui.column().classes("bg-white p-3 rounded shadow-sm border border-slate-100 items-center justify-center"):
                                ui.label(str(val)).classes(f"text-2xl font-bold text-{color}-700")
                                ui.label(label).classes("text-xs text-slate-500 text-center leading-tight")

                        _metric("Total MB52", mb)
                        _metric("Total Visión", vis)
                        _metric("Piezas Usables (Stock)", diag['usable_total'], "blue")
                        _metric("Con Claves (Ped/Pos/Lote)", diag['usable_with_keys'], "blue")
                        _metric("Match Visión (Final)", diag['usable_with_keys_and_vision'], "green")

                # Helpful hint when MB52 doesn't include the configured almacen for this process.
                try:
                    centro_cfg = (repo.data.get_config(key="sap_centro", default="4000") or "").strip()
                    almacenes = repo.data.get_sap_mb52_almacen_counts(centro=centro_cfg, limit=10)
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
                        n = await asyncio.to_thread(lambda: repo.data.rebuild_orders_from_sap_for(process=process))
                        if n > 0:
                            diag2 = repo.data.get_sap_rebuild_diagnostics(process=process)
                            extra = (
                                f" | sin match en Visión: {diag2['distinct_orderpos_missing_vision']}"
                                if diag2.get("distinct_orderpos_missing_vision")
                                else ""
                            )
                            ui.notify(f"Rangos generados: {n}{extra}")
                            ui.navigate.reload()
                        else:
                            d = repo.data.get_sap_rebuild_diagnostics(process=process)
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

            missing = repo.data.count_missing_parts_from_orders(process=process)
            if missing:
                ui.label(f"Hay {missing} partes sin familia. Completa Config > Familias.").classes("text-amber-700")
                return

            missing_proc = repo.data.count_missing_process_times_from_orders(process=process)
            if missing_proc:
                ui.label(
                    f"Hay {missing_proc} partes sin tiempos. Completa Config > Maestro materiales."
                ).classes("text-amber-700")
                return

            if len(repo.dispatcher.get_lines(process=process)) == 0:
                ui.label(
                    "Falta configurar líneas. Completa Parámetros > Líneas y familias permitidas."
                ).classes("text-amber-700")
                return

            # Check cache first to avoid slow regeneration on every render
            last = repo.dispatcher.load_last_program(process=process)
            if last is None:
                auto_generate_and_save(process=process, notify=False)
                last = repo.dispatcher.load_last_program(process=process)

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
                
                errors = list((last.get("errors") or []))
                
                with ui.tabs().classes("w-full text-slate-600").props("dense active-color=primary indicator-color=primary align=left") as tabs:
                    t_prog = ui.tab("Programa", icon="view_column")
                    t_err = None
                    if errors:
                        with ui.tab("No programadas", icon="warning").classes("text-red-800") as t_err:
                            ui.badge(str(len(errors)), color="red").props("floating")

                with ui.tab_panels(tabs, value=t_prog).classes("w-full bg-transparent"):
                    with ui.tab_panel(t_prog).classes("p-0 pt-4"):
                        lines_cfg = repo.dispatcher.get_lines(process=process)
                        line_families = {ln["line_id"]: list(ln["families"]) for ln in lines_cfg}
                        line_names = {ln["line_id"]: str(ln.get("line_name") or "").strip() for ln in lines_cfg}
                        grid = "w-full grid gap-4 grid-cols-1 lg:grid-cols-2 items-start"
                        render_line_tables(
                            last["program"],
                            repo=repo,
                            process=process,
                            line_families=line_families,
                            line_names=line_names,
                            grid_classes=grid,
                        )

                    if errors and t_err:
                        with ui.tab_panel(t_err).classes("p-4"):
                            ui.label("Órdenes no programadas (errores)").classes("text-xl font-semibold")
                            ui.label(
                                "Estas órdenes no se asignaron porque su familia no está permitida en ninguna línea."
                            ).classes("text-slate-600 mb-4")
                            # Format dates in errors
                            for err in errors:
                                err["fecha_de_pedido"] = _format_date_ddmmyy(err.get("fecha_de_pedido"))
                            ui.table(
                                columns=[
                                    {"name": "prio_kind", "label": "", "field": "prio_kind"},
                                    {"name": "pedido", "label": "Pedido", "field": "pedido"},
                                    {"name": "posicion", "label": "Pos.", "field": "posicion"},
                                    {"name": "numero_parte", "label": "Plano", "field": "material"},
                                    {"name": "familia", "label": "Familia", "field": "family_id"},
                                    {"name": "cantidad", "label": "Cantidad", "field": "cantidad"},
                                    {"name": "fecha_de_pedido", "label": "Fecha pedido", "field": "fecha_de_pedido"},
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
