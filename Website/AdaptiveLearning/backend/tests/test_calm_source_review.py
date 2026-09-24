"""Calm source: what a posted value may do, what a missing calm withdraws, how stress is weighted."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "x")

import LLM_topic_decider as decider  # noqa: E402
import main  # noqa: E402
import signal_fusion  # noqa: E402
from tests.test_calm_source import _row  # noqa: E402
from tests.test_signal_state import CONSENT_ALL, SESSION, USER, _install  # noqa: E402
from tests.test_signal_trend import NOW_UTC, STUDENT, _fake, _rollup, _week  # noqa: E402

MIGRATION = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "supabase",
                         "migrations", "20260918000000_rollup_stress_sample_count.sql")


def _rows(*raws, stress=0.70):
    """calm 0.30: stressed on the SDK line (0.377), not on the local (0.25)."""
    return [{"session_id": SESSION, "focus": 0.4, "stress": stress,
             "engagement": 0.4, "raw": r} for r in raws]


def test_an_unhashable_calm_source_is_not_a_source_and_does_not_raise(monkeypatch):
    """`raw` is client-supplied on push; a dict must not be used as a set element or key."""
    _install(monkeypatch, CONSENT_ALL,
             eeg=_rows({"confidence": 0.9, "calm_source": {"a": 1}},
                       {"confidence": 0.9, "calm_source": ["local"]}))
    assert decider.get_session_signal_state(SESSION, USER).label == "stressed", \
        "garbage names no source; the remaining rows are sdk"
    row = _row(calm_source={"a": 1})
    assert "calm_source" not in row["raw"] and row["raw"]["score_scale"] == 2


def test_a_window_without_calm_withdraws_calm_and_keeps_the_rest_of_the_channel():
    """Focus and confidence don't depend on the calm source, so only calm is withdrawn."""
    ch = signal_fusion.eeg_channel(0.05, None, 0.9)
    assert ch.label == "neutral" and ch.cause == "no_calm"
    assert signal_fusion.eeg_channel(0.9, None, 0.9).label == "neutral", "focused needs calm"
    assert signal_fusion.eeg_channel(0.05, None, 0.2).cause == "low_confidence"
    assert signal_fusion.eeg_channel(None, 0.5, 0.9).cause == "no_samples"


def test_a_mixed_source_window_is_read_not_dropped(monkeypatch):
    mixed = _rows({"confidence": 0.9, "calm_source": "local"},
                  {"confidence": 0.9, "calm_source": "sdk"})
    _install(monkeypatch, CONSENT_ALL, eeg=mixed)
    state = decider.get_session_signal_state(SESSION, USER)
    assert state.label == "neutral" and state.focus == pytest.approx(0.4)
    low = [{**r, "raw": {**r["raw"], "confidence": 0.2}} for r in mixed]
    _install(monkeypatch, CONSENT_ALL, eeg=low)
    assert decider.get_session_signal_state(SESSION, USER).label == "insufficient_signal", \
        "the contact gate still applies to a window whose calm is on two scales"


def test_stress_is_weighted_by_the_rows_that_carried_one(monkeypatch):
    assert main._stress_weight({"trusted_sample_count": 4000, "stress_sample_count": 200}) == 200
    assert main._stress_weight({"trusted_sample_count": 4000}) == 4000, \
        "a row rolled before the column keeps the old approximation"
    monkeypatch.setattr(main, "_utc_now", lambda: NOW_UTC)
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": main.WINDOW_OPEN, "starts_on": "2000-01-01",
        "ends_on": "2099-12-31", "timezone": "UTC"})
    monkeypatch.setattr(main, "supabase", _fake(rollup=[
        _rollup("2026-06-08", "cognitive", avg_focus=0.5, avg_stress=0.70,
                trusted_sample_count=4000, stress_sample_count=200),
        _rollup("2026-06-09", "cognitive", avg_focus=0.5, avg_stress=0.30,
                trusted_sample_count=200, stress_sample_count=200),
    ]))
    week = _week(main._signal_trend(STUDENT, weeks=1), "2026-06-08")
    assert week["stress"] == pytest.approx(0.5, abs=1e-4)
    merged = main._merge_cohort_trend([[
        {"day": "2026-06-08", "channel": "cognitive", "avg_focus": 0.5, "avg_stress": 0.70,
         "sample_count": 4000, "trusted_sample_count": 4000, "stress_sample_count": 200,
         "student_count": 1},
    ], [
        {"day": "2026-06-08", "channel": "cognitive", "avg_focus": 0.5, "avg_stress": 0.30,
         "sample_count": 200, "trusted_sample_count": 200, "stress_sample_count": 200,
         "student_count": 1},
    ]])
    assert merged[0]["avg_stress"] == pytest.approx(0.5, abs=1e-4)


def test_the_rollup_writes_the_stress_count_and_the_rpcs_weight_on_it():
    with open(MIGRATION, encoding="utf-8") as fh:
        sql = fh.read()
    assert "count(*) FILTER (WHERE stress IS NOT NULL)" in sql
    # Two per RPC's avg_stress plus the trend's summed count; arithmetic is in assert_signal_rls.sql.
    assert sql.count('COALESCE("r"."stress_sample_count", "r"."trusted_sample_count")') == 5
    rls = os.path.join(os.path.dirname(MIGRATION), "..", "..", "scripts", "assert_signal_rls.sql")
    with open(rls, encoding="utf-8") as fh:
        assert "class_signal_student_totals(ARRAY[owner_id]" in fh.read()


def test_the_row_says_whether_each_score_was_centred_on_the_session():
    r = _row(calm_source="local", focus_centred=True, calm_centred=False)
    assert r["raw"]["focus_centred"] is True and r["raw"]["calm_centred"] is False
    r = _row(calm_source="local", focus_centred="yes", calm_centred=1)
    assert "focus_centred" not in r["raw"] and "calm_centred" not in r["raw"]
    assert "calm_centred" not in _row()["raw"], "an older sidecar sends neither"


def test_the_hold_cap_matches_the_sidecar():
    """Pinned as a literal on both sides, like the stressed line."""
    import signal_mapping
    assert signal_mapping.CALM_HOLD_MAX_SECONDS == 10.0


def test_the_two_keys_that_gate_stress_are_validated_like_the_source():
    """A value present but not the sidecar's type withholds stress rather than recording it."""
    assert _row(calm_source="local", calm_measured="false")["stress"] is None
    assert _row(calm_source="local", calm_held_seconds="150")["stress"] is None
    assert _row(calm_source="local", calm_held_seconds=float("nan"))["stress"] is None
    assert _row(calm_source="local", calm_held_seconds=True)["stress"] is None
    r = _row(calm_source="local", calm_measured=1, calm_held_seconds=[1])
    assert r["stress"] is None
    assert "calm_measured" not in r["raw"] and "calm_held_seconds" not in r["raw"]
    assert r["raw"]["calm_invalid"] == ["calm_measured", "calm_held_seconds"], \
        "a rejected key is recorded, or it reads as an older sidecar that sent none"
    assert _row(calm_source="local", calm_measured="false")["raw"]["calm_invalid"] == ["calm_measured"]
    assert "calm_invalid" not in _row(calm_source="local", calm_measured=True)["raw"]
    assert _row(calm_source="local", calm_measured=True, calm_held_seconds=2)["stress"] == pytest.approx(0.5)
    assert _row(calm_source="local")["stress"] == pytest.approx(0.5), "absent is an older sidecar"
