from app import businesses_repo
from app.settings_repo import get_settings, update_settings, \
    get_expense_config, update_expense_config, get_active_business_id, \
    set_active_business


def test_get_default_settings(conn):
    s = get_settings(conn)
    assert s["vehicle_mpg"] == 25.0
    assert s["track_hours"] == 1


def test_update_settings(conn):
    update_settings(conn, {"gas_price_per_gal": 4.10, "vehicle_mpg": 30.0})
    s = get_settings(conn)
    assert s["gas_price_per_gal"] == 4.10
    assert s["vehicle_mpg"] == 30.0


def test_get_expense_config_shape(conn):
    cfg = get_expense_config(conn, 1)
    assert cfg["fuel"]["mode"] == "mileage_fuel"
    assert cfg["fuel"]["enabled"] is True
    assert cfg["vehicle_wear"]["enabled"] is False


def test_update_expense_config(conn):
    update_expense_config(conn, 1, "insurance", enabled=True, amount=140.0)
    cfg = get_expense_config(conn, 1)
    assert cfg["insurance"]["enabled"] is True
    assert cfg["insurance"]["amount"] == 140.0


def test_update_settings_ignores_unknown_keys(conn):
    update_settings(conn, {"vehicle_mpg": 30.0, "malicious_col": "evil"})
    s = get_settings(conn)
    assert s["vehicle_mpg"] == 30.0
    assert "malicious_col" not in s


def test_pay_rate_is_no_longer_a_global_setting(conn):
    # It moved to the business. Writing it here must be ignored rather
    # than silently creating a second source of truth for the rate.
    update_settings(conn, {"pay_per_package": 9.99})
    assert "pay_per_package" not in get_settings(conn)


def test_expense_config_is_per_business(conn):
    other = businesses_repo.create_business(conn, "Newton Hub")
    update_expense_config(conn, 1, "insurance", enabled=True, amount=150.0)
    mine = get_expense_config(conn, 1)
    theirs = get_expense_config(conn, other)
    assert mine["insurance"]["amount"] == 150.0
    assert theirs["insurance"]["amount"] == 0.0


def test_active_business_round_trips(conn):
    other = businesses_repo.create_business(conn, "Newton Hub")
    set_active_business(conn, other)
    assert get_active_business_id(conn) == other
