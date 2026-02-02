# Copilot Instructions (Foundry Plan)

## Project Overview
Foundry Plan is a Windows-first production planning web app (NiceGUI + SQLite) that generates work queues for factory lines. It ingests SAP reports (Excel) to visualize stock and simulate production schedules ("programas").

## Architecture & Boundaries
- **Entry Point**: `src/foundryplan/app.py` bootstraps the app, dependency injection, and routing.
- **Persistence Layer**: `src/foundryplan/data/repository.py` is the **only** permitted access point to the DB. It handles all reads/writes.
    - *Pattern*: UI widgets/pages must receive a `Repository` instance and use it; never query `Db` directly.
- **Business Logic**: `src/foundryplan/dispatcher/scheduler.py` is a **pure functional** module.
    - Input: `list[Line]`, `list[Job]`, `list[Part]`.
    - Output: Scheduled queues.
    - *Rule*: Logic here must be stateless. No DB access inside scheduler functions.
- **Data Models**: `src/foundryplan/dispatcher/models.py`. 
    - `Job`: Replaces deprecated `Order`. Represents a schedule-able unit.
    - `Line`: Represents a production line with constraints.
    - `Part`: Represents static metadata about a material (family, process times).

## Data Flow (SAP → Scheduler)
1. **Ingest**: User uploads MB52 (stock) and Vision (orders) Excel files.
    - `Repository.import_excel_bytes` parses specific columns via `src/foundryplan/data/excel_io.py`.
    - Data persists in raw tables: `sap_mb52_snapshot`, `sap_vision`.
2. **Reconciliation**: `Repository` joins tables to build actionable items.
    - Key Join: MB52 (`documento_comercial`, `posicion_sd`, `lote`) ↔ Vision (`pedido`, `posicion`).
    - *Invariant*: Vision is the source of truth for dates (`fecha_de_pedido`).
    - *Invariant*: Stock availability filters: `libre_utilizacion=1` AND `en_control_calidad=0` (configurable per process).
3. **Scheduling**: `scheduler.generate_dispatch_program` creates the plan.
    - Constraints: `check_constraints` matches `Part` attributes against `Line` config (e.g., `family_id`, `mec_perf_inclinada`).
    - Sort Order: 
        1. Tests (`Job.is_test`) 
        2. Priority (`Job.priority` ASC)
        3. Due Date (`Job.start_by`) based on `fecha_de_pedido` - process times.

## Developer Workflow
- **Run (Dev)**: `\.venv\Scripts\python.exe run_app.py --port 8080`
- **Tests**: `\.venv\Scripts\python.exe -m pytest`
    - *Critical*: Run `pytest tests/test_scheduler_v2.py` after any logic change.
    - `conftest.py` handles DB fixture setup.
- **DB Migrations**: Schema defined in `src/foundryplan/data/db.py:Db.ensure_schema()`.
    - Uses strict WAL mode. Schema updates are best-effort idempotent SQL commands on startup.

## Specialized Context
- **Lotes**: Alphanumeric lotes are treated as **Tests**. The numeric sequence (correlativo) is extracted from the first digit group.
- **Config**: "Familias" and "Tiempos" are user-managed master data stored in SQLite. If missing for a part, it cannot be scheduled.
- **UI Architecture**: `src/foundryplan/ui/` uses NiceGUI.
    - `pages.py`: Routing and page composition.
    - `widgets.py`: Reusable UI components.
    - *Pattern*: Use `ui.refreshable` for dynamic content that updates on state changes.

## Important File Paths
- Logic: `src/foundryplan/dispatcher/scheduler.py`
- Models: `src/foundryplan/dispatcher/models.py`
- Data Access: `src/foundryplan/data/repository.py`
- DB Schema: `src/foundryplan/data/db.py`
- Settings/Constants: `src/foundryplan/settings.py`
