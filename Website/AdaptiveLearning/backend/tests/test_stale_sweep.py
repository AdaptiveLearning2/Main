"""The loop around the abandoned-session sweep, and `student_sessions`' idle/activity flags."""
import os
import time

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import main  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def catch_ups(monkeypatch):
    """Records the loop's chart catch-up rather than letting it query a real project."""
    calls = []
    monkeypatch.setattr(main.chart_archive, "archive_missing",
                        lambda client, **k: calls.append(client) or {})
    return calls


class _FakeDB:
    """The sessions read, plus `last_activity_for_sessions` over answers and signal rows.

    Signal rows stand for ones that passed the measured-row filter, which is SQL's to apply:
    see `test_the_activity_function_counts_only_measured_rows` and `assert_signal_rls.sql`.
    """

    def __init__(self, sessions, answers, boom=False, signals=None):
        self._sessions, self._answers, self._boom = sessions, answers, boom
        self._signals = signals or {}
        self.activity_asked = []

    def table(self, name):
        assert name == "sessions", f"activity is read through the function, not {name}"
        return self

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def execute(self):
        return type("R", (), {"data": self._sessions})

    def rpc(self, name, params):
        assert name == "last_activity_for_sessions", name
        self.activity_asked.append(list(params["p_session_ids"]))
        fake = self

        class _R:
            def execute(self):
                if fake._boom:
                    raise RuntimeError("activity unavailable")
                stamps = [(a["session_id"], a["answered_at"]) for a in fake._answers or []]
                stamps += [(r["session_id"], r["ts"])
                           for rows in fake._signals.values() for r in rows]
                data = []
                for sid in params["p_session_ids"]:
                    mine = [t for s, t in stamps if s == sid]
                    data.append({"session_id": sid,
                                 "last_activity_at": max(mine, key=main._parse_ts) if mine else None})
                return type("R", (), {"data": data})

        return _R()


@pytest.fixture(autouse=True)
def _no_first_delay(monkeypatch):
    """The loop tests below drive one pass synchronously; the startup delay has its own test."""
    monkeypatch.setattr(main, "_STALE_SWEEP_FIRST_DELAY_SEC", 0.0)


def test_the_startup_delay_defaults_off_outside_production(monkeypatch):
    """`--reload` restarts within any delay, so a dev process would never sweep."""
    monkeypatch.delenv("STALE_SWEEP_FIRST_DELAY_SECONDS", raising=False)
    assert main._first_sweep_delay(production=False) == 0.0
    assert main._first_sweep_delay(production=True) == 60.0
    monkeypatch.setenv("STALE_SWEEP_FIRST_DELAY_SECONDS", "5")
    assert main._first_sweep_delay(production=False) == main._first_sweep_delay(production=True) == 5.0


def test_the_first_pass_waits_out_the_startup_delay_and_a_stop_ends_it(monkeypatch):
    """A cold start serves its first requests before the sweep competes for a 0.1-CPU host."""
    swept, waits = [], []

    class _StopDuringDelay:
        def wait(self, timeout):
            waits.append(timeout)
            return True                    # stopped while waiting

        def is_set(self):
            return True

    monkeypatch.setattr(main, "_STALE_SWEEP_FIRST_DELAY_SEC", 60.0)
    monkeypatch.setattr(main, "_stale_sweep_stop", _StopDuringDelay())
    monkeypatch.setattr(main, "_sweep_abandoned_sessions", lambda *a, **k: swept.append(1) or {})
    main._stale_sweep_loop()
    assert waits == [60.0], "the first pass did not wait on the startup delay"
    assert swept == [], "a stop during the delay still swept"


def test_the_first_sweep_does_not_wait_a_whole_interval(monkeypatch):
    """A process that doesn't outlive one interval (e.g. `uvicorn --reload`) would never sweep.

    Measured on a local stack: 123 sessions open, every one past the 6h threshold.
    """
    swept = []
    monkeypatch.setattr(main, "_sweep_abandoned_sessions",
                        lambda *a, **k: swept.append(1) or {})
    # A long interval, so anything that runs must have run *before* the wait.
    monkeypatch.setattr(main, "_STALE_SWEEP_INTERVAL_SEC", 3600.0)
    main._stale_sweep_stop.set()          # stop after the first pass
    try:
        main._stale_sweep_loop()
    finally:
        main._stale_sweep_stop.clear()
    assert swept == [1], "the sweep must run once before waiting on the interval"


