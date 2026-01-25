from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    db_path: Path
    host: str = "0.0.0.0"
    port: int = 8080


def default_db_path() -> Path:
    # Fixed, repo-local database location (keeps paths stable across machines).
    return Path("db") / "foundryplanner.db"
