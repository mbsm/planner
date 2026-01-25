# Database Schema (v5)

FoundryPlanner uses SQLite with WAL enabled. Schema versioning lives in the `schema_version` table; v5 adds the strategic planning layer while keeping existing dispatch tables unchanged.

## Dispatch (existing, shared)
- families
- app_config
- parts (shared by planner and dispatcher)
- sap_mb52 (raw MB52 upload)
- sap_vision (raw Visión upload)
- orders (shared by planner and dispatcher)
- programa / last_program (dispatch outputs)
- orderpos_priority / order_priority
- line_config
- program_in_progress / program_in_progress_item
- vision_kpi_daily, mb52_progress_last, vision_progress_last

## Strategic Planning (new in v5)
Inputs:
- plan_orders_weekly
- plan_parts_routing
- plan_molding_lines_config
- plan_flasks_inventory
- plan_capacities_weekly
- plan_global_capacities_weekly
- plan_initial_flask_usage

Outputs:
- plan_molding
- plan_pouring
- plan_shakeout
- plan_completion
- order_results

## Notes
- Schema migrations run in `Db.ensure_schema()` and bump `schema_version` to 5.
- Dispatcher remains independent of planner outputs; both layers share `orders` and `parts` only.
- Indexes added on plan tables for order/line/week lookups.
