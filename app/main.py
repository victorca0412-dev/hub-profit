import csv
import io
import os
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import init_db, get_conn
from app import (settings_repo, entries_repo, drivers_repo, businesses_repo,
                 periods, fueleconomy)
from app.validation import parse_date, parse_number, parse_int

DB_PATH = os.environ.get("HUBPROFIT_DB", "data/hub.db")
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
init_db(DB_PATH)

BASE_DIR = Path(__file__).parent
app = FastAPI(title="HubProfit")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

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
LOG_FIELDS = ("date", "packages", "miles", "hours", "extra_expense",
              "driver_id", "note")


@contextmanager
def get_db():
    conn = get_conn(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


# ── Active business ──────────────────────────────────────────────────

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


def _month_counts(conn, entries, business_id):
    months = {e["date"][:7] for e in entries}
    return {ym: entries_repo.distinct_workdays_in_month(conn, ym, business_id)
            for ym in months}


# ── Dashboard ────────────────────────────────────────────────────────

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


# ── Log Day ──────────────────────────────────────────────────────────

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


def _render_log(request, conn, business, entry, values, errors, status=200):
    cfg = settings_repo.get_expense_config(conn, business["id"])
    drivers = drivers_repo.list_drivers(conn, business["id"], only_active=True)
    if entry and entry["driver_id"] is not None and \
            not any(d["id"] == entry["driver_id"] for d in drivers):
        # Editing must not drop the day's assigned driver just because
        # they were later deactivated: keep them selectable.
        assigned = drivers_repo.get_driver(conn, entry["driver_id"],
                                           business["id"])
        if assigned is not None:
            drivers.append(assigned)
    return templates.TemplateResponse(request, "log_day.html", _ctx(
        conn, business=business, expense_config=cfg, drivers=drivers,
        tiers=businesses_repo.get_tiers(conn, business["id"]),
        today=date.today().isoformat(), entry=entry, values=values,
        errors=errors, active="log"), status_code=status)


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
        business = _active_business(conn)
        entry = None
        if edit is not None:
            entry = entries_repo.get_entry(conn, edit, business["id"])
            if entry is None:
                raise HTTPException(status_code=404, detail="Entry not found")
        values = _log_values(entry=entry)
        if entry is None:
            values["date"] = date.today().isoformat()
        return _render_log(request, conn, business, entry, values, {})


@app.post("/log")
async def log_submit(request: Request):
    form = await request.form()
    data, errors = _parse_log_form(form)
    with get_db() as conn:
        business = _active_business(conn)
        if errors:
            return _render_log(request, conn, business, None,
                               _log_values(form=form), errors, status=400)
        entries_repo.create_entry(conn, data, business["id"])
    return RedirectResponse("/", status_code=303)


@app.post("/log/{entry_id}")
async def log_update(request: Request, entry_id: int):
    form = await request.form()
    data, errors = _parse_log_form(form)
    with get_db() as conn:
        business = _active_business(conn)
        entry = entries_repo.get_entry(conn, entry_id, business["id"])
        if entry is None:
            raise HTTPException(status_code=404, detail="Entry not found")
        if errors:
            return _render_log(request, conn, business, entry,
                               _log_values(entry=entry, form=form),
                               errors, status=400)
        entries_repo.update_entry(conn, entry_id, data, business["id"])
    return RedirectResponse("/history", status_code=303)


# ── History ──────────────────────────────────────────────────────────

@app.get("/history")
def history(request: Request, period: str = "all"):
    with get_db() as conn:
        business = _active_business(conn)
        start, end = periods.range_for(period)
        entries = entries_repo.list_entries(conn, start, end, business["id"])
        mdc = _month_counts(conn, entries, business["id"])
        rows = [{**r["entry"], "computed": r["computed"]}
                for r in periods.computed_entries(entries, mdc)]
        return templates.TemplateResponse(request, "history.html", _ctx(
            conn, business=business, rows=rows, period=period,
            active="history"))


@app.post("/history/delete/{entry_id}")
def history_delete(entry_id: int):
    with get_db() as conn:
        business = _active_business(conn)
        entries_repo.delete_entry(conn, entry_id, business["id"])
    return RedirectResponse("/history", status_code=303)


@app.get("/history.csv")
def history_csv(period: str = "all"):
    with get_db() as conn:
        business = _active_business(conn)
        start, end = periods.range_for(period)
        entries = entries_repo.list_entries(conn, start, end, business["id"])
        mdc = _month_counts(conn, entries, business["id"])
        rows = periods.computed_entries(entries, mdc)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "packages", "miles", "rate", "hours", "earnings",
                "expenses", "net", "hourly"])
    for r in sorted(rows, key=lambda x: x["entry"]["date"]):
        e, c = r["entry"], r["computed"]
        w.writerow([e["date"], e["packages"], e["miles"],
                    round(c["rate"], 4), e.get("hours") or "",
                    round(c["earnings"], 2), round(c["total_expenses"], 2),
                    round(c["net"], 2),
                    round(c["hourly"], 2) if c["hourly"] is not None else ""])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=hubprofit.csv"})


# ── Settings ─────────────────────────────────────────────────────────

def _stored_settings_values(conn, business):
    s = settings_repo.get_settings(conn)
    return {"pay_per_package": str(business["pay_per_package"]),
            "gas_price_per_gal": str(s["gas_price_per_gal"]),
            "vehicle_mpg": str(s["vehicle_mpg"])}


