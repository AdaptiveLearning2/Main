"""Week-over-week averages, read from the rollup and nothing else.

The trend is the one reporting surface most likely to be read *after* a school
year ends — which is exactly when `expire_signal_rows` has removed the
per-sample rows. So it reads `signal_daily_rollup`, the copy that survives, and
never the tables the weekly report reads under a row cap: that cap trims
oldest-first, so over six months the early weeks would come back empty and read
as a quiet term rather than as data nobody fetched.
"""
import os
from datetime import datetime, timezone

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402
from tests.test_access_control import _FakeSupabase  # noqa: E402

STUDENT = "student-1"

# A Thursday. Its Monday is 2026-06-08, which is what every window below counts
# back from -- pinned so "this week" cannot drift with the wall clock.
NOW_UTC = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch):
    monkeypatch.setattr(main, "_utc_now", lambda: NOW_UTC)
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": main.WINDOW_OPEN, "starts_on": "2000-01-01",
        "ends_on": "2099-12-31", "timezone": "UTC"})


def _rollup(day, channel, **cols):
    base = {"user_id": STUDENT, "day": day, "channel": channel,
            "sample_count": 0, "trusted_sample_count": 0}
    return {**base, **cols}


def _tables(rollup=(), consent=True):
    return {
        "signal_consent": [{"user_id": STUDENT, "eeg_enabled": True,
                            "headband_optical_enabled": consent,
                            "camera_enabled": consent}],
        "signal_daily_rollup": list(rollup),
    }


def _fake(rollup=(), **kw):
    return _FakeSupabase(_tables(rollup=rollup), **kw)


def _week(out, monday):
    return next(w for w in out["weeks"] if w["week_start"] == monday)


# ─── the shape of the range ──────────────────────────────────────────────

def test_weeks_are_whole_and_monday_anchored(monkeypatch):
    """Counting back `weeks * 7` days from today would put a part-week at each
    end, so the first bar would be a fraction of the others while looking like
    a full one."""
    monkeypatch.setattr(main, "supabase", _fake())

    out = main._signal_trend(STUDENT, weeks=4)

    assert [w["week_start"] for w in out["weeks"]] == [
        "2026-05-18", "2026-05-25", "2026-06-01", "2026-06-08"]


def test_a_week_with_nothing_recorded_is_still_a_week(monkeypatch):
    """A gap, not a dropped bar. Omitted, a fortnight off school renders as the
    weeks either side sitting next to each other."""
    monkeypatch.setattr(main, "supabase", _fake(rollup=[
        _rollup("2026-06-08", "cognitive", avg_focus=0.5, trusted_sample_count=10)]))

    out = main._signal_trend(STUDENT, weeks=3)

    assert len(out["weeks"]) == 3
    quiet = _week(out, "2026-06-01")
    assert quiet["focus"] is None, "no data is a gap, never a zero"
    assert quiet["days_with_data"] == 0


def test_the_range_is_bounded(monkeypatch):
    monkeypatch.setattr(main, "supabase", _fake())
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": STUDENT})
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a: None)

    assert len(main.student_signal_trend(STUDENT, None, weeks=999)["weeks"]) \
        == main._TREND_MAX_WEEKS
    assert len(main.student_signal_trend(STUDENT, None, weeks=0)["weeks"]) == 2


# ─── the arithmetic ──────────────────────────────────────────────────────

def test_a_week_is_weighted_by_samples_not_a_mean_of_daily_means(monkeypatch):
    """Two days, one of them a hundred times heavier.

    An unweighted mean of 0.9 and 0.1 is 0.50. Weighted 1000:10 it is 0.892 --
    and the weight is not a preference: `rollup_signal_day` writes `avg(focus)`,
    Postgres `avg()` skips nulls, so the stored average's own denominator is
    the trusted count.
    """
    monkeypatch.setattr(main, "supabase", _fake(rollup=[
        _rollup("2026-06-08", "cognitive", avg_focus=0.9, trusted_sample_count=1000),
        _rollup("2026-06-09", "cognitive", avg_focus=0.1, trusted_sample_count=10),
    ]))

    focus = _week(main._signal_trend(STUDENT, weeks=1), "2026-06-08")["focus"]

    assert focus == pytest.approx((0.9 * 1000 + 0.1 * 10) / 1010, abs=1e-4)
    assert focus != pytest.approx(0.5, abs=0.01), "that is the unweighted answer"


def test_a_day_that_measured_nothing_does_not_drag_the_week_down(monkeypatch):
    """A null average is "recorded but unusable", not zero. Contributing 0
    would pull the week down by exactly the days that measured nothing."""
    monkeypatch.setattr(main, "supabase", _fake(rollup=[
        _rollup("2026-06-08", "cognitive", avg_focus=0.8, trusted_sample_count=100),
        # Poor electrode contact all day: rows exist, measurements are null.
        _rollup("2026-06-09", "cognitive", avg_focus=None,
                sample_count=400, trusted_sample_count=0),
    ]))

    week = _week(main._signal_trend(STUDENT, weeks=1), "2026-06-08")

    assert week["focus"] == pytest.approx(0.8)
    assert week["days_with_data"] == 2, "the day happened, it just measured nothing"


