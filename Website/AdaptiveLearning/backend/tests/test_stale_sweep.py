"""When the abandoned-session sweep runs.

`_sweep_abandoned_sessions` itself is exercised in `test_session_alerts.py`;
this file is about the loop around it, which is where the defect was. The
sweep worked correctly every time it was called by hand -- it just was not
being called.
"""
import os
import time

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import main  # noqa: E402


class _FakeDB:
    """Just enough of the supabase client for `student_sessions`: a sessions
    read and a session_answers read that can be made to fail."""

    def __init__(self, sessions, answers, boom=False):
        self._sessions, self._answers, self._boom = sessions, answers, boom

    def table(self, name):
        self._t = name
        return self

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def execute(self):
        if self._t == "session_answers":
            if self._boom:
                raise RuntimeError("session_answers unavailable")
            return type("R", (), {"data": self._answers})
        return type("R", (), {"data": self._sessions})


def test_the_first_sweep_does_not_wait_a_whole_interval(monkeypatch):
    """A process that does not outlive one interval never swept at all.

    `uvicorn --reload` restarts on every file save, so on a development machine
    that is most of them: measured on a local stack after a stretch of editing,
    **123 sessions open and every one past the 6h threshold**, on a sweep that
    works correctly when called by hand. In production the same gap is smaller
    and still real -- nothing is collected until an interval after each deploy.
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
    """The stop event is checked between passes as well as at the top. Without
    that, a shutdown arriving during the first sweep would sit through a whole
    interval before the loop condition noticed -- and this thread is joined on
    shutdown, so that is a hang, not a delay."""
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
    """`abandoned` is an age (6h) and only stops the list claiming a session
    from June is in progress. A student who answered three questions and shut
    the laptop stayed `LIVE` on the teacher's screen, duration ticking up,
    until that mark -- telling a teacher a child was working who had gone.

    `idle` is real quiet, against the same window `class_live` uses.
    """
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
    """The teeth. Marking everything idle would be as wrong as marking
    everything live."""
    rows = [_session("s-busy", started_min_ago=30)]
    answers = [{"session_id": "s-busy", "answered_at": main._utc_now().isoformat()}]
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *a, **k: None)
    monkeypatch.setattr(main, "get_user", lambda r: {"id": "t1"})
    monkeypatch.setattr(main, "supabase", _FakeDB(rows, answers))

    assert main.student_sessions("u1", request=None)[0]["idle"] is False


def test_a_failed_activity_read_never_claims_idle(monkeypatch):
    """Three states, not two. A database blip must not relabel a live session
    as quiet -- the same error as reporting a failed count as a quiet week. The
    client gates on `activity_known` for exactly this."""
    rows = [_session("s-unknown", started_min_ago=30)]
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *a, **k: None)
    monkeypatch.setattr(main, "get_user", lambda r: {"id": "t1"})
    monkeypatch.setattr(main, "supabase", _FakeDB(rows, answers=None, boom=True))

    out = main.student_sessions("u1", request=None)
    assert out[0]["activity_known"] is False
    assert out[0]["idle"] is False, "unknown is not idle"
