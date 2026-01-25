# Quick Reference: Two-Layer Planning System

## Project Renamed: PlannerTerm → FoundryPlanner

**Old scope:** Dispatch for Terminaciones (single process)  
**New scope:** Full plant production planning (7+ processes, 2 layers)

---

## The Two Layers

### Layer 1: Strategic Planning (Weekly)
**What:** High-level production plan respecting plant capacity  
**When:** Every Monday 00:00 UTC (configurable)  
**How:** `foundry_planner_engine` (MIP solver, minimizes weighted lateness)  
**Where:** `src/foundryplanner/planning/` (NEW)  
**Input spine:** shared `orders` + `parts` tables (built once from SAP MB52 + Visión, plus internal part master)  
**Output:** `plan_molding` (weekly molds per order per line)  

**Constraints:**
- Flask availability (reusable, limited inventory)
- Melt deck tonnage (global cap per week)
- Line working hours (per line per week)
- Pattern wear limits (max molds per part per week)
- Pouring delays (molding week → pouring week offset)
- Post-process lead times (heat treat, grind, blast, QA)

### Layer 2: Tactical Dispatch (Hourly/On-demand)
**What:** Daily work queue per line (which order to run next?)  
**When:** On file upload OR hourly (configurable)  
**How (today):** Heuristic: sort by priority asc, then (due date − process time); uses the shared `orders` (MB52+Visión) and `parts` master tables. Does **not** consume weekly plan today.  
**Future:** Only a future **molding dispatcher** will consume `plan_molding`; current dispatchers stay independent of planner outputs.  
**Where:** `src/foundryplanner/dispatching/scheduler.py` (existing heuristic)  
**Output:** `programa` (hourly dispatch per line)  

