"""School-year retention window: what it denies, and that it never gates reads."""

import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402

STUDENT = "student-1"


class _Window:
    """Minimal PostgREST stand-in for the single-row config table."""

    def __init__(self, rows, raises=False):
        self._rows = rows
        self._raises = raises

    def table(self, name):
        assert name == "retention_window", name
        return self

    def select(self, *_a):
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        if self._raises:
            raise RuntimeError("retention_window unavailable")
        return type("R", (), {"data": list(self._rows)})()


def _today():
    """Today in UTC (these rows use UTC), not the machine's local date."""
    return datetime.now(timezone.utc).date()


def _row(starts, ends, tz="UTC"):
    return {"id": True, "starts_on": starts, "ends_on": ends, "timezone": tz}


def _window(monkeypatch, rows, raises=False):
    """Real `_retention_window` over conftest's open-year default; clears the TTL cache first."""
    monkeypatch.undo()
    main._retention_cache_clear()
    monkeypatch.setattr(main, "supabase", _Window(rows, raises))
    return main._retention_window()


# ── the four denying states, each named ─────────────────────────────────────

def test_a_day_inside_the_window_records(monkeypatch):
    today = _today()
    w = _window(monkeypatch, [_row(str(today - timedelta(days=1)),
                                   str(today + timedelta(days=1)))])
    assert w["state"] == main.WINDOW_OPEN


def test_the_first_and_last_days_of_school_are_school_days(monkeypatch):
    today = str(_today())
    assert _window(monkeypatch, [_row(today, "2099-12-31")])["state"] == main.WINDOW_OPEN
    assert _window(monkeypatch, [_row("2000-01-01", today)])["state"] == main.WINDOW_OPEN


def test_before_the_year_starts_is_not_the_same_as_after_it_ends(monkeypatch):
    today = _today()
    before = _window(monkeypatch, [_row(str(today + timedelta(days=10)),
                                        str(today + timedelta(days=20)))])
    after = _window(monkeypatch, [_row(str(today - timedelta(days=20)),
                                       str(today - timedelta(days=10)))])
    assert before["state"] == main.WINDOW_BEFORE
    assert after["state"] == main.WINDOW_AFTER
    assert before["state"] != after["state"]


def test_an_unconfigured_window_records_nothing(monkeypatch):
    w = _window(monkeypatch, [])
    assert w["state"] == main.WINDOW_UNCONFIGURED
    assert w["state"] in main._WINDOW_DENIED


def test_a_failed_read_records_nothing_and_says_so(monkeypatch):
    w = _window(monkeypatch, [], raises=True)
    assert w["state"] == main.WINDOW_UNREADABLE
    # Distinct from unconfigured: that one is the school's, this one is ours.
    assert w["state"] != main.WINDOW_UNCONFIGURED


# ── the timezone is load-bearing ────────────────────────────────────────────

def test_the_boundary_is_resolved_in_the_schools_timezone(monkeypatch):
    """03:00 UTC is still the previous day (`ends_on`) in Los Angeles."""
    class _FixedNow:
        @staticmethod
        def now(tz=None):
            from datetime import datetime, timezone as _tz
            return datetime(2026, 6, 12, 3, 0, tzinfo=_tz.utc).astimezone(tz or _tz.utc)

    monkeypatch.undo()
    main._retention_cache_clear()
    monkeypatch.setattr(main, "_utc_now", lambda: _FixedNow.now())

    # 11 June in Los Angeles, 12 June in UTC.
    monkeypatch.setattr(main, "supabase",
                        _Window([_row("2026-01-05", "2026-06-11", "America/Los_Angeles")]))
    assert main._retention_window()["state"] == main.WINDOW_OPEN, (
        "the school's last day was cut short by a UTC comparison"
    )

    main._retention_cache_clear()
    monkeypatch.setattr(main, "supabase",
                        _Window([_row("2026-01-05", "2026-06-11", "UTC")]))
    assert main._retention_window()["state"] == main.WINDOW_AFTER, (
        "the control: in UTC that same instant really is past the window"
    )


def test_an_unknown_timezone_denies_rather_than_assuming_utc(monkeypatch):
    """A UTC fallback would silently shift every boundary by hours."""
    w = _window(monkeypatch, [_row("2000-01-01", "2099-12-31", "Mars/Olympus_Mons")])
    assert w["state"] == main.WINDOW_UNREADABLE


def test_unparseable_dates_deny(monkeypatch):
    w = _window(monkeypatch, [_row("not-a-date", "2099-12-31")])
    assert w["state"] == main.WINDOW_UNREADABLE


# ── composition with consent ────────────────────────────────────────────────

@pytest.fixture
def _consented(monkeypatch):
    """Every channel consented, so the window is the only variable."""
    monkeypatch.setattr(main, "_consent", lambda _s: {
        "eeg_enabled": True, "headband_optical_enabled": True,
        "camera_enabled": True, "retrieved": True, "exists": True})


