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
            "Tipo Pieza": ["MOLDE PIEZA 40330012345", "MOLDE PIEZA 43533098765"],  # Text with material code
            "Lote": ["L1", ""],
            "Caja": ["ABC1234", "XYZ5678"],
            "Cancha": ["TCF-L1000", "TCF-L1100"],  # Both valid canchas
            "Fecha Desmoldeo": ["2024-01-02", "2024-01-03"],
            "Hora Desm.": ["08:00", "09:00"],
            "Hs. Enfria": [12, 8],
            "Tipo molde": ["X", "Y"],
            "Fecha fundida": ["2024-01-01", "2024-01-02"],
            "Hora Fundida": ["07:00", "07:30"],
            "Cant. Moldes": [0.5, 1.0],
        }
    )

    repo.import_sap_demolding_bytes(content=demolding_bytes)

    with repo.db.connect() as con:
        # Check snapshot table
        rows = con.execute(
            "SELECT material, flask_id, demolding_date, mold_quantity FROM core_sap_demolding_snapshot ORDER BY material"
        ).fetchall()
        
        assert len(rows) == 2
        
        row = rows[0]
        assert row["material"] == "40330012345"
        assert row["flask_id"] == "ABC"  # truncated to first 3 characters
        assert row["demolding_date"] == "2024-01-02"
        assert row["mold_quantity"] == 0.5
        
        # Check piezas_fundidas table (completed pieces)
        piezas = con.execute(
            "SELECT material, part_code, flask_id FROM core_piezas_fundidas ORDER BY material"
        ).fetchall()
        
        assert len(piezas) == 2
        assert piezas[0]["material"] == "40330012345"
        assert piezas[0]["part_code"] == "12345"  # Extracted from Pieza pattern
        assert piezas[0]["flask_id"] == "ABC1234"  # Full flask_id in piezas table
        
        assert piezas[1]["material"] == "43533098765"
        assert piezas[1]["part_code"] == "98765"  # Extracted from Fundido pattern
