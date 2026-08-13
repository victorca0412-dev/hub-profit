from app import businesses_repo
from app.settings_repo import update_settings, update_expense_config
from app.entries_repo import create_entry, list_entries, get_entry, \
    update_entry, delete_entry, distinct_workdays_in_month


def _set_rate(conn, rate, business_id=1):
    businesses_repo.update_business(conn, business_id,
                                    {"pay_per_package": rate})


def test_create_entry_snapshots_current_settings(conn):
    _set_rate(conn, 1.65)
    update_settings(conn, {"vehicle_mpg": 28.0, "gas_price_per_gal": 3.40})
    update_expense_config(conn, 1, "insurance", enabled=True, amount=140.0)
    eid = create_entry(conn, {"date": "2026-06-24", "packages": 47,
                              "miles": 38.0, "hours": 2.5}, business_id=1)
    e = get_entry(conn, eid, business_id=1)
    assert e["snap_pay_per_package"] == 1.65
    assert e["snap_gas_price"] == 3.40
    assert e["snap_mpg"] == 28.0
    assert e["snap_expense_config"]["insurance"]["amount"] == 140.0


def test_snapshot_is_frozen_after_settings_change(conn):
    _set_rate(conn, 1.65)
    eid = create_entry(conn, {"date": "2026-06-24", "packages": 10,
                              "miles": 0}, business_id=1)
    _set_rate(conn, 2.00)
    e = get_entry(conn, eid, business_id=1)
    assert e["snap_pay_per_package"] == 1.65


def test_list_entries_in_date_range(conn):
    create_entry(conn, {"date": "2026-06-01", "packages": 10, "miles": 0},
                 business_id=1)
    create_entry(conn, {"date": "2026-06-15", "packages": 20, "miles": 0},
                 business_id=1)
    create_entry(conn, {"date": "2026-07-01", "packages": 30, "miles": 0},
                 business_id=1)
    june = list_entries(conn, "2026-06-01", "2026-06-30", business_id=1)
    assert [e["packages"] for e in june] == [20, 10]


def test_delete_entry(conn):
    eid = create_entry(conn, {"date": "2026-06-24", "packages": 5, "miles": 0},
                       business_id=1)
    delete_entry(conn, eid, business_id=1)
    assert get_entry(conn, eid, business_id=1) is None


def test_distinct_workdays_in_month_counts_whole_month(conn):
    for d in ["2026-06-01", "2026-06-01", "2026-06-02", "2026-07-01"]:
        create_entry(conn, {"date": d, "packages": 1, "miles": 0},
                     business_id=1)
    assert distinct_workdays_in_month(conn, "2026-06", business_id=1) == 2
    assert distinct_workdays_in_month(conn, "2026-07", business_id=1) == 1


def test_update_entry_changes_user_fields(conn):
    eid = create_entry(conn, {"date": "2026-07-17", "packages": 47,
                              "miles": 38.0, "hours": 2.5,
                              "note": "typo day"}, business_id=1)
    ok = update_entry(conn, eid, {"date": "2026-07-16", "packages": 50,
                                  "miles": 40.0, "hours": 3.0,
                                  "note": "fixed"}, business_id=1)
    assert ok is True
    e = get_entry(conn, eid, business_id=1)
    assert e["date"] == "2026-07-16"
    assert e["packages"] == 50
    assert e["miles"] == 40.0
    assert e["hours"] == 3.0
    assert e["note"] == "fixed"


def test_update_entry_preserves_snapshots_after_settings_change(conn):
    # The freeze promise: editing a day must not reprice it at today's rates.
    _set_rate(conn, 1.65)
    update_settings(conn, {"gas_price_per_gal": 3.40, "vehicle_mpg": 28.0})
    eid = create_entry(conn, {"date": "2026-07-17", "packages": 47,
                              "miles": 38.0}, business_id=1)
    _set_rate(conn, 2.00)
    update_settings(conn, {"gas_price_per_gal": 4.10, "vehicle_mpg": 22.0})
    update_entry(conn, eid, {"date": "2026-07-16", "packages": 47,
                             "miles": 38.0}, business_id=1)
    e = get_entry(conn, eid, business_id=1)
    assert e["snap_pay_per_package"] == 1.65
    assert e["snap_gas_price"] == 3.40
    assert e["snap_mpg"] == 28.0


def test_update_entry_returns_false_for_unknown_id(conn):
    assert update_entry(conn, 9999, {"date": "2026-07-16", "packages": 1,
                                     "miles": 0.0}, business_id=1) is False


def test_entries_are_isolated_between_businesses(conn):
    other = businesses_repo.create_business(conn, "Newton Hub")
    create_entry(conn, {"date": "2026-08-01", "packages": 40, "miles": 30.0},
                 business_id=1)
    create_entry(conn, {"date": "2026-08-01", "packages": 10, "miles": 5.0},
                 business_id=other)
    mine = list_entries(conn, "1970-01-01", "9999-12-31", business_id=1)
    theirs = list_entries(conn, "1970-01-01", "9999-12-31", business_id=other)
    assert [e["packages"] for e in mine] == [40]
    assert [e["packages"] for e in theirs] == [10]


def test_get_entry_will_not_cross_businesses(conn):
    other = businesses_repo.create_business(conn, "Newton Hub")
    eid = create_entry(conn, {"date": "2026-08-01", "packages": 40,
                              "miles": 30.0}, business_id=1)
    assert get_entry(conn, eid, business_id=other) is None
    assert get_entry(conn, eid, business_id=1) is not None


def test_update_entry_will_not_cross_businesses(conn):
    other = businesses_repo.create_business(conn, "Newton Hub")
    eid = create_entry(conn, {"date": "2026-08-01", "packages": 40,
                              "miles": 30.0}, business_id=1)
    assert update_entry(conn, eid, {"date": "2026-08-02", "packages": 1,
                                    "miles": 0.0}, business_id=other) is False
    assert get_entry(conn, eid, business_id=1)["packages"] == 40


def test_delete_entry_will_not_cross_businesses(conn):
    other = businesses_repo.create_business(conn, "Newton Hub")
    eid = create_entry(conn, {"date": "2026-08-01", "packages": 40,
                              "miles": 30.0}, business_id=1)
    delete_entry(conn, eid, business_id=other)
    assert get_entry(conn, eid, business_id=1) is not None


def test_workday_count_is_per_business(conn):
    other = businesses_repo.create_business(conn, "Newton Hub")
    create_entry(conn, {"date": "2026-08-01", "packages": 40, "miles": 0},
                 business_id=1)
    create_entry(conn, {"date": "2026-08-02", "packages": 40, "miles": 0},
                 business_id=1)
    create_entry(conn, {"date": "2026-08-03", "packages": 40, "miles": 0},
                 business_id=other)
    assert distinct_workdays_in_month(conn, "2026-08", business_id=1) == 2
    assert distinct_workdays_in_month(conn, "2026-08",
                                      business_id=other) == 1


def test_entry_snapshots_its_own_business_rate(conn):
    other = businesses_repo.create_business(conn, "Newton Hub")
    businesses_repo.update_business(conn, other, {"pay_per_package": 2.25})
    eid = create_entry(conn, {"date": "2026-08-01", "packages": 10,
                              "miles": 0}, business_id=other)
    e = get_entry(conn, eid, business_id=other)
    assert e["snap_pay_per_package"] == 2.25
