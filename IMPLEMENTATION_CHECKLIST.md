# FoundryPlanner: Implementation Checklist (Updated 2026-01-25)

This checklist reflects the current state of the repo (what is already implemented and what remains). For the architecture and design rationale, see [INTEGRATION_ARCHITECTURE.md](INTEGRATION_ARCHITECTURE.md).

---

## Current Status (Works End-to-End)

### Two-layer system (Strategic + Tactical)
- [x] **Strategic (weekly):** foundry_planner_engine is integrated and runnable from the UI.
- [x] **Tactical (hourly/on-demand):** dispatcher remains independent of the weekly plan.

### Engine integration
- [x] Engine vendored as submodule: `external/foundry_planner_engine`.
- [x] Engine import path handled via `src/foundryplanner/planning/engine_adapter.py`.
- [x] **Separate engine database** (`engine.db`) is used to avoid table/schema collisions with the app database.
- [x] Solver runs off the NiceGUI event loop (background thread) to avoid UI disconnects.

### Configuration UI
- [x] `/config/planificador` exists (MIP/CBC tuning: time limit, mip gap, horizon, threads, solver msg).
- [x] `/config` (Dispatcher) is organized in a single column with collapsible sections and per-process line-count badges.

### Scheduled weekly solve
- [x] A background weekly solve scheduler exists in `src/foundryplanner/app.py`.
- [x] Schedule is configurable via `strategy_solve_day` (0-6) and `strategy_solve_hour` (0-23) in config.

---

## Implemented Building Blocks (Code Exists)

### Planning layer files
- [x] `src/foundryplanner/planning/data_bridge.py` populates engine inputs into `engine.db`.
- [x] `src/foundryplanner/planning/orchestrator.py` orchestrates validate → populate → solve.
- [x] `src/foundryplanner/planning/models.py` defines basic DTOs for plan visualization.

### Notes / known mismatch to fix
- [ ] `src/foundryplanner/planning/result_reader.py` currently reads from the app DB, but the engine writes outputs into `engine.db`.
  - Decision needed: (A) read directly from `engine.db`, or (B) copy/merge outputs back into the app DB after solve.

---

## Pending (High Value Next Steps)

### Strategic UI (`/plano-semanal`)
- [ ] Replace placeholder copy with real data from the plan outputs.
- [ ] Show `order_results` table (filter/sort; drill-down per order).
- [ ] Show line/week utilization (heatmap or table) from capacities vs plan.
- [ ] Export plan to Excel (for meetings/archives).

### Persistence contract for strategic outputs
- [ ] Pick one and standardize:
  - [ ] **Option A:** UI reads planning outputs directly from `engine.db`.
  - [ ] **Option B:** After solve, copy outputs into app DB tables for UI/reporting/history.

### Testing
- [ ] Add planning-layer tests:
  - [ ] `tests/test_data_bridge.py`
  - [ ] `tests/test_orchestrator.py`
  - [ ] `tests/test_result_reader.py` (after the engine.db vs app-db decision)

---

## Later / Optional

- [ ] Plan history/versioning (keep previous runs and compare scenarios).
- [ ] SLA/observability: persist solve duration + solver status per run.
- [ ] Documentation polish:
  - [ ] `docs/strategic_planning.md`
  - [ ] `docs/integration_guide.md`

---

## Success Criteria (Practical)

- [ ] Weekly solve completes reliably (timeouts handled; UI stays responsive).
- [ ] Strategic view is actionable (KPIs + allocations + export).
- [ ] Dispatcher stays independent (until a dedicated molding dispatcher is implemented).

---

## References

- Architecture: [INTEGRATION_ARCHITECTURE.md](INTEGRATION_ARCHITECTURE.md)
- Solver config: [docs/solver_configuration.md](docs/solver_configuration.md)
- Copilot conventions: [.github/copilot-instructions.md](.github/copilot-instructions.md)
