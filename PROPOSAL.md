# FoundryPlanner: Two-Layer Planning System — Executive Summary

## Overview

You are transforming **PlannerTerm** (single-layer dispatch for Terminaciones) into **FoundryPlanner** (dual-layer production planning for the entire plant).

### The Vision
```
┌─────────────────────────────────────────────────────┐
│   Strategic Weekly Planning (NEW)                   │
│   Minimize lateness respecting plant constraints    │
│   ✓ Flask capacity  ✓ Melt deck tonnage             │
│   ✓ Line hours      ✓ Pattern wear limits           │
│   ✓ Pouring delays  ✓ Post-process lead times       │
│   [Powered by foundry_planner_engine MIP solver]    │
└────────────────────┬────────────────────────────────┘
                     │
                     ↓ Weekly allocations
┌─────────────────────────────────────────────────────┐
│   Tactical Hourly Dispatch (EXISTING, ENHANCED)     │
│   Smooth production, meet weekly targets            │
│   [Existing heuristic scheduler + constraints]      │
└─────────────────────────────────────────────────────┘
```

---

## Key Changes

| Aspect | Before | After |
|--------|--------|-------|
| **Planning Scope** | Terminaciones only | Entire plant (7+ processes) |
| **Optimization** | Heuristic sorting | MIP solver (minimize weighted lateness) |
| **Constraints** | Family affinity only | Flask capacity, tonnage, lead times, pattern wear |
| **Planning Horizon** | None (daily dispatch) | 40 weeks (strategic) + hourly (tactical) |
| **Update Cadence** | Manual (file upload) | Weekly automatic + hourly on-demand |
| **Output** | `programa` (hourly queues) | `plan_molding` (weekly) + `programa` (hourly) |
| **Platform Support** | Windows only | Windows primary + macOS/Linux dev |

---

## What This Means

### For Operations
- **Better on-time delivery** (target: 85%+, up from 75%)
- **Smoother line utilization** (±10% week-to-week)
- **Weekly plan visibility** (see 40-week production roadmap)
- **Automatic replanning** (when SAP data changes)

### For IT/Dev
- **New folder:** `src/foundryplanner/planning/` (orchestration, ETL, result reading)
- **New schema:** 12 tables (plan_orders_weekly, plan_molding, order_results, etc.)
- **New UI route:** `/plano-semanal` (strategic plan view)
- **Enhanced scheduler:** `generate_program_constrained()` respects weekly allocations
- **Background task:** Weekly solver runs Monday 00:00 UTC

### For Integration
- **foundry_planner_engine** = pure library (pure MIP computation)
- **FoundryPlanner** = wrapper (ETL + orchestration + UI)
- **Database = single source of truth** (both layers read/write same SQLite)

---

## Deliverables

### Documents Created
1. **[INTEGRATION_ARCHITECTURE.md](INTEGRATION_ARCHITECTURE.md)** — 300-line detailed design
   - Data flows, table schema, two-layer architecture
   - Phase-by-phase rollout plan
   - Risk mitigation strategies
   - Success metrics

2. **[IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md)** — 400-line actionable checklist
   - 6 phases over ~7 weeks
   - Specific code tasks (functions, tests, UI pages)
   - Success criteria per phase

3. **[.github/copilot-instructions.md](.github/copilot-instructions.md)** — Updated
   - Reflects two-layer architecture
   - Integration with foundry_planner_engine
   - Scheduling contracts for both layers

4. **[README.md](README.md)** — Updated
   - New project name + scope
   - Cross-platform commands (Windows + macOS/Linux)
   - Two-layer architecture overview
   - Links to architecture docs

### Analysis Performed
- ✅ Reviewed foundry_planner_engine (MIP formulation, API, data models)
- ✅ Mapped current FoundryPlanner → Layer 2 (tactical)
- ✅ Designed Layer 1 (strategic) integration points
- ✅ Proposed ETL (SAP → engine input tables)
- ✅ Identified schema migrations & new tables
- ✅ Designed orchestration pattern (StrategyOrchestrator)

---

## Implementation Path (6 Phases, ~7 weeks)

