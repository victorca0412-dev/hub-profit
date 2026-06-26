import sqlite3
from app.db import init_db, get_conn


def test_init_db_creates_tables(tmp_path):
    db_path = tmp_path / "t.db"
    init_db(str(db_path))
    conn = get_conn(str(db_path))
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    finally:
        conn.close()
    assert {"settings", "expense_config", "daily_entries",
            "drivers", "mpg_cache"} <= names


def test_init_db_seeds_settings_and_expenses(tmp_path):
    db_path = tmp_path / "t.db"
    init_db(str(db_path))
    conn = get_conn(str(db_path))
    try:
        s = conn.execute(
            "SELECT pay_per_package FROM settings WHERE id=1").fetchone()
        keys = {r["key"] for r in conn.execute("SELECT key FROM expense_config")}
    finally:
        conn.close()
    assert s["pay_per_package"] == 1.65
    assert keys == {"fuel", "vehicle_wear", "insurance", "phone", "driver"}
