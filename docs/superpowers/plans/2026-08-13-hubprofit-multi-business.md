# HubProfit Multi-Business Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one HubProfit instance hold several Hub Delivery businesses and switch between them, migrating the existing single-business database in place without changing a single historical number.

**Architecture:** `PRAGMA user_version` drives an idempotent, transactional migration run at startup. A new `businesses` table owns name, pay rate, and driver settings; `settings` keeps only the global vehicle/fuel/display fields plus `active_business_id`. Every repo function takes a `business_id`, and routes resolve the active business once through a shared context helper.

**Tech Stack:** Python 3.12 (container), FastAPI, Jinja2, SQLite, pytest. No new dependencies.

## Global Constraints

- Branch `feat/multi-business`, already checked out. Base branch is `master`.
- Run tests with `.venv/Scripts/python -m pytest` from `C:\Users\Victor\hub-profit`.
- The 100 tests from Phase 0 must continue to pass.
- **No `ALTER TABLE ... DROP COLUMN` anywhere.** It needs SQLite 3.35+, and the
  container's version cannot be verified from this dev box. Rebuild tables instead
  using `CREATE` / `INSERT ... SELECT` / `DROP` / `RENAME TO`.
- **No `snap_*` column is ever written by an update path.**
- The migration must be idempotent and atomic. Running it twice equals running it
  once; any failure rolls back to the untouched original.
- **No historical entry's computed net may change.** This is the regression that
  matters most.
- Match existing style: 4-space indent, ~79 col lines, docstrings for the *why*.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/db.py` (modify) | Schema, migration runner, v2 migration, seeding. |
| `app/businesses_repo.py` (create) | Business CRUD, archiving, default seeding. |
| `app/settings_repo.py` (modify) | Global settings; business-scoped expense config. |
| `app/entries_repo.py` (modify) | Business-scoped entry queries. |
| `app/drivers_repo.py` (modify) | Business-scoped driver queries. |
| `app/main.py` (modify) | Active-business resolution, shared context, routes. |
| `app/templates/base.html` (modify) | Business switcher in the header. |
| `app/templates/businesses.html` (create) | Manage-businesses page. |
| `tests/test_migration.py` (create) | Migration against a v1-shaped database. |
| `tests/test_businesses_repo.py` (create) | Business repo unit tests. |

---

## Task 1: Migration infrastructure and the v2 migration

**Files:**
- Modify: `app/db.py`
- Test: `tests/test_migration.py`

**Interfaces:**
- Produces:
  - `SCHEMA_VERSION = 2` in `app/db.py`
  - `migrate(conn) -> None` — idempotent, transactional, safe on a fresh DB
  - `init_db(db_path) -> None` — unchanged signature; now creates, migrates, seeds
  - `column_names(conn, table) -> set[str]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_migration.py`. The v1 schema is pasted in as a literal so the test
keeps building the *old* shape even as `db.SCHEMA` evolves:

```python
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

SNAP_CFG = '{"fuel": {"enabled": true, "mode": "mileage_fuel", "amount": 0.0}}'


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
        name = conn.execute("SELECT name FROM businesses WHERE id=1").fetchone()[0]
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
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
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

    def test_a_fresh_database_lands_at_the_current_version(self, tmp_path):
        path = str(tmp_path / "fresh.db")
        db.init_db(path)
        conn = db.get_conn(path)
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM businesses").fetchone()[0] == 1
        # A fresh install must get the default expense rows, or every day
        # logged under it would silently report zero costs.
        assert conn.execute(
            "SELECT COUNT(*) FROM expense_config WHERE business_id=1"
        ).fetchone()[0] == 5
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_migration.py -v`
Expected: FAIL — `AttributeError: module 'app.db' has no attribute 'column_names'`

- [ ] **Step 3: Rewrite `app/db.py`**

Replace the whole file:

```python
import sqlite3

SCHEMA_VERSION = 2

