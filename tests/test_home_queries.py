import pytest
import sqlite3
from datetime import date
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
    repo = Repository(db)
    yield db, repo
    import gc
    gc.collect()
    import shutil
    try:
        shutil.rmtree(tmpdir)
    except PermissionError:
        pass

def test_get_orders_overdue_execution(temp_db):
    db, repo = temp_db
    
    # Needs material master
    repo.upsert_part_master(
        material="40200001",
        family_id="F1",
        peso_unitario_ton=1.0
    )
    
    # Overdue order: fecha_de_pedido (which acts as delivery date) was last year
    last_year = date.today().replace(year=date.today().year - 1).isoformat()
    
    with db.connect() as con:
        con.execute("""
            INSERT INTO core_sap_vision_snapshot(
                pedido, posicion, cod_material, fecha_de_pedido,
                solicitado, bodega, despachado, peso_unitario_ton, status_comercial
            ) VALUES (
                'OD1', '10', '40200001', ?,
                10, 0, 0, 1.0, 'Activo'
            )
        """, (last_year,))
        
    try:
        rows = repo.get_orders_overdue_rows(limit=10)
    except sqlite3.OperationalError as e:
        pytest.fail(f"Overdue query failed: {e}")
        
    assert len(rows) > 0
    assert rows[0]['pedido'] == 'OD1'


def test_get_orders_due_soon_execution(temp_db):
    db, repo = temp_db
    
    repo.upsert_part_master(
        material="40200002",
        family_id="F1",
        peso_unitario_ton=1.0
    )
    
    # Due soon order: fecha_de_pedido is tomorrow
    import datetime
    tomorrow = (date.today() + datetime.timedelta(days=1)).isoformat()
    
    with db.connect() as con:
        con.execute("""
            INSERT INTO core_sap_vision_snapshot(
                pedido, posicion, cod_material, fecha_de_pedido,
                solicitado, bodega, despachado, peso_unitario_ton, status_comercial
            ) VALUES (
                'DS1', '10', '40200002', ?,
                10, 0, 0, 1.0, 'Activo'
            )
        """, (tomorrow,))
        
    try:
        rows = repo.get_orders_due_soon_rows(days=7, limit=10)
    except sqlite3.OperationalError as e:
        pytest.fail(f"Due soon query failed: {e}")
        
    assert len(rows) > 0
    assert rows[0]['pedido'] == 'DS1'
