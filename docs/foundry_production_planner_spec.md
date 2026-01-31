Foundry Production Planner

## Technical Specification (SQLite + Python + OR-Tools CP-SAT)

---

## 1. Purpose

Build an automated production planner for a foundry whose main decision is:

> **How many molds to produce each working day for each order**, so that orders are delivered by their due dates, while respecting operational constraints and minimizing disruptive pattern changes.

The planner is run **manually every Monday**, replanning forward from the current state of the shop.

---

## 2. Core Concepts & Terminology

### Orders
- Planned **per order**, not per part.
- Order tuple: **(order_id, part_id, qty, due_date)**.
- Different orders may share the same `part_id` but have different due dates.

### Parts
- Describe physical and process characteristics.
- Part tuple: **(part_id, flask_size, cooling_hours, finishing_hours, gross_weight, alloy)**.

### Key operational rules
- One mold produces **one part**.
- Molding line can run **up to 6 patterns (orders) in parallel**.
- Changing patterns is **very costly** → avoid unloading/reloading.
- Prefer to keep an order/pattern running once started (implemented via penalties).
- Delivery occurs **1 working day after finishing ends**.
- Orders are considered satisfied only by **delivered** units (not by molding).

---

## 3. Planning Cadence

- Planner is run **every Monday**.
- Planning horizon starts on that Monday (`asof_date`).
- Horizon extends far enough to cover:
  - the latest due date,
  - maximum process lag,
  - safety buffer (recommended: +10 working days).

---

## 4. Technology Stack

- **Python 3.10+**
- **SQLite** (via `sqlite3`)
- **OR-Tools CP-SAT**
- Optional: Pandas (reporting)

---

## 5. Database Schema (SQLite)

All tables are scoped by `scenario_id` to support multiple scenarios.

### 5.1 `scenarios`
- `scenario_id` INTEGER PRIMARY KEY
- `name` TEXT
- `created_at` TEXT

### 5.2 `parts`
- `scenario_id` INTEGER
- `part_id` TEXT
- `flask_size` TEXT CHECK in ('S','M','L')
- `cool_hours` REAL
- `finish_hours` REAL
- `gross_weight_ton` REAL
- `alloy` TEXT NULL
- PRIMARY KEY (`scenario_id`, `part_id`)

### 5.3 `orders`
- `scenario_id` INTEGER
- `order_id` TEXT
- `part_id` TEXT
- `qty` INTEGER
- `due_date` TEXT  -- ISO 'YYYY-MM-DD'
- `priority` INTEGER DEFAULT 100  -- lower = higher priority
- PRIMARY KEY (`scenario_id`, `order_id`)

### 5.4 `resources`
- `scenario_id` INTEGER PRIMARY KEY
- `flasks_S` INTEGER
- `flasks_M` INTEGER
- `flasks_L` INTEGER
- `molding_max_per_day` INTEGER
- `molding_max_same_part_per_day` INTEGER
- `pour_max_ton_per_day` REAL
- `notes` TEXT NULL

### 5.5 `calendar_workdays`
Precomputed working days for each scenario/horizon (recommended).
- `scenario_id` INTEGER
- `workday_index` INTEGER  -- 0..H-1
- `date` TEXT              -- ISO date
- `week_index` INTEGER     -- integer grouping for weekly reports
- PRIMARY KEY (`scenario_id`, `workday_index`)
- UNIQUE (`scenario_id`, `date`)

> Only working days appear here (no weekends). Holidays can be added later by omitting those dates.

---

## 6. Initial State (Critical for Replanning)

Initial state represents what already exists on Monday morning before solving.

### 6.1 Remaining molds to plan (replanning support)

**Goal:** if an order has qty 6 and 3 molds were already produced last week, the planner must only plan 3 more.

Table: `initial_order_progress`
- `scenario_id` INTEGER
- `asof_date` TEXT  -- the Monday date the plan starts
- `order_id` TEXT
- `molded_qty_credit` INTEGER DEFAULT 0

Computation used by the solver:
- `remaining_qty[o] = orders.qty[o] - molded_qty_credit[o]`
- Clamp at 0 if needed.

### 6.2 Patterns already loaded

Table: `initial_patterns_loaded`
- `scenario_id` INTEGER
- `asof_date` TEXT
- `order_id` TEXT
- `is_loaded` INTEGER  -- 0/1

Used to reduce/penalize unnecessary unloading/reloading on day 0.