# The v1 base shape. Always applied with IF NOT EXISTS, then brought up to
# SCHEMA_VERSION by migrate(). A fresh database and a migrated one therefore
# converge on the identical schema - there is no second code path whose
# drift could go unnoticed.
SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
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
CREATE TABLE IF NOT EXISTS expense_config (
    key TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    mode TEXT NOT NULL CHECK (mode IN ('mileage_fuel', 'per_mile', 'monthly', 'per_day')),
    amount REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS drivers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS daily_entries (
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
CREATE INDEX IF NOT EXISTS idx_entries_date ON daily_entries(date);
CREATE TABLE IF NOT EXISTS mpg_cache (
    cache_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

DEFAULT_EXPENSES = [
    ("fuel", 1, "mileage_fuel", 0.0),
    ("vehicle_wear", 0, "per_mile", 0.18),
    ("insurance", 0, "monthly", 0.0),
    ("phone", 0, "monthly", 0.0),
    ("driver", 0, "per_day", 0.0),
]

ISO_DATE_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"


def get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def column_names(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn, table, column, decl):
    """ADD COLUMN if it is not already there. Idempotent."""
    if column not in column_names(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _migrate_to_v2(conn):
    """Single business -> many businesses.

    Deliberately avoids ALTER TABLE ... DROP COLUMN, which needs SQLite
    3.35+. The dev box has 3.50 but the container is python:3.12-slim and
    its version cannot be checked from a Windows box with no Docker. That
    is how the July timezone bug shipped looking applied. Tables that need
    columns removed are rebuilt instead.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS businesses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            rate_model TEXT NOT NULL DEFAULT 'flat'
                CHECK (rate_model IN ('flat', 'tiered')),
            pay_per_package REAL NOT NULL DEFAULT 1.65,
            drivers_enabled INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )""")

    # Business #1 inherits whatever the single-business install had.
    if conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0] == 0:
        legacy = column_names(conn, "settings")
        name, rate, drivers_on = "", 1.65, 0
        if {"business_name", "pay_per_package"} <= legacy:
            row = conn.execute(
                "SELECT business_name, pay_per_package, drivers_enabled "
                "FROM settings WHERE id=1").fetchone()
            if row is not None:
                name = (row["business_name"] or "").strip()
                rate = row["pay_per_package"]
                drivers_on = row["drivers_enabled"]
        conn.execute(
            "INSERT INTO businesses (id, name, rate_model, pay_per_package, "
            "drivers_enabled) VALUES (1, ?, 'flat', ?, ?)",
            (name or "My Hub Business", rate, drivers_on))

    # The DEFAULT backfills every existing row in the same statement.
    _add_column(conn, "drivers", "business_id", "INTEGER NOT NULL DEFAULT 1")
    _add_column(conn, "daily_entries", "business_id",
                "INTEGER NOT NULL DEFAULT 1")

    # expense_config needs a composite primary key, so it is rebuilt.
    # One execute() per statement: executescript would COMMIT first and
    # break the surrounding transaction.
    if "business_id" not in column_names(conn, "expense_config"):
        conn.execute("""
            CREATE TABLE expense_config_v2 (
                business_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                mode TEXT NOT NULL CHECK (mode IN
                    ('mileage_fuel', 'per_mile', 'monthly', 'per_day')),
                amount REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (business_id, key)
            )""")
        conn.execute("""
            INSERT INTO expense_config_v2
                (business_id, key, enabled, mode, amount)
                SELECT 1, key, enabled, mode, amount FROM expense_config""")
        conn.execute("DROP TABLE expense_config")
        conn.execute("ALTER TABLE expense_config_v2 RENAME TO expense_config")

    # settings sheds the three now-per-business columns and gains the
    # active-business pointer. Rebuild, because DROP COLUMN is off limits.
    if "business_name" in column_names(conn, "settings"):
        conn.execute("""
            CREATE TABLE settings_v2 (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                gas_price_per_gal REAL NOT NULL DEFAULT 3.40,
                vehicle_year TEXT NOT NULL DEFAULT '',
                vehicle_make TEXT NOT NULL DEFAULT '',
                vehicle_model TEXT NOT NULL DEFAULT '',
                vehicle_mpg REAL NOT NULL DEFAULT 25.0,
                track_hours INTEGER NOT NULL DEFAULT 1,
                active_business_id INTEGER NOT NULL DEFAULT 1
            )""")
        conn.execute("""
            INSERT INTO settings_v2 (id, gas_price_per_gal, vehicle_year,
                vehicle_make, vehicle_model, vehicle_mpg, track_hours,
                active_business_id)
                SELECT id, gas_price_per_gal, vehicle_year, vehicle_make,
                       vehicle_model, vehicle_mpg, track_hours, 1
                FROM settings""")
        conn.execute("DROP TABLE settings")
        conn.execute("ALTER TABLE settings_v2 RENAME TO settings")

    # Repair Bug C orphans: dates that sort outside every query range.
    # Catches malformed dates, which is the shape the bug produced. A
    # format-valid but impossible date is not detected and is no longer
    # reachable now that the routes validate on write.
    conn.execute(
        "UPDATE daily_entries SET date = substr(created_at, 1, 10) "
        f"WHERE date NOT GLOB '{ISO_DATE_GLOB}'")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_entries_business_date "
                 "ON daily_entries(business_id, date)")


MIGRATIONS = {2: _migrate_to_v2}


def migrate(conn):
    """Bring conn up to SCHEMA_VERSION. Idempotent and atomic.

    Uses an explicit BEGIN rather than `with conn:`. Python's sqlite3 in
    legacy isolation mode only opens an implicit transaction for DML, not
    for DDL, so CREATE/DROP/ALTER would autocommit one statement at a time
    and a failure halfway through would leave a half-migrated database
    with no way back. BEGIN forces the whole step to be one unit.

    For the same reason nothing in a migration may use executescript(),
    which issues a COMMIT before it runs.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for target in sorted(MIGRATIONS):
        if version < target:
            conn.execute("BEGIN")
            try:
                MIGRATIONS[target](conn)
                # PRAGMA cannot be parameterised; target is an int key from
                # MIGRATIONS, never user input.
                conn.execute(f"PRAGMA user_version = {int(target)}")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise


def seed_business_expenses(conn, business_id):
    """Give a business the default expense rows if it has none.

    Without these compute_entry iterates an empty config and reports zero
    costs, so a new business would silently look more profitable than it
    is.
    """
    for key, enabled, mode, amount in DEFAULT_EXPENSES:
        conn.execute(
            "INSERT OR IGNORE INTO expense_config "
            "(business_id, key, enabled, mode, amount) VALUES (?,?,?,?,?)",
            (business_id, key, enabled, mode, amount))


def init_db(db_path: str) -> None:
    conn = get_conn(db_path)
    try:
        conn.executescript(SCHEMA)
        with conn:
            if conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0:
                conn.execute("INSERT INTO settings (id) VALUES (1)")
        migrate(conn)
        with conn:
            for row in conn.execute("SELECT id FROM businesses").fetchall():
                seed_business_expenses(conn, row["id"])
    finally:
        conn.close()
```

Note what moved: expense seeding no longer runs before the migration. In v2 the
table requires a `business_id`, so seeding it with the old four-column INSERT would
violate NOT NULL on every startup after the first.

- [ ] **Step 4: Run the migration tests**

Run: `.venv/Scripts/python -m pytest tests/test_migration.py -v`
Expected: PASS, 13 tests. `test_no_historical_net_changes` is the one that matters —
if it fails, stop and investigate rather than adjusting the assertion.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/Scripts/python -m pytest`
Expected: FAIL. Repos still query without `business_id` and `settings_repo` still
lists removed columns. That is expected at this point and Tasks 2–4 fix it. Record
which tests fail so the later tasks can confirm they are addressed.

- [ ] **Step 6: Commit**

```bash
git add app/db.py tests/test_migration.py
git commit -m "feat: add a versioned migration runner and migrate to multi-business schema"
```

---

## Task 2: businesses_repo

**Files:**
- Create: `app/businesses_repo.py`
- Test: `tests/test_businesses_repo.py`

**Interfaces:**
- Consumes: `db.seed_business_expenses`.
- Produces:
  - `list_businesses(conn, include_archived=False) -> list[dict]`
  - `get_business(conn, business_id) -> dict | None`
  - `create_business(conn, name) -> int`
  - `rename_business(conn, business_id, name) -> bool`
  - `set_archived(conn, business_id, archived) -> bool`
  - `update_business(conn, business_id, values) -> None` (allow-listed fields)
  - `ActiveBusinessError` — raised when archiving the last active business

- [ ] **Step 1: Write the failing tests**

Create `tests/test_businesses_repo.py`:

```python
import pytest

from app import businesses_repo as repo
from app.db import init_db, get_conn


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "b.db")
    init_db(path)
    c = get_conn(path)
    yield c
    c.close()


def test_a_fresh_database_has_one_business(conn):
    assert len(repo.list_businesses(conn)) == 1


def test_create_business_returns_its_id(conn):
    new_id = repo.create_business(conn, "Newton Hub")
    assert new_id > 1
    assert repo.get_business(conn, new_id)["name"] == "Newton Hub"


def test_a_new_business_gets_the_default_expense_rows(conn):
    new_id = repo.create_business(conn, "Newton Hub")
    rows = conn.execute(
        "SELECT key, enabled FROM expense_config WHERE business_id=?",
        (new_id,)).fetchall()
    assert len(rows) == 5
    # Fuel on by default, matching what a fresh install gets.
    assert dict((r["key"], r["enabled"]) for r in rows)["fuel"] == 1


def test_a_new_business_starts_flat_at_the_default_rate(conn):
    b = repo.get_business(conn, repo.create_business(conn, "Newton Hub"))
    assert b["rate_model"] == "flat"
    assert b["pay_per_package"] == 1.65
    assert b["drivers_enabled"] == 0


def test_rename_business(conn):
    assert repo.rename_business(conn, 1, "Renamed") is True
    assert repo.get_business(conn, 1)["name"] == "Renamed"


def test_rename_unknown_business_reports_false(conn):
    assert repo.rename_business(conn, 999, "Nope") is False


def test_archived_business_is_hidden_by_default(conn):
    new_id = repo.create_business(conn, "Newton Hub")
    repo.set_archived(conn, new_id, True)
    assert [b["id"] for b in repo.list_businesses(conn)] == [1]
    assert len(repo.list_businesses(conn, include_archived=True)) == 2


def test_archived_business_can_be_restored(conn):
    new_id = repo.create_business(conn, "Newton Hub")
    repo.set_archived(conn, new_id, True)
    repo.set_archived(conn, new_id, False)
    assert len(repo.list_businesses(conn)) == 2


def test_the_last_active_business_cannot_be_archived(conn):
    with pytest.raises(repo.ActiveBusinessError):
        repo.set_archived(conn, 1, True)
    assert len(repo.list_businesses(conn)) == 1


def test_update_business_only_writes_allowed_fields(conn):
    repo.update_business(conn, 1, {"pay_per_package": 2.25,
                                   "drivers_enabled": 1,
                                   "id": 99, "archived": 1})
    b = repo.get_business(conn, 1)
    assert b["pay_per_package"] == 2.25
    assert b["drivers_enabled"] == 1
    assert b["id"] == 1        # id is not writable
    assert b["archived"] == 0  # archiving goes through set_archived
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_businesses_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.businesses_repo'`

- [ ] **Step 3: Write the implementation**

Create `app/businesses_repo.py`:

```python
from app.db import seed_business_expenses

ALLOWED_FIELDS = {"name", "rate_model", "pay_per_package", "drivers_enabled"}


class ActiveBusinessError(Exception):
    """Raised when an operation would leave no active business."""


def list_businesses(conn, include_archived=False):
    sql = "SELECT * FROM businesses"
    if not include_archived:
        sql += " WHERE archived = 0"
    sql += " ORDER BY id"
    return [dict(r) for r in conn.execute(sql)]


def get_business(conn, business_id):
    row = conn.execute("SELECT * FROM businesses WHERE id=?",
                       (business_id,)).fetchone()
    return dict(row) if row else None


def create_business(conn, name):
    cur = conn.execute("INSERT INTO businesses (name) VALUES (?)", (name,))
    business_id = cur.lastrowid
    # Seed inside the same transaction: a business without expense rows
    # would silently report zero costs on every day logged under it.
    seed_business_expenses(conn, business_id)
    conn.commit()
    return business_id


def rename_business(conn, business_id, name):
    cur = conn.execute("UPDATE businesses SET name=? WHERE id=?",
                       (name, business_id))
    conn.commit()
    return cur.rowcount > 0


def update_business(conn, business_id, values):
    fields = {k: v for k, v in values.items() if k in ALLOWED_FIELDS}
    if not fields:
        return
    assignments = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE businesses SET {assignments} WHERE id=?",
                 list(fields.values()) + [business_id])
    conn.commit()


def set_archived(conn, business_id, archived):
    if archived:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM businesses WHERE archived=0 AND id<>?",
            (business_id,)).fetchone()[0]
        if remaining == 0:
            raise ActiveBusinessError(
                "You cannot archive your only business.")
    cur = conn.execute("UPDATE businesses SET archived=? WHERE id=?",
                       (1 if archived else 0, business_id))
    conn.commit()
    return cur.rowcount > 0
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python -m pytest tests/test_businesses_repo.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add app/businesses_repo.py tests/test_businesses_repo.py
git commit -m "feat: add businesses repo with default expense seeding"
```

---

## Task 3: Scope the repos to a business

**Files:**
- Modify: `app/settings_repo.py`, `app/entries_repo.py`, `app/drivers_repo.py`
- Test: `tests/test_settings_repo.py`, `tests/test_entries_repo.py`,
  `tests/test_drivers_repo.py`

**Interfaces:**
- Produces (all `business_id` params are required keyword-or-positional):
  - `settings_repo.get_settings(conn)` — global fields only
  - `settings_repo.get_active_business_id(conn) -> int`
  - `settings_repo.set_active_business(conn, business_id) -> None`
  - `settings_repo.get_expense_config(conn, business_id) -> dict`
  - `settings_repo.update_expense_config(conn, business_id, key, enabled=None, amount=None)`
  - `entries_repo.create_entry(conn, data, business_id) -> int`
  - `entries_repo.list_entries(conn, start, end, business_id) -> list[dict]`
  - `entries_repo.get_entry(conn, entry_id, business_id) -> dict | None`
  - `entries_repo.update_entry(conn, entry_id, data, business_id) -> bool`
  - `entries_repo.delete_entry(conn, entry_id, business_id) -> None`
  - `entries_repo.distinct_workdays_in_month(conn, year_month, business_id) -> int`
  - `drivers_repo.add_driver(conn, name, business_id) -> int`
  - `drivers_repo.list_drivers(conn, business_id, only_active=False) -> list[dict]`
  - `drivers_repo.get_driver(conn, driver_id, business_id) -> dict | None`
  - `drivers_repo.rename_driver(conn, driver_id, name, business_id) -> bool`
  - `drivers_repo.set_driver_active(conn, driver_id, active, business_id) -> None`

`create_entry` reads the pay rate from `businesses`, not `settings`, and still reads
gas price and MPG from the global `settings` row.

- [ ] **Step 1: Write the failing isolation tests**

Append to `tests/test_entries_repo.py`:

```python
def test_entries_are_isolated_between_businesses(conn):
    from app import businesses_repo
    other = businesses_repo.create_business(conn, "Newton Hub")
    entries_repo.create_entry(conn, {
        "date": "2026-08-01", "packages": 40, "miles": 30.0}, business_id=1)
    entries_repo.create_entry(conn, {
        "date": "2026-08-01", "packages": 10, "miles": 5.0},
        business_id=other)
    mine = entries_repo.list_entries(conn, "1970-01-01", "9999-12-31",
                                     business_id=1)
    theirs = entries_repo.list_entries(conn, "1970-01-01", "9999-12-31",
                                       business_id=other)
    assert [e["packages"] for e in mine] == [40]
    assert [e["packages"] for e in theirs] == [10]


def test_get_entry_will_not_cross_businesses(conn):
    from app import businesses_repo
    other = businesses_repo.create_business(conn, "Newton Hub")
    entry_id = entries_repo.create_entry(conn, {
        "date": "2026-08-01", "packages": 40, "miles": 30.0}, business_id=1)
    assert entries_repo.get_entry(conn, entry_id, business_id=other) is None
    assert entries_repo.get_entry(conn, entry_id, business_id=1) is not None


def test_workday_count_is_per_business(conn):
    from app import businesses_repo
    other = businesses_repo.create_business(conn, "Newton Hub")
    entries_repo.create_entry(conn, {
        "date": "2026-08-01", "packages": 40, "miles": 0}, business_id=1)
    entries_repo.create_entry(conn, {
        "date": "2026-08-02", "packages": 40, "miles": 0}, business_id=1)
    entries_repo.create_entry(conn, {
        "date": "2026-08-03", "packages": 40, "miles": 0}, business_id=other)
    assert entries_repo.distinct_workdays_in_month(
        conn, "2026-08", business_id=1) == 2
    assert entries_repo.distinct_workdays_in_month(
        conn, "2026-08", business_id=other) == 1


def test_entry_snapshots_its_own_business_rate(conn):
    from app import businesses_repo
    other = businesses_repo.create_business(conn, "Newton Hub")
    businesses_repo.update_business(conn, other, {"pay_per_package": 2.25})
    entry_id = entries_repo.create_entry(conn, {
        "date": "2026-08-01", "packages": 10, "miles": 0}, business_id=other)
    e = entries_repo.get_entry(conn, entry_id, business_id=other)
    assert e["snap_pay_per_package"] == 2.25
```

Append to `tests/test_drivers_repo.py`:

```python
def test_drivers_are_isolated_between_businesses(conn):
    from app import businesses_repo
    other = businesses_repo.create_business(conn, "Newton Hub")
    add_driver(conn, "Alex", business_id=1)
    add_driver(conn, "Sam", business_id=other)
    assert [d["name"] for d in list_drivers(conn, business_id=1)] == ["Alex"]
    assert [d["name"] for d in list_drivers(conn, business_id=other)] == ["Sam"]
```

Every pre-existing call in those two test files needs `business_id=1` added. Update
them rather than adding a default — a silent default is how entries would end up in
the wrong book.

Append to `tests/test_settings_repo.py`:

```python
def test_expense_config_is_per_business(conn):
    from app import businesses_repo
    other = businesses_repo.create_business(conn, "Newton Hub")
    settings_repo.update_expense_config(conn, 1, "insurance",
                                        enabled=True, amount=150.0)
    mine = settings_repo.get_expense_config(conn, 1)
    theirs = settings_repo.get_expense_config(conn, other)
    assert mine["insurance"]["amount"] == 150.0
    assert theirs["insurance"]["amount"] == 0.0


def test_active_business_round_trips(conn):
    from app import businesses_repo
    other = businesses_repo.create_business(conn, "Newton Hub")
    settings_repo.set_active_business(conn, other)
    assert settings_repo.get_active_business_id(conn) == other
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_entries_repo.py tests/test_drivers_repo.py tests/test_settings_repo.py -v`
Expected: FAIL — unexpected keyword argument `business_id`.

- [ ] **Step 3: Update `app/settings_repo.py`**

```python
ALLOWED_SETTINGS = {
    "gas_price_per_gal", "vehicle_year", "vehicle_make", "vehicle_model",
    "vehicle_mpg", "track_hours",
}


def get_settings(conn):
    return dict(conn.execute("SELECT * FROM settings WHERE id=1").fetchone())


def update_settings(conn, values):
    fields = {k: v for k, v in values.items() if k in ALLOWED_SETTINGS}
    if not fields:
        return
    assignments = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE settings SET {assignments} WHERE id=1",
                 list(fields.values()))
    conn.commit()


def get_active_business_id(conn):
    return conn.execute(
        "SELECT active_business_id FROM settings WHERE id=1").fetchone()[0]


def set_active_business(conn, business_id):
    conn.execute("UPDATE settings SET active_business_id=? WHERE id=1",
                 (business_id,))
    conn.commit()


def get_expense_config(conn, business_id):
    out = {}
    for r in conn.execute(
            "SELECT key, enabled, mode, amount FROM expense_config "
            "WHERE business_id=?", (business_id,)):
        out[r["key"]] = {"enabled": bool(r["enabled"]), "mode": r["mode"],
                         "amount": r["amount"]}
    return out


def update_expense_config(conn, business_id, key, enabled=None, amount=None):
    if enabled is None and amount is None:
        return
    if enabled is not None:
        conn.execute("UPDATE expense_config SET enabled=? "
                     "WHERE business_id=? AND key=?",
                     (1 if enabled else 0, business_id, key))
    if amount is not None:
        conn.execute("UPDATE expense_config SET amount=? "
                     "WHERE business_id=? AND key=?",
                     (amount, business_id, key))
    conn.commit()
```

- [ ] **Step 4: Update `app/entries_repo.py`**

Every query gains `business_id`. `create_entry` takes the rate from the business:

```python
import json

from app.settings_repo import get_settings, get_expense_config


def _row_to_entry(row):
    if row is None:
        return None
    e = dict(row)
    e["snap_expense_config"] = json.loads(e["snap_expense_config"])
    return e


def create_entry(conn, data, business_id):
    s = get_settings(conn)
    cfg = get_expense_config(conn, business_id)
    rate = conn.execute(
        "SELECT pay_per_package FROM businesses WHERE id=?",
        (business_id,)).fetchone()[0]
    cur = conn.execute(
        """INSERT INTO daily_entries
           (business_id, date, driver_id, packages, miles, hours,
            extra_expense, note, snap_pay_per_package, snap_gas_price,
            snap_mpg, snap_expense_config)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (business_id, data["date"], data.get("driver_id"), data["packages"],
         data.get("miles", 0.0), data.get("hours"), data.get("extra_expense"),
         data.get("note"), rate, s["gas_price_per_gal"], s["vehicle_mpg"],
         json.dumps(cfg)),
    )
    conn.commit()
    return cur.lastrowid


