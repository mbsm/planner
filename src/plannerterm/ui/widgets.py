from __future__ import annotations

from contextlib import contextmanager

from nicegui import ui

from plannerterm.data.repository import Repository


_THEME_APPLIED = False


def apply_theme() -> None:
    """Apply a lightweight global theme for a cleaner, more professional UI."""
    try:
        ui.colors(
            primary="#2563eb",  # blue-600
            secondary="#0ea5e9",  # sky-500
            positive="#16a34a",  # green-600
            negative="#dc2626",  # red-600
            warning="#f59e0b",  # amber-500
        )
    except Exception:
        # Keep running even if NiceGUI changes the API.
        pass

    ui.add_css(
        """
        body { background: #f8fafc; }
        .pt-container { max-width: 1200px; margin-left: 0; margin-right: auto; padding: 16px; }
        .pt-subtitle { color: #475569; }
        .pt-header { border-bottom: 1px solid rgba(15, 23, 42, 0.08); }
        .pt-kpi .q-card { border: 1px solid rgba(15, 23, 42, 0.08); }
        /* Program tables: avoid horizontal scroll inside cards */
        .pt-program-table .q-table__middle { overflow-x: hidden; }
        .pt-program-table table { width: 100%; table-layout: fixed; }
        .pt-program-table th, .pt-program-table td { white-space: normal !important; word-break: break-word; }
        .pt-program-table .q-table th, .pt-program-table .q-table td { padding: 6px 8px; }
        """
    )


def ensure_theme() -> None:
    """Apply theme once, but only when called from within a page context."""
    global _THEME_APPLIED
    if _THEME_APPLIED:
        return
    apply_theme()
    _THEME_APPLIED = True


@contextmanager
def page_container():
    with ui.element("div").classes("pt-container"):
        yield


def render_nav(active: str | None = None) -> None:
    ensure_theme()
    active_key = active or "dashboard"
    sections: list[tuple[str, str, str]] = [
        ("dashboard", "Home", "/"),
        ("actualizar", "Actualizar", "/actualizar"),
    ]
    production_program_active = active_key in {
        "programa_toma_dureza",
        "programa_term",
        "programa_mecanizado",
        "programa_mecanizado_externo",
        "programa_inspeccion_externa",
        "programa_por_vulcanizar",
        "programa_en_vulcanizado",
    }
    production_progress_active = active_key in {"avance"}
    config_active = active_key in {"config", "config_lineas", "config_familias", "config_materiales", "config_pedidos"}

    with ui.header().classes("pt-header bg-white text-slate-900"):
        with ui.row().classes("w-full items-center justify-between gap-4 px-4 py-2"):
            with ui.row().classes("items-center gap-3"):
                # Use a plain <img> element instead of q-img (ui.image) to avoid
                # rendering/sizing quirks across NiceGUI/Quasar versions.
                ui.element("img").props('src="/assets/elecmetal.png" alt="Elecmetal"').style(
                    "height: 34px; width: auto; display: block;"
                )
                ui.label("Planta Rancagua").classes("text-xl md:text-2xl font-semibold leading-none")
            with ui.row().classes("items-center gap-1"):
                for key, label, path in sections:
                    is_active = key == active_key
                    props = "dense no-caps" + (" unelevated" if is_active else " flat")
                    btn = ui.button(label, on_click=lambda p=path: ui.navigate.to(p)).props(props)
                    if is_active:
                        btn.props("color=primary")
                    else:
                        btn.props("color=primary")

                prog_props = "dense no-caps" + (" unelevated" if production_program_active else " flat")
                with ui.button("Programas Producción", icon="factory").props(prog_props) as _prog_btn:
                    _prog_btn.props("color=primary")
                    with ui.menu().props("auto-close"):
                        ui.menu_item(
                            "Toma de dureza (4035)",
                            on_click=lambda: ui.navigate.to("/programa/toma-de-dureza"),
                        )
                        ui.menu_item("Terminaciones (4035)", on_click=lambda: ui.navigate.to("/programa"))
                        ui.menu_item("Mecanizado (4049)", on_click=lambda: ui.navigate.to("/programa/mecanizado"))
                        ui.menu_item(
                            "Mecanizado externo (4050)",
                            on_click=lambda: ui.navigate.to("/programa/mecanizado-externo"),
                        )
                        ui.menu_item(
                            "Inspección externa (4046)",
                            on_click=lambda: ui.navigate.to("/programa/inspeccion-externa"),
                        )
                        ui.menu_item(
                            "Por vulcanizar (4047)",
                            on_click=lambda: ui.navigate.to("/programa/por-vulcanizar"),
                        )
                        ui.menu_item(
                            "En vulcanizado (4048)",
                            on_click=lambda: ui.navigate.to("/programa/en-vulcanizado"),
                        )

                prog2_props = "dense no-caps" + (" unelevated" if production_progress_active else " flat")
                with ui.button("Avance Producción", icon="insights").props(prog2_props) as _prog2_btn:
                    _prog2_btn.props("color=primary")
                    with ui.menu().props("auto-close"):
                        ui.menu_item(
                            "Terminaciones (4035)",
                            on_click=lambda: ui.navigate.to("/avance"),
                        )

                cfg_props = "dense no-caps" + (" unelevated" if config_active else " flat")
                with ui.button("Config", icon="settings").props(cfg_props) as _cfg_btn:
                    _cfg_btn.props("color=primary")
                    with ui.menu().props("auto-close"):
                        label_lineas = (
                            "✓ Parámetros"
                            if active_key in {"config", "config_lineas"}
                            else "Parámetros"
                        )
                        label_familias = (
                            "✓ Familias"
                            if active_key == "config_familias"
                            else "Familias"
                        )
                        label_materiales = (
                            "✓ Maestro materiales"
                            if active_key == "config_materiales"
                            else "Maestro materiales"
                        )
                        label_pedidos = (
                            "✓ Pedidos"
                            if active_key == "config_pedidos"
                            else "Pedidos"
                        )

                        ui.menu_item(label_lineas, on_click=lambda: ui.navigate.to("/config"))
                        ui.menu_item(label_familias, on_click=lambda: ui.navigate.to("/familias"))
                        ui.menu_item(label_materiales, on_click=lambda: ui.navigate.to("/config/materiales"))
                        ui.menu_item(label_pedidos, on_click=lambda: ui.navigate.to("/config/pedidos"))


