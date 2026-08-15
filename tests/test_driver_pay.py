import pytest

from app import businesses_repo, drivers_repo, entries_repo
from app.calculations import compute_entry, driver_cost


class TestMigrationV4:
    def test_version_is_four(self, conn):
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 4

    def test_drivers_have_pay_columns(self, conn):
        cols = {r[1] for r in conn.execute("PRAGMA table_info(drivers)")}
        assert {"pay_model", "pay_rate"} <= cols

    def test_entries_have_driver_snapshot_columns(self, conn):
        cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_entries)")}
        assert {"snap_driver_pay_model", "snap_driver_pay_rate"} <= cols

    def test_the_shared_driver_expense_row_is_gone(self, conn):
        n = conn.execute(
            "SELECT COUNT(*) FROM expense_config WHERE key='driver'"
        ).fetchone()[0]
        assert n == 0


class TestDriverPayStorage:
    def test_a_driver_defaults_to_per_package_at_zero(self, conn):
        did = drivers_repo.add_driver(conn, "Alex", business_id=1)
        d = drivers_repo.get_driver(conn, did, business_id=1)
        assert d["pay_model"] == "per_package"
        assert d["pay_rate"] == 0.0

    def test_a_driver_can_be_created_with_a_rate(self, conn):
        did = drivers_repo.add_driver(conn, "Alex", business_id=1,
                                      pay_model="per_day", pay_rate=95.0)
        d = drivers_repo.get_driver(conn, did, business_id=1)
        assert d["pay_model"] == "per_day"
        assert d["pay_rate"] == 95.0

    def test_pay_can_be_updated(self, conn):
        did = drivers_repo.add_driver(conn, "Alex", business_id=1)
        assert drivers_repo.update_driver_pay(
            conn, did, 1, "per_package", 1.5) is True
        assert drivers_repo.get_driver(
            conn, did, business_id=1)["pay_rate"] == 1.5

    def test_pay_update_will_not_cross_businesses(self, conn):
        other = businesses_repo.create_business(conn, "Newton Hub")
        did = drivers_repo.add_driver(conn, "Alex", business_id=1)
        assert drivers_repo.update_driver_pay(
            conn, did, other, "per_package", 9.0) is False

    def test_an_unknown_pay_model_is_refused(self, conn):
        with pytest.raises(ValueError):
            drivers_repo.add_driver(conn, "Alex", business_id=1,
                                    pay_model="hourly")


def _entry(packages=45, miles=38.0, driver_id=1, model="per_package",
           rate=1.5, cfg=None):
    return {"packages": packages, "miles": miles, "hours": None,
            "extra_expense": None, "driver_id": driver_id,
            "snap_pay_per_package": 1.65, "snap_gas_price": 3.40,
            "snap_mpg": 28.0,
            "snap_expense_config": cfg if cfg is not None else {},
            "snap_rate_model": "flat", "snap_rate_tiers": None,
            "snap_driver_pay_model": model, "snap_driver_pay_rate": rate}


MILEAGE_CFG = {
    "fuel": {"enabled": True, "mode": "mileage_fuel", "amount": 0.0},
    "vehicle_wear": {"enabled": True, "mode": "per_mile", "amount": 0.12},
}


class TestDriverCost:
    def test_per_package_multiplies_by_count(self):
        assert driver_cost(_entry(packages=45, rate=1.5)) == pytest.approx(67.5)

    def test_per_day_is_flat(self):
        assert driver_cost(
            _entry(packages=45, model="per_day", rate=95.0)) == 95.0

    def test_no_driver_costs_nothing(self):
        assert driver_cost(_entry(driver_id=None)) == 0

    def test_missing_snapshot_costs_nothing(self):
        e = _entry()
        e["snap_driver_pay_model"] = None
        e["snap_driver_pay_rate"] = None
        assert driver_cost(e) == 0