def get_entry(conn, entry_id, business_id):
    row = conn.execute(
        "SELECT * FROM daily_entries WHERE id=? AND business_id=?",
        (entry_id, business_id)).fetchone()
    return _row_to_entry(row)


def list_entries(conn, start_date, end_date, business_id):
    rows = conn.execute(
        "SELECT * FROM daily_entries WHERE business_id=? "
        "AND date >= ? AND date <= ? ORDER BY date DESC, id DESC",
        (business_id, start_date, end_date)).fetchall()
    return [_row_to_entry(r) for r in rows]


def update_entry(conn, entry_id, data, business_id):
    """Update the user-entered fields of an entry.

    Deliberately does not touch any snap_* column. The frozen rates are
    what keep past days correct when settings change later, so correcting
    a typo must never reprice the day. Returns True if a row matched.
    """
    cur = conn.execute(
        """UPDATE daily_entries
           SET date=?, driver_id=?, packages=?, miles=?, hours=?,
               extra_expense=?, note=?
           WHERE id=? AND business_id=?""",
        (data["date"], data.get("driver_id"), data["packages"],
         data.get("miles", 0.0), data.get("hours"), data.get("extra_expense"),
         data.get("note"), entry_id, business_id),
    )
    conn.commit()
    return cur.rowcount > 0