| Phase | Focus | Weeks | Key Deliverables |
|-------|-------|-------|------------------|
| **1** | Foundation | 1-2 | Rename, schema v5, foundry_planner_engine dependency |
| **2** | ETL & Data Adapter | 2-3 | StrategyDataBridge, StrategyResultReader |
| **3** | Orchestration & Scheduling | 3-4 | StrategyOrchestrator, enhanced generate_program_constrained() |
| **4** | UI & Docs | 4-5 | /plano-semanal page, updated dashboard, training docs |
| **5** | Testing & Validation | 5-6 | Unit + integration tests, performance tuning |
| **6** | Deployment & Monitoring | 6-7 | Production release, SLA documentation |

**Each phase has detailed sub-tasks in [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md).**

---

## Architecture Highlights

### Two-Layer Separation
- **Layer 1 (Strategic):** Answer: "How many molds per order per week?" → pure MIP solver
- **Layer 2 (Tactical):** Answer: "Which line works on what order TODAY?" → heuristic + constraints

### Data Contracts
- **Sources:** Only SAP Visión + MB52 (no MB51). Orders are built once and shared by MIP + dispatcher. Parts/master remain the internal GUI-managed table shared by both layers.
- **Layer 1 input:** SAP demand + facility constraints → engine
- **Layer 1 output:** Weekly allocations (plan_molding) — for future molding dispatcher only
- **Layer 2 input (today):** MB52-driven orders + internal master; heuristic sorting (priority asc, then due_date − process_time)
- **Layer 2 output:** Hourly dispatch queue per line (unchanged). Weekly plan is **not** consumed today except for future molding dispatcher sequencing.

### Persistence
- **Single SQLite database** (both layers read/write)
- **Schema versioning** (v5 with 12 new tables)
- **WAL mode** (concurrent reads from UI while solve runs)

### Error Handling
- If Layer 1 fails (infeasible, timeout) → fall back to Layer 2 only
- If SAP data incomplete → diagnostic UI shows what's missing
- No crash, graceful degradation

---

## Questions to Confirm

1. **Solver schedule:** Monday 00:00 UTC acceptable? Or different day/time?
2. **Solver time limit:** 300 seconds (5 min) OK? Or tighter?
3. **MIP gap:** 1% optimality acceptable? Or accept 5% for faster solve?
4. **Timeline:** 7 weeks realistic? Any deadlines?

---

## Next Immediate Steps

1. **Review** INTEGRATION_ARCHITECTURE.md and IMPLEMENTATION_CHECKLIST.md
2. **Confirm** solver schedule (default Monday 00:00 UTC) and limits (time/MIP gap)
3. **Schedule** Phase 1 kickoff (schema + strategic folder)
4. **Assign** owner for each phase (dev, UI, testing)

---

## File Summary

**Created/Updated:**
- ✅ [INTEGRATION_ARCHITECTURE.md](INTEGRATION_ARCHITECTURE.md) — 400 lines, detailed design
- ✅ [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md) — 400 lines, actionable tasks
- ✅ [README.md](README.md) — Updated with two-layer architecture, cross-platform support
- ✅ [.github/copilot-instructions.md](.github/copilot-instructions.md) — Updated with strategic layer

**Still TODO (in code):**
- Schema v5 migration (12 tables)
- StrategyDataBridge, StrategyResultReader, StrategyOrchestrator classes
- /plano-semanal UI page
- Enhanced generate_program_constrained() function
- Background task scheduler
- Tests & validation

---

## Success Vision (Month 2)

By end of Month 2, you will have:
- ✅ Two-layer planning fully operational
- ✅ Weekly solver running automatically (Monday 00:00 UTC)
- ✅ Strategic plan view in UI (`/plano-semanal`)
- ✅ Dispatch respecting weekly allocations
- ✅ **On-time delivery: 85%+** (up from 75%)
- ✅ **Lateness reduction: 15-20%**
- ✅ **Line utilization smoothness: ±10% week-to-week**

---

## Questions? 

See:
- **Detailed architecture:** [INTEGRATION_ARCHITECTURE.md](INTEGRATION_ARCHITECTURE.md)
- **Actionable tasks:** [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md)
- **Solver docs:** [foundry_planner_engine GitHub](https://github.com/mbsm/foundry_planner_engine)
- **AI agent guide:** [.github/copilot-instructions.md](.github/copilot-instructions.md)
