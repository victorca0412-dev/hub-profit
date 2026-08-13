# HubProfit Fluctuating Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support the older Amazon Hub contract where the per-package rate depends on the day's package count, per business, without repricing a single existing day.

**Architecture:** Migration v3 adds a `rate_tiers` table and two snapshot columns. Each entry freezes the whole tier *table*, not the resolved rate, so editing a day's package count reprices it honestly against that day's contract. `calculations` gains one lookup function; everything else in the module is untouched.

**Tech Stack:** Python 3.12 (container), FastAPI, Jinja2, SQLite, pytest. No new dependencies.

## Global Constraints

- Branch `feat/tiered-contracts`, already checked out. Base branch is `master`.
- Run tests with `.venv/Scripts/python -m pytest` from `C:\Users\Victor\hub-profit`.
- The 155 tests from Phases 0–2 must continue to pass.
- **Whole-block lookup, never graduated brackets.** The day's total count selects one
  tier and every package that day pays that tier's rate. A 45-package day on tiers
  1–20/21–40/41+ at $2.25/$1.95/$1.65 earns 45 × $1.65 = **$74.25**, not $92.25.
- **Migrations use explicit `BEGIN` and never `executescript()`** — see `db.migrate`.
- **No `ALTER TABLE ... DROP COLUMN`.**
- **No `snap_*` column is ever written by an update path.**
- Flat-rate businesses must behave exactly as they do today.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/db.py` (modify) | `rate_tiers` table, snapshot columns, migration v3. |
| `app/businesses_repo.py` (modify) | Tier read/replace. |
| `app/calculations.py` (modify) | `rate_for_entry`, `earnings_for`; `compute_entry` reports the rate. |
| `app/entries_repo.py` (modify) | Snapshot the rate model and tier table. |
| `app/main.py` (modify) | Parse and validate the tier form; pass tiers to templates. |
| `app/templates/settings.html` (modify) | Contract type + tier editor. |
| `app/templates/history.html` (modify) | Effective-rate column. |
| `app/static/app.js` (modify) | Tier-aware live estimate. |
| `tests/test_tiers.py` (create) | Tier lookup and snapshot behaviour. |

---

## Task 1: Migration v3 and tier storage

**Files:** Modify `app/db.py`, `app/businesses_repo.py`. Test: `tests/test_tiers.py`.

**Interfaces:**
- `SCHEMA_VERSION = 3`, `_migrate_to_v3(conn)`, registered in `MIGRATIONS`
- `businesses_repo.get_tiers(conn, business_id) -> list[dict]` with keys
  `min_packages`, `max_packages` (None = unbounded), `rate`, ordered ascending
- `businesses_repo.replace_tiers(conn, business_id, tiers) -> None` where `tiers` is
  a list of `(max_packages | None, rate)` pairs in order

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tiers.py`:

```python
import pytest

from app import businesses_repo as repo
from app.db import init_db, get_conn


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    c = get_conn(path)
    yield c
    c.close()


TIERS = [(20, 2.25), (40, 1.95), (None, 1.65)]


class TestTierStorage:
    def test_a_new_business_has_no_tiers(self, conn):
        assert repo.get_tiers(conn, 1) == []

    def test_replace_tiers_chains_the_lower_bounds(self, conn):
        repo.replace_tiers(conn, 1, TIERS)
        rows = repo.get_tiers(conn, 1)
        assert [(r["min_packages"], r["max_packages"], r["rate"])
                for r in rows] == [(1, 20, 2.25), (21, 40, 1.95),
                                   (41, None, 1.65)]

    def test_replace_tiers_is_a_full_replacement(self, conn):
        repo.replace_tiers(conn, 1, TIERS)
        repo.replace_tiers(conn, 1, [(None, 1.80)])
        rows = repo.get_tiers(conn, 1)
        assert len(rows) == 1
        assert rows[0]["min_packages"] == 1
        assert rows[0]["max_packages"] is None

    def test_tiers_are_per_business(self, conn):
        other = repo.create_business(conn, "Newton Hub")
        repo.replace_tiers(conn, 1, TIERS)
        assert repo.get_tiers(conn, other) == []


class TestMigrationV3:
    def test_version_is_three(self, conn):
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3

    def test_existing_entries_default_to_flat(self, conn):
        from app import entries_repo
        eid = entries_repo.create_entry(
            conn, {"date": "2026-08-01", "packages": 10, "miles": 0},
            business_id=1)
        e = entries_repo.get_entry(conn, eid, business_id=1)
        assert e["snap_rate_model"] == "flat"
        assert e["snap_rate_tiers"] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_tiers.py -v`
