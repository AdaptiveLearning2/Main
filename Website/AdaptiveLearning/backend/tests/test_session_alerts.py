"""Operational alerts for teachers.

The scope is the point of the feature, so most of these tests are about what
the feed must *not* say. An alert here is a checkable fact about a session --
it timed out, or recording was expected and nothing arrived. It is never a
claim about the student, which is why `signal_fusion`'s "stressed" label is
deliberately not a producer.

The rest is the usual three-state discipline, which bites unusually hard here:
a failed read of "did any signal arrive" must not become an accusation that
recording is broken, and a student who declined the headband must not be
reported as a fault.
"""
import os
from datetime import datetime, timezone

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402
from tests.test_access_control import _FakeSupabase  # noqa: E402

TEACHER = {"id": "teacher-1"}
OTHER_TEACHER = {"id": "teacher-2"}
CLASS = "class-1"
ALICE, BOB = "student-1", "student-2"
SESSION = {"id": "sess-1", "started_at": "2026-06-11T09:00:00Z"}

NOW_UTC = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch):
    monkeypatch.setattr(main, "_utc_now", lambda: NOW_UTC)
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": main.WINDOW_OPEN, "starts_on": "2000-01-01",
        "ends_on": "2099-12-31", "timezone": "UTC"})


# ─── emission ─────────────────────────────────────────────────────────────

class _AlertSink:
    """Records what reached `session_alerts`, and what was asked about signals."""

    def __init__(self, has_signals=True, alerts_raise=False, signals_raise=False):
        self.has_signals = has_signals
        self.alerts_raise = alerts_raise
        self.signals_raise = signals_raise
        self.written = []
        self.tables = []

    def table(self, name):
        self.tables.append(name)
        sink, table = self, name

        class _Q:
            def select(self, *_a, **_k):  return self
            def eq(self, *_a):            return self
            def limit(self, *_a):         return self

            def upsert(self, rows, **kw):
                self._write = (rows, kw)
                return self

            def execute(self):
                write = getattr(self, "_write", None)
                if write is not None:
                    if sink.alerts_raise:
                        raise RuntimeError("session_alerts unavailable")
                    rows, kw = write
                    sink.written.extend(rows)
                    sink.conflict = kw
                    return type("R", (), {"data": []})()
                if table == "cognitive_signals":
                    if sink.signals_raise:
                        raise RuntimeError("cognitive_signals unavailable")
                    return type("R", (), {
                        "data": [{"id": 1}] if sink.has_signals else []})()
                return type("R", (), {"data": []})()

        return _Q()


def _emit(monkeypatch, *, closed_by=main.CLOSED_BY_SWEEP, record_eeg=True,
          answered=12, **sink_kw):
    sink = _AlertSink(**sink_kw)
    monkeypatch.setattr(main, "supabase", sink)
    monkeypatch.setattr(main, "_may_record", lambda _u: {"record_eeg": record_eeg})
    main._raise_session_alerts(ALICE, SESSION, closed_by, answered)
    return sink


def _kinds(sink):
    return sorted(a["kind"] for a in sink.written)


def test_a_session_the_student_ended_raises_no_timeout_alert(monkeypatch):
    """Finishing a lesson is the ordinary case and is not news."""
    sink = _emit(monkeypatch, closed_by=main.CLOSED_BY_STUDENT)
    assert main.ALERT_SESSION_AUTO_CLOSED not in _kinds(sink)


def test_a_session_the_sweep_ended_raises_one(monkeypatch):
    sink = _emit(monkeypatch, closed_by=main.CLOSED_BY_SWEEP)
    assert main.ALERT_SESSION_AUTO_CLOSED in _kinds(sink)
    alert = next(a for a in sink.written
                 if a["kind"] == main.ALERT_SESSION_AUTO_CLOSED)
    assert alert["detail"]["questions_answered"] == 12
    assert alert["user_id"] == ALICE and alert["session_id"] == "sess-1"


def test_the_default_close_reason_is_the_student(monkeypatch):
    """A new close site has to opt *in* to raising an alert. A wrongly-raised
    alert is worse than a missing one on a surface whose value is that every
    row means something happened."""
    import inspect
    sig = inspect.signature(main._close_session)
    assert sig.parameters["closed_by"].default == main.CLOSED_BY_STUDENT


def test_signals_missing_when_recording_was_expected_and_nothing_arrived(monkeypatch):
    sink = _emit(monkeypatch, record_eeg=True, has_signals=False)
    assert main.ALERT_SIGNALS_MISSING in _kinds(sink)


