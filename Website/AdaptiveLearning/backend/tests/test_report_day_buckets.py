"""Report days are the school's local days, not UTC; tests pin `now` where the two dates differ."""

import os
from datetime import datetime, timezone

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402
from tests.test_access_control import _FakeSupabase  # noqa: E402

STUDENT = "student-1"

# 03:00 UTC on 12 June 2026 is 20:00 on the 11th in Los Angeles (UTC-7 in June).
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
    """Control: one instant, two schools, so the timezone must reach the bucketing."""
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
    """From UTC it would add a trailing empty column for a day that hasn't happened."""
    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables()))

    days = [d["date"] for d in main._weekly_signal_report(STUDENT)["daily"]]

    assert days[-1] == "2026-06-11"
    assert days[0] == "2026-06-05", "seven school days, ending today"


# ── the fetch window has to reach back far enough ───────────────────────────

def test_the_query_starts_at_the_first_school_days_midnight(monkeypatch,
                                                            at_three_am_utc):
    """`now - 7 days` in UTC would cut the oldest day's first hours behind UTC."""
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
    """Reporting degrades to UTC where `_retention_window` denies: a chart hours off is the lesser harm."""
    monkeypatch.setattr(main, "_retention_window", lambda: window)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables()))

    report = main._weekly_signal_report(STUDENT)

    assert len(report["daily"]) == 7
    assert main._school_timezone().key == "UTC"


def test_an_unparseable_timestamp_joins_no_day(monkeypatch, at_three_am_utc):
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
    """Decided by which data is present, not by re-checking the retention window."""
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
    """The rollup is a fallback, not a cache."""
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
    """Raw and summarised days differ in precision, and the chart has to say which is which."""
    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_with_rollup(
        cog=[{"user_id": STUDENT, "ts": NOW_UTC.isoformat(), "focus": 0.9}],
        rollup=[_rollup("2026-06-09", "cognitive", avg_focus=0.42)])))

    report = main._weekly_signal_report(STUDENT)

    assert _day(report, "2026-06-11")["cognitive_from_rollup"] is False
    assert _day(report, "2026-06-09")["cognitive_from_rollup"] is True
    # Every day carries the flag, so "field absent" is never a third state.
    assert all("cognitive_from_rollup" in d for d in report["daily"])


def test_a_failed_rollup_read_is_reported_rather_than_read_as_absence(monkeypatch,
                                                                      at_three_am_utc):
    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _with_rollup(), table_raises={"signal_daily_rollup"}))

    report = main._weekly_signal_report(STUDENT)

    assert report["retrieved"]["rollup"] is False
    # One broken read must not blank the whole dashboard.
    assert report["retrieved"]["cognitive"] is True
    assert len(report["daily"]) == 7


def test_the_weeks_headline_figures_include_summarised_days(monkeypatch,
                                                            at_three_am_utc):
    """The chart and the summary figures above it must agree."""
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
    # From avg_focus: the stored avg_engagement means two different quantities and is never served.
    assert report["averages"]["engagement"] == 0.4
    assert report["highlights"]["heart_rate_bpm"] == 80.0
    assert report["highlights"]["dominant_emotion"] == "happy"
    assert report["heart_sources"] == ["muse_optics"], (
        "the only surviving record that the sensor changed"
    )


def test_the_week_weights_days_by_how_much_they_hold(monkeypatch, at_three_am_utc):
    """Weighted by sum and count, not a mean of daily means."""
    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_with_rollup(
        rollup=[_rollup("2026-06-09", "cognitive", avg_focus=1.0,
                        sample_count=900, trusted_sample_count=900),
                _rollup("2026-06-10", "cognitive", avg_focus=0.0,
                        sample_count=100, trusted_sample_count=100)])))

    # 900 samples at 1.0 and 100 at 0.0 is 0.9, not the 0.5 a mean of means gives.
    assert main._weekly_signal_report(STUDENT)["averages"]["focus"] == 0.9


def test_raw_and_summarised_days_combine_into_one_mean(monkeypatch, at_three_am_utc):
    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_with_rollup(
        cog=[{"user_id": STUDENT, "ts": NOW_UTC.isoformat(), "focus": 1.0}],
        rollup=[_rollup("2026-06-09", "cognitive", avg_focus=0.0,
                        sample_count=3, trusted_sample_count=3)])))

    # One raw sample at 1.0, three summarised at 0.0 -> 0.25.
    assert main._weekly_signal_report(STUDENT)["averages"]["focus"] == 0.25


def test_a_failed_raw_read_is_not_papered_over_by_the_rollup(monkeypatch,
                                                             at_three_am_utc):
    """Only "no rows" means the detail is gone; on a failed read the rollup may be stale."""
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


# ── face_samples counts emotion samples, not face rows ──────────────────────

def _face_row(ts, emotion, gaze_x=None):
    return {"user_id": STUDENT, "ts": ts, "emotion": emotion,
            "emotion_trusted": emotion is not None, "attention": None,
            "gaze_x": gaze_x, "gaze_y": None}


def test_a_gaze_only_row_does_not_count_as_an_emotion_sample(monkeypatch,
                                                             at_three_am_utc):
    """Gaze-only rows are real face rows with no emotion; the rollup already excludes them."""
    _school(monkeypatch, LA)
    tables = _tables()
    tables["face_signals"] = [
        _face_row("2026-06-12T03:00:00+00:00", "happy"),
        _face_row("2026-06-12T03:00:01+00:00", "sad"),
        _face_row("2026-06-12T03:00:02+00:00", None, gaze_x=0.42),
        _face_row("2026-06-12T03:00:03+00:00", None, gaze_x=0.0),
    ]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(tables))

    report = main._weekly_signal_report(STUDENT)
    day = _day(report, "2026-06-11")

    assert day is not None
    assert day["face_samples"] == 2, (
        f"counted {day['face_samples']}: gaze-only rows inflated the emotion "
        "sample count")
