"""Tests the school-year retention window: what it denies, and what it must
not.

Recording is gated on the window and consent separately, and both fail
closed. The tests that matter prove they stay separate -- a window that
also suppressed reading would take away a parent's history on the last day
of term, months before the delete job runs.

Every test here overrides conftest's `_school_year_is_open`, since a
default nothing can turn off is a rule nothing tests.
"""

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
    """Today in UTC, since these rows use `timezone: UTC`. Not
    `date.today()` (the machine's local date) -- the two differ for part of
    every day off UTC, so a test built on the local date would pass or fail
    depending on when the suite ran."""
    return datetime.now(timezone.utc).date()


def _row(starts, ends, tz="UTC"):
    return {"id": True, "starts_on": starts, "ends_on": ends, "timezone": tz}


def _window(monkeypatch, rows, raises=False):
    """Installs a real `_retention_window` over conftest's open-year
    default. Clears the TTL cache first, or every test after the first
    would assert against the previous test's cached row."""
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
    """Both bounds are inclusive. The last day matters most: the delete
    job runs on `ends_on`, so if that date didn't record, the job's own
    day would look like an outage instead of a boundary."""
    today = str(_today())
    assert _window(monkeypatch, [_row(today, "2099-12-31")])["state"] == main.WINDOW_OPEN
    assert _window(monkeypatch, [_row("2000-01-01", today)])["state"] == main.WINDOW_OPEN


def test_before_the_year_starts_is_not_the_same_as_after_it_ends(monkeypatch):
    """Two states, not one "closed". "Not started yet" and "the year is
    over" lead a parent to different actions -- collapsing them is the
    same failure as a tile that can't tell no-data from zero."""
    today = _today()
    before = _window(monkeypatch, [_row(str(today + timedelta(days=10)),
                                        str(today + timedelta(days=20)))])
    after = _window(monkeypatch, [_row(str(today - timedelta(days=20)),
                                       str(today - timedelta(days=10)))])
    assert before["state"] == main.WINDOW_BEFORE
    assert after["state"] == main.WINDOW_AFTER
    assert before["state"] != after["state"]


def test_an_unconfigured_window_records_nothing(monkeypatch):
    """No row is not an open-ended licence -- same default as consent:
    nothing recorded until someone says so. An unconfigured school gets a
    visible, fixable state, not a silent assumption that recording is
    fine."""
    w = _window(monkeypatch, [])
    assert w["state"] == main.WINDOW_UNCONFIGURED
    assert w["state"] in main._WINDOW_DENIED


def test_a_failed_read_records_nothing_and_says_so(monkeypatch):
    w = _window(monkeypatch, [], raises=True)
    assert w["state"] == main.WINDOW_UNREADABLE
    # Distinct from unconfigured: one is a fact about the school, the other is
    # our fault, and only the second is worth paging anyone about.
    assert w["state"] != main.WINDOW_UNCONFIGURED


# ── the timezone is load-bearing ────────────────────────────────────────────

def test_the_boundary_is_resolved_in_the_schools_timezone(monkeypatch):
    """The last day of school ends at local midnight, not UTC midnight.
    Constructed so UTC and the school disagree: at 03:00 UTC it's still the
    previous day in Los Angeles. With `ends_on` set to that day, a UTC
    comparison would say the year is over while the school is still in its
    final day."""
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
    """Falling back to UTC would move every boundary by hours while looking
    like it worked, on a value edited by hand twice a year."""
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
    """Consent is necessary and not sufficient. Every denying state denies all
    three channels, so a new one cannot be added on the permissive side by
    accident."""
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": state, "starts_on": "2000-01-01", "ends_on": "2000-06-01",
        "timezone": "UTC"})
    gate = main._may_record(STUDENT)
    assert not any((gate["record_eeg"], gate["record_camera"],
                    gate["record_headband_optical"]))
    # The raw consent answer is left intact beside it, so a caller can still
    # tell "they said yes but the year is over" from "they said no".
    assert gate["eeg_enabled"] is True


def test_the_window_does_not_touch_what_may_be_read(monkeypatch):
    """A data-loss bug, not just a recording one: `_reportable_channels`
    reads `_consent`, not `_may_record`. If the window gated it, every
    channel would report off the day the year ended, and a parent's
    history would vanish -- reading as a withdrawal nobody made."""
    import inspect
    source = inspect.getsource(main._reportable_channels)
    assert "_may_record" not in source, (
        "_reportable_channels is gating reads on the retention window"
    )
    assert "_consent(" in source


def test_the_reason_names_the_window_before_the_channel(monkeypatch):
    """When the year is closed, no channel is recording -- so saying "eeg not
    consented" would send a parent to the consent screen to fix something that
    is not broken there."""
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

    # And with the year open it falls through to the caller's own wording,
    # whole rather than interpolated -- "no consented heart sensor" must not
    # come back as "no consented heart sensor not consented".
    assert main._not_recording_reason(
        {"window_state": main.WINDOW_OPEN, "retrieved": True},
        "no consented heart sensor") == "no consented heart sensor"


