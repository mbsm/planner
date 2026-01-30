import pytest
from foundryplan.data.db import Db
from foundryplan.data.repository import Repository
import tempfile
from pathlib import Path

@pytest.fixture
def temp_repo():
    tmpdir = tempfile.mkdtemp()
    db_path = Path(tmpdir) / "test.db"
    db = Db(db_path)
    db.ensure_schema()
    repo = Repository(db)
    yield repo
    import gc
    gc.collect()
    import shutil
    try:
        shutil.rmtree(tmpdir)
    except PermissionError:
        pass

def test_move_in_progress_config_check(temp_repo):
    repo = temp_repo
    
    # 1. Config DISABLED (default)
    # Mock existence of an item? The check happens before DB update usually, 
    # but we should ensure we hit the check.
    
    with pytest.raises(ValueError, match="Movimiento manual deshabilitado"):
        repo.move_in_progress(pedido="X", posicion="1", line_id=2)
        
    # 2. Config ENABLED
    repo.set_config(key="ui_allow_move_in_progress_line", value="1")
    
    # Now it should proceed to try DB update (which might fail if row missing, but pass config check)
    # We don't need to seed a full job row to test the config check passed, 
    # checking for different error or success is enough.
    
    # Since we didn't insert 'program_in_progress_item', it will execute UPDATE with 0 changes or fail?
    # The code uses a try/except inside? No, it catches Exception and falls back to legacy table.
    
    repo.move_in_progress(pedido="X", posicion="1", line_id=2)
    # Should not raise "Movimiento manual deshabilitado"
    
    # 3. Disable again
    repo.set_config(key="ui_allow_move_in_progress_line", value="0")
    with pytest.raises(ValueError, match="Movimiento manual deshabilitado"):
        repo.move_in_progress(pedido="X", posicion="1", line_id=2)
