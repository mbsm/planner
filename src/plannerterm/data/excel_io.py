from __future__ import annotations

import io
import re
import unicodedata
from datetime import datetime

import pandas as pd


def read_excel_bytes(content: bytes) -> pd.DataFrame:
    """Read .xlsx bytes into a DataFrame.

    v1: reads first sheet.
    """
    bio = io.BytesIO(content)
    df = pd.read_excel(bio)
    # normalize column names
    df.columns = [str(c).strip() for c in df.columns]
    return df


def normalize_col_name(name: str) -> str:
    """Normalize Excel column names to an ASCII-ish snake_case token.

    Handles SAP exports with accents, non-breaking spaces, tabs, and punctuation.
    """

    s = str(name or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("\u00a0", " ")
    s = re.sub(r"[\s\t]+", " ", s)
    # keep alnum + spaces, turn the rest into spaces
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\s+", "_", s).strip("_")
    return s


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [normalize_col_name(c) for c in df.columns]
    return df


def to_int01(value) -> int:
    """Coerce common Excel numeric/bool-ish values to 0/1."""
    if value is None:
        return 0
    try:
        if isinstance(value, float) and pd.isna(value):
            return 0
    except Exception:
        pass
    s = str(value).strip().lower()
    if s in {"1", "true", "si", "sí", "x"}:
        return 1
    if s in {"0", "false", "no", ""}:
        return 0
    try:
        return 1 if int(float(s)) != 0 else 0
    except Exception:
        return 0


_DIGITS_RE = re.compile(r"^\d+$")


def parse_int_strict(value, *, field: str) -> int:
    """Parse an integer value from SAP exports.

    Accepts ints, floats like 123.0, and digit-only strings (keeps leading zeros).
    Raises ValueError otherwise.
    """
    if value is None:
        raise ValueError(f"{field} vacío")

    if isinstance(value, int):
        return int(value)

    if isinstance(value, float):
        if pd.isna(value):
            raise ValueError(f"{field} vacío")
        if float(value).is_integer():
            return int(value)
        raise ValueError(f"{field} inválido (no entero): {value!r}")

    s = str(value).strip()
    if not s:
        raise ValueError(f"{field} vacío")
    if _DIGITS_RE.match(s):
        return int(s)

    raise ValueError(f"{field} inválido: {value!r}")


def coerce_date(value) -> str:
    """Coerce common Excel/Pandas date representations to ISO YYYY-MM-DD."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        raise ValueError("fecha_entrega vacía")

    if isinstance(value, datetime):
        return value.date().isoformat()

    # pandas Timestamp
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime().date().isoformat()

    s = str(value).strip()
    # Accept YYYY-MM-DD
    try:
        return datetime.fromisoformat(s).date().isoformat()
    except Exception:
        pass

    # Accept DD-MM-YYYY / DD/MM/YYYY
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except Exception:
            continue

    raise ValueError(f"fecha_entrega inválida: {value!r}")


def coerce_float(value) -> float | None:
    """Coerce common Excel/Pandas numeric representations to float.

    Returns None when value is empty/NaN.
    Accepts numbers and strings (handles ',' as decimal separator).
    """
    if value is None:
        return None
    try:
        if isinstance(value, float) and pd.isna(value):
            return None
    except Exception:
        pass

    if isinstance(value, (int, float)):
        return float(value)

    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None

    # Handle common LATAM formats: 1.234,56 -> 1234.56
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")

    try:
        return float(s)
    except Exception:
        return None
