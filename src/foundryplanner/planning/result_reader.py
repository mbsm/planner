from __future__ import annotations

from foundryplanner.data.repository import Repository
from foundryplanner.planning.models import (
    WeeklyPlan,
    OrderResultsKPI,
    LineUtilization,
    LatenessSummary,
)


class StrategyResultReader:
    """Reads planning outputs (plan_molding, order_results) for UI/dispatch."""

    def __init__(self, repo: Repository):
        self.repo = repo

    def get_order_results(self, process: str = "terminaciones") -> list[OrderResultsKPI]:
        """Fetch all order KPIs from order_results table.

        Returns list of OrderResultsKPI with start_week, delivery_week, lateness metrics.
        """
        with self.repo.db.connect() as con:
            rows = con.execute(
                """
                SELECT process, pedido, posicion, start_week, delivery_week, is_late, weeks_late
                FROM order_results
                WHERE process = ?
                ORDER BY pedido, posicion
                """,
                (process,),
            ).fetchall()
        
        # Count total molds planned per order
        molds_by_order = {}
        with self.repo.db.connect() as con:
            mold_rows = con.execute(
                """
                SELECT pedido, posicion, SUM(molds_planned) as total_molds
                FROM plan_molding
                WHERE process = ?
                GROUP BY pedido, posicion
                """,
                (process,),
            ).fetchall()
        for ped, pos, molds in mold_rows:
            molds_by_order[(ped, pos)] = int(molds or 0)
        
        results = []
        for proc, ped, pos, start, delivery, late, weeks_late in rows:
            molds = molds_by_order.get((ped, pos), 0)
            results.append(
                OrderResultsKPI(
                    process=str(proc),
                    pedido=str(ped),
                    posicion=str(pos),
                    molds_to_plan=molds,
                    start_week=int(start) if start is not None else None,
                    delivery_week=int(delivery) if delivery is not None else None,
                    is_late=bool(int(late or 0)),
                    weeks_late=int(weeks_late or 0),
                )
            )
        return results

    def get_molding_plan_by_week(self, week_id: int, process: str = "terminaciones") -> list[WeeklyPlan]:
        """Get plan_molding rows for a specific week."""
        with self.repo.db.connect() as con:
            rows = con.execute(
                """
                SELECT process, line_id, pedido, posicion, week_id, molds_planned
                FROM plan_molding
                WHERE process = ? AND week_id = ?
                ORDER BY line_id, pedido, posicion
                """,
                (process, week_id),
            ).fetchall()
        
        return [
            WeeklyPlan(
                process=str(r[0]),
                line_id=int(r[1]),
                pedido=str(r[2]),
                posicion=str(r[3]),
                week_id=int(r[4]),
                molds_planned=int(r[5]),
            )
            for r in rows
        ]

    def get_molding_plan_by_order(self, pedido: str, posicion: str, process: str = "terminaciones") -> list[WeeklyPlan]:
        """Get plan_molding rows for a specific order."""
        with self.repo.db.connect() as con:
            rows = con.execute(
                """
                SELECT process, line_id, pedido, posicion, week_id, molds_planned
                FROM plan_molding
                WHERE process = ? AND pedido = ? AND posicion = ?
                ORDER BY week_id, line_id
                """,
                (process, pedido, posicion),
            ).fetchall()
        
        return [
            WeeklyPlan(
                process=str(r[0]),
                line_id=int(r[1]),
                pedido=str(r[2]),
                posicion=str(r[3]),
                week_id=int(r[4]),
                molds_planned=int(r[5]),
            )
            for r in rows
        ]

    def get_line_utilization_by_week(self, process: str = "terminaciones") -> list[LineUtilization]:
        """Compute % capacity utilized per line per week."""
        with self.repo.db.connect() as con:
            rows = con.execute(
                """
                SELECT 
                    c.process,
                    c.line_id,
                    c.week_id,
                    c.molds_capacity,
                    COALESCE(SUM(p.molds_planned), 0) as molds_planned
                FROM plan_capacities_weekly c
                LEFT JOIN plan_molding p 
                    ON c.process = p.process 
                    AND c.line_id = p.line_id 
                    AND c.week_id = p.week_id
                WHERE c.process = ?
                GROUP BY c.process, c.line_id, c.week_id, c.molds_capacity
                ORDER BY c.line_id, c.week_id
                """,
                (process,),
            ).fetchall()
        
        results = []
        for proc, line_id, week_id, capacity, planned in rows:
            cap = int(capacity or 0)
            plan = int(planned or 0)
            util_pct = (plan / cap * 100.0) if cap > 0 else 0.0
            
            results.append(
                LineUtilization(
                    process=str(proc),
                    line_id=int(line_id),
                    week_id=int(week_id),
                    molds_capacity=cap,
                    molds_planned=plan,
                    utilization_pct=round(util_pct, 2),
                )
            )
        return results

    def get_lateness_summary(self, process: str = "terminaciones") -> LatenessSummary:
        """Count on-time vs late orders; avg weeks late."""
        with self.repo.db.connect() as con:
            row = con.execute(
                """
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN is_late = 0 THEN 1 ELSE 0 END) as on_time,
                    SUM(CASE WHEN is_late = 1 THEN 1 ELSE 0 END) as late,
                    AVG(CASE WHEN is_late = 1 THEN weeks_late ELSE 0 END) as avg_late,
                    MAX(weeks_late) as max_late
                FROM order_results
                WHERE process = ?
                """,
                (process,),
            ).fetchone()
        
        if not row or row[0] == 0:
            return LatenessSummary(
                process=process,
                total_orders=0,
                on_time_count=0,
                late_count=0,
                on_time_pct=0.0,
                avg_weeks_late=0.0,
                max_weeks_late=0,
            )
        
        total = int(row[0])
        on_time = int(row[1] or 0)
        late = int(row[2] or 0)
        avg_late = float(row[3] or 0.0)
        max_late = int(row[4] or 0)
        on_time_pct = (on_time / total * 100.0) if total > 0 else 0.0
        
        return LatenessSummary(
            process=process,
            total_orders=total,
            on_time_count=on_time,
            late_count=late,
            on_time_pct=round(on_time_pct, 2),
            avg_weeks_late=round(avg_late, 2),
            max_weeks_late=max_late,
        )

    def get_plan_summary(self, process: str = "terminaciones") -> dict:
        """High-level summary for UI (utilization, lateness KPIs, etc.)."""
        lateness = self.get_lateness_summary(process=process)
        utilization = self.get_line_utilization_by_week(process=process)
        
        # Compute average utilization across all lines/weeks
        avg_util = (
            sum(u.utilization_pct for u in utilization) / len(utilization)
            if utilization
            else 0.0
        )
        
        return {
            "process": process,
            "total_orders": lateness.total_orders,
            "on_time_pct": lateness.on_time_pct,
            "late_count": lateness.late_count,
            "avg_weeks_late": lateness.avg_weeks_late,
            "avg_line_utilization_pct": round(avg_util, 2),
        }
