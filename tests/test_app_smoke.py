import csv
import importlib
import io
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.db import get_conn


def make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("HUBPROFIT_DB", str(tmp_path / "smoke.db"))
    import app.main as main
    importlib.reload(main)
    return TestClient(main.app)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient that also carries the path of the DB it is using.

    The raw-table helpers below need it: the Bug C orphan rows are by
    definition invisible to the repo layer, so asserting they were never
    written means querying the table directly.
    """
    db_path = tmp_path / "smoke.db"
    monkeypatch.setenv("HUBPROFIT_DB", str(db_path))
    import app.main as main
    importlib.reload(main)
    c = TestClient(main.app)
    c.db_path = str(db_path)
    return c


def _scalar(client, sql, params=()):
    conn = get_conn(client.db_path)
    try:
        row = conn.execute(sql, params).fetchone()
        return None if row is None else row[0]
    finally:
        conn.close()


def _raw_entry_count(client):
    return _scalar(client, "SELECT COUNT(*) FROM daily_entries")


def _first_entry_id(client):
    return _scalar(client, "SELECT id FROM daily_entries ORDER BY id LIMIT 1")


def _entry_date(client, entry_id):
    return _scalar(client, "SELECT date FROM daily_entries WHERE id=?",
                   (entry_id,))


def _first_driver_id(client):
    return _scalar(client, "SELECT id FROM drivers ORDER BY id LIMIT 1")


def _stored_rate(client, business_id=1):
    # The rate moved from settings to the business in the v2 migration.
    return _scalar(client,
                   "SELECT pay_per_package FROM businesses WHERE id=?",
                   (business_id,))


def _stored_gas(client):
    return _scalar(client, "SELECT gas_price_per_gal FROM settings WHERE id=1")


def _stored_business_name(client, business_id=1):
    return _scalar(client, "SELECT name FROM businesses WHERE id=?",
                   (business_id,))


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
    e = get_entry(conn, 1, business_id=1)
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
    did = add_driver(conn, "Alex", business_id=1)
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
    e = get_entry(conn, 1, business_id=1)
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
    did = add_driver(conn, "Sam", business_id=1)
    conn.close()
    client.post("/settings", data={
        "business_name": "", "pay_per_package": "1.65",
        "gas_price_per_gal": "3.40", "vehicle_year": "", "vehicle_make": "",
        "vehicle_model": "", "vehicle_mpg": "25", "track_hours": "1",
        "drivers_enabled": "1"})
    client.post("/log", data={"date": "2026-07-12", "packages": "18",
                              "miles": "12", "driver_id": str(did)})
    conn = get_conn(os.environ["HUBPROFIT_DB"])
    set_driver_active(conn, did, False, business_id=1)
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
    e = get_entry(conn, 1, business_id=1)
    conn.close()
    assert e["driver_id"] == did


class TestLogValidation:
    def test_malformed_date_is_rejected(self, client):
        r = client.post("/log", data={
            "date": "not-a-date", "packages": "10", "miles": "5"})
        assert r.status_code == 400
        assert "YYYY-MM-DD" in r.text

    def test_malformed_date_writes_nothing(self, client):
        client.post("/log", data={
            "date": "not-a-date", "packages": "10", "miles": "5"})
        # The orphan bug: such a row is invisible to list_entries, so
        # assert against the raw table instead.
        assert _raw_entry_count(client) == 0

    def test_negative_packages_is_rejected(self, client):
        r = client.post("/log", data={
            "date": "2026-08-01", "packages": "-50", "miles": "5"})
        assert r.status_code == 400
        assert "negative" in r.text.lower()
        assert _raw_entry_count(client) == 0

    def test_negative_miles_is_rejected(self, client):
        r = client.post("/log", data={
            "date": "2026-08-01", "packages": "10", "miles": "-5"})
        assert r.status_code == 400
        assert _raw_entry_count(client) == 0

    def test_garbage_hours_is_rejected(self, client):
        r = client.post("/log", data={
            "date": "2026-08-01", "packages": "10", "miles": "5",
            "hours": "abc"})
        assert r.status_code == 400
        assert _raw_entry_count(client) == 0

    def test_blank_hours_still_accepted(self, client):
        r = client.post("/log", data={
            "date": "2026-08-01", "packages": "10", "miles": "5",
            "hours": ""}, follow_redirects=False)
        assert r.status_code == 303

    def test_rejected_form_redisplays_what_was_typed(self, client):
        r = client.post("/log", data={
            "date": "2026-08-01", "packages": "-50", "miles": "38.5"})
        assert r.status_code == 400
        assert "38.5" in r.text
        assert "2026-08-01" in r.text

    def test_valid_submission_still_works(self, client):
        r = client.post("/log", data={
            "date": "2026-08-01", "packages": "47", "miles": "38.5"},
            follow_redirects=False)
        assert r.status_code == 303
        assert _raw_entry_count(client) == 1

    def test_edit_rejects_bad_date_and_leaves_entry_alone(self, client):
        client.post("/log", data={
            "date": "2026-08-01", "packages": "47", "miles": "38.5"})
        entry_id = _first_entry_id(client)
        r = client.post(f"/log/{entry_id}", data={
            "date": "nope", "packages": "47", "miles": "38.5"})
        assert r.status_code == 400
        assert _entry_date(client, entry_id) == "2026-08-01"


class TestSettingsValidation:
    def _save(self, client, follow_redirects=True, **overrides):
        data = {"business_name": "Test Co", "pay_per_package": "2.50",
                "gas_price_per_gal": "3.40", "vehicle_mpg": "28"}
        data.update(overrides)
        return client.post("/settings", data=data,
                           follow_redirects=follow_redirects)

    def test_blank_rate_does_not_silently_reset_to_default(self, client):
        self._save(client, pay_per_package="2.50")
        r = self._save(client, pay_per_package="")
        assert r.status_code == 400
        assert _stored_rate(client) == 2.50

    def test_garbage_rate_is_rejected(self, client):
        self._save(client, pay_per_package="2.50")
        r = self._save(client, pay_per_package="abc")
        assert r.status_code == 400
        assert _stored_rate(client) == 2.50

    def test_negative_rate_is_rejected(self, client):
        self._save(client, pay_per_package="2.50")
        r = self._save(client, pay_per_package="-1")
        assert r.status_code == 400
        assert _stored_rate(client) == 2.50

    def test_blank_gas_price_does_not_reset(self, client):
        self._save(client, gas_price_per_gal="4.10")
        r = self._save(client, gas_price_per_gal="")
        assert r.status_code == 400
        assert _stored_gas(client) == 4.10

    def test_a_rejected_save_writes_no_field_at_all(self, client):
        self._save(client, business_name="Original", pay_per_package="2.50")
        self._save(client, business_name="Changed", pay_per_package="")
        assert _stored_business_name(client) == "Original"

    def test_valid_save_still_works(self, client):
        r = self._save(client, pay_per_package="1.90", follow_redirects=False)
        assert r.status_code in (200, 303)
        assert _stored_rate(client) == 1.90


class TestDriverManagement:
    ENABLE = {"business_name": "T", "pay_per_package": "1.65",
              "gas_price_per_gal": "3.40", "vehicle_mpg": "25",
              "drivers_enabled": "1"}

    def test_a_driver_can_be_added_and_appears_on_log_day(self, client):
        client.post("/settings", data=self.ENABLE)
        client.post("/settings/drivers", data={"name": "Alex"})
        assert "Alex" in client.get("/settings").text
        assert "Alex" in client.get("/log").text

    def test_a_blank_driver_name_is_rejected(self, client):
        r = client.post("/settings/drivers", data={"name": "  "})
        assert r.status_code == 400

    def test_a_driver_can_be_renamed(self, client):
        client.post("/settings", data=self.ENABLE)
        client.post("/settings/drivers", data={"name": "Alex"})
        driver_id = _first_driver_id(client)
        client.post(f"/settings/drivers/{driver_id}/rename",
                    data={"name": "Alexandra"})
        assert "Alexandra" in client.get("/settings").text

    def test_a_deactivated_driver_leaves_the_log_day_dropdown(self, client):
        client.post("/settings", data=self.ENABLE)
        client.post("/settings/drivers", data={"name": "Alex"})
        driver_id = _first_driver_id(client)
        client.post(f"/settings/drivers/{driver_id}/active",
                    data={"active": "0"})
        page = client.get("/log").text
        select = page.split('id="inp-driver"')[1].split("</select>")[0]
        assert "Alex" not in select

    def test_driver_pay_applies_only_on_a_driver_day(self, client):
        # Driver pay moved onto the driver in 2.1.0. A per-day rate of
        # $100 reproduces the behaviour the old shared expense had.
        client.post("/settings", data=self.ENABLE)
        client.post("/settings/drivers", data={
            "name": "Alex", "pay_model": "per_day", "pay_rate": "100"})
        driver_id = _first_driver_id(client)
        client.post("/log", data={
            "date": "2026-08-03", "packages": "100", "miles": "0",
            "driver_id": str(driver_id)})
        client.post("/log", data={
            "date": "2026-08-04", "packages": "100", "miles": "0"})
        # Assert against the CSV, not the HTML: both rows earn $165.00, so
        # a substring check on the page cannot tell the net column from the
        # earnings column.
        rows = list(csv.DictReader(
            io.StringIO(client.get("/history.csv?period=all").text)))
        by_date = {r["date"]: r for r in rows}
        # 100 pkgs x $1.65 = $165.00 earned on both days.
        assert float(by_date["2026-08-03"]["net"]) == 65.0   # less $100 driver pay
        assert float(by_date["2026-08-04"]["net"]) == 165.0  # drove it myself

    def test_per_package_driver_pay_scales_with_the_count(self, client):
        client.post("/settings", data=self.ENABLE)
        client.post("/settings/drivers", data={
            "name": "Alex", "pay_model": "per_package", "pay_rate": "1.00"})
        driver_id = _first_driver_id(client)
        client.post("/log", data={
            "date": "2026-08-03", "packages": "100", "miles": "0",
            "driver_id": str(driver_id)})
        rows = list(csv.DictReader(
            io.StringIO(client.get("/history.csv?period=all").text)))
        # 100 x $1.65 earned, 100 x $1.00 paid out.
        assert float(rows[0]["net"]) == 65.0


def _create_business(client, name):
    client.post("/businesses", data={"name": name})
    return _scalar(client, "SELECT MAX(id) FROM businesses")


class TestBusinessSwitching:
    def test_switcher_is_hidden_with_only_one_business(self, client):
        assert "business-switcher" not in client.get("/").text

    def test_switcher_appears_with_two_businesses(self, client):
        _create_business(client, "Newton Hub")
        page = client.get("/").text
        assert "business-switcher" in page
        assert "Newton Hub" in page

    def test_switching_changes_what_the_dashboard_shows(self, client):
        other = _create_business(client, "Newton Hub")
        client.post("/log", data={"date": "2026-08-01", "packages": "100",
                                  "miles": "0"})
        assert "100 packages" in client.get("/?period=all").text
        client.post("/business/switch", data={"business_id": str(other)})
        assert "0 packages" in client.get("/?period=all").text

    def test_entries_do_not_leak_across_businesses_in_history(self, client):
        other = _create_business(client, "Newton Hub")
        client.post("/log", data={"date": "2026-08-01", "packages": "77",
                                  "miles": "0"})
        client.post("/business/switch", data={"business_id": str(other)})
        assert "77" not in client.get("/history?period=all").text

    def test_csv_export_is_scoped_to_the_active_business(self, client):
        other = _create_business(client, "Newton Hub")
        client.post("/log", data={"date": "2026-08-01", "packages": "77",
                                  "miles": "0"})
        client.post("/business/switch", data={"business_id": str(other)})
        assert "77" not in client.get("/history.csv?period=all").text

    def test_editing_an_entry_from_another_business_404s(self, client):
        other = _create_business(client, "Newton Hub")
        client.post("/log", data={"date": "2026-08-01", "packages": "40",
                                  "miles": "0"})
        entry_id = _first_entry_id(client)
        client.post("/business/switch", data={"business_id": str(other)})
        assert client.get(f"/log?edit={entry_id}").status_code == 404

    def test_deleting_an_entry_from_another_business_does_nothing(self, client):
        other = _create_business(client, "Newton Hub")
        client.post("/log", data={"date": "2026-08-01", "packages": "40",
                                  "miles": "0"})
        entry_id = _first_entry_id(client)
        client.post("/business/switch", data={"business_id": str(other)})
        client.post(f"/history/delete/{entry_id}")
        assert _raw_entry_count(client) == 1

    def test_switching_to_an_unknown_business_is_rejected(self, client):
        assert client.post("/business/switch",
                           data={"business_id": "999"}).status_code == 404

    def test_the_pay_rate_is_per_business(self, client):
        other = _create_business(client, "Newton Hub")
        client.post("/business/switch", data={"business_id": str(other)})
        client.post("/settings", data={
            "pay_per_package": "2.25", "gas_price_per_gal": "3.40",
            "vehicle_mpg": "25"})
        client.post("/business/switch", data={"business_id": "1"})
        assert _scalar(
            client,
            "SELECT pay_per_package FROM businesses WHERE id=1") == 1.65
        assert _scalar(
            client, "SELECT pay_per_package FROM businesses WHERE id=?",
            (other,)) == 2.25

    def test_gas_price_stays_global_across_businesses(self, client):
        other = _create_business(client, "Newton Hub")
        client.post("/settings", data={
            "pay_per_package": "1.65", "gas_price_per_gal": "4.10",
            "vehicle_mpg": "25"})
        client.post("/business/switch", data={"business_id": str(other)})
        assert 'value="4.1"' in client.get("/settings").text

    def test_an_archived_active_business_falls_back_instead_of_crashing(
            self, client):
        other = _create_business(client, "Newton Hub")
        client.post("/business/switch", data={"business_id": str(other)})
        # Archive the business that is currently active, out of band.
        conn = get_conn(client.db_path)
        conn.execute("UPDATE businesses SET archived=1 WHERE id=?", (other,))
        conn.commit()
        conn.close()
        assert client.get("/").status_code == 200
        assert _scalar(
            client, "SELECT active_business_id FROM settings WHERE id=1") == 1


class TestManageBusinesses:
    def test_page_lists_the_current_business(self, client):
        assert "My Hub Business" in client.get("/businesses").text

    def test_creating_a_business_does_not_switch_to_it(self, client):
        client.post("/businesses", data={"name": "Newton Hub"})
        assert _scalar(
            client, "SELECT active_business_id FROM settings WHERE id=1") == 1

    def test_a_blank_business_name_is_rejected(self, client):
        r = client.post("/businesses", data={"name": "   "})
        assert r.status_code == 400
        assert _scalar(client, "SELECT COUNT(*) FROM businesses") == 1

    def test_a_business_can_be_renamed(self, client):
        client.post("/businesses/1/rename", data={"name": "Renamed Co"})
        assert "Renamed Co" in client.get("/businesses").text

    def test_archiving_hides_it_from_the_switcher(self, client):
        other = _create_business(client, "Newton Hub")
        client.post(f"/businesses/{other}/archive", data={"archived": "1"})
        assert "Newton Hub" not in client.get("/").text

    def test_the_last_business_cannot_be_archived(self, client):
        r = client.post("/businesses/1/archive", data={"archived": "1"})
        assert r.status_code == 400
        assert "only business" in r.text
        assert _scalar(
            client, "SELECT archived FROM businesses WHERE id=1") == 0

    def test_a_new_business_starts_with_no_entries_and_default_expenses(
            self, client):
        other = _create_business(client, "Newton Hub")
        client.post("/business/switch", data={"business_id": str(other)})
        assert "No entries" in client.get("/history?period=all").text
        # Fuel on by default: a day with miles must show a fuel cost, not
        # zero expenses.
        client.post("/log", data={"date": "2026-08-01", "packages": "10",
                                  "miles": "50"})
        rows = list(csv.DictReader(
            io.StringIO(client.get("/history.csv?period=all").text)))
        assert float(rows[0]["expenses"]) > 0


def _all(client, sql, params=()):
    conn = get_conn(client.db_path)
    try:
        return [tuple(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


class TestTierEditor:
    BASE = {"gas_price_per_gal": "3.40", "vehicle_mpg": "25",
            "pay_per_package": "1.65"}
    # A flat rate no tier uses, so a tiered result can never be confused
    # with the fallback. With 1.65 here, 45 packages would earn $74.25
    # either way and the assertion would prove nothing.
    DISTINCT_FLAT = "9.99"

    def _save_tiered(self, client, tos, rates):
        # httpx does NOT accept a list of (key, value) tuples for data= -
        # it treats the list as raw body content and the server receives
        # no form fields at all. Repeated keys go in as list values.
        data = dict(self.BASE)
        data["pay_per_package"] = self.DISTINCT_FLAT
        data["rate_model"] = "tiered"
        data["tier_to"] = list(tos)
        data["tier_rate"] = list(rates)
        return client.post("/settings", data=data)

    def test_saving_tiers_stores_chained_bounds(self, client):
        self._save_tiered(client, ["20", "40", ""], ["2.25", "1.95", "1.65"])
        rows = _all(client, "SELECT min_packages, max_packages, rate "
                            "FROM rate_tiers ORDER BY min_packages")
        assert rows == [(1, 20, 2.25), (21, 40, 1.95), (41, None, 1.65)]

    def test_a_tiered_day_uses_the_tier_rate(self, client):
        self._save_tiered(client, ["20", "40", ""], ["2.25", "1.95", "1.65"])
        client.post("/log", data={"date": "2026-08-01", "packages": "45",
                                  "miles": "0"})
        rows = list(csv.DictReader(
            io.StringIO(client.get("/history.csv?period=all").text)))
        # 45 x $1.65 (the 41+ tier). The flat fallback would be 45 x $9.99.
        assert float(rows[0]["earnings"]) == 74.25

    def test_a_small_block_pays_the_higher_rate(self, client):
        self._save_tiered(client, ["20", "40", ""], ["2.25", "1.95", "1.65"])
        client.post("/log", data={"date": "2026-08-01", "packages": "18",
                                  "miles": "0"})
        rows = list(csv.DictReader(
            io.StringIO(client.get("/history.csv?period=all").text)))
        assert float(rows[0]["earnings"]) == 40.50

    def test_switching_back_to_flat_keeps_the_tier_rows(self, client):
        self._save_tiered(client, ["20", ""], ["2.25", "1.65"])
        client.post("/settings", data={**self.BASE, "rate_model": "flat"})
        assert _scalar(client, "SELECT COUNT(*) FROM rate_tiers") == 2
        # ...but a day logged now prices flat.
        client.post("/log", data={"date": "2026-08-01", "packages": "10",
                                  "miles": "0"})
        rows = list(csv.DictReader(
            io.StringIO(client.get("/history.csv?period=all").text)))
        assert float(rows[0]["earnings"]) == 16.50

    def test_a_descending_ceiling_is_rejected(self, client):
        r = self._save_tiered(client, ["40", "20", ""],
                              ["2.25", "1.95", "1.65"])
        assert r.status_code == 400
        assert _scalar(client, "SELECT COUNT(*) FROM rate_tiers") == 0

    def test_a_negative_rate_is_rejected(self, client):
        r = self._save_tiered(client, ["20", ""], ["-1", "1.65"])
        assert r.status_code == 400
        assert _scalar(client, "SELECT COUNT(*) FROM rate_tiers") == 0

    def test_an_open_ended_tier_in_the_middle_is_rejected(self, client):
        r = self._save_tiered(client, ["", "40", ""],
                              ["2.25", "1.95", "1.65"])
        assert r.status_code == 400
        assert _scalar(client, "SELECT COUNT(*) FROM rate_tiers") == 0

    def test_tiered_with_no_tiers_at_all_is_rejected(self, client):
        r = client.post("/settings", data={**self.BASE,
                                           "rate_model": "tiered"})
        assert r.status_code == 400

    def test_tiers_are_isolated_between_businesses(self, client):
        self._save_tiered(client, ["20", ""], ["2.25", "1.65"])
        other = _create_business(client, "Newton Hub")
        client.post("/business/switch", data={"business_id": str(other)})
        assert _scalar(
            client, "SELECT COUNT(*) FROM rate_tiers WHERE business_id=?",
            (other,)) == 0

    def test_history_and_csv_show_the_effective_rate(self, client):
        self._save_tiered(client, ["20", "40", ""], ["2.25", "1.95", "1.65"])
        client.post("/log", data={"date": "2026-08-01", "packages": "45",
                                  "miles": "0"})
        client.post("/log", data={"date": "2026-08-02", "packages": "18",
                                  "miles": "0"})
        page = client.get("/history?period=all").text
        assert "$1.65" in page
        assert "$2.25" in page
        rows = {r["date"]: r for r in csv.DictReader(
            io.StringIO(client.get("/history.csv?period=all").text))}
        assert float(rows["2026-08-01"]["rate"]) == 1.65
        assert float(rows["2026-08-02"]["rate"]) == 2.25

    def test_the_log_form_carries_the_tiers_for_the_live_estimate(self, client):
        self._save_tiered(client, ["20", ""], ["2.25", "1.65"])
        page = client.get("/log").text
        assert 'data-rate-model="tiered"' in page
        assert "min_packages" in page


def test_flat_businesses_still_show_a_rate_in_history(client):
    client.post("/log", data={"date": "2026-08-01", "packages": "10",
                              "miles": "0"})
    assert "$1.65" in client.get("/history?period=all").text


class TestVersionDisplay:
    def test_footer_shows_app_and_database_version(self, client):
        from app import __version__
        from app.db import SCHEMA_VERSION
        page = client.get("/").text
        assert f"v{__version__}" in page
        assert f"database v{SCHEMA_VERSION}" in page

    def test_help_page_shows_the_version(self, client):
        from app import __version__
        assert f"v{__version__}" in client.get("/help").text

    def test_the_version_is_on_every_page(self, client):
        from app import __version__
        for path in ("/", "/log", "/history", "/settings", "/businesses",
                     "/help"):
            assert f"v{__version__}" in client.get(path).text, path

    def test_openapi_reports_the_version(self, client):
        from app import __version__
        assert client.get("/openapi.json").json()["info"]["version"] == \
            __version__


class TestStaticAssetVersioning:
    """Assets carry ?v=<version> so an upgrade cannot leave a browser
    running the previous release's cached JavaScript. This bit during
    development: the tier editor's handlers were live on disk while the
    page kept executing a stale app.js."""

    def test_scripts_are_version_stamped(self, client):
        from app import __version__
        for path in ("/", "/log", "/history", "/settings"):
            page = client.get(path).text
            assert f"/static/app.js?v={__version__}" in page, path

    def test_stylesheet_is_version_stamped(self, client):
        from app import __version__
        assert f"/static/app.css?v={__version__}" in client.get("/").text

    def test_chart_library_is_version_stamped(self, client):
        from app import __version__
        assert f"/static/chart.min.js?v={__version__}" in client.get("/").text

    def test_no_unstamped_asset_references_remain(self, client):
        for path in ("/", "/log", "/history", "/settings", "/businesses",
                     "/help"):
            page = client.get(path).text
            assert '"/static/app.js"' not in page, path
            assert '"/static/app.css"' not in page, path


class TestLogDayWithDriver:
    ENABLE = {"business_name": "T", "pay_per_package": "1.65",
              "gas_price_per_gal": "3.40", "vehicle_mpg": "25",
              "drivers_enabled": "1"}

    def _driver(self, client, rate="1.50", model="per_package"):
        client.post("/settings", data=self.ENABLE)
        client.post("/settings/drivers", data={
            "name": "Alex", "pay_model": model, "pay_rate": rate})
        return _first_driver_id(client)

    def test_driver_rates_reach_the_estimate(self, client):
        self._driver(client)
        page = client.get("/log").text
        assert "data-driver-rates" in page
        assert "Alex" in page
        assert "1.5" in page

    def test_a_driver_day_stores_zero_miles(self, client):
        did = self._driver(client)
        client.post("/log", data={"date": "2026-08-01", "packages": "45",
                                  "miles": "38", "driver_id": str(did)})
        assert _scalar(client, "SELECT miles FROM daily_entries") == 0

    def test_an_owner_day_still_stores_miles(self, client):
        self._driver(client)
        client.post("/log", data={"date": "2026-08-01", "packages": "45",
                                  "miles": "38"})
        assert _scalar(client, "SELECT miles FROM daily_entries") == 38

    def test_a_driver_rate_above_the_owners_shows_a_loss(self, client):
        did = self._driver(client, rate="1.90")
        client.post("/log", data={"date": "2026-08-01", "packages": "45",
                                  "miles": "0", "driver_id": str(did)})
        rows = list(csv.DictReader(
            io.StringIO(client.get("/history.csv?period=all").text)))
        # 45 x $1.65 earned = $74.25, 45 x $1.90 paid = $85.50.
        assert float(rows[0]["net"]) < 0


class TestUpdateCheck:
    def test_help_page_offers_the_button(self, client):
        page = client.get("/help").text
        assert "update-check-btn" in page
        assert "Check for updates" in page

    def test_help_page_states_the_privacy_position(self, client):
        page = client.get("/help").text
        assert "only contacts the internet when you ask" in page

    def test_the_endpoint_reports_up_to_date(self, client, monkeypatch):
        from app import updates
        from app import __version__
        monkeypatch.setattr(updates, "_fetch_latest_tag",
                            lambda: "v" + __version__)
        d = client.get("/api/update-check").json()
        assert d["status"] == "up-to-date"
        assert d["current"] == __version__

    def test_the_endpoint_reports_an_update(self, client, monkeypatch):
        from app import updates
        monkeypatch.setattr(updates, "_fetch_latest_tag", lambda: "v99.0.0")
        d = client.get("/api/update-check").json()
        assert d["status"] == "update-available"
        assert d["latest"] == "99.0.0"

    def test_the_endpoint_survives_no_internet(self, client, monkeypatch):
        from app import updates

        def boom():
            raise OSError("no route to host")
        monkeypatch.setattr(updates, "_fetch_latest_tag", boom)
        r = client.get("/api/update-check")
        # Still a 200 with a usable message - an offline machine must not
        # see an error page.
        assert r.status_code == 200
        assert r.json()["status"] == "unavailable"

    def test_no_other_page_calls_the_update_endpoint(self, client):
        # The check must never fire on its own.
        for path in ("/", "/log", "/history", "/settings", "/businesses"):
            assert "api/update-check" not in client.get(path).text
