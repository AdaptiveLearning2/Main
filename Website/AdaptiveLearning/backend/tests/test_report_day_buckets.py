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


def test_a_week_of_untrusted_emotions_still_says_facial_readings_arrived(monkeypatch,
                                                                         at_three_am_utc):
    """The distribution is trusted-only; a quality gate is not a week with no camera."""
    _school(monkeypatch, LA)
    tables = _tables()
    tables["face_signals"] = [{**_face_row(NOW_UTC.isoformat(), "happy"),
                               "emotion_trusted": False}]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(tables))
    report = main._weekly_signal_report(STUDENT, eeg_enabled=False, include_heart=False)
    assert report["emotion_distribution"] == {}
    assert report["summary"] == "Facial recognition readings were recorded this week."


def test_a_raw_days_heart_samples_are_its_trusted_readings(monkeypatch, at_three_am_utc):
    """Matching the day's average; the week's `sample_counts` is what counts rejected rows."""
    _school(monkeypatch, LA)
    tables = _tables()
    tables["heart_signals"] = [
        {"user_id": STUDENT, "ts": NOW_UTC.isoformat(), "source": "rppg",
         "heart_rate_bpm": bpm, "trusted": trusted}
        for bpm, trusted in ((70.0, True), (90.0, True), (150.0, False))]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(tables))
    report = main._weekly_signal_report(STUDENT)
    day = _day(report, "2026-06-11")
    assert day["heart_samples"] == 2
    assert day["heart_rate_bpm"] == 80.0
    assert report["sample_counts"]["heart"] == 3


def test_a_rolled_up_days_heart_samples_are_its_trusted_readings_too(monkeypatch,
                                                                     at_three_am_utc):
    """One definition whether a day's raw rows are still there or have expired."""
    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_with_rollup(
        rollup=[_rollup("2026-06-09", "heart", avg_heart_rate_bpm=80.0,
                        sample_count=3, trusted_sample_count=2)])))
    day = _day(main._weekly_signal_report(STUDENT), "2026-06-09")
    assert day["heart_from_rollup"] is True
    assert day["heart_samples"] == 2


# ── a declined channel's rollup, a lost rollup, the cut day, the no-EEG sentence ──

def _rollup_query(fake):
    return next(q for name, q in zip(fake.table_calls, fake.queries)
                if name == "signal_daily_rollup")


@pytest.mark.parametrize("heart,emotion,channels", [
    (False, False, ["cognitive"]),
    (True, False, ["cognitive", "heart"]),
    (True, True, ["cognitive", "heart", "emotion"]),
])
def test_a_declined_channels_rollup_is_never_read(monkeypatch, at_three_am_utc,
                                                  heart, emotion, channels):
    """The raw read is skipped for a declined channel; its summary must be too."""
    _school(monkeypatch, LA)
    fake = _FakeSupabase(_with_rollup())
    monkeypatch.setattr(main, "supabase", fake)
    main._weekly_signal_report(STUDENT, include_heart=heart, include_emotion=emotion)
    assert ("channel", ("in", channels)) in _rollup_query(fake).filters


def _before_the_year(monkeypatch):
    """A window starting after this week: every day of it is past the expiry cutoff."""
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": main.WINDOW_BEFORE, "starts_on": "2026-09-01",
        "ends_on": "2027-06-30", "timezone": LA})


def test_a_lost_rollup_after_expiry_is_unknown_not_a_quiet_week(monkeypatch, at_three_am_utc):
    """With the raw rows expired, a failed rollup read left every day reading 'recorded nothing'."""
    _before_the_year(monkeypatch)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _with_rollup(), table_raises={"signal_daily_rollup"}))
    report = main._weekly_signal_report(STUDENT)
    assert all(d["cognitive_retrieved"] is False for d in report["daily"])
    assert "No EEG" not in report["summary"]
    assert "could not be loaded" in report["summary"]


