# Copilot Instructions (Foundry Plan)

## Project Overview
Foundry Plan is a Windows-first production planning web app (NiceGUI + SQLite) that generates work queues for factory lines. It ingests SAP reports (Excel) to visualize stock and simulate production schedules ("programas").

## Architecture & Boundaries
- **Entry Point**: `src/foundryplan/app.py` bootstraps the app, dependency injection, and routing.
    - Registers pages via `register_pages(repo)` and serves static assets from `/assets`.
    - Windows-specific: Suppresses ConnectionResetError (WinError 10054) via custom event loop policy in `run_app.py`.
- **Persistence Layer**: `src/foundryplan/data/repository.py` is the **only** permitted access point to the DB. It handles all reads/writes.
    - *Pattern*: UI widgets/pages must receive a `Repository` instance and use it; never query `Db` directly.
    - *Invariant*: All writes must go through `Repository` methods. No raw SQL in UI code.
- **Business Logic**: `src/foundryplan/dispatcher/scheduler.py` is a **pure functional** module.
    - Input: `list[Line]`, `list[Job]`, `list[Part]`.
    - Output: Scheduled queues (dict) + errors (list).
    - *Rule*: Logic here must be stateless. No DB access inside scheduler functions.
    - Scheduling algorithm: sort by test status → priority → start_by date, then balance across eligible lines.
- **Data Models**: `src/foundryplan/dispatcher/models.py`. 
    - `Job`: Replaces deprecated `Order`. Represents a schedule-able unit with priority and dates.
    - `Line`: Represents a production line with constraints dict (e.g., `{"family_id": {"A", "B"}}`).
    - `Part`: Represents static metadata about a material (family, process times in days).
    - `AuditEntry`: Tracks business events in `audit_log` table.

## Data Flow (SAP → Scheduler)
1. **Ingest**: User uploads MB52 (stock) and Vision (orders) Excel files via `/actualizar` page.
    - `Repository.import_excel_bytes` parses specific columns via `src/foundryplan/data/excel_io.py`.
    - Column names are normalized (lowercase, underscores) using `normalize_columns()`.
    - Data persists in raw tables: `sap_mb52_snapshot`, `sap_vision_snapshot`.
    - SAP keys are normalized via `_normalize_sap_key()` to handle Excel's numeric coercion (e.g., "000010" → "10").
2. **Reconciliation**: `Repository.try_rebuild_orders_from_sap_for(process)` joins tables to build actionable items.
    - Key Join: MB52 (`documento_comercial`, `posicion_sd`, `lote`) ↔ Vision (`pedido`, `posicion`).
    - *Invariant*: Vision is the source of truth for dates (`fecha_de_pedido`).
    - *Invariant*: Stock availability filters vary by process. Default: `libre_utilizacion=1` AND `en_control_calidad=0`.
        - Exception: "toma_de_dureza" uses inverse filter (not available stock).
        - See `Repository._mb52_availability_predicate_sql(process)`.
    - Lote classification: Alphanumeric lotes → `is_test=1`, numeric correlativo extracted from first digit group.
3. **Scheduling**: `scheduler.generate_dispatch_program` creates the plan.
    - Constraints: `check_constraints(line, part)` matches `Part` attributes against `Line.constraints`.
        - Examples: `family_id` (set membership), `mec_perf_inclinada` (boolean), weight range (min/max dict).
    - Sort Order (defined in scheduler):
        1. Tests (`Job.is_test`) — always first
        2. Priority (`Job.priority` ASC) — 1=high, 3=normal
        3. Due Date (`Job.start_by` ASC) — computed as `fecha_de_pedido - (vulcanizado_dias + mecanizado_dias + inspeccion_externa_dias)`
    - Load balancing: Assign to line with lowest current load (by quantity).
    - Pinned jobs: Pre-assigned items ("en proceso") are fixed at queue start and count toward initial load.

## Developer Workflow
- **Run (Dev)**: 
    - Windows: `\.venv\Scripts\python.exe run_app.py --port 8080`
    - macOS/Linux: `python run_app.py --port 8080` (uses workspace `.venv`)
    - Optional args: `--host`, `--log-level` (DEBUG/INFO/WARNING/ERROR)
- **Tests**: 
    - Windows: `\.venv\Scripts\python.exe -m pytest`
    - macOS/Linux: `python -m pytest`
    - *Critical*: Run `pytest tests/test_scheduler_v2.py` after any scheduler logic change.
    - `conftest.py` sets up `sys.path` to find `src/foundryplan` without installing package.