def test_a_stop_during_the_first_sweep_is_not_made_to_wait(monkeypatch):
    """This thread is joined on shutdown, so waiting out an interval would be a hang."""
    monkeypatch.setattr(main, "_STALE_SWEEP_INTERVAL_SEC", 3600.0)

    def _sweep_then_stop(*a, **k):
        main._stale_sweep_stop.set()
        return {}

    monkeypatch.setattr(main, "_sweep_abandoned_sessions", _sweep_then_stop)
    started = time.monotonic()
    try:
        main._stale_sweep_loop()
    finally:
        main._stale_sweep_stop.clear()
    assert time.monotonic() - started < 30, "it waited on the interval anyway"


def test_the_chart_catch_up_runs_every_pass_even_when_the_sweep_fails(monkeypatch, catch_ups):
    """Only a close schedules an archive; this pass is what retries a cancelled or failed one."""
    def boom(*a, **k):
        raise RuntimeError("sessions unavailable")

    monkeypatch.setattr(main, "_sweep_abandoned_sessions", boom)
    monkeypatch.setattr(main, "_STALE_SWEEP_INTERVAL_SEC", 3600.0)
    main._stale_sweep_stop.set()
    try:
        main._stale_sweep_loop()
    finally:
        main._stale_sweep_stop.clear()
    assert catch_ups == [main.supabase]


def _session(sid, *, ended=None, started_min_ago=1):
    from datetime import timedelta
    return {"id": sid, "user_id": "u1", "ended_at": ended,
            "started_at": (main._utc_now()
                           - timedelta(minutes=started_min_ago)).isoformat()}


def test_a_quiet_open_session_is_reported_idle_not_live(monkeypatch):
    """`abandoned` is an age (6h); `idle` is real quiet, on `class_live`'s window."""
    from datetime import timedelta
    old = (main._utc_now() - timedelta(seconds=main._STALE_AFTER_SEC + 120)).isoformat()

    rows = [_session("s-quiet", started_min_ago=30)]
    answers = [{"session_id": "s-quiet", "answered_at": old}]
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *a, **k: None)
    monkeypatch.setattr(main, "get_user", lambda r: {"id": "t1"})
    monkeypatch.setattr(main, "supabase", _FakeDB(rows, answers))

    out = main.student_sessions("u1", request=None)
    assert out[0]["idle"] is True
    assert out[0]["abandoned"] is False, "30 minutes is nowhere near the 6h age"
    assert out[0]["activity_known"] is True


def test_a_session_answered_just_now_is_still_live(monkeypatch):
    rows = [_session("s-busy", started_min_ago=30)]
    answers = [{"session_id": "s-busy", "answered_at": main._utc_now().isoformat()}]
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *a, **k: None)
    monkeypatch.setattr(main, "get_user", lambda r: {"id": "t1"})
    monkeypatch.setattr(main, "supabase", _FakeDB(rows, answers))

    assert main.student_sessions("u1", request=None)[0]["idle"] is False


def test_a_failed_activity_read_never_claims_idle(monkeypatch):
    """The client gates on `activity_known`, so a blip can't relabel a live session quiet."""
    rows = [_session("s-unknown", started_min_ago=30)]
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *a, **k: None)
    monkeypatch.setattr(main, "get_user", lambda r: {"id": "t1"})
    monkeypatch.setattr(main, "supabase", _FakeDB(rows, answers=None, boom=True))

    out = main.student_sessions("u1", request=None)
    assert out[0]["activity_known"] is False
    assert out[0]["idle"] is False, "unknown is not idle"


def _as_teacher(monkeypatch, db):
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *a, **k: None)
    monkeypatch.setattr(main, "get_user", lambda r: {"id": "t1"})
    monkeypatch.setattr(main, "supabase", db)


def test_a_session_streaming_signals_is_not_idle(monkeypatch):
    """Same inputs as `class_live` (signals and answers), not just the same window."""
    from datetime import timedelta
    old = (main._utc_now() - timedelta(seconds=main._STALE_AFTER_SEC + 120)).isoformat()
    rows = [_session("s-eeg", started_min_ago=30)]
    answers = [{"session_id": "s-eeg", "answered_at": old}]
    signals = {"cognitive_signals": [
        {"session_id": "s-eeg", "ts": main._utc_now().isoformat()}]}
    _as_teacher(monkeypatch, _FakeDB(rows, answers, signals=signals))

    out = main.student_sessions("u1", request=None)
    assert out[0]["idle"] is False, "the headband has been streaming throughout"
    assert out[0]["last_activity_at"] == signals["cognitive_signals"][0]["ts"]


