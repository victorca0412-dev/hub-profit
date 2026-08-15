# HubProfit Per-Driver Pay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Driver pay set per driver as a per-package or per-day rate, frozen into each entry, with the owner's margin shown plainly on Log Day — including when it is negative.

**Architecture:** Migration v4 adds pay columns to `drivers` and snapshot columns to `daily_entries`. `calculations` gains `driver_cost`, folded into the existing expenses breakdown so `net` already *is* the owner's margin. Mileage expenses stop applying on driver days.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, SQLite, pytest. No new dependencies.

## Global Constraints

- Branch `feat/driver-pay`, base `master`. The 199 existing tests must keep passing.
- Tests: `.venv/Scripts/python -m pytest` from `C:\Users\Victor\hub-profit`.
- Migrations use explicit `BEGIN`, never `executescript`, never `DROP COLUMN`.
- **`update_entry` never writes a `snap_*` column.**
- Driver pay is per business, like everything else.
- Style: 4-space indent, ~79 cols, docstrings for the *why*.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/db.py` | Migration v4. |
| `app/drivers_repo.py` | Pay model and rate on create/update. |
| `app/calculations.py` | `driver_cost`; mileage skipped on driver days. |
| `app/entries_repo.py` | Snapshot the driver's pay at save time. |
| `app/main.py` | Driver routes take a rate; Log Day hides miles. |
| `app/templates/settings.html` | Rate and model inline with each driver. |
| `app/templates/log_day.html` | Driver-aware miles field and estimate. |
| `app/static/app.js` | Margin breakdown in the live estimate. |
| `tests/test_driver_pay.py` (create) | The new behaviour. |

---

## Task 1: Migration v4 and driver pay storage

**Interfaces:**
- `db.SCHEMA_VERSION = 4`, `db._migrate_to_v4`, registered in `MIGRATIONS`
- `drivers_repo.add_driver(conn, name, business_id, pay_model="per_package", pay_rate=0.0)`
- `drivers_repo.update_driver_pay(conn, driver_id, business_id, pay_model, pay_rate) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_driver_pay.py`:

```python
import pytest

from app import businesses_repo, drivers_repo, entries_repo, settings_repo
from app.calculations import compute_entry, driver_cost


class TestMigrationV4:
    def test_version_is_four(self, conn):
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4

    def test_drivers_have_pay_columns(self, conn):
        cols = {r[1] for r in conn.execute("PRAGMA table_info(drivers)")}
        assert {"pay_model", "pay_rate"} <= cols

    def test_entries_have_driver_snapshot_columns(self, conn):
        cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_entries)")}
        assert {"snap_driver_pay_model", "snap_driver_pay_rate"} <= cols

    def test_the_shared_driver_expense_row_is_gone(self, conn):
        n = conn.execute(
            "SELECT COUNT(*) FROM expense_config WHERE key='driver'"
        ).fetchone()[0]
        assert n == 0