def test_heart_sources_are_unioned_and_ordered(monkeypatch):
    """This rides on a caption. An order that shuffles between reads looks like
    the sensor changed."""
    monkeypatch.setattr(main, "supabase", _fake(rollup=[
        _rollup("2026-06-08", "heart", avg_heart_rate_bpm=70,
                trusted_sample_count=10, heart_sources=["muse_optics"]),
        _rollup("2026-06-09", "heart", avg_heart_rate_bpm=72,
                trusted_sample_count=10, heart_sources=["rppg", "muse_optics"]),
    ]))

    week = _week(main._signal_trend(STUDENT, weeks=1), "2026-06-08")

    assert week["heart_sources"] == ["muse_optics", "rppg"]
    assert week["heart_rate_bpm"] == pytest.approx(71)


def test_emotion_counts_add_up_across_the_week(monkeypatch):
    monkeypatch.setattr(main, "supabase", _fake(rollup=[
        _rollup("2026-06-08", "emotion", trusted_sample_count=3,
                emotion_counts={"neutral": 2, "happy": 1}),
        _rollup("2026-06-09", "emotion", trusted_sample_count=2,
                emotion_counts={"neutral": 2}),
    ]))

    week = _week(main._signal_trend(STUDENT, weeks=1), "2026-06-08")

    assert week["emotion_distribution"] == {"neutral": 4, "happy": 1}
    assert week["emotion_samples"] == 5


# ─── the three states ────────────────────────────────────────────────────

def test_a_failed_read_is_not_a_quiet_term(monkeypatch):
    """Both answer with empty weeks, and only the flag tells them apart."""
    monkeypatch.setattr(main, "supabase", _fake(table_raises=["signal_daily_rollup"]))

    out = main._signal_trend(STUDENT, weeks=4)

    assert out["retrieved"] is False
    assert all(w["focus"] is None for w in out["weeks"])


def test_a_genuinely_empty_term_reads_as_retrieved(monkeypatch):
    monkeypatch.setattr(main, "supabase", _fake())

    out = main._signal_trend(STUDENT, weeks=4)

    assert out["retrieved"] is True


# ─── what it may read ────────────────────────────────────────────────────

def test_it_reads_the_rollup_and_never_the_per_sample_tables(monkeypatch):
    """The whole reason this is not built on `_weekly_signal_report`.

    That one caps its reads and trims oldest-first, so over a long range the
    early weeks would come back empty -- indistinguishable from a quiet term on
    the surface most likely to be read after the raw rows are deleted.
    """
    fake = _fake(rollup=[_rollup("2026-06-08", "cognitive", avg_focus=0.5,
                                 trusted_sample_count=10)])
    monkeypatch.setattr(main, "supabase", fake)

    main._signal_trend(STUDENT, weeks=26)

    assert "signal_daily_rollup" in fake.table_calls
    for table in ("cognitive_signals", "face_signals", "heart_signals", "sessions"):
        assert table not in fake.table_calls, f"{table} must not be read here"


def test_a_declined_channel_is_never_read(monkeypatch):
    """Consent skips the query, it does not filter the result.

    CLAUDE.md: *never fall back to a query that reads what the caller opted out
    of*. The first version of this read every channel and dropped the declined
    ones in Python, on the reasoning that the alternative was three queries --
    a false choice, since one `.in_()` filters the single query it already made.

    Asserting on the filter rather than the payload is the point: an absent
    heart figure cannot tell "asked and discarded" from "never asked", which is
    the whole distinction the rule is about.
    """
    rollup = [
        _rollup("2026-06-08", "cognitive", avg_focus=0.5, trusted_sample_count=10),
        _rollup("2026-06-08", "heart", avg_heart_rate_bpm=70, trusted_sample_count=10),
        _rollup("2026-06-08", "emotion", trusted_sample_count=4,
                emotion_counts={"neutral": 4}),
    ]
    fake = _fake(rollup=rollup)
    monkeypatch.setattr(main, "supabase", fake)

    out = main._signal_trend(STUDENT, weeks=1, include_heart=False,
                             include_emotion=False)

    asked = [f for q in fake.queries for f in q.filters if f[0] == "channel"]
    assert asked == [("channel", ("in", ["cognitive"]))], (
        "the declined channels must not be in the query at all")

    week = _week(out, "2026-06-08")
    assert week["focus"] == pytest.approx(0.5)
    assert week["heart_rate_bpm"] is None
    assert week["emotion_distribution"] == {}
    # And the payload says the absence is a decision, not an empty week.
    assert out["heart_included"] is False
    assert out["emotion_included"] is False


def test_a_consented_channel_is_asked_for(monkeypatch):
    """The other half: a filter that asked for nothing would pass the test
    above while reporting a blank term for everyone."""
    fake = _fake()
    monkeypatch.setattr(main, "supabase", fake)

    main._signal_trend(STUDENT, weeks=1)

    asked = [f for q in fake.queries for f in q.filters if f[0] == "channel"]
    assert asked == [("channel", ("in", ["cognitive", "heart", "emotion"]))]


def test_the_endpoint_checks_the_relationship_before_reading(monkeypatch):
    """Same rule as the weekly report -- it is the same data one aggregation
    coarser, so nothing here may be readable by anyone who could not read
    that."""
    monkeypatch.setattr(main, "supabase", _fake())
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "someone-else"})
    checked = []
    monkeypatch.setattr(main, "_verify_can_view_student",
                        lambda viewer, sid: checked.append((viewer["id"], sid)))

    main.student_signal_trend(STUDENT, None, weeks=4)

    assert checked == [("someone-else", STUDENT)]
