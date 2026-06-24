# HubProfit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build HubProfit — a self-hosted web dashboard that tracks an Amazon Hub Delivery business and shows true net profit (earnings minus fuel, fixed costs, and driver pay) per day/week/month/year.

**Architecture:** Single Docker container. FastAPI serves server-rendered Jinja2 pages backed by SQLite. A pure-Python calculations module is the financial core. Each daily entry stores a frozen snapshot of the rate/gas/MPG/expense settings used, so changing Settings never alters historical days. Vehicle MPG is fetched from the free EPA fueleconomy.gov web service and cached in the DB. Chart.js (bundled static file) renders the dashboard chart.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, Jinja2, SQLite (stdlib `sqlite3`), httpx (fueleconomy client), pytest, Chart.js, Docker.

---

## File Structure

```
hub-profit/
  app/
    __init__.py
    main.py             # FastAPI app + routes (thin; delegates to repos/calculations)
    db.py               # connection + schema init + default seed
    calculations.py     # pure financial functions (no DB) — the core
    settings_repo.py    # read/write settings row + expense_config rows
    entries_repo.py     # CRUD daily_entries (+ snapshot on create)
    drivers_repo.py     # CRUD drivers
    fueleconomy.py      # EPA web service client + DB cache
    periods.py          # date-range helpers (week/month/year/all) + aggregation
    templates/
      base.html         # shared layout + tab nav
      dashboard.html
      log_day.html
      history.html
      settings.html
      help.html
    static/
      chart.min.js      # bundled Chart.js (vendored, no CDN)
      app.css
      app.js            # live calc on Log Day form, settings warning
  tests/
    conftest.py         # temp-DB fixture
    test_calculations.py
    test_settings_repo.py
    test_entries_repo.py
    test_drivers_repo.py
    test_fueleconomy.py
    test_periods.py
    test_app_smoke.py
  requirements.txt
  Dockerfile
  docker-compose.yml
  README.md
  LICENSE
  .gitignore             # already exists
```

Each module has one responsibility. `calculations.py` and `periods.py` are pure (no DB) so they are trivially unit-testable. Repos own all SQL. `main.py` stays thin.

---

### Task 1: Project scaffolding

**Files:**
- Create: `requirements.txt`
- Create: `app/__init__.py` (empty)
- Create: `tests/__init__.py` (empty)
- Create: `pytest.ini`

- [ ] **Step 1: Create `requirements.txt`**

```
fastapi==0.115.0
uvicorn[standard]==0.30.6
jinja2==3.1.4
httpx==0.27.2
pytest==8.3.3
```

- [ ] **Step 2: Create empty package markers**

Create `app/__init__.py` (empty file) and `tests/__init__.py` (empty file).

- [ ] **Step 3: Create `pytest.ini`**

```ini
[pytest]
testpaths = tests
addopts = -q
```

- [ ] **Step 4: Create and activate a virtualenv, install deps**

Run:
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```
Expected: installs succeed.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt app/__init__.py tests/__init__.py pytest.ini
git commit -m "chore: project scaffolding for HubProfit"
```

> Note: add `.venv/` to `.gitignore` if not present.

---

### Task 2: Database schema & connection

**Files:**
- Create: `app/db.py`
- Test: `tests/conftest.py`, `tests/test_settings_repo.py` (schema asserted via repo in Task 3; here we test `init_db` directly)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test** — `tests/test_db.py`

```python
import sqlite3
from app.db import init_db, get_conn


def test_init_db_creates_tables(tmp_path):
    db_path = tmp_path / "t.db"
    init_db(str(db_path))
    conn = get_conn(str(db_path))
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"settings", "expense_config", "daily_entries",
            "drivers", "mpg_cache"} <= names


def test_init_db_seeds_settings_and_expenses(tmp_path):
    db_path = tmp_path / "t.db"
    init_db(str(db_path))
    conn = get_conn(str(db_path))
    s = conn.execute("SELECT pay_per_package FROM settings WHERE id=1").fetchone()
    assert s["pay_per_package"] == 1.65
    keys = {r["key"] for r in conn.execute("SELECT key FROM expense_config")}
    assert keys == {"fuel", "vehicle_wear", "insurance", "phone", "driver"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.db'`

- [ ] **Step 3: Write minimal implementation** — `app/db.py`

```python
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
    mode TEXT NOT NULL,
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
    driver_id INTEGER,
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
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0:
        conn.execute("INSERT INTO settings (id) VALUES (1)")
    for key, enabled, mode, amount in DEFAULT_EXPENSES:
        conn.execute(
            "INSERT OR IGNORE INTO expense_config (key, enabled, mode, amount) "
            "VALUES (?, ?, ?, ?)", (key, enabled, mode, amount))
    conn.commit()
    conn.close()
```

- [ ] **Step 4: Create `tests/conftest.py` (shared temp-DB fixture)**

```python
import pytest
from app.db import init_db, get_conn


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    c = get_conn(str(db_path))
    yield c
    c.close()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_db.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add app/db.py tests/test_db.py tests/conftest.py
git commit -m "feat: sqlite schema, connection, and seed defaults"
```

---

### Task 3: Calculations module (financial core)

**Files:**
- Create: `app/calculations.py`
- Test: `tests/test_calculations.py`

This module is pure — it takes a normalized entry dict + `days_worked_in_month` and returns a breakdown. No DB access.

