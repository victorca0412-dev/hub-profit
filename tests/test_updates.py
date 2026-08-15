from app import updates


class TestParseVersion:
    def test_parses_a_plain_version(self):
        assert updates.parse_version("2.1.0") == (2, 1, 0)

    def test_strips_a_leading_v(self):
        assert updates.parse_version("v2.1.0") == (2, 1, 0)

    def test_pads_missing_parts(self):
        assert updates.parse_version("3") == (3, 0, 0)
        assert updates.parse_version("3.2") == (3, 2, 0)

    def test_ignores_a_suffix(self):
        assert updates.parse_version("2.1.0-beta") == (2, 1, 0)

    def test_returns_none_for_rubbish(self):
        for bad in ("", None, "latest", "v", "abc.def"):
            assert updates.parse_version(bad) is None


class TestIsNewer:
    def test_detects_a_newer_release(self):
        assert updates.is_newer("2.2.0", "2.1.0") is True
        assert updates.is_newer("v3.0.0", "2.9.9") is True

    def test_same_version_is_not_newer(self):
        assert updates.is_newer("2.1.0", "2.1.0") is False

    def test_older_is_not_newer(self):
        assert updates.is_newer("2.0.0", "2.1.0") is False

    def test_compares_numerically_not_as_text(self):
        # "2.10.0" < "2.9.0" as strings, but 10 > 9.
        assert updates.is_newer("2.10.0", "2.9.0") is True

    def test_unparseable_is_never_newer(self):
        assert updates.is_newer("nightly", "2.1.0") is False
        assert updates.is_newer("2.2.0", "garbage") is False


class TestCheck:
    def test_reports_up_to_date(self):
        r = updates.check("2.1.0", fetch=lambda: "v2.1.0")
        assert r["status"] == "up-to-date"
        assert "2.1.0" in r["message"]

    def test_reports_an_available_update(self):
        r = updates.check("2.1.0", fetch=lambda: "v2.2.0")
        assert r["status"] == "update-available"
        assert r["latest"] == "2.2.0"
        assert "2.2.0" in r["message"]

    def test_a_newer_local_build_is_not_an_update(self):
        r = updates.check("2.5.0", fetch=lambda: "v2.1.0")
        assert r["status"] == "up-to-date"

    def test_a_newer_local_build_says_so_rather_than_claiming_latest(self):
        # Running unreleased code is not the same as being up to date,
        # and the message should not pretend otherwise.
        r = updates.check("2.5.0", fetch=lambda: "v2.1.0")
        assert "ahead of the latest release" in r["message"]
        assert r["latest"] == "2.1.0"

    def test_matching_versions_say_latest_plainly(self):
        r = updates.check("2.1.0", fetch=lambda: "v2.1.0")
        assert "latest version" in r["message"]
        assert "ahead" not in r["message"]

    def test_network_failure_is_reported_not_raised(self):
        def boom():
            raise OSError("no route to host")
        r = updates.check("2.1.0", fetch=boom)
        assert r["status"] == "unavailable"
        assert "online" in r["message"]

    def test_unexpected_payload_is_reported_not_raised(self):
        r = updates.check("2.1.0", fetch=lambda: None)
        assert r["status"] == "unavailable"

    def test_every_result_carries_the_releases_link(self):
        for fetch in (lambda: "v2.2.0", lambda: "v2.1.0",
                      lambda: (_ for _ in ()).throw(OSError())):
            r = updates.check("2.1.0", fetch=fetch)
            assert r["releases_url"].startswith("https://github.com/")

    def test_no_ordinary_failure_escapes(self):
        # Anything the network or a parser can throw must become a
        # result, not an exception that breaks the Help page.
        for exc in (OSError("dns"), ValueError("bad json"),
                    TimeoutError("slow"), KeyError("tag_name"),
                    RuntimeError("odd")):
            def fetch(e=exc):
                raise e
            assert updates.check("2.1.0", fetch=fetch)["status"] == \
                "unavailable"

    def test_keyboard_interrupt_is_deliberately_not_swallowed(self):
        # Catching BaseException would make the app un-interruptible.
        # Ctrl+C must still work while a check is in flight.
        def fetch():
            raise KeyboardInterrupt
        try:
            updates.check("2.1.0", fetch=fetch)
        except KeyboardInterrupt:
            return
        raise AssertionError("KeyboardInterrupt should propagate")
