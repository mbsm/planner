# Copilot instructions (PlannerTerm)

## What this repo is
- PlannerTerm is a Windows-first NiceGUI web app that generates per-line work queues (“programa”) from SAP Excel uploads (MB52 + Visión Planta) + in-app master data (familias + tiempos), persisted in a local SQLite DB.

## Key entrypoints / dev commands (Windows)
- Run from repo root: `.venv\Scripts\python.exe run_app.py --port 8080` (no install needed; `run_app.py` adds `src/` to `sys.path`).
- Tests: `.venv\Scripts\python.exe -m pytest`.
- Bootstrap: `src/plannerterm/app.py:main()` creates `Db` + `Repository`, registers pages via `src/plannerterm/ui/pages.py:register_pages(repo)`, serves `/assets`.

## Architecture boundaries (follow these)
- UI: `src/plannerterm/ui/pages.py` + shared widgets/theme in `src/plannerterm/ui/widgets.py`.
- Persistence API: UI should only touch storage through `src/plannerterm/data/repository.py:Repository`.
- SQLite schema/migrations: `src/plannerterm/data/db.py:Db.ensure_schema()` (WAL mode, best-effort migrations).
- Scheduling core: `src/plannerterm/core/scheduler.py:generate_program()` is pure over dataclasses in `src/plannerterm/core/models.py`.

## Data flow (how the app actually works)
- Upload MB52 + Visión in the UI (`/actualizar`) → `Repository.import_excel_bytes(kind='mb52'|'vision')` loads into tables `sap_mb52` / `sap_vision`.
- Rebuild orders from SAP: `Repository.try_rebuild_orders_from_sap()` → `orders` table is derived by joining MB52 (pedido/posición + lote) with Visión (fecha_de_pedido), counting usable pieces.
- Master data: `parts` holds `numero_parte -> familia` plus optional `*_dias` post-process lead times; `families` is a catalog.
- Scheduling: UI calls `generate_program(...)` and persists output in `last_program` (cached JSON).

## Project-specific conventions / business rules
- MB52 import keeps only materials starting with `"436"` and normalizes SAP numeric keys (e.g., 10.0/000010) via `Repository._normalize_sap_key`.
- “Usable” pieces for rebuild: match configured `sap_centro`/`sap_almacen_terminaciones`, `libre_utilizacion=1`, `en_control_calidad=0`, and require `documento_comercial` + `posicion_sd` + `lote`.
- Lotes can be alphanumeric; correlativos are derived by extracting digits (`Repository._lote_to_int`).

## Scheduling contract (keep tests in sync)
- Priority sorting key (see `generate_program`): tests first, then manual priority (from `orderpos_priority`), then by `start_by = fecha_entrega - (vulcanizado + mecanizado + inspeccion_externa)`.
- Assignment: choose among eligible lines (family allowed) the one with lowest current load.
- Output rows include: `_row_id`, `prio_kind`, `pedido`, `posicion`, `numero_parte`, `cantidad`, `corr_inicio`, `corr_fin`, `familia`, `fecha_entrega`, `start_by`.
- If you change scheduling behavior, update `tests/test_scheduler.py`.

## NiceGUI UI conventions used here
- Pages render consistent layout via `render_nav(active=...)` + `page_container()`.
- Avoid raw HTML/sanitization workarounds; prefer NiceGUI components.
