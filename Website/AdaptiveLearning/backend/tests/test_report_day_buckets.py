"""Report days are the school's days, not UTC's.

`_weekly_signal_report` buckets rows into calendar days. It used to do that by
slicing `str(ts)[:10]`, which is UTC because that is what PostgREST returns --
so a lesson at 4pm in California landed on the *next* day of a parent's chart,
and one at 8am in Sydney landed on the previous one. The same column that gates
recording (`retention_window.timezone`) is what resolves it.

The trap in testing this is that a UTC-configured school makes every assertion
pass whether or not the fix exists, which is exactly the state the rest of the
suite is in. Every test here therefore uses a zone with a real offset and pins
`now`, so the school's date and UTC's date genuinely differ.
"""

import os
from datetime import datetime, timezone

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402
from tests.test_access_control import _FakeSupabase  # noqa: E402

STUDENT = "student-1"

# 03:00 UTC on 12 June 2026. In Los Angeles (UTC-7 in June) that is 20:00 on the
# **11th** -- an ordinary evening, and the previous calendar day.
NOW_UTC = datetime(2026, 6, 12, 3, 0, tzinfo=timezone.utc)
LA = "America/Los_Angeles"


@pytest.fixture
def at_three_am_utc(monkeypatch):
    monkeypatch.setattr(main, "_utc_now", lambda: NOW_UTC)


def _school(monkeypatch, tz):
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": main.WINDOW_OPEN, "starts_on": "2000-01-01",
        "ends_on": "2099-12-31", "timezone": tz})


def _consent_row():
    return {"user_id": STUDENT, "eeg_enabled": True,
            "headband_optical_enabled": True, "camera_enabled": True}


def _tables(cog=(), sessions=()):
    return {"signal_consent": [_consent_row()],
            "cognitive_signals": list(cog),
            "face_signals": [], "heart_signals": [],
            "sessions": list(sessions)}


def _day(report, date_str):
    return next((d for d in report["daily"] if d["date"] == date_str), None)


# ── the bug, directly ────────────────────────────────────────────────────────

def test_an_evening_lesson_lands_on_the_local_day(monkeypatch, at_three_am_utc):
    """20:00 on the 11th in California, not 03:00 on the 12th in UTC.

    This is the whole finding: a parent looking at Thursday saw a session that
    happened on Wednesday evening, and Wednesday looked emptier than it was.
    """
    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables(
        cog=[{"user_id": STUDENT, "ts": NOW_UTC.isoformat(), "focus": 0.8}])))

    report = main._weekly_signal_report(STUDENT)

    assert _day(report, "2026-06-11")["focus"] == 0.8, (
        "the evening lesson was bucketed by UTC midnight, a day late"
    )
    assert _day(report, "2026-06-12") is None, "there is no 12th in the school's week yet"


def test_the_same_row_buckets_differently_under_a_different_school(monkeypatch,
                                                                  at_three_am_utc):
    """The control that makes the test above mean something.

    One row, one instant, two schools: it is Wednesday in California and
    already Friday in Auckland. If both answered the same, the timezone would
    not be reaching the bucketing at all.
    """
    rows = _tables(cog=[{"user_id": STUDENT, "ts": NOW_UTC.isoformat(), "focus": 0.8}])

    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(rows))
    la_days = [d["date"] for d in main._weekly_signal_report(STUDENT)["daily"]
               if d["focus"] is not None]

    _school(monkeypatch, "Pacific/Auckland")
    monkeypatch.setattr(main, "supabase", _FakeSupabase(rows))
    nz_days = [d["date"] for d in main._weekly_signal_report(STUDENT)["daily"]
               if d["focus"] is not None]

    assert la_days == ["2026-06-11"]
    assert nz_days == ["2026-06-12"]


def test_the_week_ends_on_the_schools_today(monkeypatch, at_three_am_utc):
    """The last bucket is the school's current day. Built from UTC it ran a day
    ahead, so every chart carried a trailing empty column that read as a day
    with no activity rather than as a day that has not happened."""
    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables()))

    days = [d["date"] for d in main._weekly_signal_report(STUDENT)["daily"]]

    assert days[-1] == "2026-06-11"
    assert days[0] == "2026-06-05", "seven school days, ending today"


# ── the fetch window has to reach back far enough ───────────────────────────

