from app import businesses_repo
from app.drivers_repo import (add_driver, list_drivers, set_driver_active,
                              get_driver, rename_driver)


def test_add_and_list_drivers(conn):
    add_driver(conn, "Carlos", business_id=1)
    add_driver(conn, "Me", business_id=1)
    names = [d["name"] for d in list_drivers(conn, business_id=1)]
    assert names == ["Carlos", "Me"]


def test_deactivate_driver(conn):
    did = add_driver(conn, "Temp", business_id=1)
    set_driver_active(conn, did, False, business_id=1)
    active = [d["name"] for d in list_drivers(conn, business_id=1,
                                              only_active=True)]
    assert "Temp" not in active


def test_reactivate_driver(conn):
    did = add_driver(conn, "Temp", business_id=1)
    set_driver_active(conn, did, False, business_id=1)
    set_driver_active(conn, did, True, business_id=1)
    active = [d["name"] for d in list_drivers(conn, business_id=1,
                                              only_active=True)]
    assert "Temp" in active


def test_rename_driver_changes_the_name(conn):
    driver_id = add_driver(conn, "Alex", business_id=1)
    assert rename_driver(conn, driver_id, "Alexandra", business_id=1) is True
    assert get_driver(conn, driver_id, business_id=1)["name"] == "Alexandra"


def test_rename_driver_reports_unknown_id(conn):
    assert rename_driver(conn, 999, "Nobody", business_id=1) is False


def test_drivers_are_isolated_between_businesses(conn):
    other = businesses_repo.create_business(conn, "Newton Hub")
    add_driver(conn, "Alex", business_id=1)
    add_driver(conn, "Sam", business_id=other)
    assert [d["name"] for d in list_drivers(conn, business_id=1)] == ["Alex"]
    assert [d["name"] for d in list_drivers(conn,
                                            business_id=other)] == ["Sam"]


def test_get_driver_will_not_cross_businesses(conn):
    other = businesses_repo.create_business(conn, "Newton Hub")
    did = add_driver(conn, "Alex", business_id=1)
    assert get_driver(conn, did, business_id=other) is None


def test_rename_driver_will_not_cross_businesses(conn):
    other = businesses_repo.create_business(conn, "Newton Hub")
    did = add_driver(conn, "Alex", business_id=1)
    assert rename_driver(conn, did, "Hijacked", business_id=other) is False
    assert get_driver(conn, did, business_id=1)["name"] == "Alex"
