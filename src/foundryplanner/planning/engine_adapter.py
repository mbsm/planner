from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Any, Dict

SolveFn = Callable[[str, Dict[str, Any] | None], Dict[str, Any]]


def ensure_engine_on_path() -> Path:
    """Ensure the foundry_planner_engine submodule is importable.

    The engine's main.py imports from 'src.engine.data_loader', so we need to add
    the foundry_planner_engine root (parent of src/) to sys.path.

    Returns the engine root path if it exists, else raises FileNotFoundError.
    """
    here = Path(__file__).resolve()
    repo_root = here.parents[3]  # .../planner
    engine_root = repo_root / "external" / "foundry_planner_engine"
    engine_src = engine_root / "src"
    if not engine_src.exists():
        raise FileNotFoundError(f"Engine path not found: {engine_src}")
    # Add the engine root so that `from src.engine.xxx import ...` works
    engine_root_str = str(engine_root)
    if engine_root_str not in sys.path:
        sys.path.insert(0, engine_root_str)
    return engine_root


def import_engine_solve() -> SolveFn:
    """Import and return the engine's solve() entrypoint, ensuring path is set."""
    ensure_engine_on_path()
    from src.main import solve  # type: ignore

    return solve