class TestMileageOnDriverDays:
    def test_fuel_and_wear_are_not_charged_on_a_driver_day(self):
        r = compute_entry(_entry(cfg=MILEAGE_CFG), 1)
        assert "fuel" not in r["expenses"]
        assert "vehicle_wear" not in r["expenses"]

    def test_fuel_and_wear_are_charged_on_an_owner_day(self):
        r = compute_entry(_entry(driver_id=None, cfg=MILEAGE_CFG), 1)
        assert r["expenses"]["fuel"] > 0
        assert r["expenses"]["vehicle_wear"] > 0

    def test_monthly_costs_still_apply_on_driver_days(self):
        cfg = {"insurance": {"enabled": True, "mode": "monthly",
                             "amount": 90.0}}
        r = compute_entry(_entry(cfg=cfg), 30)
        assert r["expenses"]["insurance"] == pytest.approx(3.0)


class TestMargin:
    def test_net_is_the_owners_margin(self):
        r = compute_entry(_entry(packages=45, rate=1.5), 1)
        # 45 x 1.65 earned, 45 x 1.50 paid out.
        assert r["earnings"] == pytest.approx(74.25)
        assert r["expenses"]["driver"] == pytest.approx(67.5)
        assert r["net"] == pytest.approx(6.75)

    def test_a_rate_above_the_owners_produces_a_loss(self):
        r = compute_entry(_entry(packages=45, rate=1.70), 1)
        assert r["net"] < 0

    def test_tiered_contract_margin_shrinks_on_larger_blocks(self):
        tiers = [{"min_packages": 1, "max_packages": 20, "rate": 2.25},
                 {"min_packages": 21, "max_packages": 40, "rate": 1.95},
                 {"min_packages": 41, "max_packages": None, "rate": 1.65}]
        small = _entry(packages=18, rate=1.5)
        big = _entry(packages=45, rate=1.5)
        for e in (small, big):
            e["snap_rate_model"] = "tiered"
            e["snap_rate_tiers"] = tiers
        # The table in the spec: $13.50 on 18 packages, $6.75 on 45.
        assert compute_entry(small, 1)["net"] == pytest.approx(13.50)
        assert compute_entry(big, 1)["net"] == pytest.approx(6.75)


class TestDriverSnapshots:
    def test_an_entry_freezes_the_drivers_rate(self, conn):
        did = drivers_repo.add_driver(conn, "Alex", business_id=1,
                                      pay_rate=1.5)
        eid = entries_repo.create_entry(
            conn, {"date": "2026-08-01", "packages": 45, "miles": 0,
                   "driver_id": did}, business_id=1)
        e = entries_repo.get_entry(conn, eid, business_id=1)
        assert e["snap_driver_pay_rate"] == 1.5
        assert e["snap_driver_pay_model"] == "per_package"

    def test_a_raise_does_not_reprice_a_logged_day(self, conn):
        did = drivers_repo.add_driver(conn, "Alex", business_id=1,
                                      pay_rate=1.5)
        eid = entries_repo.create_entry(
            conn, {"date": "2026-08-01", "packages": 45, "miles": 0,
                   "driver_id": did}, business_id=1)
        drivers_repo.update_driver_pay(conn, did, 1, "per_package", 1.9)
        e = entries_repo.get_entry(conn, eid, business_id=1)
        assert driver_cost(e) == pytest.approx(67.5)

    def test_editing_the_count_reprices_against_the_frozen_rate(self, conn):
        did = drivers_repo.add_driver(conn, "Alex", business_id=1,
                                      pay_rate=1.5)
        eid = entries_repo.create_entry(
            conn, {"date": "2026-08-01", "packages": 45, "miles": 0,
                   "driver_id": did}, business_id=1)
        entries_repo.update_entry(
            conn, eid, {"date": "2026-08-01", "packages": 30, "miles": 0,
                        "driver_id": did}, business_id=1)
        e = entries_repo.get_entry(conn, eid, business_id=1)
        assert driver_cost(e) == pytest.approx(45.0)
        assert e["snap_driver_pay_rate"] == 1.5

    def test_no_driver_leaves_the_snapshot_null(self, conn):
        eid = entries_repo.create_entry(
            conn, {"date": "2026-08-01", "packages": 10, "miles": 5},
            business_id=1)
        e = entries_repo.get_entry(conn, eid, business_id=1)
        assert e["snap_driver_pay_model"] is None
