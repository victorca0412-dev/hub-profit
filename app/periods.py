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
    else:
        start = date(1970, 1, 1)
        end = date(9999, 12, 31)
    return start.isoformat(), end.isoformat()


def days_worked_in_month(entries, year_month):
    return len({e["date"] for e in entries
                if e["date"].startswith(year_month)})


def aggregate(entries):
    month_days = {}
    date_counts = defaultdict(int)
    for e in entries:
        ym = e["date"][:7]
        month_days.setdefault(ym, set()).add(e["date"])
        date_counts[e["date"]] += 1

    by_day = []
    totals = defaultdict(float)
    total_packages = 0
    total_hours = 0.0
    for e in sorted(entries, key=lambda x: x["date"]):
        ym = e["date"][:7]
        dwim = len(month_days[ym])
        r = compute_entry(e, dwim, entries_on_date=date_counts[e["date"]])
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