def test_no_alert_when_signals_arrived(monkeypatch):
    sink = _emit(monkeypatch, record_eeg=True, has_signals=True)
    assert main.ALERT_SIGNALS_MISSING not in _kinds(sink)


def test_a_student_who_declined_the_headband_is_not_a_fault(monkeypatch):
    """Working exactly as configured. Alerting on it would train a teacher to
    ignore the feed, and it would also leak a consent decision as an incident."""
    sink = _emit(monkeypatch, record_eeg=False, has_signals=False)
    assert main.ALERT_SIGNALS_MISSING not in _kinds(sink)


def test_the_consent_read_is_what_decides_not_the_raw_flags(monkeypatch):
    """`_may_record`, not `_consent` -- so a closed school year or a disabled
    recording flag is not reported as a broken headband. Nothing was supposed
    to arrive in either case."""
    sink = _AlertSink(has_signals=False)
    monkeypatch.setattr(main, "supabase", sink)
    seen = []
    monkeypatch.setattr(main, "_may_record",
                        lambda u: seen.append(u) or {"record_eeg": False})
    main._raise_session_alerts(ALICE, SESSION, main.CLOSED_BY_STUDENT, 5)
    assert seen == [ALICE]
    assert sink.written == []


def test_an_unreadable_signal_count_is_not_an_accusation(monkeypatch):
    """`None` means the count failed. Reporting that as "recording is broken"
    is the same error as reporting a failed read as a quiet week -- and this
    one sends a teacher to check hardware that is fine."""
    sink = _emit(monkeypatch, record_eeg=True, signals_raise=True)
    assert main.ALERT_SIGNALS_MISSING not in _kinds(sink)


def test_an_unreadable_consent_state_raises_nothing(monkeypatch):
    sink = _AlertSink(has_signals=False)
    monkeypatch.setattr(main, "supabase", sink)

    def _boom(_u):
        raise RuntimeError("consent unavailable")

    monkeypatch.setattr(main, "_may_record", _boom)
    main._raise_session_alerts(ALICE, SESSION, main.CLOSED_BY_STUDENT, 5)
    assert sink.written == []


# `_recording_was_expected` is its own function because a try/except cannot
# see either of these: both helpers fail closed by *returning*, not raising.
# Read as a plain bool, an outage is indistinguishable from a student who
# declined the headband -- and the outage is the likelier of the two.

def test_an_unreadable_consent_record_is_unknown_not_a_decline(monkeypatch):
    """`_consent()` catches its own read failure and returns a fail-closed
    dict, which `_may_record` spreads straight through. Nothing is thrown, so
    the surrounding try never fires and `record_eeg` is False for a reason
    that is not a decision anyone made."""
    monkeypatch.setattr(main, "_may_record", lambda _u: {
        "record_eeg": False, "retrieved": False, "window_state": main.WINDOW_OPEN})
    assert main._recording_was_expected(ALICE) is None


def test_an_unreadable_school_year_is_unknown_too(monkeypatch):
    """`WINDOW_UNREADABLE` denies, so a failed read of the retention window
    lands as `record_eeg: False` by the same route."""
    monkeypatch.setattr(main, "_may_record", lambda _u: {
        "record_eeg": False, "retrieved": True,
        "window_state": main.WINDOW_UNREADABLE})
    assert main._recording_was_expected(ALICE) is None


@pytest.mark.parametrize("window_state", [
    main.WINDOW_AFTER, main.WINDOW_BEFORE, main.WINDOW_UNCONFIGURED])
def test_a_closed_year_is_a_real_no_not_an_unknown(monkeypatch, window_state):
    """The distinction the fix must not blur: these are all states where
    nothing was *supposed* to arrive, so they stay False and stay silent."""
    monkeypatch.setattr(main, "_may_record", lambda _u: {
        "record_eeg": False, "retrieved": True, "window_state": window_state})
    assert main._recording_was_expected(ALICE) is False


def test_a_declined_channel_is_a_real_no(monkeypatch):
    monkeypatch.setattr(main, "_may_record", lambda _u: {
        "record_eeg": False, "retrieved": True, "window_state": main.WINDOW_OPEN})
    assert main._recording_was_expected(ALICE) is False


def test_a_payload_with_no_retrieved_flag_is_not_read_as_a_failure(monkeypatch):
    """`is False`, not falsiness -- an older payload has no opinion."""
    monkeypatch.setattr(main, "_may_record",
                        lambda _u: {"record_eeg": True, "window_state": main.WINDOW_OPEN})
    assert main._recording_was_expected(ALICE) is True