def delete_entry(conn, entry_id, business_id):
    conn.execute("DELETE FROM daily_entries WHERE id=? AND business_id=?",
                 (entry_id, business_id))
    conn.commit()


def distinct_workdays_in_month(conn, year_month, business_id):
    """Count distinct dates worked in a calendar month, e.g. '2026-06'.

    Scoped per business: a day worked under Hub A is a workday for Hub A's
    book, and a day worked under both is a workday in each.
    """
    row = conn.execute(
        "SELECT COUNT(DISTINCT date) FROM daily_entries "
        "WHERE business_id=? AND substr(date, 1, 7) = ?",
        (business_id, year_month)).fetchone()
    return row[0]
```

- [ ] **Step 5: Update `app/drivers_repo.py`**

```python
def add_driver(conn, name, business_id):
    cur = conn.execute(
        "INSERT INTO drivers (name, business_id) VALUES (?, ?)",
        (name, business_id))
    conn.commit()
    return cur.lastrowid


def list_drivers(conn, business_id, only_active=False):
    sql = "SELECT * FROM drivers WHERE business_id=?"
    if only_active:
        sql += " AND active=1"
    sql += " ORDER BY id"
    return [dict(r) for r in conn.execute(sql, (business_id,))]


def set_driver_active(conn, driver_id, active, business_id):
    conn.execute("UPDATE drivers SET active=? WHERE id=? AND business_id=?",
                 (1 if active else 0, driver_id, business_id))
    conn.commit()