def render_line_tables(
    program: dict[int, list[dict]],
    *,
    repo: Repository | None = None,
    process: str = "terminaciones",
    line_families: dict[int, list[str]] | None = None,
    line_names: dict[int, str] | None = None,
    grid_classes: str | None = None,
) -> None:
    # program: line_id -> list of items (dict)
    grid = grid_classes or "w-full grid gap-4 grid-cols-1 lg:grid-cols-2 items-stretch"
    with ui.element("div").classes(grid):
        def _as_int_or_none(v) -> int | None:
            try:
                return int(v)
            except Exception:
                return None

        def _sort_key(kv) -> tuple[int, str]:
            raw = kv[0]
            n = _as_int_or_none(raw)
            return (n if n is not None else 10**9, str(raw))

        for raw_line_id, items in sorted(program.items(), key=_sort_key):
            line_id_int = _as_int_or_none(raw_line_id)
            lookup_id = line_id_int if line_id_int is not None else raw_line_id
            # Stable numeric line id for this table (avoid late-binding/closure bugs).
            line_id_for_table = line_id_int if line_id_int is not None else (_as_int_or_none(raw_line_id) or 0)
            with ui.card().classes("w-full h-full flex flex-col"):
                display_id = line_id_int if line_id_int is not None else raw_line_id
                line_label = f"Línea {display_id}"
                if line_names and lookup_id in line_names and str(line_names[lookup_id]).strip():
                    line_label = str(line_names[lookup_id]).strip()
                ui.label(line_label).classes("text-xl font-semibold")
                families: list[str] = []
                if line_families and lookup_id in line_families:
                    families = [f for f in line_families[lookup_id] if f]
                elif items:
                    families = sorted(
                        {
                            str(item.get("familia", "")).strip()
                            for item in items
                            if item.get("familia")
                        }
                    )

                if families:
                    ui.label(", ".join(families)).classes("text-sm text-slate-600")

                if not items:
                    ui.label("(sin tareas)").classes("text-gray-500")
                    continue

                rows = list(items)

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
                    # If the leftmost digit is 0, drop it (e.g. 0123 -> 123, 0001 -> 001).
                    if ai_s.startswith("0"):
                        ai_s = ai_s[1:]
                    if bi_s.startswith("0"):
                        bi_s = bi_s[1:]
                    return ai_s if ai_s == bi_s else f"{ai_s}-{bi_s}"

                for r in rows:
                    r["lotes_rango"] = _format_lotes_range(r)
                    r.setdefault("in_progress", 0)
                    r.setdefault("_pt_line_id", int(line_id_for_table))
                    # Keep a stable field for UI logic; scheduler encodes tests via prio_kind.
                    if "is_test" not in r:
                        r["is_test"] = 1 if str(r.get("prio_kind") or "").strip().lower() == "test" else 0

                tbl = ui.table(
                    columns=[
                        {"name": "prio_kind", "label": "", "field": "prio_kind"},
                        {"name": "pedido", "label": "Pedido", "field": "pedido"},
                        {"name": "posicion", "label": "Pos.", "field": "posicion"},
                        {"name": "lotes_rango", "label": "Lotes", "field": "lotes_rango"},
                        {"name": "numero_parte", "label": "Parte", "field": "numero_parte"},
                        {"name": "cantidad", "label": "Cantidad", "field": "cantidad"},
                        {"name": "familia", "label": "Familia", "field": "familia"},
                        {"name": "fecha_entrega", "label": "Entrega", "field": "fecha_entrega"},
                    ],
                    rows=rows,
                    row_key="_row_id",
                ).classes("w-full pt-program-table").props("dense flat bordered separator=cell wrap-cells")

                # Use body-cell slots (like Maestro de materiales) to keep NiceGUI/Quasar row events.
                # We color every cell of the row when in_progress=1 for a dark-green highlight.
                tbl.add_slot(
                    "body-cell",
                    r"""
<q-td
        :props="props"
        :class="Number(props.row.in_progress) === 1 ? 'bg-green-10 text-white' : ''"
>
    {{ props.value }}
</q-td>
""",
                )
                tbl.add_slot(
                    "body-cell-prio_kind",
                    r"""
<q-td
        :props="props"
        :class="Number(props.row.in_progress) === 1 ? 'bg-green-10 text-white' : ''"
>
    <q-icon v-if="props.value === 'test'" name="science" color="warning" size="18px">
        <q-tooltip>Prueba (lote con letras)</q-tooltip>
    </q-icon>
    <q-icon v-else-if="props.value === 'priority'" name="priority_high" color="negative" size="18px">
        <q-tooltip>Prioridad</q-tooltip>
    </q-icon>
    <q-icon v-else name="remove" color="grey-6" size="18px">
        <q-tooltip>Normal</q-tooltip>
    </q-icon>
</q-td>
""",
                )

                if repo is not None:
                    if int(line_id_for_table or 0) <= 0:
                        continue

                    line_id_for_table = int(line_id_for_table)

                    allow_move_line = False
                    try:
                        allow_move_line = (
                            str(repo.get_config(key="ui_allow_move_in_progress_line", default="0") or "0").strip() == "1"
                        )
                    except Exception:
                        allow_move_line = False

                    # Build line options once per table, but based on the whole program shown.
                    def _program_line_options() -> dict[int, str]:
                        # NiceGUI ui.select expects the *value* to be one of the option keys.
                        # Use {value: label} so we can store numeric line_id directly.
                        opts: dict[int, str] = {}
                        for raw_k, _items in sorted(program.items(), key=_sort_key):
                            k_int = _as_int_or_none(raw_k)
                            if k_int is None:
                                continue
                            lookup_k = k_int
                            label = f"Línea {k_int}"
                            if line_names and lookup_k in line_names and str(line_names[lookup_k]).strip():
                                label = str(line_names[lookup_k]).strip()
                            opts[int(k_int)] = label
                        return opts

                    dialog = ui.dialog().props("persistent")
                    selected: dict | None = None

                    title_lbl = None
                    info_lbl = None
                    btn_mark = None
                    btn_unmark = None
                    line_select = None
                    btn_move = None
                    btn_split = None

                    with dialog:
                        with ui.card().classes("bg-white p-6").style("width: 92vw; max-width: 560px"):
                            title_lbl = ui.label("").classes("text-xl font-semibold")
                            info_lbl = ui.label("").classes("text-slate-700 whitespace-pre-line")

                            # Collapsible section: move (optional)
                            if allow_move_line:
                                with ui.expansion("Mover a línea").props("dense").classes("w-full"):
                                    line_select = ui.select(
                                        options=_program_line_options(),
                                        label="Línea destino",
                                        value=int(line_id_for_table),
                                    ).props("outlined dense")
                                    with ui.row().classes("justify-end gap-2"):
                                        btn_move = ui.button("Mover", icon="swap_horiz").props("unelevated color=secondary")

                            # Collapsible section: split
                            with ui.expansion("Dividir (split)").props("dense").classes("w-full"):
                                ui.label("Crea 2 partes y reparte correlativos en forma balanceada.").classes(
                                    "text-xs text-slate-600"
                                )
                                with ui.row().classes("justify-end gap-2"):
                                    btn_split = ui.button("Crear split balanceado", icon="call_split").props(
                                        "unelevated color=primary"
                                    )

                            ui.separator()
                            with ui.row().classes("justify-end gap-2"):
                                ui.button("Cancelar", on_click=dialog.close).props("flat")
                                btn_unmark = ui.button("Quitar en proceso").props("unelevated color=grey-7")
                                btn_mark = ui.button("Marcar en proceso").props("unelevated color=primary")

                    def _pick_row(args) -> dict | None:
                        def _walk(obj):
                            if obj is None:
                                return
                            if isinstance(obj, dict):
                                yield obj
                                for v in obj.values():
                                    yield from _walk(v)
                            elif isinstance(obj, (list, tuple)):
                                for it in obj:
                                    yield from _walk(it)

                        def _looks_like_program_row(d: dict) -> bool:
                            return ("pedido" in d and "posicion" in d) or ("numero_parte" in d and "cantidad" in d)

                        # NiceGUI/Quasar event payload varies depending on slots; be defensive.
                        if isinstance(args, dict) and isinstance(args.get("row"), dict):
                            return args.get("row")

                        for d in _walk(args):
                            # Common shape: {args: {row: {...}}}
                            inner = d.get("args")
                            if isinstance(inner, dict) and isinstance(inner.get("row"), dict):
                                return inner.get("row")
                            # Another common shape: {row: {...}}
                            if isinstance(d.get("row"), dict):
                                return d.get("row")
                            # Sometimes the row dict is emitted directly
                            if _looks_like_program_row(d):
                                return d

                        return None

                    def _refresh_dialog_view() -> None:
                        if not isinstance(selected, dict) or title_lbl is None or info_lbl is None:
                            return
                        pedido = str(selected.get("pedido") or "").strip()
                        posicion = str(selected.get("posicion") or "").strip()
                        parte = str(selected.get("numero_parte") or "").strip()
                        lotes = str(selected.get("lotes_rango") or "").strip()
                        qty = str(selected.get("cantidad") or "").strip()
                        fam = str(selected.get("familia") or "").strip()
                        in_prog = int(selected.get("in_progress") or 0) == 1
                        line_dbg = int(selected.get("_pt_line_id") or line_id_for_table)
                        split_id_dbg = 1
                        try:
                            split_id_dbg = int(selected.get("_pt_split_id") or 1)
                        except Exception:
                            split_id_dbg = 1

                        if line_select is not None:
                            # Default to the current line of the selected row.
                            line_select.value = line_dbg

                        title_lbl.text = f"Pedido {pedido} / {posicion}"
                        info_lbl.text = "\n".join(
                            [
                                f"Línea: {line_dbg}",
                                f"Split: {split_id_dbg}",
                                f"Parte: {parte}",
                                f"Familia: {fam}",
                                f"Lotes: {lotes}",
                                f"Cantidad: {qty}",
                                f"Estado: {'EN PROCESO' if in_prog else 'NO EN PROCESO'}",
                            ]
                        )
                        if btn_mark is not None:
                            btn_mark.visible = not in_prog
                        if btn_unmark is not None:
                            btn_unmark.visible = in_prog
                        if btn_move is not None:
                            btn_move.visible = in_prog
                        if btn_split is not None:
                            # Split only makes sense for in-progress rows.
                            btn_split.visible = in_prog

                    def _do_mark_unmark(*, mark: bool, line_id: int = line_id_for_table) -> None:
                        nonlocal selected
                        if not isinstance(selected, dict):
                            return
                        pedido = str(selected.get("pedido") or "").strip()
                        posicion = str(selected.get("posicion") or "").strip()
                        is_test = int(
                            selected.get("is_test")
                            or (1 if str(selected.get("prio_kind") or "").strip().lower() == "test" else 0)
                        )
                        if not pedido or not posicion:
                            ui.notify("Fila inválida (sin pedido/posición)", color="negative")
                            return
                        try:
                            # Prefer the user's selection, then the row's current line.
                            chosen_line = None
                            if allow_move_line and line_select is not None:
                                chosen_line = line_select.value
                            line_id_effective = int(chosen_line or selected.get("_pt_line_id") or line_id)
                            if mark:
                                repo.mark_in_progress(
                                    process=process,
                                    pedido=pedido,
                                    posicion=posicion,
                                    is_test=is_test,
                                    line_id=line_id_effective,
                                )
                            else:
                                repo.unmark_in_progress(
                                    process=process,
                                    pedido=pedido,
                                    posicion=posicion,
                                    is_test=is_test,
                                )
                            dialog.close()
                            ui.navigate.reload()
                        except Exception as ex:
                            ui.notify(f"Error: {ex}", color="negative")

                    def _do_move() -> None:
                        nonlocal selected
                        if not isinstance(selected, dict):
                            return
                        if not allow_move_line:
                            ui.notify("Mover de línea está deshabilitado en Parámetros", color="warning")
                            return
                        pedido = str(selected.get("pedido") or "").strip()
                        posicion = str(selected.get("posicion") or "").strip()
                        is_test = int(
                            selected.get("is_test")
                            or (1 if str(selected.get("prio_kind") or "").strip().lower() == "test" else 0)
                        )
                        split_id = 1
                        try:
                            split_id = int(selected.get("_pt_split_id") or 1)
                        except Exception:
                            split_id = 1
                        if not pedido or not posicion:
                            ui.notify("Fila inválida (sin pedido/posición)", color="negative")
                            return
                        try:
                            dest = int((line_select.value if line_select is not None else None) or line_id_for_table)
                            # Move only the selected split part.
                            if hasattr(repo, "move_in_progress"):
                                repo.move_in_progress(
                                    process=process,
                                    pedido=pedido,
                                    posicion=posicion,
                                    is_test=is_test,
                                    line_id=dest,
                                    split_id=split_id,
                                )
                            else:
                                # Backward-compatible: move the whole row.
                                repo.mark_in_progress(
                                    process=process,
                                    pedido=pedido,
                                    posicion=posicion,
                                    is_test=is_test,
                                    line_id=dest,
                                )
                            dialog.close()
                            ui.navigate.reload()
                        except Exception as ex:
                            ui.notify(f"Error: {ex}", color="negative")

                    def _do_create_split() -> None:
                        nonlocal selected
                        if not isinstance(selected, dict):
                            return
                        pedido = str(selected.get("pedido") or "").strip()
                        posicion = str(selected.get("posicion") or "").strip()
                        is_test = int(
                            selected.get("is_test")
                            or (1 if str(selected.get("prio_kind") or "").strip().lower() == "test" else 0)
                        )
                        if not pedido or not posicion:
                            ui.notify("Fila inválida (sin pedido/posición)", color="negative")
                            return
                        try:
                            if not hasattr(repo, "create_balanced_split"):
                                raise ValueError("Versión de la app sin soporte de split")
                            repo.create_balanced_split(
                                process=process,
                                pedido=pedido,
                                posicion=posicion,
                                is_test=is_test,
                            )
                            dialog.close()
                            ui.navigate.reload()
                        except Exception as ex:
                            ui.notify(f"Error: {ex}", color="negative")

                    if btn_mark is not None:
                        btn_mark.on_click(lambda line_id=line_id_for_table: _do_mark_unmark(mark=True, line_id=line_id))
                    if btn_unmark is not None:
                        btn_unmark.on_click(lambda line_id=line_id_for_table: _do_mark_unmark(mark=False, line_id=line_id))
                    if btn_move is not None:
                        btn_move.on_click(_do_move)
                    if btn_split is not None:
                        btn_split.on_click(_do_create_split)

                    def _open_dialog(e) -> None:
                        nonlocal selected
                        row = _pick_row(getattr(e, "args", None))
                        if not isinstance(row, dict):
                            ui.notify("No se pudo leer la fila seleccionada", color="negative")
                            return
                        selected = dict(row)
                        _refresh_dialog_view()
                        dialog.open()

                    tbl.on("rowDblClick", _open_dialog)
                    tbl.on("rowDblclick", _open_dialog)