class TestDriverPayStorage:
    def test_a_driver_defaults_to_per_package_at_zero(self, conn):
        did = drivers_repo.add_driver(conn, "Alex", business_id=1)
        d = drivers_repo.get_driver(conn, did, business_id=1)
        assert d["pay_model"] == "per_package"
        assert d["pay_rate"] == 0.0

    def test_a_driver_can_be_created_with_a_rate(self, conn):
        did = drivers_repo.add_driver(conn, "Alex", business_id=1,
                                      pay_model="per_day", pay_rate=95.0)
        d = drivers_repo.get_driver(conn, did, business_id=1)
        assert d["pay_model"] == "per_day"
        assert d["pay_rate"] == 95.0

    def test_pay_can_be_updated(self, conn):
        did = drivers_repo.add_driver(conn, "Alex", business_id=1)
        assert drivers_repo.update_driver_pay(
            conn, did, 1, "per_package", 1.5) is True
        assert drivers_repo.get_driver(
            conn, did, business_id=1)["pay_rate"] == 1.5

    def test_pay_update_will_not_cross_businesses(self, conn):
        other = businesses_repo.create_business(conn, "Newton Hub")
        did = drivers_repo.add_driver(conn, "Alex", business_id=1)
        assert drivers_repo.update_driver_pay(
            conn, did, other, "per_package", 9.0) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_driver_pay.py -v`
Expected: FAIL — `user_version` is 3, no `pay_model` column.

- [ ] **Step 3: Add the migration**

In `app/db.py`, set `SCHEMA_VERSION = 4` and add:

```python
def _migrate_to_v4(conn):
    """Driver pay moves onto the driver, as a per-package or per-day rate.

    The shared expense_config 'driver' row is deleted rather than kept
    for compatibility. Safe only because multi-driver mode became usable
    on 2026-08-13 and no driver-assigned entry exists anywhere - verified
    against the lab before deciding. That window does not reopen.
    """
    _add_column(conn, "drivers", "pay_model",
                "TEXT NOT NULL DEFAULT 'per_package'")
    _add_column(conn, "drivers", "pay_rate", "REAL NOT NULL DEFAULT 0")
    # NULL on existing rows: they have no driver, so they cost nothing.
    _add_column(conn, "daily_entries", "snap_driver_pay_model", "TEXT")
    _add_column(conn, "daily_entries", "snap_driver_pay_rate", "REAL")
    conn.execute("DELETE FROM expense_config WHERE key = 'driver'")


MIGRATIONS = {2: _migrate_to_v2, 3: _migrate_to_v3, 4: _migrate_to_v4}
```

Also remove `("driver", 0, "per_day", 0.0)` from `DEFAULT_EXPENSES`, or
`seed_business_expenses` will put it straight back on the next start.

- [ ] **Step 4: Update `app/drivers_repo.py`**

```python
ALLOWED_PAY_MODELS = ("per_package", "per_day")


def add_driver(conn, name, business_id, pay_model="per_package",
               pay_rate=0.0):
    if pay_model not in ALLOWED_PAY_MODELS:
        raise ValueError("unknown pay model: %s" % pay_model)
    cur = conn.execute(
        "INSERT INTO drivers (name, business_id, pay_model, pay_rate) "
        "VALUES (?,?,?,?)", (name, business_id, pay_model, pay_rate))
    conn.commit()
    return cur.lastrowid


def update_driver_pay(conn, driver_id, business_id, pay_model, pay_rate):
    if pay_model not in ALLOWED_PAY_MODELS:
        raise ValueError("unknown pay model: %s" % pay_model)
    cur = conn.execute(
        "UPDATE drivers SET pay_model=?, pay_rate=? "
        "WHERE id=? AND business_id=?",
        (pay_model, pay_rate, driver_id, business_id))
    conn.commit()
    return cur.rowcount > 0
```

- [ ] **Step 5: Run tests, then the suite**

Run: `.venv/Scripts/python -m pytest tests/test_driver_pay.py -v` — PASS.
Run: `.venv/Scripts/python -m pytest` — the settings-page tests that assert on the
"Driver pay (per day)" expense row will now fail. Update those tests to reflect the
row being gone; do not restore the row.

- [ ] **Step 6: Commit**

```bash
git add app/db.py app/drivers_repo.py tests/
git commit -m "feat: driver pay stored per driver, migration v4"
```

---

## Task 2: Cost, snapshots, and mileage on driver days

**Interfaces:**
- `calculations.driver_cost(entry) -> float`
- `compute_entry` includes it in `expenses["driver"]`
- `entries_repo.create_entry` writes `snap_driver_pay_model` / `snap_driver_pay_rate`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_driver_pay.py`:

