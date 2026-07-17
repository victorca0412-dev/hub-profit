# HubProfit Edit Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user correct a logged day from the UI — open it in the Log Day form, change what's wrong, save it again.

**Architecture:** Add `update_entry` to the repo layer, then two routes (`GET /log?edit=<id>` and `POST /log/<id>`) that reuse the existing `log_day.html` template rather than duplicating it. The template branches on an optional `entry` variable for its heading, field values, and form action. History gains an Edit link beside Delete.

**Tech Stack:** Python 3, FastAPI, Jinja2, SQLite (stdlib `sqlite3`), pytest, `fastapi.testclient.TestClient`.

**Spec:** `docs/superpowers/specs/2026-07-16-hubprofit-edit-entry-design.md`

**Branch:** `feat/edit-entries-and-tax-setaside` (already checked out; the spec is committed on it)

## Global Constraints

- **No schema change.** This feature writes only to columns that already exist. `app/db.py` must not be modified — it has no migration path (`CREATE TABLE IF NOT EXISTS` only), and the deployed database is already populated.
- **`update_entry` must never write any `snap_*` column.** Re-snapshotting would silently reprice a past day at today's rates, breaking the "rates frozen in history" promise in `README.md`. This is the single most important property in this plan.
- **Reuse `log_day.html`.** Do not create a second edit template. The live estimate in `app/static/app.js` only exists on that one page.
- **No changes to `app/static/app.js`.** `initLogEstimate()` already calls `update()` on init (`app.js:127`), so the estimate populates itself from pre-filled values.
- **Existing tests must keep passing.** Run `pytest` (config in `pytest.ini`, `testpaths = tests`, `addopts = -q`).
- All commands below run from the repo root: `C:\Users\Victor\hub-profit`.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `app/entries_repo.py` | DB access for entries. Gains `update_entry`. | Modify |
| `app/main.py` | Routes. Gains `edit` param on `log_form`, plus `log_update`. | Modify |
| `app/templates/log_day.html` | The one entry form, now serving create and edit. | Modify |
| `app/templates/history.html` | Gains the Edit link. | Modify |
| `app/static/app.css` | Lays Edit and Delete side by side. | Modify |
| `tests/test_entries_repo.py` | Repo-layer tests. | Modify |
| `tests/test_app_smoke.py` | Route/template tests. | Modify |
| `README.md` | Feature list. | Modify |

Note on an existing quirk you'll see in `app/main.py`: `log_submit` names a form parameter `date`, which shadows `from datetime import date` inside that function's body. It's harmless there and the new `log_update` follows the same shape for consistency. Just don't call `date.today()` inside either function.

---

### Task 1: `update_entry` in the repo layer

**Files:**
- Modify: `app/entries_repo.py` (add function after `list_entries`, before `delete_entry`)
- Test: `tests/test_entries_repo.py`

**Interfaces:**
- Consumes: `create_entry(conn, data)`, `get_entry(conn, entry_id)` — both already exist in `app/entries_repo.py`. `update_settings(conn, data)` from `app/settings_repo.py`. The `conn` pytest fixture from `tests/conftest.py` gives a fresh initialised DB.
- Produces: `update_entry(conn, entry_id, data) -> bool`. `data` is a dict with keys `date` (str, `YYYY-MM-DD`), `packages` (int), and optionally `miles` (float, defaults 0.0), `hours` (float|None), `extra_expense` (float|None), `driver_id` (int|None), `note` (str|None). Returns `True` if a row matched, `False` if `entry_id` does not exist. Task 2's routes depend on this exact signature and return type.

- [ ] **Step 1: Write the failing tests**

Add to the imports at the top of `tests/test_entries_repo.py` — the existing import block becomes:

```python
from app.settings_repo import update_settings, update_expense_config
from app.entries_repo import create_entry, list_entries, get_entry, \
    update_entry, delete_entry, distinct_workdays_in_month
```

Append these three tests to the end of `tests/test_entries_repo.py`:

```python
def test_update_entry_changes_user_fields(conn):
    eid = create_entry(conn, {"date": "2026-07-17", "packages": 47,
                              "miles": 38.0, "hours": 2.5,
                              "note": "typo day"})
    ok = update_entry(conn, eid, {"date": "2026-07-16", "packages": 50,
                                  "miles": 40.0, "hours": 3.0,
                                  "note": "fixed"})
    assert ok is True
    e = get_entry(conn, eid)
    assert e["date"] == "2026-07-16"
    assert e["packages"] == 50
    assert e["miles"] == 40.0
    assert e["hours"] == 3.0
    assert e["note"] == "fixed"


def test_update_entry_preserves_snapshots_after_settings_change(conn):
    # The freeze promise: editing a day must not reprice it at today's rates.
    update_settings(conn, {"pay_per_package": 1.65, "gas_price_per_gal": 3.40,
                           "vehicle_mpg": 28.0})
    eid = create_entry(conn, {"date": "2026-07-17", "packages": 47,
                              "miles": 38.0})
    update_settings(conn, {"pay_per_package": 2.00, "gas_price_per_gal": 4.10,
                           "vehicle_mpg": 22.0})
    update_entry(conn, eid, {"date": "2026-07-16", "packages": 47,
                             "miles": 38.0})
    e = get_entry(conn, eid)
    assert e["snap_pay_per_package"] == 1.65
    assert e["snap_gas_price"] == 3.40
    assert e["snap_mpg"] == 28.0


def test_update_entry_returns_false_for_unknown_id(conn):
    assert update_entry(conn, 9999, {"date": "2026-07-16", "packages": 1,
                                     "miles": 0.0}) is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_entries_repo.py -v`

Expected: FAIL at collection with `ImportError: cannot import name 'update_entry' from 'app.entries_repo'`.

- [ ] **Step 3: Write the implementation**

In `app/entries_repo.py`, insert this function between `list_entries` and `delete_entry`:

```python
def update_entry(conn, entry_id, data):
    """Update the user-entered fields of an entry.

    Deliberately does not touch any snap_* column. The frozen rates are what
    keep past days correct when settings change later, so correcting a typo
    must never reprice the day. Returns True if a row matched.
    """
    cur = conn.execute(
        """UPDATE daily_entries
           SET date=?, driver_id=?, packages=?, miles=?, hours=?,
               extra_expense=?, note=?
           WHERE id=?""",
        (data["date"], data.get("driver_id"), data["packages"],
         data.get("miles", 0.0), data.get("hours"), data.get("extra_expense"),
         data.get("note"), entry_id),
    )
    conn.commit()
    return cur.rowcount > 0
```

The column list is written out explicitly rather than built from `data.keys()`. That is the point: an explicit list cannot be tricked into writing a `snap_*` column by a caller passing an unexpected key.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_entries_repo.py -v`

Expected: PASS, 8 tests (5 existing + 3 new).

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest`

Expected: PASS, 47 tests (44 existing + 3 new).

- [ ] **Step 6: Commit**

```bash
git add app/entries_repo.py tests/test_entries_repo.py
git commit -m "feat: add update_entry to entries repo

Writes only the user-entered fields, never the snap_* columns, so
correcting a past day cannot silently reprice it at current rates."
```

---

### Task 2: Edit routes and form reuse

**Files:**
- Modify: `app/main.py:60-69` (`log_form`), and add `log_update` after `log_submit` (`app/main.py:72-83`)
- Modify: `app/main.py:8` (import `HTTPException`)
- Modify: `app/templates/log_day.html`
- Test: `tests/test_app_smoke.py`

**Interfaces:**
- Consumes: `entries_repo.get_entry(conn, entry_id)` (returns an entry dict or `None`), `entries_repo.update_entry(conn, entry_id, data) -> bool` from Task 1, and the module-level `_f(val, default=None)` float parser at `app/main.py:35`.
- Produces: `GET /log?edit=<id>` renders the pre-filled form; `POST /log/<id>` saves and redirects 303 to `/history`. Task 3's Edit link targets `/log?edit=<id>`.

