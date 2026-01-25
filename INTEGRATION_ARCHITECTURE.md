# Two-Layer Planning Architecture

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
- **Update Cadence:** Manual (when files uploaded)

- **Input:** 7 input tables (orders, parts, capacities, flasks, weekly limits) sourced from Visión + MB52 Excel uploads + internal master (no MB51)
- **Process:** Mixed-Integer Program (MIP) solver
- **Output:** 5 result tables (`plan_molding`, `plan_pouring`, `plan_shakeout`, `plan_completion`, `order_results`)
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

## Data Architecture

### New Tables (Foundry Planning Engine)

#### Input Tables (populated from SAP + config)
```sql
-- Strategic capacity model
plan_orders_weekly         -- Derived from SAP (one row per order, with firm demand)
plan_molding_lines_config  -- Facility geometry (line hours, tooling)
plan_flasks_inventory      -- Physical flask assets per size/line
plan_capacities_weekly     -- Weekly line limits (with maintenance windows)
plan_global_capacities_weekly -- Melt deck tonnage limits by week
plan_initial_flask_usage   -- WIP state (which flasks are in-use at horizon start)
plan_parts_routing         -- Part specs (weight, cooling time, lead time, pattern limits)
```

#### Output Tables (populated by foundry_planner_engine.solve())
```sql
plan_molding               -- PRIMARY: Molds per order per week
plan_pouring               -- Derived: Pouring schedule (delayed from molding)
plan_shakeout              -- Derived: Flask release schedule
plan_completion            -- Derived: Finished goods ready date
order_results              -- KPIs: start_week, delivery_week, is_late, weeks_late
```

### Existing Tables (Enhanced for Dispatch Layer)

```sql
-- Already exists; will be extended
orders                     -- Built from Visión + MB52 (no MB51); shared by MIP and dispatcher
parts                      -- Internal GUI-managed master; shared by MIP and dispatcher
programa                   -- UNCHANGED: hourly dispatch still works (MB52-driven)
last_program               -- Cached dispatch
```

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
│              FoundryPlanner SQLite Database                         │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │ LAYER 1: Strategic (Weekly) Planning                        ││
│  │ ─────────────────────────────────────────────────────────── ││
│  │ [Input]  sap_mb52, sap_vision (SAP raw)                   ││
│  │          ↓ (ETL)                                            ││
│  │ [Input]  plan_orders_weekly, plan_parts_routing             ││
│  │          plan_molding_lines_config, plan_flasks_inventory   ││
│  │          ↓ foundry_planner_engine.solve()                   ││
│  │ [Output] plan_molding, plan_pouring, plan_shakeout          ││
│  │          plan_completion, order_results                     ││
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

## Implementation Path

### Phase 1: Foundation (Weeks 1-2)
**Goal:** Get foundry_planner_engine integrated as a library; rename project

1. **Rename project & update all docs**
   - `PlannerTerm` → `FoundryPlanner`
   - Update README, doc files, package names, git repo name
   - Update copilot instructions

2. **Set up foundry_planner_engine as dependency**
   - Add to `requirements.txt`: `foundry_planner_engine @ git+https://github.com/mbsm/foundry_planner_engine.git`
   - OR: Vendored submodule for easier development
   - Create `src/foundryplanner/planning/` folder (will house Layer 1 integration)

3. **Create schema migrations**
   - Add all 12 new tables to `db.py:Db.ensure_schema()`
   - Validate foreign keys, indexes, constraints
   - Add version bump (current schema version → v5)

### Phase 2: ETL & Data Adapter (Weeks 2-3)
**Goal:** Bridge SAP data → foundry_planner_engine input tables

1. **Create SAP↔Engine data mappers** (`src/foundryplanner/planning/data_bridge.py`)
   ```python
   class StrategyDataBridge:
       def populate_plan_orders_weekly(self, process: str) -> int
           # Join sap_mb52 + sap_vision → plan_orders_weekly
           # Filter by process/almacen, derive firma demand
       
       def populate_plan_routing(self, process: str) -> int
           # parts + sap_vision → plan_parts_routing
           # Extract weight, cooling time, lead time
       
       def populate_plan_capacities(self, week_range: tuple) -> int
           # app_config → plan_molding_lines_config, plan_capacities_weekly
           # Handle maintenance windows, seasonal limits
   ```

2. **Create result reader** (`src/foundryplanner/planning/result_reader.py`)
   ```python
   class StrategyResultReader:
       def get_weekly_plan_for_order(self, order_id: str) -> dict
           # Read plan_molding + order_results for display
       
       def get_line_allocation(self, line_id: int, week_id: int) -> list[dict]
           # Extract plan_molding rows for UI visualization
   ```