def test_a_lost_rollup_before_expiry_is_still_a_quiet_week(monkeypatch, at_three_am_utc):
    """Control: while the raw rows exist they are the record, and empty means empty."""
    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _with_rollup(), table_raises={"signal_daily_rollup"}))
    report = main._weekly_signal_report(STUDENT)
    assert all(d["cognitive_retrieved"] is True for d in report["daily"])
    assert "No EEG" in report["summary"]


def test_a_day_holding_raw_rows_and_a_rollup_is_counted_once(monkeypatch, at_three_am_utc):
    """An open session's rows sit beside a rollup written earlier that day; the week takes the raw."""
    _school(monkeypatch, LA)
    wednesday = [{"user_id": STUDENT, "ts": "2026-06-10T20:00:00+00:00", "focus": 1.0},
                 {"user_id": STUDENT, "ts": "2026-06-10T20:01:00+00:00", "focus": 1.0}]
    tuesday = [{"user_id": STUDENT, "ts": f"2026-06-09T20:0{i}:00+00:00", "focus": f}
               for i, f in enumerate((0.0, 0.0, 0.6))]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_with_rollup(
        cog=wednesday + tuesday,
        rollup=[_rollup("2026-06-09", "cognitive", avg_focus=0.0,
                        sample_count=1, trusted_sample_count=1)])))
    report = main._weekly_signal_report(STUDENT)
    assert _day(report, "2026-06-09")["cognitive_from_rollup"] is False
    # Raw only: 2.6 / 5. The rollup instead of Tuesday's rows gives 0.67; both give 0.43.
    assert report["averages"]["focus"] == 0.52


def _face_and_heart():
    return {"face_signals": [{"user_id": STUDENT, "ts": NOW_UTC.isoformat(), "emotion": "happy"}],
            "heart_signals": [{"user_id": STUDENT, "ts": NOW_UTC.isoformat(),
                               "heart_rate_bpm": 80.0, "trusted": True, "source": "rppg"}]}


def test_a_week_without_eeg_does_not_say_its_other_channels_recorded_nothing(
        monkeypatch, at_three_am_utc):
    """EEG declined, camera allowed: the summary said no facial or heart samples were recorded."""
    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase({**_with_rollup(), **_face_and_heart()}))
    summary = main._weekly_signal_report(STUDENT, eeg_enabled=False)["summary"]
    assert summary == "Facial recognition and heart rate readings were recorded this week."


def test_eeg_rows_with_no_usable_score_are_not_called_absent(monkeypatch, at_three_am_utc):
    """Poor contact nulls focus; the rows arrived, so 'no EEG samples' would be false."""
    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_with_rollup(
        cog=[{"user_id": STUDENT, "ts": NOW_UTC.isoformat(), "focus": None, "stress": None}])))
    summary = main._weekly_signal_report(STUDENT, include_heart=False,
                                         include_emotion=False)["summary"]
    assert summary.startswith("EEG readings were recorded this week, but none gave a usable")
    assert "No EEG" not in summary


def test_eeg_held_only_in_rolled_up_days_is_not_called_absent(monkeypatch, at_three_am_utc):
    """Past expiry the rollup is the record; its rows arrived even with no usable average."""
    _before_the_year(monkeypatch)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_with_rollup(
        rollup=[_rollup("2026-06-09", "cognitive", avg_focus=None, avg_stress=None,
                        sample_count=40, trusted_sample_count=0)])))
    report = main._weekly_signal_report(STUDENT, include_heart=False, include_emotion=False)
    assert _day(report, "2026-06-09")["cognitive_from_rollup"] is True
    assert report["summary"].startswith(
        "EEG readings were recorded this week, but none gave a usable")
    assert "No EEG" not in report["summary"]


# ── body arousal: the rollup's heart categories, with a state for every absence ──

def _heart_rollup(day, counts, sources=("muse_optics",)):
    """As `rollup_signal_day` writes it: `stress_counts` is `jsonb_object_agg`, null with none."""
    return _rollup(day, "heart", avg_heart_rate_bpm=80.0, sample_count=50,
                   trusted_sample_count=40, heart_sources=list(sources), stress_counts=counts)