### 6.3 Flasks currently in use

Table: `initial_flask_inuse`
- `scenario_id` INTEGER
- `asof_date` TEXT
- `flask_size` TEXT CHECK in ('S','M','L')
- `release_workday_index` INTEGER  -- relative to plan start; 0 means released on day 0
- `qty_inuse` INTEGER

Interpretation: these flasks are unavailable until `release_workday_index`.

### 6.4 Pour capacity already committed

Table: `initial_pour_load`
- `scenario_id` INTEGER
- `asof_date` TEXT
- `workday_index` INTEGER
- `tons_committed` REAL

Interpretation: these pours cannot be changed and consume pour capacity.

---

## 7. Process Timing (Day-Bucket Approximation, v1)

Convert hours → working-day lags:

For each part `p`:
- `cool_days[p]   = ceil(cool_hours[p] / 24)`
- `finish_days[p] = ceil(finish_hours[p] / 24)`

From molding day `d`:

- Pour day: `d + 1`
- Shakeout day: `d + 1 + cool_days[p] + 1`
- Delivery day: `d + L[p]` where:

`L[p] = 1 (pour) + cool_days[p] + 1 (shakeout) + finish_days[p] + 1 (delivery)`

Also define shakeout occupancy length for flasks:

`S[p] = 1 (pour) + cool_days[p] + 1 (shakeout)`

A mold made on day `k` occupies a flask from day `k` through day `k + S[p] - 1`.

---

## 8. CP-SAT Variables (Order-Level)

Let:
- `O` = set of orders
- `D` = workday indices 0..H-1
- `p(o)` = part_id of order `o`

### 8.1 Production
- `x[o,d] ∈ ℕ` molds of order `o` molded on day `d`

### 8.2 Pattern usage indicator
- `y[o,d] ∈ {0,1}` = 1 if order `o` is produced at all on day `d`

Link:
- `x[o,d] ≤ molding_max_same_part_per_day * y[o,d]`
- (Optional tightening) `x[o,d] ≥ 1 * y[o,d]` if you want y=1 to imply at least 1 mold.

---

## 9. Constraints

### 9.1 Remaining quantity per order
Compute:
- `remaining_qty[o] = max(0, qty[o] - molded_qty_credit[o])`

Constraint:
- `Σ_d x[o,d] = remaining_qty[o]`

### 9.2 Molding capacity (total)
For each day `d`:
- `Σ_o x[o,d] ≤ molding_max_per_day`

### 9.3 Maximum parallel patterns
For each day `d`:
- `Σ_o y[o,d] ≤ 6`

### 9.4 Pouring capacity (molten metal)
Pour on day `t` comes from molds made on day `t-1`.

Let `w[o] = gross_weight_ton[p(o)]`.

For each day `t`:
- `pour_from_plan[t] = Σ_o w[o] * x[o, t-1]` (if t-1 in range else 0)
- `pour_total[t] = pour_from_plan[t] + tons_committed[t]`

Constraint:
- `pour_total[t] ≤ pour_max_ton_per_day`

**Implementation note:** CP-SAT requires integer coefficients. Represent tons as integer kilograms:
- `w_kg = round(w_ton * 1000)`
- `cap_kg = round(pour_max_ton_per_day * 1000)`
- committed_kg similarly.

### 9.5 Flask availability (release at shakeout) + initial in-use
A mold of order `o` uses one flask of size `flask_size[p(o)]` for `S[p(o)]` working days starting at molding day `k`.

For each size `s` and day `d`:

- `inuse_plan[s,d] = Σ_{o with size s} Σ_{k: k ≤ d < k+S[p(o)]} x[o,k]`

Initial inuse contribution:
- `inuse_initial[s,d] = Σ rows r where r.size=s and r.release_workday_index > d of qty_inuse[r]`

Total:
- `inuse_total[s,d] = inuse_plan[s,d] + inuse_initial[s,d]`

Constraint:
- `inuse_total[s,d] ≤ flasks_s`

### 9.6 Delivery and lateness (order-level)
Delivered units for order `o` on day `t`:
- `deliv[o,t] = x[o, t - L[p(o)]]` if valid index, else 0.

Delivered by due date:
- `delivered_by_due[o] = Σ_{t=0..D_o} deliv[o,t]`

Late slack:
- `late_qty[o] ≥ remaining_qty[o] − delivered_by_due[o]`
- `late_qty[o] ≥ 0`

---

