from __future__ import annotations


from foundryplanner.data.repository import Repository
from foundryplanner.planning.engine_adapter import import_engine_solve


class StrategyOrchestrator:
    """Orchestrates weekly solve and dispatch regeneration."""

    def __init__(self, repo: Repository):
        self.repo = repo

    async def solve_weekly_plan(self, *, process: str = "terminaciones", force: bool = False) -> dict:
        """Run foundry_planner_engine after preparing inputs.

        Returns:
            dict with keys: status ("success"|"infeasible"|"error"), message, stats
        """
        try:
            # Step 1: Validate SAP data completeness
            validation = self._validate_data(process=process)
            if not validation["is_valid"]:
                return {
                    "status": "error",
                    "message": f"Data validation failed: {validation['message']}",
                    "stats": {},
                }
            
            # Step 2: Populate input tables via DataBridge
            bridge = self.repo.get_strategy_data_bridge()
            stats = bridge.populate_all(process=process, week_range=(0, 40))
            
            if stats.get("orders", 0) == 0:
                return {
                    "status": "error",
                    "message": "No orders to plan (plan_orders_weekly is empty)",
                    "stats": stats,
                }
            
            # Step 3: Call foundry_planner_engine.solve()
            solve = import_engine_solve()
            db_path = str(self.repo.db.path.resolve())
            
            # Get solver options from config
            time_limit = int(self.repo.get_config("strategy_time_limit_seconds", default="300") or 300)
            mip_gap = float(self.repo.get_config("strategy_mip_gap", default="0.01") or 0.01)
            
            options = {
                "time_limit_seconds": time_limit,
                "mip_gap": mip_gap,
            }
            
            result = solve(db_path, options=options)
            
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

    async def regenerate_dispatch_from_plan(self, *, process: str = "terminaciones") -> dict:
        """Rebuild dispatch queues constrained by plan_molding.

        Currently calls existing unconstrained dispatcher (future: add weekly plan constraints).
        
        Returns:
            dict with keys: status, message, programs_generated
        """
        try:
            # Read plan_molding to get weekly allocations (future: pass to constrained scheduler)
            # For now, just regenerate using existing unconstrained logic
            
            # Auto-generate dispatch for this process
            lines = self.repo.get_lines(process=process)
            orders = self.repo.get_orders_model(process=process)
            parts = self.repo.get_parts_model()
            priority_set = self.repo.get_priority_orderpos()
            
            from foundryplanner.dispatching.scheduler import generate_program
            
            program, errors = generate_program(
                lines=lines,
                orders=orders,
                parts=parts,
                priority_orderpos=priority_set,
            )
            
            # Save program
            self.repo.save_last_program(process=process, program=program, errors=errors)
            
            total_rows = sum(len(rows) for rows in program.values())
            
            return {
                "status": "success",
                "message": f"Dispatch regenerated for {process}",
                "programs_generated": total_rows,
                "errors": len(errors),
            }
        
        except Exception as e:
            return {
                "status": "error",
                "message": f"Dispatch regeneration error: {str(e)}",
                "programs_generated": 0,
            }

    def _validate_data(self, process: str = "terminaciones") -> dict:
        """Validate that required data exists before running solver.

        Returns:
            dict with keys: is_valid (bool), message (str)
        """
        # Check that we have orders
        orders = self.repo.get_orders_model(process=process)
        if not orders:
            return {"is_valid": False, "message": "No orders found in database"}
        
        # Check that we have parts master
        parts = self.repo.get_parts_model()
        if not parts:
            return {"is_valid": False, "message": "No parts found in master data"}
        
        # Check that we have lines configured
        lines = self.repo.get_lines(process=process)
        if not lines:
            return {"is_valid": False, "message": f"No lines configured for process {process}"}
        
        return {"is_valid": True, "message": "Data validation passed"}