- [ ] **Step 1: Write the failing test** — `tests/test_calculations.py`

```python
from app.calculations import compute_entry

BASE_CONFIG = {
    "fuel": {"enabled": True, "mode": "mileage_fuel", "amount": 0.0},
    "vehicle_wear": {"enabled": False, "mode": "per_mile", "amount": 0.18},
    "insurance": {"enabled": True, "mode": "monthly", "amount": 140.0},
    "phone": {"enabled": True, "mode": "monthly", "amount": 40.0},
    "driver": {"enabled": False, "mode": "per_day", "amount": 0.0},
}


def make_entry(**over):
    e = {
        "packages": 47, "miles": 38.0, "hours": 2.5,
        "extra_expense": 0.0, "driver_id": None,
        "snap_pay_per_package": 1.65, "snap_gas_price": 3.40,
        "snap_mpg": 28.0, "snap_expense_config": BASE_CONFIG,
    }
    e.update(over)
    return e


def test_earnings():
    r = compute_entry(make_entry(), days_worked_in_month=20)
    assert round(r["earnings"], 2) == 77.55


def test_fuel_estimate():
    r = compute_entry(make_entry(), days_worked_in_month=20)
    # 38 / 28 * 3.40 = 4.6142...
    assert round(r["expenses"]["fuel"], 2) == 4.61


def test_fixed_costs_spread_across_days():
    r = compute_entry(make_entry(), days_worked_in_month=20)
    # insurance 140/20 = 7.00, phone 40/20 = 2.00
    assert round(r["expenses"]["insurance"], 2) == 7.00
    assert round(r["expenses"]["phone"], 2) == 2.00


def test_disabled_expense_excluded():
    r = compute_entry(make_entry(), days_worked_in_month=20)
    assert "vehicle_wear" not in r["expenses"]


def test_vehicle_wear_per_mile_when_enabled():
    cfg = {**BASE_CONFIG, "vehicle_wear":
           {"enabled": True, "mode": "per_mile", "amount": 0.18}}
    r = compute_entry(make_entry(snap_expense_config=cfg), days_worked_in_month=20)
    assert round(r["expenses"]["vehicle_wear"], 2) == 6.84  # 38 * 0.18


def test_net_and_hourly():
    r = compute_entry(make_entry(), days_worked_in_month=20)
    # 77.55 - (4.61 + 7.00 + 2.00) = 63.94
    assert round(r["net"], 2) == 63.94
    assert round(r["hourly"], 2) == 25.58  # 63.94 / 2.5


def test_extra_expense_subtracted():
    r = compute_entry(make_entry(extra_expense=10.0), days_worked_in_month=20)
    assert round(r["net"], 2) == 53.94


def test_hourly_none_when_no_hours():
    r = compute_entry(make_entry(hours=None), days_worked_in_month=20)
    assert r["hourly"] is None


def test_mpg_zero_safe():
    r = compute_entry(make_entry(snap_mpg=0.0), days_worked_in_month=20)
    assert r["expenses"]["fuel"] == 0.0


def test_driver_pay_only_when_assigned():
    cfg = {**BASE_CONFIG, "driver":
           {"enabled": True, "mode": "per_day", "amount": 50.0}}
    no_drv = compute_entry(make_entry(snap_expense_config=cfg),
                           days_worked_in_month=20)
    assert "driver" not in no_drv["expenses"]
    with_drv = compute_entry(make_entry(snap_expense_config=cfg, driver_id=3),
                             days_worked_in_month=20)
    assert with_drv["expenses"]["driver"] == 50.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_calculations.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.calculations'`

- [ ] **Step 3: Write minimal implementation** — `app/calculations.py`

```python
"""Pure financial calculations. No DB access.

An "entry" is a dict with: packages, miles, hours (or None),
extra_expense (or None), driver_id (or None), and the frozen snapshot
fields snap_pay_per_package, snap_gas_price, snap_mpg, and
snap_expense_config (a dict keyed by expense name).
"""


def _expense_cost(key, cfg, entry, days_worked_in_month):
    mode = cfg["mode"]
    amount = cfg["amount"]
    miles = entry["miles"]
    if mode == "mileage_fuel":
        mpg = entry["snap_mpg"]
        if mpg <= 0:
            return 0.0
        return miles / mpg * entry["snap_gas_price"]
    if mode == "per_mile":
        return miles * amount
    if mode == "monthly":
        days = days_worked_in_month if days_worked_in_month > 0 else 1
        return amount / days
    if mode == "per_day":
        if key == "driver" and entry.get("driver_id") is None:
            return None  # driver pay only applies on driver-assigned days
        return amount
    return 0.0


def compute_entry(entry, days_worked_in_month):
    earnings = entry["packages"] * entry["snap_pay_per_package"]
    expenses = {}
    for key, cfg in entry["snap_expense_config"].items():
        if not cfg.get("enabled"):
            continue
        cost = _expense_cost(key, cfg, entry, days_worked_in_month)
        if cost is None:
            continue
        expenses[key] = cost
    extra = entry.get("extra_expense") or 0.0
    total_expenses = sum(expenses.values()) + extra
    net = earnings - total_expenses
    hours = entry.get("hours")
    hourly = (net / hours) if hours else None
    return {
        "earnings": earnings,
        "expenses": expenses,
        "extra_expense": extra,
        "total_expenses": total_expenses,
        "net": net,
        "hourly": hourly,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_calculations.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: Commit**

```bash
git add app/calculations.py tests/test_calculations.py
git commit -m "feat: pure financial calculations module with snapshot support"
```

---

### Task 4: Settings repository

**Files:**
- Create: `app/settings_repo.py`
- Test: `tests/test_settings_repo.py`

- [ ] **Step 1: Write the failing test** — `tests/test_settings_repo.py`

```python
from app.settings_repo import get_settings, update_settings, \
    get_expense_config, update_expense_config


