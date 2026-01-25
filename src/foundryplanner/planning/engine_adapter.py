from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Any, Dict

SolveFn = Callable[[str, Dict[str, Any] | None], Dict[str, Any]]


def ensure_engine_on_path() -> Path:
    """Ensure the foundry_planner_engine submodule src/ is importable.

    Returns the engine src path if it exists, else raises FileNotFoundError.
    """
    here = Path(__file__).resolve()
    repo_root = here.parents[3]  # .../planner
    engine_src = repo_root / "external" / "foundry_planner_engine" / "src"
    if not engine_src.exists():
        raise FileNotFoundError(f"Engine path not found: {engine_src}")
    engine_path_str = str(engine_src)
    if engine_path_str not in sys.path:
        sys.path.append(engine_path_str)
    return engine_src


def import_engine_solve() -> SolveFn:
    """Import and return the engine's solve() entrypoint, ensuring path is set."""
    ensure_engine_on_path()
    from src.main import solve  # type: ignore

    return solve
