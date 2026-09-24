"""The loop around the abandoned-session sweep, and `student_sessions`' idle/activity flags."""
import os
import time

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import main  # noqa: E402


class _FakeDB:
    """The sessions read plus the four activity sources, any one of which can be made to fail."""

    SIGNALS = ("cognitive_signals", "face_signals", "heart_signals")

    def __init__(self, sessions, answers, boom=False, signals=None,
                 boom_table="session_answers"):
        self._sessions, self._answers, self._boom = sessions, answers, boom
        self._signals = signals or {}
        self._boom_table = boom_table
        self.filters = []

    def table(self, name):
        self._t = name
        return self

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    # Recorded, since the measured-row filter is server-side. Nothing here executes them:
    # see `test_the_filters_are_valid_postgrest` and `scripts/assert_signal_rls.sql`.

    def or_(self, expression):
        self.filters.append((self._t, expression))
        return self

    def filter(self, column, operator, value):
        self.filters.append((self._t, f"{column}.{operator}.{value}"))
        return self

    def measured_columns(self, table):
        """Which columns this table's rows were required to have, whichever filter form said so."""
        return {term.split(".")[0]
                for recorded, expression in self.filters if recorded == table
                for term in expression.split(",")}

    def execute(self):
        if self._boom and self._t == self._boom_table:
            raise RuntimeError(f"{self._t} unavailable")
        if self._t == "session_answers":
            return type("R", (), {"data": self._answers})
        if self._t in self.SIGNALS:
            return type("R", (), {"data": self._signals.get(self._t, [])})
        return type("R", (), {"data": self._sessions})


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


def test_a_failed_signal_read_is_unknown_too(monkeypatch):
    """A failed source can only under-report activity, so the flag goes unknown."""
    rows = [_session("s-partial", started_min_ago=30)]
    _as_teacher(monkeypatch, _FakeDB(rows, answers=[], boom=True,
                                     boom_table="heart_signals"))

    out = main.student_sessions("u1", request=None)
    assert out[0]["activity_known"] is False
    assert out[0]["idle"] is False


def test_a_headband_on_a_desk_does_not_keep_a_session_alive(monkeypatch):
    """`contact_poor` rows (measurements nulled) arrive every tick, so only measured rows count.

    Asserted on the filter, since it is applied server-side.
    """
    from datetime import timedelta
    old = (main._utc_now() - timedelta(seconds=main._STALE_AFTER_SEC + 120)).isoformat()
    rows = [_session("s-desk", started_min_ago=30)]
    answers = [{"session_id": "s-desk", "answered_at": old}]
    db = _FakeDB(rows, answers)
    _as_teacher(monkeypatch, db)

    out = main.student_sessions("u1", request=None)
    assert out[0]["idle"] is True, "nothing measured anything; the student left"

    assert db.measured_columns("cognitive_signals") == {"focus"}
    assert db.measured_columns("heart_signals") == {"heart_rate_bpm"}
    assert db.measured_columns("session_answers") == set(), (
        "an answer is activity whatever the sensors were doing")


def test_a_gaze_only_face_row_counts_as_a_measurement(monkeypatch):
    """Under `-Gaze -NoEmotion` every `emotion` is NULL; pose refuses independently of gaze."""
    rows = [_session("s-cam", started_min_ago=30)]
    db = _FakeDB(rows, answers=[])
    _as_teacher(monkeypatch, db)
    main.student_sessions("u1", request=None)

    face = db.measured_columns("face_signals")
    assert "emotion" in face
    assert "gaze_x" in face, "a gaze-only deployment measures too"
    assert "head_yaw" in face, "pose refuses independently of gaze"


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


def test_the_filters_are_valid_postgrest():
    """Built with the real client and asserted on the wire; the fake only records strings.

    Also pins the single-column form `focus=not.is.null`, not a one-branch `or=(...)`.
    """
    from urllib.parse import unquote
    from postgrest import SyncPostgrestClient

    client = SyncPostgrestClient("http://localhost:54321/rest/v1")
    emitted = {}
    for table, column, measured in main._ACTIVITY_SOURCES:
        query = (client.table(table).select(f"session_id, {column}")
                 .in_("session_id", ["11111111-1111-1111-1111-111111111111"]))
        if measured:
            query = main._measured_only(query, measured)
        emitted[table] = unquote(str(query.order(column, desc=True)
                                     .limit(500).request.params))

    assert "focus=not.is.null" in emitted["cognitive_signals"]
    assert "heart_rate_bpm=not.is.null" in emitted["heart_signals"]
    assert ("or=(emotion.not.is.null,gaze_x.not.is.null,head_yaw.not.is.null)"
            in emitted["face_signals"])
    assert "not.is.null" not in emitted["session_answers"], (
        "an answer is activity whatever the sensors were doing")
    for table, params in emitted.items():
        assert "order=" in params and "limit=500" in params, (table, params)


# Column existence is checked by `scripts/assert_signal_rls.sql` against
# `information_schema`, deliberately not by parsing migration SQL here.

def test_nothing_to_look_up_is_not_a_failed_read(monkeypatch):
    """`activity_known: False` means only "the read failed", not "no open sessions"."""
    rows = [_session("s-done", ended=main._utc_now().isoformat())]
    _as_teacher(monkeypatch, _FakeDB(rows, answers=[]))

    out = main.student_sessions("u1", request=None)
    assert out[0]["activity_known"] is True
    assert out[0]["idle"] is False
