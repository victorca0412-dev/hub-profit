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
    start, end = range_for("week", date(2026, 6, 24))
    assert start == "2026-06-22"
    assert end == "2026-06-28"


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
    assert round(agg["total_earnings"], 2) == 148.50
    assert len(agg["by_day"]) == 2
    assert agg["by_day"][0]["date"] == "2026-06-22"
    assert "net" in agg["by_day"][0]
