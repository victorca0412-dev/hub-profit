import sqlite3

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


def get_conn(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str) -> None:
    conn = get_conn(db_path)
    try:
        conn.executescript(SCHEMA)
        with conn:  # explicit transaction: seed commits atomically or rolls back
            if conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0:
                conn.execute("INSERT INTO settings (id) VALUES (1)")
            for key, enabled, mode, amount in DEFAULT_EXPENSES:
                conn.execute(
                    "INSERT OR IGNORE INTO expense_config (key, enabled, mode, amount) "
                    "VALUES (?, ?, ?, ?)", (key, enabled, mode, amount))
    finally:
        conn.close()
