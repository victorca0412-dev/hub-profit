# HubProfit Bug Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the four confirmed input-validation and dead-feature bugs (A–D) from the 2026-08-13 spec, so no user input can silently corrupt stored data and the documented multi-driver feature actually works.

**Architecture:** Add a pure `app/validation.py` module of parser functions that return `(value, error_message)` tuples. Routes collect errors into a dict and re-render the submitted form with inline messages and HTTP 400 instead of writing bad data. Templates gain a `values` dict so a rejected form redisplays what the user typed. Driver management gets the routes and Settings UI it never had.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, SQLite, pytest. No new dependencies.

## Global Constraints

- Work on branch `feat/multi-business-and-tiered-rates`, already checked out.
- The existing 56 tests must continue to pass after every task.
- Run tests with `.venv/Scripts/python -m pytest` from `C:\Users\Victor\hub-profit`.
- **No `snap_*` column is ever written by an update path.** This is the app's
  frozen-rate promise and predates this plan.
- **No schema changes in this plan.** Part 0 ships onto the live volume with no
  migration. Orphan-row repair belongs to Plan 2.
- Never substitute a default value for input the user actually typed. A bad value is
  an error, not a reason to guess.
- Match the existing code style: 4-space indent, ~79 col lines, docstrings only where
  the *why* is non-obvious.

---

## File Structure

| File | Responsibility |
|---|---|
| `app/validation.py` (create) | Pure parsers. No DB, no FastAPI, no I/O. |
| `tests/test_validation.py` (create) | Unit tests for the parsers. |
| `app/main.py` (modify) | Collect errors, re-render with 400, driver routes. |
| `app/drivers_repo.py` (modify) | Add `rename_driver`. |
| `app/templates/log_day.html` (modify) | Render from `values`, show `errors`. |
| `app/templates/settings.html` (modify) | Show `errors`, add driver management. |
| `app/static/app.css` (modify) | `.field-error` styling. |
| `tests/test_app_smoke.py` (modify) | Route-level regression tests for A–D. |
| `tests/test_drivers_repo.py` (modify) | Test for `rename_driver`. |

---

## Task 1: Validation parsers

**Files:**
- Create: `app/validation.py`
- Test: `tests/test_validation.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `parse_date(raw, label="Date") -> tuple[str | None, str | None]`
  - `parse_number(raw, label, required=True, min_value=0.0) -> tuple[float | None, str | None]`
  - `parse_int(raw, label, required=True, min_value=0) -> tuple[int | None, str | None]`

  Each returns `(value, None)` on success or `(None, message)` on failure. A blank
  value for a non-required field returns `(None, None)` — success with no value.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_validation.py`:

```python
from app.validation import parse_date, parse_number, parse_int


class TestParseDate:
    def test_accepts_iso_date(self):
        assert parse_date("2026-08-13") == ("2026-08-13", None)

    def test_normalises_unpadded_parts(self):
        assert parse_date("2026-8-3") == ("2026-08-03", None)

    def test_rejects_garbage(self):
        value, err = parse_date("not-a-date")
        assert value is None
        assert "YYYY-MM-DD" in err

    def test_rejects_impossible_calendar_date(self):
        value, err = parse_date("2026-02-30")
        assert value is None
        assert err

    def test_rejects_blank(self):
        value, err = parse_date("")
        assert value is None
        assert err

    def test_rejects_datetime_string(self):
        # fromisoformat would accept this; strptime must not.
        value, err = parse_date("2026-08-13T10:00:00")
        assert value is None
        assert err

    def test_uses_label_in_message(self):
        _, err = parse_date("nope", label="Delivery date")
        assert "Delivery date" in err


class TestParseNumber:
    def test_accepts_decimal(self):
        assert parse_number("38.5", label="Miles") == (38.5, None)

    def test_accepts_integer_string(self):
        assert parse_number("40", label="Miles") == (40.0, None)

    def test_rejects_negative(self):
        value, err = parse_number("-1", label="Miles")
        assert value is None
        assert "negative" in err.lower()

    def test_rejects_garbage(self):
        value, err = parse_number("abc", label="Miles")
        assert value is None
        assert "Miles" in err

    def test_rejects_blank_when_required(self):
        value, err = parse_number("", label="Pay per package")
        assert value is None
        assert "Pay per package" in err

    def test_blank_is_ok_when_optional(self):
        assert parse_number("", label="Hours", required=False) == (None, None)

    def test_garbage_still_rejected_when_optional(self):
        value, err = parse_number("abc", label="Hours", required=False)
        assert value is None
        assert err

    def test_zero_allowed_by_default(self):
        assert parse_number("0", label="Miles") == (0.0, None)


class TestParseInt:
    def test_accepts_integer(self):
        assert parse_int("47", label="Packages") == (47, None)

    def test_rejects_negative(self):
        value, err = parse_int("-50", label="Packages")
        assert value is None
        assert "negative" in err.lower()

    def test_rejects_decimal(self):
        value, err = parse_int("47.5", label="Packages")
        assert value is None
        assert "whole number" in err.lower()

    def test_rejects_blank_when_required(self):
        value, err = parse_int("", label="Packages")
        assert value is None
        assert err

    def test_blank_is_ok_when_optional(self):
        assert parse_int("", label="Driver", required=False) == (None, None)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_validation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.validation'`