# ── every recording site is gated, checked together ─────────────────────────
#
# The negative above proves reading is not gated. This is the other half:
# every recording site must gate too, or it would pass with the window
# wired into nothing.
#
# Checked by source, not behaviour: driving each site to a refusal needs
# six different fixtures, and what matters is not that they refuse today
# but that none of them reads consent without the window -- a property of
# the call itself. Same approach as `_MODE_AWARE` in test_ingest_mode.py.

# The endpoints are discovered, not listed: any function in `main` that
# inserts into a signal table is a recording site by definition. A
# hand-kept allowlist would need someone to remember to add each new one;
# "writes signal rows" does not, so a seventh endpoint added later is
# caught automatically.
_SIGNAL_TABLES = ("cognitive_signals", "face_signals", "heart_signals")

# These two gate without inserting -- they're the poller's permission
# checks, and the poller does the writing. Nothing in their source reveals
# that, so they stay an explicit exception list beside the derived rule.
_GATING_CALLBACKS = ("_poller_may_record_eeg", "_poller_may_record_eeg_reason",
                     "_heart_consent_for_poller")


def _writes_signal_rows(source: str) -> bool:
    """Whether a function inserts into a signal table.

    Deliberately crude: a table name and a write call in the same body. It
    over-matches on purpose -- over-matching just means someone gets asked
    about a function that didn't need gating, while under-matching hides a
    real recording site.

    `upsert` is included because leaving it out missed `ingest_heart`: heart
    rows dedupe on `heart_session_source_ts_key`, so that endpoint upserts
    where the other two insert.
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
    """The derived list only matters if it actually finds things. A
    typo in `_writes_signal_rows` would yield an empty set, and every
    parametrised test below would pass by having nothing to check."""
    sites = _recording_sites()
    for known in ("ingest_cognitive", "ingest_face", "ingest_heart"):
        assert known in sites, (known, sites)


@pytest.mark.parametrize("name", _recording_sites() + list(_GATING_CALLBACKS)
                         + ["eeg_start"])
def test_every_recording_site_gates_on_the_window(name):
    """A site that calls `_consent` directly records all summer. Consent
    alone is not sufficient -- `_may_record` also checks the window, and
    skipping it writes rows outside the window with no error at all."""
    import inspect
    source = inspect.getsource(getattr(main, name))
    assert "_may_record(" in source, (
        f"{name} writes signal rows but does not consult _may_record -- it is "
        "gated on consent alone, so it records outside the school year"
    )
    # `_consent(` with a paren: the word appears in prose and in variable names
    # all over these functions, and only the call is the mistake.
    assert "_consent(" not in source, (
        f"{name} calls _consent directly. Use _may_record, which composes it "
        "with the retention window; the raw flags are on its result if a "
        "caller genuinely needs to tell 'they agreed' from 'may record now'."
    )


def test_a_raw_consent_dict_permits_nothing():
    """The safe direction for the mistake the test above catches.
    `_permitted_heart_sources` reads `record_*`, so a bare `_consent` result
    yields no sources -- a caller that forgets the window records nothing
    instead of recording all year."""
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
    """Otherwise it is a poller that is simply not running, with consent intact
    and nothing attached saying why -- the silent quiet week, arriving through
    the status endpoint."""
    monkeypatch.setattr(main.eeg_poller, "status", lambda _u: {"running": False})
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": state, "starts_on": "2026-09-01", "ends_on": "2027-06-30",
        "timezone": "UTC"})
    monkeypatch.setattr(main, "_consent", lambda _s: {
        "eeg_enabled": True, "retrieved": True})

    status = main._poller_status(STUDENT)

    assert status["stopped_reason"] == expected
    # And the dates ride along, so a surface can say *when* rather than only
    # that recording is off.
    assert status["window_starts_on"] == "2026-09-01"


def test_the_window_outranks_consent_in_the_status_too(monkeypatch):
    """Same precedence as the refusal messages. A withdrawal reported during a
    closed year sends a parent to a screen where nothing is wrong."""
    monkeypatch.setattr(main.eeg_poller, "status", lambda _u: {"running": False})
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": main.WINDOW_AFTER, "starts_on": "2026-09-01",
        "ends_on": "2027-06-30", "timezone": "UTC"})
    monkeypatch.setattr(main, "_consent", lambda _s: {
        "eeg_enabled": False, "retrieved": True, "eeg_revoked_at": "2026-10-01"})

    assert main._poller_status(STUDENT)["stopped_reason"] == "school_year_ended"


def test_every_window_state_has_a_meaning():
    """Three facts about a state -- whether it records, its sentence, its
    stop reason -- used to live in three structures kept in step by hand.
    They're one table now; this test checks that every declared WINDOW_
    constant has a row in it, so a new state can't deny correctly and then
    explain itself as nothing."""
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
    """`_school_timezone` should degrade to UTC rather than refuse -- a
    wrong day boundary is a smaller harm than a blank one. Its old fallback
    needed the `tzdata` package Windows doesn't ship, so without it the
    degrade path itself raised and took the whole request down. `tzdata`
    is a dependency now, but this is pinned here because CI runs on Linux,
    where the system database masks the regression."""
    def _no_tzdata(*_a, **_k):
        raise Exception("No time zone found with key UTC")

    monkeypatch.setattr(main, "ZoneInfo", _no_tzdata)
    main._retention_cache = None

    tz = main._school_timezone()

    assert tz is not None, "the fallback raised instead of degrading"
    # Usable as a tzinfo, not merely non-None: the callers pass it straight to
    # astimezone(), which is where a sentinel would fail instead.
    stamped = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc).astimezone(tz)
    assert stamped.utcoffset() == timedelta(0)


# ── the switch: a deployment that is not on a school year ───────────────────

def _unenforced(**over):
    """A row that says "not gating on a year" -- the prototyping shape,
    no dates. Its own helper rather than `_row(None, None)` plus a flag, so
    the tests below read as what the column is for."""
    row = {"id": True, "starts_on": None, "ends_on": None,
           "timezone": "UTC", "enforced": False}
    row.update(over)
    return row


def test_an_unenforced_window_records(monkeypatch):
    """The point of the column. Without it the only way to record was to invent
    a pair of term dates, which produces a row indistinguishable from a real
    school year on the one table whose job is to say when recording is allowed."""
    w = _window(monkeypatch, [_unenforced()])

    assert w["state"] == main.WINDOW_NOT_ENFORCED
    assert main._WINDOW_STATES[w["state"]].records is True


def test_not_enforced_is_distinguishable_from_inside_the_year(monkeypatch):
    """Both record, and they are different facts about a deployment. Collapsing
    them to WINDOW_OPEN would make "we never configured a term" unreadable from
    anywhere that reports why recording is or is not happening."""
    today = _today()
    inside = _window(monkeypatch, [_row(str(today - timedelta(days=1)),
                                        str(today + timedelta(days=1)))])
    off = _window(monkeypatch, [_unenforced()])

    assert inside["state"] != off["state"]


def test_enforcing_with_no_dates_denies_rather_than_recording_for_ever(monkeypatch):
    """A half-finished edit is not a licence. `enforced = true` with no
    dates means someone started configuring a year and didn't finish --
    reading that as unbounded would make leaving a form blank the most
    permissive state in the system."""
    w = _window(monkeypatch, [_row(None, None)])

    assert w["state"] == main.WINDOW_UNCONFIGURED
    assert w["state"] in main._WINDOW_DENIED


def test_a_row_without_the_column_still_enforces(monkeypatch):
    """Fails closed on the column's absence, checked with `is False` rather
    than falsiness. A row predating this column, or one PostgREST returns
    without it, must keep the gate on -- otherwise adding the column would
    silently open every unmigrated deployment."""
    today = _today()
    legacy = _row(str(today + timedelta(days=30)), str(today + timedelta(days=200)))
    assert "enforced" not in legacy

    assert _window(monkeypatch, [legacy])["state"] == main.WINDOW_BEFORE

    # And an explicit null is the same case, which is what a nullable column
    # would hand back if the default were ever dropped.
    assert _window(monkeypatch, [dict(legacy, enforced=None)])["state"] == main.WINDOW_BEFORE


def test_an_unenforced_window_still_needs_a_resolvable_timezone(monkeypatch):
    """The reporting surfaces bucket days in the school timezone whether or not
    the year is enforced, so a typo'd zone is still a refusal here -- turning
    enforcement off is about term dates, not about the rest of the row."""
    w = _window(monkeypatch, [_unenforced(timezone="Not/AZone")])

    assert w["state"] == main.WINDOW_UNREADABLE


def test_turning_enforcement_off_does_not_bypass_consent(monkeypatch):
    """Two independent gates. `_may_record` needs both, and the switch added
    here only speaks to the school year -- a student who consented to nothing
    still records nothing."""
    monkeypatch.undo()
    main._retention_cache_clear()
    monkeypatch.setattr(main, "supabase", _Window([_unenforced()]))
    monkeypatch.setattr(main, "_consent", lambda *_a, **_k: {
        "retrieved": True, "eeg_enabled": False,
        "headband_optical_enabled": False, "camera_enabled": False})

    verdict = main._may_record(STUDENT)

    assert not verdict.get("record_eeg")