```python
def _entry(packages=45, miles=38.0, driver_id=1, model="per_package",
           rate=1.5, cfg=None):
    return {"packages": packages, "miles": miles, "hours": None,
            "extra_expense": None, "driver_id": driver_id,
            "snap_pay_per_package": 1.65, "snap_gas_price": 3.40,
            "snap_mpg": 28.0,
            "snap_expense_config": cfg if cfg is not None else {},
            "snap_rate_model": "flat", "snap_rate_tiers": None,
            "snap_driver_pay_model": model, "snap_driver_pay_rate": rate}


MILEAGE_CFG = {
    "fuel": {"enabled": True, "mode": "mileage_fuel", "amount": 0.0},
    "vehicle_wear": {"enabled": True, "mode": "per_mile", "amount": 0.12},
}


class TestDriverCost:
    def test_per_package_multiplies_by_count(self):
        assert driver_cost(_entry(packages=45, rate=1.5)) == pytest.approx(67.5)

    def test_per_day_is_flat(self):
        assert driver_cost(
            _entry(packages=45, model="per_day", rate=95.0)) == 95.0

    def test_no_driver_costs_nothing(self):
        assert driver_cost(_entry(driver_id=None)) == 0

    def test_missing_snapshot_costs_nothing(self):
        e = _entry()
        e["snap_driver_pay_model"] = None
        e["snap_driver_pay_rate"] = None
        assert driver_cost(e) == 0


class TestMileageOnDriverDays:
    def test_fuel_and_wear_are_not_charged_on_a_driver_day(self):
        r = compute_entry(_entry(cfg=MILEAGE_CFG), 1)
        assert "fuel" not in r["expenses"]
        assert "vehicle_wear" not in r["expenses"]

    def test_fuel_and_wear_are_charged_on_an_owner_day(self):
        r = compute_entry(_entry(driver_id=None, cfg=MILEAGE_CFG), 1)
        assert r["expenses"]["fuel"] > 0
        assert r["expenses"]["vehicle_wear"] > 0

    def test_monthly_costs_still_apply_on_driver_days(self):
        cfg = {"insurance": {"enabled": True, "mode": "monthly",
                             "amount": 90.0}}
        r = compute_entry(_entry(cfg=cfg), 30)
        assert r["expenses"]["insurance"] == pytest.approx(3.0)


class TestMargin:
    def test_net_is_the_owners_margin(self):
        r = compute_entry(_entry(packages=45, rate=1.5), 1)
        # 45 x 1.65 earned, 45 x 1.50 paid out.
        assert r["earnings"] == pytest.approx(74.25)
        assert r["expenses"]["driver"] == pytest.approx(67.5)
        assert r["net"] == pytest.approx(6.75)

    def test_a_rate_above_the_owners_produces_a_loss(self):
        r = compute_entry(_entry(packages=45, rate=1.70), 1)
        assert r["net"] < 0

    def test_tiered_contract_margin_shrinks_on_larger_blocks(self):
        tiers = [{"min_packages": 1, "max_packages": 20, "rate": 2.25},
                 {"min_packages": 21, "max_packages": 40, "rate": 1.95},
                 {"min_packages": 41, "max_packages": None, "rate": 1.65}]
        small = _entry(packages=18, rate=1.5)
        big = _entry(packages=45, rate=1.5)
        for e in (small, big):
            e["snap_rate_model"] = "tiered"
            e["snap_rate_tiers"] = tiers
        # The table in the spec: $13.50 on 18 packages, $6.75 on 45.
        assert compute_entry(small, 1)["net"] == pytest.approx(13.50)
        assert compute_entry(big, 1)["net"] == pytest.approx(6.75)


class TestDriverSnapshots:
    def test_an_entry_freezes_the_drivers_rate(self, conn):
        did = drivers_repo.add_driver(conn, "Alex", business_id=1,
                                      pay_rate=1.5)
        eid = entries_repo.create_entry(
            conn, {"date": "2026-08-01", "packages": 45, "miles": 0,
                   "driver_id": did}, business_id=1)
        e = entries_repo.get_entry(conn, eid, business_id=1)
        assert e["snap_driver_pay_rate"] == 1.5
        assert e["snap_driver_pay_model"] == "per_package"

    def test_a_raise_does_not_reprice_a_logged_day(self, conn):
        did = drivers_repo.add_driver(conn, "Alex", business_id=1,
                                      pay_rate=1.5)
        eid = entries_repo.create_entry(
            conn, {"date": "2026-08-01", "packages": 45, "miles": 0,
                   "driver_id": did}, business_id=1)
        drivers_repo.update_driver_pay(conn, did, 1, "per_package", 1.9)
        e = entries_repo.get_entry(conn, eid, business_id=1)
        assert driver_cost(e) == pytest.approx(67.5)

    def test_editing_the_count_reprices_against_the_frozen_rate(self, conn):
        did = drivers_repo.add_driver(conn, "Alex", business_id=1,
                                      pay_rate=1.5)
        eid = entries_repo.create_entry(
            conn, {"date": "2026-08-01", "packages": 45, "miles": 0,
                   "driver_id": did}, business_id=1)
        entries_repo.update_entry(
            conn, eid, {"date": "2026-08-01", "packages": 30, "miles": 0,
                        "driver_id": did}, business_id=1)
        e = entries_repo.get_entry(conn, eid, business_id=1)
        assert driver_cost(e) == pytest.approx(45.0)
        assert e["snap_driver_pay_rate"] == 1.5

    def test_no_driver_leaves_the_snapshot_null(self, conn):
        eid = entries_repo.create_entry(
            conn, {"date": "2026-08-01", "packages": 10, "miles": 5},
            business_id=1)
        e = entries_repo.get_entry(conn, eid, business_id=1)
        assert e["snap_driver_pay_model"] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_driver_pay.py -v`