def test_get_default_settings(conn):
    s = get_settings(conn)
    assert s["pay_per_package"] == 1.65
    assert s["track_hours"] == 1


def test_update_settings(conn):
    update_settings(conn, {"pay_per_package": 1.80, "vehicle_mpg": 30.0,
                           "business_name": "JVC Vending Services LLC"})
    s = get_settings(conn)
    assert s["pay_per_package"] == 1.80
    assert s["vehicle_mpg"] == 30.0
    assert s["business_name"] == "JVC Vending Services LLC"


def test_get_expense_config_shape(conn):
    cfg = get_expense_config(conn)
    assert cfg["fuel"]["mode"] == "mileage_fuel"
    assert cfg["fuel"]["enabled"] is True
    assert cfg["vehicle_wear"]["enabled"] is False


def test_update_expense_config(conn):
    update_expense_config(conn, "insurance", enabled=True, amount=140.0)
    cfg = get_expense_config(conn)
    assert cfg["insurance"]["enabled"] is True
    assert cfg["insurance"]["amount"] == 140.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_settings_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.settings_repo'`

- [ ] **Step 3: Write minimal implementation** — `app/settings_repo.py`

```python
ALLOWED_SETTINGS = {
    "business_name", "pay_per_package", "gas_price_per_gal",
    "vehicle_year", "vehicle_make", "vehicle_model", "vehicle_mpg",
    "track_hours", "drivers_enabled",
}


def get_settings(conn):
    row = conn.execute("SELECT * FROM settings WHERE id=1").fetchone()
    return dict(row)


def update_settings(conn, values):
    fields = {k: v for k, v in values.items() if k in ALLOWED_SETTINGS}
    if not fields:
        return
    assignments = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE settings SET {assignments} WHERE id=1",
                 list(fields.values()))
    conn.commit()


def get_expense_config(conn):
    out = {}
    for r in conn.execute("SELECT key, enabled, mode, amount FROM expense_config"):
        out[r["key"]] = {
            "enabled": bool(r["enabled"]),
            "mode": r["mode"],
            "amount": r["amount"],
        }
    return out


def update_expense_config(conn, key, enabled=None, amount=None):
    if enabled is not None:
        conn.execute("UPDATE expense_config SET enabled=? WHERE key=?",
                     (1 if enabled else 0, key))
    if amount is not None:
        conn.execute("UPDATE expense_config SET amount=? WHERE key=?",
                     (amount, key))
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_settings_repo.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/settings_repo.py tests/test_settings_repo.py
git commit -m "feat: settings and expense_config repository"
```

---

### Task 5: Entries repository (with snapshot on create)

**Files:**
- Create: `app/entries_repo.py`
- Test: `tests/test_entries_repo.py`

- [ ] **Step 1: Write the failing test** — `tests/test_entries_repo.py`

```python
from app.settings_repo import update_settings, update_expense_config
from app.entries_repo import create_entry, list_entries, get_entry, \
    delete_entry


def test_create_entry_snapshots_current_settings(conn):
    update_settings(conn, {"pay_per_package": 1.65, "vehicle_mpg": 28.0,
                           "gas_price_per_gal": 3.40})
    update_expense_config(conn, "insurance", enabled=True, amount=140.0)
    eid = create_entry(conn, {"date": "2026-06-24", "packages": 47,
                              "miles": 38.0, "hours": 2.5})
    e = get_entry(conn, eid)
    assert e["snap_pay_per_package"] == 1.65
    assert e["snap_mpg"] == 28.0
    assert e["snap_expense_config"]["insurance"]["amount"] == 140.0


def test_snapshot_is_frozen_after_settings_change(conn):
    update_settings(conn, {"pay_per_package": 1.65})
    eid = create_entry(conn, {"date": "2026-06-24", "packages": 10,
                              "miles": 0})
    update_settings(conn, {"pay_per_package": 2.00})  # change rate later
    e = get_entry(conn, eid)
    assert e["snap_pay_per_package"] == 1.65  # unchanged


def test_list_entries_in_date_range(conn):
    create_entry(conn, {"date": "2026-06-01", "packages": 10, "miles": 0})
    create_entry(conn, {"date": "2026-06-15", "packages": 20, "miles": 0})
    create_entry(conn, {"date": "2026-07-01", "packages": 30, "miles": 0})
    june = list_entries(conn, "2026-06-01", "2026-06-30")
    assert [e["packages"] for e in june] == [20, 10]  # newest first


def test_delete_entry(conn):
    eid = create_entry(conn, {"date": "2026-06-24", "packages": 5, "miles": 0})
    delete_entry(conn, eid)
    assert get_entry(conn, eid) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_entries_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.entries_repo'`

- [ ] **Step 3: Write minimal implementation** — `app/entries_repo.py`

