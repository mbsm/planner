# FoundryPlanner: Implementation Checklist

This document tracks the phased rollout of the two-layer planning system. For detailed architecture, see [INTEGRATION_ARCHITECTURE.md](INTEGRATION_ARCHITECTURE.md).

---

## Phase 1: Foundation (Weeks 1-2)

_Status: Ready to proceed to Phase 2; remaining open items are tracked as carryover below._

### Rename Project & Update Docs
- [x] Rename package: `plannerterm` → `foundryplanner`
- [x] Update README + docs to FoundryPlanner
- [x] Update copilot instructions: ✅ DONE ([.github/copilot-instructions.md](.github/copilot-instructions.md))
- [x] Create INTEGRATION_ARCHITECTURE.md: ✅ DONE
- [x] Update package metadata (pyproject.toml)
- [x] Update GitHub repo description
  - Set to: "FoundryPlanner – weekly MIP planning + hourly dispatch (NiceGUI + SQLite), powered by foundry_planner_engine"

### Set Up foundry_planner_engine Dependency
- [x] Vendor as git submodule: `external/foundry_planner_engine`
- [x] Wire package import path in code (strategy layer) via `planning.engine_adapter.ensure_engine_on_path()` / `import_engine_solve()`
- [x] Verify Python 3.11+ compatibility → **Declared 3.14+ as minimum**
- [x] Document solver configuration options → **See [docs/solver_configuration.md](docs/solver_configuration.md)**

### Create Planning Folder Structure
- [x] Create `src/foundryplanner/planning/` folder
- [x] Create `src/foundryplanner/planning/__init__.py`
- [x] Create `src/foundryplanner/planning/data_bridge.py` (stub)
- [x] Create `src/foundryplanner/planning/result_reader.py` (stub)
- [x] Create `src/foundryplanner/planning/orchestrator.py` (stub)

### Schema Migrations
- [x] Read current schema version from database (add version tracking if missing)
- [x] Design new tables for Layer 1 (see INTEGRATION_ARCHITECTURE.md)
- [x] Update `src/foundryplanner/data/db.py:Db.ensure_schema()`
  - [x] Add version check: `if current_version < 5: migrate_to_v5()`
  - [x] Create strategic tables (inputs + outputs for foundry_planner_engine)
  - [x] Add indexes for performance (plan_* lookups by order/line/week)
  - [x] Increment schema version to v5
- [x] Create test database to verify migration works
- [x] Document schema changes (add comments in code + docs/schema.md)

---

## Phase 2: ETL & Data Adapter (Weeks 2-3)

### Carryover from Phase 1
- [x] Verify Python 3.11+ compatibility → **Declared 3.14+ as minimum**
- [x] Document solver configuration options → **See [docs/solver_configuration.md](docs/solver_configuration.md)**

### Create SAP ↔ Engine Data Mappers (Visión + MB52 Excel uploads only; internal part master)
- [x] Implement `StrategyDataBridge` class in `src/foundryplanner/planning/data_bridge.py`:
  - [x] `populate_plan_orders_weekly` — Join sap_mb52 + sap_vision + internal master → plan_orders_weekly
  - [x] `populate_plan_parts_routing` — Internal part master → plan_parts_routing
  - [x] `populate_plan_molding_lines_config` — app_config + configuration → plan_molding_lines_config
  - [x] `populate_plan_flasks_inventory` — app_config → plan_flasks_inventory
  - [x] `populate_plan_capacities_weekly` — app_config + maintenance windows → plan_capacities_weekly
  - [x] `populate_plan_global_capacities` — app_config → plan_global_capacities_weekly (melt deck tonnage)
  - [x] `populate_plan_initial_flask_usage` — Current WIP → plan_initial_flask_usage
  - [x] `populate_all` — Call all populate_* methods; return summary stats
  - [x] Handle NULL values, missing config gracefully
  - [x] Add validation: ensure all required fields present
  - [x] Add diagnostics: return summary stats (row counts per table)