Expected: FAIL — `cannot import name 'driver_cost'`.

- [ ] **Step 3: Update `app/calculations.py`**

Add above `compute_entry`:

```python
def driver_cost(entry):
    """What the owner pays their driver for this day.

    Reads the rate frozen into the entry, never the driver's current
    rate: a raise in March must not reprice January. Correcting a day's
    package count does move a per-package cost, because the count moved.
    """
    if entry.get("driver_id") is None:
        return 0.0
    model = entry.get("snap_driver_pay_model")
    rate = entry.get("snap_driver_pay_rate")
    if not model or rate is None:
        return 0.0
    if model == "per_day":
        return rate
    return entry["packages"] * rate
```

In `_expense_cost`, skip mileage-based expenses on driver days:

```python
    if mode in ("mileage_fuel", "per_mile") and \
            entry.get("driver_id") is not None:
        # The driver runs their own vehicle, so their miles are not the
        # owner's cost. Charging them here overstates expenses.
        return None
```

Delete the old `per_day` driver special case — driver pay no longer comes from
`expense_config` at all.

In `compute_entry`, add the driver cost to the breakdown after the config loop:

```python
    cost = driver_cost(entry)
    if cost:
        expenses["driver"] = cost
```

- [ ] **Step 4: Snapshot the rate in `app/entries_repo.py`**

In `create_entry`, look the driver up and freeze their pay:

```python
    driver_id = data.get("driver_id")
    pay_model = pay_rate = None
    if driver_id is not None:
        row = conn.execute(
            "SELECT pay_model, pay_rate FROM drivers "
            "WHERE id=? AND business_id=?", (driver_id, business_id)
        ).fetchone()
        if row is not None:
            pay_model, pay_rate = row["pay_model"], row["pay_rate"]
```

Add both to the INSERT column list and values.

- [ ] **Step 5: Run tests and the suite**

Run: `.venv/Scripts/python -m pytest` — expect failures in older tests that assert
driver pay comes from `expense_config`. Update them to the new model.

- [ ] **Step 6: Commit**

```bash
git add app/calculations.py app/entries_repo.py tests/
git commit -m "feat: per-package driver cost, frozen per entry; no mileage on driver days"
```

---

## Task 3: Settings UI and routes

- [ ] **Step 1: Write failing route tests**

Append to `tests/test_app_smoke.py`:

