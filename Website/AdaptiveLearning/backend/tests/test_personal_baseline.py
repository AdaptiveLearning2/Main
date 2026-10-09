"""A child compared with their own usual: the daily-rollup baseline behind every report tile."""
import os
from datetime import date, datetime, timezone

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import main  # noqa: E402
from tests.test_access_control import _FakeSupabase  # noqa: E402

STUDENT = "student-1"
# A Thursday. A 7-day report covers 2026-06-05..11; the usual is built from the 8 weeks before.
NOW_UTC = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)
START, END = date(2026, 6, 5), date(2026, 6, 11)
# Five prior school days across two weeks: the least that may form a usual.
PRIOR = ["2026-05-25", "2026-05-26", "2026-05-27", "2026-06-01", "2026-06-02"]


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch):
    monkeypatch.setattr(main, "_utc_now", lambda: NOW_UTC)
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": main.WINDOW_OPEN, "starts_on": "2000-01-01",
        "ends_on": "2099-12-31", "timezone": "UTC"})


def _cog(day, focus=0.5, stress=0.4, n=100, scale=3, calm=("sdk",)):
    """A cognitive row with the columns `rollup_signal_day` writes."""
    return {"user_id": STUDENT, "day": day, "channel": "cognitive",
            "avg_focus": focus, "avg_stress": stress, "trusted_sample_count": n,
            "stress_sample_count": n, "sample_count": n,
            "score_scale_min": scale, "score_scale_max": scale, "calm_sources": list(calm)}


def _heart(day, bpm=75.0, rmssd=40.0, counts=None, n=100, sources=("muse_optics",)):
    return {"user_id": STUDENT, "day": day, "channel": "heart",
            "avg_heart_rate_bpm": bpm, "avg_rmssd_ms": rmssd, "trusted_sample_count": n,
            "sample_count": n, "heart_sources": list(sources), "stress_counts": counts}


def _fake(rows, **kw):
    return _FakeSupabase({"signal_daily_rollup": list(rows)}, **kw)


def _baseline(monkeypatch, rows, current=None, include_heart=True, **kw):
    fake = _fake(rows, **kw)
    monkeypatch.setattr(main, "supabase", fake)
    current = current or {"focus": (0.5, 500), "calm": (0.6, 500)}
    return main._personal_baseline(STUDENT, START, END, current, include_heart), fake


def _now_cog(**kw):
    """A current-period row, which carries the period's score scale and calm source."""
    return _cog("2026-06-09", **kw)


def _rollup_query(fake):
    return next(q for name, q in zip(fake.table_calls, fake.queries)
                if name == "signal_daily_rollup")


# ─── which days form the usual ──────────────────────────────────────────

def test_the_current_period_is_not_part_of_its_own_usual(monkeypatch):
    rows = [_cog(d, focus=0.5) for d in PRIOR] + [_now_cog(focus=0.99, n=5000)]
    focus = _baseline(monkeypatch, rows)[0]["measures"]["focus"]

    assert focus["median"] == 0.5
    assert focus["days_used"] == 5


def test_a_thin_day_is_left_out(monkeypatch):
    rows = [_cog(d, focus=0.5) for d in PRIOR] + [_cog("2026-06-03", focus=0.9, n=59), _now_cog()]
    focus = _baseline(monkeypatch, rows)[0]["measures"]["focus"]

    assert focus["days_used"] == 5
    assert focus["high"] < 0.9


def test_a_day_on_another_score_scale_is_left_out(monkeypatch):
    rows = [_cog(d, focus=0.5) for d in PRIOR] + [_cog("2026-06-03", focus=0.9, scale=2),
                                                   _now_cog()]
    measures = _baseline(monkeypatch, rows)[0]["measures"]

    assert measures["focus"]["days_used"] == 5
    assert measures["calm"]["days_used"] == 5


def test_another_calm_source_leaves_the_day_out_of_calm_only(monkeypatch):
    """Two calm sources score stress in different units; focus is unaffected by which."""
    rows = [_cog(d) for d in PRIOR] + [_cog("2026-06-03", calm=("local",)), _now_cog()]
    measures = _baseline(monkeypatch, rows)[0]["measures"]

    assert measures["calm"]["days_used"] == 5
    assert measures["focus"]["days_used"] == 6