def test_an_unknown_recording_state_is_logged_not_just_withheld(monkeypatch, capsys):
    """The *outcome* is the same either way -- no alert -- which is why the
    log line is what this test asserts on.

    Withholding is correct: a teacher cannot act on a database blip, and an
    alert kind for it would be noise. What was wrong before is that the
    unknown was silently spelled as a decline, so a real recording outage
    during a consent-read failure left no trace anywhere. Assert on the
    outcome alone and this test passes against the bug it exists to catch.
    """
    sink = _AlertSink(has_signals=False)
    monkeypatch.setattr(main, "supabase", sink)
    monkeypatch.setattr(main, "_may_record", lambda _u: {
        "record_eeg": False, "retrieved": False, "window_state": main.WINDOW_OPEN})
    main._raise_session_alerts(ALICE, SESSION, main.CLOSED_BY_STUDENT, 5)

    assert sink.written == []
    # And it did not go and count signals for a session it cannot judge.
    assert "cognitive_signals" not in sink.tables
    out = capsys.readouterr().out
    assert "cannot tell whether recording was expected" in out
    assert main.ALERT_SIGNALS_MISSING in out


def test_a_real_decline_is_not_logged_as_an_unknown(monkeypatch, capsys):
    """The other half: an ordinary declined channel must stay quiet, or the
    log fills with one line per closed session and stops being read."""
    sink = _AlertSink(has_signals=False)
    monkeypatch.setattr(main, "supabase", sink)
    monkeypatch.setattr(main, "_may_record", lambda _u: {
        "record_eeg": False, "retrieved": True, "window_state": main.WINDOW_OPEN})
    main._raise_session_alerts(ALICE, SESSION, main.CLOSED_BY_STUDENT, 5)
    assert "cannot tell whether recording was expected" not in capsys.readouterr().out


def test_the_real_consent_helper_fails_closed_without_raising(monkeypatch):
    """The premise of all of the above, asserted against the real `_consent`
    rather than assumed. If it ever starts raising, the tests above go on
    passing while describing a function that no longer behaves that way."""
    monkeypatch.setattr(main, "supabase",
                        _FakeSupabase({}, table_raises=["signal_consent"]))
    out = main._consent(ALICE)
    assert out["retrieved"] is False
    assert out.get("eeg_enabled") is False


def test_both_kinds_can_fire_for_one_session(monkeypatch):
    sink = _emit(monkeypatch, closed_by=main.CLOSED_BY_SWEEP,
                 record_eeg=True, has_signals=False)
    assert _kinds(sink) == [main.ALERT_SESSION_AUTO_CLOSED,
                            main.ALERT_SIGNALS_MISSING]


def test_the_write_is_deduped_on_session_and_kind(monkeypatch):
    """A replayed close must not double-report. `_claim_session_close` is the
    primary guard; this is the backstop that survives a second emitter."""
    sink = _emit(monkeypatch, closed_by=main.CLOSED_BY_SWEEP)
    assert sink.conflict.get("on_conflict") == "session_id,kind"
    assert sink.conflict.get("ignore_duplicates") is True


def test_a_failed_alert_write_never_breaks_the_close(monkeypatch):
    """It runs after the credit and the rollup. A session's record must not be
    lost because a notification could not be filed."""
    _emit(monkeypatch, closed_by=main.CLOSED_BY_SWEEP, alerts_raise=True)


def test_a_session_with_no_id_is_left_alone(monkeypatch):
    sink = _AlertSink()
    monkeypatch.setattr(main, "supabase", sink)
    monkeypatch.setattr(main, "_may_record", lambda _u: {"record_eeg": True})
    main._raise_session_alerts(ALICE, {}, main.CLOSED_BY_SWEEP, 3)
    assert sink.written == [] and sink.tables == []


# ─── wiring into the close sequence ───────────────────────────────────────

def test_alerts_are_raised_after_the_discard_not_before(monkeypatch):
    """An alert about a session that is about to be deleted would be removed
    by the cascade a moment later, and an empty session is not an operational
    fault worth anyone's attention."""
    calls = []
    monkeypatch.setattr(main, "_claim_session_close", lambda *_a: True)
    monkeypatch.setattr(main, "_answer_counts", lambda *_a: (0, 0, True))
    monkeypatch.setattr(main, "_discard_if_nothing_recorded",
                        lambda *_a, **_k: True)
    monkeypatch.setattr(main, "_raise_session_alerts",
                        lambda *a: calls.append(a))

    out = main._close_session(ALICE, SESSION, "2026-06-11T10:00:00Z",
                              closed_by=main.CLOSED_BY_SWEEP)
    assert out == {"discarded": True}
    assert calls == [], "an alert was filed for a session that was discarded"


