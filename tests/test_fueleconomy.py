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