def _render_settings(request, conn, business, values, errors, status=200):
    cfg = settings_repo.get_expense_config(conn, business["id"])
    drivers = drivers_repo.list_drivers(conn, business["id"])
    tiers = businesses_repo.get_tiers(conn, business["id"])
    return templates.TemplateResponse(request, "settings.html", _ctx(
        conn, business=business, expense_config=cfg, drivers=drivers,
        tiers=tiers, values=values, errors=errors, active="settings"),
        status_code=status)


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
        ceiling, err = parse_int(raw_to,
                                 label=f"Tier {index + 1} upper limit")
        if err:
            return [], err
        if ceiling < low:
            return [], (f"Tier {index + 1} must end at {low} or higher - "
                        "each tier has to start above the one before it.")
        tiers.append((ceiling, rate))
        low = ceiling + 1
    return tiers, None


@app.get("/settings")
def settings_page(request: Request):
    with get_db() as conn:
        business = _active_business(conn)
        return _render_settings(request, conn, business,
                                _stored_settings_values(conn, business), {})


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

    rate_model = "tiered" if form.get("rate_model") == "tiered" else "flat"
    tiers = []
    if rate_model == "tiered":
        tiers, tier_err = _parse_tier_form(form)
        if tier_err:
            errors["tiers"] = tier_err

    with get_db() as conn:
        business = _active_business(conn)
        if errors:
            # Nothing is written when anything is wrong. A partial save
            # would leave the rate and the costs disagreeing about which
            # submission they came from.
            values = {k: (form.get(k) or "") for k, _ in SETTINGS_NUMBERS}
            return _render_settings(request, conn, business, values, errors,
                                    status=400)
        settings_repo.update_settings(conn, {
            "vehicle_year": form.get("vehicle_year", ""),
            "vehicle_make": form.get("vehicle_make", ""),
            "vehicle_model": form.get("vehicle_model", ""),
            "track_hours": 1 if form.get("track_hours") else 0,
            "gas_price_per_gal": numbers["gas_price_per_gal"],
            "vehicle_mpg": numbers["vehicle_mpg"],
        })
        businesses_repo.update_business(conn, business["id"], {
            "name": (form.get("business_name") or "").strip()
                    or business["name"],
            "pay_per_package": numbers["pay_per_package"],
            "drivers_enabled": 1 if form.get("drivers_enabled") else 0,
            "rate_model": rate_model,
        })
        if rate_model == "tiered":
            # Switching to flat deliberately leaves the rows in place, so
            # toggling back and forth does not destroy the table.
            businesses_repo.replace_tiers(conn, business["id"], tiers)
        for key, (enabled, amount) in expenses.items():
            settings_repo.update_expense_config(
                conn, business["id"], key, enabled=enabled, amount=amount)
    return RedirectResponse("/settings", status_code=303)


# ── Drivers ──────────────────────────────────────────────────────────

@app.post("/settings/drivers")
async def driver_add(request: Request):
    form = await request.form()
    name = (form.get("name") or "").strip()
    with get_db() as conn:
        business = _active_business(conn)
        if not name:
            return _render_settings(
                request, conn, business,
                _stored_settings_values(conn, business),
                {"driver_name": "Driver name is required."}, status=400)
        drivers_repo.add_driver(conn, name, business["id"])
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/drivers/{driver_id}/rename")
async def driver_rename(request: Request, driver_id: int):
    form = await request.form()
    name = (form.get("name") or "").strip()
    with get_db() as conn:
        business = _active_business(conn)
        if not name:
            return _render_settings(
                request, conn, business,
                _stored_settings_values(conn, business),
                {"driver_name": "Driver name is required."}, status=400)
        if not drivers_repo.rename_driver(conn, driver_id, name,
                                          business["id"]):
            raise HTTPException(status_code=404, detail="Driver not found")
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/drivers/{driver_id}/active")
async def driver_set_active(request: Request, driver_id: int):
    form = await request.form()
    with get_db() as conn:
        business = _active_business(conn)
        if drivers_repo.get_driver(conn, driver_id, business["id"]) is None:
            raise HTTPException(status_code=404, detail="Driver not found")
        drivers_repo.set_driver_active(
            conn, driver_id, form.get("active") == "1", business["id"])
    return RedirectResponse("/settings", status_code=303)


# ── Businesses ───────────────────────────────────────────────────────

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


# ── Help & vehicle API ───────────────────────────────────────────────

@app.get("/help")
def help_page(request: Request):
    with get_db() as conn:
        return templates.TemplateResponse(request, "help.html",
                                          _ctx(conn, active="help"))


@app.get("/api/makes")
def api_makes(year: str):
    try:
        return JSONResponse(fueleconomy.get_makes(year))
    except Exception:
        return JSONResponse({"error": "vehicle service unavailable"},
                            status_code=502)


@app.get("/api/models")
def api_models(year: str, make: str):
    try:
        return JSONResponse(fueleconomy.get_models(year, make))
    except Exception:
        return JSONResponse({"error": "vehicle service unavailable"},
                            status_code=502)


@app.post("/api/lookup_mpg")
def api_lookup_mpg(year: str = Form(...), make: str = Form(...),
                   model: str = Form(...)):
    try:
        with get_db() as conn:
            mpg = fueleconomy.cached_mpg(conn, year, make, model)
    except Exception:
        return JSONResponse({"error": "vehicle service unavailable"},
                            status_code=502)
    return JSONResponse({"mpg": mpg})