def test_the_newest_source_wins_whichever_table_it_came_from(monkeypatch):
    """Negative control: with every source stale, the session really is quiet."""
    from datetime import timedelta
    old = (main._utc_now() - timedelta(seconds=main._STALE_AFTER_SEC + 120)).isoformat()
    rows = [_session("s-quiet-all", started_min_ago=30)]
    answers = [{"session_id": "s-quiet-all", "answered_at": old}]
    signals = {"heart_signals": [{"session_id": "s-quiet-all", "ts": old}]}
    _as_teacher(monkeypatch, _FakeDB(rows, answers, signals=signals))

    assert main.student_sessions("u1", request=None)[0]["idle"] is True


def test_activity_is_asked_for_the_open_sessions_only(monkeypatch):
    """One read for every open session; a closed one is not idle, so it needs none."""
    rows = [_session("s-open", started_min_ago=30),
            _session("s-done", ended=main._utc_now().isoformat())]
    db = _FakeDB(rows, answers=[])
    _as_teacher(monkeypatch, db)
    main.student_sessions("u1", request=None)
    assert db.activity_asked == [["s-open"]]


def test_the_activity_function_counts_only_measured_rows():
    """Source check, stated: the SQL copies `_ACTIVITY_SOURCES`. A filter dropped there puts a
    headband left on a desk (`contact_poor` rows, measurements nulled) back to "live"."""
    import re
    from pathlib import Path
    migrations = sorted((Path(__file__).resolve().parents[4] / "supabase" / "migrations").glob("*.sql"))
    header = re.compile(r'CREATE\s+OR\s+REPLACE\s+FUNCTION\s+"public"\."last_activity_for_sessions"')
    sql = re.sub(r"--[^\n]*", "", [m for m in migrations if header.search(m.read_text("utf-8"))][-1]
                 .read_text("utf-8"))
    body = sql[header.search(sql).start():]
    body = body[:body.index("$$;", body.index("$$") + 2)]
    for table, _column, measured in main._ACTIVITY_SOURCES:
        # This table's subquery, up to its `ON true`.
        found = re.search(r'FROM\s+"public"\."' + table + r'"\s+x(.*?)ON\s+true', body, re.S)
        assert found, f"last_activity_for_sessions does not read {table}"
        where = found.group(1)
        for col in measured:
            assert re.search(rf"x\.{col}\s+IS\s+NOT\s+NULL", where), \
                f"{table}.{col} counts as activity in main but not in the function"
        if not measured:
            assert "IS NOT NULL" not in where, "an answer is activity whatever the sensors did"


def test_a_measured_signal_row_still_counts(monkeypatch):
    """Filtering every signal row out would also pass the desk test above."""
    from datetime import timedelta
    old = (main._utc_now() - timedelta(seconds=main._STALE_AFTER_SEC + 120)).isoformat()
    rows = [_session("s-worn", started_min_ago=30)]
    answers = [{"session_id": "s-worn", "answered_at": old}]
    # The fake applies no filter, so this stands for a row that passed it.
    signals = {"cognitive_signals": [
        {"session_id": "s-worn", "ts": main._utc_now().isoformat()}]}
    _as_teacher(monkeypatch, _FakeDB(rows, answers, signals=signals))

    assert main.student_sessions("u1", request=None)[0]["idle"] is False


def test_nothing_to_look_up_is_not_a_failed_read(monkeypatch):
    """`activity_known: False` means only "the read failed", not "no open sessions"."""
    rows = [_session("s-done", ended=main._utc_now().isoformat())]
    _as_teacher(monkeypatch, _FakeDB(rows, answers=[]))

    out = main.student_sessions("u1", request=None)
    assert out[0]["activity_known"] is True
    assert out[0]["idle"] is False


# ── the live monitor closes only a session whose sensor went quiet ──────────