3. **Add UI page for weekly plan** (`src/foundryplanner/ui/pages.py`)
   - New route: `/plano-semanal` (strategic weekly view)
   - Show `order_results` KPIs, molding allocation per line/week
   - Manual override controls (if needed for replanning)

### Phase 3: Orchestration & Scheduling (Weeks 3-4)
**Goal:** Wire up weekly solve + dispatch regeneration; add background tasks

1. **Create strategy orchestrator** (`src/foundryplanner/planning/orchestrator.py`)
   ```python
   class StrategyOrchestrator:
       async def solve_weekly_plan(self, *, force=False) -> dict
           """Call foundry_planner_engine.solve(); handle errors."""
           # Validate input data completeness
           # Populate input tables
           # Call solve()
           # Parse results, compute KPIs
           # Trigger Layer 2 regeneration
       
       async def regenerate_dispatch_from_plan(self) -> dict
           """Call enhanced generate_program constrained by weekly plan."""
           # Read plan_molding allocations
           # Call generate_program(constraint_by_weekly=True)
           # Save resultado → programa
   ```

2. **Add background task runner** (via NiceGUI's startup hooks)
   ```python
   @app.on_startup
   async def scheduled_weekly_solve():
       # Schedule: every Monday 00:00 UTC
       # Call orchestrator.solve_weekly_plan()
       # Notify UI on completion
   ```

3. **Enhance dispatch layer** (`src/foundryplanner/dispatching/scheduler.py`)
   ```python
   def generate_program_constrained(
       lines: list[Line],
       orders: list[Order],
       parts: list[Part],
       weekly_plan: dict[str, int]  # NEW: {order_id: molds_this_week}
   ) -> tuple[dict, list]:
       """Enhanced scheduler that respects weekly plan allocations."""
       # Sort orders by (plan_priority, due_date, load)
       # Cap each order by weekly_plan[order_id]
       # Assign to lowest-load eligible line
   ```

### Phase 4: UI & Documentation (Weeks 4-5)
**Goal:** Users can see & manage both layers

1. **Update Dashboard** (`/`)
   - Add KPI widget: weekly plan health (% on-time, average lateness)
   - Link to detailed plan view
   - Alert if weekly solve is stale (> 7 days old)

2. **Add Strategic Plan View** (`/plano-semanal`)
   - Table: orders × weeks with molding allocation (`plan_molding`)
   - Heatmap: line utilization per week (% capacity)
   - Metrics: `order_results` (start week, completion week, lateness)
   - Download button: export plan to Excel for meetings

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

## Configuration & Tuning

### Solver Options (Configurable via UI or app_config)
```python
SOLVER_CONFIG = {
    "strategy_enabled": True,                    # Toggle Layer 1 on/off
    "planning_horizon_weeks": 40,                # Typical: 40 weeks
    "solve_time_limit_seconds": 300,             # Max optimization time
    "mip_gap_tolerance": 0.01,                   # 1% optimality gap
    "weekly_solve_day": "MONDAY",                # When to run
    "weekly_solve_hour": 0,                      # UTC hour
    "fallback_to_dispatch_only": True,           # If solve fails
}
```

### Facility Model (Configurable)
```python
# app_config table
"plan_melt_deck_tons_per_week": "150",          # Tonnage capacity
"plan_flask_cooling_time_hours": "4",           # Per-size defaults
"plan_molding_hours_per_week": "80",            # Per line per week
```

---

## Success Metrics

| Metric | Current | Target | Timeline |
|--------|---------|--------|----------|
| **On-time delivery %** | 75% | 88%+ | By month 3 |
| **Avg lateness (weeks)** | 2.1 | <1.0 | By month 3 |
| **Line utilization smoothness** | High variance | ±10% | By month 2 |
| **Weekly solve time** | N/A | <60s | By phase 3 |
| **Data freshness** | Manual | Automatic | By phase 3 |

---

## Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|-----------|
| Foundry solver is infeasible for some weeks | Medium | High | Add fallback to dispatch-only; tune capacity model |
| SAP data quality issues | High | Medium | Add data validation & diagnostic UI |
| Weekly solve runs too long | Medium | High | Tune solver time_limit & MIP gap; use commercial solver |
| User confusion (two plans?) | High | Medium | Clear UI labeling; training docs; reconciliation view |
| Performance regression in dispatch | Low | Medium | Regression tests; profile before/after |

---

## Next Steps

1. **Confirm project name** with stakeholders (FoundryPlanner? FoundryPlanner?)
2. **Review this architecture** — any changes needed?
3. **Begin Phase 1** — rename + schema setup
4. **Set weekly sync** — review progress, blockers
