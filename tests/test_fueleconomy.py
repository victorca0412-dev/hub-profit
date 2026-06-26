from app.fueleconomy import combined_mpg_from_options


class FakeResp:
    def __init__(self, data):
        self._data = data
    def json(self):
        return self._data
    def raise_for_status(self):
        pass


def test_combined_mpg_parses_comb08(monkeypatch):
    def fake_get(url, headers=None, params=None):
        if "options" in url:
            return FakeResp({"menuItem": {"text": "Auto (S6)", "value": "41234"}})
        return FakeResp({"comb08": "28"})
    import app.fueleconomy as fe
    monkeypatch.setattr(fe.httpx, "get", fake_get)
    mpg = combined_mpg_from_options(2019, "Toyota", "RAV4")
    assert mpg == 28.0


def test_combined_mpg_handles_list_of_options(monkeypatch):
    def fake_get(url, headers=None, params=None):
        if "options" in url:
            return FakeResp({"menuItem": [
                {"text": "FWD", "value": "100"},
                {"text": "AWD", "value": "200"}]})
        return FakeResp({"comb08": "26"})
    import app.fueleconomy as fe
    monkeypatch.setattr(fe.httpx, "get", fake_get)
    mpg = combined_mpg_from_options(2019, "Toyota", "RAV4")
    assert mpg == 26.0


def test_get_makes_returns_values(monkeypatch):
    import app.fueleconomy as fe

    def fake_get(url, headers=None, params=None):
        return FakeResp({"menuItem": [
            {"text": "Toyota", "value": "Toyota"},
            {"text": "Honda", "value": "Honda"}]})

    monkeypatch.setattr(fe.httpx, "get", fake_get)
    assert fe.get_makes(2019) == ["Toyota", "Honda"]


def test_combined_mpg_none_when_no_options(monkeypatch):
    import app.fueleconomy as fe

    def fake_get(url, headers=None, params=None):
        return FakeResp({})  # no menuItem at all

    monkeypatch.setattr(fe.httpx, "get", fake_get)
    assert combined_mpg_from_options(2019, "X", "Y") is None


def test_combined_mpg_none_when_comb08_non_numeric(monkeypatch):
    import app.fueleconomy as fe

    def fake_get(url, headers=None, params=None):
        if "options" in url:
            return FakeResp({"menuItem": {"text": "x", "value": "1"}})
        return FakeResp({"comb08": "N/A"})

    monkeypatch.setattr(fe.httpx, "get", fake_get)
    assert combined_mpg_from_options(2019, "X", "Y") is None


def test_none_result_is_not_cached(monkeypatch, conn):
    import app.fueleconomy as fe

    def fake_get(url, headers=None, params=None):
        return FakeResp({})  # no options -> None

    monkeypatch.setattr(fe.httpx, "get", fake_get)
    assert fe.cached_mpg(conn, 2019, "X", "Y") is None
    count = conn.execute("SELECT COUNT(*) FROM mpg_cache").fetchone()[0]
    assert count == 0


def test_cached_mpg_stores_and_avoids_second_network_call(monkeypatch, conn):
    import app.fueleconomy as fe
    calls = {"n": 0}

    def fake_get(url, headers=None, params=None):
        calls["n"] += 1
        if "options" in url:
            return FakeResp({"menuItem": {"text": "x", "value": "1"}})
        return FakeResp({"comb08": "30"})

    monkeypatch.setattr(fe.httpx, "get", fake_get)
    first = fe.cached_mpg(conn, 2019, "Toyota", "RAV4")
    calls_after_first = calls["n"]
    second = fe.cached_mpg(conn, 2019, "Toyota", "RAV4")
    assert first == 30.0
    assert second == 30.0
    # second lookup served from cache: no additional network calls
    assert calls["n"] == calls_after_first
