# Two-Layer Planning Architecture (Updated 2026-01-25)

Implementation note: the strategic solver runs on a **separate SQLite database** (`engine.db`) created/populated by the app to avoid table/schema collisions with the main app database.

## Vision
Transform FoundryPlanner from a **single-layer hourly dispatch system** → **dual-layer production planning platform**:
1. **High-Level Planning (Weekly, Foundry Planning Engine)**: Respects facility-wide capacity constraints, optimizes order assignments across molding lines, minimizes late deliveries
2. **Low-Level Dispatch (Hourly/On-demand, Existing FoundryPlanner)**: Uses MB52+Visión-derived orders and internal parts; today it runs independently of the weekly plan (only a future molding dispatcher will consume `plan_molding`).

---

## Current State Analysis

### FoundryPlanner Today (Dispatch Layer Only)
- **Input:** SAP MB52 + Visión (stock + order demand) via Excel uploads; internal GUI-managed part master
- **Process:** Heuristic (priority asc, then due_date − process_time) using MB52-derived orders; assigns to lowest-load eligible line
- **Output:** `programa` (hourly work queues per line)
- **Constraint Awareness:** Family-to-line affinity only; no facility-wide capacity model
- **Update Cadence:** On SAP upload and on config changes (best-effort regeneration)

- **Input:** Engine-owned input tables (orders, parts, capacities, flasks, weekly limits) populated by FoundryPlanner into `engine.db`
- **Process:** Mixed-Integer Program (MIP) solver (foundry_planner_engine)
- **Output:** Engine-owned result tables (e.g., `plan_molding`, `order_results`, plus derived tables)
- **Constraint Awareness:** Flask capacity, pouring delays, global melt deck tonnage, pattern limits, lead times
- **Update Cadence:** Weekly replanning (ideal)
- **Optimization:** Minimizes weighted lateness across plant

---

## Proposed Two-Layer Integration

### Layer 1: Strategic Weekly Planner (High-Level)
```
Input: SAP Demand + Facility Constraints
         ↓
    [foundry_planner_engine.solve()]
         ↓
Output: Weekly production plan per line (respects all plant constraints)
        - plan_molding[order_id, week_id, molds_planned]
        - order_results[order_id, start_week, delivery_week, is_late]
        - plan_pouring, plan_shakeout, plan_completion (derived)
```

**Responsibilities:**
- Answer: "How many molds of each order per week on each line to meet demand within capacity?"
- Constraints: Flask availability, global tonnage limits, lead times, line capacities, pattern wear
- Objective: Minimize weighted lateness (priority-based)

**Update Frequency:** Weekly (e.g., Monday morning)

### Layer 2: Tactical Hourly Dispatch (Low-Level)
```
Input (today): MB52 + Visión-derived orders + internal part master
    ↓
    [FoundryPlanner generate_program() - current heuristic]
    ↓
Output: Hourly/daily work queue per line
   - programa[pedido, line_id, corr_start, corr_end, priority_rank]

Future (molding dispatcher only): will consume plan_molding to sequence per pattern slot.
```

**Responsibilities (today):**
- Answer: "Which specific orders/SKUs should each line work on TODAY/THIS HOUR?"
- Constraints: Family affinity, current WIP; uses MB52-derived stock and priorities
- Objective: Smooth production, minimize changeovers, meet weekly targets

**Update Frequency:** Hourly or on-demand (when new orders/cancellations arrive)

---

## Data Architecture (Current Implementation)

FoundryPlanner uses two SQLite databases:

### 1) App DB (main)
Stores SAP imports, dispatcher configuration, and dispatcher outputs.

### 2) Engine DB (`engine.db`)
Created/populated by the planning layer right before solving.

Typical engine inputs (engine-owned names):
- `orders`, `parts`
- `molding_lines_config`
- `capacities_weekly`, `global_capacities_weekly`
- `flasks_inventory`, `initial_flask_usage`

Typical engine outputs:
- `plan_molding`
- `order_results`

### Dispatcher tables (app DB)

- `sap_mb52`, `sap_vision` (raw imports)
- `orders` and `parts` (used by dispatcher)
- `last_program` / `programa` (dispatch output)

### Data Flow Between Layers (today vs future molding dispatcher)

```
┌─────────────────────────────────────────────────────────────────┐
│                      SAP ERP System                             │
│  MB52 (stock) + Visión Planta (demand, dates)                  │
└────────┬────────────────────────────────────────────────────────┘
         │
         │ Repository.import_excel_bytes()
         ↓
┌─────────────────────────────────────────────────────────────────┐
│              FoundryPlanner (two SQLite DB files)                   │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ LAYER 1: Strategic (Weekly) Planning                        ││
│  │ ─────────────────────────────────────────────────────────── ││
│  │ [Input]  app DB: sap_mb52, sap_vision, parts, config        ││
│  │          ↓ (ETL)                                            ││
│  │ [Input]  engine.db: orders, parts, capacities, flasks...    ││
│  │          ↓ foundry_planner_engine.solve()                   ││
│  │ [Output] engine.db: plan_molding, order_results, ...        ││
│  └─────────────────────────────────────────────────────────────┘│
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ LAYER 2: Tactical (Hourly) Dispatch                          ││
│  │ ─────────────────────────────────────────────────────────── ││
│  │ [Input]  orders (demand from Visión+MB52), parts (internal master) ││
│  │          ↓ generate_program() heuristic (priority, due_date - process_time) ││
│  │ [Output] programa (hourly queues per line)                  ││
│  │          last_program (cached dispatch)                     ││
│  │          (Future) molding dispatcher consumes plan_molding  ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
         │
         ↓ UI Renders
┌──────────────────────────┐
│  NiceGUI Web Interface   │
│  • Dashboard (KPIs)      │
│  • Weekly Plan View      │
│  • Daily Dispatch View   │
│  • Config (lines/parts)  │
└──────────────────────────┘
```

