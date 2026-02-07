from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from foundryplan.data.db import Db


def save_schedule_result(
    db: Db,
    *,
    scenario_id: int,
    asof_date: str,
    result: dict[str, Any],
) -> None:
    """Save planner schedule result to database.
    
    Args:
        db: Database connection wrapper
        scenario_id: Planner scenario ID
        asof_date: ISO date string (YYYY-MM-DD)
        result: Output dict from solve_planner_heuristic/run_planner
    """
    run_timestamp = datetime.now().isoformat()
    
    with db.connect() as con:
        con.execute(
            """
            INSERT INTO planner_schedule_results (
                scenario_id, run_timestamp, asof_date, status,
                suggested_horizon_days, actual_horizon_days,
                skipped_orders, horizon_exceeded,
                molds_schedule_json, pour_days_json, shakeout_days_json,
                completion_days_json, finish_days_json, late_days_json,
                errors_json, objective
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scenario_id,
                run_timestamp,
                asof_date,
                result.get("status", "UNKNOWN"),
                result.get("suggested_horizon_days"),
                result.get("actual_horizon_days", 0),
                result.get("skipped_orders", 0),
                1 if result.get("horizon_exceeded") else 0,
                json.dumps(result.get("molds_schedule") or {}),
                json.dumps(result.get("pour_days") or {}),
                json.dumps(result.get("shakeout_days") or {}),
                json.dumps(result.get("completion_days") or {}),
                json.dumps(result.get("finish_days") or {}),
                json.dumps(result.get("late_days") or {}),
                json.dumps(result.get("errors") or []),
                result.get("objective"),
            ),
        )
        con.commit()


def get_latest_schedule_result(
    db: Db,
    *,
    scenario_id: int,
) -> dict[str, Any] | None:
    """Load latest planner schedule result from database.
    
    Args:
        db: Database connection wrapper
        scenario_id: Planner scenario ID
        
    Returns:
        Dict with same structure as solve_planner_heuristic output, or None if no result found
    """
    with db.connect() as con:
        row = con.execute(
            """
            SELECT 
                run_timestamp, asof_date, status,
                suggested_horizon_days, actual_horizon_days,
                skipped_orders, horizon_exceeded,
                molds_schedule_json, pour_days_json, shakeout_days_json,
                completion_days_json, finish_days_json, late_days_json,
                errors_json, objective
            FROM planner_schedule_results
            WHERE scenario_id = ?
            ORDER BY run_timestamp DESC
            LIMIT 1
            """,
            (scenario_id,),
        ).fetchone()
        
        if not row:
            return None
    
    # Reconstruct result dict
    return {
        "run_timestamp": row[0],
        "asof_date": row[1],
        "status": row[2],
        "suggested_horizon_days": row[3],
        "actual_horizon_days": row[4],
        "skipped_orders": row[5],
        "horizon_exceeded": bool(row[6]),
        "molds_schedule": json.loads(row[7] or "{}"),
        "pour_days": json.loads(row[8] or "{}"),
        "shakeout_days": json.loads(row[9] or "{}"),
        "completion_days": json.loads(row[10] or "{}"),
        "finish_days": json.loads(row[11] or "{}"),
        "late_days": json.loads(row[12] or "{}"),
        "errors": json.loads(row[13] or "[]"),
        "objective": row[14],
    }


def delete_old_schedule_results(
    db: Db,
    *,
    scenario_id: int,
    keep_last_n: int = 10,
) -> None:
    """Delete old schedule results, keeping only the most recent N.
    
    Args:
        db: Database connection wrapper
        scenario_id: Planner scenario ID
        keep_last_n: Number of results to keep (default 10)
    """
    with db.connect() as con:
        con.execute(
            """
            DELETE FROM planner_schedule_results
            WHERE scenario_id = ?
            AND run_timestamp NOT IN (
                SELECT run_timestamp
                FROM planner_schedule_results
                WHERE scenario_id = ?
                ORDER BY run_timestamp DESC
                LIMIT ?
            )
            """,
            (scenario_id, scenario_id, keep_last_n),
        )
        con.commit()