Both routes 404 on an unknown id.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app_smoke.py`:

```python
def test_log_form_without_edit_is_blank_and_defaults_to_today(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    html = client.get("/log").text
    assert "Log a Day" in html
    assert 'action="/log"' in html
    assert 'value="{}"'.format(date.today().isoformat()) in html


def test_edit_form_prefills_the_entry(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    client.post("/log", data={"date": "2026-07-17", "packages": "47",
                              "miles": "38", "hours": "2.5"})
    html = client.get("/log?edit=1").text
    assert "Edit Day" in html
    assert 'value="2026-07-17"' in html
    assert 'value="47"' in html
    assert 'action="/log/1"' in html


def test_edit_form_unknown_id_returns_404(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    assert client.get("/log?edit=9999").status_code == 404


def test_edit_saves_and_moves_entry_between_dates(tmp_path, monkeypatch):
    # The reported bug's actual fix path: 2026-07-17 -> 2026-07-16.
    client = make_client(tmp_path, monkeypatch)
    client.post("/log", data={"date": "2026-07-17", "packages": "47",
                              "miles": "38", "hours": "2.5"})
    resp = client.post("/log/1", data={"date": "2026-07-16", "packages": "50",
                                       "miles": "40", "hours": "3"},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/history"
    html = client.get("/history?period=all").text
    assert "2026-07-16" in html
    assert "2026-07-17" not in html
    assert "50" in html


def test_edit_post_unknown_id_returns_404(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    resp = client.post("/log/9999", data={"date": "2026-07-16",
                                          "packages": "1", "miles": "0"})
    assert resp.status_code == 404
```

Each test gets a fresh temp DB via the `HUBPROFIT_DB` env var in `make_client`, so `AUTOINCREMENT` makes the first entry's id `1`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_app_smoke.py -v`

Expected: 4 of the 5 new tests FAIL.

- `test_edit_form_prefills_the_entry` — fails on `assert "Edit Day" in html`. The `edit` param is ignored today, so a blank "Log a Day" form renders.
- `test_edit_form_unknown_id_returns_404` — fails with `200 != 404`.
- `test_edit_saves_and_moves_entry_between_dates` and `test_edit_post_unknown_id_returns_404` — fail with **405 Method Not Allowed**, because `/log/<id>` does not exist as a route yet.

`test_log_form_without_edit_is_blank_and_defaults_to_today` **passes immediately** — that is expected and correct. It is a characterization test pinning behaviour that already works, so that Task 2's changes to `log_form` and the template cannot silently break the create path. Do not "fix" it.

`date` is already imported in `tests/test_app_smoke.py:2` (`from datetime import date`), so that assertion needs no new import.

- [ ] **Step 3: Import `HTTPException`**

In `app/main.py`, change line 8 from:

```python
from fastapi import FastAPI, Request, Form
```

to:

```python
from fastapi import FastAPI, Request, Form, HTTPException
```

- [ ] **Step 4: Add the `edit` parameter to `log_form`**

Replace `log_form` at `app/main.py:60-69` with:

```python
@app.get("/log")
def log_form(request: Request, edit: int | None = None):
    with get_db() as conn:
        s = settings_repo.get_settings(conn)
        cfg = settings_repo.get_expense_config(conn)
        drivers = drivers_repo.list_drivers(conn, only_active=True)
        entry = None
        if edit is not None:
            entry = entries_repo.get_entry(conn, edit)
            if entry is None:
                raise HTTPException(status_code=404, detail="Entry not found")
    return templates.TemplateResponse(request, "log_day.html", {
        "settings": s, "expense_config": cfg,
        "drivers": drivers, "today": date.today().isoformat(),
        "entry": entry, "active": "log"})
```

- [ ] **Step 5: Add the `log_update` route**

In `app/main.py`, insert this immediately after `log_submit` (which ends at line 83) and before the `/history` route:

```python
@app.post("/log/{entry_id}")
def log_update(entry_id: int, date: str = Form(...), packages: int = Form(...),
               miles: float = Form(0.0), hours: str = Form(""),
               extra_expense: str = Form(""), driver_id: str = Form(""),
               note: str = Form("")):
    with get_db() as conn:
        ok = entries_repo.update_entry(conn, entry_id, {
            "date": date, "packages": packages, "miles": miles,
            "hours": _f(hours), "extra_expense": _f(extra_expense),
            "driver_id": int(driver_id) if driver_id else None,
            "note": note or None})
    if not ok:
        raise HTTPException(status_code=404, detail="Entry not found")
    return RedirectResponse("/history", status_code=303)
```

This mirrors `log_submit` exactly, including the `date` parameter shadowing the `datetime.date` import — consistent with the existing code, and safe because neither function calls `date.today()`.

- [ ] **Step 6: Update the template**

In `app/templates/log_day.html`, make these six edits.

Line 4 — the heading:

```html
<h1 class="page-title">{{ "Edit Day" if entry else "Log a Day" }}</h1>
```

Line 15 — the form action:

```html
  <form method="post" action="{{ '/log/' ~ entry.id if entry else '/log' }}">
```

Line 23 — the date, defaulting to today only when creating:

```html
          <input type="date" id="inp-date" name="date"
                 value="{{ entry.date if entry else today }}" required>
```

Lines 27-28 — packages:

```html
          <input type="number" id="inp-packages" name="packages" min="0" step="1"
                 value="{{ entry.packages if entry else '' }}"
                 placeholder="e.g. 47" required>
```

Lines 35-36 — miles:

```html
        <input type="number" id="inp-miles" name="miles" min="0" step="0.1"
               value="{{ entry.miles if entry else '' }}"
               placeholder="e.g. 38.5">
```

Lines 42-43 — hours (nullable, so guard against printing `None`):

```html
      <input type="number" id="inp-hours" name="hours" min="0" step="0.25"
             value="{{ entry.hours if entry and entry.hours is not none else '' }}"
             placeholder="e.g. 3.5">
```

Lines 54-55 — extra expense (also nullable):

```html
        <input type="number" id="inp-extra" name="extra_expense" min="0" step="0.01"
               value="{{ entry.extra_expense if entry and entry.extra_expense is not none else '' }}"
               placeholder="0.00">
```

Lines 61-67 — the driver select, preserving the assigned driver:

```html
        <select id="inp-driver" name="driver_id">
          <option value="">Me</option>
          {% for d in drivers %}
          <option value="{{ d.id }}"
            {{ 'selected' if entry and entry.driver_id == d.id else '' }}>{{ d.name }}</option>
          {% endfor %}
        </select>
```

Line 72 — the note:

```html
        <input type="text" id="inp-note" name="note"
               value="{{ entry.note if entry and entry.note else '' }}"
               placeholder="e.g. heavy rain, late start">
```

Line 95 — the submit button:

```html
      <button type="submit" class="btn btn-primary">{{ "Save Changes" if entry else "Save Day" }}</button>
```

`entry` is a plain dict. Jinja's `entry.date` resolves via item lookup when the attribute is absent, which is the pattern `history.html` already uses for its `r.date` / `r.packages` rows.

The `#log-config` blob at lines 7-12 is untouched — it reads from `settings`, not `entry`, and the estimate should preview against current rates while you type.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `python -m pytest tests/test_app_smoke.py -v`

Expected: PASS, 10 tests (5 existing + 5 new).

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest`

Expected: PASS, 52 tests.

- [ ] **Step 9: Commit**

```bash
git add app/main.py app/templates/log_day.html tests/test_app_smoke.py
git commit -m "feat: add GET /log?edit=<id> and POST /log/<id>

Reuses log_day.html rather than duplicating the form, so the live
earnings estimate in app.js works on the edit path unchanged. Both
routes 404 on an unknown id."
```

---

### Task 3: History Edit button

**Files:**
- Modify: `app/templates/history.html:50-54` (the actions cell)
- Modify: `app/static/app.css` (append to the HISTORY section, after line 370)
- Modify: `README.md`
- Test: `tests/test_app_smoke.py`

**Interfaces:**
- Consumes: `GET /log?edit=<id>` from Task 2. Each history row `r` already carries `r.id`.
- Produces: nothing consumed by later tasks — this is the last task.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_app_smoke.py`:

```python
def test_history_row_has_edit_link(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    client.post("/log", data={"date": "2026-07-17", "packages": "47",
                              "miles": "38"})
    html = client.get("/history?period=all").text
    assert 'href="/log?edit=1"' in html
    assert "Delete" in html  # delete still available
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_app_smoke.py::test_history_row_has_edit_link -v`

Expected: FAIL with `assert 'href="/log?edit=1"' in html`.

- [ ] **Step 3: Add the Edit link**

In `app/templates/history.html`, replace the actions cell at lines 50-54:

```html
        <td>
          <div class="row-actions">
            <a class="btn btn-sm btn-ghost" href="/log?edit={{ r.id }}">Edit</a>
            <form method="post" action="/history/delete/{{ r.id }}" class="delete-day-form">
              <button type="submit" class="btn btn-sm btn-danger">Delete</button>
            </form>
          </div>
        </td>
```

`btn-ghost` already exists at `app/static/app.css:334`. The `delete-day-form` class stays — `app.js:309` hooks it for the "Delete this day?" confirm.

- [ ] **Step 4: Add the layout CSS**

In `app/static/app.css`, append after line 370 (`.data-table tr:last-child td { border-bottom: none; }` / `.data-table tr:hover td`), inside the HISTORY section:

```css
.row-actions {
  display: flex;
  gap: 0.4rem;
  align-items: center;
}
.row-actions a.btn:hover { text-decoration: none; }
```

The `:hover` rule is needed because `app/static/app.css:26` sets `a:hover { text-decoration: underline; }` globally, which would underline the Edit button on hover. `.csv-link:hover` and `.period-switcher a:hover` already suppress it the same way.

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_app_smoke.py::test_history_row_has_edit_link -v`

Expected: PASS.

- [ ] **Step 6: Update the README feature list**

In `README.md`, in the "What it does" bullet list, add a bullet after the "Logs each day's **packages delivered** and **miles driven**" line:

```markdown
- **Edit any logged day** — fix a typo or a wrong date without deleting and re-entering it
```

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest`

Expected: PASS, 53 tests (44 original + 9 new).

- [ ] **Step 8: Commit**

```bash
git add app/templates/history.html app/static/app.css README.md tests/test_app_smoke.py
git commit -m "feat: add Edit button to history rows

Links to /log?edit=<id>. Delete keeps its confirm dialog."
```

---

## Manual verification

After Task 3, drive the real app rather than trusting the tests alone:

```bash
python -m uvicorn app.main:app --reload --port 8000
```

1. Open `http://localhost:8000/log`, log a day dated `2026-07-17`. Heading reads "Log a Day", button reads "Save Day".
2. Open `http://localhost:8000/history`. The row shows Edit and Delete side by side.
3. Click **Edit**. The form opens pre-filled, heading reads "Edit Day", button reads "Save Changes", and the Estimated Earnings Preview already shows the right numbers without touching a field — that's `app.js:127` calling `update()` on init.
4. Change the date to `2026-07-16` and save. History shows `2026-07-16`, and only one row exists.
5. Change the gas price in Settings, then edit that day again and save. Its net must not move — the snapshot held.

Step 5 is the one that matters most. It is the freeze promise, checked by hand.

## Deployment

Once merged, deploy per established homelab practice:

```bash
git pull && docker compose build && docker compose up -d
```

The build step is mandatory. **Never** check "Remove volumes" on the Portainer redeploy — it would destroy the `hubprofit_data` volume holding the real entries.

No schema change ships here, so the deployed database needs no special handling.

Afterwards, in the live app: History → Edit on the `2026-07-17` row → change to `2026-07-16` → Save. That closes the original report.

## Known follow-up

The timezone bug that caused the wrong date is deliberately out of scope (see the spec's non-goals). The Log Day form will keep pre-filling tomorrow's date every evening after 8pm ET until `TZ` is set in `docker-compose.yml` and `tzdata` is installed in the `Dockerfile`. Edit is the workaround, not the fix.
