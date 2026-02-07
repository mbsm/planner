"""Tests for FASE 3.1: Job creation from MB52 import."""

import io
import sqlite3
import tempfile
from pathlib import Path

import openpyxl
import pytest

from foundryplan.data.db import Db
from foundryplan.data.repository import Repository


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "test.db"
    db = Db(db_path)
    db.ensure_schema()
    
    # Configure to accept all materials (not just 436*)
    from foundryplan.data.repository import Repository
    repo = Repository(db)
    repo.data.set_config(key="sap_material_prefixes", value="*")
    
    yield db, db_path
    
    # Cleanup
    try:
        for f in Path(tmpdir).glob("test.db*"):
            f.unlink(missing_ok=True)
        Path(tmpdir).rmdir()
    except Exception:
        pass


def create_mock_mb52_excel(rows: list[dict]) -> bytes:
    """Create a mock MB52 Excel file with given rows."""
    wb = openpyxl.Workbook()
    ws = wb.active
    
    # Headers (exact SAP headers before normalization)
    headers = [
        "Material", "Texto Breve de Material", "Centro", "Almacén",
        "Lote", "PB a nivel de almacén", "Libre utilización",
        "Documento Comercial", "Posición (SD)", "En control calidad"  # Changed: removed "de"
    ]
    ws.append(headers)
    
    # Data rows
    for row in rows:
        ws.append([
            row.get("material", ""),
            row.get("texto_breve", ""),
            row.get("centro", "4000"),
            row.get("almacen", "4035"),
            row.get("lote", ""),
            row.get("pb_almacen", 0),
            row.get("libre_utilizacion", 1),
            row.get("documento_comercial", ""),
            row.get("posicion_sd", ""),
            row.get("en_control_calidad", 0),
        ])
    
    # Save to bytes
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.read()


def test_create_jobs_from_mb52_basic(temp_db):
    """Test that jobs are created automatically when importing MB52."""
    db, _ = temp_db
    repo = Repository(db)
    
    # Create MB52 with test data
    mb52_rows = [
        {
            "material": "43633021531",
            "texto_breve": "TEST PART",
            "centro": "4000",
            "almacen": "4035",  # Terminaciones
            "lote": "001-001",
            "pb_almacen": 1.5,
            "libre_utilizacion": 1,
            "documento_comercial": "1010044531",
            "posicion_sd": "10",
            "en_control_calidad": 0,
        },
        {
            "material": "43633021531",
            "texto_breve": "TEST PART",
            "centro": "4000",
            "almacen": "4035",
            "lote": "001-002",
            "pb_almacen": 1.5,
            "libre_utilizacion": 1,
            "documento_comercial": "1010044531",
            "posicion_sd": "10",
            "en_control_calidad": 0,
        },
    ]
    
    mb52_bytes = create_mock_mb52_excel(mb52_rows)
    
    # Import MB52
    repo.data.import_sap_mb52_bytes(content=mb52_bytes, mode="replace")
    
    # Verify job was created
    with db.connect() as con:
        jobs = con.execute(
            "SELECT job_id, process_id, pedido, posicion, material, qty, priority, is_test, state FROM dispatcher_job"
        ).fetchall()
    
    assert len(jobs) >= 1, "Should create at least one job (terminaciones)"
    
    # Find terminaciones job
    term_job = None
    for j in jobs:
        if j["process_id"] == "terminaciones":
            term_job = j
            break
    
    assert term_job is not None, "Should create terminaciones job"
    assert term_job["pedido"] == "1010044531"
    assert term_job["posicion"] == "10"
    assert term_job["material"] == "43633021531"
    assert term_job["qty"] == 2  # 2 lotes
    assert term_job["priority"] == 3  # normal priority (default)
    assert term_job["is_test"] == 0  # numeric lote
    assert term_job["state"] == "pending"
    
    # Verify job_units were created
    with db.connect() as con:
        job_units = con.execute(
            "SELECT job_unit_id, job_id, lote, correlativo_int, qty, status FROM dispatcher_job_unit WHERE job_id = ?",
            (term_job["job_id"],),
        ).fetchall()
    
    assert len(job_units) == 2, "Should create 2 job_units (one per lote)"
    
    lotes = {ju["lote"] for ju in job_units}
    assert lotes == {"001-001", "001-002"}
    
    for ju in job_units:
        assert ju["qty"] == 1
        assert ju["status"] == "available"
        assert ju["correlativo_int"] == 1  # First numeric group


