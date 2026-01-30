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
    repo.import_sap_mb52_bytes(content=mb52_bytes, mode="replace")
    
    # Verify job was created
    with db.connect() as con:
        jobs = con.execute(
            "SELECT job_id, process_id, pedido, posicion, material, qty_total, priority, is_test, state FROM job"
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
    assert term_job["qty_total"] == 2  # 2 lotes
    assert term_job["priority"] == 3  # normal priority (default)
    assert term_job["is_test"] == 0  # numeric lote
    assert term_job["state"] == "pending"
    
    # Verify job_units were created
    with db.connect() as con:
        job_units = con.execute(
            "SELECT job_unit_id, job_id, lote, correlativo_int, qty, status FROM job_unit WHERE job_id = ?",
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
    repo.import_sap_mb52_bytes(content=mb52_bytes, mode="replace")
    
    with db.connect() as con:
        jobs = con.execute(
            "SELECT priority, is_test FROM job WHERE process_id = 'terminaciones'"
        ).fetchall()
    
    assert len(jobs) >= 1
    term_job = jobs[0]
    assert term_job["is_test"] == 1, "Should mark as test"
    assert term_job["priority"] == 1, "Should use 'prueba' priority (1)"


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
    repo.import_sap_mb52_bytes(content=mb52_bytes, mode="replace")
    
    with db.connect() as con:
        jobs = con.execute(
            "SELECT process_id, qty_total FROM job ORDER BY process_id"
        ).fetchall()
    
    processes = {j["process_id"] for j in jobs}
    assert "terminaciones" in processes, "Should create terminaciones job"
    assert "mecanizado" in processes, "Should create mecanizado job"
    
    # Each process should have 1 piece
    for j in jobs:
        assert j["qty_total"] == 1


def test_update_jobs_from_vision(temp_db):
    """Test that jobs are updated with fecha_entrega from Vision."""
    db, _ = temp_db
    repo = Repository(db)
    
    # First import MB52
    mb52_rows = [
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
        {
            "material": "43633021531",
            "centro": "4000",
            "almacen": "4035",
            "lote": "001-002",
            "libre_utilizacion": 1,
            "documento_comercial": "1010044531",
            "posicion_sd": "10",
            "en_control_calidad": 0,
        },
    ]
    
    mb52_bytes = create_mock_mb52_excel(mb52_rows)
    repo.import_sap_mb52_bytes(content=mb52_bytes, mode="replace")
    
    # Create Vision Excel
    vision_wb = openpyxl.Workbook()
    vision_ws = vision_wb.active
    
    vision_headers = [
        "Pedido", "Pos.", "Cod. Material", "Descripción Material",
        "Fecha de pedido", "Fecha Entrega", "Solicitado",
        "Terminación"
    ]
    vision_ws.append(vision_headers)
    vision_ws.append([
        "1010044531",  # Pedido
        "10",  # Pos
        "43633021531",  # Cod Material
        "TEST PART",  # Descripción
        "2026-02-15",  # Fecha de pedido
        "2026-03-01",  # Fecha Entrega
        "100",  # Solicitado
        "50",  # Terminación (qty_completed)
    ])
    
    vision_buffer = io.BytesIO()
    vision_wb.save(vision_buffer)
    vision_buffer.seek(0)
    vision_bytes = vision_buffer.read()
    
    # Import Vision
    repo.import_sap_vision_bytes(content=vision_bytes)
    
    # Verify job was updated with fecha_entrega only
    with db.connect() as con:
        job = con.execute(
            """
            SELECT fecha_entrega, qty_total
            FROM job
            WHERE process_id = 'terminaciones'
              AND pedido = '1010044531'
              AND posicion = '10'
            """
        ).fetchone()
    
    assert job is not None
    assert job["fecha_entrega"] == "2026-03-01", "Should update fecha_entrega from Vision"
    assert job["qty_total"] == 2, "qty_total should remain as lote count from MB52"
