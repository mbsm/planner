"""Shared utilities for repository implementations.

This module contains helper functions used across Data/Dispatcher/Planner repositories.
"""

from __future__ import annotations

import re
import json
import logging
from foundryplan.data.excel_io import parse_int_strict

logger = logging.getLogger(__name__)


def normalize_process(process: str | None, processes: dict[str, dict[str, str]]) -> str:
    """Normalize process name to canonical form."""
    p = str(process or "terminaciones").strip().lower()
    aliases = {
        "vulcanizado": "en_vulcanizado",
        "en-vulcanizado": "en_vulcanizado",
        "vulc": "en_vulcanizado",
        "en vulcanizado": "en_vulcanizado",
        "toma_dureza": "toma_de_dureza",
        "toma de dureza": "toma_de_dureza",
        "toma-de-dureza": "toma_de_dureza",
    }
    p = aliases.get(p, p)
    if p not in processes:
        raise ValueError(f"process no soportado: {process!r}")
    return p


def normalize_sap_key(value) -> str | None:
    """Normalize SAP numeric identifiers loaded through Excel.

    Excel often turns values like 000010 into 10.0; we normalize both sides
    to a canonical string without decimals and without leading zeros.
    Also handles whitespace, tabs, and non-breaking spaces.
    """
    if value is None:
        return None
    # Clean whitespace first (including non-breaking spaces)
    s = str(value).replace("\u00a0", " ").strip()
    if not s or s.lower() == "nan":
        return None
    try:
        n = parse_int_strict(value, field="sap_key")
        return str(int(n))
    except Exception:
        # If it's not numeric, return the cleaned string
        return s


def lote_to_int(value) -> int | None:
    """Coerce MB52 lote into an integer correlativo.

    Some SAP exports include alphanumeric lotes (e.g. '0030PD0674').
    For Terminaciones test lotes, the correlativo is the numeric prefix
    (digits before letters). We keep the scheduling logic numeric by
    extracting the first digit group.
    
    Returns None if lote is empty/invalid.
    """
    if value is None:
        return None
    
    # Handle pandas NaN and string "nan"
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None
    
    try:
        return int(parse_int_strict(value, field="Lote"))
    except Exception:
        m = re.search(r"\d+", s)
        if not m:
            return None  # No digits found, return None instead of raising
        return int(m.group(0))


def is_lote_test(lote: str) -> bool:
    """Determine if a lote is a production test (alphanumeric).
    
    Business rule: alphanumeric lotes are production tests and must be prioritized.
    """
    if not lote:
        return False
    return bool(re.search(r"[A-Za-z]", str(lote)))
