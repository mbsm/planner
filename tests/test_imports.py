import io
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from foundryplan.data.db import Db
from foundryplan.data.repository import Repository


def make_excel_bytes(data: dict) -> bytes:
    """Create a minimal Excel file from a column->values dict."""
    bio = io.BytesIO()
    pd.DataFrame(data).to_excel(bio, index=False)
    bio.seek(0)
    return bio.read()


@pytest.fixture()
def repo(tmp_path) -> Repository:
    db_path = Path(tmp_path) / "test.db"
    db = Db(db_path)
    db.ensure_schema()
    repo = Repository(db)
    repo.set_config(key="sap_material_prefixes", value="*")
    return repo


def test_import_excel_dispatches_to_specific_handlers(monkeypatch, repo):
    flags: dict[str, tuple[bytes, str | None]] = {}

    def fake_mb52(self, *, content: bytes, mode: str = "replace"):
        flags["mb52"] = (content, mode)

    def fake_vision(self, *, content: bytes):
        flags["vision"] = (content, None)

    def fake_demolding(self, *, content: bytes):
        flags["demolding"] = (content, None)

    monkeypatch.setattr(Repository, "import_sap_mb52_bytes", fake_mb52)
    monkeypatch.setattr(Repository, "import_sap_vision_bytes", fake_vision)
    monkeypatch.setattr(Repository, "import_sap_demolding_bytes", fake_demolding)

    content = make_excel_bytes({"material": ["123"]})

    repo.import_excel_bytes(kind="mb52", content=content)
    repo.import_excel_bytes(kind="vision", content=content)
    repo.import_excel_bytes(kind="demolding", content=content)

    assert "mb52" in flags and flags["mb52"][1] == "replace"
    assert "vision" in flags
    assert "demolding" in flags


def test_import_excel_rejects_unknown_kind(repo):
    content = make_excel_bytes({"material": ["123"]})
    with pytest.raises(ValueError):
        repo.import_excel_bytes(kind="unknown", content=content)


def test_import_mb52_invalid_mode_raises(repo):
    with pytest.raises(ValueError):
        repo.import_sap_mb52_bytes(content=b"irrelevant", mode="invalid")


def test_import_demolding_truncates_flask_id_and_skips_invalid(repo):
    demolding_bytes = make_excel_bytes(
        {
            "material": ["MAT1", "MAT2"],
            "lote": ["L1", ""],
            "flask_id": ["ABC1234", ""],
            "demolding_date": ["2024-01-02", "2024-01-03"],
            "demolding_time": ["08:00", "09:00"],
            "cooling_hours": [12, 8],
            "mold_type": ["X", "Y"],
            "poured_date": ["2024-01-01", "2024-01-02"],
            "poured_time": ["07:00", "07:30"],
            "mold_quantity": [2, 1],
        }
    )

    repo.import_sap_demolding_bytes(content=demolding_bytes)

    with repo.db.connect() as con:
        rows = con.execute(
            "SELECT material, flask_id, demolding_date, mold_quantity FROM sap_demolding_snapshot"
        ).fetchall()

    assert len(rows) == 2  # both rows are kept; empty/NaN flask_id is coerced to string

    row = rows[0]
    assert row["material"] == "MAT1"
    assert row["flask_id"] == "ABC"  # truncated to first 3 characters
    assert row["demolding_date"] == "2024-01-02"
    assert row["mold_quantity"] == 2