def test_the_query_starts_at_the_first_school_days_midnight(monkeypatch,
                                                            at_three_am_utc):
    """`now - 7 days` in UTC starts *after* the earliest school day begins when
    the school is behind UTC, so the oldest day on the chart silently lost its
    first hours and averaged only the rest."""
    _school(monkeypatch, LA)
    fake = _FakeSupabase(_tables())
    monkeypatch.setattr(main, "supabase", fake)

    report = main._weekly_signal_report(STUDENT)

    # Midnight on 5 June in Los Angeles is 07:00 UTC on the 5th.
    assert report["since"].startswith("2026-06-05T07:00")
    oldest_day = report["daily"][0]["date"]
    since = main._parse_ts(report["since"])
    from zoneinfo import ZoneInfo
    assert since.astimezone(ZoneInfo(LA)).date().isoformat() == oldest_day
    assert since.astimezone(ZoneInfo(LA)).hour == 0, (
        "the oldest day starts mid-morning, so its early rows were never fetched"
    )


# ── degradation, in the opposite direction to the recording gate ────────────

@pytest.mark.parametrize("window", [
    {"state": main.WINDOW_UNREADABLE, "timezone": None},
    {"state": main.WINDOW_UNCONFIGURED, "timezone": None},
    {"state": main.WINDOW_OPEN, "timezone": "Mars/Olympus_Mons"},
])
def test_an_unusable_timezone_still_produces_a_report(monkeypatch, window):
    """Deliberately unlike `_retention_window`, which denies on exactly these.

    A wrong boundary while *recording* means data collected against a refusal.
    A wrong boundary while *reporting* means a chart column is a few hours off.
    Refusing here would blank a parent's dashboard over a config typo, which is
    the larger harm -- so this degrades to UTC and says nothing.
    """
    monkeypatch.setattr(main, "_retention_window", lambda: window)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables()))

    report = main._weekly_signal_report(STUDENT)

    assert len(report["daily"]) == 7
    assert main._school_timezone().key == "UTC"


def test_an_unparseable_timestamp_joins_no_day(monkeypatch, at_three_am_utc):
    """Dropped from every bucket rather than silently landing in one. A row
    that cannot be placed in time is not evidence about any particular day."""
    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables(
        cog=[{"user_id": STUDENT, "ts": "not-a-timestamp", "focus": 0.9}])))

    report = main._weekly_signal_report(STUDENT)

    assert all(d["focus"] is None for d in report["daily"])


# ── the rollup is what the report reads once the raw rows are gone ──────────

def _rollup(day, channel, **cols):
    base = {"user_id": STUDENT, "day": day, "channel": channel,
            "sample_count": 0, "trusted_sample_count": 0}
    return {**base, **cols}


def _with_rollup(cog=(), rollup=()):
    return {**_tables(cog=cog), "signal_daily_rollup": list(rollup)}


def test_a_day_with_no_raw_rows_falls_back_to_its_rollup(monkeypatch, at_three_am_utc):
    """After the delete job runs this is the only path there is.

    The day is chosen on what is *present* rather than by comparing it against
    the retention window: the two agree, because the job only deletes days
    outside the window, but presence cannot drift from reality where a second
    copy of the boundary arithmetic can.
    """
    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_with_rollup(
        rollup=[_rollup("2026-06-09", "cognitive", avg_focus=0.42,
                        sample_count=1200, trusted_sample_count=1100)])))

    day = _day(main._weekly_signal_report(STUDENT), "2026-06-09")

    assert day["focus"] == 0.42
    assert day["cognitive_from_rollup"] is True
    assert day["cognitive_retrieved"] is True, "a summarised day was retrieved"
    assert day["cognitive_samples"] == 1200, (
        "the count is what keeps a thin day visibly thin after the detail is gone"
    )


def test_raw_rows_win_over_a_rollup_for_the_same_day(monkeypatch, at_three_am_utc):
    """Full fidelity where it still exists. The rollup is a fallback, not a
    cache -- preferring it would quietly answer today's chart from yesterday's
    summary."""
    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_with_rollup(
        cog=[{"user_id": STUDENT, "ts": NOW_UTC.isoformat(), "focus": 0.9}],
        rollup=[_rollup("2026-06-11", "cognitive", avg_focus=0.1,
                        sample_count=99)])))

    day = _day(main._weekly_signal_report(STUDENT), "2026-06-11")

    assert day["focus"] == 0.9
    assert day["cognitive_from_rollup"] is False
    assert day["cognitive_samples"] == 1


def test_a_summarised_day_is_marked_as_one(monkeypatch, at_three_am_utc):
    """Reduced fidelity, named.

    A day averaged from its own samples and a day averaged once and then
    deleted answer the same question to different precision. A chart that mixes
    them without saying so invites a comparison that is not sound.
    """
    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_with_rollup(
        cog=[{"user_id": STUDENT, "ts": NOW_UTC.isoformat(), "focus": 0.9}],
        rollup=[_rollup("2026-06-09", "cognitive", avg_focus=0.42)])))

    report = main._weekly_signal_report(STUDENT)

    assert _day(report, "2026-06-11")["cognitive_from_rollup"] is False
    assert _day(report, "2026-06-09")["cognitive_from_rollup"] is True
    # Every day carries the flag, so a consumer never reads "field absent" as a
    # third state -- the same rule the retrieved flags follow.
    assert all("cognitive_from_rollup" in d for d in report["daily"])