```python
import json
from app.settings_repo import get_settings, get_expense_config


def _row_to_entry(row):
    if row is None:
        return None
    e = dict(row)
    e["snap_expense_config"] = json.loads(e["snap_expense_config"])
    return e


def create_entry(conn, data):
    s = get_settings(conn)
    cfg = get_expense_config(conn)
    cur = conn.execute(
        """INSERT INTO daily_entries
           (date, driver_id, packages, miles, hours, extra_expense, note,
            snap_pay_per_package, snap_gas_price, snap_mpg, snap_expense_config)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (data["date"], data.get("driver_id"), data["packages"],
         data.get("miles", 0.0), data.get("hours"), data.get("extra_expense"),
         data.get("note"), s["pay_per_package"], s["gas_price_per_gal"],
         s["vehicle_mpg"], json.dumps(cfg)),
    )
    conn.commit()
    return cur.lastrowid


def get_entry(conn, entry_id):
    row = conn.execute("SELECT * FROM daily_entries WHERE id=?",
                       (entry_id,)).fetchone()
    return _row_to_entry(row)


def list_entries(conn, start_date, end_date):
    rows = conn.execute(
        "SELECT * FROM daily_entries WHERE date >= ? AND date <= ? "
        "ORDER BY date DESC, id DESC", (start_date, end_date)).fetchall()
    return [_row_to_entry(r) for r in rows]


def delete_entry(conn, entry_id):
    conn.execute("DELETE FROM daily_entries WHERE id=?", (entry_id,))
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_entries_repo.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/entries_repo.py tests/test_entries_repo.py
git commit -m "feat: daily entries repository with frozen settings snapshot"
```

---

### Task 6: Period helpers & aggregation

**Files:**
- Create: `app/periods.py`
- Test: `tests/test_periods.py`

- [ ] **Step 1: Write the failing test** — `tests/test_periods.py`

```python
from datetime import date
from app.periods import range_for, days_worked_in_month, aggregate

BASE_CONFIG = {
    "fuel": {"enabled": True, "mode": "mileage_fuel", "amount": 0.0},
    "insurance": {"enabled": True, "mode": "monthly", "amount": 140.0},
}


def make_entry(d, packages, miles=0.0, hours=None):
    return {"date": d, "packages": packages, "miles": miles, "hours": hours,
            "extra_expense": 0.0, "driver_id": None,
            "snap_pay_per_package": 1.65, "snap_gas_price": 3.40,
            "snap_mpg": 28.0, "snap_expense_config": BASE_CONFIG}


def test_range_for_week():
    start, end = range_for("week", date(2026, 6, 24))  # a Wednesday
    assert start == "2026-06-22"  # Monday
    assert end == "2026-06-28"    # Sunday


def test_range_for_month():
    start, end = range_for("month", date(2026, 6, 24))
    assert start == "2026-06-01"
    assert end == "2026-06-30"


def test_range_for_year():
    start, end = range_for("year", date(2026, 6, 24))
    assert (start, end) == ("2026-01-01", "2026-12-31")


def test_days_worked_in_month_counts_distinct_dates():
    entries = [make_entry("2026-06-01", 10), make_entry("2026-06-01", 5),
               make_entry("2026-06-02", 8)]
    assert days_worked_in_month(entries, "2026-06") == 2


def test_aggregate_totals_and_chart():
    entries = [make_entry("2026-06-22", 40, hours=2.0),
               make_entry("2026-06-23", 50, hours=2.5)]
    agg = aggregate(entries)
    assert agg["total_packages"] == 90
    assert round(agg["total_earnings"], 2) == 148.50  # 90 * 1.65
    assert len(agg["by_day"]) == 2
    assert agg["by_day"][0]["date"] == "2026-06-22"
    assert "net" in agg["by_day"][0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_periods.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.periods'`

- [ ] **Step 3: Write minimal implementation** — `app/periods.py`

```python
import calendar
from collections import defaultdict
from datetime import date, timedelta
from app.calculations import compute_entry


def range_for(period, today=None):
    today = today or date.today()
    if period == "week":
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
    elif period == "month":
        start = today.replace(day=1)
        last = calendar.monthrange(today.year, today.month)[1]
        end = today.replace(day=last)
    elif period == "year":
        start = date(today.year, 1, 1)
        end = date(today.year, 12, 31)
    else:  # all
        start = date(1970, 1, 1)
        end = date(9999, 12, 31)
    return start.isoformat(), end.isoformat()


def days_worked_in_month(entries, year_month):
    return len({e["date"] for e in entries
                if e["date"].startswith(year_month)})


def aggregate(entries):
    # Pre-count distinct workdays per month for fixed-cost allocation.
    month_days = {}
    for e in entries:
        ym = e["date"][:7]
        month_days.setdefault(ym, set()).add(e["date"])

    by_day = []
    totals = defaultdict(float)
    total_packages = 0
    total_hours = 0.0
    for e in sorted(entries, key=lambda x: x["date"]):
        ym = e["date"][:7]
        dwim = len(month_days[ym])
        r = compute_entry(e, dwim)
        by_day.append({"date": e["date"], "net": r["net"],
                       "earnings": r["earnings"],
                       "expenses": r["total_expenses"],
                       "packages": e["packages"]})
        totals["net"] += r["net"]
        totals["earnings"] += r["earnings"]
        totals["expenses"] += r["total_expenses"]
        total_packages += e["packages"]
        total_hours += e["hours"] or 0.0
    return {
        "by_day": by_day,
        "total_net": totals["net"],
        "total_earnings": totals["earnings"],
        "total_expenses": totals["expenses"],
        "total_packages": total_packages,
        "total_hours": total_hours,
        "hourly": (totals["net"] / total_hours) if total_hours else None,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_periods.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add app/periods.py tests/test_periods.py
git commit -m "feat: period ranges and aggregation for dashboard"
```

