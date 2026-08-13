import sqlite3

SCHEMA_VERSION = 3

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


def _migrate_to_v3(conn):
    """Flat rates -> optional per-business fluctuating contracts."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rate_tiers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id INTEGER NOT NULL,
            min_packages INTEGER NOT NULL,
            max_packages INTEGER,
            rate REAL NOT NULL
        )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tiers_business "
                 "ON rate_tiers(business_id, min_packages)")
    # Existing entries are flat and stay flat. snap_rate_tiers is NULL for
    # them, so earnings keep coming from snap_pay_per_package exactly as
    # before - the upgrade cannot move a historical number.
    _add_column(conn, "daily_entries", "snap_rate_model",
                "TEXT NOT NULL DEFAULT 'flat'")
    _add_column(conn, "daily_entries", "snap_rate_tiers", "TEXT")


MIGRATIONS = {2: _migrate_to_v2, 3: _migrate_to_v3}


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