def test_calm_is_one_minus_the_stored_stress(monkeypatch):
    rows = [_cog(d, stress=0.3) for d in PRIOR] + [_now_cog(stress=0.3)]
    calm = _baseline(monkeypatch, rows)[0]["measures"]["calm"]

    assert calm["median"] == pytest.approx(0.7)


# ─── how much history is enough ─────────────────────────────────────────

def test_four_days_is_not_enough_history(monkeypatch):
    rows = [_cog(d) for d in PRIOR[:4]] + [_now_cog()]
    focus = _baseline(monkeypatch, rows)[0]["measures"]["focus"]

    assert focus["status"] == "not_enough_history"
    assert focus["days_used"] == 4
    assert focus["verdict"] is None and focus["median"] is None


def test_five_days_inside_one_week_is_not_enough_history(monkeypatch):
    rows = [_cog(f"2026-05-2{i}") for i in range(5, 10)] + [_now_cog()]
    focus = _baseline(monkeypatch, rows)[0]["measures"]["focus"]

    assert focus["status"] == "not_enough_history"
    assert (focus["days_used"], focus["weeks_used"]) == (5, 1)


def test_five_days_across_two_weeks_is_enough(monkeypatch):
    rows = [_cog(d) for d in PRIOR] + [_now_cog()]
    focus = _baseline(monkeypatch, rows)[0]["measures"]["focus"]

    assert focus["status"] == "compared"
    assert (focus["days_used"], focus["weeks_used"]) == (5, 2)


# ─── the statistic and the verdict ──────────────────────────────────────

def test_the_usual_is_a_median_of_days_not_weighted_by_samples(monkeypatch):
    """A 4000-sample day counts once, like a 60-sample one: the range is day-to-day spread."""
    values = [(0.4, 60), (0.4, 60), (0.4, 60), (0.9, 4000), (0.9, 4000)]
    rows = [_cog(d, focus=f, n=n) for d, (f, n) in zip(PRIOR, values)] + [_now_cog()]
    focus = _baseline(monkeypatch, rows)[0]["measures"]["focus"]

    assert focus["median"] == 0.4


@pytest.mark.parametrize("current,verdict", [(0.654, "about_usual"), (0.656, "higher"),
                                             (0.546, "about_usual"), (0.544, "lower")])
def test_the_verdict_compares_what_the_tile_prints(monkeypatch, current, verdict):
    """65.4% beside "usual 55–65%" must read as usual, or the reader cannot check it."""
    values = [0.55, 0.55, 0.60, 0.65, 0.65]
    rows = [_cog(d, focus=f) for d, f in zip(PRIOR, values)] + [_now_cog()]
    focus = _baseline(monkeypatch, rows, {"focus": (current, 500)})[0]["measures"]["focus"]

    assert (focus["low"], focus["high"]) == (0.55, 0.65)
    assert focus["verdict"] == verdict


def test_a_narrow_usual_is_widened_so_a_point_is_not_a_change(monkeypatch):
    rows = [_cog(d, focus=0.5) for d in PRIOR] + [_now_cog()]
    focus = _baseline(monkeypatch, rows, {"focus": (0.52, 500)})[0]["measures"]["focus"]

    assert (focus["low"], focus["high"]) == (0.47, 0.53)
    assert focus["verdict"] == "about_usual"


# ─── states that are not a comparison ───────────────────────────────────

def test_too_few_samples_this_period_is_its_own_state(monkeypatch):
    rows = [_cog(d) for d in PRIOR] + [_now_cog()]
    focus = _baseline(monkeypatch, rows, {"focus": (0.9, 10)})[0]["measures"]["focus"]

    assert focus["status"] == "too_little_this_period"
    assert focus["verdict"] is None


def test_a_period_that_spans_a_scale_change_is_not_compared(monkeypatch):
    rows = [_cog(d) for d in PRIOR] + [_cog("2026-06-08", scale=2), _now_cog(scale=3)]
    focus = _baseline(monkeypatch, rows)[0]["measures"]["focus"]

    assert (focus["status"], focus["reason"]) == ("not_comparable", "mixed_scale")


