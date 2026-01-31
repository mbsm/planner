
import pytest
import json
from uuid import uuid4
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
    
    # Setup config
    repo = Repository(db)
    repo.set_config(key="job_priority_map", value='{"prueba": 1, "urgente": 2, "normal": 3}')
    
    yield db, db_path
    
    try:
        Path(tmpdir).rmdir()
    except Exception:
        pass

def create_dummy_job(db, process="terminaciones", is_test=0, priority=3, pedido="P1", posicion="10"):
    job_id = f"job_{uuid4().hex}"
    with db.connect() as con:
        con.execute(
            """
            INSERT INTO job(
                job_id, process_id, pedido, posicion, material,
                qty, priority, is_test, state
            ) VALUES (?, ?, ?, ?, 'M1', 1, ?, ?, 'pending')
            """,
            (job_id, process, pedido, posicion, priority, is_test)
        )
    return job_id

def test_mark_job_urgent_normal_flow(temp_db):
    db, _ = temp_db
    repo = Repository(db)
    
    job_id = create_dummy_job(db, is_test=0, priority=3) # Normal
    
    # Mark urgent
    repo.mark_job_urgent(job_id)
    
    with db.connect() as con:
        row = con.execute("SELECT priority FROM job WHERE job_id = ?", (job_id,)).fetchone()
    assert row["priority"] == 2, "Should be updated to urgent priority (2)"
    
    # Unmark (back to normal)
    repo.unmark_job_urgent(job_id)
    
    with db.connect() as con:
        row = con.execute("SELECT priority FROM job WHERE job_id = ?", (job_id,)).fetchone()
    assert row["priority"] == 3, "Should be updated to normal priority (3)"

def test_mark_job_urgent_on_test_job(temp_db):
    db, _ = temp_db
    repo = Repository(db)
    
    # Job is a test (priority 1)
    job_id = create_dummy_job(db, is_test=1, priority=1)
    
    # Try to mark urgent (should fail or be ignored)
    # Raising ValueError seems appropriate for explicit user action
    with pytest.raises(ValueError, match="Cannot change priority of a test job"):
        repo.mark_job_urgent(job_id)
        
    with db.connect() as con:
        row = con.execute("SELECT priority FROM job WHERE job_id = ?", (job_id,)).fetchone()
    assert row["priority"] == 1, "Priority should remain 1 (test)"

def test_config_change_recalculates_priorities(temp_db):
    db, _ = temp_db
    repo = Repository(db)
    
    # 1. Setup jobs with default priorities: Normal=3, Urgent=2
    job_normal = create_dummy_job(db, priority=3, pedido="P1", posicion="10")
    job_urgent = create_dummy_job(db, priority=2, pedido="P2", posicion="10")
    job_test = create_dummy_job(db, priority=1, is_test=1, pedido="P3", posicion="10")

    # Mark P2/10 as manual priority
    with db.connect() as con:
        con.execute(
            "INSERT INTO orderpos_priority(pedido, posicion, is_priority, kind) VALUES('P2', '10', 1, 'manual')"
        )
    
    # 2. Change config: Normal -> 4, Urgent -> 3, Test -> 1 (unchanged)
    # Note: urgent moves to 3 (which was normal), normal moves to 4.
    new_config = json.dumps({"prueba": 1, "urgente": 3, "normal": 4})
    repo.set_config(key="job_priority_map", value=new_config)
    
    # 3. Verify updates
    with db.connect() as con:
        p_normal = con.execute("SELECT priority FROM job WHERE job_id=?", (job_normal,)).fetchone()["priority"]
        p_urgent = con.execute("SELECT priority FROM job WHERE job_id=?", (job_urgent,)).fetchone()["priority"]
        p_test = con.execute("SELECT priority FROM job WHERE job_id=?", (job_test,)).fetchone()["priority"]
        
    assert p_normal == 4, "Normal job should update to new normal value"
    assert p_urgent == 3, "Urgent job should update to new urgent value"
    assert p_test == 1, "Test job should stay 1"
