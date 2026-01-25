# Solver Configuration Options

The strategic planning layer uses the `foundry_planner_engine` solver, which accepts optional configuration parameters. These options control solve time, solution quality, and planning horizon.

## Available Options

### `time_limit_seconds`
- **Type:** Integer
- **Default:** None (solver runs until optimality or timeout)
- **Description:** Maximum wall-clock time (in seconds) for the MIP solver to run. If the solver hasn't found an optimal solution within this time, it returns the best solution found so far.
- **Typical range:** 60-600 seconds
- **Example:**
  ```python
  options = {"time_limit_seconds": 300}  # 5 minutes
  ```

### `mip_gap`
- **Type:** Float (0.0 to 1.0)
- **Default:** 0.0 (exact optimality)
- **Description:** Acceptable optimality gap for the MIP solver. A value of 0.01 means "stop when within 1% of optimal". Larger gaps speed up solve time but may sacrifice quality.
- **Typical range:** 0.01 (1%) to 0.05 (5%)
- **Example:**
  ```python
  options = {"mip_gap": 0.02}  # Accept 2% suboptimality
  ```

### `planning_horizon_weeks`
- **Type:** Integer
- **Default:** 40 (hardcoded in solver.py)
- **Description:** Number of weeks to plan ahead. Longer horizons increase model size and solve time but allow better capacity smoothing.
- **Typical range:** 12-52 weeks
- **Example:**
  ```python
  options = {"planning_horizon_weeks": 26}  # 6 months
  ```

## Configuring in FoundryPlanner

Options are stored in the `app_config` table and passed to the solver via `StrategyOrchestrator`. Default values (if not configured):

| Key | Default | Notes |
|-----|---------|-------|
| `strategy_time_limit_seconds` | 300 | 5-minute timeout |
| `strategy_mip_gap` | 0.01 | 1% optimality gap |
| `strategy_planning_horizon_weeks` | 40 | ~10 months |

### Example: Updating via Repository

```python
repo.set_config("strategy_time_limit_seconds", "180")  # 3 minutes
repo.set_config("strategy_mip_gap", "0.02")            # 2% gap
repo.set_config("strategy_planning_horizon_weeks", "26")  # 6 months
```

### Example: UI Configuration Page

Add a settings section under `/config` to allow operators to adjust solver tuning:

```python
with ui.card():
    ui.label("Solver Configuration").classes("text-xl font-semibold")
    time_limit = ui.number("Max solve time (seconds)", value=300, min=30, max=600)
    mip_gap = ui.number("MIP gap tolerance", value=0.01, min=0.0, max=0.1, step=0.01)
    horizon = ui.number("Planning horizon (weeks)", value=40, min=12, max=52)
    
    async def save():
        repo.set_config("strategy_time_limit_seconds", str(int(time_limit.value)))
        repo.set_config("strategy_mip_gap", str(mip_gap.value))
        repo.set_config("strategy_planning_horizon_weeks", str(int(horizon.value)))
        ui.notify("Solver config saved")
    
    ui.button("Save", on_click=save)
```

## Performance Tuning Guidelines

### Fast Solve (< 60s)
- Set `time_limit_seconds: 60`
- Set `mip_gap: 0.05` (5%)
- Use for: daily replanning, interactive scenarios

### Balanced (default)
- Set `time_limit_seconds: 300`
- Set `mip_gap: 0.01` (1%)
- Use for: weekly production planning

### High Quality (> 5 min)
- Set `time_limit_seconds: 600+`
- Set `mip_gap: 0.001` (0.1%)
- Use for: monthly strategic planning, board presentations

## Solver Behavior

The engine uses **PuLP** with the **CBC** MIP solver (open-source). If the solver:
- **Finds optimal:** Returns status `SUCCESS` with exact solution
- **Hits time limit:** Returns status `SUCCESS` with best feasible solution found
- **Fails feasibility:** Returns status `INFEASIBLE` if constraints cannot be satisfied
- **Crashes:** Returns status `ERROR` with exception message

## Monitoring Solve Quality

After each solve, check:
- **Status:** Should be `SUCCESS`
- **Objective value:** Lower = less lateness
- **Gap:** If using `mip_gap`, monitor actual gap achieved
- **Runtime:** If consistently hitting time limit, increase it or relax gap

## Future Enhancements

- Support for commercial solvers (Gurobi, CPLEX) for faster solves on large instances
- Multi-threaded solving (CBC supports `-threads` flag)
- Warm-start from previous plan (reuse basis)
- Sensitivity analysis (what-if scenarios)

## References

- **PuLP Documentation:** https://coin-or.github.io/pulp/
- **CBC Solver Options:** https://github.com/coin-or/Cbc
- **foundry_planner_engine README:** [external/foundry_planner_engine/README.md](../external/foundry_planner_engine/README.md)