- [ ] **Step 3: Write the implementation**

Create `app/validation.py`:

```python
"""User-input parsers.

Each parser returns (value, error_message). Exactly one of the two is
None. A blank optional field is success with no value: (None, None).

These deliberately never fall back to a default. Substituting a default
for something the user typed is how a cleared pay-rate field silently
became $1.65 and poisoned every entry logged afterwards.
"""

from datetime import datetime


def parse_date(raw, label="Date"):
    text = (raw or "").strip()
    if not text:
        return None, f"{label} is required."
    try:
        # strptime, not date.fromisoformat: fromisoformat also accepts
        # "20260813" and full datetime strings, which are not what the
        # date input posts and not what the DB queries compare against.
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat(), None
    except ValueError:
        return None, f"{label} must be a real date in YYYY-MM-DD form."


def parse_number(raw, label, required=True, min_value=0.0):
    text = (raw or "").strip() if isinstance(raw, str) else raw
    if text in (None, ""):
        if required:
            return None, f"{label} is required."
        return None, None
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None, f"{label} must be a number."
    if value != value or value in (float("inf"), float("-inf")):
        return None, f"{label} must be a number."
    if value < min_value:
        if min_value == 0:
            return None, f"{label} cannot be negative."
        return None, f"{label} must be at least {min_value}."
    return value, None


def parse_int(raw, label, required=True, min_value=0):
    text = (raw or "").strip() if isinstance(raw, str) else raw
    if text in (None, ""):
        if required:
            return None, f"{label} is required."
        return None, None
    try:
        value = int(text)
    except (TypeError, ValueError):
        return None, f"{label} must be a whole number."
    if value < min_value:
        if min_value == 0:
            return None, f"{label} cannot be negative."
        return None, f"{label} must be at least {min_value}."
    return value, None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_validation.py -v`
Expected: PASS, 21 tests.

- [ ] **Step 5: Run the whole suite**

Run: `.venv/Scripts/python -m pytest`
Expected: PASS, 77 tests (56 existing + 21 new).

- [ ] **Step 6: Commit**

```bash
git add app/validation.py tests/test_validation.py
git commit -m "feat: add input parsers that reject bad values instead of defaulting"
```

---

## Task 2: Validate the Log Day form (bugs C and D)

**Files:**
- Modify: `app/main.py` — `log_form`, `log_submit`, `log_update`
- Modify: `app/templates/log_day.html`
- Modify: `app/static/app.css`
- Test: `tests/test_app_smoke.py`

**Interfaces:**
- Consumes: `parse_date`, `parse_number`, `parse_int` from Task 1.
- Produces:
  - `_log_values(entry=None, form=None) -> dict` in `app/main.py`, returning string
    keys `date, packages, miles, hours, extra_expense, driver_id, note`.
  - `log_day.html` renders every field from `values` and every message from
    `errors`, a `dict[str, str]` keyed by field name.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app_smoke.py`:

```python
class TestLogValidation:
    def test_malformed_date_is_rejected(self, client):
        r = client.post("/log", data={
            "date": "not-a-date", "packages": "10", "miles": "5"})
        assert r.status_code == 400
        assert "YYYY-MM-DD" in r.text

    def test_malformed_date_writes_nothing(self, client):
        client.post("/log", data={
            "date": "not-a-date", "packages": "10", "miles": "5"})
        # The orphan bug: such a row is invisible to list_entries, so
        # assert against the raw table instead.
        assert _raw_entry_count(client) == 0

    def test_negative_packages_is_rejected(self, client):
        r = client.post("/log", data={
            "date": "2026-08-01", "packages": "-50", "miles": "5"})
        assert r.status_code == 400
        assert "negative" in r.text.lower()
        assert _raw_entry_count(client) == 0

    def test_negative_miles_is_rejected(self, client):
        r = client.post("/log", data={
            "date": "2026-08-01", "packages": "10", "miles": "-5"})
        assert r.status_code == 400
        assert _raw_entry_count(client) == 0

    def test_garbage_hours_is_rejected(self, client):
        r = client.post("/log", data={
            "date": "2026-08-01", "packages": "10", "miles": "5",
            "hours": "abc"})
        assert r.status_code == 400
        assert _raw_entry_count(client) == 0

    def test_blank_hours_still_accepted(self, client):
        r = client.post("/log", data={
            "date": "2026-08-01", "packages": "10", "miles": "5",
            "hours": ""}, follow_redirects=False)
        assert r.status_code == 303

    def test_rejected_form_redisplays_what_was_typed(self, client):
        r = client.post("/log", data={
            "date": "2026-08-01", "packages": "-50", "miles": "38.5"})
        assert r.status_code == 400
        assert "38.5" in r.text
        assert "2026-08-01" in r.text

    def test_valid_submission_still_works(self, client):
        r = client.post("/log", data={
            "date": "2026-08-01", "packages": "47", "miles": "38.5"},
            follow_redirects=False)
        assert r.status_code == 303
        assert _raw_entry_count(client) == 1

    def test_edit_rejects_bad_date_and_leaves_entry_alone(self, client):
        client.post("/log", data={
            "date": "2026-08-01", "packages": "47", "miles": "38.5"})
        entry_id = _first_entry_id(client)
        r = client.post(f"/log/{entry_id}", data={
            "date": "nope", "packages": "47", "miles": "38.5"})
        assert r.status_code == 400
        assert _entry_date(client, entry_id) == "2026-08-01"
