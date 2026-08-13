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


def _rate_from_tiers(packages, tiers):
    """Whole-block lookup: the count picks one tier, all packages pay it.

    Deliberately NOT graduated brackets. On tiers 1-20/21-40/41+ at
    $2.25/$1.95/$1.65 a 45-package day earns 45 x $1.65 = $74.25, not
    $92.25. Amazon's block offers priced the whole block by its size,
    which is why smaller blocks paid more per package.
    """
    for tier in tiers or []:
        low = tier["min_packages"]
        high = tier["max_packages"]
        if packages >= low and (high is None or packages <= high):
            return tier["rate"]
    return None


def rate_for_entry(entry):
    """The per-package rate this entry actually pays."""
    if entry.get("snap_rate_model") == "tiered":
        rate = _rate_from_tiers(entry["packages"],
                                entry.get("snap_rate_tiers"))
        if rate is not None:
            return rate
        # A malformed or empty tier table must not zero out a real day's
        # earnings. Every entry carries a flat rate to fall back on.
    return entry["snap_pay_per_package"]


def earnings_for(entry):
    return entry["packages"] * rate_for_entry(entry)


def compute_entry(entry, days_worked_in_month, entries_on_date=1):
    earnings = earnings_for(entry)
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
        "rate": rate_for_entry(entry),
        "expenses": expenses,
        "extra_expense": extra,
        "total_expenses": total_expenses,
        "net": net,
        "hourly": hourly,
    }