def test_the_close_site_reason_reaches_the_emitter(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_claim_session_close", lambda *_a: True)
    monkeypatch.setattr(main, "_answer_counts", lambda *_a: (7, 4, True))
    monkeypatch.setattr(main, "_discard_if_nothing_recorded",
                        lambda *_a, **_k: False)
    monkeypatch.setattr(main, "_credit_session_to_user_stats", lambda *_a: None)
    monkeypatch.setattr(main, "_rollup_session_days", lambda *_a: None)
    monkeypatch.setattr(main.chart_archive, "schedule", lambda *_a: None)
    monkeypatch.setattr(main, "_raise_session_alerts",
                        lambda *a: calls.append(a))

    main._close_session(ALICE, SESSION, "2026-06-11T10:00:00Z",
                        closed_by=main.CLOSED_BY_SWEEP)
    assert calls == [(ALICE, SESSION, main.CLOSED_BY_SWEEP, 7)]


# Close sites where the *student* ended the session. Everything else is a
# sweep closing a session they walked away from. Listed by name rather than
# detected, because no property of the source separates them -- `/end` and the
# stale sweeps all stop the poller and call the same helper -- and the whole
# content of `session_auto_closed` is which of the two happened.
STUDENT_DRIVEN_CLOSERS = {"end_session"}


def test_every_close_site_says_who_ended_the_session():
    """`closed_by` defaults to the student, so a sweep that forgets to pass it
    raises no alert and nothing anywhere says so.

    A partition rather than a search: every close site must be either a named
    student-driven one or a sweep that says so, which means a new site fails
    this test until someone classifies it. Same shape as `_MODE_AWARE` listing
    its endpoints -- the enumeration is the point.
    """
    from conftest import close_sites

    closers = close_sites()
    assert len(closers) >= 3, "close sites vanished; this test is now vacuous"

    for name, src in closers:
        if name in STUDENT_DRIVEN_CLOSERS:
            assert "CLOSED_BY_SWEEP" not in src, (
                f"{name} is listed as student-driven but marks its close as a "
                "sweep, so finishing a lesson would raise a timeout alert")
        else:
            assert "CLOSED_BY_SWEEP" in src, (
                f"{name} ends a session the student did not end, but does not "
                "say so -- `closed_by` defaults to the student, so no timeout "
                "alert is raised and the feed is quietly incomplete. Add it to "
                "STUDENT_DRIVEN_CLOSERS if that is wrong.")


def test_the_student_driven_list_still_names_real_functions():
    """A renamed endpoint would otherwise leave a dead name in the set above
    and silently move that site into the sweep branch."""
    import inspect
    for name in STUDENT_DRIVEN_CLOSERS:
        assert inspect.isfunction(getattr(main, name, None)), (
            f"{name} is no longer a function in main -- the exemption above is "
            "now dead and the test that reads it is weaker than it looks")


# ─── the read surface ─────────────────────────────────────────────────────

def _alert(user, kind, created_at, session="sess-1"):
    return {"id": f"{user}-{kind}", "user_id": user, "session_id": session,
            "kind": kind, "detail": {}, "created_at": created_at}


def _tables(alerts=(), roster=(ALICE, BOB), owner="teacher-1"):
    return {
        "classes": [{"id": CLASS, "teacher_id": owner}],
        "class_memberships": [{"class_id": CLASS, "student_id": s,
                               "joined_at": "2026-01-01T00:00:00Z"} for s in roster],
        "profiles": [{"id": ALICE, "display_name": "Alice", "role": "student"},
                     {"id": BOB, "display_name": "Bob", "role": "student"}],
        "session_alerts": list(alerts),
    }


@pytest.fixture(autouse=True)
def _teacher_owns_the_class(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: TEACHER)


def test_a_non_owning_teacher_is_refused(monkeypatch):
    """Narrower than `_verify_can_view_student` on purpose: these are
    classroom-operations facts for the person who can walk over and fix it."""
    monkeypatch.setattr(main, "get_user", lambda _r: OTHER_TEACHER)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables()))
    with pytest.raises(main.HTTPException) as exc:
        main.class_alerts(CLASS, None)
    assert exc.value.status_code == 403


