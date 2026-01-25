from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

from nicegui import app, ui

from foundryplanner.settings import Settings, default_db_path
from foundryplanner.data.db import Db
from foundryplanner.data.repository import Repository
from foundryplanner.ui.pages import register_pages


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Planta Rancagua")
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    settings = Settings(db_path=default_db_path(), host=args.host, port=args.port)

    db = Db(settings.db_path)
    db.ensure_schema()

    repo = Repository(db)
    planta = repo.get_config(key="planta", default="Planta Rancagua") or "Planta Rancagua"
    register_pages(repo)

    def _seconds_until_next(day_of_week: int, hour: int) -> float:
        """Return seconds until next occurrence of weekday/hour in UTC."""
        now = datetime.utcnow()
        target_date = now.date() + timedelta(days=(day_of_week - now.weekday()) % 7)
        target = datetime.combine(target_date, time(hour=hour))
        if target <= now:
            target += timedelta(days=7)
        return max((target - now).total_seconds(), 60.0)

    @app.on_startup
    async def schedule_weekly_solve() -> None:
        """Schedule weekly strategic solve at configured day/hour (UTC)."""

        day_raw = repo.get_config("strategy_solve_day", default="0") or "0"
        hour_raw = repo.get_config("strategy_solve_hour", default="0") or "0"
        day = max(0, min(6, int(day_raw)))
        hour = max(0, min(23, int(hour_raw)))

        orchestrator = repo.get_strategy_orchestrator()

        async def _runner() -> None:
            while True:
                delay = _seconds_until_next(day, hour)
                await asyncio.sleep(delay)
                try:
                    result = await orchestrator.solve_weekly_plan()
                    if result.get("status") == "success":
                        repo.set_config("strategy_last_solve_at", datetime.utcnow().isoformat())
                except Exception as exc:  # pragma: no cover - background logging
                    print(f"[planner] Scheduled weekly solve failed: {exc}")

        asyncio.create_task(_runner())

    assets_dir = Path(__file__).resolve().parents[2] / "assets"
    if assets_dir.exists():
        app.add_static_files("/assets", str(assets_dir))

    if sys.platform == "win32":
        @app.on_startup
        async def _silence_windows_connection_reset() -> None:
            # Suppress noisy ConnectionResetError 10054 from Windows clients dropping websockets.
            loop = asyncio.get_running_loop()

            def _handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
                exc = context.get("exception")
                if isinstance(exc, ConnectionResetError) and getattr(exc, "winerror", None) == 10054:
                    return
                loop.default_exception_handler(context)

            loop.set_exception_handler(_handler)

    ui.run(host=settings.host, port=settings.port, title=planta, reload=False)


if __name__ in {"__main__", "__mp_main__"}:
    main()