def test_a_period_whose_rolled_days_carry_no_scale_is_not_compared(monkeypatch):
    rows = [_cog(d) for d in PRIOR] + [_now_cog(scale=None)]
    focus = _baseline(monkeypatch, rows)[0]["measures"]["focus"]

    assert (focus["status"], focus["reason"]) == ("not_comparable", "scale_unknown")


def test_a_period_with_nothing_rolled_up_yet_is_pending_not_changed(monkeypatch):
    """An open lesson's scale is not known until it closes: unknown, never "changed"."""
    rows = [_cog(d) for d in PRIOR] + [_heart(d) for d in PRIOR]
    measures = _baseline(monkeypatch, rows, {"focus": (0.5, 500),
                                             "heart_rate_bpm": (75.0, 500)})[0]["measures"]

    for m in ("focus", "heart_rate_bpm"):
        assert (measures[m]["status"], measures[m]["reason"]) == \
            ("pending", "period_not_summarised")


def test_the_reports_own_heart_sensors_stand_in_for_an_unrolled_period(monkeypatch):
    """The weekly report knows today's sensor from raw rows, so heart can be compared."""
    rows = [_heart(d, bpm=70.0) for d in PRIOR]
    bpm = _baseline(monkeypatch, rows, {"heart_rate_bpm": (80.0, 500),
                                        "heart_sources": ["muse_optics"]})[0]["measures"][
        "heart_rate_bpm"]

    assert bpm["status"] == "compared"


@pytest.mark.parametrize("unread,channel_measures", [
    (["cognitive"], ("focus", "calm")), (["heart"], ("heart_rate_bpm", "rmssd_ms"))])
def test_a_figure_that_failed_to_load_is_not_a_quiet_period(monkeypatch, unread,
                                                             channel_measures):
    rows = [_cog(d) for d in PRIOR] + [_now_cog()]
    current = {"focus": (None, 0), "heart_rate_bpm": (None, 0), "unread": unread}
    out = _baseline(monkeypatch, rows, current)[0]

    assert out["retrieved"] is True, "the rollup read worked; the period's figure did not"
    for m in channel_measures:
        assert (out["measures"][m]["status"], out["measures"][m]["reason"]) == \
            ("not_retrieved", "current_unread")


def test_a_failed_summary_read_marks_its_figures_unread():
    assert main._usual_current({"retrieved": False})["unread"] == ["cognitive", "heart"]
    assert main._usual_current({"retrieved": True})["unread"] == []


def test_the_usual_carries_body_arousal_with_its_reason(monkeypatch):
    rows = [_heart("2026-06-09", counts=None)]
    out = _baseline(monkeypatch, rows, {"heart_rate_bpm": (75.0, 500)})[0]

    assert out["body_arousal"]["state"] == "unusable"
    assert out["body_arousal"]["high_share"] is None


@pytest.mark.parametrize("value,measure,shown", [(0.125, "focus", 13), (72.5, "heart_rate_bpm", 73)])
def test_a_half_is_rounded_up_as_the_page_rounds_it(value, measure, shown):
    """`Math.round(12.5)` is 13; Python's `round` gives 12, and the verdict would disagree."""
    assert main._usual_display(value, measure) == shown


def test_no_reading_this_period_is_not_a_comparison(monkeypatch):
    rows = [_cog(d) for d in PRIOR] + [_now_cog()]
    focus = _baseline(monkeypatch, rows, {"focus": (None, 0)})[0]["measures"]["focus"]

    assert focus["status"] == "no_current"


# ─── heart measures ─────────────────────────────────────────────────────

def test_heart_days_from_another_sensor_are_left_out(monkeypatch):
    """Camera and headband rates differ in accuracy, so one is no usual for the other."""
    rows = [_heart(d, bpm=90.0, sources=("rppg",)) for d in PRIOR] + [_heart("2026-06-09")]
    bpm = _baseline(monkeypatch, rows, {"heart_rate_bpm": (75.0, 500)})[0]["measures"][
        "heart_rate_bpm"]

    assert bpm["status"] == "not_enough_history"
    assert bpm["days_used"] == 0


