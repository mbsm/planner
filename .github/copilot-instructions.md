# Copilot instructions (FoundryPlanner)

## What this repo is
- FoundryPlanner is a cross-platform production planning platform (Windows primary, macOS dev) with **two optimization layers**:
  1. **Strategic (Weekly):** Facility-wide MIP-based planning using [foundry_planner_engine](https://github.com/mbsm/foundry_planner_engine) — respects global constraints (flask capacity, melt deck tonnage, line hours), minimizes weighted lateness.
  2. **Tactical (Hourly/On-demand):** Dispatch queues per line, derived from weekly plan + SAP stock (MB52) + master data (families, post-process times).
- Persists to local SQLite; UI via NiceGUI.

## Key entrypoints / dev commands
- **Windows**: `.venv\Scripts\python.exe run_app.py --port 8080` | `.venv\Scripts\python.exe -m pytest`
- **macOS/Linux**: `.venv/bin/python run_app.py --port 8080` | `.venv/bin/python -m pytest`
- No install needed: `run_app.py` adds `src/` to `sys.path` and handles Windows async event loop policy.
- Bootstrap: `src/foundryplanner/app.py:main()` creates `Db` + `Repository`, registers pages via `src/foundryplanner/ui/pages.py:register_pages(repo)`, serves `/assets`.
- foundry_planner_engine is vendored as a git submodule in `external/foundry_planner_engine` (run `git submodule update --init --recursive` after clone).

## Workflow (mandatory)
- Before committing any code change, **run the linter** and fix all errors/warnings. Iterate until clean.
- Then **run the tests**; if they fail, fix while keeping the linter clean.
- After tests pass, make a **clear, descriptive commit** and push to the remote.

## Architecture boundaries (follow these)
- **Planning Layer (Strategic)** (`src/foundryplanner/planning/`): Orchestrates foundry_planner_engine; manages ETL (SAP → engine input tables), solve workflow, result persistence. Pure computation: no UI.
- **Dispatching Layer (Tactical)** (`src/foundryplanner/dispatching/scheduler.py`): Existing dispatch logic remains **independent of the weekly plan** (unconstrained heuristic). Future molding dispatcher is the only place that will consume `plan_molding`.
- **UI Layer** (`src/foundryplanner/ui/pages.py`): Renders both layers; new `/plano-semanal` route for strategic plan visualization.
- **Persistence API**: Both layers use `src/foundryplanner/data/repository.py:Repository` — single data access interface.
- **SQLite schema/migrations**: `src/foundryplanner/data/db.py:Db.ensure_schema()` (WAL mode, versioned). Schema v5+ includes 12 new tables for strategic planning.
- **Schedulers (planning + dispatch)**: Strategic `foundry_planner_engine.solve()` (MIP, pure). Tactical `generate_program()` (heuristic, MB52-driven, independent of plan); future molding dispatcher will use plan allocations.

## Data flow (how the app actually works)
- **Sources (SAP):** Only Visión + MB52 (no MB51). Orders are built once from these and shared between MIP and dispatcher. Parts/master remain the internal GUI-managed table shared by both layers.
- **Layer 1 (Strategic):** SAP uploads → ETL (Visión + MB52 + internal master) → `plan_orders_weekly`, `plan_capacities_weekly`, etc. → `foundry_planner_engine.solve()` → `plan_molding`, `order_results` (weekly MIP plan).
- **Layer 2 (Tactical):** Existing heuristic using MB52 data; sorts by priority asc, then `due_date - process_time`. It does **not** consume the weekly plan today. Only the **future molding dispatcher** will consume `plan_molding` to sequence per pattern slot. Dispatch refresh triggers: MB52 upload, dispatch parameter/config updates, or orders flagged as urgent (manual priority).
- **Data pathway:**
  1. Upload MB52 + Visión in UI (`/actualizar`) → `Repository.import_excel_bytes()` → `sap_mb52`, `sap_vision` tables.
   2. Triggering: `StrategyOrchestrator.solve_weekly_plan()` runs only via scheduled job or explicit manual call (no auto-run on SAP upload) → populate strategic input tables → `foundry_planner_engine.solve()` → persist plan tables.
  3. Tactical dispatch stays MB52-driven; weekly plan is only for the molding dispatcher (to be implemented).
  4. UI renders both layers: dashboard (KPIs), `/plano-semanal` (strategic view), `/programa` (tactical dispatch).
- **Multi-process support** (Layer 2 only): 7 processes (terminaciones, toma_de_dureza, mecanizado, etc.) each maintain separate dispatch queues per almacen/process.

## Project-specific conventions / business rules
- **Excel import**: MB52 keeps only materials starting with `"436"`; normalizes SAP numeric keys (e.g., 10.0/000010) via `Repository._normalize_sap_key`.
- **Column name normalization**: `excel_io.normalize_col_name()` handles SAP exports with accents, non-breaking spaces → ASCII snake_case.
- **Usable pieces (Terminaciones)**: match configured `sap_centro`/`sap_almacen_terminaciones`, `libre_utilizacion=1`, `en_control_calidad=0`, require `documento_comercial` + `posicion_sd` + `lote`.
- **Toma de dureza inversion**: uses opposite predicate (`libre_utilizacion=0 OR en_control_calidad=1`) to track unavailable stock.
- **Alphanumeric lotes**: correlativos extracted via `Repository._lote_to_int()` (finds first digit group, e.g., `"0030PD0674"` → `30`).

## Scheduling contract (keep tests in sync)
- **Layer 1 (Strategic/Weekly):** MIP solver minimizes weighted lateness. Respects plant-wide constraints: flask capacity per line, global melt deck tonnage, line working hours, pattern wear limits, pouring delays, post-process lead times.
  - Inputs: `plan_orders_weekly`, `plan_parts_routing`, `plan_capacities_weekly`, `plan_flasks_inventory`, etc.
  - Outputs: `plan_molding[order_id, week_id, molds_planned]`, `order_results[order_id, start_week, delivery_week, is_late, weeks_late]`.
- **Layer 2 (Tactical/Hourly):** Unconstrained heuristic scheduler (independent of weekly plan).
  - Priority sorting key: **tests first** (`is_test=True`), then **manual priority / urgent flag**, then **`start_by = fecha_entrega - post_process_days`**.
  - Assignment: choose among eligible lines (family allowed) the one with **lowest current load**.
  - Output rows include: `_row_id`, `prio_kind`, `pedido`, `posicion`, `numero_parte`, `cantidad`, `corr_inicio`, `corr_fin`, `familia`, `fecha_entrega`, `start_by`.
  - Refresh triggers: MB52 upload, dispatch parameter/config updates, orders flagged as urgent (manual priority updates).
- **If you change either scheduling behavior**, update `tests/test_scheduler.py` + strategic integration tests.

## NiceGUI UI conventions used here
- Pages render consistent layout via `render_nav(active=...)` + `page_container()`.
- Avoid raw HTML/sanitization workarounds; prefer NiceGUI components.
- Use `asyncio.to_thread()` for blocking DB/Excel ops in UI callbacks to keep UI responsive.
- Both strategic (`/plano-semanal`) and tactical (`/programa`) views accessible from same dashboard.

## Integration with foundry_planner_engine
- Engine is a **pure library** (no UI, no database logic).
- Called via `StrategyOrchestrator.solve_weekly_plan()` (orchestrator pattern).
- Inputs populated by `StrategyDataBridge` (ETL from SAP + config).
- Outputs read by `StrategyResultReader` (for UI + Layer 2 triggering).
- See [INTEGRATION_ARCHITECTURE.md](INTEGRATION_ARCHITECTURE.md) for detailed design and phased rollout.
