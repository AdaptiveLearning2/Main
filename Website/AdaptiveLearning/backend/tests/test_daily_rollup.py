"""The rollup writer: which days it recomputes, and that it never costs a close.

The aggregation itself is SQL and is exercised against a real Postgres by the
migration checks; what lives here is the part Python owns -- which days get
recomputed for a session, in whose timezone, and what happens when the call
fails.

The property that matters most is the last one. `_rollup_session_days` runs at
the end of `end_session`, and a rollup is derived data: losing a day of it costs
a summary that the next close rebuilds, while raising would cost the student
their session record and their stats update.
"""

import os
from datetime import datetime, timezone

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402

USER = "11111111-2222-3333-4444-555555555555"
LA = "America/Los_Angeles"


class _RpcRecorder:
    """Records rollup_signal_day calls; optionally blows up on them."""

    def __init__(self, raises=False):
        self.calls = []
        self._raises = raises

    def rpc(self, name, params):
        self.calls.append((name, params))
        recorder = self

        class _Exec:
            def execute(self_inner):
                if recorder._raises:
                    raise RuntimeError("rollup failed")
                return type("R", (), {"data": None})()

        return _Exec()

    @property
    def days(self):
        return [c[1]["p_day"] for c in self.calls]


@pytest.fixture
def rpc(monkeypatch):
    r = _RpcRecorder()
    monkeypatch.setattr(main, "supabase", r)
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": main.WINDOW_OPEN, "starts_on": "2000-01-01",
        "ends_on": "2099-12-31", "timezone": LA})
    return r


def test_a_session_rolls_up_the_school_day_it_happened_on(rpc):
    """20:00 on the 11th in California, not the 12th in UTC -- the same
    boundary the weekly report buckets by, and they have to agree or the rollup
    cannot answer the report's questions once the raw rows are gone."""
    main._rollup_session_days(USER, "2026-06-12T02:00:00Z", "2026-06-12T03:00:00Z")

    assert rpc.days == ["2026-06-11"]
    assert rpc.calls[0][1]["p_timezone"] == LA
    assert rpc.calls[0][1]["p_user_id"] == USER


def test_a_session_over_local_midnight_rolls_up_both_days(rpc):
    """One session, two school days. Recomputing both is cheaper than reasoning
    about which one moved, and the writer is idempotent so the extra call is
    free."""
    # 23:30 on the 11th to 00:30 on the 12th, Los Angeles.
    main._rollup_session_days(USER, "2026-06-12T06:30:00Z", "2026-06-12T07:30:00Z")

    assert rpc.days == ["2026-06-11", "2026-06-12"]


def test_an_implausible_span_does_not_loop(rpc):
    """A corrupt `started_at` must not turn a session close into thousands of
    RPCs. A lesson does not span a year; anything claiming to is bad data."""
    main._rollup_session_days(USER, "1970-01-01T00:00:00Z", "2026-06-12T03:00:00Z")

    assert rpc.days == ["2026-06-11"], "fell back to the closing day only"


def test_an_inverted_span_still_rolls_up_the_closing_day(rpc):
    """The silent half of the same guard.

    A `started_at` later than `ended_at` -- clock skew, or a bad write -- made
    the loop condition false from the start, so it ran zero times and logged
    nothing. A session that just closed always has one day worth recomputing,
    and doing nothing quietly is the outcome this whole helper is shaped to
    avoid.
    """
    main._rollup_session_days(USER, "2026-06-12T03:00:00Z", "2026-06-10T03:00:00Z")

    assert rpc.days == ["2026-06-09"], "an inverted range rolled up nothing"


def test_a_failed_rollup_does_not_reach_the_caller(monkeypatch):
    """The whole reason this is called last and swallows.

    `end_session` has already written `ended_at` and the student's `user_stats`
    by the time this runs. Raising here would surface as a failed session close
    for a summary that the next close rebuilds anyway.
    """
    monkeypatch.setattr(main, "supabase", _RpcRecorder(raises=True))
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": main.WINDOW_OPEN, "timezone": "UTC"})

    main._rollup_session_days(USER, "2026-06-11T10:00:00Z", "2026-06-11T11:00:00Z")


def test_unparseable_timestamps_still_roll_up_today(rpc, monkeypatch):
    """A session whose stamps cannot be read is still a session that just
    ended, and today's rollup is the one it could have changed."""
    monkeypatch.setattr(main, "_utc_now",
                        lambda: datetime(2026, 6, 12, 3, 0, tzinfo=timezone.utc))

    main._rollup_session_days(USER, None, None)

    assert rpc.days == ["2026-06-11"]


def test_the_rollup_runs_after_the_writes_that_matter():
    """Ordering, asserted rather than assumed.

    If the rollup call moved above the `sessions` update or the `user_stats`
    write, a slow or hanging RPC would delay them -- and it would see a session
    row without its `ended_at`.
    """
    import inspect
    source = inspect.getsource(main.end_session)
    assert source.index("_rollup_session_days") > source.index("user_stats"), (
        "the rollup runs before the stats write it must not be able to affect"
    )