- [x] Implement `StrategyResultReader` class in `src/foundryplanner/planning/result_reader.py`:
  - [x] `get_order_results()` — Read order_results table; return structured KPIs
  - [x] `get_molding_plan_by_week(week_id)` — Get plan_molding rows for a specific week
  - [x] `get_molding_plan_by_order(pedido, posicion)` — Get plan_molding rows for a specific order
  - [x] `get_line_utilization_by_week()` — Compute % capacity utilized per line per week
  - [x] `get_lateness_summary()` — Count on-time vs late orders; avg weeks late
  - [x] `get_plan_summary()` — High-level KPIs for dashboard
  - [x] Handle case where plan doesn't exist (return empty/zeros)

### Implement Repository Extensions
- [x] Add to `src/foundryplanner/data/repository.py`:
  ```python
  def get_strategy_data_bridge(self) -> StrategyDataBridge:
    return StrategyDataBridge(self)

  def get_strategy_result_reader(self) -> StrategyResultReader:
    return StrategyResultReader(self)

  def get_strategy_orchestrator(self) -> StrategyOrchestrator:
    return StrategyOrchestrator(self)
  ```

### Create UI Models for Strategic Plan
- [x] Create `src/foundryplanner/planning/models.py`:
  - [x] WeeklyPlan (process, line_id, pedido, posicion, week_id, molds_planned)
  - [x] OrderResultsKPI (process, pedido, posicion, molds_to_plan, start_week, delivery_week, is_late, weeks_late)
  - [x] LineUtilization (process, line_id, week_id, capacity, planned, utilization_pct)
  - [x] LatenessSummary (process, total_orders, on_time_count, late_count, on_time_pct, avg_weeks_late)

---

## Phase 3: Orchestration & Scheduling (Weeks 3-4)

### Create Strategy Orchestrator
- [ ] Implement `StrategyOrchestrator` in `src/foundryplanner/planning/orchestrator.py`:
  ```python
  class StrategyOrchestrator:
      def __init__(self, repo: Repository):
          self.repo = repo
      
      async def solve_weekly_plan(self, *, force=False) -> dict:
          """Orchestrates: validate → ETL → solve → persist → trigger Layer 2"""
          # 1. Validate SAP data completeness
          # 2. Populate input tables
          # 3. Call foundry_planner_engine.solve()
          # 4. Handle results or errors
          # 5. Trigger dispatch regeneration
          return {"status": "success"|"infeasible"|"error", "message": "..."}
      
      async def regenerate_dispatch_from_plan(self) -> dict:
          """Reads plan_molding; calls generate_program_constrained()"""
          # 1. Read plan_molding allocations
          # 2. Call enhanced generate_program()
          # 3. Save resultado
          return {"status": "success"|"error", "message": "..."}
  ```
  - [ ] Add error handling: infeasible → log + alert, don't crash
  - [ ] Add validation: check for missing config (capacities, parts, etc.)
  - [ ] Add diagnostics: log ETL stats (rows inserted, skipped, errors)
  - [ ] Add fallback: if solver fails, keep old plan or fallback to Layer 2 only

### Enhance Dispatch Layer Scheduler
- [ ] Modify `src/foundryplanner/dispatching/scheduler.py`:
  ```python
  def generate_program_constrained(
      lines: list[Line],
      orders: list[Order],
      parts: list[Part],
      weekly_plan: dict[str, int] | None = None,  # NEW
  ) -> tuple[dict[int, list[dict]], list[dict]]:
      """Enhanced scheduler respecting weekly allocations"""
      # 1. Sort orders (same as before, but check plan priority)
      # 2. For each order, cap by weekly_plan[order_id] if available
      # 3. Assign to lowest-load eligible line (respecting cap)
  ```
  - [ ] Add tests for constrained vs unconstrained modes
  - [ ] Ensure backward compatibility (can still call without weekly_plan)

### Add Background Task Runner
- [ ] Update `src/foundryplanner/app.py`:
  ```python
  @app.on_startup
  async def schedule_weekly_solve():
      """Schedule weekly solve at Monday 00:00 UTC"""
      orchestrator = StrategyOrchestrator(repo)
      
      async def scheduled_job():
          while True:
              # Calculate next Monday 00:00 UTC
              delay_seconds = seconds_until_next_monday_midnight()
              await asyncio.sleep(delay_seconds)
              result = await orchestrator.solve_weekly_plan()
              # Log result, notify if needed
      
      asyncio.create_task(scheduled_job())
  ```
  - [ ] Make schedule configurable (app_config: "strategy_solve_day", "strategy_solve_hour")
  - [ ] Add manual trigger button in UI for testing

