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
            "drivers", "mpg_cache", "businesses"} <= names


def test_init_db_seeds_settings_and_expenses(tmp_path):
    db_path = tmp_path / "t.db"
    init_db(str(db_path))
    conn = get_conn(str(db_path))
    try:
        # The pay rate moved to the business; settings keeps only the
        # global vehicle/fuel/display fields.
        b = conn.execute(
            "SELECT pay_per_package FROM businesses WHERE id=1").fetchone()
        s = conn.execute(
            "SELECT active_business_id FROM settings WHERE id=1").fetchone()
        keys = {r["key"] for r in conn.execute(
            "SELECT key FROM expense_config WHERE business_id=1")}
    finally:
        conn.close()
    assert b["pay_per_package"] == 1.65
    assert s["active_business_id"] == 1
    assert keys == {"fuel", "vehicle_wear", "insurance", "phone"}