Expected: FAIL — `module 'app.businesses_repo' has no attribute 'get_tiers'`

- [ ] **Step 3: Add migration v3 to `app/db.py`**

Change `SCHEMA_VERSION` to `3`, add the function, and register it:

```python
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
```

- [ ] **Step 4: Add tier storage to `app/businesses_repo.py`**

```python
def get_tiers(conn, business_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM rate_tiers WHERE business_id=? ORDER BY min_packages",
        (business_id,))]


def replace_tiers(conn, business_id, tiers):
    """Replace a business's whole tier table.

    `tiers` is an ordered list of (max_packages | None, rate). Lower
    bounds are derived here rather than stored from the form: the first
    tier always starts at 1 and each next starts one above the previous
    ceiling, which makes a gap or an overlap unrepresentable.
    """
    conn.execute("DELETE FROM rate_tiers WHERE business_id=?", (business_id,))
    low = 1
    for max_packages, rate in tiers:
        conn.execute(
            "INSERT INTO rate_tiers (business_id, min_packages, "
            "max_packages, rate) VALUES (?,?,?,?)",
            (business_id, low, max_packages, rate))
        if max_packages is None:
            break  # an unbounded tier must be the last one
        low = max_packages + 1
    conn.commit()
```

- [ ] **Step 5: Run to verify pass, then the whole suite**

Run: `.venv/Scripts/python -m pytest tests/test_tiers.py -v`
Expected: PASS, 6 tests.

Run: `.venv/Scripts/python -m pytest`
Expected: PASS, 161 tests. The v2 migration tests must still pass — a database
upgrading straight from v1 now runs v2 then v3 in sequence.

- [ ] **Step 6: Commit**

```bash
git add app/db.py app/businesses_repo.py tests/test_tiers.py
git commit -m "feat: add rate_tiers storage and migration v3"
```

---

## Task 2: The earnings engine

**Files:** Modify `app/calculations.py`, `app/entries_repo.py`. Test: `tests/test_tiers.py`, `tests/test_calculations.py`.

**Interfaces:**
- `calculations.rate_for_entry(entry) -> float` — the per-package rate this entry
  actually pays
- `calculations.earnings_for(entry) -> float`
- `compute_entry(...)` result gains a `"rate"` key
- `entries_repo.create_entry` snapshots `snap_rate_model` and `snap_rate_tiers`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tiers.py`:

```python
def _entry(packages, tiers=None, flat=1.65):
    return {"packages": packages, "miles": 0.0, "hours": None,
            "extra_expense": None, "driver_id": None,
            "snap_pay_per_package": flat, "snap_gas_price": 3.40,
            "snap_mpg": 28.0, "snap_expense_config": {},
            "snap_rate_model": "tiered" if tiers else "flat",
            "snap_rate_tiers": tiers}


TIER_ROWS = [{"min_packages": 1, "max_packages": 20, "rate": 2.25},
             {"min_packages": 21, "max_packages": 40, "rate": 1.95},
             {"min_packages": 41, "max_packages": None, "rate": 1.65}]