def test_heart_rate_and_variability_are_compared_with_their_own_days(monkeypatch):
    rows = [_heart(d, bpm=70.0, rmssd=50.0) for d in PRIOR] + [_heart("2026-06-09")]
    measures = _baseline(monkeypatch, rows, {"heart_rate_bpm": (80.0, 500),
                                             "rmssd_ms": (30.0, 500)})[0]["measures"]

    assert (measures["heart_rate_bpm"]["verdict"], measures["rmssd_ms"]["verdict"]) == \
        ("higher", "lower")


def test_body_arousal_this_period_comes_from_the_rollup(monkeypatch):
    rows = [_heart(d, counts={"low": 90, "high": 10}) for d in PRIOR] + [
        _heart("2026-06-09", counts={"low": 20, "high": 20, "calibrating": 6})]
    arousal = _baseline(monkeypatch, rows)[0]["measures"]["body_arousal"]

    assert arousal["current"] == 0.5
    assert arousal["median"] == pytest.approx(0.1)
    assert arousal["verdict"] == "higher"


def test_a_declined_heart_channel_is_not_queried(monkeypatch):
    rows = [_heart(d) for d in PRIOR] + [_cog(d) for d in PRIOR] + [_now_cog()]
    out, fake = _baseline(monkeypatch, rows, {"heart_rate_bpm": (75.0, 500)},
                          include_heart=False)

    assert ("channel", ("in", ["cognitive"])) in _rollup_query(fake).filters
    assert {out["measures"][m]["status"] for m in main._USUAL_HEART} == {"not_requested"}


def test_a_failed_read_is_not_a_short_history(monkeypatch):
    out, _ = _baseline(monkeypatch, [], table_raises={"signal_daily_rollup"})

    assert out["retrieved"] is False
    assert {m["status"] for m in out["measures"].values()} == {"not_retrieved"}


def test_the_read_names_its_columns(monkeypatch):
    _, fake = _baseline(monkeypatch, [_now_cog()])

    assert "*" not in ",".join(_rollup_query(fake)._cols)
    assert "stress_counts" in _rollup_query(fake)._cols


# ─── the endpoints ──────────────────────────────────────────────────────

def _refuse(*_a):
    raise HTTPException(403, "no")


@pytest.mark.parametrize("handler", [main.student_weekly_report, main.student_signal_summary])
def test_a_refused_viewer_reaches_no_rollup(monkeypatch, handler):
    fake = _fake([_cog(d) for d in PRIOR])
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "stranger"})
    monkeypatch.setattr(main, "_verify_can_view_student", _refuse)

    with pytest.raises(HTTPException):
        handler(STUDENT, None)
    assert "signal_daily_rollup" not in fake.table_calls


def test_the_weekly_report_carries_the_usual_for_its_own_averages(monkeypatch):
    rows = [_cog(d, focus=0.4) for d in PRIOR] + [_now_cog(focus=0.8)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase({
        "signal_daily_rollup": rows, "profiles": [{"id": STUDENT, "display_name": "Ana"}],
        "signal_consent": [{"user_id": STUDENT, "eeg_enabled": True}],
        "cognitive_signals": [], "face_signals": [], "heart_signals": [], "sessions": []}))
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": STUDENT})
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a: None)

    report = main.student_weekly_report(STUDENT, None)

    focus = report["usual"]["measures"]["focus"]
    assert focus["current"] == report["averages"]["focus"] == 0.8
    assert report["usual"]["measures"]["heart_rate_bpm"]["status"] == "not_requested"


def _weekly_tables(rows, cog=()):
    return {"signal_daily_rollup": rows, "profiles": [{"id": STUDENT, "display_name": "Ana"}],
            "signal_consent": [{"user_id": STUDENT, "eeg_enabled": True}],
            "cognitive_signals": list(cog), "face_signals": [], "heart_signals": [],
            "sessions": []}


def _weekly(monkeypatch, tables, **kw):
    monkeypatch.setattr(main, "supabase", _FakeSupabase(tables, **kw))
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": STUDENT})
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a: None)
    return main.student_weekly_report(STUDENT, None)