def test_create_jobs_test_priority(temp_db):
    """Test that jobs with alphanumeric lotes get 'prueba' priority."""
    db, _ = temp_db
    repo = Repository(db)
    
    mb52_rows = [
        {
            "material": "43633021531",
            "centro": "4000",
            "almacen": "4035",
            "lote": "0030PD0674",  # Alphanumeric = test
            "libre_utilizacion": 1,
            "documento_comercial": "1010044531",
            "posicion_sd": "10",
            "en_control_calidad": 0,
        },
    ]
    
    mb52_bytes = create_mock_mb52_excel(mb52_rows)
    repo.data.import_sap_mb52_bytes(content=mb52_bytes, mode="replace")
    
    with db.connect() as con:
        jobs = con.execute(
            "SELECT priority, is_test FROM dispatcher_job WHERE process_id = 'terminaciones'"
        ).fetchall()
    
    assert len(jobs) >= 1
    term_job = jobs[0]
    assert term_job["is_test"] == 1, "Should mark as test"
    assert term_job["priority"] == 1, "Should use 'prueba' priority (1)"


def test_create_jobs_auto_split_test_and_normal_lotes(temp_db):
    """If MB52 has mixed lotes (test + normal) for same key, split into separate jobs."""
    db, _ = temp_db
    repo = Repository(db)

    mb52_rows = [
        {
            "material": "43633021531",
            "centro": "4000",
            "almacen": "4035",
            "lote": "0030PD0674",  # Alphanumeric = test
            "libre_utilizacion": 1,
            "documento_comercial": "1010044531",
            "posicion_sd": "10",
            "en_control_calidad": 0,
        },
        {
            "material": "43633021531",
            "centro": "4000",
            "almacen": "4035",
            "lote": "001-001",  # Numeric = normal
            "libre_utilizacion": 1,
            "documento_comercial": "1010044531",
            "posicion_sd": "10",
            "en_control_calidad": 0,
        },
    ]

    mb52_bytes = create_mock_mb52_excel(mb52_rows)
    repo.data.import_sap_mb52_bytes(content=mb52_bytes, mode="replace")

    with db.connect() as con:
        jobs = con.execute(
            "SELECT job_id, is_test, qty, priority FROM dispatcher_job WHERE process_id='terminaciones' AND pedido='1010044531' AND posicion='10' AND material='43633021531' ORDER BY is_test ASC"
        ).fetchall()

    assert len(jobs) == 2, "Should create 2 jobs: normal + test"

    normal_job = jobs[0]
    test_job = jobs[1]

    assert normal_job["is_test"] == 0
    assert normal_job["qty"] == 1
    assert normal_job["priority"] == 3

    assert test_job["is_test"] == 1
    assert test_job["qty"] == 1
    assert test_job["priority"] == 1

    with db.connect() as con:
        normal_units = con.execute(
            "SELECT lote FROM dispatcher_job_unit WHERE job_id = ? ORDER BY lote",
            (normal_job["job_id"],),
        ).fetchall()
        test_units = con.execute(
            "SELECT lote FROM dispatcher_job_unit WHERE job_id = ? ORDER BY lote",
            (test_job["job_id"],),
        ).fetchall()

    assert [r[0] for r in normal_units] == ["001-001"]
    assert [r[0] for r in test_units] == ["0030PD0674"]


