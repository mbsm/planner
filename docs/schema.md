# Database Schema (v5)

FoundryPlanner uses SQLite with WAL enabled. Schema versioning lives in `schema_version`.

Important implementation detail (current): the strategic solver runs against a **separate SQLite database file** (`engine.db`) to avoid table/schema collisions with the app DB.

---

## App DB (main)

The main app database stores SAP imports, the tactical dispatcher data, and user-managed master data.

Common tables:
- `families`
- `app_config`
- `parts` (internal master)
- `sap_mb52` (raw MB52 upload)
- `sap_vision` (raw Visión upload)
- `orders` (per-process demand built from MB52+Visión)
- `programa` / `last_program` (dispatch outputs)
- `orderpos_priority` / `order_priority`
- `line_config`
- `program_in_progress` / `program_in_progress_item`
- `vision_kpi_daily`, `mb52_progress_last`, `vision_progress_last`

Also present (created in schema v5): `plan_*` / `order_results` tables.

Note: today these plan tables are **not** the source of truth for the weekly solver outputs; they are reserved for a possible future “copy-back” of engine outputs into the app DB.

---

## Engine DB (`engine.db`)

This database is created/populated by the planning layer before running the engine.

Typical engine inputs (names are engine-owned):
- `orders`
- `parts`
- `molding_lines_config`
- `flasks_inventory`
- `capacities_weekly`
- `global_capacities_weekly`
- `initial_flask_usage`

Typical engine outputs:
- `plan_molding`
- `order_results`
- (and any other engine-derived tables)

---

## Notes

- Migrations run in `Db.ensure_schema()` and bump `schema_version` to 5.
- Dispatcher remains independent of weekly planner outputs.