---

### Task 7: Drivers repository

**Files:**
- Create: `app/drivers_repo.py`
- Test: `tests/test_drivers_repo.py`

- [ ] **Step 1: Write the failing test** — `tests/test_drivers_repo.py`

```python
from app.drivers_repo import add_driver, list_drivers, set_driver_active


def test_add_and_list_drivers(conn):
    add_driver(conn, "Carlos")
    add_driver(conn, "Me")
    names = [d["name"] for d in list_drivers(conn)]
    assert names == ["Carlos", "Me"]


def test_deactivate_driver(conn):
    did = add_driver(conn, "Temp")
    set_driver_active(conn, did, False)
    active = [d["name"] for d in list_drivers(conn, only_active=True)]
    assert "Temp" not in active
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_drivers_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.drivers_repo'`

- [ ] **Step 3: Write minimal implementation** — `app/drivers_repo.py`

```python
def add_driver(conn, name):
    cur = conn.execute("INSERT INTO drivers (name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid


def list_drivers(conn, only_active=False):
    sql = "SELECT * FROM drivers"
    if only_active:
        sql += " WHERE active=1"
    sql += " ORDER BY id"
    return [dict(r) for r in conn.execute(sql)]


def set_driver_active(conn, driver_id, active):
    conn.execute("UPDATE drivers SET active=? WHERE id=?",
                 (1 if active else 0, driver_id))
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_drivers_repo.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add app/drivers_repo.py tests/test_drivers_repo.py
git commit -m "feat: drivers repository (UI gated until enabled)"
```

---

### Task 8: fueleconomy.gov client with DB cache

**Files:**
- Create: `app/fueleconomy.py`
- Test: `tests/test_fueleconomy.py`

The EPA web service returns XML. Endpoints:
`https://www.fueleconomy.gov/ws/rest/vehicle/menu/year`,
`.../make?year=YYYY`, `.../model?year=YYYY&make=MAKE`,
`.../options?year=YYYY&make=MAKE&model=MODEL` (returns vehicle ids),
`.../vehicle/{id}` (returns `comb08` = combined MPG).
We pass `Accept: application/json` to get JSON back. Results cached in `mpg_cache`.

- [ ] **Step 1: Write the failing test** — `tests/test_fueleconomy.py`

```python
from app.fueleconomy import combined_mpg_from_options


class FakeResp:
    def __init__(self, data):
        self._data = data
    def json(self):
        return self._data
    def raise_for_status(self):
        pass


def test_combined_mpg_parses_comb08(monkeypatch):
    # options returns a menuItem whose value is the vehicle id
    def fake_get(url, headers=None, params=None):
        if "options" in url:
            return FakeResp({"menuItem": {"text": "Auto (S6)", "value": "41234"}})
        return FakeResp({"comb08": "28"})
    import app.fueleconomy as fe
    monkeypatch.setattr(fe.httpx, "get", fake_get)
    mpg = combined_mpg_from_options(2019, "Toyota", "RAV4")
    assert mpg == 28.0


def test_combined_mpg_handles_list_of_options(monkeypatch):
    def fake_get(url, headers=None, params=None):
        if "options" in url:
            return FakeResp({"menuItem": [
                {"text": "FWD", "value": "100"},
                {"text": "AWD", "value": "200"}]})
        return FakeResp({"comb08": "26"})
    import app.fueleconomy as fe
    monkeypatch.setattr(fe.httpx, "get", fake_get)
    mpg = combined_mpg_from_options(2019, "Toyota", "RAV4")
    assert mpg == 26.0  # first option used
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_fueleconomy.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.fueleconomy'`

- [ ] **Step 3: Write minimal implementation** — `app/fueleconomy.py`

```python
import json
import httpx

BASE = "https://www.fueleconomy.gov/ws/rest"
HEADERS = {"Accept": "application/json"}


def _get(url, params=None):
    resp = httpx.get(url, headers=HEADERS, params=params)
    resp.raise_for_status()
    return resp.json()


def get_years():
    data = _get(f"{BASE}/vehicle/menu/year")
    return [m["value"] for m in _as_list(data.get("menuItem"))]


def get_makes(year):
    data = _get(f"{BASE}/vehicle/menu/make", params={"year": year})
    return [m["value"] for m in _as_list(data.get("menuItem"))]


def get_models(year, make):
    data = _get(f"{BASE}/vehicle/menu/model",
                params={"year": year, "make": make})
    return [m["value"] for m in _as_list(data.get("menuItem"))]


def combined_mpg_from_options(year, make, model):
    data = _get(f"{BASE}/vehicle/menu/options",
                params={"year": year, "make": make, "model": model})
    items = _as_list(data.get("menuItem"))
    if not items:
        return None
    vehicle_id = items[0]["value"]
    detail = _get(f"{BASE}/vehicle/{vehicle_id}")
    comb = detail.get("comb08")
    return float(comb) if comb not in (None, "") else None


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


# --- caching wrappers (used by routes; take a DB connection) ---

def cached_mpg(conn, year, make, model):
    key = f"mpg:{year}:{make}:{model}"
    row = conn.execute("SELECT payload FROM mpg_cache WHERE cache_key=?",
                       (key,)).fetchone()
    if row:
        return json.loads(row["payload"])
    mpg = combined_mpg_from_options(year, make, model)
    conn.execute("INSERT OR REPLACE INTO mpg_cache (cache_key, payload) "
                 "VALUES (?, ?)", (key, json.dumps(mpg)))
    conn.commit()
    return mpg
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_fueleconomy.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add app/fueleconomy.py tests/test_fueleconomy.py
git commit -m "feat: fueleconomy.gov MPG client with DB cache"
```