### Wire Up Orchestrator Calls
- [ ] Update `src/foundryplanner/ui/pages.py`:
  ```python
  def register_pages(repo: Repository) -> None:
      orchestrator = StrategyOrchestrator(repo)
      
      async def on_sap_upload():
          """After SAP upload, auto-trigger solve + dispatch"""
          result = await orchestrator.solve_weekly_plan()
          if result["status"] == "success":
              ui.notify("Plan actualizado")
          else:
              ui.notify(f"Error: {result['message']}", color="negative")
      
      # Wire into existing upload handlers
  ```
  - [ ] Add "Force Replan" button in UI for manual triggers
  - [ ] Add "Last solve timestamp" display on dashboard

---

## Phase 4: UI & Documentation (Weeks 4-5)

### Create Strategic Plan View (`/plano-semanal`)
- [ ] Add new page route in `src/foundryplanner/ui/pages.py`:
  ```python
  @page('/')
  def plano_semanal() -> None:
      """Strategic weekly plan visualization"""
      # Header: Last solve time, replan button, filters
      # Section 1: Order KPIs (late, on-time, avg lateness)
      # Section 2: Heatmap of line utilization per week
      # Section 3: Table of order_results with drill-down
      # Section 4: Download plan as Excel
  ```
  - [ ] Display `order_results` table with sorting/filtering
  - [ ] Heatmap: weeks (x-axis) vs lines (y-axis), colored by % utilization
  - [ ] Order details: expand to show plan_molding allocations per week
  - [ ] Export button: save plan to Excel for meetings/archives

### Update Dashboard (`/`)
- [ ] Add strategic plan widget:
  - Last solve timestamp + status
  - On-time % (current week plan vs historical)
  - Average lateness (current plan vs historical)
  - Alert if plan is stale (> 7 days)
- [ ] Link to `/plano-semanal` for detailed view

### Update Dispatch View (`/programa`)
- [ ] Add filter: "Show plan version" dropdown
- [ ] Highlight source: which orders come from current weekly plan vs ad-hoc
- [ ] Add constraint indicator: show if actual dispatch ≠ planned (warning)

### Create Documentation
- [ ] Write `docs/strategic_planning.md`:
  - Two-layer architecture overview
  - Input data (SAP + config) + validation
  - Optimization objectives & constraints
  - Output interpretation (plan_molding, order_results, lateness)
  - Configuration parameters (solver time limit, MIP gap)
  
- [ ] Write `docs/integration_guide.md`:
  - How to swap/extend optimization engine
  - API contract for StrategyDataBridge / StrategyOrchestrator
  - Testing strategies
  
- [ ] Update `docs/configuracion.md`:
  - Add facility capacity parameters (flask sizes, hourly rates, melt deck tonnage)
  - Explain maintenance windows + seasonal limits
  
- [ ] Update [.github/copilot-instructions.md](.github/copilot-instructions.md): ✅ DONE
- [ ] Update [INTEGRATION_ARCHITECTURE.md](INTEGRATION_ARCHITECTURE.md): ✅ DONE

---

## Phase 5: Testing & Validation (Weeks 5-6)

### Unit Tests
- [ ] `tests/test_data_bridge.py`:
  - SAP records → plan_orders_weekly (joins, filtering)
  - Config → plan_capacities_weekly (defaults, edge cases)
  - NULL handling, missing data
  
- [ ] `tests/test_result_reader.py`:
  - Read empty plan (no solve yet)
  - Read valid plan (KPIs, line utilization)
  - Cache behavior
  
- [ ] `tests/test_orchestrator.py`:
  - Happy path: SAP upload → solve → dispatch trigger
  - Error cases: missing config, infeasible, solver timeout
  - Fallback behavior

- [ ] `tests/test_scheduler_constrained.py`:
  - Dispatch respects weekly_plan caps
  - Backward compatibility (no plan supplied)
  - Edge case: plan cap < order size