def test_the_weekly_floor_counts_usable_readings_not_poor_contact_rows(monkeypatch):
    """100 rows with 10 usable focus readings is too little, as `/signal-summary` counts it."""
    ts = NOW_UTC.isoformat()
    cog = [{"user_id": STUDENT, "ts": ts, "focus": 0.6 if i < 10 else None, "stress": None}
           for i in range(100)]
    report = _weekly(monkeypatch, _weekly_tables([_cog(d) for d in PRIOR], cog))

    assert report["average_samples"]["focus"] == 10
    assert report["usual"]["measures"]["focus"]["status"] == "too_little_this_period"


def test_the_weekly_usual_knows_an_open_lesson_is_not_counted(monkeypatch):
    """Only the report sees today's raw headband rows; the usual must carry that through."""
    tables = _weekly_tables([_heart("2026-06-09", counts={"high": 40})])
    tables["signal_consent"] = [{"user_id": STUDENT, "eeg_enabled": True,
                                 "headband_optical_enabled": True}]
    tables["heart_signals"] = [{"user_id": STUDENT, "ts": NOW_UTC.isoformat(),
                                "source": "muse_optics", "heart_rate_bpm": 75.0,
                                "trusted": True}]
    tables["sessions"] = [{"id": "open-1", "user_id": STUDENT,
                           "started_at": NOW_UTC.isoformat(), "ended_at": None}]
    report = _weekly(monkeypatch, tables)

    assert report["body_arousal"]["pending"] is True
    assert report["usual"]["body_arousal"]["pending"] is True


def _summary_endpoint(monkeypatch, sessions, heart_samples=0, any_rows=True):
    """`/signal-summary` for a heart-consented student with no rolled heart days."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase({
        "signal_daily_rollup": [_cog(d) for d in PRIOR], "sessions": sessions}))
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": STUDENT})
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a: None)
    monkeypatch.setattr(main, "_reportable_channels", lambda *_a: main._channels_from_consent(
        {"retrieved": True, "eeg_enabled": True, "headband_optical_enabled": True}))
    monkeypatch.setattr(main, "_signal_summary", lambda *_a, **_k: {
        **main._EMPTY_SUMMARY, "heart_samples": heart_samples})
    monkeypatch.setattr(main, "_any_rows_since", lambda table, *_a: any_rows)
    return main.student_signal_summary(STUDENT, None)["usual"]["body_arousal"]


_OPEN = [{"id": "open-1", "user_id": STUDENT, "started_at": NOW_UTC.isoformat(),
          "ended_at": None}]


def test_the_summary_sees_a_poor_contact_open_lesson_as_pending(monkeypatch):
    """The summary counts usable samples only; the open lesson and its rows still exist."""
    assert _summary_endpoint(monkeypatch, _OPEN)["state"] == "pending"


@pytest.mark.parametrize("sessions,any_rows,state", [
    ([], True, "unknown"),        # rows, no lesson open, nothing rolled: never summarised
    ([], None, "unknown"),        # whether rows arrived could not be read
    ([], False, "none"),
])
def test_the_summary_never_calls_unexplained_rows_an_absence(monkeypatch, sessions, any_rows,
                                                             state):
    assert _summary_endpoint(monkeypatch, sessions, any_rows=any_rows)["state"] == state


def test_the_parent_dashboard_sees_an_open_lesson_too(monkeypatch):
    monkeypatch.setattr(main, "supabase", _FakeSupabase({
        "parent_child_links": [{"parent_id": "p", "child_id": "a", "created_at": "2026-06-01"}],
        "signal_daily_rollup": [], "sessions": [{**_OPEN[0], "user_id": "a"}]}))
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "p"})
    channels = main._channels_from_consent(
        {"retrieved": True, "eeg_enabled": True, "headband_optical_enabled": True})
    monkeypatch.setattr(main, "_reportable_channels_many",
                        lambda ids, *_a: {k: channels for k in ids})
    monkeypatch.setattr(main, "_signal_summaries", lambda ids, **_k: {
        k: {**main._EMPTY_SUMMARY, "heart_samples": 0} for k in ids})
    for name in ("_stats_including_open_session_many", "_profiles_many",
                 "_topic_performance_many"):
        monkeypatch.setattr(main, name, lambda ids: {})
    monkeypatch.setattr(main, "_recent_sessions_many", lambda ids, n: {})
    monkeypatch.setattr(main, "_any_rows_since", lambda *_a: True)

    child = main.my_children(None)[0]

    assert child["signal_summary"]["usual"]["body_arousal"]["state"] == "pending"


def test_the_weekly_report_compares_heart_while_todays_lesson_is_open(monkeypatch):
    """Today's raw headband rows name the sensor, so heart is compared, not left pending."""
    tables = _weekly_tables([_heart(d, bpm=70.0) for d in PRIOR])
    tables["signal_consent"] = [{"user_id": STUDENT, "eeg_enabled": True,
                                 "headband_optical_enabled": True}]
    tables["heart_signals"] = [{"user_id": STUDENT, "ts": NOW_UTC.isoformat(),
                                "source": "muse_optics", "heart_rate_bpm": 90.0,
                                "trusted": True} for _ in range(40)]
    bpm = _weekly(monkeypatch, tables)["usual"]["measures"]["heart_rate_bpm"]

    assert (bpm["status"], bpm["verdict"]) == ("compared", "higher")