```python
class TestDriverPayRoutes:
    ENABLE = {"business_name": "T", "pay_per_package": "1.65",
              "gas_price_per_gal": "3.40", "vehicle_mpg": "25",
              "drivers_enabled": "1"}

    def test_a_driver_is_created_with_a_rate(self, client):
        client.post("/settings", data=self.ENABLE)
        client.post("/settings/drivers", data={
            "name": "Alex", "pay_model": "per_package", "pay_rate": "1.50"})
        assert _scalar(client, "SELECT pay_rate FROM drivers") == 1.5
        assert _scalar(client, "SELECT pay_model FROM drivers") == "per_package"

    def test_a_rate_can_be_updated(self, client):
        client.post("/settings", data=self.ENABLE)
        client.post("/settings/drivers", data={"name": "Alex",
                                               "pay_rate": "1.50"})
        did = _first_driver_id(client)
        client.post("/settings/drivers/%d/pay" % did,
                    data={"pay_model": "per_day", "pay_rate": "95"})
        assert _scalar(client, "SELECT pay_rate FROM drivers") == 95.0

    def test_a_negative_rate_is_rejected(self, client):
        client.post("/settings", data=self.ENABLE)
        r = client.post("/settings/drivers", data={"name": "Alex",
                                                   "pay_rate": "-1"})
        assert r.status_code == 400
        assert _scalar(client, "SELECT COUNT(*) FROM drivers") == 0

    def test_an_unknown_pay_model_is_rejected(self, client):
        client.post("/settings", data=self.ENABLE)
        r = client.post("/settings/drivers", data={
            "name": "Alex", "pay_model": "hourly", "pay_rate": "1"})
        assert r.status_code == 400

    def test_the_driver_pay_expense_row_is_gone_from_settings(self, client):
        assert "exp_driver_amount" not in client.get("/settings").text
```

- [ ] **Step 2: Update `app/main.py`**

`driver_add` parses the rate and model; add a `driver_pay` route:

```python
DRIVER_PAY_MODELS = ("per_package", "per_day")


def _parse_driver_pay(form):
    """Return (pay_model, pay_rate, error)."""
    model = form.get("pay_model") or "per_package"
    if model not in DRIVER_PAY_MODELS:
        return None, None, "Choose per package or per day."
    rate, err = parse_number(form.get("pay_rate"), label="Driver pay",
                             required=False)
    if err:
        return None, None, err
    return model, rate or 0.0, None


@app.post("/settings/drivers/{driver_id}/pay")
async def driver_pay(request: Request, driver_id: int):
    form = await request.form()
    model, rate, err = _parse_driver_pay(form)
    with get_db() as conn:
        business = _active_business(conn)
        if err:
            return _render_settings(
                request, conn, business,
                _stored_settings_values(conn, business),
                {"driver_pay": err}, status=400)
        if not drivers_repo.update_driver_pay(conn, driver_id,
                                              business["id"], model, rate):
            raise HTTPException(status_code=404, detail="Driver not found")
    return RedirectResponse("/settings", status_code=303)
```

`driver_add` gains the same parsing and passes both to `add_driver`.

Remove `"driver"` from `EXPENSE_KEYS` and `EXPENSE_LABELS`.

- [ ] **Step 3: Update `app/templates/settings.html`**

In the Drivers card, each row gains a pay form; the add form gains rate and model
inputs. Remove nothing else — the `driver` expense row disappears on its own because
`expense_config` no longer has it.

```html
    <form method="post" action="/settings/drivers/{{ d.id }}/pay"
          class="driver-pay">
      $ <input type="number" name="pay_rate" min="0" step="0.01"
               value="{{ d.pay_rate }}">
      <select name="pay_model">
        <option value="per_package" {{ 'selected' if d.pay_model == 'per_package' }}>per package</option>
        <option value="per_day" {{ 'selected' if d.pay_model == 'per_day' }}>per day</option>
      </select>
      <button type="submit" class="btn btn-sm btn-ghost">Save pay</button>
    </form>
```

- [ ] **Step 4: Run the suite and commit**

```bash
.venv/Scripts/python -m pytest
git add app/main.py app/templates/settings.html tests/test_app_smoke.py
git commit -m "feat: set driver pay from Settings"
```

---

## Task 4: Log Day — hide miles, show the margin

- [ ] **Step 1: Write failing tests**

