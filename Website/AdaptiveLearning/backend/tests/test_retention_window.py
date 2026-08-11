"""The school-year retention window: what it denies, and what it must not.

Recording is gated on the window *and* consent. The two are separate questions
and both fail closed, so the tests that matter are the ones proving they stay
separate -- a window that also suppressed reading would take a parent's history
away on the last day of term, months before the delete job is meant to remove
anything.

Every test here overrides conftest's `_school_year_is_open`, which is the point
of that fixture having an escape hatch: a default nothing can turn off is a rule
nothing tests.
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
    """Today **in UTC**, because these rows are configured with `timezone: UTC`.

    Not `date.today()`, which is the machine's local date. The two differ for
    part of every day on any machine that is not on UTC, so a test built on the
    local date passes or fails depending on what time the suite runs -- which is
    how the first version of `test_the_first_and_last_days...` failed in the
    evening and would have passed in the morning.
    """
    return datetime.now(timezone.utc).date()


def _row(starts, ends, tz="UTC"):
    return {"id": True, "starts_on": starts, "ends_on": ends, "timezone": tz}


def _window(monkeypatch, rows, raises=False):
    """Install a real `_retention_window` over conftest's open-year default.

    Clears the TTL cache first: successful reads are reused for
    `_RETENTION_TTL_SECONDS`, so without this every test after the first would
    be asserting against the previous test's row.
    """
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
    """Both bounds inclusive.

    The last day matters most: the delete job runs *on* `ends_on`, and if that
    date did not record, the job's own day would be the one day of the year
    with no data -- which reads as an outage rather than as a boundary.
    """
    today = str(_today())
    assert _window(monkeypatch, [_row(today, "2099-12-31")])["state"] == main.WINDOW_OPEN
    assert _window(monkeypatch, [_row("2000-01-01", today)])["state"] == main.WINDOW_OPEN


def test_before_the_year_starts_is_not_the_same_as_after_it_ends(monkeypatch):
    """Two states, not one 'closed'.

    "Recording has not started yet" and "the year is over" lead a parent to
    different actions, and collapsing them into a single off state is the same
    failure as a tile that cannot tell no-data from zero.
    """
    today = _today()
    before = _window(monkeypatch, [_row(str(today + timedelta(days=10)),
                                        str(today + timedelta(days=20)))])
    after = _window(monkeypatch, [_row(str(today - timedelta(days=20)),
                                       str(today - timedelta(days=10)))])
    assert before["state"] == main.WINDOW_BEFORE
    assert after["state"] == main.WINDOW_AFTER
    assert before["state"] != after["state"]


def test_an_unconfigured_window_records_nothing(monkeypatch):
    """No row is not an open-ended licence.

    The same default as consent: nothing recorded until someone says so. A
    school that has not configured its year has a visible, fixable state rather
    than a silent assumption that recording is fine.
    """
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
    """"The last day of school" ends at local midnight, not UTC midnight.

    Constructed so UTC and the school disagree about the date: at 03:00 UTC it
    is still the previous day in Los Angeles (UTC-7/8). With `ends_on` set to
    that previous day, a UTC comparison says the year is over while the school
    is still in its final day.
    """
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
    """The one that would be a data-loss bug rather than a recording one.

    `_reportable_channels` reads `_consent`, not `_may_record`. If the window
    gated it, every channel would report as off the day the school year ended
    and a parent's history would vanish months before the delete job is meant
    to remove anything -- and it would look like a withdrawal, which is a claim
    about a decision nobody made.
    """
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
# The negative above proves reading is *not* gated. This is the other half, and
# it exists because the first version of this file had only the negative: it
# would have passed with the window wired into nothing at all.
#
# By source rather than by behaviour, deliberately. Driving each of these to a
# refusal needs six different fixtures -- a poller, a session owner, a rate
# limiter, a live sidecar -- and the thing worth pinning is not that they refuse
# today but that none of them reads consent *without* the window. That is a
# property of the call, so the call is what is inspected. Same argument as
# `_MODE_AWARE` in test_ingest_mode.py, which lists the endpoints that must know
# the ingest mode after five review rounds found them one at a time.

# The endpoints are **discovered, not listed**: any function in `main` whose
# body inserts into one of the three signal tables is a recording site by
# definition, and has to be gated. A hand-kept allowlist is the shape
# `_MODE_AWARE` has, and it is only there because nothing can derive the set of
# endpoints that "should know about the ingest mode" -- that is a judgement.
# "Writes signal rows" is not a judgement, so it should not be maintained by
# hand, and a seventh endpoint added next year is caught without anyone
# remembering this file exists.
_SIGNAL_TABLES = ("cognitive_signals", "face_signals", "heart_signals")

# The two that gate without inserting: they are the poller's permission checks,
# and the poller does the writing. Nothing in their source can reveal them, so
# these stay explicit -- a short list of exceptions beside a derived rule, which
# is a different thing from a list standing in for the rule.
_GATING_CALLBACKS = ("_poller_may_record_eeg", "_heart_consent_for_poller")


def _writes_signal_rows(source: str) -> bool:
    """Whether a function inserts into a signal table.

    Deliberately crude -- a table name and a write call in the same body. It
    over-matches rather than under-matches, and the failure of over-matching is
    a test telling someone to gate a function that did not need it, which is a
    conversation. Under-matching is a recording site nobody notices.

    `upsert` is in the list because leaving it out missed `ingest_heart` on the
    first run: heart rows dedupe on `heart_session_source_ts_key`, so that
    endpoint upserts where the other two insert. That is precisely the
    under-match this cannot afford, and it is why the self-check below exists.
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
    """The derived list is only worth anything if it actually finds things.

    Without this, a typo in `_writes_signal_rows` yields an empty set and every
    parametrised test below silently passes by having nothing to check -- the
    exact way an exhaustiveness test stops being one.
    """
    sites = _recording_sites()
    for known in ("ingest_cognitive", "ingest_face", "ingest_heart"):
        assert known in sites, (known, sites)


@pytest.mark.parametrize("name", _recording_sites() + list(_GATING_CALLBACKS)
                         + ["eeg_start"])
def test_every_recording_site_gates_on_the_window(name):
    """A site that calls `_consent` directly records all summer.

    Consent is necessary and not sufficient: `_may_record` is the conjunction,
    and reading the raw flags skips the school year silently -- no error, no
    empty result, just rows written outside the window that the delete job will
    then be asked to reason about.
    """
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

    `_permitted_heart_sources` reads `record_*`, so handing it a bare `_consent`
    result yields no sources rather than every consented one -- a caller that
    forgets the window records nothing instead of recording all year.
    """
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