def test_create_jobs_multiple_processes(temp_db):
    """Test that jobs are created for ALL active processes with stock."""
    db, _ = temp_db
    repo = Repository(db)
    
    mb52_rows = [
        # Stock in Terminaciones (4035)
        {
            "material": "43633021531",
            "centro": "4000",
            "almacen": "4035",
            "lote": "001-001",
            "libre_utilizacion": 1,
            "documento_comercial": "1010044531",
            "posicion_sd": "10",
            "en_control_calidad": 0,
        },
        # Stock in Mecanizado (4049)
        {
            "material": "43633021531",
            "centro": "4000",
            "almacen": "4049",
            "lote": "002-001",
            "libre_utilizacion": 1,
            "documento_comercial": "1010044531",
            "posicion_sd": "10",
            "en_control_calidad": 0,
        },
    ]
    
    mb52_bytes = create_mock_mb52_excel(mb52_rows)
    repo.data.import_sap_mb52_bytes(content=mb52_bytes, mode="replace")
    
    with db.connect() as con:
        jobs = con.execute(
            "SELECT process_id, qty FROM dispatcher_job ORDER BY process_id"
        ).fetchall()
    
    processes = {j["process_id"] for j in jobs}
    assert "terminaciones" in processes, "Should create terminaciones job"
    assert "mecanizado" in processes, "Should create mecanizado job"
    
    # Each process should have 1 piece
    for j in jobs:
        assert j["qty"] == 1


def test_update_jobs_from_vision(temp_db):
    """Test that jobs are updated with fecha_de_pedido from Vision using real data fixtures."""
    db, _ = temp_db
    repo = Repository(db)
    
   # Import real data fixture from sample_data
    from fixtures_real_data import FIXTURE_MB52_MULTI_REAL, FIXTURE_VISION_MULTI_REAL
    
    # First import MB52 using real data
    mb52_bytes = create_mock_mb52_excel(FIXTURE_MB52_MULTI_REAL)
    repo.data.import_sap_mb52_bytes(content=mb52_bytes, mode="replace")
    
    # Verify jobs were created from MB52 (almacen 4046 = inspeccion_externa)
    with db.connect() as con:
        job_before = con.execute(
            """
            SELECT pedido, posicion, qty, fecha_de_pedido
            FROM dispatcher_job
            WHERE process_id = 'inspeccion_externa'
              AND pedido = ?
              AND posicion = ?
            """,
            (FIXTURE_VISION_MULTI_REAL['pedido'], FIXTURE_VISION_MULTI_REAL['posicion'])
        ).fetchone()
    
    assert job_before is not None, "Job should be created from MB52"
    assert job_before["qty"] == 3, "Should have 3 lotes from MB52"
    assert job_before["fecha_de_pedido"] is None, "fecha_de_pedido should be NULL before Vision import"
    
    # Create Vision Excel with real data
    vision_wb = openpyxl.Workbook()
    vision_ws = vision_wb.active
    
    vision_headers = [
        "Pedido", "Pos.", "Cod. Material", "Descripción Material",
        "Fecha de pedido", "Fecha Entrega", "Solicitado",
        "Status Comercial"
    ]
    vision_ws.append(vision_headers)
    vision_ws.append([
        FIXTURE_VISION_MULTI_REAL['pedido'],
        FIXTURE_VISION_MULTI_REAL['posicion'],
        FIXTURE_VISION_MULTI_REAL['cod_material'],
        FIXTURE_VISION_MULTI_REAL['descripcion_material'],
        FIXTURE_VISION_MULTI_REAL['fecha_de_pedido'],
        "2026-03-01",  # Fecha Entrega
        FIXTURE_VISION_MULTI_REAL['solicitado'],
        "1",  # Status Comercial (active)
    ])
    
    vision_buffer = io.BytesIO()
    vision_wb.save(vision_buffer)
    vision_buffer.seek(0)
    vision_bytes = vision_buffer.read()
    
    # Import Vision
    repo.data.import_sap_vision_bytes(content=vision_bytes)
    
    # Verify job was updated with fecha_de_pedido
    with db.connect() as con:
        job_after = con.execute(
            """
            SELECT fecha_de_pedido, qty
            FROM dispatcher_job
            WHERE process_id = 'inspeccion_externa'
              AND pedido = ?
              AND posicion = ?
            """,
            (FIXTURE_VISION_MULTI_REAL['pedido'], FIXTURE_VISION_MULTI_REAL['posicion'])
        ).fetchone()
    
    assert job_after is not None
    assert job_after["fecha_de_pedido"] == FIXTURE_VISION_MULTI_REAL['fecha_de_pedido'], \
        "Should update fecha_de_pedido from Vision"
    assert job_after["qty"] == 3, "qty should remain as lote count from MB52"



