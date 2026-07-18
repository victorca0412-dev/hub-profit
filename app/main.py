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
    with get_db() as conn:
        start, end = periods.range_for(period)
        entries = entries_repo.list_entries(conn, start, end)
        mdc = _month_counts(conn, entries)
        agg = periods.aggregate(entries, month_day_counts=mdc)
        s = settings_repo.get_settings(conn)
    return templates.TemplateResponse(request, "dashboard.html", {
        "agg": agg, "period": period, "settings": s, "active": "dashboard"})


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
            # Editing must not drop the day's assigned driver just because
            # they were later deactivated: make sure they stay selectable.
            if entry["driver_id"] is not None and \
                    not any(d["id"] == entry["driver_id"] for d in drivers):
                assigned = drivers_repo.get_driver(conn, entry["driver_id"])
                if assigned is not None:
                    drivers.append(assigned)
    return templates.TemplateResponse(request, "log_day.html", {
        "settings": s, "expense_config": cfg,
        "drivers": drivers, "today": date.today().isoformat(),
        "entry": entry, "active": "log"})


@app.post("/log")
def log_submit(date: str = Form(...), packages: int = Form(...),
               miles: float = Form(0.0), hours: str = Form(""),
               extra_expense: str = Form(""), driver_id: str = Form(""),
               note: str = Form("")):
    with get_db() as conn:
        entries_repo.create_entry(conn, {
            "date": date, "packages": packages, "miles": miles,
            "hours": _f(hours), "extra_expense": _f(extra_expense),
            "driver_id": int(driver_id) if driver_id else None,
            "note": note or None})
    return RedirectResponse("/", status_code=303)


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


@app.get("/settings")
def settings_page(request: Request):
    with get_db() as conn:
        s = settings_repo.get_settings(conn)
        cfg = settings_repo.get_expense_config(conn)
    return templates.TemplateResponse(request, "settings.html", {
        "settings": s, "expense_config": cfg, "active": "settings"})


@app.post("/settings")
async def settings_save(request: Request):
    form = await request.form()
    with get_db() as conn:
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