def test_a_failed_weekly_eeg_read_is_unread_in_the_usual(monkeypatch):
    report = _weekly(monkeypatch, _weekly_tables([_cog(d) for d in PRIOR] + [_now_cog()]),
                     rpc_raises=lambda name, p: RuntimeError("x")
                     if name == "weekly_signal_days" and p.get("p_channel") == "cognitive"
                     else None)

    assert report["retrieved"]["cognitive"] is False
    focus = report["usual"]["measures"]["focus"]
    assert (focus["status"], focus["reason"]) == ("not_retrieved", "current_unread")


def test_the_signal_summary_carries_the_usual_with_calm_inverted(monkeypatch):
    rows = [_cog(d, stress=0.4) for d in PRIOR] + [_now_cog()]
    monkeypatch.setattr(main, "supabase", _fake(rows))
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": STUDENT})
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a: None)
    monkeypatch.setattr(main, "_reportable_channels", lambda *_a: main._channels_from_consent(
        {"retrieved": True, "eeg_enabled": True}))
    monkeypatch.setattr(main, "_signal_summary", lambda *_a, **_k: {
        **main._EMPTY_SUMMARY, "focus": 0.5, "stress": 0.25, "cognitive_samples": 500,
        "heart_included": False})

    calm = main.student_signal_summary(STUDENT, None)["usual"]["measures"]["calm"]

    assert calm["current"] == 0.75
    assert calm["median"] == pytest.approx(0.6)


# ─── many students at once (the parent dashboard) ───────────────────────

def _kid_rows(kid, focus=0.5):
    return [{**_cog(d, focus=focus), "user_id": kid} for d in PRIOR] + \
        [{**_now_cog(), "user_id": kid}]


def _many(monkeypatch, rows, kids, heart=None, **kw):
    fake = _fake(rows, **kw)
    monkeypatch.setattr(main, "supabase", fake)
    out = main._personal_baselines_many(
        {k: {"focus": (0.5, 500)} for k in kids},
        {k: (heart or {}).get(k, False) for k in kids}, {k: True for k in kids}, START, END)
    return out, fake


def _rollup_queries(fake):
    return [q for name, q in zip(fake.table_calls, fake.queries) if name == "signal_daily_rollup"]


def _filter(q, col):
    return next(v for c, v in q.filters if c == col)


def test_a_siblings_heart_consent_never_widens_another_childs_read(monkeypatch):
    rows = _kid_rows("a") + _kid_rows("b") + [{**_heart(d), "user_id": "b"} for d in PRIOR]
    out, fake = _many(monkeypatch, rows, ["a", "b"], heart={"a": True, "b": False})

    by_kids = {tuple(_filter(q, "user_id")[1]): _filter(q, "channel")[1]
               for q in _rollup_queries(fake)}
    assert by_kids == {("a",): ["cognitive", "heart"], ("b",): ["cognitive"]}
    assert out["b"]["measures"]["heart_rate_bpm"]["status"] == "not_requested"