---

### Task 9: FastAPI app + routes + smoke tests

**Files:**
- Create: `app/main.py`
- Test: `tests/test_app_smoke.py`

Routes:
- `GET /` → dashboard (query `?period=week|month|year|all`)
- `GET /log` → log day form; `POST /log` → create entry, redirect to `/`
- `GET /history` → table; `POST /history/delete/{id}` → delete, redirect
- `GET /settings` → settings page; `POST /settings` → update settings + expenses
- `GET /help` → help/FAQ
- `GET /api/makes?year=` and `/api/models?year=&make=` and
  `POST /api/lookup_mpg` → JSON for the Settings vehicle picker (uses cache)

The DB path comes from env `HUBPROFIT_DB` (default `data/hub.db`); `init_db`
is called on startup.

- [ ] **Step 1: Write the failing test** — `tests/test_app_smoke.py`

```python
import importlib
from fastapi.testclient import TestClient


def make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("HUBPROFIT_DB", str(tmp_path / "smoke.db"))
    import app.main as main
    importlib.reload(main)
    return TestClient(main.app)


def test_pages_load(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    for path in ["/", "/log", "/history", "/settings", "/help"]:
        assert client.get(path).status_code == 200


def test_log_day_roundtrip(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    resp = client.post("/log", data={
        "date": "2026-06-24", "packages": "47", "miles": "38",
        "hours": "2.5"}, follow_redirects=False)
    assert resp.status_code in (302, 303)
    history = client.get("/history")
    assert "47" in history.text


def test_settings_update(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    resp = client.post("/settings", data={
        "business_name": "JVC Vending Services LLC",
        "pay_per_package": "1.65", "gas_price_per_gal": "3.40",
        "vehicle_year": "2019", "vehicle_make": "Toyota",
        "vehicle_model": "RAV4", "vehicle_mpg": "28",
        "track_hours": "1"}, follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "JVC Vending Services LLC" in client.get("/settings").text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_app_smoke.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Write minimal implementation** — `app/main.py`

```python
import os
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import init_db, get_conn
from app import settings_repo, entries_repo, drivers_repo, periods, fueleconomy

DB_PATH = os.environ.get("HUBPROFIT_DB", "data/hub.db")
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
init_db(DB_PATH)

BASE_DIR = Path(__file__).parent
app = FastAPI(title="HubProfit")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def db():
    return get_conn(DB_PATH)


def _f(val, default=None):
    return float(val) if val not in (None, "") else default


@app.get("/")
def dashboard(request: Request, period: str = "week"):
    conn = db()
    start, end = periods.range_for(period)
    entries = entries_repo.list_entries(conn, start, end)
    agg = periods.aggregate(entries)
    s = settings_repo.get_settings(conn)
    conn.close()
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "agg": agg, "period": period, "settings": s,
        "active": "dashboard"})


@app.get("/log")
def log_form(request: Request):
    conn = db()
    s = settings_repo.get_settings(conn)
    cfg = settings_repo.get_expense_config(conn)
    drivers = drivers_repo.list_drivers(conn, only_active=True)
    conn.close()
    return templates.TemplateResponse("log_day.html", {
        "request": request, "settings": s, "expense_config": cfg,
        "drivers": drivers, "today": date.today().isoformat(),
        "active": "log"})


@app.post("/log")
def log_submit(date: str = Form(...), packages: int = Form(...),
               miles: float = Form(0.0), hours: str = Form(""),
               extra_expense: str = Form(""), driver_id: str = Form(""),
               note: str = Form("")):
    conn = db()
    entries_repo.create_entry(conn, {
        "date": date, "packages": packages, "miles": miles,
        "hours": _f(hours), "extra_expense": _f(extra_expense),
        "driver_id": int(driver_id) if driver_id else None,
        "note": note or None})
    conn.close()
    return RedirectResponse("/", status_code=303)


@app.get("/history")
def history(request: Request, period: str = "all"):
    conn = db()
    start, end = periods.range_for(period)
    entries = entries_repo.list_entries(conn, start, end)
    # attach computed net per entry for the table
    month_days = {}
    for e in entries:
        month_days.setdefault(e["date"][:7], set()).add(e["date"])
    from app.calculations import compute_entry
    rows = []
    for e in entries:
        r = compute_entry(e, len(month_days[e["date"][:7]]))
        rows.append({**e, "computed": r})
    conn.close()
    return templates.TemplateResponse("history.html", {
        "request": request, "rows": rows, "period": period,
        "active": "history"})


@app.post("/history/delete/{entry_id}")
def history_delete(entry_id: int):
    conn = db()
    entries_repo.delete_entry(conn, entry_id)
    conn.close()
    return RedirectResponse("/history", status_code=303)