def test_an_open_window_records_what_was_consented(monkeypatch, _consented):
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": main.WINDOW_OPEN, "starts_on": "2000-01-01",
        "ends_on": "2099-12-31", "timezone": "UTC"})
    gate = main._may_record(STUDENT)
    assert (gate["record_eeg"], gate["record_camera"],
            gate["record_headband_optical"]) == (True, True, True)


@pytest.mark.parametrize("state", sorted(main._WINDOW_DENIED))
def test_a_closed_window_records_nothing_however_consented(monkeypatch, _consented, state):
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": state, "starts_on": "2000-01-01", "ends_on": "2000-06-01",
        "timezone": "UTC"})
    gate = main._may_record(STUDENT)
    assert not any((gate["record_eeg"], gate["record_camera"],
                    gate["record_headband_optical"]))
    # Raw consent rides alongside: "said yes, year over" vs "said no".
    assert gate["eeg_enabled"] is True


def test_the_window_does_not_touch_what_may_be_read(monkeypatch):
    """Gating reads on the window would hide a parent's history the day the year ends."""
    import inspect
    source = inspect.getsource(main._reportable_channels)
    assert "_may_record" not in source, (
        "_reportable_channels is gating reads on the retention window"
    )
    assert "_consent(" in source


def test_the_reason_names_the_window_before_the_channel(monkeypatch):
    for state, expected in (
        (main.WINDOW_BEFORE, "not started"),
        (main.WINDOW_AFTER, "ended"),
        (main.WINDOW_UNCONFIGURED, "no school year"),
        (main.WINDOW_UNREADABLE, "could not check"),
    ):
        reason = main._not_recording_reason(
            {"window_state": state, "retrieved": True}, "eeg not consented")
        assert expected in reason, (state, reason)
        assert "not consented" not in reason

    # Year open: the caller's wording comes back whole, not interpolated.
    assert main._not_recording_reason(
        {"window_state": main.WINDOW_OPEN, "retrieved": True},
        "no consented heart sensor") == "no consented heart sensor"


# ── every recording site is gated, checked together ─────────────────────────
# Checked by source: the property is that no site reads consent without the window.

# Discovered, not listed: any `main` function inserting into these is a recording site.
_SIGNAL_TABLES = ("cognitive_signals", "face_signals", "heart_signals")

# The poller's permission checks gate without inserting, so they are listed by hand.
_GATING_CALLBACKS = ("_poller_may_record_eeg", "_poller_may_record_eeg_reason",
                     "_heart_consent_for_poller")


def _writes_signal_rows(source: str) -> bool:
    """Whether a function writes a signal table; over-matches on purpose.

    `upsert` covers `ingest_heart`, which dedupes on `heart_session_source_ts_key`.
    """
    return (any(t in source for t in _SIGNAL_TABLES)
            and (".insert(" in source or ".upsert(" in source))


def _recording_sites():
    import inspect
    found = []
    for name, obj in vars(main).items():
        if not inspect.isfunction(obj) or obj.__module__ != "main":
            continue
        try:
            source = inspect.getsource(obj)
        except OSError:                      # pragma: no cover -- defensive
            continue
        if _writes_signal_rows(source):
            found.append(name)
    return sorted(found)


def test_the_discovery_finds_the_endpoints_we_know_about():
    """An empty discovery would let every parametrised test below pass vacuously."""
    sites = _recording_sites()
    for known in ("ingest_cognitive", "ingest_face", "ingest_heart"):
        assert known in sites, (known, sites)


@pytest.mark.parametrize("name", _recording_sites() + list(_GATING_CALLBACKS)
                         + ["eeg_start"])
def test_every_recording_site_gates_on_the_window(name):
    import inspect
    source = inspect.getsource(getattr(main, name))
    assert "_may_record(" in source, (
        f"{name} writes signal rows but does not consult _may_record -- it is "
        "gated on consent alone, so it records outside the school year"
    )
    # With the paren: only the call is the mistake, not the word.
    assert "_consent(" not in source, (
        f"{name} calls _consent directly. Use _may_record, which composes it "
        "with the retention window; the raw flags are on its result if a "
        "caller genuinely needs to tell 'they agreed' from 'may record now'."
    )


def test_a_raw_consent_dict_permits_nothing():
    """`_permitted_heart_sources` reads `record_*`, so forgetting the window records nothing."""
    raw_consent = {"headband_optical_enabled": True, "camera_enabled": True,
                   "retrieved": True}
    assert main._permitted_heart_sources(raw_consent) == set()

    permitted = main._permitted_heart_sources(
        {"record_headband_optical": True, "record_camera": False})
    assert permitted == {"muse_optics", "muse_ppg"}


# ── the poller's status says why, rather than going quiet ───────────────────

