"""Seed a throwaway demo database for the README screenshots.

Deliberately uses invented business names and plausible-but-fake numbers.
Never point this at a real database - it writes into HUBPROFIT_DB, which
this script sets to data/demo.db before importing the app.

    .venv/Scripts/python scripts/seed_demo.py
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEMO_DB = ROOT / "data" / "demo.db"
DEMO_DB.parent.mkdir(parents=True, exist_ok=True)
if DEMO_DB.exists():
    DEMO_DB.unlink()
os.environ["HUBPROFIT_DB"] = str(DEMO_DB)

from app.db import init_db, get_conn                      # noqa: E402
from app import businesses_repo, settings_repo, entries_repo  # noqa: E402

init_db(str(DEMO_DB))
conn = get_conn(str(DEMO_DB))

# ── Global vehicle / fuel ────────────────────────────────────────────
settings_repo.update_settings(conn, {
    "vehicle_year": "2019", "vehicle_make": "Toyota",
    "vehicle_model": "RAV4", "vehicle_mpg": 28.0,
    "gas_price_per_gal": 3.29, "track_hours": 1,
})

# ── Business 1: flat rate, the common case ───────────────────────────
businesses_repo.rename_business(conn, 1, "Riverside Delivery Co")
businesses_repo.update_business(conn, 1, {"pay_per_package": 1.65})
settings_repo.update_expense_config(conn, 1, "fuel", enabled=True)
# A part-time gig carries a share of these, not the whole household bill.
settings_repo.update_expense_config(conn, 1, "insurance",
                                    enabled=True, amount=85.00)
settings_repo.update_expense_config(conn, 1, "phone",
                                    enabled=True, amount=30.00)
settings_repo.update_expense_config(conn, 1, "vehicle_wear",
                                    enabled=True, amount=0.12)

# ── Business 2: the older fluctuating contract ───────────────────────
second = businesses_repo.create_business(conn, "Northgate Hub")
businesses_repo.update_business(conn, second, {
    "pay_per_package": 1.65, "rate_model": "tiered"})
businesses_repo.replace_tiers(conn, second,
                              [(20, 2.25), (40, 1.95), (None, 1.65)])
settings_repo.update_expense_config(conn, second, "fuel", enabled=True)
settings_repo.update_expense_config(conn, second, "insurance",
                                    enabled=True, amount=40.00)

# ── Four weeks of weekdays, ending on the most recent weekday ────────
# Ending on the latest weekday (today, if today is one) matters: the
# dashboard defaults to the "week" view, and data that stops last Friday
# leaves that default landing page empty.
today = date.today()
last_day = today
while last_day.weekday() >= 5:          # rewind off Sat/Sun
    last_day -= timedelta(days=1)

PATTERN = [  # packages, miles, hours
    (47, 38.5, 3.25), (52, 41.0, 3.5), (39, 31.5, 2.75), (61, 47.5, 4.0),
    (44, 35.0, 3.0),  (58, 45.0, 3.75), (36, 29.0, 2.5), (49, 39.5, 3.25),
    (55, 43.0, 3.5),  (42, 33.5, 2.75), (63, 49.0, 4.25), (38, 30.5, 2.5),
    (51, 40.5, 3.25), (46, 36.5, 3.0),  (57, 44.5, 3.75), (41, 32.5, 2.75),
    (53, 42.0, 3.5),  (48, 38.0, 3.25), (60, 46.5, 4.0),  (43, 34.0, 2.75),
]

day = last_day
placed = 0
while placed < len(PATTERN):
    if day.weekday() < 5:                      # weekdays only
        pkgs, miles, hours = PATTERN[placed]
        entries_repo.create_entry(conn, {
            "date": day.isoformat(), "packages": pkgs,
            "miles": miles, "hours": hours}, business_id=1)
        placed += 1
    day -= timedelta(days=1)

# A handful of days on the tiered business, sized to land in different
# tiers so the rate column shows more than one value.
for offset, pkgs in enumerate([18, 34, 45, 27, 52]):
    d = last_day - timedelta(days=offset)
    entries_repo.create_entry(conn, {
        "date": d.isoformat(), "packages": pkgs,
        "miles": round(pkgs * 0.8, 1), "hours": round(pkgs / 15, 2)},
        business_id=second)

n1 = conn.execute("SELECT COUNT(*) FROM daily_entries "
                  "WHERE business_id=1").fetchone()[0]
n2 = conn.execute("SELECT COUNT(*) FROM daily_entries "
                  "WHERE business_id=?", (second,)).fetchone()[0]
conn.close()

print(f"Seeded {DEMO_DB}")
print(f"  Riverside Delivery Co : {n1} days (flat $1.65)")
print(f"  Northgate Hub         : {n2} days (tiered 1-20/21-40/41+)")
print()
print("Run the demo server with:")
print(f'  HUBPROFIT_DB="{DEMO_DB}" '
      ".venv/Scripts/python -m uvicorn app.main:app --port 8100")
