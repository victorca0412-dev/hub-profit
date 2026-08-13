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
from app import settings_repo, entries_repo, drivers_repo, periods, fueleconomy
from app.validation import parse_date, parse_number, parse_int

DB_PATH = os.environ.get("HUBPROFIT_DB", "data/hub.db")
Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
init_db(DB_PATH)

BASE_DIR = Path(__file__).parent
app = FastAPI(title="HubProfit")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@contextmanager
def get_db():
    conn = get_conn(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def _month_counts(conn, entries):
    months = {e["date"][:7] for e in entries}
    return {ym: entries_repo.distinct_workdays_in_month(conn, ym)
            for ym in months}


@app.get("/")
def dashboard(request: Request, period: str = "week"):
    with get_db() as conn:
        start, end = periods.range_for(period)
        entries = entries_repo.list_entries(conn, start, end)
        mdc = _month_counts(conn, entries)
        agg = periods.aggregate(entries, month_day_counts=mdc)
        s = settings_repo.get_settings(conn)
    return templates.TemplateResponse(request, "dashboard.html", {
        "agg": agg, "period": period, "settings": s, "active": "dashboard"})


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


@app.get("/history")
def history(request: Request, period: str = "all"):
    with get_db() as conn:
        start, end = periods.range_for(period)
        entries = entries_repo.list_entries(conn, start, end)
        mdc = _month_counts(conn, entries)
        rows = [{**r["entry"], "computed": r["computed"]}
                for r in periods.computed_entries(entries, mdc)]
    return templates.TemplateResponse(request, "history.html", {
        "rows": rows, "period": period, "active": "history"})


@app.post("/history/delete/{entry_id}")
def history_delete(entry_id: int):
    with get_db() as conn:
        entries_repo.delete_entry(conn, entry_id)
    return RedirectResponse("/history", status_code=303)


@app.get("/history.csv")
def history_csv(period: str = "all"):
    with get_db() as conn:
        start, end = periods.range_for(period)
        entries = entries_repo.list_entries(conn, start, end)
        mdc = _month_counts(conn, entries)
        rows = periods.computed_entries(entries, mdc)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "packages", "miles", "hours", "earnings",
                "expenses", "net", "hourly"])
    for r in sorted(rows, key=lambda x: x["entry"]["date"]):
        e, c = r["entry"], r["computed"]
        w.writerow([e["date"], e["packages"], e["miles"], e.get("hours") or "",
                    round(c["earnings"], 2), round(c["total_expenses"], 2),
                    round(c["net"], 2),
                    round(c["hourly"], 2) if c["hourly"] is not None else ""])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=hubprofit.csv"})


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


def _stored_settings_values(conn):
    s = settings_repo.get_settings(conn)
    return {k: str(s[k]) for k, _ in SETTINGS_NUMBERS}


@app.get("/settings")
def settings_page(request: Request):
    with get_db() as conn:
        return _render_settings(request, conn,
                                _stored_settings_values(conn), {})


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


@app.get("/help")
def help_page(request: Request):
    return templates.TemplateResponse(request, "help.html", {"active": "help"})


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
