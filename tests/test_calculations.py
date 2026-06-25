from app.calculations import compute_entry

BASE_CONFIG = {
    "fuel": {"enabled": True, "mode": "mileage_fuel", "amount": 0.0},
    "vehicle_wear": {"enabled": False, "mode": "per_mile", "amount": 0.18},
    "insurance": {"enabled": True, "mode": "monthly", "amount": 140.0},
    "phone": {"enabled": True, "mode": "monthly", "amount": 40.0},
    "driver": {"enabled": False, "mode": "per_day", "amount": 0.0},
}


def make_entry(**over):
    e = {
        "packages": 47, "miles": 38.0, "hours": 2.5,
        "extra_expense": 0.0, "driver_id": None,
        "snap_pay_per_package": 1.65, "snap_gas_price": 3.40,
        "snap_mpg": 28.0, "snap_expense_config": BASE_CONFIG,
    }
    e.update(over)
    return e


def test_earnings():
    r = compute_entry(make_entry(), days_worked_in_month=20)
    assert round(r["earnings"], 2) == 77.55


def test_fuel_estimate():
    r = compute_entry(make_entry(), days_worked_in_month=20)
    assert round(r["expenses"]["fuel"], 2) == 4.61


def test_fixed_costs_spread_across_days():
    r = compute_entry(make_entry(), days_worked_in_month=20)
    assert round(r["expenses"]["insurance"], 2) == 7.00
    assert round(r["expenses"]["phone"], 2) == 2.00


def test_disabled_expense_excluded():
    r = compute_entry(make_entry(), days_worked_in_month=20)
    assert "vehicle_wear" not in r["expenses"]


def test_vehicle_wear_per_mile_when_enabled():
    cfg = {**BASE_CONFIG, "vehicle_wear":
           {"enabled": True, "mode": "per_mile", "amount": 0.18}}
    r = compute_entry(make_entry(snap_expense_config=cfg), days_worked_in_month=20)
    assert round(r["expenses"]["vehicle_wear"], 2) == 6.84


def test_net_and_hourly():
    r = compute_entry(make_entry(), days_worked_in_month=20)
    assert round(r["net"], 2) == 63.94
    assert round(r["hourly"], 2) == 25.57


def test_extra_expense_subtracted():
    r = compute_entry(make_entry(extra_expense=10.0), days_worked_in_month=20)
    assert round(r["net"], 2) == 53.94


def test_hourly_none_when_no_hours():
    r = compute_entry(make_entry(hours=None), days_worked_in_month=20)
    assert r["hourly"] is None


def test_mpg_zero_safe():
    r = compute_entry(make_entry(snap_mpg=0.0), days_worked_in_month=20)
    assert r["expenses"]["fuel"] == 0.0


def test_driver_pay_only_when_assigned():
    cfg = {**BASE_CONFIG, "driver":
           {"enabled": True, "mode": "per_day", "amount": 50.0}}
    no_drv = compute_entry(make_entry(snap_expense_config=cfg),
                           days_worked_in_month=20)
    assert "driver" not in no_drv["expenses"]
    with_drv = compute_entry(make_entry(snap_expense_config=cfg, driver_id=3),
                             days_worked_in_month=20)
    assert with_drv["expenses"]["driver"] == 50.0