@pytest.mark.parametrize("state,expected", [
    (main.WINDOW_BEFORE, "school_year_not_started"),
    (main.WINDOW_AFTER, "school_year_ended"),
    (main.WINDOW_UNCONFIGURED, "school_year_unconfigured"),
    (main.WINDOW_UNREADABLE, "school_year_unknown"),
])
def test_a_closed_year_explains_why_the_poller_is_not_running(monkeypatch, state, expected):
    monkeypatch.setattr(main.eeg_poller, "status", lambda _u: {"running": False})
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": state, "starts_on": "2026-09-01", "ends_on": "2027-06-30",
        "timezone": "UTC"})
    monkeypatch.setattr(main, "_consent", lambda _s: {
        "eeg_enabled": True, "retrieved": True})

    status = main._poller_status(STUDENT)

    assert status["stopped_reason"] == expected
    # The dates ride along, so a surface can say *when*.
    assert status["window_starts_on"] == "2026-09-01"


def test_the_window_outranks_consent_in_the_status_too(monkeypatch):
    monkeypatch.setattr(main.eeg_poller, "status", lambda _u: {"running": False})
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": main.WINDOW_AFTER, "starts_on": "2026-09-01",
        "ends_on": "2027-06-30", "timezone": "UTC"})
    monkeypatch.setattr(main, "_consent", lambda _s: {
        "eeg_enabled": False, "retrieved": True, "eeg_revoked_at": "2026-10-01"})

    assert main._poller_status(STUDENT)["stopped_reason"] == "school_year_ended"


def test_every_window_state_has_a_meaning():
    declared = {v for k, v in vars(main).items()
                if k.startswith("WINDOW_") and isinstance(v, str)}
    assert declared, "the constants moved; this test is no longer looking at them"

    missing = declared - set(main._WINDOW_STATES)
    assert not missing, (
        f"{sorted(missing)} declared without a row in _WINDOW_STATES -- it would "
        "deny (that part is derived) and then explain itself as nothing"
    )

    for state, meaning in main._WINDOW_STATES.items():
        if meaning.records:
            assert meaning.reason is None and meaning.stopped_reason is None, (
                f"{state} records, so it has no reason to give"
            )
        else:
            assert meaning.reason and meaning.stopped_reason, (
                f"{state} denies without saying why"
            )


def test_the_timezone_fallback_cannot_itself_fail(monkeypatch):
    """Degrades to UTC without `tzdata`; pinned here because Linux CI's system tz database masks it."""
    def _no_tzdata(*_a, **_k):
        raise Exception("No time zone found with key UTC")

    monkeypatch.setattr(main, "ZoneInfo", _no_tzdata)
    main._retention_cache = None

    tz = main._school_timezone()

    assert tz is not None, "the fallback raised instead of degrading"
    # Usable as a tzinfo: callers pass it straight to astimezone().
    stamped = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc).astimezone(tz)
    assert stamped.utcoffset() == timedelta(0)


# ── the switch: a deployment that is not on a school year ───────────────────

def _unenforced(**over):
    """A row that says "not gating on a year": no dates."""
    row = {"id": True, "starts_on": None, "ends_on": None,
           "timezone": "UTC", "enforced": False}
    row.update(over)
    return row


def test_an_unenforced_window_records(monkeypatch):
    w = _window(monkeypatch, [_unenforced()])

    assert w["state"] == main.WINDOW_NOT_ENFORCED
    assert main._WINDOW_STATES[w["state"]].records is True


def test_not_enforced_is_distinguishable_from_inside_the_year(monkeypatch):
    today = _today()
    inside = _window(monkeypatch, [_row(str(today - timedelta(days=1)),
                                        str(today + timedelta(days=1)))])
    off = _window(monkeypatch, [_unenforced()])

    assert inside["state"] != off["state"]


def test_enforcing_with_no_dates_denies_rather_than_recording_for_ever(monkeypatch):
    w = _window(monkeypatch, [_row(None, None)])

    assert w["state"] == main.WINDOW_UNCONFIGURED
    assert w["state"] in main._WINDOW_DENIED


def test_a_row_without_the_column_still_enforces(monkeypatch):
    """`enforced` is read with `is False`, so an absent column keeps the gate on."""
    today = _today()
    legacy = _row(str(today + timedelta(days=30)), str(today + timedelta(days=200)))
    assert "enforced" not in legacy

    assert _window(monkeypatch, [legacy])["state"] == main.WINDOW_BEFORE

    # An explicit null is the same case.
    assert _window(monkeypatch, [dict(legacy, enforced=None)])["state"] == main.WINDOW_BEFORE


def test_an_unenforced_window_still_needs_a_resolvable_timezone(monkeypatch):
    """Reporting buckets days in the school timezone whether or not the year is enforced."""
    w = _window(monkeypatch, [_unenforced(timezone="Not/AZone")])

    assert w["state"] == main.WINDOW_UNREADABLE


def test_turning_enforcement_off_does_not_bypass_consent(monkeypatch):
    monkeypatch.undo()
    main._retention_cache_clear()
    monkeypatch.setattr(main, "supabase", _Window([_unenforced()]))
    monkeypatch.setattr(main, "_consent", lambda *_a, **_k: {
        "retrieved": True, "eeg_enabled": False,
        "headband_optical_enabled": False, "camera_enabled": False})

    verdict = main._may_record(STUDENT)

    assert not verdict.get("record_eeg")
