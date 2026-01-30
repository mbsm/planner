
import pytest
import tempfile
from pathlib import Path
from foundryplan.data.db import Db
from foundryplan.data.repository import Repository
import io
import openpyxl

@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "test.db"
    db = Db(db_path)
    db.ensure_schema()
    
    # Configure to accept all materials (not just 436*)
    repo = Repository(db)
    repo.set_config(key="sap_material_prefixes", value="*")
    
    yield db, db_path
    
    # Cleanup
    try:
        for f in Path(tmpdir).glob("test.db*"):
            f.unlink(missing_ok=True)
        Path(tmpdir).rmdir()
    except Exception:
        pass

def create_mock_mb52_excel(rows: list[dict]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    headers = ["Material", "Texto Breve de Material", "Centro", "Almacén", "Lote", "PB a nivel de almacén", "Libre utilización", "Documento Comercial", "Posición (SD)", "En control calidad"]
    ws.append(headers)
    for row in rows:
        ws.append([
            row.get("material", ""), "", row.get("centro", "4000"), row.get("almacen", "4035"),
            row.get("lote", ""), 0, 1, row.get("documento_comercial", ""), row.get("posicion_sd", ""), 0
        ])
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.read()

def test_split_retention_existing_lotes(temp_db):
    """
    Verify that when updating from MB52, existing lotes stay in their assigned jobs (splits),
    and only NEW lotes are assigned to the target job.
    """
    db, _ = temp_db
    repo = Repository(db)
    
    # 1. Setup: 2 lotes (A, B)
    rows_1 = [
        {"material": "M1", "lote": "LOTE-1", "documento_comercial": "P1", "posicion_sd": "10"},
        {"material": "M1", "lote": "LOTE-2", "documento_comercial": "P1", "posicion_sd": "10"},
    ]
    repo.import_sap_mb52_bytes(content=create_mock_mb52_excel(rows_1), mode="replace")
    
    with db.connect() as con:
        job = con.execute("SELECT job_id FROM job WHERE pedido='P1'").fetchone()
        job_id = job["job_id"]
        
    # 2. Split: Job 1 has 1 qty (Lote A presumably), Job 2 has 1 qty (Lote B)
    # The split_job implementation takes `qty_split` and moves it to a NEW job.
    # If we have 2 lotes, split 1:
    job1_id, job2_id = repo.split_job(job_id=job_id, qty_split=1)
    
    # Verify split state
    with db.connect() as con:
        j1 = con.execute("SELECT qty_total FROM job WHERE job_id=?", (job1_id,)).fetchone()
        j2 = con.execute("SELECT qty_total FROM job WHERE job_id=?", (job2_id,)).fetchone()
        # Check who has what
        lotes_j1 = [r[0] for r in con.execute("SELECT lote FROM job_unit WHERE job_id=?", (job1_id,)).fetchall()]
        lotes_j2 = [r[0] for r in con.execute("SELECT lote FROM job_unit WHERE job_id=?", (job2_id,)).fetchall()]
        
    print(f"DEBUG: J1={lotes_j1}, J2={lotes_j2}")
    
    # 3. Update MB52: LOTE-A, LOTE-B, LOTE-C (New)
    # Expected: 
    # - LOTE-A stays in its job
    # - LOTE-B stays in its job
    # - LOTE-C goes to the smaller job (or one of them)
    rows_2 = [
        {"material": "M1", "lote": "LOTE-1", "documento_comercial": "P1", "posicion_sd": "10"},
        {"material": "M1", "lote": "LOTE-2", "documento_comercial": "P1", "posicion_sd": "10"},
        {"material": "M1", "lote": "LOTE-3", "documento_comercial": "P1", "posicion_sd": "10"},
    ]
    repo.import_sap_mb52_bytes(content=create_mock_mb52_excel(rows_2), mode="replace")
    
    with db.connect() as con:
        j1_new = con.execute("SELECT qty_total FROM job WHERE job_id=?", (job1_id,)).fetchone()
        j2_new = con.execute("SELECT qty_total FROM job WHERE job_id=?", (job2_id,)).fetchone()
        
    # Currently (buggy behavior expected): One job has 3, the other has 0 (and is deleted)
    # Desired behavior: One has 1 (or 2), the other has 2 (or 1), total 3. Both exist.
    
    assert j1_new is not None, "Job 1 should survive"
    assert j2_new is not None, "Job 2 should survive"
    
    qty1 = j1_new["qty_total"]
    qty2 = j2_new["qty_total"]
    
    assert qty1 + qty2 == 3, "Total should be 3"
    assert qty1 > 0 and qty2 > 0, "Both jobs should retain their lotes"

