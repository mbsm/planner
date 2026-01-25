from __future__ import annotations

import argparse
import asyncio
import sys
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