def _live(monkeypatch, latest, stamps=None, measured=None):
    """`class_live` for one student whose open session started 15 minutes ago.

    `stamps` collects each close's `ended_at`; `measured(started)` is what `last_activity_for_sessions` answers.
    """
    from datetime import datetime, timedelta
    started = (datetime.utcnow() - timedelta(seconds=900)).isoformat()
    session = {"id": "sess-1", "user_id": "stu-1", "started_at": started}
    closed = []
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "teacher-1"})
    monkeypatch.setattr(main, "_verify_class_owner", lambda *_a: None)
    monkeypatch.setattr(main, "_profiles_many", lambda ids: {})
    monkeypatch.setattr(main, "_open_sessions_many", lambda ids: {"stu-1": [session]})
    monkeypatch.setattr(main, "_latest_signals_many", lambda ids: {"sess-1": latest(started)})
    monkeypatch.setattr(main, "_close_session", lambda *a, **k: (
        closed.append(a[1]["id"]), stamps is not None and stamps.append(a[2])))
    monkeypatch.setattr(main.eeg_poller, "stop", lambda *a, **k: None)

    class _Members:
        def table(self, name):
            q = type("Q", (), {})()
            q.select = q.eq = lambda *a, **k: q
            q.execute = lambda: type("R", (), {"data": [{"student_id": "stu-1"}]})()
            return q

        def rpc(self, name, params):
            assert name == "last_activity_for_sessions" and params["p_session_ids"] == ["sess-1"]
            at = measured(started) if measured else None
            return type("Q", (), {"execute": lambda _s: type("R", (), {
                "data": [{"session_id": "sess-1", "last_activity_at": at}]})()})()
    monkeypatch.setattr(main, "supabase", _Members())
    return main.class_live("class-1", None), closed


def test_a_sensorless_student_on_one_question_is_not_closed(monkeypatch):
    """No headband, no camera: fifteen minutes on a question sends the server nothing."""
    out, closed = _live(monkeypatch, lambda started: {})
    assert closed == []
    assert out[0]["active_session"] is None       # not live, but left open for the sweep


def test_a_sensor_that_went_quiet_still_closes(monkeypatch):
    out, closed = _live(monkeypatch, lambda started: {"cognitive": {"ts": started, "focus": 0.5}})
    assert closed == ["sess-1"]


def test_a_quiet_session_closes_at_its_last_reading_not_now(monkeypatch):
    """Last seen as the sweep defines it: a newer reading with nothing in it (headband off the head) does not count."""
    from datetime import datetime, timedelta
    stamps, seen = [], {}

    def latest(started):
        empty = (datetime.fromisoformat(started) + timedelta(seconds=60)).isoformat()
        return {"cognitive": {"ts": empty, "focus": None}}

    def measured(started):
        seen["ts"] = started + "+00:00"
        return seen["ts"]

    _, closed = _live(monkeypatch, latest, stamps=stamps, measured=measured)
    assert closed == ["sess-1"]
    assert [main._parse_ts(s) for s in stamps] == [main._parse_ts(seen["ts"])]


# ── the sweep stamps the last activity, not the sweep time ──────────────────

class _SweepDB(_FakeDB):
    def is_(self, *a, **k): return self
    def lt(self, *a, **k): return self


def _sweep(monkeypatch, db):
    stamps = {}
    monkeypatch.setattr(main, "supabase", db)
    monkeypatch.setattr(main.eeg_poller, "stop", lambda *a, **k: None)
    monkeypatch.setattr(main, "_close_session",
                        lambda uid, s, ended, **k: stamps.update({s["id"]: ended}) or {"discarded": False})
    before = main._utc_now()
    out = main._sweep_abandoned_sessions()
    return out, stamps, before


def test_the_sweep_closes_a_session_when_it_was_last_active(monkeypatch):
    s = _session("s-old", started_min_ago=300)
    _, stamps, _ = _sweep(monkeypatch, _SweepDB(
        [s], [{"session_id": "s-old", "answered_at": "2026-01-01T10:05:00+00:00"},
              {"session_id": "s-old", "answered_at": "2026-01-01T10:02:00+00:00"}]))
    assert main._parse_ts(stamps["s-old"]) == main._parse_ts("2026-01-01T10:05:00+00:00")


def test_a_session_with_no_activity_closes_at_its_start(monkeypatch):
    s = _session("s-empty", started_min_ago=300)
    _, stamps, _ = _sweep(monkeypatch, _SweepDB([s], []))
    assert main._parse_ts(stamps["s-empty"]) == main._parse_ts(s["started_at"])


def test_a_failed_activity_read_closes_at_the_sweep_time(monkeypatch):
    """Reporting fails open: the session still closes, stamped as before."""
    s = _session("s-old", started_min_ago=300)
    out, stamps, before = _sweep(monkeypatch, _SweepDB([s], [], boom=True))
    assert out["closed"] == 1
    assert main._parse_ts(stamps["s-old"]) >= before