def test_a_failed_rollup_read_is_reported_rather_than_read_as_absence(monkeypatch,
                                                                      at_three_am_utc):
    """Once the raw rows are gone the rollup *is* the history, so a failed read
    means the week is unreadable -- not that it was quiet. That is the one claim
    this read is least entitled to make."""
    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _with_rollup(), table_raises={"signal_daily_rollup"}))

    report = main._weekly_signal_report(STUDENT)

    assert report["retrieved"]["rollup"] is False
    # And the rest of the report still came back: one broken read does not
    # blank a dashboard.
    assert report["retrieved"]["cognitive"] is True
    assert len(report["daily"]) == 7


def test_the_weeks_headline_figures_include_summarised_days(monkeypatch,
                                                            at_three_am_utc):
    """The chart and the numbers above it have to agree.

    `daily` fell back to the rollup while `averages` and `highlights` were
    computed only from rows still in the raw tables -- so once the delete job
    runs, a week whose detail is gone renders a full chart above an empty
    summary. Two answers to one question, on one screen.
    """
    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_with_rollup(
        rollup=[_rollup("2026-06-09", "cognitive", avg_focus=0.4,
                        avg_stress=0.2, avg_engagement=0.6,
                        sample_count=100, trusted_sample_count=100),
                _rollup("2026-06-10", "heart", avg_heart_rate_bpm=80.0,
                        heart_sources=["muse_optics"],
                        sample_count=50, trusted_sample_count=50),
                _rollup("2026-06-10", "emotion",
                        emotion_counts={"happy": 7, "neutral": 2},
                        sample_count=9, trusted_sample_count=9)])))

    report = main._weekly_signal_report(STUDENT)

    assert report["averages"]["focus"] == 0.4
    assert report["averages"]["engagement"] == 0.6
    assert report["highlights"]["heart_rate_bpm"] == 80.0
    assert report["highlights"]["dominant_emotion"] == "happy"
    assert report["heart_sources"] == ["muse_optics"], (
        "the only surviving record that the sensor changed"
    )


def test_the_week_weights_days_by_how_much_they_hold(monkeypatch, at_three_am_utc):
    """Sum and count, not a mean of daily means.

    Days differ in length. Averaging the averages weights a four-sample day
    like a four-thousand-sample one, which is how a single quiet evening can
    drag a week's figure around.
    """
    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_with_rollup(
        rollup=[_rollup("2026-06-09", "cognitive", avg_focus=1.0,
                        sample_count=900, trusted_sample_count=900),
                _rollup("2026-06-10", "cognitive", avg_focus=0.0,
                        sample_count=100, trusted_sample_count=100)])))

    # 900 samples at 1.0 and 100 at 0.0 is 0.9, not the 0.5 a mean of means gives.
    assert main._weekly_signal_report(STUDENT)["averages"]["focus"] == 0.9


def test_raw_and_summarised_days_combine_into_one_mean(monkeypatch, at_three_am_utc):
    """The state during the term after the delete job first runs: part of the
    week still has its samples, part has only a summary."""
    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_with_rollup(
        cog=[{"user_id": STUDENT, "ts": NOW_UTC.isoformat(), "focus": 1.0}],
        rollup=[_rollup("2026-06-09", "cognitive", avg_focus=0.0,
                        sample_count=3, trusted_sample_count=3)])))

    # One raw sample at 1.0, three summarised at 0.0 -> 0.25.
    assert main._weekly_signal_report(STUDENT)["averages"]["focus"] == 0.25


def test_a_failed_raw_read_is_not_papered_over_by_the_rollup(monkeypatch,
                                                             at_three_am_utc):
    """"No rows" and "the query failed" are not the same evidence.

    Only the first means the detail is gone. The rollup is written as sessions
    close, so on a failed read it may be *stale* -- today's lags the session in
    progress -- and serving it as complete would present old numbers as current
    with `retrieved` vouching for them.
    """
    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _with_rollup(rollup=[_rollup("2026-06-09", "cognitive", avg_focus=0.4,
                                     sample_count=10, trusted_sample_count=10)]),
        table_raises={"cognitive_signals"}))

    day = _day(main._weekly_signal_report(STUDENT), "2026-06-09")

    assert day["cognitive_from_rollup"] is False
    assert day["cognitive_retrieved"] is False, (
        "a failed live read was reported as a complete day"
    )
    assert day["focus"] is None