def test_split_job_basic(temp_db):
    """Test basic job split functionality."""
    db, _ = temp_db
    repo = Repository(db)
    
    # Create a job with 10 lotes
    mb52_rows = [
        {
            "material": "K106321",
            "centro": "4000",
            "almacen": "4035",
            "lote": f"001-{i:03d}",
            "libre_utilizacion": 1,
            "documento_comercial": "0030517821",
            "posicion_sd": "000010",
            "en_control_calidad": 0,
        }
        for i in range(1, 11)  # 10 lotes
    ]
    
    mb52_bytes = create_mock_mb52_excel(mb52_rows)
    repo.data.import_sap_mb52_bytes(content=mb52_bytes, mode="replace")
    
    # Get the created job
    with db.connect() as con:
        original_job = con.execute(
            """
            SELECT job_id, qty
            FROM dispatcher_job
            WHERE process_id = 'terminaciones'
              AND pedido = '30517821'
              AND posicion = '10'
            """
        ).fetchone()
    
    assert original_job is not None
    assert original_job["qty"] == 10
    original_job_id = original_job["job_id"]
    
    # Split: 4 lotes in first job, 6 in second
    job1_id, job2_id = repo.dispatcher.split_job(job_id=original_job_id, qty_split=4)
    
    assert job1_id == original_job_id, "Original job ID should be preserved"
    assert job2_id != job1_id, "New job should have different ID"
    
    # Validate both jobs
    with db.connect() as con:
        jobs = con.execute(
            """
            SELECT job_id, process_id, pedido, posicion, material, qty, priority, is_test
            FROM dispatcher_job
            WHERE process_id = 'terminaciones'
              AND pedido = '30517821'
              AND posicion = '10'
            ORDER BY job_id
            """
        ).fetchall()
    
    assert len(jobs) == 2, "Should have 2 jobs after split"
    
    # Find job1 and job2
    job1 = next(j for j in jobs if j["job_id"] == job1_id)
    job2 = next(j for j in jobs if j["job_id"] == job2_id)
    
    # Validate quantities
    assert job1["qty"] == 4, "First job should have 4 lotes"
    assert job2["qty"] == 6, "Second job should have 6 lotes"
    
    # Validate inherited fields
    assert job1["process_id"] == job2["process_id"] == "terminaciones"
    assert job1["pedido"] == job2["pedido"] == "30517821"
    assert job1["posicion"] == job2["posicion"] == "10"
    assert job1["material"] == job2["material"] == "K106321"
    assert job1["priority"] == job2["priority"], "Both should have same priority"
    assert job1["is_test"] == job2["is_test"], "Both should have same is_test"
    
    # Validate job_units distribution
    with db.connect() as con:
        units1 = con.execute(
            "SELECT lote FROM dispatcher_job_unit WHERE job_id = ? ORDER BY lote",
            (job1_id,),
        ).fetchall()
        units2 = con.execute(
            "SELECT lote FROM dispatcher_job_unit WHERE job_id = ? ORDER BY lote",
            (job2_id,),
        ).fetchall()
    
    assert len(units1) == 4, "First job should have 4 job_units"
    assert len(units2) == 6, "Second job should have 6 job_units"
    
    # Validate lotes are correctly distributed (first 4 stay, next 6 move)
    expected_lotes1 = [f"001-{i:03d}" for i in range(1, 5)]
    expected_lotes2 = [f"001-{i:03d}" for i in range(5, 11)]
    
    actual_lotes1 = [u["lote"] for u in units1]
    actual_lotes2 = [u["lote"] for u in units2]
    
    assert actual_lotes1 == expected_lotes1, "First 4 lotes should stay in job1"
    assert actual_lotes2 == expected_lotes2, "Next 6 lotes should move to job2"