@app.get("/settings")
def settings_page(request: Request):
    conn = db()
    s = settings_repo.get_settings(conn)
    cfg = settings_repo.get_expense_config(conn)
    conn.close()
    return templates.TemplateResponse("settings.html", {
        "request": request, "settings": s, "expense_config": cfg,
        "active": "settings"})


@app.post("/settings")
async def settings_save(request: Request):
    form = await request.form()
    conn = db()
    settings_repo.update_settings(conn, {
        "business_name": form.get("business_name", ""),
        "pay_per_package": _f(form.get("pay_per_package"), 1.65),
        "gas_price_per_gal": _f(form.get("gas_price_per_gal"), 3.40),
        "vehicle_year": form.get("vehicle_year", ""),
        "vehicle_make": form.get("vehicle_make", ""),
        "vehicle_model": form.get("vehicle_model", ""),
        "vehicle_mpg": _f(form.get("vehicle_mpg"), 25.0),
        "track_hours": 1 if form.get("track_hours") else 0,
        "drivers_enabled": 1 if form.get("drivers_enabled") else 0,
    })
    for key in ("fuel", "vehicle_wear", "insurance", "phone", "driver"):
        settings_repo.update_expense_config(
            conn, key,
            enabled=bool(form.get(f"exp_{key}_enabled")),
            amount=_f(form.get(f"exp_{key}_amount"), 0.0))
    conn.close()
    return RedirectResponse("/settings", status_code=303)


@app.get("/help")
def help_page(request: Request):
    return templates.TemplateResponse("help.html", {
        "request": request, "active": "help"})


@app.get("/api/makes")
def api_makes(year: str):
    return JSONResponse(fueleconomy.get_makes(year))


@app.get("/api/models")
def api_models(year: str, make: str):
    return JSONResponse(fueleconomy.get_models(year, make))


@app.post("/api/lookup_mpg")
def api_lookup_mpg(year: str = Form(...), make: str = Form(...),
                   model: str = Form(...)):
    conn = db()
    mpg = fueleconomy.cached_mpg(conn, year, make, model)
    conn.close()
    return JSONResponse({"mpg": mpg})
```

- [ ] **Step 4: Create the templates and static assets (Task 10 builds these in full).** For now create minimal placeholder templates so smoke tests pass, then replace in Task 10.

Create `app/templates/base.html`:

```html
<!doctype html>
<html><head><meta charset="utf-8"><title>HubProfit</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="/static/app.css"></head>
<body>
<nav class="tabs">
  <a href="/" class="{{ 'on' if active=='dashboard' }}">Dashboard</a>
  <a href="/log" class="{{ 'on' if active=='log' }}">Log Day</a>
  <a href="/history" class="{{ 'on' if active=='history' }}">History</a>
  <a href="/settings" class="{{ 'on' if active=='settings' }}">Settings</a>
  <a href="/help" class="{{ 'on' if active=='help' }}">Help/FAQ</a>
</nav>
<main>{% block content %}{% endblock %}</main>
</body></html>
```

Create minimal `dashboard.html`, `log_day.html`, `history.html`, `settings.html`,
`help.html` each extending base with just enough to pass smoke tests, e.g.
`history.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>History</h1>
<table><tbody>
{% for r in rows %}
<tr><td>{{ r.date }}</td><td>{{ r.packages }}</td>
<td>{{ '%.2f'|format(r.computed.net) }}</td>
<td><form method="post" action="/history/delete/{{ r.id }}">
<button>Delete</button></form></td></tr>
{% endfor %}
</tbody></table>
{% endblock %}
```

`settings.html` minimal must render `{{ settings.business_name }}` and a form
posting to `/settings` with the field names used in `settings_save`.
Create `app/static/app.css` (empty for now) and `app/static/app.js` (empty),
and place a vendored `app/static/chart.min.js` (download Chart.js UMD build).

- [ ] **Step 5: Run smoke tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_app_smoke.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Run the full suite**

Run: `.venv/Scripts/python -m pytest -v`
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add app/main.py app/templates app/static tests/test_app_smoke.py
git commit -m "feat: FastAPI routes and minimal templates (smoke-tested)"
```

---

### Task 10: Full UI — templates, CSS, Chart.js, live calc

**Files:**
- Modify: `app/templates/*.html`
- Modify: `app/static/app.css`, `app/static/app.js`

Build out the approved design: Dashboard layout "B" (net-profit hero + supporting
stats + Chart.js bar chart of `agg.by_day` net), the Log Day form with live
auto-calc (mirror `calculations.py` math in `app.js`), the History table with
period filter and CSV export link, the Settings page (pay, vehicle picker calling
`/api/makes`,`/api/models`,`/api/lookup_mpg`, expense toggles + amounts, and the
rate-change confirm warning), and the Help/FAQ page explaining every number.

- [ ] **Step 1: Build `dashboard.html`** — net-profit hero, period switcher
  (links to `/?period=week|month|year|all`), stat tiles
  (`agg.total_packages`, `agg.hourly`, `agg.total_earnings`,
  `agg.total_expenses`), and a `<canvas id="chart">` rendered by
  `app.js` from a JSON script tag of `agg.by_day`.

