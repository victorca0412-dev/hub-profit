import json
import httpx

BASE = "https://www.fueleconomy.gov/ws/rest"
HEADERS = {"Accept": "application/json"}


def _get(url, params=None):
    resp = httpx.get(url, headers=HEADERS, params=params)
    resp.raise_for_status()
    return resp.json()


def get_years():
    data = _get(f"{BASE}/vehicle/menu/year")
    return [m["value"] for m in _as_list(data.get("menuItem"))]


def get_makes(year):
    data = _get(f"{BASE}/vehicle/menu/make", params={"year": year})
    return [m["value"] for m in _as_list(data.get("menuItem"))]


def get_models(year, make):
    data = _get(f"{BASE}/vehicle/menu/model",
                params={"year": year, "make": make})
    return [m["value"] for m in _as_list(data.get("menuItem"))]


def combined_mpg_from_options(year, make, model):
    data = _get(f"{BASE}/vehicle/menu/options",
                params={"year": year, "make": make, "model": model})
    items = _as_list(data.get("menuItem"))
    if not items:
        return None
    try:
        vehicle_id = int(items[0]["value"])  # also guards URL path safety
    except (ValueError, TypeError, KeyError):
        return None
    detail = _get(f"{BASE}/vehicle/{vehicle_id}")
    comb = detail.get("comb08")
    try:
        return float(comb)
    except (ValueError, TypeError):
        return None  # EPA returns non-numeric (e.g. EV records, "N/A")


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


# --- caching wrappers (used by routes; take a DB connection) ---

def cached_mpg(conn, year, make, model):
    key = f"mpg:{year}:{make}:{model}"
    row = conn.execute("SELECT payload FROM mpg_cache WHERE cache_key=?",
                       (key,)).fetchone()
    if row:
        return json.loads(row["payload"])
    mpg = combined_mpg_from_options(year, make, model)
    # Only cache a real result. Caching None would permanently poison the
    # lookup if the EPA API was momentarily empty or failing.
    if mpg is not None:
        conn.execute("INSERT OR REPLACE INTO mpg_cache (cache_key, payload) "
                     "VALUES (?, ?)", (key, json.dumps(mpg)))
        conn.commit()
    return mpg
