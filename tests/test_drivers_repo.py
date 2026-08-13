from app.drivers_repo import (add_driver, list_drivers, set_driver_active,
                              get_driver, rename_driver)


def test_add_and_list_drivers(conn):
    add_driver(conn, "Carlos")
    add_driver(conn, "Me")
    names = [d["name"] for d in list_drivers(conn)]
    assert names == ["Carlos", "Me"]


def test_deactivate_driver(conn):
    did = add_driver(conn, "Temp")
    set_driver_active(conn, did, False)
    active = [d["name"] for d in list_drivers(conn, only_active=True)]
    assert "Temp" not in active


def test_reactivate_driver(conn):
    did = add_driver(conn, "Temp")
    set_driver_active(conn, did, False)
    set_driver_active(conn, did, True)
    active = [d["name"] for d in list_drivers(conn, only_active=True)]
    assert "Temp" in active


def test_rename_driver_changes_the_name(conn):
    driver_id = add_driver(conn, "Alex")
    assert rename_driver(conn, driver_id, "Alexandra") is True
    assert get_driver(conn, driver_id)["name"] == "Alexandra"


def test_rename_driver_reports_unknown_id(conn):
    assert rename_driver(conn, 999, "Nobody") is False