class TestTierLookup:
    @pytest.mark.parametrize("packages,expected_rate", [
        (1, 2.25), (19, 2.25), (20, 2.25),      # first tier, incl. ceiling
        (21, 1.95), (39, 1.95), (40, 1.95),     # middle tier, both edges
        (41, 1.65), (45, 1.65), (5000, 1.65),   # open-ended final tier
    ])
    def test_boundaries_select_the_right_tier(self, packages, expected_rate):
        from app.calculations import rate_for_entry
        assert rate_for_entry(_entry(packages, TIER_ROWS)) == expected_rate

    def test_whole_block_pays_one_rate_not_brackets(self):
        from app.calculations import earnings_for
        # The decision this whole feature rests on. Graduated brackets
        # would give 20*2.25 + 20*1.95 + 5*1.65 = 92.25.
        assert earnings_for(_entry(45, TIER_ROWS)) == pytest.approx(74.25)

    def test_a_small_block_pays_more_per_package(self):
        from app.calculations import earnings_for
        assert earnings_for(_entry(18, TIER_ROWS)) == pytest.approx(40.50)

    def test_zero_packages_earns_zero(self):
        from app.calculations import earnings_for
        assert earnings_for(_entry(0, TIER_ROWS)) == 0

    def test_flat_entries_ignore_any_tiers(self):
        from app.calculations import earnings_for
        e = _entry(45, None, flat=1.65)
        assert earnings_for(e) == pytest.approx(74.25)

    def test_a_count_matching_no_tier_falls_back_to_the_flat_rate(self):
        from app.calculations import earnings_for
        # A malformed table must not silently zero out a day's earnings.
        broken = [{"min_packages": 50, "max_packages": 60, "rate": 9.99}]
        assert earnings_for(_entry(10, broken)) == pytest.approx(16.50)

    def test_an_empty_tier_table_falls_back_to_the_flat_rate(self):
        from app.calculations import earnings_for
        assert earnings_for(_entry(10, [])) == pytest.approx(16.50)


class TestTierSnapshots:
    def test_a_tiered_entry_freezes_the_whole_table(self, conn):
        from app import entries_repo
        repo.update_business(conn, 1, {"rate_model": "tiered"})
        repo.replace_tiers(conn, 1, TIERS)
        eid = entries_repo.create_entry(
            conn, {"date": "2026-08-01", "packages": 45, "miles": 0},
            business_id=1)
        e = entries_repo.get_entry(conn, eid, business_id=1)
        assert e["snap_rate_model"] == "tiered"
        assert len(e["snap_rate_tiers"]) == 3

    def test_changing_tiers_later_does_not_reprice_a_logged_day(self, conn):
        from app import entries_repo
        from app.calculations import earnings_for
        repo.update_business(conn, 1, {"rate_model": "tiered"})
        repo.replace_tiers(conn, 1, TIERS)
        eid = entries_repo.create_entry(
            conn, {"date": "2026-08-01", "packages": 45, "miles": 0},
            business_id=1)
        repo.replace_tiers(conn, 1, [(None, 0.50)])
        e = entries_repo.get_entry(conn, eid, business_id=1)
        assert earnings_for(e) == pytest.approx(74.25)

    def test_editing_the_count_reprices_against_the_frozen_table(self, conn):
        from app import entries_repo
        from app.calculations import earnings_for
        repo.update_business(conn, 1, {"rate_model": "tiered"})
        repo.replace_tiers(conn, 1, TIERS)
        eid = entries_repo.create_entry(
            conn, {"date": "2026-08-01", "packages": 45, "miles": 0},
            business_id=1)
        # 45 -> 38 moves the day from the 41+ tier into the 21-40 tier.
        # The rate moves because the count moved; the contract did not.
        entries_repo.update_entry(
            conn, eid, {"date": "2026-08-01", "packages": 38, "miles": 0},
            business_id=1)
        e = entries_repo.get_entry(conn, eid, business_id=1)
        assert earnings_for(e) == pytest.approx(38 * 1.95)
        assert e["snap_rate_model"] == "tiered"
        assert len(e["snap_rate_tiers"]) == 3
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_tiers.py -v`
Expected: FAIL — `cannot import name 'rate_for_entry'`

- [ ] **Step 3: Update `app/calculations.py`**

Add above `compute_entry`:

```python
def _rate_from_tiers(packages, tiers):
    """Whole-block lookup: the count picks one tier, all packages pay it.

    Deliberately NOT graduated brackets. On tiers 1-20/21-40/41+ at
    $2.25/$1.95/$1.65 a 45-package day earns 45 x $1.65 = $74.25, not
    $92.25. Amazon's block offers priced the whole block by its size,
    which is why smaller blocks paid more per package.
    """
    for tier in tiers or []:
        low = tier["min_packages"]
        high = tier["max_packages"]
        if packages >= low and (high is None or packages <= high):
            return tier["rate"]
    return None


