from __future__ import annotations

import asyncio


from foundryplanner.data.repository import Repository
from foundryplanner.planning.engine_adapter import import_engine_solve


class StrategyOrchestrator:
    """Orchestrates weekly strategic planning solve.
    
    Does NOT trigger dispatch regeneration - dispatch layer is independent.
    """

    def __init__(self, repo: Repository):
        self.repo = repo

    async def solve_weekly_plan(self, *, process: str = "terminaciones", force: bool = False) -> dict:
        """Run foundry_planner_engine after preparing inputs.

        Returns:
            dict with keys: status ("success"|"infeasible"|"error"), message, stats
        """
        # IMPORTANT: The solver is CPU-heavy and blocks for seconds/minutes.
        # Run it in a worker thread so the NiceGUI event loop stays responsive.
        return await asyncio.to_thread(self._solve_weekly_plan_sync, process=process, force=force)

    def _solve_weekly_plan_sync(self, *, process: str = "terminaciones", force: bool = False) -> dict:
        try:
            if force:
                # IMPORTANT: Do not wipe existing orders.
                # Only attempt a best-effort rebuild from SAP when there are *no* orders yet.
                # Otherwise, pressing "Forzar planificación" would clear the Config > Pedidos sheet.
                if self.repo.count_orders(process=process) == 0:
                    self.repo.try_rebuild_orders_from_sap_for(process=process)

            # Step 1: Validate SAP data completeness
            validation = self._validate_data(process=process)
            if not validation["is_valid"]:
                return {
                    "status": "error",
                    "message": f"Data validation failed: {validation['message']}",
                    "stats": {},
                }
            
            # Step 2: Populate input tables via DataBridge (into separate engine.db)
            bridge = self.repo.get_strategy_data_bridge()
            stats = bridge.populate_all(process=process, week_range=(0, 40))
            
            if stats.get("orders", 0) == 0:
                return {
                    "status": "error",
                    "message": "No orders to plan (orders table is empty)",
                    "stats": stats,
                }
            
            # Step 3: Call foundry_planner_engine.solve() with engine database path
            solve = import_engine_solve()
            engine_db_path = str(bridge.get_engine_db_path().resolve())
            
            # Get solver options from config
            time_limit = int(self.repo.get_config(key="strategy_time_limit_seconds", default="300") or 300)
            mip_gap = float(self.repo.get_config(key="strategy_mip_gap", default="0.01") or 0.01)

            planning_horizon_weeks = int(self.repo.get_config(key="strategy_planning_horizon_weeks", default="40") or 40)
            threads_raw = self.repo.get_config(key="strategy_solver_threads", default="")
            threads = None
            if threads_raw is not None and str(threads_raw).strip() != "":
                threads = int(str(threads_raw).strip())

            solver_msg = int(self.repo.get_config(key="strategy_solver_msg", default="0") or 0)
            
            options = {
                "time_limit_seconds": time_limit,
                "mip_gap": mip_gap,
                "planning_horizon_weeks": planning_horizon_weeks,
                "solver_msg": solver_msg,
            }

            if threads is not None:
                options["threads"] = threads
            
            result = solve(engine_db_path, options=options)
            
            # Step 4: Handle results
            if result.get("status") == "SUCCESS":
                return {
                    "status": "success",
                    "message": "Weekly plan solved successfully",
                    "stats": stats,
                    "solver_result": result,
                }
            elif result.get("status") == "INFEASIBLE":
                return {
                    "status": "infeasible",
                    "message": "Solver could not find feasible solution (constraints too tight)",
                    "stats": stats,
                    "solver_result": result,
                }
            else:
                return {
                    "status": "error",
                    "message": f"Solver error: {result.get('message', 'Unknown error')}",
                    "stats": stats,
                    "solver_result": result,
                }
        
        except Exception as e:
            return {
                "status": "error",
                "message": f"Orchestration error: {str(e)}",
                "stats": {},
            }

    def _validate_data(self, process: str = "terminaciones") -> dict:
        """Validate that required data exists before running solver.

        Returns:
            dict with keys: is_valid (bool), message (str)
        """
        # Check that we have orders
        orders = self.repo.get_orders_model(process=process)
        if not orders:
            sap_mb52 = self.repo.count_sap_mb52()
            sap_vision = self.repo.count_sap_vision()
            return {
                "is_valid": False,
                "message": (
                    f"No orders found in database for process='{process}'. "
                    f"SAP tables: MB52 rows={sap_mb52}, Visión rows={sap_vision}. "
                    "Go to /actualizar, upload MB52 + Visión, then click 'Forzar planificación' again."
                ),
            }
        
        # Check that we have parts master
        parts = self.repo.get_parts_model()
        if not parts:
            return {"is_valid": False, "message": "No parts found in master data"}
        
        # Check that we have lines configured
        lines = self.repo.get_lines(process=process)
        if not lines:
            return {"is_valid": False, "message": f"No lines configured for process {process}"}
        
        return {"is_valid": True, "message": "Data validation passed"}
