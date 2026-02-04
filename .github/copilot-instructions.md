# Copilot Instructions (Foundry Plan)

## Project Overview
Foundry Plan is a Windows-first production planning web app (NiceGUI + SQLite) with two distinct planning engines:
1. **Dispatcher**: Generates work queues for downstream lines (Mecanizado, Terminaciones, etc.) using heuristic sorting.
2. **Planner**: Simulates molding (Moldeo) schedule using greedy capacity-based algorithm.

## Architecture & Boundaries

### Entry Point & Bootstrap
- `run_app.py`: Configures Windows event loop policy (Selector) to suppress WinError 10054 on client disconnects.
- `src/foundryplan/app.py`: Main initialization—instantiates `Db`, creates `Repository`, registers pages via `register_pages(repo)`, serves static assets.
- Pages are functions that receive `repo: Repository` via closure; they build NiceGUI routes and containers.

### Data Layer (Single Source of Truth)
- **`src/foundryplan/data/repository.py`**: Repository facade exposes only `repo.data`, `repo.dispatcher`, `repo.planner`.
  - Each view instantiates its own implementation class from the respective module.
  - **CRITICAL**: Always access repo through module views (`repo.data.*`, `repo.dispatcher.*`, `repo.planner.*`).
- **Repository views**: `repo.data`, `repo.dispatcher`, `repo.planner` (see `src/foundryplan/data/repository_views.py`).
  - DataRepository → DataRepositoryImpl (in `src/foundryplan/data/data_repository.py`)
  - DispatcherRepository → DispatcherRepositoryImpl (in `src/foundryplan/dispatcher/dispatcher_repository.py`)
  - PlannerRepository → PlannerRepositoryImpl (in `src/foundryplan/planner/planner_repository.py`)
- **`src/foundryplan/data/db.py`**: Manages SQLite connection, WAL mode, schema migration.
  - *Constraint*: Do not call `Db.connect()` directly outside Repository implementations.
  - Schema split: `src/foundryplan/data/schema/` with `data_schema.py`, `dispatcher_schema.py`, `planner_schema.py`.

### Dispatcher (Downstream Scheduling)
- **`src/foundryplan/dispatcher/scheduler.py`**: Pure functional module.
  - Input: `lines: list[Line]`, `jobs: list[Job]`, `parts: list[Part]`, optional `pinned_program`.
  - Output: `(queues: dict[str, list[dict]], errors: list[dict])`.
  - Algorithm: `check_constraints()` validates line eligibility; `generate_dispatch_program()` sorts jobs by (test status, priority, due_date) and balances load.
  - *No DB access. No side effects. Stateless.*
- **`src/foundryplan/dispatcher/models.py`**: Data classes for scheduling (`Line`, `Job`, `Part`).

### Planner (Moldeo Optimization)
- **`src/foundryplan/planner/api.py`**: High-level interface.
  - `prepare_and_sync()`: Reads orders/parts/config from repo, calls solver.
  - `run_planner()`: Wraps solver and persists results.
- **`src/foundryplan/planner/solve.py`**: Solver logic.
  - `solve_planner_heuristic()`: Greedy day-by-day allocation respecting flask capacity, pouring limits, cooling times.
  - Input: `PlannerOrder`, `PlannerPart`, `PlannerResource`, workdays.
  - Output: Schedule dict (order_id → day_idx → mold qty).
- **`src/foundryplan/planner/model.py`**: Pure data classes for solver.
- **`src/foundryplan/planner/extract.py`**: Fetches data from repo, transforms to solver inputs.

### UI (NiceGUI)
- **`src/foundryplan/ui/pages.py`**: Page registration (routing). Key functions:
  - `register_pages(repo)`: Defines all routes and builds container structure.
  - `auto_generate_and_save()`: Triggers dispatcher after validation.
  - `refresh_from_sap_all()`: Rebuilds orders from MB52+Vision, regenerates programs.
- **`src/foundryplan/ui/widgets.py`**: Reusable components (tables, forms, etc.).
  - *Pattern*: Manual `.refresh()` on containers; avoid `@ui.refreshable` decorator (unreliable state).

## Data Flow

### 1. Ingest (Upload)
User uploads MB52 (stock snapshot) and Vision (orders) via page `/actualizar`:
- Excel columns normalized via `src/foundryplan/data/excel_io.py` (handles format coercion).
- SAP keys (Documento Comercial, Posición SD) normalized via `_normalize_sap_key()`.
- Tables stored in `sap_mb52_snapshot`, `sap_vision_snapshot`, `sap_demolding_snapshot`.
- **MB52**: No material prefix filtering (loads all materials).
- **Vision**: Filters by `sap_vision_material_prefixes` config (default: 401,402,403,404).
- **Desmoldeo**: No material prefix filtering (loads all materials). Auto-updates `material_master` and regenerates `planner_daily_resources`.

### 2. Reconciliation
`Repository.try_rebuild_orders_from_sap_for(process)`:
- Joins MB52 (stock) + Vision (dates/qty) on normalized SAP keys.
- Filters by process `almacen` config.
- Applies `_mb52_availability_predicate_sql(process)` (e.g., `libre_utilizacion=1`).
- Creates/updates `orders` table with reconciled data.
- Vision is source of truth for `fecha_de_pedido`; MB52 is source for qty available.

### 3. Dispatcher (Downstream Workflows)
`auto_generate_and_save(process)` in UI calls:
1. `repo.dispatcher.get_jobs_model(process)` → list of `Job` (orders with qty).
2. `repo.dispatcher.get_parts_model()` → master data (`Part`: family, vulcanizado_dias, mecanizado_dias, inspeccion_externa_dias, weights).
3. `repo.dispatcher.get_dispatch_lines_model(process)` → list of `Line` (work centers with constraints).
4. `scheduler.generate_dispatch_program(...)` → returns queues + errors.
5. `repo.dispatcher.save_last_program(process, program, errors)` → persists to DB.

