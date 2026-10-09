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


def test_a_period_with_no_known_scale_is_not_compared(monkeypatch):
    rows = [_cog(d) for d in PRIOR]
    focus = _baseline(monkeypatch, rows)[0]["measures"]["focus"]

    assert (focus["status"], focus["reason"]) == ("not_comparable", "scale_unknown")


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
