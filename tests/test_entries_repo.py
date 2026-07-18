from app.settings_repo import update_settings, update_expense_config
from app.entries_repo import create_entry, list_entries, get_entry, \
    update_entry, delete_entry, distinct_workdays_in_month


def test_create_entry_snapshots_current_settings(conn):
    update_settings(conn, {"pay_per_package": 1.65, "vehicle_mpg": 28.0,
                           "gas_price_per_gal": 3.40})
    update_expense_config(conn, "insurance", enabled=True, amount=140.0)
    eid = create_entry(conn, {"date": "2026-06-24", "packages": 47,
                              "miles": 38.0, "hours": 2.5})
    e = get_entry(conn, eid)
    assert e["snap_pay_per_package"] == 1.65
    assert e["snap_gas_price"] == 3.40
    assert e["snap_mpg"] == 28.0
    assert e["snap_expense_config"]["insurance"]["amount"] == 140.0


def test_snapshot_is_frozen_after_settings_change(conn):
    update_settings(conn, {"pay_per_package": 1.65})
    eid = create_entry(conn, {"date": "2026-06-24", "packages": 10,
                              "miles": 0})
    update_settings(conn, {"pay_per_package": 2.00})
    e = get_entry(conn, eid)
    assert e["snap_pay_per_package"] == 1.65


def test_list_entries_in_date_range(conn):
    create_entry(conn, {"date": "2026-06-01", "packages": 10, "miles": 0})
    create_entry(conn, {"date": "2026-06-15", "packages": 20, "miles": 0})
    create_entry(conn, {"date": "2026-07-01", "packages": 30, "miles": 0})
    june = list_entries(conn, "2026-06-01", "2026-06-30")
    assert [e["packages"] for e in june] == [20, 10]


def test_delete_entry(conn):
    eid = create_entry(conn, {"date": "2026-06-24", "packages": 5, "miles": 0})
    delete_entry(conn, eid)
    assert get_entry(conn, eid) is None


def test_distinct_workdays_in_month_counts_whole_month(conn):
    create_entry(conn, {"date": "2026-06-01", "packages": 1, "miles": 0})
    create_entry(conn, {"date": "2026-06-01", "packages": 1, "miles": 0})
    create_entry(conn, {"date": "2026-06-02", "packages": 1, "miles": 0})
    create_entry(conn, {"date": "2026-07-01", "packages": 1, "miles": 0})
    assert distinct_workdays_in_month(conn, "2026-06") == 2
    assert distinct_workdays_in_month(conn, "2026-07") == 1


def test_update_entry_changes_user_fields(conn):
    eid = create_entry(conn, {"date": "2026-07-17", "packages": 47,
                              "miles": 38.0, "hours": 2.5,
                              "note": "typo day"})
    ok = update_entry(conn, eid, {"date": "2026-07-16", "packages": 50,
                                  "miles": 40.0, "hours": 3.0,
                                  "note": "fixed"})
    assert ok is True
    e = get_entry(conn, eid)
    assert e["date"] == "2026-07-16"
    assert e["packages"] == 50
    assert e["miles"] == 40.0
    assert e["hours"] == 3.0
    assert e["note"] == "fixed"


def test_update_entry_preserves_snapshots_after_settings_change(conn):
    # The freeze promise: editing a day must not reprice it at today's rates.
    update_settings(conn, {"pay_per_package": 1.65, "gas_price_per_gal": 3.40,
                           "vehicle_mpg": 28.0})
    eid = create_entry(conn, {"date": "2026-07-17", "packages": 47,
                              "miles": 38.0})
    update_settings(conn, {"pay_per_package": 2.00, "gas_price_per_gal": 4.10,
                           "vehicle_mpg": 22.0})
    update_entry(conn, eid, {"date": "2026-07-16", "packages": 47,
                             "miles": 38.0})
    e = get_entry(conn, eid)
    assert e["snap_pay_per_package"] == 1.65
    assert e["snap_gas_price"] == 3.40
    assert e["snap_mpg"] == 28.0


def test_update_entry_returns_false_for_unknown_id(conn):
    assert update_entry(conn, 9999, {"date": "2026-07-16", "packages": 1,
                                     "miles": 0.0}) is False
