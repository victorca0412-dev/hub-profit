from app.drivers_repo import add_driver, list_drivers, set_driver_active


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
