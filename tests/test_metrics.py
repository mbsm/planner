import pytest
import sqlite3
from datetime import date, timedelta
from foundryplan.data.db import Db
from foundryplan.data.repository import Repository
import tempfile
from pathlib import Path

@pytest.fixture
def temp_db():
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "test.db"
    db = Db(db_path)
    db.ensure_schema()
    
    # Initialize repo
    repo = Repository(db)
    
    yield db, repo
    
    try:
        import shutil
        shutil.rmtree(tmpdir)
    except Exception:
        pass

def test_upsert_vision_kpi_daily_execution(temp_db):
    db, repo = temp_db
    
    # Needs data in sap_vision_snapshot and material_master depending on query
    # Insert dummy vision data
    
    # Ensure columns exist (peso_unitario_ton was added optionally in db.py, ensure it's there)
    with db.connect() as con:
        # Check if table 'parts' exists? No, it should be material_master.
        # Check if query uses 'parts'.
        pass

    # Insert a dummy row in sap_vision_snapshot
    # We need a valid material to join with master if the query relies on it.
    
    repo.upsert_part_master(
        material="40200001",
        family_id="F1",
        peso_unitario_ton=1.5
    )
    
    with db.connect() as con:
        con.execute("""
            INSERT INTO core_sap_vision_snapshot(
                pedido, posicion, cod_material, fecha_de_pedido,
                solicitado, bodega, despachado, peso_unitario_ton
            ) VALUES (
                'PED1', '10', '40200001', '2025-01-01',
                10, 0, 0, 1.5
            )
        """)
        con.execute("""
            INSERT INTO core_sap_vision_snapshot(
                pedido, posicion, cod_material, fecha_de_pedido,
                solicitado, bodega, despachado, peso_unitario_ton
            ) VALUES (
                'PED2', '10', '40200002', '2025-02-01',
                5, 0, 0, 2.0
            )
        """)
        
    # Run KPI upsert for a date AFTER PED1 delivery but BEFORE PED2 delivery
    # PED1 delivery: Jan 10. PED2 delivery: Feb 20.
    # Snapshot date: Jan 30.
    # PED1 is overdue (Jan 10 < Jan 30).
    # PED2 is NOT overdue (Feb 20 > Jan 30).
    
    # Expected: 
    # Total Pending: (10 units * 1.5) + (5 units * 2.0) = 15 + 10 = 25 tons.
    # Overdue: (10 units * 1.5) = 15 tons.
    
    # Note: Query uses fecha_de_pedido as the date base.
    
    try:
        result = repo.upsert_vision_kpi_daily(snapshot_date=date(2025, 1, 30))
    except sqlite3.OperationalError as e:
        pytest.fail(f"SQL Error: {e}")
        
    assert result["snapshot_date"] == "2025-01-30"
    
    # Verify values - this might fail if the logic is buggy (as suspected)
    # If the logic uses fecha_de_pedido, both might be considered 'past' or neither depending on logic.
    # Let's see what happens.
