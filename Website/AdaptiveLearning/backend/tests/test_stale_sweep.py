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