def rate_for_entry(entry):
    """The per-package rate this entry actually pays."""
    if entry.get("snap_rate_model") == "tiered":
        rate = _rate_from_tiers(entry["packages"],
                                entry.get("snap_rate_tiers"))
        if rate is not None:
            return rate
        # A malformed or empty tier table must not zero out a real day's
        # earnings. Every entry carries a flat rate to fall back on.
    return entry["snap_pay_per_package"]


def earnings_for(entry):
    return entry["packages"] * rate_for_entry(entry)
```

In `compute_entry`, replace the earnings line and add the rate to the result:

```python
def compute_entry(entry, days_worked_in_month, entries_on_date=1):
    earnings = earnings_for(entry)
```

```python
    return {
        "earnings": earnings,
        "rate": rate_for_entry(entry),
        "expenses": expenses,
        ...
    }
```

- [ ] **Step 4: Snapshot the contract in `app/entries_repo.py`**

`create_entry` reads the model and tiers alongside the rate:

```python
def create_entry(conn, data, business_id):
    s = get_settings(conn)
    cfg = get_expense_config(conn, business_id)
    b = conn.execute(
        "SELECT pay_per_package, rate_model FROM businesses WHERE id=?",
        (business_id,)).fetchone()
    tiers = None
    if b["rate_model"] == "tiered":
        rows = conn.execute(
            "SELECT min_packages, max_packages, rate FROM rate_tiers "
            "WHERE business_id=? ORDER BY min_packages",
            (business_id,)).fetchall()
        # Freeze the whole table, not the resolved rate. Storing the rate
        # would pin a 45-package price onto a day later corrected to 38.
        tiers = json.dumps([dict(r) for r in rows]) if rows else None
    cur = conn.execute(
        """INSERT INTO daily_entries
           (business_id, date, driver_id, packages, miles, hours,
            extra_expense, note, snap_pay_per_package, snap_gas_price,
            snap_mpg, snap_expense_config, snap_rate_model, snap_rate_tiers)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (business_id, data["date"], data.get("driver_id"), data["packages"],
         data.get("miles", 0.0), data.get("hours"), data.get("extra_expense"),
         data.get("note"), b["pay_per_package"], s["gas_price_per_gal"],
         s["vehicle_mpg"], json.dumps(cfg),
         b["rate_model"] if tiers else "flat", tiers),
    )
    conn.commit()
    return cur.lastrowid
```

`_row_to_entry` decodes the tier JSON:

```python
def _row_to_entry(row):
    if row is None:
        return None
    e = dict(row)
    e["snap_expense_config"] = json.loads(e["snap_expense_config"])
    if e.get("snap_rate_tiers"):
        e["snap_rate_tiers"] = json.loads(e["snap_rate_tiers"])
    return e
```

Note `rate_model` is stored as `'flat'` when the tier table is empty, so a business
switched to tiered before any tiers exist still prices its days sensibly.

- [ ] **Step 5: Run the suite**

Run: `.venv/Scripts/python -m pytest`
Expected: PASS. Existing `test_calculations.py` tests pass unchanged because
`snap_rate_model` is absent from their fixtures and `.get()` returns None.

- [ ] **Step 6: Commit**

```bash
git add app/calculations.py app/entries_repo.py tests/test_tiers.py
git commit -m "feat: price tiered days by whole-block lookup"
```

---

## Task 3: The tier editor

**Files:** Modify `app/main.py`, `app/templates/settings.html`, `app/static/app.css`. Test: `tests/test_app_smoke.py`.

**Interfaces:**
- `_parse_tier_form(form) -> (tiers, error | None)` in `app/main.py`, where `tiers`
  is a list of `(max_packages | None, rate)`
- `settings.html` receives `tiers` and `business.rate_model`
- Form fields: repeated `tier_to` and `tier_rate` inputs; the final `tier_to` is
  blank, meaning unbounded

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app_smoke.py`:

```python
class TestTierEditor:
    BASE = {"gas_price_per_gal": "3.40", "vehicle_mpg": "25",
            "pay_per_package": "1.65"}

    def _save_tiered(self, client, tos, rates):
        data = dict(self.BASE)
        data["rate_model"] = "tiered"
        return client.post("/settings", data=[
            *data.items(),
            *[("tier_to", t) for t in tos],
            *[("tier_rate", r) for r in rates]])

    def test_saving_tiers_stores_chained_bounds(self, client):
        self._save_tiered(client, ["20", "40", ""], ["2.25", "1.95", "1.65"])
        rows = _all(client, "SELECT min_packages, max_packages, rate "
                            "FROM rate_tiers ORDER BY min_packages")
        assert rows == [(1, 20, 2.25), (21, 40, 1.95), (41, None, 1.65)]

    def test_a_tiered_day_uses_the_tier_rate(self, client):
        self._save_tiered(client, ["20", "40", ""], ["2.25", "1.95", "1.65"])
        client.post("/log", data={"date": "2026-08-01", "packages": "45",
                                  "miles": "0"})
        rows = list(csv.DictReader(
            io.StringIO(client.get("/history.csv?period=all").text)))
        assert float(rows[0]["earnings"]) == 74.25

    def test_a_small_block_pays_the_higher_rate(self, client):
        self._save_tiered(client, ["20", "40", ""], ["2.25", "1.95", "1.65"])
        client.post("/log", data={"date": "2026-08-01", "packages": "18",
                                  "miles": "0"})
        rows = list(csv.DictReader(
            io.StringIO(client.get("/history.csv?period=all").text)))
        assert float(rows[0]["earnings"]) == 40.50

    def test_switching_back_to_flat_keeps_the_tier_rows(self, client):
        self._save_tiered(client, ["20", ""], ["2.25", "1.65"])
        client.post("/settings", data={**self.BASE, "rate_model": "flat"})
        assert _scalar(client, "SELECT COUNT(*) FROM rate_tiers") == 2
        # ...but a day logged now prices flat.
        client.post("/log", data={"date": "2026-08-01", "packages": "10",
                                  "miles": "0"})
        rows = list(csv.DictReader(
            io.StringIO(client.get("/history.csv?period=all").text)))
        assert float(rows[0]["earnings"]) == 16.50

    def test_a_descending_ceiling_is_rejected(self, client):
        r = self._save_tiered(client, ["40", "20", ""],
                              ["2.25", "1.95", "1.65"])
        assert r.status_code == 400
        assert _scalar(client, "SELECT COUNT(*) FROM rate_tiers") == 0

    def test_a_negative_rate_is_rejected(self, client):
        r = self._save_tiered(client, ["20", ""], ["-1", "1.65"])
        assert r.status_code == 400
        assert _scalar(client, "SELECT COUNT(*) FROM rate_tiers") == 0

    def test_tiered_with_no_tiers_at_all_is_rejected(self, client):
        r = client.post("/settings", data={**self.BASE,
                                           "rate_model": "tiered"})
        assert r.status_code == 400

    def test_tiers_are_isolated_between_businesses(self, client):
        self._save_tiered(client, ["20", ""], ["2.25", "1.65"])
        other = _create_business(client, "Newton Hub")
        client.post("/business/switch", data={"business_id": str(other)})
        assert _scalar(
            client, "SELECT COUNT(*) FROM rate_tiers WHERE business_id=?",
            (other,)) == 0
```

Add the list helper beside `_scalar`:

```python
def _all(client, sql, params=()):
    conn = get_conn(client.db_path)
    try:
        return [tuple(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_app_smoke.py -k TierEditor -v`
Expected: FAIL — no `rate_tiers` rows are written.

- [ ] **Step 3: Parse and validate tiers in `app/main.py`**

```python
def _parse_tier_form(form):
    """Read the repeating tier rows. Returns (tiers, error).

    Lower bounds are not read from the form - the editor derives them and
    replace_tiers recomputes them - so a gap or overlap cannot be
    expressed. What still needs checking is that ceilings ascend, rates
    are sane, and only the final row is unbounded.
    """
    tos = form.getlist("tier_to")
    rates = form.getlist("tier_rate")
    if not rates:
        return [], "Add at least one tier, or choose a flat rate."

    tiers = []
    low = 1
    for index, raw_rate in enumerate(rates):
        rate, err = parse_number(raw_rate, label=f"Tier {index + 1} rate")
        if err:
            return [], err
        raw_to = tos[index] if index < len(tos) else ""
        is_last = index == len(rates) - 1
        if (raw_to or "").strip() == "":
            if not is_last:
                return [], ("Only the last tier can be open-ended. Give "
                            f"tier {index + 1} an upper limit.")
            tiers.append((None, rate))
            break
        ceiling, err = parse_int(raw_to, label=f"Tier {index + 1} upper limit")
        if err:
            return [], err
        if ceiling < low:
            return [], (f"Tier {index + 1} must end at {low} or higher — "
                        "each tier has to start above the one before it.")
        tiers.append((ceiling, rate))
        low = ceiling + 1
    return tiers, None
```

In `settings_save`, after the existing number parsing and before the write:

```python
    rate_model = "tiered" if form.get("rate_model") == "tiered" else "flat"
    tiers = []
    if rate_model == "tiered":
        tiers, tier_err = _parse_tier_form(form)
        if tier_err:
            errors["tiers"] = tier_err
```

and inside the success path, alongside the other business fields:

```python
        businesses_repo.update_business(conn, business["id"], {
            "name": (form.get("business_name") or "").strip()
                    or business["name"],
            "pay_per_package": numbers["pay_per_package"],
            "drivers_enabled": 1 if form.get("drivers_enabled") else 0,
            "rate_model": rate_model,
        })
        if rate_model == "tiered":
            businesses_repo.replace_tiers(conn, business["id"], tiers)
```

Switching to flat deliberately leaves the rows in place, so toggling back and forth
does not destroy the table.

`_render_settings` passes the tiers through:

```python
def _render_settings(request, conn, business, values, errors, status=200):
    cfg = settings_repo.get_expense_config(conn, business["id"])
    drivers = drivers_repo.list_drivers(conn, business["id"])
    tiers = businesses_repo.get_tiers(conn, business["id"])
    return templates.TemplateResponse(request, "settings.html", _ctx(
        conn, business=business, expense_config=cfg, drivers=drivers,
        tiers=tiers, values=values, errors=errors, active="settings"),
        status_code=status)
```

- [ ] **Step 4: Add the editor to `app/templates/settings.html`**

Inside the "Pay & Earnings" card, after the rate field:

```html
    <div class="field">
      <label class="check-label">
        <input type="radio" name="rate_model" value="flat"
               {{ 'checked' if business.rate_model != 'tiered' }}>
        Flat rate per package
      </label>
      <label class="check-label">
        <input type="radio" name="rate_model" value="tiered"
               {{ 'checked' if business.rate_model == 'tiered' }}>
        Fluctuating &mdash; the rate depends on the day's package count
      </label>
    </div>

    <div class="tier-editor" id="tier-editor"
         {{ 'hidden' if business.rate_model != 'tiered' }}>
      <p class="hint">
        The day's total package count picks one tier, and every package that day
        pays that tier's rate. Smaller blocks usually pay more per package.
      </p>
      {% if errors.tiers %}<p class="field-error">{{ errors.tiers }}</p>{% endif %}
      <table class="tier-table">
        <thead><tr><th>From</th><th>To</th><th>Rate</th><th></th></tr></thead>
        <tbody id="tier-rows">
          {% for t in tiers %}
          <tr class="tier-row">
            <td class="tier-from">{{ t.min_packages }}</td>
            <td><input type="number" name="tier_to" min="1" step="1"
                       value="{{ t.max_packages if t.max_packages is not none else '' }}"
                       placeholder="&#8734;"></td>
            <td>$ <input type="number" name="tier_rate" min="0" step="0.01"
                         value="{{ t.rate }}" required></td>
            <td><button type="button" class="btn btn-sm btn-ghost tier-remove">&times;</button></td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      <button type="button" id="tier-add" class="btn btn-sm btn-ghost">+ Add tier</button>
    </div>
```

Append to `app/static/app.css`:

```css
.tier-editor { margin-top: 1rem; }
.tier-table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
.tier-table th {
  text-align: left;
  font-weight: 600;
  color: #64748b;
  font-size: 0.75rem;
  padding: 0.25rem 0.5rem 0.25rem 0;
}
.tier-table td { padding: 0.25rem 0.5rem 0.25rem 0; }
.tier-table input { width: 100%; max-width: 110px; }
.tier-from { color: #64748b; white-space: nowrap; }
```

- [ ] **Step 5: Run the suite**

Run: `.venv/Scripts/python -m pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/templates/settings.html app/static/app.css tests/test_app_smoke.py
git commit -m "feat: add the fluctuating-contract tier editor"
```

---

## Task 4: Effective rate in History and CSV, and the live estimate

**Files:** Modify `app/main.py`, `app/templates/history.html`, `app/static/app.js`. Test: `tests/test_app_smoke.py`.

- [ ] **Step 1: Add the rate column to the CSV**

In `history_csv`, add `"rate"` to the header after `"miles"` and
`round(c["rate"], 4)` to each row in the same position.

- [ ] **Step 2: Add the rate column to History**

In `app/templates/history.html`, add `<th class="text-right">Rate</th>` after the
Miles header and the matching cell:

```html
        <td class="text-right text-muted">${{ "%.2f" | format(r.computed.rate) }}</td>
```

- [ ] **Step 3: Make the live estimate tier-aware**

In `app/templates/log_day.html`, add the tier table to the config blob:

```html
  data-rate-model="{{ business.rate_model }}"
  data-tiers='{{ tiers | tojson }}'
```

and pass `tiers=businesses_repo.get_tiers(conn, business["id"])` from `_render_log`.

In `app/static/app.js`, inside `initLogEstimate`, replace the flat earnings line:

```javascript
    var rateModel = cfg.dataset.rateModel || "flat";
    var tiers = [];
    try { tiers = JSON.parse(cfg.dataset.tiers || "[]"); } catch (e) { tiers = []; }

    function rateFor(pkgs) {
      if (rateModel !== "tiered") return payPerPkg;
      for (var i = 0; i < tiers.length; i++) {
        var lo = tiers[i].min_packages;
        var hi = tiers[i].max_packages;
        if (pkgs >= lo && (hi === null || pkgs <= hi)) return tiers[i].rate;
      }
      return payPerPkg;   // mirrors the server's fallback
    }
```

and in `update()`:

```javascript
      var rate     = rateFor(pkgs);
      var earnings = pkgs * rate;
```

Show which rate is being applied by setting a sibling element:

```javascript
      var rateNote = document.getElementById("est-rate-note");
      if (rateNote) {
        rateNote.textContent = pkgs
          ? pkgs + " \u00d7 $" + rate.toFixed(2)
          : "";
      }
```

Add the element under the earnings row in `log_day.html`:

```html
      <div class="estimate-row estimate-sub">
        <span id="est-rate-note" class="text-muted"></span>
        <span></span>
      </div>
```

- [ ] **Step 4: Write a test for the History column**

Append to `tests/test_app_smoke.py`, inside `TestTierEditor`:

```python
    def test_history_shows_the_effective_rate(self, client):
        self._save_tiered(client, ["20", "40", ""], ["2.25", "1.95", "1.65"])
        client.post("/log", data={"date": "2026-08-01", "packages": "45",
                                  "miles": "0"})
        client.post("/log", data={"date": "2026-08-02", "packages": "18",
                                  "miles": "0"})
        page = client.get("/history?period=all").text
        assert "$1.65" in page
        assert "$2.25" in page

    def test_flat_businesses_still_show_a_rate(self, client):
        client.post("/log", data={"date": "2026-08-01", "packages": "10",
                                  "miles": "0"})
        assert "$1.65" in client.get("/history?period=all").text
```

- [ ] **Step 5: Run the suite**

Run: `.venv/Scripts/python -m pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/templates/history.html app/templates/log_day.html app/static/app.js tests/test_app_smoke.py
git commit -m "feat: show the effective per-package rate in History, CSV, and the estimate"
```

---

## Task 5: Documentation

**Files:** Modify `app/templates/help.html`, `README.md`.

- [ ] **Step 1: Add a Help/FAQ entry**

```html
  <div class="faq-item">
    <div class="faq-q">My pay depends on how many packages are in the block. Can HubProfit handle that?</div>
    <div class="faq-a">
      <p>Yes. In Settings, choose <strong>Fluctuating</strong> instead of a flat rate
      and enter your tiers &mdash; for example 1&ndash;20 at $2.25, 21&ndash;40 at
      $1.95, and 41 or more at $1.65.</p>
      <p>The day's <strong>total package count picks one tier, and every package that
      day pays that tier's rate.</strong> A 45-package day on the tiers above earns
      45 &times; $1.65 = $74.25. It is not worked out bracket by bracket.</p>
      <p>Each tier starts automatically where the previous one ended, so you cannot
      accidentally leave a gap. Leave the last tier's upper limit blank to mean
      "and above".</p>
      <p>Your tiers are frozen into each day when you save it, the same as a flat
      rate. Changing your tiers later never reprices a day you have already logged.
      If you correct a day's package count, it reprices against <em>that day's</em>
      tiers &mdash; the rate moves because the count moved, not because the contract
      did.</p>
      <p>Contract type is per business, so you can run one Hub account on a flat rate
      and another on a fluctuating one.</p>
    </div>
  </div>
```

- [ ] **Step 2: Add the README bullet**

```markdown
- **Fluctuating pay contracts** — if your per-package rate depends on the block size, enter your tiers and HubProfit prices each day at the right one
```

- [ ] **Step 3: Run the suite and commit**

```bash
.venv/Scripts/python -m pytest
git add app/templates/help.html README.md
git commit -m "docs: explain fluctuating contracts"
```

---

## Verification Before Handoff

- [ ] `.venv/Scripts/python -m pytest` — full suite green.
- [ ] Whole-block maths confirmed by hand: 45 packages on 1–20/21–40/41+ at
      $2.25/$1.95/$1.65 earns **$74.25**, not $92.25.
- [ ] A v1-shaped database upgraded straight through v2 and v3 in one run, with
      historical nets unchanged.
- [ ] A tiered day edited from 45 to 38 packages reprices to $1.95 and its `snap_*`
      columns are unchanged.
- [ ] Changing tiers in Settings leaves every logged day's earnings untouched.
- [ ] A flat business is unaffected by the presence of tier rows.

**Deployment note:** ships migration v3. Portainer → Pull and redeploy; never check
"Remove volumes". Back up `hubprofit_data` first.
