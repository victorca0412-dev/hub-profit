import pytest
from app.db import init_db, get_conn


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    c = get_conn(str(db_path))
    yield c
    c.close()