def _heart_raw(source):
    return [{"user_id": STUDENT, "ts": NOW_UTC.isoformat(), "source": source,
             "heart_rate_bpm": 75.0, "trusted": True}]


def _arousal(monkeypatch, tables, **kw):
    _school(monkeypatch, LA)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(tables, **kw))
    return main._weekly_signal_report(STUDENT)


def test_body_arousal_pools_the_windows_days_and_leaves_calibration_out(monkeypatch,
                                                                       at_three_am_utc):
    report = _arousal(monkeypatch, _with_rollup(rollup=[
        _heart_rollup("2026-06-09", {"calibrating": 6, "high": 1}),
        _heart_rollup("2026-06-10", {"low": 2, "moderate": 1}),
    ]))

    assert report["body_arousal"] == {"high_share": 0.25, "moderate_share": 0.25,
                                      "classified_windows": 4, "calibrating_windows": 6,
                                      "state": "measured", "pending": False,
                                      "few_readings": True}
    assert _day(report, "2026-06-09")["body_arousal"] == 1.0
    assert _day(report, "2026-06-10")["body_arousal"] == 0.0, "a measured 0% is a reading"
    assert _day(report, "2026-06-11")["body_arousal"] is None, "no rollup that day: a gap"


def test_a_day_with_raw_rows_still_takes_its_categories_from_the_rollup(monkeypatch,
                                                                        at_three_am_utc):
    """The raw aggregate has no categories; dropping the rollup beside raw rows loses them."""
    tables = _with_rollup(rollup=[_heart_rollup("2026-06-11", {"high": 3, "low": 1})])
    tables["heart_signals"] = _heart_raw("muse_optics")
    report = _arousal(monkeypatch, tables)

    assert _day(report, "2026-06-11")["body_arousal"] == 0.75
    assert report["body_arousal"]["state"] == "measured"


def _open_session():
    """A lesson still running: the rollup is written only when `ended_at` is set."""
    return {"id": "open-1", "user_id": STUDENT, "started_at": NOW_UTC.isoformat(),
            "ended_at": None}


@pytest.mark.parametrize("rollup,raw_source,open_lesson,state", [
    ([_heart_rollup("2026-06-09", {"calibrating": 6})], None, False, "calibrating"),
    ([], "muse_optics", True, "pending"),
    ([], None, True, "none"),         # a lesson without the heart sensor promises nothing
    ([_heart_rollup("2026-06-09", None)], None, False, "unusable"),
    ([], "rppg", False, "camera_only"),
    ([_heart_rollup("2026-06-09", None, sources=("rppg",))], None, False, "camera_only"),
    ([], None, False, "none"),
])
def test_every_absence_of_body_arousal_says_which_one(monkeypatch, at_three_am_utc,
                                                      rollup, raw_source, open_lesson, state):
    """None of these is a 0% week: each has a share of None and its own reason."""
    tables = _with_rollup(rollup=rollup)
    if raw_source:
        tables["heart_signals"] = _heart_raw(raw_source)
    if open_lesson:
        tables["sessions"] = [_open_session()]
    arousal = _arousal(monkeypatch, tables)["body_arousal"]

    assert arousal["state"] == state
    assert arousal["high_share"] is None
    assert arousal["classified_windows"] == 0


def test_a_failed_rollup_read_leaves_body_arousal_unknown(monkeypatch, at_three_am_utc):
    tables = _with_rollup()
    tables["heart_signals"] = _heart_raw("muse_optics")
    report = _arousal(monkeypatch, tables, table_raises={"signal_daily_rollup"})

    assert report["body_arousal"]["state"] == "not_retrieved"
    assert report["body_arousal"]["high_share"] is None


def test_a_declined_heart_channel_has_no_body_arousal_and_no_heart_read(monkeypatch,
                                                                       at_three_am_utc):
    _school(monkeypatch, LA)
    fake = _FakeSupabase(_with_rollup(rollup=[_heart_rollup("2026-06-09", {"high": 5})]))
    monkeypatch.setattr(main, "supabase", fake)

    report = main._weekly_signal_report(STUDENT, include_heart=False, include_emotion=False)

    assert report["body_arousal"]["state"] == "not_requested"
    assert report["body_arousal"]["high_share"] is None
    assert all(d["body_arousal"] is None for d in report["daily"])
    assert ("channel", ("in", ["cognitive"])) in _rollup_query(fake).filters