```python
class TestLogDayWithDriver:
    def test_driver_rates_are_available_to_the_estimate(self, client):
        client.post("/settings", data=TestDriverPayRoutes.ENABLE)
        client.post("/settings/drivers", data={"name": "Alex",
                                               "pay_rate": "1.50"})
        page = client.get("/log").text
        assert "data-driver-rates" in page
        assert "1.5" in page

    def test_a_driver_day_stores_zero_miles(self, client):
        client.post("/settings", data=TestDriverPayRoutes.ENABLE)
        client.post("/settings/drivers", data={"name": "Alex",
                                               "pay_rate": "1.50"})
        did = _first_driver_id(client)
        client.post("/log", data={"date": "2026-08-01", "packages": "45",
                                  "miles": "38", "driver_id": str(did)})
        assert _scalar(client, "SELECT miles FROM daily_entries") == 0
```

- [ ] **Step 2: Zero the miles server-side**

In `_parse_log_form`, after parsing:

```python
    if data.get("driver_id") is not None:
        # The driver runs their own vehicle; their mileage is not the
        # owner's cost, so it is not recorded against the owner's day.
        data["miles"] = 0.0
```

- [ ] **Step 3: Pass driver rates to the template**

In `_render_log`, the drivers list already carries `pay_model` and `pay_rate`.
Add a JSON blob to `log_day.html`:

```html
  data-driver-rates='{{ drivers | map(attribute="id") | list | tojson }}'
```

Replace with a proper mapping:

```html
  data-driver-rates='{{ driver_rates | tojson }}'
```

built in `_render_log` as
`{str(d["id"]): {"model": d["pay_model"], "rate": d["pay_rate"], "name": d["name"]} for d in drivers}`.

- [ ] **Step 4: Update `app/static/app.js`**

In `initLogEstimate`, read the driver select and the rates blob, hide the miles
field when a driver is chosen, and add the margin rows:

```javascript
    var driverSel   = document.getElementById("inp-driver");
    var milesField  = milesInput ? milesInput.closest(".field") : null;
    var driverRates = {};
    try { driverRates = JSON.parse(cfg.dataset.driverRates || "{}"); }
    catch (e) { driverRates = {}; }

    function currentDriver() {
      if (!driverSel || !driverSel.value) return null;
      return driverRates[driverSel.value] || null;
    }
```

and inside `update()`:

```javascript
      var driver = currentDriver();
      if (milesField) milesField.style.display = driver ? "none" : "";
      if (driver) miles = 0;

      var driverPay = 0;
      if (driver) {
        driverPay = driver.model === "per_day"
          ? driver.rate : pkgs * driver.rate;
      }
      var margin = earnings - fuel - extra - driverPay;
```

Render a driver row and a margin row, marking the margin red and adding
`"This block loses money at " + driver.name + "'s rate"` when it is negative.

- [ ] **Step 5: Update `app/templates/log_day.html`**

Add the driver-pay and margin rows to the estimate box, and a note under the driver
select explaining that mileage is not recorded for driver days.

- [ ] **Step 6: Run the suite and commit**

```bash
.venv/Scripts/python -m pytest
git add app/main.py app/templates/log_day.html app/static/app.js tests/
git commit -m "feat: show the owner's margin on driver days"
```

---

## Task 5: Documentation

- [ ] Update the Help/FAQ multi-driver answer for per-driver pay, the margin, and
      that mileage is not recorded on driver days.
- [ ] Add a CHANGELOG entry under a new `2.1.0` heading and bump
      `app/__init__.py` to `2.1.0`.
- [ ] Add the README bullet for per-driver pay.
- [ ] Commit.

---

## Verification Before Handoff

- [ ] `.venv/Scripts/python -m pytest` green.
- [ ] A v3 database upgrades to v4 with every existing entry's net unchanged.
- [ ] The spec's table reproduces: $13.50 on 18 packages, $6.75 on 45, same rate.
- [ ] A driver rate above the owner's effective rate shows a loss and says so.
- [ ] Changing a driver's rate leaves logged days untouched.
- [ ] Selecting a driver hides the miles field and stores zero.
