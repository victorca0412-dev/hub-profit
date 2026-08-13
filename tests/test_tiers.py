import pytest

from app import businesses_repo as repo
from app.db import init_db, get_conn


@pytest.fixture
def conn(tmp_path):
    path = str(tmp_path / "t.db")
    init_db(path)
    c = get_conn(path)
    yield c
    c.close()


TIERS = [(20, 2.25), (40, 1.95), (None, 1.65)]

TIER_ROWS = [{"min_packages": 1, "max_packages": 20, "rate": 2.25},
             {"min_packages": 21, "max_packages": 40, "rate": 1.95},
             {"min_packages": 41, "max_packages": None, "rate": 1.65}]


def _entry(packages, tiers=None, flat=1.65):
    return {"packages": packages, "miles": 0.0, "hours": None,
            "extra_expense": None, "driver_id": None,
            "snap_pay_per_package": flat, "snap_gas_price": 3.40,
            "snap_mpg": 28.0, "snap_expense_config": {},
            "snap_rate_model": "tiered" if tiers is not None else "flat",
            "snap_rate_tiers": tiers}


class TestTierStorage:
    def test_a_new_business_has_no_tiers(self, conn):
        assert repo.get_tiers(conn, 1) == []

    def test_replace_tiers_chains_the_lower_bounds(self, conn):
        repo.replace_tiers(conn, 1, TIERS)
        rows = repo.get_tiers(conn, 1)
        assert [(r["min_packages"], r["max_packages"], r["rate"])
                for r in rows] == [(1, 20, 2.25), (21, 40, 1.95),
                                   (41, None, 1.65)]

    def test_replace_tiers_is_a_full_replacement(self, conn):
        repo.replace_tiers(conn, 1, TIERS)
        repo.replace_tiers(conn, 1, [(None, 1.80)])
        rows = repo.get_tiers(conn, 1)
        assert len(rows) == 1
        assert rows[0]["min_packages"] == 1
        assert rows[0]["max_packages"] is None

    def test_tiers_are_per_business(self, conn):
        other = repo.create_business(conn, "Newton Hub")
        repo.replace_tiers(conn, 1, TIERS)
        assert repo.get_tiers(conn, other) == []


class TestMigrationV3:
    def test_version_is_three(self, conn):
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3

    def test_existing_entries_default_to_flat(self, conn):
        from app import entries_repo
        eid = entries_repo.create_entry(
            conn, {"date": "2026-08-01", "packages": 10, "miles": 0},
            business_id=1)
        e = entries_repo.get_entry(conn, eid, business_id=1)
        assert e["snap_rate_model"] == "flat"
        assert e["snap_rate_tiers"] is None


class TestTierLookup:
    @pytest.mark.parametrize("packages,expected_rate", [
        (1, 2.25), (19, 2.25), (20, 2.25),      # first tier, incl. ceiling
        (21, 1.95), (39, 1.95), (40, 1.95),     # middle tier, both edges
        (41, 1.65), (45, 1.65), (5000, 1.65),   # open-ended final tier
    ])
    def test_boundaries_select_the_right_tier(self, packages, expected_rate):
        from app.calculations import rate_for_entry
        assert rate_for_entry(_entry(packages, TIER_ROWS)) == expected_rate

    def test_whole_block_pays_one_rate_not_brackets(self):
        from app.calculations import earnings_for
        # The decision this whole feature rests on. Graduated brackets
        # would give 20*2.25 + 20*1.95 + 5*1.65 = 92.25.
        assert earnings_for(_entry(45, TIER_ROWS)) == pytest.approx(74.25)

    def test_a_small_block_pays_more_per_package(self):
        from app.calculations import earnings_for
        assert earnings_for(_entry(18, TIER_ROWS)) == pytest.approx(40.50)

    def test_zero_packages_earns_zero(self):
        from app.calculations import earnings_for
        assert earnings_for(_entry(0, TIER_ROWS)) == 0

    def test_flat_entries_ignore_any_tiers(self):
        from app.calculations import earnings_for
        e = _entry(45, None, flat=1.65)
        assert earnings_for(e) == pytest.approx(74.25)

    def test_a_count_matching_no_tier_falls_back_to_the_flat_rate(self):
        from app.calculations import earnings_for
        # A malformed table must not silently zero out a day's earnings.
        broken = [{"min_packages": 50, "max_packages": 60, "rate": 9.99}]
        assert earnings_for(_entry(10, broken)) == pytest.approx(16.50)

    def test_an_empty_tier_table_falls_back_to_the_flat_rate(self):
        from app.calculations import earnings_for
        assert earnings_for(_entry(10, [])) == pytest.approx(16.50)


class TestTierSnapshots:
    def test_a_tiered_entry_freezes_the_whole_table(self, conn):
        from app import entries_repo
        repo.update_business(conn, 1, {"rate_model": "tiered"})
        repo.replace_tiers(conn, 1, TIERS)
        eid = entries_repo.create_entry(
            conn, {"date": "2026-08-01", "packages": 45, "miles": 0},
            business_id=1)
        e = entries_repo.get_entry(conn, eid, business_id=1)
        assert e["snap_rate_model"] == "tiered"
        assert len(e["snap_rate_tiers"]) == 3

    def test_changing_tiers_later_does_not_reprice_a_logged_day(self, conn):
        from app import entries_repo
        from app.calculations import earnings_for
        repo.update_business(conn, 1, {"rate_model": "tiered"})
        repo.replace_tiers(conn, 1, TIERS)
        eid = entries_repo.create_entry(
            conn, {"date": "2026-08-01", "packages": 45, "miles": 0},
            business_id=1)
        repo.replace_tiers(conn, 1, [(None, 0.50)])
        e = entries_repo.get_entry(conn, eid, business_id=1)
        assert earnings_for(e) == pytest.approx(74.25)

    def test_editing_the_count_reprices_against_the_frozen_table(self, conn):
        from app import entries_repo
        from app.calculations import earnings_for
        repo.update_business(conn, 1, {"rate_model": "tiered"})
        repo.replace_tiers(conn, 1, TIERS)
        eid = entries_repo.create_entry(
            conn, {"date": "2026-08-01", "packages": 45, "miles": 0},
            business_id=1)
        # 45 -> 38 moves the day from the 41+ tier into the 21-40 tier.
        # The rate moves because the count moved; the contract did not.
        entries_repo.update_entry(
            conn, eid, {"date": "2026-08-01", "packages": 38, "miles": 0},
            business_id=1)
        e = entries_repo.get_entry(conn, eid, business_id=1)
        assert earnings_for(e) == pytest.approx(38 * 1.95)
        assert e["snap_rate_model"] == "tiered"
        assert len(e["snap_rate_tiers"]) == 3