def get_driver(conn, driver_id, business_id):
    row = conn.execute(
        "SELECT * FROM drivers WHERE id=? AND business_id=?",
        (driver_id, business_id)).fetchone()
    return dict(row) if row else None


def rename_driver(conn, driver_id, name, business_id):
    cur = conn.execute(
        "UPDATE drivers SET name=? WHERE id=? AND business_id=?",
        (name, driver_id, business_id))
    conn.commit()
    return cur.rowcount > 0
```

- [ ] **Step 6: Update the existing repo tests**

Add `business_id=1` to every pre-existing call in `tests/test_entries_repo.py`,
`tests/test_drivers_repo.py`, and `tests/test_settings_repo.py`. Their `conn`
fixture comes from `tests/conftest.py`, which calls `init_db`, so business #1 already
exists.

- [ ] **Step 7: Run the repo tests**

Run: `.venv/Scripts/python -m pytest tests/test_entries_repo.py tests/test_drivers_repo.py tests/test_settings_repo.py tests/test_businesses_repo.py tests/test_migration.py -v`
Expected: PASS. `tests/test_app_smoke.py` still fails; Task 4 fixes it.

- [ ] **Step 8: Commit**

```bash
git add app/settings_repo.py app/entries_repo.py app/drivers_repo.py tests/
git commit -m "feat: scope entries, drivers, and expense config to a business"
```

---

## Task 4: Routes, active business, and the switcher

**Files:**
- Modify: `app/main.py`, `app/templates/base.html`, `app/templates/settings.html`
- Modify: `app/static/app.css`
- Test: `tests/test_app_smoke.py`

**Interfaces:**
- Produces:
  - `_active_business(conn) -> dict` — self-healing active-business resolution
  - `_ctx(conn, **extra) -> dict` — base template context, merged into every render
  - `POST /business/switch` — sets the active business, redirects to the referrer

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app_smoke.py`:

```python
def _create_business(client, name):
    client.post("/businesses", data={"name": name})
    return _scalar(client, "SELECT MAX(id) FROM businesses")


class TestBusinessSwitching:
    def test_switcher_is_hidden_with_only_one_business(self, client):
        assert "business-switcher" not in client.get("/").text

    def test_switcher_appears_with_two_businesses(self, client):
        _create_business(client, "Newton Hub")
        page = client.get("/").text
        assert "business-switcher" in page
        assert "Newton Hub" in page

    def test_switching_changes_what_the_dashboard_shows(self, client):
        other = _create_business(client, "Newton Hub")
        client.post("/log", data={"date": "2026-08-01", "packages": "100",
                                  "miles": "0"})
        assert "100 packages" in client.get("/?period=all").text
        client.post("/business/switch", data={"business_id": str(other)})
        assert "0 packages" in client.get("/?period=all").text

    def test_entries_do_not_leak_across_businesses_in_history(self, client):
        other = _create_business(client, "Newton Hub")
        client.post("/log", data={"date": "2026-08-01", "packages": "77",
                                  "miles": "0"})
        client.post("/business/switch", data={"business_id": str(other)})
        assert "77" not in client.get("/history?period=all").text

    def test_csv_export_is_scoped_to_the_active_business(self, client):
        other = _create_business(client, "Newton Hub")
        client.post("/log", data={"date": "2026-08-01", "packages": "77",
                                  "miles": "0"})
        client.post("/business/switch", data={"business_id": str(other)})
        assert "77" not in client.get("/history.csv?period=all").text

    def test_editing_an_entry_from_another_business_404s(self, client):
        other = _create_business(client, "Newton Hub")
        client.post("/log", data={"date": "2026-08-01", "packages": "40",
                                  "miles": "0"})
        entry_id = _first_entry_id(client)
        client.post("/business/switch", data={"business_id": str(other)})
        assert client.get(f"/log?edit={entry_id}").status_code == 404

    def test_switching_to_an_unknown_business_is_rejected(self, client):
        assert client.post("/business/switch",
                           data={"business_id": "999"}).status_code == 404

    def test_the_pay_rate_is_per_business(self, client):
        other = _create_business(client, "Newton Hub")
        client.post("/business/switch", data={"business_id": str(other)})
        client.post("/settings", data={
            "pay_per_package": "2.25", "gas_price_per_gal": "3.40",
            "vehicle_mpg": "25"})
        client.post("/business/switch", data={"business_id": "1"})
        assert _scalar(
            client, "SELECT pay_per_package FROM businesses WHERE id=1") == 1.65
        assert _scalar(
            client,
            "SELECT pay_per_package FROM businesses WHERE id=?",
            (other,)) == 2.25

    def test_an_archived_active_business_falls_back_instead_of_crashing(
            self, client):
        other = _create_business(client, "Newton Hub")
        client.post("/business/switch", data={"business_id": str(other)})
        # Archive the business that is currently active, out of band.
        conn = get_conn(client.db_path)
        conn.execute("UPDATE businesses SET archived=1 WHERE id=?", (other,))
        conn.commit()
        conn.close()
        assert client.get("/").status_code == 200
        assert _scalar(
            client, "SELECT active_business_id FROM settings WHERE id=1") == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_app_smoke.py -k BusinessSwitching -v`
Expected: FAIL — `POST /businesses` returns 405.

- [ ] **Step 3: Add active-business resolution and shared context to `app/main.py`**

Add the import and helpers near the top, after `get_db`:

```python
from app import businesses_repo
```

```python
def _active_business(conn):
    """The business every request is scoped to.

    Self-healing on purpose: if the stored id points at a business that
    was archived or removed, fall back to the first active one and
    persist that. Otherwise a single stale id would 500 every page in the
    app with no way to fix it from the UI.
    """
    business = businesses_repo.get_business(
        conn, settings_repo.get_active_business_id(conn))
    if business is None or business["archived"]:
        active = businesses_repo.list_businesses(conn)
        if not active:
            raise HTTPException(status_code=500,
                                detail="No active business configured")
        business = active[0]
        settings_repo.set_active_business(conn, business["id"])
    return business


def _ctx(conn, **extra):
    """Base template context. Every render merges this in."""
    business = extra.pop("business", None) or _active_business(conn)
    ctx = {
        "business": business,
        "businesses": businesses_repo.list_businesses(conn),
        "settings": settings_repo.get_settings(conn),
    }
    ctx.update(extra)
    return ctx
```

Then thread `business["id"]` through every route. `dashboard` becomes:

```python
@app.get("/")
def dashboard(request: Request, period: str = "week"):
    with get_db() as conn:
        business = _active_business(conn)
        start, end = periods.range_for(period)
        entries = entries_repo.list_entries(conn, start, end, business["id"])
        mdc = _month_counts(conn, entries, business["id"])
        agg = periods.aggregate(entries, month_day_counts=mdc)
        return templates.TemplateResponse(request, "dashboard.html", _ctx(
            conn, business=business, agg=agg, period=period,
            active="dashboard"))
```

`_month_counts` gains the parameter:

```python
def _month_counts(conn, entries, business_id):
    months = {e["date"][:7] for e in entries}
    return {ym: entries_repo.distinct_workdays_in_month(conn, ym, business_id)
            for ym in months}
```

Apply the same pattern to `history`, `history_csv`, `log_form`, `log_submit`,
`log_update`, `history_delete`, `settings_page`, `settings_save`, and the three
driver routes: resolve `business = _active_business(conn)` first, pass
`business["id"]` to every repo call, and build the template context with `_ctx`.

`settings_save` now splits its writes — global fields to `settings`, rate and driver
toggle to the business:

```python
        settings_repo.update_settings(conn, {
            "vehicle_year": form.get("vehicle_year", ""),
            "vehicle_make": form.get("vehicle_make", ""),
            "vehicle_model": form.get("vehicle_model", ""),
            "track_hours": 1 if form.get("track_hours") else 0,
            "gas_price_per_gal": numbers["gas_price_per_gal"],
            "vehicle_mpg": numbers["vehicle_mpg"],
        })
        businesses_repo.update_business(conn, business["id"], {
            "name": form.get("business_name", "").strip() or business["name"],
            "pay_per_package": numbers["pay_per_package"],
            "drivers_enabled": 1 if form.get("drivers_enabled") else 0,
        })
```