### Integration Tests
- [ ] End-to-end workflow:
  1. Upload SAP files (MB52 + Visión)
  2. Trigger weekly solve
  3. Verify plan_molding + order_results populated
  4. Regenerate dispatch
  5. Verify programa respects allocations

- [ ] Stress tests:
  - 500+ orders, 40-week horizon → measure solve time
  - Verify no timeouts, acceptable solution quality

- [ ] Regression tests:
  - Compare new 2-layer output vs old 1-layer on historical data
  - Measure lateness improvement %

### Validation Metrics
- [ ] Lateness improvement: target 15-20% reduction
- [ ] On-time %: target 85%+ (vs current 75%)
- [ ] Line utilization: std dev < 15% week-to-week
- [ ] Solve time: < 60 seconds for typical scenario

---

## Phase 6: Deployment & Monitoring (Weeks 6-7)

### Performance Tuning
- [ ] Profile solver on real SAP data (typical & large scenarios)
- [ ] Tune solver parameters:
  - time_limit_seconds: target < 60s
  - mip_gap: balance 1% (tight) vs 5% (fast)
  - planning_horizon: 40 weeks default, adjustable for faster solve
- [ ] Document SLA: "Weekly solve completes in < 60s; plan available by 06:00 UTC"

### Error Handling & Fallback
- [ ] Test failure modes:
  - Missing SAP data → clear error message, don't solve
  - Infeasible problem → alert + fall back to Layer 2 only
  - Solver timeout → use best found solution or previous plan
  - Database error → log + retry, UI graceful degradation
- [ ] Add monitoring alerts:
  - Solve failed for 2+ weeks → escalate
  - Solve time exceeding SLA → investigate

### Deploy & Release
- [ ] Code review: all PR merged, CI passing
- [ ] Update CHANGELOG with two-layer planning feature
- [ ] Tag version (e.g., v2.0.0)
- [ ] Deploy to staging → test with real SAP data
- [ ] Deploy to production → rollout with monitoring

### User Training & Documentation
- [ ] Create user guide: "How to read the weekly plan"
  - What does "on-time" mean?
  - How to interpret lateness weeks?
  - When to override plan manually?
- [ ] Create admin guide: "How to adjust capacity model"
  - Update flask inventory
  - Adjust line working hours (seasonal)
  - Configure melt deck tonnage limits
- [ ] Create dev guide: "How to integrate custom optimization engine"
  - Swap foundry_planner_engine for another solver
  - Implement alternative StrategyDataBridge / ResultReader

---

## Post-Launch (Ongoing)

### Monitoring & Optimization
- [ ] Weekly review of solve metrics (time, quality, feasibility)
- [ ] Monthly lateness report (on-time %, avg weeks late)
- [ ] Tune solver parameters based on observed performance
- [ ] Collect user feedback: is plan easy to understand? actionable?

### Future Enhancements
- [ ] Manual plan override UI (if operator wants to adjust allocations)
- [ ] Plan versioning: keep history, compare scenarios
- [ ] Sensitivity analysis: "What if we add 10 more flasks?"
- [ ] Integration with production floor data (actual vs planned)
- [ ] Real-time dispatch updates (on-line replanning)

---

## Success Criteria

| Metric | Target | Owner | Deadline |
|--------|--------|-------|----------|
| Schema v5 migrates cleanly | 100% | Dev | Week 1 |
| foundry_planner_engine imports successfully | Yes | Dev | Week 1 |
| Strategic view renders weekly plan | Yes | UI | Week 4 |
| Dispatch respects weekly allocations | 100% | Dispatch | Week 3 |
| Weekly solve < 60 seconds | 95% of runs | Dev | Week 5 |
| Lateness improvement | ≥ 15% | Biz | Month 2 |
| On-time delivery | ≥ 85% | Biz | Month 2 |

---

## Contacts & Questions

- **Architecture questions:** See [INTEGRATION_ARCHITECTURE.md](INTEGRATION_ARCHITECTURE.md)
- **Solver questions:** See [foundry_planner_engine README](https://github.com/mbsm/foundry_planner_engine)
- **Code questions:** See [.github/copilot-instructions.md](.github/copilot-instructions.md)
