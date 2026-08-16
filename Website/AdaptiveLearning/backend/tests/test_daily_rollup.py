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


def test_one_days_failure_does_not_skip_the_next(monkeypatch):
    """Per-day isolation.

    A single try around the loop meant a failure on the first day of a two-day
    session also skipped the second -- and the second is the closing day, the
    one the call exists for. The days are independent recomputations; nothing
    about one failing says the next will.
    """
    attempted = []

    class _FirstDayFails:
        def rpc(self, name, params):
            attempted.append(params["p_day"])
            first = len(attempted) == 1

            class _Exec:
                def execute(_s):
                    if first:
                        raise RuntimeError("transient")
                    return type("R", (), {"data": None})()
            return _Exec()

    monkeypatch.setattr(main, "supabase", _FirstDayFails())
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": main.WINDOW_OPEN, "timezone": LA})

    # 23:30 on the 11th to 00:30 on the 12th, Los Angeles.
    main._rollup_session_days(USER, "2026-06-12T06:30:00Z", "2026-06-12T07:30:00Z")

    assert attempted == ["2026-06-11", "2026-06-12"], (
        "the second day was skipped because the first one failed"
    )


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
    # Both now live in `_close_session`, which is where the ordering is decided
    # for all three close sites at once.
    source = inspect.getsource(main._close_session)
    assert source.index("_rollup_session_days") > source.index("_credit_session_to_user_stats"), (
        "the rollup runs before the stats write it must not be able to affect"
    )


# ── every path that closes a session has to summarise its day ───────────────

def test_every_session_close_writes_a_rollup():
    """Discovered, not listed.

    Three places end a session: the `/end` endpoint, `start_session`'s sweep of
    sessions a student abandoned, and `class_live`'s staleness monitor. Only the
    first called the rollup, so a student who closed the tab -- the case the
    sweep exists for -- left a day with raw rows and no summary. Once the delete
    job runs, that day is simply gone.

    Matched on the write rather than on a hand-kept list of functions, for the
    same reason as the recording-site check: "sets ended_at" is a fact about the
    code, not a judgement someone has to remember to record here.

    Now asserted through `_close_session`. Each site used to carry its own copy
    of the close sequence and each copy drifted separately -- the sweep credited
    correct answers it had never selected, `class_live` skipped the empty-session
    discard -- so the sequence lives in one helper and this checks that every
    closer reaches it. The helper's own contents are pinned just below, which is
    what stops the indirection turning into a hole.
    """
    import inspect

    from conftest import close_sites

    closers = close_sites()

    assert len(closers) >= 3, f"the search stopped finding closers: {closers}"
    for name, source in closers:
        assert "_close_session(" in source or "_rollup_session_days" in source, (
            f"{name} ends a session without rolling up its day. The rollup is "
            "what survives the end-of-year delete, so a day closed this way is "
            "lost rather than summarised."
        )


def test_the_shared_close_does_every_step_a_close_owes():
    """The other half of the check above.

    Routing three sites through one helper only helps if the helper is complete;
    a step dropped here now goes missing from all three at once, which is the
    risk the consolidation trades for. Each of these shipped as "the close site
    that was missed" at least once.
    """
    import inspect
    source = inspect.getsource(main._close_session)

    for call, why in [
        ("_discard_if_nothing_recorded",
         "an abandoned pairing stays in the student's History for ever"),
        ("_credit_session_to_user_stats",
         "the session's answers never reach the lifetime totals"),
        ("_rollup_session_days",
         "the day has raw rows and no summary, and the delete job takes it"),
        ("chart_archive.schedule",
         "the raw rows expire with no picture left behind them"),
    ]:
        assert call in source, f"the shared close no longer calls {call}: {why}"

    # Discard first: a rollup of nothing and an archive of four empty charts are
    # work done for a session that is about to stop existing.
    assert source.index("_discard_if_nothing_recorded") < source.index("_rollup_session_days")
    assert source.index("_discard_if_nothing_recorded") < source.index("chart_archive.schedule")
