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
        data = dict(self.ENABLE)
        data.update({"exp_driver_enabled": "1", "exp_driver_amount": "100"})
        client.post("/settings", data=data)
        client.post("/settings/drivers", data={"name": "Alex"})
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