class _StartDB(_SweepDB):
    """`start_session`'s stray read, then the new session's insert."""

    def insert(self, obj, **_k):
        self._inserting = obj
        return self

    def execute(self):
        obj = getattr(self, "_inserting", None)
        if obj is not None:
            self._inserting = None
            return type("R", (), {"data": [{"id": "s-new", **obj}]})
        return super().execute()


def test_starting_a_session_closes_a_stray_when_it_was_last_active(monkeypatch):
    stray = _session("s-stray", started_min_ago=600)
    monkeypatch.setattr(main, "supabase", _StartDB(
        [stray], [{"session_id": "s-stray", "answered_at": "2026-01-01T09:30:00+00:00"}]))
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u1"})
    monkeypatch.setattr(main.eeg_poller, "stop", lambda *a, **k: None)
    stamps = {}
    monkeypatch.setattr(main, "_close_session",
                        lambda uid, s, ended, **k: stamps.update({s["id"]: ended}))
    monkeypatch.setattr(main, "_profile", lambda _u: {})
    monkeypatch.setattr(main, "_served_grade", lambda *a, **k: None)
    monkeypatch.setattr(main, "_ensure_queue", lambda *a, **k: None)

    main.start_session(main.StartSessionRequest(title=None), request=None)

    assert main._parse_ts(stamps["s-stray"]) == main._parse_ts("2026-01-01T09:30:00+00:00")


# ── a new session leaves a lesson still live in another tab ─────────────────

def _start(monkeypatch, strays, answers, boom=False):
    """`start_session` over these open sessions; (closed ids, poller-stopped ids)."""
    closed, stopped = [], []
    monkeypatch.setattr(main, "supabase", _StartDB(strays, answers, boom=boom))
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u1"})
    monkeypatch.setattr(main.eeg_poller, "stop", lambda sid, *a, **k: stopped.append(sid))
    monkeypatch.setattr(main, "_close_session", lambda uid, s, ended, **k: closed.append(s["id"]))
    monkeypatch.setattr(main, "_profile", lambda _u: {})
    monkeypatch.setattr(main, "_served_grade", lambda *a, **k: None)
    monkeypatch.setattr(main, "_ensure_queue", lambda *a, **k: None)
    main.start_session(main.StartSessionRequest(title=None), request=None)
    return closed, stopped


def _ago(minutes):
    from datetime import timedelta
    return (main._utc_now() - timedelta(minutes=minutes)).isoformat()


def test_a_lesson_answered_in_another_tab_just_now_is_left_open(monkeypatch):
    """Closed, its next answer would 409 and split it, and that tab's new session would close this one."""
    live, stale = _session("s-live", started_min_ago=30), _session("s-stale", started_min_ago=300)
    closed, stopped = _start(monkeypatch, [live, stale], [
        {"session_id": "s-live", "answered_at": _ago(1)},
        {"session_id": "s-stale", "answered_at": _ago(200)}])
    assert closed == ["s-stale"]
    assert stopped == ["s-stale"], "the live tab's poller was stopped"


def test_a_lesson_just_opened_with_nothing_answered_is_left_open(monkeypatch):
    fresh, old = _session("s-fresh", started_min_ago=2), _session("s-old", started_min_ago=20)
    closed, _ = _start(monkeypatch, [fresh, old], [])
    assert closed == ["s-old"]


def test_quiet_for_the_stale_window_is_closed(monkeypatch):
    """The boundary is class_live's `_STALE_AFTER_SEC`, so the two agree on what has gone quiet."""
    window = main._STALE_AFTER_SEC // 60
    just_in = _session("s-in", started_min_ago=300)
    just_out = _session("s-out", started_min_ago=300)
    closed, _ = _start(monkeypatch, [just_in, just_out], [
        {"session_id": "s-in", "answered_at": _ago(window - 1)},
        {"session_id": "s-out", "answered_at": _ago(window + 1)}])
    assert closed == ["s-out"]


def test_an_unreadable_activity_closes_nothing(monkeypatch):
    """Leaving a dead session to the sweep costs nothing; closing a live one splits it."""
    closed, stopped = _start(monkeypatch, [_session("s-old", started_min_ago=300)], [], boom=True)
    assert closed == [] and stopped == []