def test_split_job_validation_errors(temp_db):
    """Test split_job validation errors."""
    db, _ = temp_db
    repo = Repository(db)
    
    # Create a job with 5 lotes
    mb52_rows = [
        {
            "material": "K106321",
            "centro": "4000",
            "almacen": "4035",
            "lote": f"001-{i:03d}",
            "libre_utilizacion": 1,
            "documento_comercial": "0030517821",
            "posicion_sd": "000010",
            "en_control_calidad": 0,
        }
        for i in range(1, 6)  # 5 lotes
    ]
    
    mb52_bytes = create_mock_mb52_excel(mb52_rows)
    repo.data.import_sap_mb52_bytes(content=mb52_bytes, mode="replace")
    
    with db.connect() as con:
        job = con.execute(
            "SELECT job_id FROM dispatcher_job WHERE pedido = '30517821'"
        ).fetchone()
    
    job_id = job["job_id"]
    
    # Test: qty_split must be positive
    with pytest.raises(ValueError, match="qty_split must be positive"):
        repo.dispatcher.split_job(job_id=job_id, qty_split=0)
    
    with pytest.raises(ValueError, match="qty_split must be positive"):
        repo.dispatcher.split_job(job_id=job_id, qty_split=-1)
    
    # Test: qty_split must be less than qty
    with pytest.raises(ValueError, match="must be less than job qty"):
        repo.dispatcher.split_job(job_id=job_id, qty_split=5)
    
    with pytest.raises(ValueError, match="must be less than job qty"):
        repo.dispatcher.split_job(job_id=job_id, qty_split=10)
    
    # Test: job not found
    with pytest.raises(ValueError, match="Job .* not found"):
        repo.dispatcher.split_job(job_id="nonexistent_job_id", qty_split=2)


def test_split_distribution_new_stock(temp_db):
    """Test that new stock is distributed to split with lowest qty."""
    db, _ = temp_db
    repo = Repository(db)
    
    # Step 1: Create initial job with 10 lotes
    mb52_rows_initial = [
        {
            "material": "K106321",
            "centro": "4000",
            "almacen": "4035",
            "lote": f"batch1-{i:03d}",
            "libre_utilizacion": 1,
            "documento_comercial": "0030517821",
            "posicion_sd": "000010",
            "en_control_calidad": 0,
        }
        for i in range(1, 11)
    ]
    
    mb52_bytes = create_mock_mb52_excel(mb52_rows_initial)
    repo.data.import_sap_mb52_bytes(content=mb52_bytes, mode="replace")
    
    # Step 2: Get job and split it (4 + 6)
    with db.connect() as con:
        original = con.execute(
            "SELECT job_id FROM dispatcher_job WHERE pedido = '30517821'"
        ).fetchone()
    
    job1_id, job2_id = repo.dispatcher.split_job(job_id=original["job_id"], qty_split=4)
    
    # Step 3: Simulate MB52 update with new stock (5 new lotes)
    # This should go to job1 since it has qty=4 (less than job2's qty=6)
    mb52_rows_new = [
        {
            "material": "K106321",
            "centro": "4000",
            "almacen": "4035",
            "lote": f"batch2-{i:03d}",
            "libre_utilizacion": 1,
            "documento_comercial": "0030517821",
            "posicion_sd": "000010",
            "en_control_calidad": 0,
        }
        for i in range(1, 6)  # 5 new lotes
    ]
    
    mb52_bytes_new = create_mock_mb52_excel(mb52_rows_new)
    repo.data.import_sap_mb52_bytes(content=mb52_bytes_new, mode="replace")
    
    # Step 4: Verify new stock went to job1 (had lowest qty)
    # Job 2 received no stock (0), so it should be deleted by cleanup logic.
    with db.connect() as con:
        job1 = con.execute(
            "SELECT qty FROM dispatcher_job WHERE job_id = ?",
            (job1_id,),
        ).fetchone()
        job2 = con.execute(
            "SELECT qty FROM dispatcher_job WHERE job_id = ?",
            (job2_id,),
        ).fetchone()
    
    assert job1 is not None
    assert job1["qty"] == 5, "job1 should receive the 5 new lotes (was lowest)"
    assert job2 is None, "job2 should be deleted because qty dropped to 0"


