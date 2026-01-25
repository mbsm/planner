from __future__ import annotations

from datetime import date, timedelta

from foundryplanner.data.repository import Repository


class StrategyDataBridge:
    """ETL from SAP/internal tables into planning input tables for the weekly solver."""

    def __init__(self, repo: Repository):
        self.repo = repo

    def populate_plan_orders_weekly(self, process: str = "terminaciones") -> int:
        """Build plan_orders_weekly from shared orders table (MB52 + Visión filters).

        Reuses the exact same `orders` table built for dispatching to ensure consistent demand.
        Maps calendar dates to week IDs and aggregates order quantities by (pedido, posicion, part).
        """
        orders = self.repo.get_orders_model(process=process)
        priority_set = self.repo.get_priority_orderpos_set()
        
        # Compute reference date (week 0 = current Monday)
        today = date.today()
        week_zero = today - timedelta(days=today.weekday())
        
        # Aggregate by (pedido, posicion) to avoid duplicates
        aggregated: dict[tuple[str, str], dict] = {}
        for order in orders:
            key = (order.pedido, order.posicion)
            if key in aggregated:
                # Sum quantities if same order appears multiple times
                aggregated[key]["demand_molds"] += order.cantidad
            else:
                # Convert fecha_entrega to week_id
                delta_days = (order.fecha_entrega - week_zero).days
                due_week = max(0, delta_days // 7)
                
                # Priority: tests=2, manual=1, normal=0
                if order.is_test:
                    priority = 2
                elif key in priority_set:
                    priority = 1
                else:
                    priority = 0
                
                aggregated[key] = {
                    "numero_parte": order.numero_parte,
                    "demand_molds": order.cantidad,
                    "due_week": due_week,
                    "priority": priority,
                }
        
        rows_to_insert = [
            (process, pedido, posicion, data["numero_parte"], data["demand_molds"], data["due_week"], data["priority"])
            for (pedido, posicion), data in aggregated.items()
        ]
        
        with self.repo.db.connect() as con:
            con.execute("DELETE FROM plan_orders_weekly WHERE process = ?", (process,))
            con.executemany(
                "INSERT INTO plan_orders_weekly(process, pedido, posicion, numero_parte, demand_molds, due_week, priority) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                rows_to_insert,
            )
        
        return len(rows_to_insert)

    def populate_plan_parts_routing(self, process: str = "terminaciones") -> int:
        """Build plan_parts_routing from internal part master.

        Maps part attributes (weight, lead times, family) to engine input schema.
        """
        parts = self.repo.get_parts_model()
        
        rows_to_insert = []
        for part in parts:
            # Sum post-process lead times (convert days to weeks for engine, or keep in days if engine expects days)
            vulc = part.vulcanizado_dias or 0
            mec = part.mecanizado_dias or 0
            insp = part.inspeccion_externa_dias or 0
            lead_time_days = vulc + mec + insp
            
            # Cooling time: placeholder (we don't have this in parts table; default to 3 days)
            cooling_time_days = 3
            
            # Pattern wear limit: placeholder (default to high value)
            pattern_wear_limit = 9999
            
            rows_to_insert.append((
                part.numero_parte,
                part.peso_ton,
                cooling_time_days,
                lead_time_days,
                pattern_wear_limit,
                part.familia,
            ))
        
        with self.repo.db.connect() as con:
            con.execute("DELETE FROM plan_parts_routing")
            con.executemany(
                "INSERT INTO plan_parts_routing(numero_parte, weight_ton, cooling_time_days, lead_time_days, pattern_wear_limit, family) "
                "VALUES(?, ?, ?, ?, ?, ?)",
                rows_to_insert,
            )
        
        return len(rows_to_insert)

    def populate_plan_molding_lines_config(self, process: str = "terminaciones") -> int:
        """Build plan_molding_lines_config from line configuration.

        Maps configured lines (from line_config table) to engine input schema.
        """
        lines = self.repo.get_lines_model(process=process)
        
        # Default capacity values (can be overridden via app_config later)
        default_hours_per_week = 80.0
        default_molds_per_hour = 1.0
        default_pattern_wear = 9999
        
        rows_to_insert = []
        for line in lines:
            rows_to_insert.append((
                process,
                line.line_id,
                default_hours_per_week,
                default_molds_per_hour,
                default_pattern_wear,
            ))
        
        with self.repo.db.connect() as con:
            con.execute("DELETE FROM plan_molding_lines_config WHERE process = ?", (process,))
            con.executemany(
                "INSERT INTO plan_molding_lines_config(process, line_id, hours_per_week, molds_per_hour, pattern_wear_limit) "
                "VALUES(?, ?, ?, ?, ?)",
                rows_to_insert,
            )
        
        return len(rows_to_insert)

    def populate_plan_flasks_inventory(self) -> int:
        """Build plan_flasks_inventory from configuration.

        Placeholder: uses default values (in real scenario, read from app_config or separate table).
        """
        # Default flask inventory (to be made configurable via app_config)
        default_flasks = [
            ("120", 25, "1,2,3"),  # Flask size, quantity, allowed lines
            ("105", 25, "1,2,3"),
            ("146", 25, "1,2,3"),
        ]
        
        rows_to_insert = [(size, qty, lines) for size, qty, lines in default_flasks]
        
        with self.repo.db.connect() as con:
            con.execute("DELETE FROM plan_flasks_inventory")
            con.executemany(
                "INSERT INTO plan_flasks_inventory(flask_size, quantity, allowed_lines) VALUES(?, ?, ?)",
                rows_to_insert,
            )
        
        return len(rows_to_insert)

    def populate_plan_capacities_weekly(self, process: str = "terminaciones", week_range: tuple[int, int] = (0, 40)) -> int:
        """Build plan_capacities_weekly from config and maintenance windows.

        Default: uniform capacity across all weeks and lines (can be extended for holidays/maintenance).
        """
        lines = self.repo.get_lines_model(process=process)
        default_hours = 80.0
        default_molds_capacity = 125  # Placeholder
        
        rows_to_insert = []
        for week_id in range(week_range[0], week_range[1]):
            for line in lines:
                rows_to_insert.append((
                    process,
                    line.line_id,
                    week_id,
                    default_hours,
                    default_molds_capacity,
                ))
        
        with self.repo.db.connect() as con:
            con.execute(
                "DELETE FROM plan_capacities_weekly WHERE process = ? AND week_id >= ? AND week_id < ?",
                (process, week_range[0], week_range[1]),
            )
            con.executemany(
                "INSERT INTO plan_capacities_weekly(process, line_id, week_id, hours_available, molds_capacity) "
                "VALUES(?, ?, ?, ?, ?)",
                rows_to_insert,
            )
        
        return len(rows_to_insert)

    def populate_plan_global_capacities(self, week_range: tuple[int, int] = (0, 40)) -> int:
        """Build plan_global_capacities_weekly from config.

        Melt deck tonnage limit per week (default: 500 tons/week).
        """
        default_melt_tons = 500.0
        
        rows_to_insert = [(week_id, default_melt_tons) for week_id in range(week_range[0], week_range[1])]
        
        with self.repo.db.connect() as con:
            con.execute(
                "DELETE FROM plan_global_capacities_weekly WHERE week_id >= ? AND week_id < ?",
                (week_range[0], week_range[1]),
            )
            con.executemany(
                "INSERT INTO plan_global_capacities_weekly(week_id, melt_deck_tons) VALUES(?, ?)",
                rows_to_insert,
            )
        
        return len(rows_to_insert)

    def populate_plan_initial_flask_usage(self) -> int:
        """Build plan_initial_flask_usage from current WIP.

        Placeholder: assumes zero initial usage (can be extended to read from current plan state).
        """
        # Default: no flasks in use at start
        rows_to_insert = []
        
        with self.repo.db.connect() as con:
            con.execute("DELETE FROM plan_initial_flask_usage")
            if rows_to_insert:
                con.executemany(
                    "INSERT INTO plan_initial_flask_usage(flask_size, line_id, molds_in_use) VALUES(?, ?, ?)",
                    rows_to_insert,
                )
        
        return len(rows_to_insert)

    def populate_all(self, process: str = "terminaciones", week_range: tuple[int, int] = (0, 40)) -> dict:
        """Populate all input tables for the weekly solver.

        Returns summary statistics for validation and diagnostics.
        """
        stats = {}
        
        stats["orders"] = self.populate_plan_orders_weekly(process=process)
        stats["parts"] = self.populate_plan_parts_routing(process=process)
        stats["lines"] = self.populate_plan_molding_lines_config(process=process)
        stats["flasks"] = self.populate_plan_flasks_inventory()
        stats["capacities"] = self.populate_plan_capacities_weekly(process=process, week_range=week_range)
        stats["global_caps"] = self.populate_plan_global_capacities(week_range=week_range)
        stats["initial_usage"] = self.populate_plan_initial_flask_usage()
        
        return stats