---

## What Remains

- Strategic UI should render real outputs (instead of placeholder copy) in `/plano-semanal`.
- Decide persistence contract for engine outputs:
   - read directly from `engine.db`, or
   - copy outputs back into app DB tables for history/reporting.

See the up-to-date checklist: [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md)

Historical phased rollout notes were removed to avoid drift. Use the checklist above for current TODOs.

3. **Update Dispatch View** (`/programa`)
   - Add filter: show which orders are from current weekly plan vs ad-hoc
   - Highlight constraint violations (if actual ≠ planned)

4. **Create docs**
   - `docs/strategic_planning.md`: Weekly plan algorithm, inputs, outputs
   - `docs/integration_guide.md`: How to extend or swap engines
   - Update `.github/copilot-instructions.md` with two-layer architecture
   - Update README with new project name, feature list

### Phase 5: Testing & Validation (Weeks 5-6)
**Goal:** Ensure correctness of data flows & optimization

1. **Unit tests** (`tests/`)
   - `test_data_bridge.py`: SAP → foundry_planner tables
   - `test_result_reader.py`: foundry_planner outputs → UI models
   - `test_orchestrator.py`: solve workflow, error handling

2. **Integration tests**
   - End-to-end: Upload SAP → weekly solve → dispatch regenerate → compare with manual
   - Smoke tests: Large SAP files (500+ orders) don't timeout

3. **Validation**
   - Compare old heuristic (Layer 2 only) vs new (Layer 1 + 2) on historical data
   - Measure: lateness improvement, line utilization smoothness

### Phase 6: Deployment & Monitoring (Weeks 6-7)
**Goal:** Production-ready release

1. **Performance tuning**
   - Profile solver on real SAP data
   - Tune time_limit & MIP gap for acceptable solve times
   - Document SLA: "weekly solve completes in < 60s"

2. **Error handling & fallback**
   - If weekly solve fails (infeasible), fall back to Layer 2 only
   - Log all errors; trigger alerts
   - Graceful degradation (UI still functional)

3. **Documentation & training**
   - User guide: "How to interpret the weekly plan"
   - Admin guide: "How to adjust capacity model for next season"
   - Dev guide: "How to integrate your own optimization engine"

---

## Alternative Approaches Considered

### Option A: Monolithic Rewrite (❌ Not Recommended)
- Scrap all existing code, rewrite from scratch
- **Downside:** Loss of working dispatch, risk of regression, schedule slip
- **Upside:** Clean slate, no legacy debt

### Option B: Fork & Maintain Separate Services (❌ Not Recommended)
- FoundryPlanner (dispatch), FoundryPlannerService (weekly plan) as microservices
- **Downside:** Complex deployment, data sync overhead, ops burden
- **Upside:** Independent scaling, team separation

### Option C: Proposed — Layered Library Integration (✅ Recommended)
- Keep FoundryPlanner as single monolith web app
- Import foundry_planner_engine as library (not separate service)
- Orchestrate via background tasks
- **Upside:** Single deployment, shared database, simpler ops
- **Downside:** Tighter coupling (acceptable; both under your control)

---

## Database Migration Strategy

### Current Schema Version: v4
### Target Schema Version: v5

**Migration Script (pseudo-code):**
```python
# db.py:Db.ensure_schema() — add version check
def ensure_schema(self):
    version = self._get_schema_version()
    
    if version < 5:
        self._migrate_to_v5()
    
    # ... rest of schema creation

def _migrate_to_v5(self):
    with self.connect() as con:
        # Create 12 new tables (plan_molding, plan_pouring, etc.)
        con.executescript(SCHEMA_V5_ADDITIONS)
        
        # Create indexes for performance
        con.execute("CREATE INDEX idx_plan_molding_order ON plan_molding(order_id)")
        con.execute("CREATE INDEX idx_plan_molding_week ON plan_molding(week_id)")
        
        # Bump version
        con.execute("UPDATE app_config SET value='5' WHERE key='schema_version'")
```

---

## Configuration & Tuning (Current)

### Solver keys (stored in `app_config`)
Configurable in `/config/planificador`:

- `strategy_time_limit_seconds`
- `strategy_mip_gap`
- `strategy_planning_horizon_weeks`
- `strategy_solver_threads` (optional)
- `strategy_solver_msg`

### Scheduler keys (UTC, stored in `app_config`)

- `strategy_solve_day` (0=Lunes … 6=Domingo)
- `strategy_solve_hour` (0-23)

---

## References

- Checklist: [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md)
- Solver configuration: [docs/solver_configuration.md](docs/solver_configuration.md)
