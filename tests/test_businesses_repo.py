import pytest

from app import businesses_repo as repo
from app.db import init_db, get_conn


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "b.db")
    init_db(path)
    c = get_conn(path)
    yield c
    c.close()


def test_a_fresh_database_has_one_business(conn):
    assert len(repo.list_businesses(conn)) == 1


def test_create_business_returns_its_id(conn):
    new_id = repo.create_business(conn, "Newton Hub")
    assert new_id > 1
    assert repo.get_business(conn, new_id)["name"] == "Newton Hub"


def test_a_new_business_gets_the_default_expense_rows(conn):
    new_id = repo.create_business(conn, "Newton Hub")
    rows = conn.execute(
        "SELECT key, enabled FROM expense_config WHERE business_id=?",
        (new_id,)).fetchall()
    assert len(rows) == 4
    # Fuel on by default, matching what a fresh install gets.
    assert dict((r["key"], r["enabled"]) for r in rows)["fuel"] == 1


def test_a_new_business_starts_flat_at_the_default_rate(conn):
    b = repo.get_business(conn, repo.create_business(conn, "Newton Hub"))
    assert b["rate_model"] == "flat"
    assert b["pay_per_package"] == 1.65
    assert b["drivers_enabled"] == 0


def test_rename_business(conn):
    assert repo.rename_business(conn, 1, "Renamed") is True
    assert repo.get_business(conn, 1)["name"] == "Renamed"


def test_rename_unknown_business_reports_false(conn):
    assert repo.rename_business(conn, 999, "Nope") is False


def test_archived_business_is_hidden_by_default(conn):
    new_id = repo.create_business(conn, "Newton Hub")
    repo.set_archived(conn, new_id, True)
    assert [b["id"] for b in repo.list_businesses(conn)] == [1]
    assert len(repo.list_businesses(conn, include_archived=True)) == 2


def test_archived_business_can_be_restored(conn):
    new_id = repo.create_business(conn, "Newton Hub")
    repo.set_archived(conn, new_id, True)
    repo.set_archived(conn, new_id, False)
    assert len(repo.list_businesses(conn)) == 2


def test_the_last_active_business_cannot_be_archived(conn):
    with pytest.raises(repo.ActiveBusinessError):
        repo.set_archived(conn, 1, True)
    assert len(repo.list_businesses(conn)) == 1


def test_update_business_only_writes_allowed_fields(conn):
    repo.update_business(conn, 1, {"pay_per_package": 2.25,
                                   "drivers_enabled": 1,
                                   "id": 99, "archived": 1})
    b = repo.get_business(conn, 1)
    assert b["pay_per_package"] == 2.25
    assert b["drivers_enabled"] == 1
    assert b["id"] == 1        # id is not writable
    assert b["archived"] == 0  # archiving goes through set_archived
