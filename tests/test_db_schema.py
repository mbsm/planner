"""Tests for database schema and migrations."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from foundryplan.data.db import Db
from foundryplan.data.repository import Repository


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "test.db"
    db = Db(db_path)
    yield db, db_path
    
    # Cleanup: close all connections and remove files
    try:
        for f in Path(tmpdir).glob("test.db*"):
            f.unlink(missing_ok=True)
        Path(tmpdir).rmdir()
    except Exception:
        pass  # Ignore cleanup errors


def test_ensure_schema_creates_all_tables(temp_db):
    """Test that ensure_schema() creates all required FASE 1 tables."""
    db, db_path = temp_db
    db.ensure_schema()

    with db.connect() as con:
        cursor = con.cursor()
        
        # Get all table names
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = {row[0] for row in cursor.fetchall()}

    # FASE 1.1: Config tables
    assert "family_catalog" in tables
    assert "app_config" in tables
    assert "material_master" in tables
    assert "process" in tables
    assert "resource" in tables
    assert "resource_constraint" in tables
    assert "process_attribute_def" in tables

    # FASE 1.2: SAP tables
    assert "sap_mb52_snapshot" in tables
    assert "sap_vision_snapshot" in tables
    
    # FASE 1.3: Job tables
    assert "job" in tables
    assert "job_unit" in tables

    # FASE 1.4: Dispatch tables
    assert "dispatch_queue_run" in tables
    assert "dispatch_queue_item" in tables
    assert "last_dispatch" in tables
    assert "dispatch_in_progress" in tables
    assert "dispatch_in_progress_item" in tables

    # FASE 1.5-1.6: State & KPI tables
    assert "vision_kpi_daily" in tables
    assert "program_in_progress" in tables
    assert "program_in_progress_item" in tables


def test_material_master_structure(temp_db):
    """Test that material_master has correct columns."""
    db, db_path = temp_db
    db.ensure_schema()

    with db.connect() as con:
        cursor = con.cursor()
        cursor.execute("PRAGMA table_info(material_master)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}  # name: type

    expected_columns = {
        "material": "TEXT",
        "family_id": "TEXT",
        "aleacion": "TEXT",
        "piezas_por_molde": "REAL",
        "peso_bruto_ton": "REAL",
        "tiempo_enfriamiento_molde_dias": "INTEGER",
        "vulcanizado_dias": "INTEGER",
        "mecanizado_dias": "INTEGER",
        "inspeccion_externa_dias": "INTEGER",
        "peso_unitario_ton": "REAL",
        "mec_perf_inclinada": "INTEGER",
        "sobre_medida_mecanizado": "INTEGER",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    }

    for col_name, col_type in expected_columns.items():
        assert col_name in columns, f"Column {col_name} missing from material_master"
        assert columns[col_name] == col_type, f"Column {col_name} has type {columns[col_name]}, expected {col_type}"


def test_job_structure(temp_db):
    """Test that job table has correct columns."""
    db, db_path = temp_db
    db.ensure_schema()

    with db.connect() as con:
        cursor = con.cursor()
        cursor.execute("PRAGMA table_info(job)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}

    expected_columns = {
        "job_id": "TEXT",
        "process_id": "TEXT",
        "pedido": "TEXT",
        "posicion": "TEXT",
        "numero_parte": "TEXT",
        "qty_total": "INTEGER",
        "qty_completed": "INTEGER",
        "qty_remaining": "INTEGER",
        "priority": "INTEGER",
        "is_test": "INTEGER",
        "state": "TEXT",
        "created_at": "TEXT",
        "updated_at": "TEXT",
    }

    for col_name in expected_columns:
        assert col_name in columns, f"Column {col_name} missing from job table"


def test_seeds_family_catalog(temp_db):
    """Test that family_catalog is seeded with default families."""
    db, db_path = temp_db
    db.ensure_schema()

    with db.connect() as con:
        cursor = con.cursor()
        cursor.execute("SELECT family_id FROM family_catalog ORDER BY family_id")
        families = {row[0] for row in cursor.fetchall()}

    expected_families = {"Parrillas", "Lifters", "Corazas", "Otros", "No pieza"}
    assert families == expected_families


def test_seeds_app_config(temp_db):
    """Test that app_config is seeded with default values."""
    db, db_path = temp_db
    db.ensure_schema()

    with db.connect() as con:
        cursor = con.cursor()
        cursor.execute("SELECT config_key, config_value FROM app_config WHERE config_key IN ('sap_center', 'sap_material_prefixes')")
        config = {row[0]: row[1] for row in cursor.fetchall()}

    assert config.get("sap_center") == "4000"
    assert config.get("sap_material_prefixes") == "436"


def test_seeds_process(temp_db):
    """Test that process table is seeded with default processes."""
    db, db_path = temp_db
    db.ensure_schema()

    with db.connect() as con:
        cursor = con.cursor()
        cursor.execute("SELECT process_id FROM process WHERE is_active = 1 ORDER BY process_id")
        processes = {row[0] for row in cursor.fetchall()}

    expected_processes = {
        "moldeo",
        "terminaciones",
        "mecanizado",
        "mecanizado_externo",
        "inspeccion_externa",
        "vulcanizado",
        "toma_dureza",
    }
    assert processes == expected_processes


def test_migration_parts_to_material_master():
    """Test that parts table data is migrated to material_master."""
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "test_legacy.db"
    
    # Create legacy database with parts table (pre-v0.2)
    with sqlite3.connect(db_path) as con:
        con.execute(
            """CREATE TABLE parts (
                numero_parte TEXT PRIMARY KEY,
                familia TEXT NOT NULL
            )"""
        )
        con.execute("INSERT INTO parts VALUES('436001', 'Parrillas')")
        con.execute("INSERT INTO parts VALUES('436002', 'Lifters')")
        con.commit()

    # Now run migrations with Db class
    db = Db(db_path)
    db.ensure_schema()

    # Verify data was migrated
    with db.connect() as con:
        cursor = con.cursor()
        cursor.execute("SELECT material, family_id FROM material_master WHERE material IN ('436001', '436002')")
        rows = cursor.fetchall()

    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}: {rows}"
    materials = {row[0]: row[1] for row in rows}
    assert materials.get("436001") == "Parrillas"
    assert materials.get("436002") == "Lifters"
    
    # Cleanup
    try:
        for f in Path(tmpdir).glob("test_legacy.db*"):
            f.unlink(missing_ok=True)
        Path(tmpdir).rmdir()
    except Exception:
        pass


def test_auto_test_detection_in_rebuild_orders(temp_db):
    """Test FASE 2.3: Auto-detection of test lotes and orderpos_priority creation."""
    db, db_path = temp_db
    db.ensure_schema()
    repo = Repository(db)
    
    # Setup: Create minimal MB52 and Vision data in BOTH legacy and v0.2 tables
    with db.connect() as con:
        # Create legacy tables (not created by ensure_schema)
        con.execute("""
            CREATE TABLE IF NOT EXISTS sap_mb52 (
                centro TEXT,
                almacen TEXT,
                material TEXT,
                lote TEXT,
                documento_comercial TEXT,
                posicion_sd TEXT,
                libre_utilizacion INTEGER,
                en_control_calidad INTEGER
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS sap_vision (
                pedido TEXT,
                posicion TEXT,
                fecha_pedido TEXT,
                cod_material TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                process TEXT,
                almacen TEXT,
                pedido TEXT,
                posicion TEXT,
                numero_parte TEXT,
                cantidad INTEGER,
                fecha_entrega TEXT,
                primer_correlativo INTEGER,
                ultimo_correlativo INTEGER,
                tiempo_proceso_min INTEGER,
                is_test INTEGER DEFAULT 0
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS orderpos_priority (
                pedido TEXT,
                posicion TEXT,
                is_priority INTEGER DEFAULT 1,
                kind TEXT,
                PRIMARY KEY (pedido, posicion)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS last_program (
                process TEXT PRIMARY KEY,
                data TEXT
            )
        """)
        
        # Update config values for terminaciones process (seeds may have created defaults)
        con.executemany("""
            INSERT INTO app_config (config_key, config_value)
            VALUES (?, ?)
            ON CONFLICT(config_key) DO UPDATE SET config_value=excluded.config_value
        """, [
            ("sap_centro", "4000"),
            ("sap_almacen_terminaciones", "4022"),
        ])
        
        # Insert MB52 rows with numeric and alphanumeric lotes
        con.executemany("""
            INSERT INTO sap_mb52 (
                centro, almacen, material, lote,
                documento_comercial, posicion_sd,
                libre_utilizacion, en_control_calidad
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            ("4000", "4022", "436001", "12345", "5000001", "10", 1, 0),  # numeric lote (normal)
            ("4000", "4022", "436001", "12346", "5000001", "10", 1, 0),  # numeric lote (normal)
            ("4000", "4022", "436002", "ABC123", "5000002", "20", 1, 0),  # alphanumeric lote (test)
            ("4000", "4022", "436002", "ABC124", "5000002", "20", 1, 0),  # alphanumeric lote (test)
        ])
        
        # Insert Vision rows
        con.executemany("""
            INSERT INTO sap_vision (pedido, posicion, fecha_pedido, cod_material)
            VALUES (?, ?, ?, ?)
        """, [
            ("5000001", "10", "2024-03-15", "436001"),
            ("5000002", "20", "2024-03-20", "436002"),
        ])
        
    # Execute: rebuild orders
    count = repo.rebuild_orders_from_sap_for(process="terminaciones")
    
    # Assert: 2 orders created (1 per pedido/posicion)
    assert count == 2
    
    # Verify: orders table has is_test flag set correctly
    with db.connect() as con:
        cursor = con.cursor()
        cursor.execute("""
            SELECT pedido, posicion, numero_parte, cantidad, is_test
            FROM orders
            WHERE process = 'terminaciones'
            ORDER BY pedido, posicion
        """)
        orders = [tuple(row) for row in cursor.fetchall()]
        
    assert len(orders) == 2
    # Order 1: 5000001/10 with numeric lotes → is_test=0
    assert orders[0] == ("5000001", "10", "436001", 2, 0)
    # Order 2: 5000002/20 with alphanumeric lotes → is_test=1
    assert orders[1] == ("5000002", "20", "436002", 2, 1)
    
    # Verify: orderpos_priority created for test order
    with db.connect() as con:
        cursor = con.cursor()
        cursor.execute("""
            SELECT pedido, posicion, is_priority, kind
            FROM orderpos_priority
            ORDER BY pedido, posicion
        """)
        priorities = [tuple(row) for row in cursor.fetchall()]
        
    assert len(priorities) == 1
    assert priorities[0] == ("5000002", "20", 1, "test")
    
    # Verify: delete_all_pedido_priorities keeps tests by default
    repo.delete_all_pedido_priorities(keep_tests=True)
    
    with db.connect() as con:
        cursor = con.cursor()
        cursor.execute("SELECT COUNT(*) FROM orderpos_priority WHERE kind='test'")
        test_count = cursor.fetchone()[0]
        
    assert test_count == 1, "Test priorities should be protected by default"
    
    # Verify: delete_all_pedido_priorities removes tests when keep_tests=False
    repo.delete_all_pedido_priorities(keep_tests=False)
    
    with db.connect() as con:
        cursor = con.cursor()
        cursor.execute("SELECT COUNT(*) FROM orderpos_priority")
        total_count = cursor.fetchone()[0]
        
    assert total_count == 0, "All priorities should be removed when keep_tests=False"

