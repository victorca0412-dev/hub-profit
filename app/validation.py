"""User-input parsers.

Each parser returns (value, error_message). Exactly one of the two is
None. A blank optional field is success with no value: (None, None).

These deliberately never fall back to a default. Substituting a default
for something the user typed is how a cleared pay-rate field silently
became $1.65 and poisoned every entry logged afterwards.
"""

import math
from datetime import datetime


def parse_date(raw, label="Date"):
    text = (raw or "").strip()
    if not text:
        return None, f"{label} is required."
    try:
        # strptime, not date.fromisoformat: fromisoformat also accepts
        # "20260813" and full datetime strings, which are not what the
        # date input posts and not what the DB queries compare against.
        return datetime.strptime(text, "%Y-%m-%d").date().isoformat(), None
    except ValueError:
        return None, f"{label} must be a real date in YYYY-MM-DD form."


def _blank(raw):
    text = raw.strip() if isinstance(raw, str) else raw
    return text, text in (None, "")


def parse_number(raw, label, required=True, min_value=0.0):
    text, is_blank = _blank(raw)
    if is_blank:
        if required:
            return None, f"{label} is required."
        return None, None
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None, f"{label} must be a number."
    if math.isnan(value) or math.isinf(value):
        return None, f"{label} must be a number."
    if value < min_value:
        if min_value == 0:
            return None, f"{label} cannot be negative."
        return None, f"{label} must be at least {min_value}."
    return value, None


def parse_int(raw, label, required=True, min_value=0):
    text, is_blank = _blank(raw)
    if is_blank:
        if required:
            return None, f"{label} is required."
        return None, None
    try:
        value = int(text)
    except (TypeError, ValueError):
        return None, f"{label} must be a whole number."
    if value < min_value:
        if min_value == 0:
            return None, f"{label} cannot be negative."
        return None, f"{label} must be at least {min_value}."
    return value, None