def test_each_day_says_why_it_has_no_body_arousal(monkeypatch, at_three_am_utc):
    tables = _with_rollup(rollup=[_heart_rollup("2026-06-09", {"high": 40}),
                                  _heart_rollup("2026-06-10", None, sources=("rppg",))])
    tables["heart_signals"] = _heart_raw("muse_optics")
    tables["sessions"] = [_open_session()]
    report = _arousal(monkeypatch, tables)

    states = {d["date"]: d["body_arousal_state"] for d in report["daily"]}
    assert states["2026-06-09"] == "measured"
    assert states["2026-06-10"] == "camera_only"
    assert states["2026-06-11"] == "pending", "today's headband lesson has not closed"
    assert states["2026-06-08"] == "none"


def test_a_measured_week_beside_an_open_lesson_says_that_lesson_is_not_counted(
        monkeypatch, at_three_am_utc):
    tables = _with_rollup(rollup=[_heart_rollup("2026-06-09", {"high": 40})])
    tables["heart_signals"] = _heart_raw("muse_optics")
    tables["sessions"] = [_open_session()]
    arousal = _arousal(monkeypatch, tables)["body_arousal"]

    assert (arousal["state"], arousal["pending"]) == ("measured", True)


def test_a_running_lesson_without_heart_rows_promises_no_figure(monkeypatch, at_three_am_utc):
    """No heart rows in it, so nothing will arrive "when the lesson finishes"."""
    tables = _with_rollup(rollup=[_heart_rollup("2026-06-09", {"high": 40})])
    tables["sessions"] = [_open_session()]
    assert _arousal(monkeypatch, tables)["body_arousal"]["pending"] is False


def test_a_finished_lesson_earlier_in_the_week_does_not_hide_a_running_one(monkeypatch,
                                                                          at_three_am_utc):
    tables = _with_rollup(rollup=[_heart_rollup("2026-06-11", {"high": 40})])
    tables["heart_signals"] = _heart_raw("muse_optics")
    tables["sessions"] = [_open_session()]
    assert _arousal(monkeypatch, tables)["body_arousal"]["pending"] is True


def test_a_session_open_past_the_abandon_window_is_not_a_running_lesson(monkeypatch,
                                                                         at_three_am_utc):
    """A laptop that slept mid-lesson: open until the sweep, but not in progress."""
    tables = _with_rollup()
    tables["heart_signals"] = _heart_raw("muse_optics")
    old = NOW_UTC - main.timedelta(seconds=main._SESSION_ABANDONED_AFTER_SEC + 60)
    tables["sessions"] = [{**_open_session(), "started_at": old.isoformat()}]
    assert _arousal(monkeypatch, tables)["body_arousal"]["state"] == "unknown"


def test_an_earlier_unsummarised_day_is_not_part_of_todays_lesson(monkeypatch, at_three_am_utc):
    """A failed write on a past day reads unknown, though a lesson is running today."""
    tables = _with_rollup()
    earlier = (NOW_UTC - main.timedelta(days=2)).isoformat()
    tables["heart_signals"] = _heart_raw("muse_optics") + [
        {**_heart_raw("muse_optics")[0], "ts": earlier}]
    tables["sessions"] = [_open_session()]
    report = _arousal(monkeypatch, tables)

    assert _day(report, "2026-06-11")["body_arousal_state"] == "pending"
    assert _day(report, "2026-06-09")["body_arousal_state"] == "unknown"
    assert report["body_arousal"]["state"] == "unknown"


def test_rows_never_summarised_with_no_lesson_open_are_unknown_not_pending(monkeypatch,
                                                                           at_three_am_utc):
    """A failed rollup write or a missed sweep: not an open lesson, and not an absence."""
    tables = _with_rollup()
    tables["heart_signals"] = _heart_raw("muse_optics")
    report = _arousal(monkeypatch, tables)

    assert (report["body_arousal"]["state"], report["body_arousal"]["pending"]) == \
        ("unknown", None)
    assert _day(report, "2026-06-11")["body_arousal_state"] == "unknown"


