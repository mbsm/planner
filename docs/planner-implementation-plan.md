# Foundry Production Planner — Implementation Plan (Aligned to Spec)

This plan plugs the Planner module into the current Foundry Plan codebase using the
spec in `foundry_production_planner_spec.md`.

The Planner is a **weekly (Monday)**, order-level scheduler for **moldeo** that decides
how many molds per order to produce each working day.

## 1) Scope & goals
- Add **Plan** page to the UI menu (run + review plan).
- Add **Config > Planner** page for capacities, holidays, flask sizes, initial state.
- Map Planner inputs from existing SAP snapshots + master data.
- Persist Planner outputs in new tables (daily + weekly).

Non-goals for now:
- Authentication (Entra ID) - deferred.
- Multi-plant DB - still 1 DB per plant.

## 2) Integration approach
We will integrate the Planner as a module under `src/foundryplan/planner/` with a clear
input -> solve -> persist flow.

**New package**: `src/foundryplan/planner/`
- `model.py`: input DTOs (orders, parts, resources, calendar, initial state)
- `extract.py`: DB -> DTO mapping
- `solve.py`: OR-Tools CP-SAT model
- `persist.py`: write outputs
- `api.py`: facade used by UI

## 3) Database schema (planner tables)
All planner tables are **scoped by `scenario_id`** per the spec.

### 3.1 Scenario & inputs
Tables (from spec):
- `scenarios`
- `parts`
- `orders`
- `resources`
- `calendar_workdays`
- `initial_order_progress`
- `initial_patterns_loaded`
- `initial_flask_inuse`
- `initial_pour_load`

### 3.2 Outputs
- `plan_daily_order`
- `plan_weekly_order`
- `order_status`

## 4) Mapping from current system (SAP) to planner inputs

### 4.1 Orders
Planner order tuple: `(order_id, part_id, qty, due_date)`
- Source: `sap_vision_snapshot`
- `order_id` = `pedido + '/' + posicion` (stable key)
- `part_id` = `cod_material`
- `qty` = `solicitado`
- `due_date` = `fecha_de_pedido`
- `priority`:
  - `orderpos_priority` (manual) -> lower priority value
  - default: 100

### 4.2 Parts
Planner part tuple: `(part_id, flask_size, cool_hours, finish_hours, gross_weight_ton, alloy)`
- Source: `material_master` (columns added for planner)
  - `part_id` = `material`
  - `gross_weight_ton` = `peso_unitario_ton`
  - `alloy` = `aleacion`
  - `flask_size` = `flask_size`
  - `cool_hours` = `tiempo_enfriamiento_molde_dias * 24`
  - `finish_hours` = 0 (pending spec detail)

### 4.3 Resources
Planner resource tuple (spec): flasks + per-day capacities
- Source: new `resources` (planner) table
  - `flasks_S/M/L`
  - `molding_max_per_day`
  - `molding_max_same_part_per_day`
  - `pour_max_ton_per_day`

### 4.4 Calendar
Planner calendar is precomputed working days:
- Source: new `calendar_workdays`
  - Generated from config:
    - start date = selected Monday
    - skip weekends + holidays (config)

### 4.5 Initial state (replanning)
Inputs for Monday morning:
- `initial_order_progress`: **computed (blocking if missing data)**
  - Use `x_fundir` from `sap_vision_snapshot` (castings) and convert to molds:
    - `molds_remaining = ceil(x_fundir / piezas_por_molde)`
  - Subtract MB52 molds in **moldeo warehouse** (configurable):
    - `moldes_en_almacen_moldeo` = count of MB52 units in `app_config.sap_almacen_moldeo`
  - Final credit:
    - `molded_qty_credit = max(0, molds_remaining - moldes_en_almacen_moldeo)`
  - **Block planner** if `piezas_por_molde` is missing for any part.
- `initial_patterns_loaded`: manual input (UI)
- `initial_flask_inuse`: **derived from MB52 moldeo stock**
  - Query MB52 in `app_config.sap_almacen_moldeo`
  - Map each mold to `flask_size` via master data
  - Assume current molds release after **cooling days**:
    - `release_workday_index = cool_days[p]`
  - **Note:** to be accurate we need SAP info for when each mold was poured; otherwise we assume “poured today”.
- `initial_pour_load`: manual input (UI)

## 5) UI changes

### 5.1 Menu
Add **Plan** page entry.

### 5.2 Plan page
- Select scenario + asof_date (Monday)
- Run planner
- View latest `plan_daily_order` + `plan_weekly_order`
- Summary: total planned pieces / tons / late qty

### 5.3 Config > Planner
Inputs:
- holiday calendar (excluye días no hábiles)
- resources (flask counts, capacities)
- initial state inputs (progress, patterns loaded, flasks in use, pour load) — pendiente de UI

## 6) CP-SAT model (per spec)
We will implement **exactly** the variables/constraints in the spec:
- `x[o,d]` molds/day
- `y[o,d]` pattern active
- `start[o,d]`, `stop[o,d]`
- lateness slack by delivered-by-due
- flask occupancy and pour capacity with kg scaling

Objective:
`W_late >> W_switch >> W_wip` as specified.

## 7) Implementation steps (phased)

**Phase A — Schema + repository**
1) Add planner tables in `Db.ensure_schema()`.
2) Add repository methods to read/write planner config and initial state.

**Phase B — Extraction**
3) Build `planner/extract.py` to map SAP -> planner scenario inputs.
4) Implement calendar generator.

**Phase C — Solve + persist**
5) Implement CP-SAT solver in `planner/solve.py`.
6) Persist outputs to `plan_daily_order`, `plan_weekly_order`, `order_status`.

**Phase D — UI**
7) Add Plan page + Config > Planner page.
8) Add "Run plan" action + summary widgets.

**Phase E — Tests**
9) Unit tests for extraction + solver feasibility.
10) Smoke test for UI run.

## 8) Open questions
- Which source defines molded progress (`initial_order_progress`)?
- Should we auto-generate `calendar_workdays` for every run or store once per scenario?
- How to handle multiple scenarios (default single scenario vs snapshots)?

## 9) Dependencies
Add `ortools` to `requirements.txt`.
