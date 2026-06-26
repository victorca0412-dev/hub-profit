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
    try:
        return float(val) if val not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _month_counts(conn, entries):
    months = {e["date"][:7] for e in entries}
    return {ym: entries_repo.distinct_workdays_in_month(conn, ym)
            for ym in months}


@app.get("/")
def dashboard(request: Request, period: str = "week"):
    conn = db()
    start, end = periods.range_for(period)
    entries = entries_repo.list_entries(conn, start, end)
    mdc = _month_counts(conn, entries)
    agg = periods.aggregate(entries, month_day_counts=mdc)
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
    mdc = _month_counts(conn, entries)
    rows = [{**r["entry"], "computed": r["computed"]}
            for r in periods.computed_entries(entries, mdc)]
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
