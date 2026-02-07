"""Tests for Vision plant filter using alloy catalog."""

import io
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
    return Repository(db)


def test_vision_filter_uses_alloy_catalog(repo: Repository):
    """Test that Vision import only accepts finished products (40XX00YYYYY) with configured alloys."""    
    # Insert some test alloys in catalog (33, 45 active; 99 inactive)
    with repo.db.connect() as con:
        con.execute("DELETE FROM core_alloy_catalog")
        con.executemany(
            "INSERT INTO core_alloy_catalog (alloy_code, alloy_name, is_active) VALUES (?, ?, ?)",
            [
                ('33', 'CM3', 1),  # Active
                ('45', 'Test Alloy', 1),  # Active
                ('99', 'Inactive Alloy', 0),  # Inactive
            ]
        )
        con.commit()
    
    vision_bytes = make_excel_bytes({
        "Pedido": ["P001", "P002", "P003", "P004", "P005", "P006"],
        "Posici\u00f3n": ["10", "20", "30", "40", "50", "60"],
        "Cod. material": [
            "40330012345",  # Valid: Pieza with alloy 33 (active)
            "40450098765",  # Valid: Pieza with alloy 45 (active)
            "40990012345",  # Invalid: Pieza with alloy 99 (inactive)
            "43533012345",  # Invalid: Fundido (not finished product)
            "12345678901",  # Invalid: Unknown pattern
            "40210012345",  # Invalid: Pieza with alloy 21 (not in catalog)
        ],
        "Descripci\u00f3n material": ["MAT1", "MAT2", "MAT3", "MAT4", "MAT5", "MAT6"],
        "Fecha de pedido": ["2024-02-01"] * 6,
        "Solicitado": [10, 20, 15, 25, 5, 8],
        "Status comercial": ["Activo"] * 6,
    })
    
    repo.import_sap_vision_bytes(content=vision_bytes)
    
    with repo.db.connect() as con:
        rows = con.execute(
            "SELECT cod_material FROM core_sap_vision_snapshot ORDER BY cod_material"
        ).fetchall()
    
    # Should only import materials that are finished products (40XX00YYYYY) with active alloys
    assert len(rows) == 2
    materials = [r["cod_material"] for r in rows]
    assert "40330012345" in materials  # Alloy 33 active
    assert "40450098765" in materials  # Alloy 45 active
    # All others rejected


def test_vision_filter_with_ztlh_tipo_posicion(repo: Repository):
    """Test that ZTLH tipo_posicion bypasses alloy filter."""
    
    # Setup alloys (only 33 active)
    with repo.db.connect() as con:
        con.execute("DELETE FROM core_alloy_catalog")
        con.execute("INSERT INTO core_alloy_catalog (alloy_code, alloy_name, is_active) VALUES ('33', 'CM3', 1)")
        con.commit()
    
    vision_bytes = make_excel_bytes({
        "Pedido": ["P001", "P002"],
        "Posici\u00f3n": ["10", "20"],
        "Tipo posici\u00f3n": ["", "ZTLH"],  # Second is special ZTLH
        "Cod. material": [
            "40450012345",  # Invalid alloy 45, normal tipo
            "40450098765",  # Invalid alloy 45, but ZTLH bypasses filter
        ],
        "Descripci\u00f3n material": ["MAT1", "MAT2"],
        "Fecha de pedido": ["2024-02-01"] * 2,
        "Solicitado": [10, 20],
        "Status comercial": ["Activo"] * 2,
    })
    
    repo.import_sap_vision_bytes(content=vision_bytes)
    
    with repo.db.connect() as con:
        rows = con.execute(
            "SELECT cod_material FROM core_sap_vision_snapshot ORDER BY cod_material"
        ).fetchall()
    
    # Should only import ZTLH row (bypasses alloy filter)
    assert len(rows) == 1
    assert rows[0]["cod_material"] == "40450098765"


def test_vision_filter_fallback_when_no_alloys(repo: Repository):
    """Test that filter falls back to default alloys if catalog is empty."""
    
    # Empty alloy catalog
    with repo.db.connect() as con:
        con.execute("DELETE FROM core_alloy_catalog")
        con.commit()
    
    vision_bytes = make_excel_bytes({
        "Pedido": ["P001", "P002"],
        "Posici\u00f3n": ["10", "20"],
        "Cod. material": [
            "40330012345",  # Alloy 33 (in fallback set)
            "40990012345",  # Alloy 99 (not in fallback set)
        ],
        "Descripci\u00f3n material": ["MAT1", "MAT2"],
        "Fecha de pedido": ["2024-02-01"] * 2,
        "Solicitado": [10, 20],
        "Status comercial": ["Activo"] * 2,
    })
    
    repo.import_sap_vision_bytes(content=vision_bytes)
    
    with repo.db.connect() as con:
        rows = con.execute(
            "SELECT cod_material FROM core_sap_vision_snapshot"
        ).fetchall()
    
    # Should use fallback set (33 included, 99 excluded)
    assert len(rows) == 1
    assert rows[0]["cod_material"] == "40330012345"