- [ ] **Step 2: Build `log_day.html`** — fields date/packages/miles/hours
  (shown only if `settings.track_hours`)/extra_expense/driver (shown only if
  `settings.drivers_enabled`), plus a live result box. `app.js` recomputes
  earnings = packages × `settings.pay_per_package`, fuel = miles ÷
  `settings.vehicle_mpg` × `settings.gas_price_per_gal`, on input.

- [ ] **Step 3: Build `settings.html`** — all settings fields; cascading
  Year/Make/Model dropdowns; "Look up MPG" button → `POST /api/lookup_mpg`
  fills the mpg field; expense toggles + amount inputs named
  `exp_<key>_enabled` / `exp_<key>_amount`; JS `confirm()` on submit warning
  "This applies to future entries only. Days already logged keep their saved rate."

- [ ] **Step 4: Build `history.html`** — period filter, full table (date,
  packages, miles, earnings, expenses, net, $/hr), delete buttons, and a
  "Download CSV" link to a new `GET /history.csv` route (add it to `main.py`,
  streaming the same rows).

- [ ] **Step 5: Build `help.html`** — plain-language FAQ: what HubProfit is,
  how the fuel estimate works, what "frozen rate" means, how fixed costs are
  spread, how to fill in Settings, and that it's free/self-hostable.

- [ ] **Step 6: Style in `app.css`** — match the approved mockups (blue accent
  `#2563eb`, card layout, the net-profit hero gradient). Keep it one small file.

- [ ] **Step 7: Manual verification**

Run: `.venv/Scripts/python -m uvicorn app.main:app --reload`
Open `http://localhost:8000`, add a day on `/log`, confirm the dashboard hero,
chart, history table, CSV download, and the Settings vehicle MPG lookup all work.

- [ ] **Step 8: Run full test suite**

Run: `.venv/Scripts/python -m pytest -v`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add app/templates app/static app/main.py
git commit -m "feat: full HubProfit UI — dashboard, log, history, settings, help"
```

---

### Task 11: Dockerize (single container)

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`

- [ ] **Step 1: Create `Dockerfile`**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
ENV HUBPROFIT_DB=/data/hub.db
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Create `docker-compose.yml`**

```yaml
services:
  hubprofit:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - hubprofit_data:/data
    restart: unless-stopped
volumes:
  hubprofit_data:
```

- [ ] **Step 3: Build and run**

Run: `docker compose up --build -d`
Open `http://localhost:8000`. Confirm a logged day persists after
`docker compose restart` (named volume holds the DB — never remove volumes on
redeploy).

- [ ] **Step 4: Commit**

```bash
git add Dockerfile docker-compose.yml
git commit -m "feat: single-container Docker deploy with persistent volume"
```

---

### Task 12: Public-release docs (README + MIT license)

**Files:**
- Create: `README.md`
- Create: `LICENSE`

- [ ] **Step 1: Create `LICENSE`** — standard MIT text, copyright
  "2026 JVC Vending Services LLC".

- [ ] **Step 2: Create `README.md`** — what HubProfit is (Amazon Hub Delivery
  profit tracker), a screenshot, Quick Start (`git clone` →
  `docker compose up -d` → open `http://localhost:8000` → set your rate/vehicle/
  expenses in Settings), feature list, and a "your data stays on your machine"
  note. No secrets, no real config values.

- [ ] **Step 3: Verify no secrets are committed**

Run: `git ls-files | grep -Ei 'hub.db|.env$'`
Expected: no output (DB and env files are gitignored).

- [ ] **Step 4: Commit**

```bash
git add README.md LICENSE
git commit -m "docs: MIT license and README for public release"
```

---

## Self-Review

**Spec coverage:**
- Net profit dashboard (hero layout B) → Tasks 6, 9, 10 ✓
- Daily-summary logging → Tasks 5, 9, 10 ✓
- $1.65 default rate → Task 2 seed ✓
- Mileage-based fuel estimate → Task 3 (`mileage_fuel`) ✓
- fueleconomy.gov MPG lookup + cache → Task 8 ✓
- Frozen historical rate snapshot → Task 5 + Task 3 ✓
- Rate-change warning → Task 10 Step 3 ✓
- Fixed-cost monthly spread across workdays → Task 3 + Task 6 ✓
- Optional hours / $-per-hour → Task 2 (`track_hours`), Tasks 3, 10 ✓
- Driver support designed-in, UI gated → Tasks 2, 7, 9, 10 ✓
- Tabs: Dashboard/Log/History/Settings/Help → Tasks 9, 10 ✓
- CSV export → Task 10 Step 4 ✓
- Single Docker container + persistent volume → Task 11 ✓
- Public GitHub release (MIT, README, no secrets) → Task 12 ✓
- Gas-widget stretch feature → intentionally NOT in v1 (spec §11) ✓
- Tests for calculations, repos, fueleconomy, smoke → Tasks 3–9 ✓

**Placeholder scan:** Minimal templates in Task 9 are explicitly replaced in
Task 10; no unresolved TODOs remain in shipped code.

**Type consistency:** `compute_entry(entry, days_worked_in_month)` signature is
consistent across Tasks 3, 6, 9. `snap_expense_config` is a dict everywhere
(JSON-encoded only at the DB boundary in `entries_repo`). Expense keys
(`fuel, vehicle_wear, insurance, phone, driver`) match across db seed, repo,
calculations, and the settings form field names (`exp_<key>_enabled/amount`).