def test_the_refusal_happens_before_the_feed_is_read(monkeypatch):
    fake = _FakeSupabase(_tables())
    monkeypatch.setattr(main, "get_user", lambda _r: OTHER_TEACHER)
    monkeypatch.setattr(main, "supabase", fake)
    with pytest.raises(main.HTTPException):
        main.class_alerts(CLASS, None)
    assert "classes" in fake.table_calls
    assert "session_alerts" not in fake.table_calls


def test_alerts_carry_the_student_name_and_school_day(monkeypatch):
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables(alerts=[
        _alert(ALICE, main.ALERT_SESSION_AUTO_CLOSED, "2026-06-11T09:30:00Z")])))
    out = main.class_alerts(CLASS, None)
    assert out["retrieved"] is True
    assert out["alerts"][0]["student_name"] == "Alice"
    assert out["alerts"][0]["school_day"] == "2026-06-11"


def test_the_day_is_the_schools_not_the_viewers(monkeypatch):
    """A late Californian lesson belongs to the day it was taught on. Against
    a UTC clock a 5pm session lands on the following day."""
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": main.WINDOW_OPEN, "starts_on": "2000-01-01",
        "ends_on": "2099-12-31", "timezone": "America/Los_Angeles"})
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables(alerts=[
        _alert(ALICE, main.ALERT_SESSION_AUTO_CLOSED, "2026-06-12T01:00:00Z")])))
    out = main.class_alerts(CLASS, None)
    assert out["alerts"][0]["school_day"] == "2026-06-11"


def test_a_class_with_no_alerts_is_not_a_failed_read(monkeypatch):
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables()))
    out = main.class_alerts(CLASS, None)
    assert out["alerts"] == [] and out["retrieved"] is True


def test_a_failed_feed_read_is_not_a_quiet_week(monkeypatch):
    monkeypatch.setattr(main, "supabase",
                        _FakeSupabase(_tables(), table_raises=["session_alerts"]))
    out = main.class_alerts(CLASS, None)
    assert out["retrieved"] is False and out["alerts"] == []


def test_a_failed_roster_read_is_not_a_quiet_week_either(monkeypatch):
    monkeypatch.setattr(main, "supabase",
                        _FakeSupabase(_tables(), table_raises=["class_memberships"]))
    out = main.class_alerts(CLASS, None)
    assert out["retrieved"] is False


def test_a_class_with_no_students_reads_nothing(monkeypatch):
    fake = _FakeSupabase(_tables(roster=()))
    monkeypatch.setattr(main, "supabase", fake)
    out = main.class_alerts(CLASS, None)
    assert out["retrieved"] is True and out["student_count"] == 0
    assert "session_alerts" not in fake.table_calls


def test_truncation_is_disclosed_rather_than_inferred(monkeypatch):
    """Silent truncation reads as "that is all of them"."""
    many = [_alert(ALICE, main.ALERT_SESSION_AUTO_CLOSED,
                   f"2026-06-11T09:{i:02d}:00Z", session=f"s-{i}")
            for i in range(main._ALERT_FEED_CAP + 10)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables(alerts=many)))
    out = main.class_alerts(CLASS, None)
    assert len(out["alerts"]) == main._ALERT_FEED_CAP
    assert out["truncated"] is True


def test_a_short_feed_is_not_marked_truncated(monkeypatch):
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables(alerts=[
        _alert(ALICE, main.ALERT_SIGNALS_MISSING, "2026-06-11T09:30:00Z")])))
    assert main.class_alerts(CLASS, None)["truncated"] is False


def test_the_window_is_bounded(monkeypatch):
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables()))
    assert main.class_alerts(CLASS, None, days=10_000)["days"] \
        == main._CLASS_TREND_MAX_DAYS


def test_the_feed_is_scoped_to_the_roster_and_the_window(monkeypatch):
    """Assert on the filters, not the payload. An empty result cannot tell
    "asked and found nothing" from "asked for the wrong thing"."""
    fake = _FakeSupabase(_tables())
    monkeypatch.setattr(main, "supabase", fake)
    main.class_alerts(CLASS, None, days=7)
    feed = next(q for q in fake.queries
                if any(c == "user_id" for c, _ in q.filters))
    cols = [c for c, _ in feed.filters]
    assert "user_id" in cols and "created_at" in cols