**Constraints (today):**
- Family-to-line affinity (which parts can run on which lines)
- Current WIP state (what's already on each line)

---

## Key Files

| File | Purpose | Status |
|------|---------|--------|
| [PROPOSAL.md](PROPOSAL.md) | Executive summary + next steps | ✅ Created |
| [INTEGRATION_ARCHITECTURE.md](INTEGRATION_ARCHITECTURE.md) | 300-line detailed design | ✅ Created |
| [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md) | 6 phases, 400 tasks | ✅ Created |
| [README.md](README.md) | Updated project description | ✅ Updated |
| [.github/copilot-instructions.md](.github/copilot-instructions.md) | AI agent guide | ✅ Updated |
| Source code | (Phase 1-6 per checklist) | 🔄 Ready to start |

---

## Data Flow (Bird's Eye)

```
SAP ERP
├─ MB52 (stock by part/lote)
└─ Visión Planta (orders + dates)
       ↓
    [Upload UI]
       ↓
┌─ sap_mb52 ──────────┐
└─ sap_vision ────────┤
                      ↓ ETL (StrategyDataBridge)
┌─ plan_orders_weekly ──────────────────┐
├─ plan_parts_routing                   ├─ Layer 1: Strategic
├─ plan_molding_lines_config            ├─ (Weekly solve)
├─ plan_flasks_inventory                │
└─ plan_capacities_weekly ──────────────┤
       ↓ foundry_planner_engine.solve()  │
┌─ plan_molding ───────────────────────┬┘
├─ plan_pouring
├─ order_results
└─ plan_shakeout ──────────┐
                           ├─ Layer 2: Tactical
                           ├─ (Enhanced scheduler)
   orders + parts (shared) ─┤
       ↓ generate_program_constrained()
       └─ programa ───────────────────────→ UI: /programa
                                           UI: /plano-semanal
```

---

## Schema Changes

### New (12 Tables in v5)

**Inputs (populated by ETL):**
- `plan_orders_weekly` — Active customer demand
- `plan_parts_routing` — Part specs (weight, cooling time, lead time)
- `plan_molding_lines_config` — Line capacity (hours/week)
- `plan_flasks_inventory` — Physical flask assets
- `plan_capacities_weekly` — Weekly line limits
- `plan_global_capacities_weekly` — Melt deck tonnage cap
- `plan_initial_flask_usage` — WIP state (flasks in-use)

**Outputs (populated by solver):**
- `plan_molding` — Weekly production schedule
- `plan_pouring` — Pouring schedule (delayed)
- `plan_shakeout` — Flask release schedule
- `plan_completion` — Finished goods ready date
- `order_results` — KPIs per order (start_week, delivery_week, is_late, weeks_late)

### Modified (Existing Tables)

- `orders` — shared by both layers; built once from MB52 + Visión; ADD weekly_target_molds, weekly_cumulative_plan columns
- `parts` (internal master) — shared by both layers; managed in-app
- `programs` — UNCHANGED (still works as before)

---

## Class Hierarchy (Phase 1-3)

```
StrategyOrchestrator (NEW)
├─ solve_weekly_plan()
│  ├─ StrategyDataBridge.populate_all()
│  ├─ foundry_planner_engine.solve()
│  └─ StrategyResultReader.get_order_results()
└─ regenerate_dispatch_from_plan()
   └─ generate_program_constrained()  [ENHANCED]

Repository (EXISTING)
├─ get_strategic_data_bridge() → StrategyDataBridge
├─ get_strategic_result_reader() → StrategyResultReader
└─ [rest of existing methods]
```

---

## UI Routes

**New:**
- `GET /plano-semanal` — Strategic weekly plan view (order KPIs, line utilization heatmap)

**Updated:**
- `GET /` — Dashboard (add strategic plan widget, lateness trend)
- `GET /programa` — Dispatch (add filter: plan vs ad-hoc, constraint indicator)

**Existing (unchanged):**
- `GET /actualizar` — SAP upload (now triggers automatic solve)
- `GET /avance-produccion` — Production progress

---

## Configuration (app_config Table)

**New configs (Phase 1):**
```sql
strategy_enabled                       -- True/False (toggle Layer 1)
strategy_solve_day                     -- "MONDAY"
strategy_solve_hour                    -- 0 (UTC)
planning_horizon_weeks                 -- 40
solver_time_limit_seconds              -- 300
solver_mip_gap_tolerance               -- 0.01 (1%)
plan_melt_deck_tons_per_week           -- "150"
plan_molding_hours_per_week            -- "80" per line
```

---

## Testing Strategy

**Unit tests (Phase 5):**
- `test_data_bridge.py` — ETL correctness
- `test_result_reader.py` — Result parsing
- `test_orchestrator.py` — Workflow, error handling
- `test_scheduler_constrained.py` — Dispatch constraints

**Integration tests (Phase 5):**
- End-to-end: Upload → solve → dispatch
- Stress: 500+ orders, 40 weeks
- Regression: Compare vs old heuristic

**Validation (Phase 5):**
- Lateness improvement: ≥ 15%
- On-time %: ≥ 85%
- Line utilization smoothness: ± 10% week-to-week
- Solve time: < 60 seconds

---

## Success Metrics (Month 2)

| Metric | Target |
|--------|--------|
| On-time delivery % | 85%+ |
| Average lateness (weeks) | < 1.0 |
| Line utilization smoothness (std dev) | ± 10% |
| Weekly solve time (95th percentile) | < 60s |
| User satisfaction (new plan view) | 4/5 stars |

---

## Risk: What Could Go Wrong?

| Risk | Mitigation |
|------|-----------|
| Solver is infeasible | Diagnostic UI, fallback to Layer 2 only |
| Solver times out | Configure time_limit, use commercial solver |
| SAP data quality issues | Data validation, diagnostic report |
| User confusion (2 layers) | Clear UI labeling, training docs |
| Performance regression | Regression tests, profile before/after |

---

## Quick Start (After Phase 1)

```bash
# Pull engine submodule (once per clone)
git submodule update --init --recursive

# Setup
.venv/bin/python -m pip install -r requirements.txt

# Run app
.venv/bin/python run_app.py --port 8080

# Run tests
.venv/bin/python -m pytest

# Manual solve trigger (for testing)
curl -X POST http://localhost:8080/api/solve-weekly-plan
```

---

## Key Contacts & Resources

- **Foundry Planning Engine:** https://github.com/mbsm/foundry_planner_engine
- **Architecture design:** [INTEGRATION_ARCHITECTURE.md](INTEGRATION_ARCHITECTURE.md)
- **Implementation tasks:** [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md)
- **AI agent guide:** [.github/copilot-instructions.md](.github/copilot-instructions.md)

---

## Timeline at a Glance

```
Week 1-2: Foundation (rename, schema, foundry_planner_engine dep)
Week 2-3: ETL & Data Adapter (StrategyDataBridge, ResultReader)
Week 3-4: Orchestration (StrategyOrchestrator, enhanced scheduler)
Week 4-5: UI & Docs (/plano-semanal page, docs)
Week 5-6: Testing & Validation (unit + integration tests)
Week 6-7: Deploy & Monitor (production release, SLA)
Month 2+: Harvest benefits (monitor lateness, line smoothness)
```

---

## Need More Detail?

- **"How does the MIP solver work?"** → See foundry_planner_engine README + INTEGRATION_ARCHITECTURE.md
- **"What code do I need to write?"** → See IMPLEMENTATION_CHECKLIST.md
- **"What data goes where?"** → See INTEGRATION_ARCHITECTURE.md (data architecture section)
- **"How should AI agents understand this?"** → See [.github/copilot-instructions.md](.github/copilot-instructions.md)