- **DB Migrations**: Schema defined in `src/foundryplan/data/db.py:Db.ensure_schema()`.
    - Uses strict WAL mode (`PRAGMA journal_mode=WAL`) and foreign keys enabled.
    - Schema updates are best-effort idempotent SQL commands on startup.
    - Pre-migration checks: e.g., drops old `job_unit` table if missing `job_unit_id` PK column.
    - No formal migration framework — schema evolves via careful DDL in `ensure_schema()`.
- **Project Structure**:
    - `run_app.py`: Entry point, sets up event loop policy and sys.path.
    - `src/foundryplan/`: Main package (importable after sys.path fix).
    - `db/foundryplan.db`: SQLite database (auto-created, repo-local).
    - `assets/`: Static files (e.g., `elecmetal.png` logo).
    - `docs/`: User and developer manuals, data model documentation.

## Specialized Context
- **Lotes**: Alphanumeric lotes are treated as **Tests** (`is_test=1`). The numeric sequence (correlativo) is extracted from the first digit group.
    - Example: "ABC123" → correlativo=123, is_test=1. "456" → correlativo=456, is_test=0.
- **Config**: "Familias" and "Tiempos" are user-managed master data stored in SQLite (`material_master`, `family_catalog`).
    - If missing for a part, it cannot be scheduled (program will show errors).
    - Managed via `/config/*` pages (Config dropdown in nav).
- **Multi-Process Support**: The app supports multiple processes (almacenes):
    - terminaciones, mecanizado, mecanizado_externo, inspeccion_externa, por_vulcanizar, en_vulcanizado, toma_de_dureza.
    - Each process has its own almacen (warehouse) configured in `app_config` table.
    - Process keys are normalized via `Repository._normalize_process()` and must match `self.processes` dict.
- **UI Architecture**: `src/foundryplan/ui/` uses NiceGUI (Python reactive web framework).
    - `pages.py`: Routing and page composition. Each page function decorated with `@ui.page(path)`.
    - `widgets.py`: Reusable UI components (`page_container`, `render_nav`, `render_line_tables`).
    - Theme: Applied once via `ensure_theme()` using custom CSS classes (e.g., `.pt-container`, `.pt-kpi`).
    - *Pattern*: Dynamic content uses callbacks + manual `.refresh()` on containers, not `@ui.refreshable` decorator (not used in this codebase).
    - Auto-refresh on data changes: `auto_generate_and_save()` + `ui.notify()` pattern.
- **Audit Log**: All business events (imports, program generation, config changes) are recorded via `Repository.log_audit(category, message, details)`.
    - Never throws — failures print to stderr to avoid disrupting app flow.

## Important File Paths
- Entry: `run_app.py`, `src/foundryplan/app.py`
- Logic: `src/foundryplan/dispatcher/scheduler.py`
- Models: `src/foundryplan/dispatcher/models.py`
- Data Access: `src/foundryplan/data/repository.py` (4100 lines — all DB interactions)
- DB Schema: `src/foundryplan/data/db.py:Db.ensure_schema()` (1145 lines)
- Excel Parsing: `src/foundryplan/data/excel_io.py`
- UI: `src/foundryplan/ui/pages.py` (2576 lines), `src/foundryplan/ui/widgets.py` (636 lines)
- Settings/Constants: `src/foundryplan/settings.py`
- Tests: `tests/test_scheduler_v2.py` (critical), `conftest.py` (sys.path setup)
- Docs: `docs/modelo-datos.md` (data model reference), `docs/manual-usuario.md`, `docs/manual-desarrollo.md`

## Key Patterns to Follow
- **SAP Key Normalization**: Always use `Repository._normalize_sap_key()` when comparing pedido, posicion, documento_comercial (handles Excel's numeric coercion).
- **Process Filtering**: Use `Repository._mb52_availability_predicate_sql(process=process)` for stock availability — never hardcode `libre_utilizacion` checks.
- **Date Arithmetic**: Process times (`vulcanizado_dias`, etc.) are in days. Compute `start_by = fecha_de_pedido - timedelta(days=sum_of_times)`.
- **Scheduler Purity**: Never add DB queries to `scheduler.py`. If you need more data, fetch it in `Repository` and pass to scheduler as arguments.
- **UI Error Handling**: Use `try/except` + `ui.notify(message, color="negative")` for user-facing errors. Don't crash the page.