def test_an_unread_sessions_table_cannot_promise_no_lesson_is_open(monkeypatch, at_three_am_utc):
    """No uncounted rows yet, but a lesson may have just started: the caveat is unknown."""
    tables = _with_rollup(rollup=[_heart_rollup("2026-06-09", {"high": 40})])
    arousal = _arousal(monkeypatch, tables, table_raises={"sessions"})["body_arousal"]

    assert (arousal["state"], arousal["pending"]) == ("measured", None)


def test_a_closed_lesson_is_not_an_open_one(monkeypatch, at_three_am_utc):
    tables = _with_rollup()
    tables["heart_signals"] = _heart_raw("muse_optics")
    tables["sessions"] = [{**_open_session(), "ended_at": NOW_UTC.isoformat()}]
    assert _arousal(monkeypatch, tables)["body_arousal"]["state"] == "unknown"


def test_an_unread_sessions_table_leaves_open_lessons_unknown(monkeypatch, at_three_am_utc):
    tables = _with_rollup()
    tables["heart_signals"] = _heart_raw("muse_optics")
    arousal = _arousal(monkeypatch, tables, table_raises={"sessions"})["body_arousal"]

    assert (arousal["state"], arousal["pending"]) == ("unknown", None)


def test_a_thin_share_is_flagged(monkeypatch, at_three_am_utc):
    thin = _arousal(monkeypatch, _with_rollup(rollup=[
        _heart_rollup("2026-06-09", {"high": main._AROUSAL_MIN_WINDOWS - 1})]))["body_arousal"]
    full = _arousal(monkeypatch, _with_rollup(rollup=[
        _heart_rollup("2026-06-09", {"high": main._AROUSAL_MIN_WINDOWS})]))["body_arousal"]

    assert (thin["few_readings"], full["few_readings"]) == (True, False)


def test_an_open_lesson_with_only_poor_contact_rows_is_pending(monkeypatch, at_three_am_utc):
    """The raw aggregate names trusted sources only, so these rows name no sensor at all."""
    tables = _with_rollup()
    tables["heart_signals"] = [{**_heart_raw("muse_optics")[0], "trusted": False}]
    tables["sessions"] = [_open_session()]
    report = _arousal(monkeypatch, tables)

    assert report["body_arousal"]["state"] == "pending"
    assert _day(report, "2026-06-11")["body_arousal_state"] == "pending"


def test_unrolled_trusted_camera_rows_are_camera_only_not_unknown(monkeypatch, at_three_am_utc):
    """Camera rows never get a category, so a missing rollup row hides nothing about them."""
    tables = _with_rollup()
    tables["heart_signals"] = _heart_raw("rppg")
    assert _arousal(monkeypatch, tables)["body_arousal"]["state"] == "camera_only"


def test_a_failed_raw_heart_read_claims_no_absence(monkeypatch, at_three_am_utc):
    raises = lambda name, p: RuntimeError("x") \
        if name == "weekly_signal_days" and p.get("p_channel") == "heart" else None  # noqa: E731
    report = _arousal(monkeypatch, _with_rollup(), rpc_raises=raises)

    assert (report["body_arousal"]["state"], report["body_arousal"]["pending"]) == \
        ("unknown", None)
    assert {d["body_arousal_state"] for d in report["daily"]} == {"unknown"}


def test_a_failed_raw_heart_read_leaves_a_measured_share_measured(monkeypatch, at_three_am_utc):
    raises = lambda name, p: RuntimeError("x") \
        if name == "weekly_signal_days" and p.get("p_channel") == "heart" else None  # noqa: E731
    arousal = _arousal(monkeypatch, _with_rollup(rollup=[_heart_rollup("2026-06-09", {"high": 40})]),
                       rpc_raises=raises)["body_arousal"]

    assert (arousal["state"], arousal["pending"]) == ("measured", None)