def test_each_child_is_compared_with_their_own_rows_only(monkeypatch):
    out, _ = _many(monkeypatch, _kid_rows("a") + [{**_now_cog(), "user_id": "b"}], ["a", "b"])

    assert out["a"]["measures"]["focus"]["status"] == "compared"
    assert out["b"]["measures"]["focus"]["days_used"] == 0


@pytest.mark.parametrize("heart", [False, True])
def test_children_are_read_in_chunks_that_fit_under_the_row_cap(monkeypatch, heart):
    size = main._usual_batch_size(START, END, heart)
    days = (END - (START - main.timedelta(weeks=main._USUAL_WEEKS))).days + 1
    assert size * days * (2 if heart else 1) < main._POSTGREST_MAX_ROWS
    assert (size + 1) * days * (2 if heart else 1) >= main._POSTGREST_MAX_ROWS
    kids = [f"k{i}" for i in range(size + 1)]
    _, fake = _many(monkeypatch, [], kids, heart={k: heart for k in kids})

    sizes = [len(_filter(q, "user_id")[1]) for q in _rollup_queries(fake)]
    assert sizes == [size, 1]


class _ChunkQuery:
    """The rollup chain, answering per chunk: one id fails, or one chunk is at the cap."""

    def __init__(self, fail_id=None, cap_id=None):
        self.fail_id, self.cap_id, self.ids = fail_id, cap_id, []

    def in_(self, col, values):
        self.ids = list(values)
        return self

    def execute(self):
        if self.fail_id in self.ids:
            raise RuntimeError("chunk read failed")
        rows = [r for k in self.ids for r in _kid_rows(k)]
        if self.cap_id in self.ids:
            rows = (rows * 200)[:main._POSTGREST_MAX_ROWS]
        return type("R", (), {"data": rows})()


@pytest.mark.parametrize("trouble", ["fail_id", "cap_id"])
def test_a_failed_or_capped_chunk_marks_only_its_own_children(monkeypatch, trouble):
    size = main._usual_batch_size(START, END, False)
    kids = [f"k{i}" for i in range(size + 1)]
    last = kids[-1]
    monkeypatch.setattr(main, "_usual_rollup_query",
                        lambda *a: _ChunkQuery(**{trouble: last}))
    out = main._personal_baselines_many({k: {"focus": (0.5, 500)} for k in kids},
                                        {k: False for k in kids}, {k: True for k in kids},
                                        START, END)

    assert out[last]["retrieved"] is False
    assert out[last]["measures"]["focus"]["status"] == "not_retrieved"
    assert out["k0"]["retrieved"] is True
    assert out["k0"]["measures"]["focus"]["status"] == "compared"


def test_the_parent_dashboard_stamps_each_childs_usual(monkeypatch):
    kids = ["a", "b"]
    monkeypatch.setattr(main, "supabase", _FakeSupabase({
        "parent_child_links": [{"parent_id": "p", "child_id": k, "created_at": "2026-06-01"}
                               for k in kids],
        "signal_daily_rollup": _kid_rows("a", focus=0.4) + _kid_rows("b", focus=0.8)}))
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "p"})
    channels = main._channels_from_consent({"retrieved": True, "eeg_enabled": True})
    monkeypatch.setattr(main, "_reportable_channels_many",
                        lambda ids, *_a: {k: channels for k in ids})
    monkeypatch.setattr(main, "_signal_summaries", lambda ids, **_k: {
        k: {**main._EMPTY_SUMMARY, "focus": 0.6, "cognitive_samples": 500} for k in ids})
    for name in ("_stats_including_open_session_many", "_profiles_many",
                 "_topic_performance_many"):
        monkeypatch.setattr(main, name, lambda ids: {})
    monkeypatch.setattr(main, "_recent_sessions_many", lambda ids, n: {})

    children = {c["user_id"]: c for c in main.my_children(None)}

    assert children["a"]["signal_summary"]["usual"]["measures"]["focus"]["verdict"] == "higher"
    assert children["b"]["signal_summary"]["usual"]["measures"]["focus"]["verdict"] == "lower"