```

`tests/test_app_smoke.py` currently has no pytest fixture — it has a plain
`make_client(tmp_path, monkeypatch)` helper that each test calls. The new tests need
a client that also exposes its database path so assertions can read the raw table,
so add a real fixture alongside `make_client` and leave the existing tests untouched.

Add to the imports at the top of `tests/test_app_smoke.py`:

```python
import pytest
from app.db import get_conn
```

Then add the fixture and helpers directly below `make_client`:

```python
@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient that also carries the path of the DB it is using.

    The raw-table helpers below need it: the Bug C orphan rows are by
    definition invisible to the repo layer, so asserting they were never
    written means querying the table directly.
    """
    db_path = tmp_path / "smoke.db"
    monkeypatch.setenv("HUBPROFIT_DB", str(db_path))
    import app.main as main
    importlib.reload(main)
    c = TestClient(main.app)
    c.db_path = str(db_path)
    return c


def _scalar(client, sql, params=()):
    conn = get_conn(client.db_path)
    try:
        row = conn.execute(sql, params).fetchone()
        return None if row is None else row[0]
    finally:
        conn.close()


def _raw_entry_count(client):
    return _scalar(client, "SELECT COUNT(*) FROM daily_entries")


def _first_entry_id(client):
    return _scalar(client, "SELECT id FROM daily_entries ORDER BY id LIMIT 1")


def _entry_date(client, entry_id):
    return _scalar(client, "SELECT date FROM daily_entries WHERE id=?",
                   (entry_id,))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_app_smoke.py -k LogValidation -v`
Expected: FAIL — the malformed-date posts return 303, not 400.

- [ ] **Step 3: Add the values helper and rewrite the log routes**

In `app/main.py`, add the import:

```python
from app.validation import parse_date, parse_number, parse_int
```

Add the helper above `log_form`:

```python
LOG_FIELDS = ("date", "packages", "miles", "hours", "extra_expense",
              "driver_id", "note")


def _log_values(entry=None, form=None):
    """String values for redisplaying the Log Day form.

    Prefers what the user just submitted, falls back to the entry being
    edited, then to blank. Keeping this in one place is what lets a
    rejected form show back exactly what was typed.
    """
    values = {k: "" for k in LOG_FIELDS}
    if entry:
        for k in LOG_FIELDS:
            v = entry.get(k)
            values[k] = "" if v is None else str(v)
    if form is not None:
        for k in LOG_FIELDS:
            if k in form:
                values[k] = form.get(k) or ""
    return values
```

Replace `log_form`, `log_submit`, and `log_update` with:

```python
def _render_log(request, conn, entry, values, errors, status=200):
    s = settings_repo.get_settings(conn)
    cfg = settings_repo.get_expense_config(conn)
    drivers = drivers_repo.list_drivers(conn, only_active=True)
    if entry and entry["driver_id"] is not None and \
            not any(d["id"] == entry["driver_id"] for d in drivers):
        # Editing must not drop the day's assigned driver just because
        # they were later deactivated: keep them selectable.
        assigned = drivers_repo.get_driver(conn, entry["driver_id"])
        if assigned is not None:
            drivers.append(assigned)
    return templates.TemplateResponse(request, "log_day.html", {
        "settings": s, "expense_config": cfg, "drivers": drivers,
        "today": date.today().isoformat(), "entry": entry,
        "values": values, "errors": errors, "active": "log"},
        status_code=status)


def _parse_log_form(form):
    """Return (data, errors). data is only meaningful when errors is empty."""
    errors = {}
    data = {}

    data["date"], err = parse_date(form.get("date"), label="Date")
    if err:
        errors["date"] = err

    data["packages"], err = parse_int(form.get("packages"), label="Packages")
    if err:
        errors["packages"] = err

    data["miles"], err = parse_number(
        form.get("miles"), label="Miles", required=False)
    if err:
        errors["miles"] = err
    if data["miles"] is None and not err:
        data["miles"] = 0.0

    data["hours"], err = parse_number(
        form.get("hours"), label="Hours", required=False)
    if err:
        errors["hours"] = err

    data["extra_expense"], err = parse_number(
        form.get("extra_expense"), label="Extra expense", required=False)
    if err:
        errors["extra_expense"] = err

    data["driver_id"], err = parse_int(
        form.get("driver_id"), label="Driver", required=False)
    if err:
        errors["driver_id"] = err

    data["note"] = (form.get("note") or "").strip() or None
    return data, errors


@app.get("/log")
def log_form(request: Request, edit: int | None = None):
    with get_db() as conn:
        entry = None
        if edit is not None:
            entry = entries_repo.get_entry(conn, edit)
            if entry is None:
                raise HTTPException(status_code=404, detail="Entry not found")
        values = _log_values(entry=entry)
        if entry is None:
            values["date"] = date.today().isoformat()
        return _render_log(request, conn, entry, values, {})


@app.post("/log")
async def log_submit(request: Request):
    form = await request.form()
    data, errors = _parse_log_form(form)
    with get_db() as conn:
        if errors:
            return _render_log(request, conn, None,
                               _log_values(form=form), errors, status=400)
        entries_repo.create_entry(conn, data)
    return RedirectResponse("/", status_code=303)


@app.post("/log/{entry_id}")
async def log_update(request: Request, entry_id: int):
    form = await request.form()
    data, errors = _parse_log_form(form)
    with get_db() as conn:
        entry = entries_repo.get_entry(conn, entry_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Entry not found")
        if errors:
            return _render_log(request, conn, entry,
                               _log_values(entry=entry, form=form),
                               errors, status=400)
        entries_repo.update_entry(conn, entry_id, data)
    return RedirectResponse("/history", status_code=303)
```

Note the `date.today()` call now sits in `_render_log`, which no longer shadows the
`date` import — the old `log_submit(date: str = Form(...))` signature shadowed it.

- [ ] **Step 4: Update the Log Day template**

In `app/templates/log_day.html`, replace every `value="{{ entry.X ... }}"` expression
with the `values` equivalent and add an error slot under each field. The date and
packages fields become:

```html
      <div class="field-row">
        <div class="field">
          <label for="inp-date">Date</label>
          <input type="date" id="inp-date" name="date"
                 value="{{ values.date }}" required>
          {% if errors.date %}<p class="field-error">{{ errors.date }}</p>{% endif %}
        </div>
        <div class="field">
          <label for="inp-packages">Packages delivered</label>
          <input type="number" id="inp-packages" name="packages" min="0" step="1"
                 value="{{ values.packages }}"
                 placeholder="e.g. 47" required>
          {% if errors.packages %}<p class="field-error">{{ errors.packages }}</p>{% endif %}
        </div>
      </div>
```

Apply the same shape to `miles`, `hours`, `extra_expense`, and `note`
(`value="{{ values.miles }}"` and so on). For the driver select, replace the
`entry.driver_id == d.id` comparison with a string comparison against `values`:

```html
          <option value="{{ d.id }}"
            {{ 'selected' if values.driver_id == d.id|string else '' }}>{{ d.name }}</option>
```

And the two hidden-field fallbacks, which preserve fields the settings currently
hide, become:

```html
      {% elif values.hours %}
      <input type="hidden" name="hours" value="{{ values.hours }}">
      {% endif %}
```

```html
      {% elif values.driver_id %}
      <input type="hidden" name="driver_id" value="{{ values.driver_id }}">
      {% endif %}
```

Add a summary banner directly inside the `<form>`, before the first `form-section`:

```html
    {% if errors %}
    <div class="form-error-banner">
      Nothing was saved. Please correct the highlighted fields below.
    </div>
    {% endif %}
```

- [ ] **Step 5: Add the error styling**

Append to `app/static/app.css`:

```css
.field-error {
  color: #dc2626;
  font-size: 0.8rem;
  margin: 0.25rem 0 0;
}
.form-error-banner {
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #991b1b;
  border-radius: 8px;
  padding: 0.75rem 1rem;
  margin-bottom: 1rem;
  font-size: 0.9rem;
}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_app_smoke.py -v`
Expected: PASS, including the new `TestLogValidation` class and every pre-existing
smoke test.

- [ ] **Step 7: Run the whole suite**

Run: `.venv/Scripts/python -m pytest`
Expected: PASS. If a pre-existing test asserted on the old `entry.`-based markup,
update that test to the `values.` equivalent rather than reverting the template.

- [ ] **Step 8: Commit**

```bash
git add app/main.py app/templates/log_day.html app/static/app.css tests/test_app_smoke.py
git commit -m "fix: reject malformed dates and negative numbers on the Log Day form

A malformed date passed straight through to the database, where every
read filters date BETWEEN '1970-01-01' AND '9999-12-31'. Compared as
text, 'not-a-date' sorts above '9999-12-31', so the row was invisible
to History, the dashboard, and the CSV export, and could not be
reached to delete.

Negative packages were likewise accepted server-side because min=0
was only enforced by the browser."
```

---

## Task 3: Validate the Settings form (bug B)

**Files:**
- Modify: `app/main.py` — `settings_page`, `settings_save`
- Modify: `app/templates/settings.html`
- Test: `tests/test_app_smoke.py`

**Interfaces:**
- Consumes: `parse_number` from Task 1.
- Produces: `settings.html` renders `errors` and prefers `values` over `settings`
  for the numeric inputs.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app_smoke.py`:

```python
class TestSettingsValidation:
    def _save(self, client, **overrides):
        data = {"business_name": "Test Co", "pay_per_package": "2.50",
                "gas_price_per_gal": "3.40", "vehicle_mpg": "28"}
        data.update(overrides)
        return client.post("/settings", data=data)

    def test_blank_rate_does_not_silently_reset_to_default(self, client):
        self._save(client, pay_per_package="2.50")
        r = self._save(client, pay_per_package="")
        assert r.status_code == 400
        assert _stored_rate(client) == 2.50

    def test_garbage_rate_is_rejected(self, client):
        self._save(client, pay_per_package="2.50")
        r = self._save(client, pay_per_package="abc")
        assert r.status_code == 400
        assert _stored_rate(client) == 2.50

    def test_negative_rate_is_rejected(self, client):
        self._save(client, pay_per_package="2.50")
        r = self._save(client, pay_per_package="-1")
        assert r.status_code == 400
        assert _stored_rate(client) == 2.50

    def test_blank_gas_price_does_not_reset(self, client):
        self._save(client, gas_price_per_gal="4.10")
        r = self._save(client, gas_price_per_gal="")
        assert r.status_code == 400
        assert _stored_gas(client) == 4.10

    def test_a_rejected_save_writes_no_field_at_all(self, client):
        self._save(client, business_name="Original", pay_per_package="2.50")
        self._save(client, business_name="Changed", pay_per_package="")
        assert _stored_business_name(client) == "Original"

    def test_valid_save_still_works(self, client):
        r = self._save(client, pay_per_package="1.90", follow_redirects=False)
        assert r.status_code in (200, 303)
        assert _stored_rate(client) == 1.90
```

Add the reader helpers alongside the Task 2 helpers, reusing `_scalar`:

```python
def _stored_rate(client):
    return _scalar(client, "SELECT pay_per_package FROM settings WHERE id=1")


def _stored_gas(client):
    return _scalar(client, "SELECT gas_price_per_gal FROM settings WHERE id=1")


def _stored_business_name(client):
    return _scalar(client, "SELECT business_name FROM settings WHERE id=1")
```

Note `_save` passes `follow_redirects` through in the last test only; give `_save` a
`**overrides` signature that pops `follow_redirects` before building the data dict:

```python
    def _save(self, client, follow_redirects=True, **overrides):
        data = {"business_name": "Test Co", "pay_per_package": "2.50",
                "gas_price_per_gal": "3.40", "vehicle_mpg": "28"}
        data.update(overrides)
        return client.post("/settings", data=data,
                           follow_redirects=follow_redirects)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_app_smoke.py -k SettingsValidation -v`
Expected: FAIL — blank rate returns 303 and `_stored_rate` reads 1.65.

- [ ] **Step 3: Rewrite the settings routes**

In `app/main.py`, replace `settings_page` and `settings_save`:

```python
SETTINGS_NUMBERS = (
    ("pay_per_package", "Pay per package"),
    ("gas_price_per_gal", "Gas price per gallon"),
    ("vehicle_mpg", "Fuel economy (MPG)"),
)
EXPENSE_KEYS = ("fuel", "vehicle_wear", "insurance", "phone", "driver")
EXPENSE_LABELS = {
    "fuel": "Fuel", "vehicle_wear": "Vehicle wear",
    "insurance": "Insurance", "phone": "Phone / data",
    "driver": "Driver pay",
}


def _render_settings(request, conn, values, errors, status=200):
    s = settings_repo.get_settings(conn)
    cfg = settings_repo.get_expense_config(conn)
    drivers = drivers_repo.list_drivers(conn)
    return templates.TemplateResponse(request, "settings.html", {
        "settings": s, "expense_config": cfg, "drivers": drivers,
        "values": values, "errors": errors, "active": "settings"},
        status_code=status)


@app.get("/settings")
def settings_page(request: Request):
    with get_db() as conn:
        s = settings_repo.get_settings(conn)
        values = {k: str(s[k]) for k, _ in SETTINGS_NUMBERS}
        return _render_settings(request, conn, values, {})


@app.post("/settings")
async def settings_save(request: Request):
    form = await request.form()
    errors = {}
    numbers = {}
    for key, label in SETTINGS_NUMBERS:
        numbers[key], err = parse_number(form.get(key), label=label)
        if err:
            errors[key] = err

    expenses = {}
    for key in EXPENSE_KEYS:
        amount, err = parse_number(
            form.get(f"exp_{key}_amount"),
            label=f"{EXPENSE_LABELS[key]} amount", required=False)
        if err:
            errors[f"exp_{key}_amount"] = err
        expenses[key] = (bool(form.get(f"exp_{key}_enabled")), amount or 0.0)

    with get_db() as conn:
        if errors:
            # Nothing is written when anything is wrong. A partial save
            # would leave the rate and the costs disagreeing about which
            # submission they came from.
            values = {k: (form.get(k) or "") for k, _ in SETTINGS_NUMBERS}
            return _render_settings(request, conn, values, errors, status=400)
        settings_repo.update_settings(conn, {
            "business_name": form.get("business_name", ""),
            "vehicle_year": form.get("vehicle_year", ""),
            "vehicle_make": form.get("vehicle_make", ""),
            "vehicle_model": form.get("vehicle_model", ""),
            "track_hours": 1 if form.get("track_hours") else 0,
            "drivers_enabled": 1 if form.get("drivers_enabled") else 0,
            **numbers,
        })
        for key, (enabled, amount) in expenses.items():
            settings_repo.update_expense_config(
                conn, key, enabled=enabled, amount=amount)
    return RedirectResponse("/settings", status_code=303)
```

- [ ] **Step 4: Update the settings template**

In `app/templates/settings.html`, point the three numeric inputs at `values` and add
error slots. `pay_per_package` becomes:

```html
      <div class="field">
        <label for="pay_per_package">Pay per package ($)</label>
        <input type="number" id="pay_per_package" name="pay_per_package"
               value="{{ values.pay_per_package }}" min="0" step="0.01"
               placeholder="1.65">
        {% if errors.pay_per_package %}<p class="field-error">{{ errors.pay_per_package }}</p>{% endif %}
      </div>
```

Do the same for `vehicle_mpg` and `gas_price_per_gal`. Add the banner directly
inside the `<form>`, before the first `card`:

```html
  {% if errors %}
  <div class="form-error-banner">
    Nothing was saved. Please correct the highlighted fields below.
  </div>
  {% endif %}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_app_smoke.py -k SettingsValidation -v`
Expected: PASS, 6 tests.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/Scripts/python -m pytest`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/main.py app/templates/settings.html tests/test_app_smoke.py
git commit -m "fix: stop a blank pay field silently resetting the rate to 1.65

Rate fields were read as _f(form.get(...), 1.65), and _f returns its
default for blank and unparseable input alike. Clearing the field and
saving rewrote the rate to Amazon's default with no indication.

That is the most damaging shape of this bug in an app whose whole
premise is frozen historical rates: every day logged afterwards
snapshots the wrong rate permanently, and each one has to be corrected
by hand. A bad value is now an error and nothing is written."
```

---

## Task 4: Driver management (bug A)

**Files:**
- Modify: `app/drivers_repo.py`
- Modify: `app/main.py`
- Modify: `app/templates/settings.html`
- Test: `tests/test_drivers_repo.py`, `tests/test_app_smoke.py`

**Interfaces:**
- Consumes: `_render_settings` from Task 3, `drivers_repo.add_driver` /
  `list_drivers` / `set_driver_active` (all already exist and are currently
  unreachable).
- Produces: `drivers_repo.rename_driver(conn, driver_id, name) -> bool` and three
  routes: `POST /settings/drivers`, `POST /settings/drivers/{id}/rename`,
  `POST /settings/drivers/{id}/active`.

- [ ] **Step 1: Write the failing repo test**

Append to `tests/test_drivers_repo.py`:

```python
def test_rename_driver_changes_the_name(conn):
    driver_id = drivers_repo.add_driver(conn, "Alex")
    assert drivers_repo.rename_driver(conn, driver_id, "Alexandra") is True
    assert drivers_repo.get_driver(conn, driver_id)["name"] == "Alexandra"


def test_rename_driver_reports_unknown_id(conn):
    assert drivers_repo.rename_driver(conn, 999, "Nobody") is False
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_drivers_repo.py -v`
Expected: FAIL — `AttributeError: module 'app.drivers_repo' has no attribute 'rename_driver'`

- [ ] **Step 3: Add the repo function**

Append to `app/drivers_repo.py`:

```python
def rename_driver(conn, driver_id, name):
    cur = conn.execute("UPDATE drivers SET name=? WHERE id=?",
                       (name, driver_id))
    conn.commit()
    return cur.rowcount > 0
```

- [ ] **Step 4: Run it to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_drivers_repo.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing route tests**

Append to `tests/test_app_smoke.py`:

```python
class TestDriverManagement:
    def test_a_driver_can_be_added_and_appears_on_log_day(self, client):
        client.post("/settings", data={
            "business_name": "T", "pay_per_package": "1.65",
            "gas_price_per_gal": "3.40", "vehicle_mpg": "25",
            "drivers_enabled": "1"})
        client.post("/settings/drivers", data={"name": "Alex"})
        assert "Alex" in client.get("/settings").text
        assert "Alex" in client.get("/log").text

    def test_a_blank_driver_name_is_rejected(self, client):
        r = client.post("/settings/drivers", data={"name": "  "})
        assert r.status_code == 400

    def test_a_driver_can_be_renamed(self, client):
        client.post("/settings/drivers", data={"name": "Alex"})
        driver_id = _first_driver_id(client)
        client.post(f"/settings/drivers/{driver_id}/rename",
                    data={"name": "Alexandra"})
        assert "Alexandra" in client.get("/settings").text

    def test_a_deactivated_driver_leaves_the_log_day_dropdown(self, client):
        client.post("/settings", data={
            "business_name": "T", "pay_per_package": "1.65",
            "gas_price_per_gal": "3.40", "vehicle_mpg": "25",
            "drivers_enabled": "1"})
        client.post("/settings/drivers", data={"name": "Alex"})
        driver_id = _first_driver_id(client)
        client.post(f"/settings/drivers/{driver_id}/active",
                    data={"active": "0"})
        page = client.get("/log").text
        select = page.split('id="inp-driver"')[1].split("</select>")[0]
        assert "Alex" not in select

    def test_driver_pay_applies_only_on_a_driver_day(self, client):
        client.post("/settings", data={
            "business_name": "T", "pay_per_package": "1.65",
            "gas_price_per_gal": "3.40", "vehicle_mpg": "25",
            "drivers_enabled": "1",
            "exp_driver_enabled": "1", "exp_driver_amount": "100"})
        client.post("/settings/drivers", data={"name": "Alex"})
        driver_id = _first_driver_id(client)
        client.post("/log", data={
            "date": "2026-08-03", "packages": "100", "miles": "0",
            "driver_id": str(driver_id)})
        client.post("/log", data={
            "date": "2026-08-04", "packages": "100", "miles": "0"})
        # Assert against the CSV, not the HTML: both rows earn $165.00, so
        # a substring check on the page cannot tell the net column from the
        # earnings column.
        rows = list(csv.DictReader(
            io.StringIO(client.get("/history.csv?period=all").text)))
        by_date = {r["date"]: r for r in rows}
        # 100 pkgs x $1.65 = $165.00 earned on both days.
        assert float(by_date["2026-08-03"]["net"]) == 65.0   # less $100 driver pay
        assert float(by_date["2026-08-04"]["net"]) == 165.0  # drove it myself
```

This test needs `import csv` and `import io` at the top of the file.

Add the helper alongside the others:

```python
def _first_driver_id(client):
    return _scalar(client, "SELECT id FROM drivers ORDER BY id LIMIT 1")
```

- [ ] **Step 6: Run them to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_app_smoke.py -k DriverManagement -v`
Expected: FAIL — `POST /settings/drivers` returns 405, the route does not exist.

- [ ] **Step 7: Add the routes**

In `app/main.py`, after `settings_save`:

```python
@app.post("/settings/drivers")
async def driver_add(request: Request):
    form = await request.form()
    name = (form.get("name") or "").strip()
    with get_db() as conn:
        if not name:
            s = settings_repo.get_settings(conn)
            values = {k: str(s[k]) for k, _ in SETTINGS_NUMBERS}
            return _render_settings(request, conn, values,
                                    {"driver_name": "Driver name is required."},
                                    status=400)
        drivers_repo.add_driver(conn, name)
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/drivers/{driver_id}/rename")
async def driver_rename(request: Request, driver_id: int):
    form = await request.form()
    name = (form.get("name") or "").strip()
    with get_db() as conn:
        if not name:
            s = settings_repo.get_settings(conn)
            values = {k: str(s[k]) for k, _ in SETTINGS_NUMBERS}
            return _render_settings(request, conn, values,
                                    {"driver_name": "Driver name is required."},
                                    status=400)
        if not drivers_repo.rename_driver(conn, driver_id, name):
            raise HTTPException(status_code=404, detail="Driver not found")
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/drivers/{driver_id}/active")
async def driver_set_active(request: Request, driver_id: int):
    form = await request.form()
    with get_db() as conn:
        if drivers_repo.get_driver(conn, driver_id) is None:
            raise HTTPException(status_code=404, detail="Driver not found")
        drivers_repo.set_driver_active(
            conn, driver_id, form.get("active") == "1")
    return RedirectResponse("/settings", status_code=303)
```

- [ ] **Step 8: Add the Settings UI**

In `app/templates/settings.html`, add a new card **after the closing `</form>` of the
main settings form** — these are separate forms and must not nest:

```html
{% if settings.drivers_enabled %}
<div class="card settings-section">
  <div class="settings-section-title">Drivers</div>
  <p class="hint">
    Days assigned to a driver are charged the "Driver pay (per day)" expense.
    Days you drove yourself are not.
  </p>

  {% if errors.driver_name %}
  <p class="field-error">{{ errors.driver_name }}</p>
  {% endif %}

  {% for d in drivers %}
  <div class="driver-row">
    <form method="post" action="/settings/drivers/{{ d.id }}/rename"
          class="driver-rename">
      <input type="text" name="name" value="{{ d.name }}" required>
      <button type="submit" class="btn btn-sm btn-ghost">Rename</button>
    </form>
    <form method="post" action="/settings/drivers/{{ d.id }}/active">
      <input type="hidden" name="active" value="{{ '0' if d.active else '1' }}">
      <button type="submit" class="btn btn-sm btn-ghost">
        {{ "Deactivate" if d.active else "Reactivate" }}
      </button>
    </form>
    {% if not d.active %}<span class="text-muted">inactive</span>{% endif %}
  </div>
  {% endfor %}

  <form method="post" action="/settings/drivers" class="driver-add">
    <input type="text" name="name" placeholder="New driver name" required>
    <button type="submit" class="btn btn-ghost">Add driver</button>
  </form>
</div>
{% endif %}
```

Append to `app/static/app.css`:

```css
.driver-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0;
  border-bottom: 1px solid #f1f5f9;
}
.driver-row form { display: flex; gap: 0.5rem; margin: 0; }
.driver-add { display: flex; gap: 0.5rem; margin-top: 0.75rem; }
```

- [ ] **Step 9: Run the tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_app_smoke.py -k DriverManagement -v`
Expected: PASS, 5 tests.

- [ ] **Step 10: Run the whole suite**

Run: `.venv/Scripts/python -m pytest`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add app/drivers_repo.py app/main.py app/templates/settings.html app/static/app.css tests/test_drivers_repo.py tests/test_app_smoke.py
git commit -m "fix: make the documented multi-driver feature reachable

Settings offered a multi-driver toggle, Log Day rendered a Driver
dropdown, and Help/FAQ documented assigning days to drivers and
charging driver pay - but no route ever called add_driver or
set_driver_active. Both were dead code, the dropdown could only ever
contain 'Me', and the per-day driver expense could never fire because
it is gated on driver_id being set.

Adds add, rename, and activate/deactivate in Settings."
```

---

## Task 5: Documentation

**Files:**
- Modify: `app/templates/help.html`
- Modify: `README.md`

- [ ] **Step 1: Document driver management in Help/FAQ**

The existing "What is multi-driver mode?" answer describes assigning entries to
drivers but never says where drivers come from. Extend that answer:

```html
      <p>Turn on <strong>multi-driver mode</strong> in Settings, then add your
      drivers in the <strong>Drivers</strong> section that appears below it. Once a
      driver exists, the Log Day form lets you assign the day to them.</p>
      <p>Deactivating a driver keeps every day they already worked intact and
      removes them from the dropdown for new entries.</p>
```

- [ ] **Step 2: Add the feature to the README**

`README.md` never mentions drivers at all. In the `## What it does` list (starting at
line 11), insert a bullet after the `$/hour` line:

```markdown
- Optional **multi-driver mode** — add drivers, assign a day to one of them, and charge driver pay only on the days they worked
```

- [ ] **Step 3: Verify the docs render**

Run: `.venv/Scripts/python -m pytest tests/test_app_smoke.py -v`
Expected: PASS — the help page smoke test still returns 200.

- [ ] **Step 4: Commit**

```bash
git add app/templates/help.html README.md
git commit -m "docs: explain where drivers come from in Help/FAQ"
```

---

## Verification Before Handoff

- [ ] `.venv/Scripts/python -m pytest` — full suite green.
- [ ] Re-run the original bug probes and confirm each is fixed:
  - Blank `pay_per_package` returns 400 and leaves the stored rate alone.
  - `date=not-a-date` returns 400 and writes no row.
  - `packages=-50` returns 400 and writes no row.
  - A driver can be added through Settings and selected on Log Day.
- [ ] `git log --oneline` shows one commit per task.
- [ ] No `snap_*` column is written anywhere outside `create_entry`.

**Deployment note:** this plan ships no schema change, so it deploys onto the live
`hubprofit_data` volume with no migration. Portainer → Pull and redeploy. Never check
"Remove volumes".
