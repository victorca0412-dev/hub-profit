"""Check GitHub for a newer HubProfit release.

Only ever runs when the user clicks the button. HubProfit's promise is
that it does not phone home, and an automatic check on page load would
break that whether or not anyone noticed. The one network call this
module makes is user-initiated, the same shape as the MPG lookup.
"""

import httpx

RELEASES_API = ("https://api.github.com/repos/"
                "victorca0412-dev/hub-profit/releases/latest")
RELEASES_PAGE = "https://github.com/victorca0412-dev/hub-profit/releases"
TIMEOUT_SECONDS = 6


def parse_version(text):
    """'v2.1.0' or '2.1' -> (2, 1, 0). Returns None if unparseable."""
    if not text:
        return None
    cleaned = str(text).strip().lstrip("vV")
    parts = cleaned.split(".")[:3]
    numbers = []
    for part in parts:
        digits = ""
        for ch in part:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            return None
        numbers.append(int(digits))
    if not numbers:
        return None
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers)


def is_newer(candidate, current):
    """True when candidate is a strictly later version than current."""
    a, b = parse_version(candidate), parse_version(current)
    if a is None or b is None:
        return False
    return a > b


def _fetch_latest_tag():
    resp = httpx.get(RELEASES_API, timeout=TIMEOUT_SECONDS,
                     headers={"Accept": "application/vnd.github+json"})
    resp.raise_for_status()
    return resp.json().get("tag_name")


def check(current_version, fetch=None):
    """Compare the running version against the latest published release.

    Returns a dict the template renders directly. Never raises: an
    update check that breaks the page would be worse than no check, and
    the app has to work on a machine with no internet at all.
    """
    fetch = fetch or _fetch_latest_tag
    try:
        latest = fetch()
    except Exception:
        return {"status": "unavailable",
                "message": "Could not reach GitHub. Check again when you "
                           "are online.",
                "current": current_version,
                "releases_url": RELEASES_PAGE}
    if parse_version(latest) is None:
        return {"status": "unavailable",
                "message": "GitHub returned something unexpected.",
                "current": current_version,
                "releases_url": RELEASES_PAGE}
    if is_newer(latest, current_version):
        return {"status": "update-available",
                "message": "Version %s is available. You are running %s."
                           % (str(latest).lstrip("vV"), current_version),
                "latest": str(latest).lstrip("vV"),
                "current": current_version,
                "releases_url": RELEASES_PAGE}
    published = str(latest).lstrip("vV")
    if is_newer(current_version, published):
        # An unreleased local build. Saying "you have the latest" would
        # claim something that has not been checked.
        message = ("You are running %s, which is ahead of the latest "
                   "release (%s)." % (current_version, published))
    else:
        message = ("You are running the latest version (%s)."
                   % current_version)
    return {"status": "up-to-date",
            "message": message,
            "latest": published,
            "current": current_version,
            "releases_url": RELEASES_PAGE}