def test_split_distribution_all_zero_creates_new_job(temp_db):
    """Test that when all splits are at qty=0, they are deleted, and new stock creates a new single job."""
    db, _ = temp_db
    repo = Repository(db)
    
    # Step 1: Create initial job with 8 lotes
    mb52_rows_initial = [
        {
            "material": "K106321",
            "centro": "4000",
            "almacen": "4035",
            "lote": f"batch1-{i:03d}",
            "libre_utilizacion": 1,
            "documento_comercial": "0030517821",
            "posicion_sd": "000010",
            "en_control_calidad": 0,
        }
        for i in range(1, 9)
    ]
    
    mb52_bytes = create_mock_mb52_excel(mb52_rows_initial)
    repo.data.import_sap_mb52_bytes(content=mb52_bytes, mode="replace")
    
    # Step 2: Split job (3 + 5)
    with db.connect() as con:
        original = con.execute(
            "SELECT job_id FROM dispatcher_job WHERE pedido = '30517821'"
        ).fetchone()
    
    job1_id, job2_id = repo.dispatcher.split_job(job_id=original["job_id"], qty_split=3)
    
    # Step 3: Simulate MB52 update with NO stock (all lotes disappear)
    mb52_bytes_empty = create_mock_mb52_excel([])
    repo.data.import_sap_mb52_bytes(content=mb52_bytes_empty, mode="replace")
    
    # Verify both splits were deleted because qty dropped to 0
    with db.connect() as con:
        jobs = con.execute(
            """
            SELECT job_id, qty
            FROM dispatcher_job
            WHERE process_id = 'terminaciones'
              AND pedido = '30517821'
              AND posicion = '10'
            ORDER BY job_id
            """
        ).fetchall()
    
    assert len(jobs) == 0, "Both splits should be deleted (cleanup logic)"
    
    # Step 4: Simulate MB52 update with new stock (should create NEW job)
    mb52_rows_new = [
        {
            "material": "K106321",
            "centro": "4000",
            "almacen": "4035",
            "lote": f"batch2-{i:03d}",
            "libre_utilizacion": 1,
            "documento_comercial": "0030517821",
            "posicion_sd": "000010",
            "en_control_calidad": 0,
        }
        for i in range(1, 4)  # 3 new lotes
    ]
    
    mb52_bytes_new = create_mock_mb52_excel(mb52_rows_new)
    repo.data.import_sap_mb52_bytes(content=mb52_bytes_new, mode="replace")
    
    # Step 5: Verify a NEW job was created (1 total job now)
    with db.connect() as con:
        jobs = con.execute(
            """
            SELECT job_id, qty
            FROM dispatcher_job
            WHERE process_id = 'terminaciones'
              AND pedido = '30517821'
              AND posicion = '10'
            ORDER BY created_at
            """
        ).fetchall()
    
    assert len(jobs) == 1, "Should have 1 job: old ones deleted, 1 new created"
    
    # Only 1 job
    assert jobs[0]["qty"] == 3, "New job should have the 3 new lotes"
    assert jobs[0]["job_id"] not in [job1_id, job2_id], "Should be a different job_id"

