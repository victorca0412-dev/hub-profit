"""Pure financial calculations. No DB access.

An "entry" is a dict with: packages, miles, hours (or None),
extra_expense (or None), driver_id (or None), and the frozen snapshot
fields snap_pay_per_package, snap_gas_price, snap_mpg, and
snap_expense_config (a dict keyed by expense name).
"""


def _expense_cost(key, cfg, entry, days_worked_in_month, entries_on_date):
    mode = cfg["mode"]
    amount = cfg["amount"]
    miles = entry["miles"]
    if mode == "mileage_fuel":
        mpg = entry["snap_mpg"]
        if mpg <= 0:
            return 0.0
        return miles / mpg * entry["snap_gas_price"]
    if mode == "per_mile":
        return miles * amount
    if mode == "monthly":
        # A monthly fixed cost is spread across the distinct work days in the
        # month. When several entries share one date (e.g. multiple drivers),
        # that day's single share is split evenly among them so the date is
        # never charged more than once.
        days = days_worked_in_month if days_worked_in_month > 0 else 1
        per_entry = entries_on_date if entries_on_date > 0 else 1
        return amount / days / per_entry
    if mode == "per_day":
        if key == "driver" and entry.get("driver_id") is None:
            return None  # driver pay only applies on driver-assigned days
        return amount
    return 0.0


def compute_entry(entry, days_worked_in_month, entries_on_date=1):
    earnings = entry["packages"] * entry["snap_pay_per_package"]
    expenses = {}
    for key, cfg in entry["snap_expense_config"].items():
        if not cfg.get("enabled"):
            continue
        cost = _expense_cost(key, cfg, entry, days_worked_in_month,
                             entries_on_date)
        if cost is None:
            continue
        expenses[key] = cost
    extra = entry.get("extra_expense") or 0.0
    total_expenses = sum(expenses.values()) + extra
    net = earnings - total_expenses
    hours = entry.get("hours")
    hourly = (net / hours) if hours else None
    return {
        "earnings": earnings,
        "expenses": expenses,
        "extra_expense": extra,
        "total_expenses": total_expenses,
        "net": net,
        "hourly": hourly,
    }
