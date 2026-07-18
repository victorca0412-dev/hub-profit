import importlib
from datetime import date
from fastapi.testclient import TestClient


def make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("HUBPROFIT_DB", str(tmp_path / "smoke.db"))
    import app.main as main
    importlib.reload(main)
    return TestClient(main.app)


def test_pages_load(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    for path in ["/", "/log", "/history", "/settings", "/help"]:
        assert client.get(path).status_code == 200


def test_log_day_roundtrip(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    resp = client.post("/log", data={
        "date": "2026-06-24", "packages": "47", "miles": "38",
        "hours": "2.5"}, follow_redirects=False)
    assert resp.status_code in (302, 303)
    history = client.get("/history")
    assert "47" in history.text


def test_settings_update(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    resp = client.post("/settings", data={
        "business_name": "JVC Vending Services LLC",
        "pay_per_package": "1.65", "gas_price_per_gal": "3.40",
        "vehicle_year": "2019", "vehicle_make": "Toyota",
        "vehicle_model": "RAV4", "vehicle_mpg": "28",
        "track_hours": "1"}, follow_redirects=False)
    assert resp.status_code in (302, 303)
    assert "JVC Vending Services LLC" in client.get("/settings").text


def test_history_csv_download(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    client.post("/log", data={"date": "2026-06-24", "packages": "10",
                              "miles": "20", "hours": "1.5"})
    resp = client.get("/history.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert "date,packages" in resp.text
    assert "2026-06-24" in resp.text


def test_dashboard_renders_hero(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    # Use today's date so the entry falls inside the "month" view regardless
    # of when the test runs (the chart canvas only renders when the period
    # has data).
    today = date.today().isoformat()
    client.post("/log", data={"date": today, "packages": "47",
                              "miles": "38", "hours": "2.5"})
    html = client.get("/?period=month").text
    assert "Net" in html  # hero present
    assert "netchart" in html  # chart canvas present


def test_log_form_without_edit_is_blank_and_defaults_to_today(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    html = client.get("/log").text
    assert "Log a Day" in html
    assert 'action="/log"' in html
    assert 'value="{}"'.format(date.today().isoformat()) in html


def test_edit_form_prefills_the_entry(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    client.post("/log", data={"date": "2026-07-17", "packages": "47",
                              "miles": "38", "hours": "2.5"})
    html = client.get("/log?edit=1").text
    assert "Edit Day" in html
    assert 'value="2026-07-17"' in html
    assert 'value="47"' in html
    assert 'action="/log/1"' in html


def test_edit_form_unknown_id_returns_404(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    assert client.get("/log?edit=9999").status_code == 404


def test_edit_saves_and_moves_entry_between_dates(tmp_path, monkeypatch):
    # The reported bug's actual fix path: 2026-07-17 -> 2026-07-16.
    client = make_client(tmp_path, monkeypatch)
    client.post("/log", data={"date": "2026-07-17", "packages": "47",
                              "miles": "38", "hours": "2.5"})
    resp = client.post("/log/1", data={"date": "2026-07-16", "packages": "50",
                                       "miles": "40", "hours": "3"},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/history"
    html = client.get("/history?period=all").text
    assert "2026-07-16" in html
    assert "2026-07-17" not in html
    assert "50" in html


def test_edit_post_unknown_id_returns_404(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    resp = client.post("/log/9999", data={"date": "2026-07-16",
                                          "packages": "1", "miles": "0"})
    assert resp.status_code == 404


def test_history_row_has_edit_link(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    client.post("/log", data={"date": "2026-07-17", "packages": "47",
                              "miles": "38"})
    html = client.get("/history?period=all").text
    assert 'href="/log?edit=1"' in html
    assert "Delete" in html  # delete still available


def test_edit_preserves_hours_when_tracking_disabled(tmp_path, monkeypatch):
    # A day logged with hours must keep them when edited after track_hours is off.
    client = make_client(tmp_path, monkeypatch)
    client.post("/log", data={"date": "2026-07-10", "packages": "40",
                              "miles": "30", "hours": "4.5"})
    # Turn hours tracking OFF (omit track_hours from the settings POST).
    client.post("/settings", data={
        "business_name": "", "pay_per_package": "1.65",
        "gas_price_per_gal": "3.40", "vehicle_year": "", "vehicle_make": "",
        "vehicle_model": "", "vehicle_mpg": "25"})
    # The edit form no longer shows a visible hours input; it must still
    # carry the existing value forward via a hidden field.
    html = client.get("/log?edit=1").text
    assert 'name="hours" value="4.5"' in html
    # A real browser submits every named field on the form, hidden ones
    # included, alongside the edited packages value.
    client.post("/log/1", data={"date": "2026-07-10", "packages": "42",
                                "miles": "30", "hours": "4.5"})
    # Hours must survive.
    from app.entries_repo import get_entry
    from app.db import get_conn
    import os
    conn = get_conn(os.environ["HUBPROFIT_DB"])
    e = get_entry(conn, 1)
    conn.close()
    assert e["hours"] == 4.5
    assert e["packages"] == 42


def test_edit_preserves_driver_when_drivers_disabled(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    import os
    from app.db import get_conn
    from app.drivers_repo import add_driver
    from app.entries_repo import get_entry
    conn = get_conn(os.environ["HUBPROFIT_DB"])
    did = add_driver(conn, "Alex")
    conn.close()
    # Log a day assigned to that driver, with drivers enabled.
    client.post("/settings", data={
        "business_name": "", "pay_per_package": "1.65",
        "gas_price_per_gal": "3.40", "vehicle_year": "", "vehicle_make": "",
        "vehicle_model": "", "vehicle_mpg": "25", "track_hours": "1",
        "drivers_enabled": "1"})
    client.post("/log", data={"date": "2026-07-11", "packages": "20",
                              "miles": "15", "driver_id": str(did)})
    # Turn drivers OFF, then edit the day.
    client.post("/settings", data={
        "business_name": "", "pay_per_package": "1.65",
        "gas_price_per_gal": "3.40", "vehicle_year": "", "vehicle_make": "",
        "vehicle_model": "", "vehicle_mpg": "25", "track_hours": "1"})
    # The edit form no longer shows the driver select; it must still carry
    # the existing assignment forward via a hidden field.
    html = client.get("/log?edit=1").text
    assert 'name="driver_id" value="{}"'.format(did) in html
    # A real browser submits every named field on the form, hidden ones
    # included, alongside the edited packages value.
    client.post("/log/1", data={"date": "2026-07-11", "packages": "25",
                                "miles": "15", "driver_id": str(did)})
    conn = get_conn(os.environ["HUBPROFIT_DB"])
    e = get_entry(conn, 1)
    conn.close()
    assert e["driver_id"] == did
    assert e["packages"] == 25


def test_edit_preserves_deactivated_assigned_driver(tmp_path, monkeypatch):
    # drivers_enabled stays ON, but the assigned driver is deactivated.
    client = make_client(tmp_path, monkeypatch)
    import os
    from app.db import get_conn
    from app.drivers_repo import add_driver, set_driver_active
    from app.entries_repo import get_entry
    conn = get_conn(os.environ["HUBPROFIT_DB"])
    did = add_driver(conn, "Sam")
    conn.close()
    client.post("/settings", data={
        "business_name": "", "pay_per_package": "1.65",
        "gas_price_per_gal": "3.40", "vehicle_year": "", "vehicle_make": "",
        "vehicle_model": "", "vehicle_mpg": "25", "track_hours": "1",
        "drivers_enabled": "1"})
    client.post("/log", data={"date": "2026-07-12", "packages": "18",
                              "miles": "12", "driver_id": str(did)})
    conn = get_conn(os.environ["HUBPROFIT_DB"])
    set_driver_active(conn, did, False)
    conn.close()
    # The edit form must render the deactivated driver as a selected option,
    # so re-saving keeps them assigned.
    html = client.get("/log?edit=1").text
    assert 'value="{}"'.format(did) in html
    assert 'name="driver_id"' in html and "Sam" in html
    resp = client.post("/log/1", data={"date": "2026-07-12", "packages": "19",
                                       "miles": "12", "driver_id": str(did)},
                       follow_redirects=False)
    assert resp.status_code == 303
    conn = get_conn(os.environ["HUBPROFIT_DB"])
    e = get_entry(conn, 1)
    conn.close()
    assert e["driver_id"] == did
