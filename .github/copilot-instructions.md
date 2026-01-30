# Copilot instructions (Foundry Plan)

## What this repo is
- Foundry Plan is a Windows-first NiceGUI web app that generates per-line work queues ("programa") for multiple procesos (Terminaciones, Mecanizado, etc.) from SAP Excel uploads (MB52 + Visión Planta) + in-app master data (familias + tiempos), persisted in a local SQLite DB.

## How to run / test (Windows)
- Run from repo root: `.venv\Scripts\python.exe run_app.py --port 8080` (no install needed; `run_app.py` adds `src/` to `sys.path`).
- Tests: `.venv\Scripts\python.exe -m pytest`.

## Big picture architecture (boundaries to respect)
- App bootstrap: `src/foundryplan/app.py:main()` wires `Db` → `Repository` → `register_pages(repo)` and serves `/assets`.
- UI: `src/foundryplan/ui/pages.py` + shared layout/theme in `src/foundryplan/ui/widgets.py` (call `render_nav(repo=repo)` + `page_container()`).
- Persistence: UI should only touch storage through `src/foundryplan/data/repository.py:Repository`.
- DB schema/migrations: `src/foundryplan/data/db.py:Db.ensure_schema()` (WAL mode, best-effort migrations; don't break startup).
- Scheduling core: `src/foundryplan/core/scheduler.py:generate_program()` is pure over dataclasses in `src/foundryplan/core/models.py`.

## Data flow (SAP → orders → program)
- DB location is fixed: `db/foundryplan.db` (see `src/foundryplan/settings.py:default_db_path`).
- Upload MB52 + Visión in `/actualizar` → `Repository.import_excel_bytes(kind='mb52'|'vision')` populates `sap_mb52` / `sap_vision`.
- Rebuild derived `orders` per proceso: `Repository.try_rebuild_orders_from_sap_for(process=...)` joins MB52 (documento_comercial+posicion_sd+lote) with Visión (pedido+posicion) and groups lotes into correlativo ranges.
- Procesos are keyed in `Repository.processes` and use per-process warehouse config (e.g. `sap_almacen_mecanizado`, `sap_almacen_inspeccion_externa`).

## Project-specific rules & invariants
- MB52 material filtering is configurable via `sap_material_prefixes` (comma-separated; `*` means keep all), and SAP numeric keys are normalized via `Repository._normalize_sap_key`.
- “Usable” MB52 pieces are typically `libre_utilizacion=1` and `en_control_calidad=0`; special case: `process='toma_de_dureza'` intentionally uses NOT-available stock (`_mb52_availability_predicate_sql`).
- Alphanumeric lotes exist; correlativos are derived by extracting the first digit group (`Repository._lote_to_int`), and those rows can become “tests” (`Order.is_test`).
- Config mutations invalidate derived/cached data: `Repository.set_config(...)` clears `orders` + `last_program`; many master-data edits also clear `last_program`.

## Scheduling contract (keep tests in sync)
- Sort key in `generate_program`: tests first (`Order.is_test`), then manual priority (`orderpos_priority`), then `start_by = fecha_entrega - (vulcanizado_dias + mecanizado_dias + inspeccion_externa_dias)`.
- Assignment: choose among eligible lines (family allowed) the one with lowest current load.
- Output rows include: `_row_id`, `prio_kind`, `pedido`, `posicion`, `numero_parte`, `cantidad`, `corr_inicio`, `corr_fin`, `familia`, `fecha_entrega`, `start_by`.
- Program persistence merges “pinned/in-progress” rows from `program_in_progress_item` when saving/loading (`Repository.save_last_program` / `load_last_program`).
- If you change scheduling behavior, update `tests/test_scheduler.py`.