Algorithm: Sort jobs by (is_test DESC, priority ASC, due_date ASC). Assign to eligible lines, balancing load.
`start_by` is calculated in real-time from `Part.vulcanizado_dias + Part.mecanizado_dias + Part.inspeccion_externa_dias`.

### 4. Planner (Moldeo)

**Daily Resources System:**
- Core table: `planner_daily_resources` - stores available capacity day-by-day
- Regenerated automatically when:
  - Saving Config > Planner
  - Importing Desmoldeo report
- Calculation:
  ```
  Horizon = min(planner_horizon_days, days_to_last_vision_order)
  molding_capacity = molding_per_shift × shifts_per_day
  same_mold_capacity = same_mold_per_shift × shifts_per_day
  pouring_capacity = pour_per_shift × shifts_per_day
  flask_available = total - occupied_from_demolding
  ```
- Flasks occupied from demolding: filtered by cancha, counted from today until demolding_date + 1

**Solver Workflow:**
`run_planner()` in UI calls:
1. `planner.api.prepare_and_sync(repo, asof_date, ...)` → extracts orders/parts/resources.
2. `solve_planner_heuristic(...)` → greedy day-by-day capacity allocation.
3. Persists schedule to `planner_schedule` table.

Solver prioritizes: overdue orders → currently loaded patterns → priority → due_date. Respects flask/pouring limits and cooling times.
Future: Will read constraints from `planner_daily_resources` table.

## Developer Workflow

### Running Locally
```powershell
# Windows (from workspace root)
.\.venv\Scripts\python.exe run_app.py --port 8080

# macOS/Linux
python run_app.py --port 8080
```
Then visit http://localhost:8080.

### Testing
```powershell
# Run all tests
python -m pytest

# After dispatcher changes (critical)
python -m pytest tests/test_scheduler_v2.py -v
```

### Database
- Schema: `src/foundryplan/data/db.py:Db.ensure_schema()`.
- Location: `db/foundryplan.db` (local SQLite).
- Mode: WAL (Write-Ahead Logging) for concurrency.
- Migrations: Idempotent SQL on startup; old columns dropped if needed.

## Key Patterns & Conventions

### Normalization
- **SAP Keys**: Use `repo._normalize_sap_key()` for all SAP document/position comparisons (handles numeric coercion).
- **Process Names**: Use `repo._normalize_process(process)` (lowercase, handles aliases).
- **Dates**: Always work with `date` objects; convert from ISO strings via `date.fromisoformat()`.

### Stock Filtering
- **`_mb52_availability_predicate_sql(process)`**: Generates SQL WHERE clause for process-specific stock filters.
  - Reads JSON config from `process.availability_predicate_json`.
  - Example: `{"libre_utilizacion": 1, "en_control_calidad": 0}` → only "free" stock.
  - Falls back to defaults if no config.

### Scheduling Dates
Computed in real-time from `Part` (material_master): `due_date - (vulcanizado_dias + mecanizado_dias + inspeccion_externa_dias)`.
- **Dispatcher**: Uses vulcanizado_dias, mecanizado_dias, inspeccion_externa_dias for lead time.
- **Planner**: Uses finish_days (converted to hours ×24), min_finish_days (converted to hours ×24), tiempo_enfriamiento_molde_dias (stored as hours) for capacity planning.
- **Field semantics**:
  - `finish_days`, `min_finish_days`: Stored as **days**, converted to hours (×24) by planner. Defaults: 15, 5.
  - `tiempo_enfriamiento_molde_dias`: Stored as **hours** (despite name), used directly by planner.
- **Legacy field**: `orders.tiempo_proceso_min` exists but is not used (always NULL).
- Always computed in `Job` when building dispatcher model.

### Purity & Separation
- **Dispatcher/Planner**: Pure functions only. No DB access. Receive data structures, return results.
- **UI**: Orchestrates repo calls, error handling, notification.
- **Repo**: All persistence, query logic, normalization.

### Lotes & Tests
- Alphanumeric lotes (e.g., "L123ABC") are marked `is_test=1`.
- Correlativo extracted from first digit prefix; used for display/filtering.

### Multi-Process Support
- App manages multiple workflows (terminaciones, mecanizado, mecanizado_externo, etc.).
- Each process has config: `almacen`, availability predicate, lines, family constraints.
- `Repository.processes` dict holds mapping: `process_id → {"almacen_key": ..., "label": ...}`.

### Error Handling & Notifications
```python
# UI Pattern
try:
    result = repo.some_operation()
except ValueError as e:
    ui.notify(f"Invalid input: {e}", color="negative")
except Exception as e:
    ui.notify(f"Unexpected error: {e}", color="negative")
    logger.exception("Detailed error")
```

### Audit Trail
- `repo.log_audit(category, message, details)` records all major operations (import, program gen, config change).
- Safely handles DB failures (doesn't crash app).

## Important File Paths
- **Entry**: `run_app.py`, `src/foundryplan/app.py`
- **Dispatcher**: `src/foundryplan/dispatcher/scheduler.py`, `models.py`
- **Planner**: `src/foundryplan/planner/solve.py`, `api.py`, `extract.py`, `model.py`
- **Data**: `src/foundryplan/data/repository.py`, `db.py`, `excel_io.py`
- **UI**: `src/foundryplan/ui/pages.py`, `widgets.py`
- **Tests**: `tests/test_scheduler_v2.py` (dispatcher), `tests/test_job_creation.py` (data layer)
