import sqlite3

import pytest

from app import db


V1_SCHEMA = """
CREATE TABLE settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    business_name TEXT NOT NULL DEFAULT '',
    pay_per_package REAL NOT NULL DEFAULT 1.65,
    gas_price_per_gal REAL NOT NULL DEFAULT 3.40,
    vehicle_year TEXT NOT NULL DEFAULT '',
    vehicle_make TEXT NOT NULL DEFAULT '',
    vehicle_model TEXT NOT NULL DEFAULT '',
    vehicle_mpg REAL NOT NULL DEFAULT 25.0,
    track_hours INTEGER NOT NULL DEFAULT 1,
    drivers_enabled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE expense_config (
    key TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    mode TEXT NOT NULL CHECK (mode IN ('mileage_fuel','per_mile','monthly','per_day')),
    amount REAL NOT NULL DEFAULT 0
);
CREATE TABLE drivers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE daily_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    driver_id INTEGER REFERENCES drivers(id) ON DELETE SET NULL,
    packages INTEGER NOT NULL,
    miles REAL NOT NULL DEFAULT 0,
    hours REAL,
    extra_expense REAL,
    note TEXT,
    snap_pay_per_package REAL NOT NULL,
    snap_gas_price REAL NOT NULL,
    snap_mpg REAL NOT NULL,
    snap_expense_config TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE mpg_cache (
    cache_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

SNAP_CFG = ('{"fuel": {"enabled": true, "mode": "mileage_fuel", '
            '"amount": 0.0}, "insurance": {"enabled": true, '
            '"mode": "monthly", "amount": 150.0}}')


@pytest.fixture
def v1_db(tmp_path):
    """A populated database in the pre-migration shape."""
    path = str(tmp_path / "v1.db")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(V1_SCHEMA)
    conn.execute(
        "INSERT INTO settings (id, business_name, pay_per_package, "
        "gas_price_per_gal, vehicle_mpg, drivers_enabled) "
        "VALUES (1, 'JVC Vending Services', 1.65, 3.40, 28.0, 1)")
    for key, enabled, mode, amount in [
            ("fuel", 1, "mileage_fuel", 0.0),
            ("insurance", 1, "monthly", 150.0),
            ("phone", 0, "monthly", 0.0)]:
        conn.execute("INSERT INTO expense_config VALUES (?,?,?,?)",
                     (key, enabled, mode, amount))
    conn.execute("INSERT INTO drivers (name, active) VALUES ('Alex', 1)")
    for d, pkgs, miles in [("2026-07-01", 40, 30.0), ("2026-07-02", 55, 44.0)]:
        conn.execute(
            "INSERT INTO daily_entries (date, packages, miles, "
            "snap_pay_per_package, snap_gas_price, snap_mpg, "
            "snap_expense_config) VALUES (?,?,?,?,?,?,?)",
            (d, pkgs, miles, 1.65, 3.40, 28.0, SNAP_CFG))
    conn.commit()
    conn.close()
    return path


def _nets(path):
    """Computed net per date, straight from the table.

    Deliberately does not go through entries_repo: this helper has to run
    both before and after the migration, and the repo's signature changes
    in a later task. Reading raw rows keeps it version-agnostic.
    """
    import json

    from app import periods
    conn = db.get_conn(path)
    try:
        entries = []
        for r in conn.execute("SELECT * FROM daily_entries"):
            e = dict(r)
            e["snap_expense_config"] = json.loads(e["snap_expense_config"])
            entries.append(e)
        return {r["entry"]["date"]: round(r["computed"]["net"], 6)
                for r in periods.computed_entries(entries)}
    finally:
        conn.close()


class TestMigration:
    def test_creates_the_businesses_table(self, v1_db):
        db.init_db(v1_db)
        conn = db.get_conn(v1_db)
        rows = conn.execute("SELECT * FROM businesses").fetchall()
        conn.close()
        assert len(rows) == 1

    def test_first_business_takes_the_legacy_name_and_rate(self, v1_db):
        db.init_db(v1_db)
        conn = db.get_conn(v1_db)
        b = conn.execute("SELECT * FROM businesses WHERE id=1").fetchone()
        conn.close()
        assert b["name"] == "JVC Vending Services"
        assert b["pay_per_package"] == 1.65
        assert b["drivers_enabled"] == 1
        assert b["rate_model"] == "flat"

    def test_blank_legacy_name_gets_a_placeholder(self, tmp_path):
        path = str(tmp_path / "blank.db")
        conn = sqlite3.connect(path)
        conn.executescript(V1_SCHEMA)
        conn.execute("INSERT INTO settings (id) VALUES (1)")
        conn.commit()
        conn.close()
        db.init_db(path)
        conn = db.get_conn(path)
        name = conn.execute(
            "SELECT name FROM businesses WHERE id=1").fetchone()[0]
        conn.close()
        assert name == "My Hub Business"

    def test_every_existing_entry_is_assigned_to_business_one(self, v1_db):
        db.init_db(v1_db)
        conn = db.get_conn(v1_db)
        ids = [r[0] for r in conn.execute(
            "SELECT DISTINCT business_id FROM daily_entries")]
        conn.close()
        assert ids == [1]

    def test_existing_drivers_are_assigned_to_business_one(self, v1_db):
        db.init_db(v1_db)
        conn = db.get_conn(v1_db)
        ids = [r[0] for r in conn.execute(
            "SELECT DISTINCT business_id FROM drivers")]
        conn.close()
        assert ids == [1]

    def test_expense_config_survives_with_its_values(self, v1_db):
        db.init_db(v1_db)
        conn = db.get_conn(v1_db)
        rows = {r["key"]: r for r in conn.execute(
            "SELECT * FROM expense_config WHERE business_id=1")}
        conn.close()
        assert rows["insurance"]["amount"] == 150.0
        assert rows["insurance"]["enabled"] == 1
        assert rows["fuel"]["mode"] == "mileage_fuel"

    def test_settings_loses_the_per_business_columns(self, v1_db):
        db.init_db(v1_db)
        conn = db.get_conn(v1_db)
        cols = db.column_names(conn, "settings")
        conn.close()
        assert "business_name" not in cols
        assert "pay_per_package" not in cols
        assert "drivers_enabled" not in cols
        assert "active_business_id" in cols

    def test_global_settings_values_survive_the_rebuild(self, v1_db):
        db.init_db(v1_db)
        conn = db.get_conn(v1_db)
        s = conn.execute("SELECT * FROM settings WHERE id=1").fetchone()
        conn.close()
        assert s["vehicle_mpg"] == 28.0
        assert s["gas_price_per_gal"] == 3.40
        assert s["active_business_id"] == 1

    def test_no_historical_net_changes(self, v1_db):
        before = _nets(v1_db)
        db.init_db(v1_db)
        after = _nets(v1_db)
        assert before == after
        assert len(before) == 2

    def test_migration_is_idempotent(self, v1_db):
        db.init_db(v1_db)
        conn = db.get_conn(v1_db)
        snapshot = (
            conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM expense_config").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM daily_entries").fetchone()[0])
        conn.close()
        db.init_db(v1_db)
        db.init_db(v1_db)
        conn = db.get_conn(v1_db)
        again = (
            conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM expense_config").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM daily_entries").fetchone()[0])
        assert (conn.execute("PRAGMA user_version").fetchone()[0]
                == db.SCHEMA_VERSION)
        conn.close()
        assert snapshot == again

    def test_orphan_date_is_repaired_and_becomes_visible(self, v1_db):
        conn = sqlite3.connect(v1_db)
        conn.execute(
            "INSERT INTO daily_entries (date, packages, miles, "
            "snap_pay_per_package, snap_gas_price, snap_mpg, "
            "snap_expense_config, created_at) "
            "VALUES ('not-a-date', 12, 9.0, 1.65, 3.40, 28.0, ?, "
            "'2026-07-05 14:00:00')", (SNAP_CFG,))
        conn.commit()
        conn.close()
        db.init_db(v1_db)
        conn = db.get_conn(v1_db)
        row = conn.execute(
            "SELECT date FROM daily_entries WHERE packages=12").fetchone()
        conn.close()
        assert row["date"] == "2026-07-05"

    def test_repairing_an_orphan_respreads_that_month_s_monthly_costs(
            self, v1_db):
        """Documented, intentional consequence of the Bug C repair.

        A monthly cost is spread across the days worked that month. An
        orphan row was a real day worked that no query could see, so it
        was excluded from that divisor. Making it visible necessarily
        adds a workday and every day in that month gets a slightly
        smaller share.

        The alternative - leaving the orphan invisible so the arithmetic
        never moves - means permanently under-reporting days worked and
        losing a day's earnings. Recorded here so this is never
        "corrected" back.
        """
        conn = sqlite3.connect(v1_db)
        # A day that no query could reach, created on a date not already
        # worked, so repairing it genuinely adds a July workday.
        conn.execute(
            "INSERT INTO daily_entries (date, packages, miles, "
            "snap_pay_per_package, snap_gas_price, snap_mpg, "
            "snap_expense_config, created_at) "
            "VALUES ('not-a-date', 44, 31.0, 1.65, 3.40, 28.0, ?, "
            "'2026-07-26 09:00:00')", (SNAP_CFG,))
        conn.commit()
        conn.close()

        before = _nets(v1_db)
        db.init_db(v1_db)
        after = _nets(v1_db)

        # The fixture has 2 July days; the repair makes it 3.
        # Insurance is $150/month, so the share per day falls from
        # 150/2 to 150/3 and each day's net rises by the difference.
        expected_gain = 150.0 / 2 - 150.0 / 3
        assert after["2026-07-01"] - before["2026-07-01"] == \
            pytest.approx(expected_gain, abs=1e-6)
        # And the repaired day is now visible on its own created_at date.
        assert "2026-07-26" in after

    def test_migration_alone_changes_no_net_when_there_is_no_orphan(
            self, v1_db):
        # The companion to the test above: absent an orphan, the upgrade
        # is arithmetically inert.
        before = _nets(v1_db)
        db.init_db(v1_db)
        assert _nets(v1_db) == before

    def test_a_fresh_database_lands_at_the_current_version(self, tmp_path):
        path = str(tmp_path / "fresh.db")
        db.init_db(path)
        conn = db.get_conn(path)
        assert (conn.execute("PRAGMA user_version").fetchone()[0]
                == db.SCHEMA_VERSION)
        assert conn.execute(
            "SELECT COUNT(*) FROM businesses").fetchone()[0] == 1
        # A fresh install must get the default expense rows, or every day
        # logged under it would silently report zero costs.
        assert conn.execute(
            "SELECT COUNT(*) FROM expense_config WHERE business_id=1"
        ).fetchone()[0] == 4
        conn.close()

    def test_a_failed_migration_leaves_the_database_untouched(self, v1_db,
                                                              monkeypatch):
        def boom(conn):
            conn.execute("CREATE TABLE businesses (id INTEGER PRIMARY KEY)")
            raise RuntimeError("simulated failure mid-migration")

        # Patch the registry, not the function: MIGRATIONS captured the
        # original function object at import time, so replacing the module
        # attribute alone would leave the real migration running.
        monkeypatch.setattr(db, "MIGRATIONS", {2: boom})
        with pytest.raises(RuntimeError):
            db.init_db(v1_db)
        conn = db.get_conn(v1_db)
        cols = db.column_names(conn, "settings")
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.close()
        # Rolled back: no businesses table, settings untouched, version 0.
        assert "businesses" not in tables
        assert "business_name" in cols
        assert version == 0
