from app.validation import parse_date, parse_number, parse_int


class TestParseDate:
    def test_accepts_iso_date(self):
        assert parse_date("2026-08-13") == ("2026-08-13", None)

    def test_normalises_unpadded_parts(self):
        assert parse_date("2026-8-3") == ("2026-08-03", None)

    def test_rejects_garbage(self):
        value, err = parse_date("not-a-date")
        assert value is None
        assert "YYYY-MM-DD" in err

    def test_rejects_impossible_calendar_date(self):
        value, err = parse_date("2026-02-30")
        assert value is None
        assert err

    def test_rejects_blank(self):
        value, err = parse_date("")
        assert value is None
        assert err

    def test_rejects_datetime_string(self):
        # fromisoformat would accept this; strptime must not.
        value, err = parse_date("2026-08-13T10:00:00")
        assert value is None
        assert err

    def test_uses_label_in_message(self):
        _, err = parse_date("nope", label="Delivery date")
        assert "Delivery date" in err


class TestParseNumber:
    def test_accepts_decimal(self):
        assert parse_number("38.5", label="Miles") == (38.5, None)

    def test_accepts_integer_string(self):
        assert parse_number("40", label="Miles") == (40.0, None)

    def test_rejects_negative(self):
        value, err = parse_number("-1", label="Miles")
        assert value is None
        assert "negative" in err.lower()

    def test_rejects_garbage(self):
        value, err = parse_number("abc", label="Miles")
        assert value is None
        assert "Miles" in err

    def test_rejects_blank_when_required(self):
        value, err = parse_number("", label="Pay per package")
        assert value is None
        assert "Pay per package" in err

    def test_blank_is_ok_when_optional(self):
        assert parse_number("", label="Hours", required=False) == (None, None)

    def test_garbage_still_rejected_when_optional(self):
        value, err = parse_number("abc", label="Hours", required=False)
        assert value is None
        assert err

    def test_zero_allowed_by_default(self):
        assert parse_number("0", label="Miles") == (0.0, None)

    def test_rejects_infinity(self):
        # float("inf") parses fine and would poison every downstream sum.
        value, err = parse_number("inf", label="Miles")
        assert value is None
        assert err

    def test_rejects_nan(self):
        value, err = parse_number("nan", label="Miles")
        assert value is None
        assert err


class TestParseInt:
    def test_accepts_integer(self):
        assert parse_int("47", label="Packages") == (47, None)

    def test_rejects_negative(self):
        value, err = parse_int("-50", label="Packages")
        assert value is None
        assert "negative" in err.lower()

    def test_rejects_decimal(self):
        value, err = parse_int("47.5", label="Packages")
        assert value is None
        assert "whole number" in err.lower()

    def test_rejects_blank_when_required(self):
        value, err = parse_int("", label="Packages")
        assert value is None
        assert err

    def test_blank_is_ok_when_optional(self):
        assert parse_int("", label="Driver", required=False) == (None, None)
