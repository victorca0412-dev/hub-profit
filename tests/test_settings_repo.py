from app.settings_repo import get_settings, update_settings, \
    get_expense_config, update_expense_config


def test_get_default_settings(conn):
    s = get_settings(conn)
    assert s["pay_per_package"] == 1.65
    assert s["track_hours"] == 1


def test_update_settings(conn):
    update_settings(conn, {"pay_per_package": 1.80, "vehicle_mpg": 30.0,
                           "business_name": "JVC Vending Services LLC"})
    s = get_settings(conn)
    assert s["pay_per_package"] == 1.80
    assert s["vehicle_mpg"] == 30.0
    assert s["business_name"] == "JVC Vending Services LLC"


def test_get_expense_config_shape(conn):
    cfg = get_expense_config(conn)
    assert cfg["fuel"]["mode"] == "mileage_fuel"
    assert cfg["fuel"]["enabled"] is True
    assert cfg["vehicle_wear"]["enabled"] is False


def test_update_expense_config(conn):
    update_expense_config(conn, "insurance", enabled=True, amount=140.0)
    cfg = get_expense_config(conn)
    assert cfg["insurance"]["enabled"] is True
    assert cfg["insurance"]["amount"] == 140.0