`_stored_settings_values` reads the rate from the business:

```python
def _stored_settings_values(conn, business):
    s = settings_repo.get_settings(conn)
    return {"pay_per_package": str(business["pay_per_package"]),
            "gas_price_per_gal": str(s["gas_price_per_gal"]),
            "vehicle_mpg": str(s["vehicle_mpg"])}
```

Add the switch route:

```python
@app.post("/business/switch")
async def business_switch(request: Request):
    form = await request.form()
    business_id, err = parse_int(form.get("business_id"), label="Business")
    with get_db() as conn:
        if err or businesses_repo.get_business(conn, business_id) is None:
            raise HTTPException(status_code=404, detail="Business not found")
        settings_repo.set_active_business(conn, business_id)
    back = request.headers.get("referer") or "/"
    return RedirectResponse(back, status_code=303)
```

- [ ] **Step 4: Add the switcher to `app/templates/base.html`**

Between the brand and the tabs:

```html
<header class="site-header">
  <span class="brand">HubProfit</span>
  {% if businesses and businesses|length > 1 %}
  <form method="post" action="/business/switch" class="business-switcher">
    <select name="business_id" onchange="this.form.submit()">
      {% for b in businesses %}
      <option value="{{ b.id }}" {{ 'selected' if b.id == business.id }}>{{ b.name }}</option>
      {% endfor %}
    </select>
    <noscript><button type="submit">Switch</button></noscript>
  </form>
  {% elif business %}
  <span class="business-name">{{ business.name }}</span>
  {% endif %}
  <nav class="tabs">
    ...unchanged...
    <a href="/businesses" class="{{ 'on' if active=='businesses' else '' }}">Businesses</a>
  </nav>
</header>
```

The single-business case renders the name as plain text, which is what finally makes
`business_name` visible in the UI — Bug E from the spec.

Append to `app/static/app.css`:

```css
.business-switcher { margin: 0 0 0 1rem; display: inline-flex; }
.business-switcher select {
  font-size: 0.85rem;
  padding: 0.25rem 0.5rem;
  border-radius: 6px;
  border: 1px solid #cbd5e1;
  background: #fff;
}
.business-name {
  margin-left: 1rem;
  font-size: 0.85rem;
  color: #64748b;
}
```

- [ ] **Step 5: Run the suite**

Run: `.venv/Scripts/python -m pytest`
Expected: PASS. Every Phase 0 smoke test must still pass — they exercise business #1
implicitly, which is exactly the single-business path real users are on today.

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/templates/base.html app/templates/settings.html app/static/app.css tests/test_app_smoke.py
git commit -m "feat: scope every route to the active business and add the switcher"
```

---

## Task 5: The manage-businesses page

**Files:**
- Create: `app/templates/businesses.html`
- Modify: `app/main.py`, `app/static/app.css`
- Test: `tests/test_app_smoke.py`

**Interfaces:**
- Produces: `GET /businesses`, `POST /businesses`,
  `POST /businesses/{id}/rename`, `POST /businesses/{id}/archive`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app_smoke.py`:

```python
class TestManageBusinesses:
    def test_page_lists_the_current_business(self, client):
        assert "My Hub Business" in client.get("/businesses").text

    def test_creating_a_business_does_not_switch_to_it(self, client):
        client.post("/businesses", data={"name": "Newton Hub"})
        assert _scalar(
            client, "SELECT active_business_id FROM settings WHERE id=1") == 1

    def test_a_blank_business_name_is_rejected(self, client):
        r = client.post("/businesses", data={"name": "   "})
        assert r.status_code == 400
        assert _scalar(client, "SELECT COUNT(*) FROM businesses") == 1

    def test_a_business_can_be_renamed(self, client):
        client.post("/businesses/1/rename", data={"name": "Renamed Co"})
        assert "Renamed Co" in client.get("/businesses").text

    def test_archiving_hides_it_from_the_switcher(self, client):
        other = _create_business(client, "Newton Hub")
        client.post(f"/businesses/{other}/archive", data={"archived": "1"})
        assert "Newton Hub" not in client.get("/").text

    def test_the_last_business_cannot_be_archived(self, client):
        r = client.post("/businesses/1/archive", data={"archived": "1"})
        assert r.status_code == 400
        assert "only business" in r.text
        assert _scalar(
            client, "SELECT archived FROM businesses WHERE id=1") == 0

    def test_a_new_business_starts_with_no_entries_and_default_expenses(
            self, client):
        other = _create_business(client, "Newton Hub")
        client.post("/business/switch", data={"business_id": str(other)})
        assert "No entries" in client.get("/history?period=all").text
        # Fuel on by default: a day with miles must show a fuel cost, not
        # zero expenses.
        client.post("/log", data={"date": "2026-08-01", "packages": "10",
                                  "miles": "50"})
        rows = list(csv.DictReader(
            io.StringIO(client.get("/history.csv?period=all").text)))
        assert float(rows[0]["expenses"]) > 0
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_app_smoke.py -k ManageBusinesses -v`
Expected: FAIL — `GET /businesses` returns 404.

- [ ] **Step 3: Add the routes to `app/main.py`**

