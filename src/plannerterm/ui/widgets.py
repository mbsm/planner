from __future__ import annotations

from contextlib import contextmanager

from nicegui import ui


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
        .pt-container { max-width: 1200px; margin: 0 auto; padding: 16px; }
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
        ("dashboard", "Dashboard", "/"),
        ("programa", "Programa", "/programa"),
        ("actualizar", "Actualizar", "/actualizar"),
    ]
    config_active = active_key in {"config", "config_lineas", "config_familias", "config_materiales", "config_pedidos"}

    with ui.header().classes("pt-header bg-white text-slate-900"):
        with ui.row().classes("w-full items-center justify-between gap-4 px-4 py-2"):
            with ui.row().classes("items-center gap-3"):
                # Prefer ui.image for compatibility across NiceGUI versions.
                # Some versions don't support ui.html(..., sanitize=...).
                try:
                    ui.image("/assets/elecmetal.png").style("height: 32px; width: auto;")
                except Exception:
                    ui.html('<img src="/assets/elecmetal.png" style="height: 32px; width: auto;" alt="Logo"/>')
                ui.label("Programa de Terminaciones").classes("text-xl md:text-2xl font-semibold leading-none")
            with ui.row().classes("items-center gap-1"):
                for key, label, path in sections:
                    is_active = key == active_key
                    props = "dense no-caps" + (" unelevated" if is_active else " flat")
                    btn = ui.button(label, on_click=lambda p=path: ui.navigate.to(p)).props(props)
                    if is_active:
                        btn.props("color=primary")
                    else:
                        btn.props("color=primary")

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


def render_line_tables(program: dict[int, list[dict]], line_families: dict[int, list[str]] | None = None) -> None:
    # program: line_id -> list of items (dict)
    with ui.element("div").classes("w-full grid gap-4 grid-cols-1 lg:grid-cols-2 items-stretch"):
        for line_id, items in sorted(program.items(), key=lambda x: x[0]):
            with ui.card().classes("w-full h-full flex flex-col"):
                ui.label(f"Línea {line_id}").classes("text-xl font-semibold")
                families: list[str] = []
                if line_families and line_id in line_families:
                    families = [f for f in line_families[line_id] if f]
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
                tbl = ui.table(
                    columns=[
                        {"name": "prio_kind", "label": "", "field": "prio_kind"},
                        {"name": "pedido", "label": "Pedido", "field": "pedido"},
                        {"name": "posicion", "label": "Pos.", "field": "posicion"},
                        {"name": "numero_parte", "label": "Parte", "field": "numero_parte"},
                        {"name": "cantidad", "label": "Stock (MB52)", "field": "cantidad"},
                        {"name": "familia", "label": "Familia", "field": "familia"},
                        {"name": "fecha_entrega", "label": "Entrega", "field": "fecha_entrega"},
                    ],
                    rows=rows,
                    row_key="_row_id",
                ).classes("w-full pt-program-table").props("dense flat bordered separator=cell wrap-cells")

                tbl.add_slot(
                    "body-cell-prio_kind",
                    r"""
<q-td :props="props" style="width: 36px">
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
