# Copilot instructions (Foundry Plan)

## What this repo is
- Windows-first NiceGUI web app that builds per-line work queues (“programa”) for multiple procesos from SAP Excel uploads (MB52 + Visión Planta) plus in-app master data (familias + tiempos), persisted in SQLite.

## Run / test (Windows)
- Install deps: `.venv\Scripts\python.exe -m pip install -r requirements.txt`.
- Run: `.venv\Scripts\python.exe run_app.py --port 8080` (adds `src/` to `sys.path`).
- Tests: `.venv\Scripts\python.exe -m pytest`.

## Big-picture architecture (respect boundaries)
- Bootstrap: `src/foundryplan/app.py:main()` wires `Db` → `Repository` → `register_pages(repo)` and serves `/assets`.
- UI: `src/foundryplan/ui/pages.py` + shared layout in `src/foundryplan/ui/widgets.py` (call `render_nav(repo=repo)` + `page_container()`).
- Persistence: UI should only touch storage through `src/foundryplan/data/repository.py:Repository`.
- DB schema/migrations: `src/foundryplan/data/db.py:Db.ensure_schema()` (WAL mode, best‑effort migrations; don’t break startup).
- Scheduling core: `src/foundryplan/core/scheduler.py:generate_program()` is pure over dataclasses in `src/foundryplan/core/models.py`.

## Data flow (SAP → orders → program)
- DB path is fixed: `db/foundryplan.db` (see `src/foundryplan/settings.py:default_db_path`).
- Upload MB52 + Visión in `/actualizar` → `Repository.import_excel_bytes(kind='mb52'|'vision')` populates `sap_mb52` / `sap_vision`.
- Rebuild derived `orders` per proceso: `Repository.try_rebuild_orders_from_sap_for(process=...)` joins MB52 (documento_comercial+posicion_sd+lote) with Visión (pedido+posicion) and groups lotes into correlativo ranges.
- Procesos are keyed in `Repository.processes` and use per-process warehouse config (e.g., `sap_almacen_mecanizado`, `sap_almacen_inspeccion_externa`).

## Project-specific invariants / conventions
- **Dates**: SAP `fecha_entrega` is invalid; always use `fecha_de_pedido` as the source of truth for order dates/deadlines.
- MB52 filtering is configurable via `sap_material_prefixes` (comma-separated; `*` means keep all); numeric keys normalized via `Repository._normalize_sap_key`.
- “Usable” MB52 pieces: `libre_utilizacion=1` and `en_control_calidad=0`; special case `process='toma_de_dureza'` uses NOT‑available stock (`_mb52_availability_predicate_sql`).
- Alphanumeric lotes: correlativos derive from the first digit group (`Repository._lote_to_int`); these rows can become tests (`Order.is_test`).
- Config mutations invalidate derived data: `Repository.set_config(...)` clears `orders` + `last_program`; many master-data edits also clear `last_program`.

## Scheduling contract (keep tests in sync)
- Sort key in `generate_program`: tests first (`Order.is_test`), then manual priority (`orderpos_priority`), then `start_by = fecha_entrega - (vulcanizado_dias + mecanizado_dias + inspeccion_externa_dias)`.
- Assignment: among eligible lines (family allowed), choose the one with lowest current load.
- Output rows include: `_row_id`, `prio_kind`, `pedido`, `posicion`, `numero_parte`, `cantidad`, `corr_inicio`, `corr_fin`, `familia`, `fecha_entrega`, `start_by`.
- Program persistence merges “pinned/in-progress” rows from `program_in_progress_item` when saving/loading (`Repository.save_last_program` / `load_last_program`).
- If you change scheduling behavior, update tests in `tests/test_scheduler.py`.