```python
def _render_businesses(request, conn, errors, status=200):
    return templates.TemplateResponse(request, "businesses.html", _ctx(
        conn, all_businesses=businesses_repo.list_businesses(
            conn, include_archived=True),
        errors=errors, active="businesses"), status_code=status)


@app.get("/businesses")
def businesses_page(request: Request):
    with get_db() as conn:
        return _render_businesses(request, conn, {})


@app.post("/businesses")
async def business_create(request: Request):
    form = await request.form()
    name = (form.get("name") or "").strip()
    with get_db() as conn:
        if not name:
            return _render_businesses(
                request, conn, {"name": "Business name is required."},
                status=400)
        # Deliberately does not switch to the new business - the user
        # switches when they mean to.
        businesses_repo.create_business(conn, name)
    return RedirectResponse("/businesses", status_code=303)


@app.post("/businesses/{business_id}/rename")
async def business_rename(request: Request, business_id: int):
    form = await request.form()
    name = (form.get("name") or "").strip()
    with get_db() as conn:
        if not name:
            return _render_businesses(
                request, conn, {"name": "Business name is required."},
                status=400)
        if not businesses_repo.rename_business(conn, business_id, name):
            raise HTTPException(status_code=404, detail="Business not found")
    return RedirectResponse("/businesses", status_code=303)


@app.post("/businesses/{business_id}/archive")
async def business_archive(request: Request, business_id: int):
    form = await request.form()
    archived = form.get("archived") == "1"
    with get_db() as conn:
        if businesses_repo.get_business(conn, business_id) is None:
            raise HTTPException(status_code=404, detail="Business not found")
        try:
            businesses_repo.set_archived(conn, business_id, archived)
        except businesses_repo.ActiveBusinessError as exc:
            return _render_businesses(request, conn, {"archive": str(exc)},
                                      status=400)
    return RedirectResponse("/businesses", status_code=303)
```

- [ ] **Step 4: Create `app/templates/businesses.html`**

```html
{% extends "base.html" %}

{% block content %}
<h1 class="page-title">Businesses</h1>

<p class="hint">
  Each business is its own set of books &mdash; its own pay rate, expenses, drivers,
  and logged days. Your vehicle, MPG, and gas price are shared across all of them.
  If two businesses share a cost such as insurance, enter your own split in each
  one rather than the full amount in both.
</p>

{% if errors.archive %}<p class="field-error">{{ errors.archive }}</p>{% endif %}
{% if errors.name %}<p class="field-error">{{ errors.name }}</p>{% endif %}

<div class="card">
  {% for b in all_businesses %}
  <div class="business-row {{ 'is-archived' if b.archived }}">
    <form method="post" action="/businesses/{{ b.id }}/rename">
      <input type="text" name="name" value="{{ b.name }}" required>
      <button type="submit" class="btn btn-sm btn-ghost">Rename</button>
    </form>
    <span class="text-muted">
      {{ "Fluctuating" if b.rate_model == 'tiered' else "$%.2f / package"|format(b.pay_per_package) }}
    </span>
    <form method="post" action="/businesses/{{ b.id }}/archive">
      <input type="hidden" name="archived" value="{{ '0' if b.archived else '1' }}">
      <button type="submit" class="btn btn-sm btn-ghost">
        {{ "Restore" if b.archived else "Archive" }}
      </button>
    </form>
    {% if b.id == business.id %}<span class="badge">active</span>{% endif %}
  </div>
  {% endfor %}

  <form method="post" action="/businesses" class="business-add">
    <input type="text" name="name" placeholder="New business name" required>
    <button type="submit" class="btn btn-primary">Add business</button>
  </form>
</div>

<p class="hint mt-2">
  Archiving keeps every day already logged under a business. It only hides it from
  the switcher. You can restore it at any time.
</p>
{% endblock %}
```

Append to `app/static/app.css`:

```css
.business-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.6rem 0;
  border-bottom: 1px solid #f1f5f9;
}
.business-row form { display: flex; gap: 0.5rem; margin: 0; }
.business-row.is-archived { opacity: 0.55; }
.business-add { display: flex; gap: 0.5rem; margin-top: 1rem; }
.badge {
  font-size: 0.7rem;
  background: #dbeafe;
  color: #1d4ed8;
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
}
```

- [ ] **Step 5: Run the suite**

Run: `.venv/Scripts/python -m pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/templates/businesses.html app/static/app.css tests/test_app_smoke.py
git commit -m "feat: add the manage-businesses page"
```

---

## Task 6: Documentation

**Files:**
- Modify: `app/templates/help.html`, `README.md`

- [ ] **Step 1: Add a Help/FAQ entry**

Add a new `faq-item` after the multi-driver one:

```html
  <div class="faq-item">
    <div class="faq-q">Can I track more than one Hub account?</div>
    <div class="faq-a">
      <p>Yes. Open the <strong>Businesses</strong> tab and add one. A switcher then
      appears in the header, and every page &mdash; Dashboard, Log Day, History,
      CSV export &mdash; shows only the business you have selected.</p>
      <p>Each business keeps its own <strong>pay rate, expenses, drivers, and logged
      days</strong>. Your <strong>vehicle, MPG, and gas price are shared</strong>
      across all of them, since most owners drive the same car for both.</p>
      <p>There is deliberately no combined total. If a cost such as insurance is
      shared between two businesses, enter your own split in each one &mdash; putting
      the full amount in both would count it twice.</p>
      <p>Archiving a business hides it from the switcher and keeps every day already
      logged under it. You cannot archive your only business.</p>
    </div>
  </div>
```

- [ ] **Step 2: Add it to the README feature list**

After the multi-driver bullet:

```markdown
- **Multiple Hub accounts** — track more than one business in one instance and switch between them, each with its own rate, expenses, and history
```

- [ ] **Step 3: Document the migration in the Updating section**

Add after the update commands in `## Updating`:

```markdown
> **This release upgrades your database.** The upgrade runs automatically the first
> time the new version starts, and it does not change any day you have already
> logged. It is safe to run more than once. As with any upgrade that touches
> stored data, backing up the `hubprofit_data` volume first is cheap insurance.
```

- [ ] **Step 4: Run the suite and commit**

```bash
.venv/Scripts/python -m pytest
git add app/templates/help.html README.md
git commit -m "docs: explain multiple businesses and the database upgrade"
```

---

## Verification Before Handoff

- [ ] `.venv/Scripts/python -m pytest` — full suite green.
- [ ] Migration re-verified by hand against a copy of a **v1-shaped** database:
      entry count unchanged, computed nets unchanged, `user_version` = 2.
- [ ] `init_db` run three times on the same file — no error, no duplicate rows.
- [ ] A second business created, switched to, a day logged, and confirmed absent
      from the first business's Dashboard, History, and CSV.
- [ ] The switcher does not render when only one business exists.
- [ ] No `snap_*` column written outside `create_entry`.

**Deployment note:** unlike every previous HubProfit release, this one **ships a
schema migration**. Back up the `hubprofit_data` volume before the first redeploy.
Portainer → Pull and redeploy; never check "Remove volumes".