## 10. Pattern Change Penalty (Key Feature)

We penalize starting/stopping patterns day-to-day.

Inputs:
- `y_initial[o]` from `initial_patterns_loaded` (0/1) for the plan start Monday.

Variables:
- `start[o,d] ∈ {0,1}`
- `stop[o,d] ∈ {0,1}`

Constraints:
- For d = 0:
  - `start[o,0] ≥ y[o,0] − y_initial[o]`
- For d ≥ 1:
  - `start[o,d] ≥ y[o,d] − y[o,d-1]`
  - `stop[o,d]  ≥ y[o,d-1] − y[o,d]`

Interpretation:
- `start=1` when the order/pattern begins being used (loaded)
- `stop=1` when it stops being used (unloaded)

This captures changes in the set of up to 6 active patterns.

Optional stronger policy (v1.1):
- Penalize multiple starts for same order to discourage on/off/on:
  - `num_starts[o] = Σ_d start[o,d]`
  - add penalty for `num_starts[o] - 1` (first start is “free”).

---

## 11. Objective Function

Weighted sum designed to behave like lexicographic priorities:

1) On-time delivery (minimize late quantity)
2) Minimize pattern changes (starts/stops)
3) Reduce WIP congestion (flask in use)

Objective:

Minimize:
- `W_late   * Σ_o late_qty[o]`
- `W_switch * Σ_o Σ_d (start[o,d] + stop[o,d])`
- `W_wip    * Σ_s Σ_d inuse_total[s,d]`

Recommended weights:
- `W_late   = 1_000_000`
- `W_switch = 100_000`
- `W_wip    = 1`

Adjust weights as needed, preserving hierarchy: `W_late >> W_switch >> W_wip`.

---

## 12. Outputs

All outputs are written with a unique `run_id` (e.g., UUID) per solve.

### 12.1 Daily plan by order
Table: `plan_daily_order`
- `scenario_id`
- `run_id`
- `asof_date`
- `workday_index`
- `date`
- `order_id`
- `part_id`
- `molds_molded` INTEGER
- PRIMARY KEY (`scenario_id`, `run_id`, `workday_index`, `order_id`)

### 12.2 Weekly plan by order
Table: `plan_weekly_order`
- `scenario_id`
- `run_id`
- `asof_date`
- `week_index`
- `order_id`
- `part_id`
- `molds_molded_week` INTEGER
- PRIMARY KEY (`scenario_id`, `run_id`, `week_index`, `order_id`)

### 12.3 Order status
Table: `order_status`
- `scenario_id`
- `run_id`
- `asof_date`
- `order_id`
- `part_id`
- `qty`
- `molded_qty_credit`
- `remaining_qty`
- `due_date`
- `delivered_by_due`
- `late_qty`
- `completion_workday_index` NULL
- PRIMARY KEY (`scenario_id`, `run_id`, `order_id`)

Completion day (optional):
- smallest day `t` where cumulative `Σ_{u≤t} deliv[o,u]` reaches `remaining_qty[o]`.

---

## 13. Execution Flow (Every Monday)

1) User selects `scenario_id` and `asof_date` (Monday).
2) System builds/loads `calendar_workdays` from `asof_date` to horizon end.
3) Load:
   - parts, orders, resources
   - initial_order_progress, initial_patterns_loaded
   - initial_flask_inuse, initial_pour_load
4) Compute `remaining_qty` for each order.
5) Build CP-SAT model:
   - variables x, y, start/stop, late
   - all constraints
   - objective
6) Solve with time limit (e.g., 30–120s) and multi-threading.
7) Write outputs to SQLite.
8) Produce reports (daily + weekly + late orders).

---

## 14. Validation & Sanity Checks

Before solving:
- All `orders.part_id` exist in `parts`.
- All due dates map into the calendar.
- Warn if an order’s due date is earlier than the earliest possible delivery based on lags and remaining qty.
- Ensure initial credits do not exceed qty (clamp or error).

After solving:
- Verify constraints (optional debug mode).
- Summarize:
  - total late quantity
  - number of pattern starts/stops
  - peak flask usage by size
  - peak pour usage

---

## 15. Explicit v1 Assumptions

- Cooling/finishing hours are rounded up to whole working days.
- Calendar includes working days only (no holidays unless provided).
- Pattern changes are discouraged via penalties (not absolutely forbidden).
- One mold = one part.
- No alloy campaign / changeover constraints yet.