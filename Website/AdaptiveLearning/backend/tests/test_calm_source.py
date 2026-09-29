"""Calm has two sources (SDK, local) on two scales, and every reader has to know which."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "x")

import LLM_topic_decider as decider  # noqa: E402
import signal_fusion  # noqa: E402
import signal_mapping  # noqa: E402
from tests.test_signal_state import CONSENT_ALL, SESSION, USER, _install  # noqa: E402


def test_the_stressed_line_follows_the_calm_source_and_matches_the_sidecar():
    assert signal_fusion.EEG_STRESSED_CALM_MAX_BY_SOURCE == {"sdk": 0.377, "local": 0.25}
    assert signal_fusion.eeg_channel(0.4, 0.30, 0.9).label == "stressed"
    assert signal_fusion.eeg_channel(0.4, 0.30, 0.9, calm_source="local").label == "neutral"
    assert signal_fusion.eeg_channel(0.4, 0.20, 0.9, calm_source="local").label == "stressed"
    assert signal_fusion.eeg_channel(0.4, 0.30, 0.9, calm_source="martian").label == "stressed", \
        "an unknown source takes the SDK line, which every older row was scored on"


def test_the_decider_picks_the_stressed_line_by_the_rows_calm_source(monkeypatch):
    rows = [{"session_id": SESSION, "focus": 0.4, "stress": 0.70,
             "engagement": 0.4, "raw": {"confidence": 0.9, "calm_source": "local"}}]
    _install(monkeypatch, CONSENT_ALL, eeg=rows)
    assert decider.get_session_signal_state(SESSION, USER).label == "neutral"
    _install(monkeypatch, CONSENT_ALL, eeg=[{**rows[0], "raw": {"confidence": 0.9}}])
    assert decider.get_session_signal_state(SESSION, USER).label == "stressed", "no key is sdk"
    # Two sources in one window are two scales: calm is no opinion.
    mixed = [rows[0], {**rows[0], "raw": {"confidence": 0.9, "calm_source": "sdk"}}]
    _install(monkeypatch, CONSENT_ALL, eeg=mixed)
    assert decider.get_session_signal_state(SESSION, USER).label != "stressed"


def _row(**features):
    base = {"focus_score": 60.0, "calm_score": 50.0, "confidence": 80.0,
            "signal_quality": "good", "quality_basis": "contact"}
    return signal_mapping.map_eeg_to_cognitive(
        {"timestamp": "t", "features": {**base, **features}}, "s", "u")


def test_the_row_records_the_calm_source_its_scale_and_whether_calm_was_measured():
    local = _row(calm_source="local", calm_measured=True, calm_held_seconds=2.0)
    assert local["raw"]["calm_source"] == "local"
    assert local["raw"]["score_scale"] == 2, "the source is its own key, not a scale"
    assert local["stress"] == pytest.approx(0.5)
    assert _row(calm_source="sdk")["raw"]["score_scale"] == 2
    assert _row()["raw"]["score_scale"] == 2, "no source is sdk"
    placeholder = _row(calm_source="local", calm_measured=False)
    assert placeholder["stress"] is None and placeholder["focus"] == pytest.approx(0.6)
    assert placeholder["raw"]["calm_measured"] is False
    stale = _row(calm_source="local", calm_measured=True, calm_held_seconds=150.0)
    assert stale["stress"] is None and stale["raw"]["calm_held_seconds"] == 150.0
    fresh = _row(calm_source="local", calm_measured=True,
                 calm_held_seconds=signal_mapping.CALM_HOLD_MAX_SECONDS)
    assert fresh["stress"] == pytest.approx(0.5), "at the cap is still held, not stale"


def test_the_score_scale_is_the_version_alone_so_a_bump_cannot_read_as_a_source(monkeypatch):
    """Packed into one number, the next version bump would have read as the local calm."""
    monkeypatch.setattr(signal_mapping, "SCORE_SCALE_VERSION", 3)
    for features in ({"calm_source": "sdk"}, {"calm_source": "local"}, {}):
        assert _row(**features)["raw"]["score_scale"] == 3
    assert "calm_source" not in _row()["raw"], "absent stays absent, and reads as sdk"


def test_an_unknown_calm_source_withholds_stress_and_says_why():
    """A source outside sdk/local puts calm on no known scale; focus is unaffected."""
    for bad in ("martian", {"a": 1}, ["local"], 3):
        row = _row(calm_source=bad)
        assert row["stress"] is None and row["focus"] == pytest.approx(0.6)
        assert "calm_source" not in row["raw"] and row["raw"]["calm_invalid"] == ["calm_source"]


def test_a_row_on_no_known_calm_source_adds_nothing_to_the_windows_calm(monkeypatch):
    """Its stress is on no known scale, so averaging it in moves calm by a meaningless amount."""
    base = {"session_id": SESSION, "focus": 0.4, "engagement": 0.4}
    rows = [{**base, "stress": 0.2, "raw": {"confidence": 0.9}},
            {**base, "stress": 0.9, "raw": {"confidence": 0.9, "calm_source": "martian"}}]
    _install(monkeypatch, CONSENT_ALL, eeg=rows)
    assert decider.get_session_signal_state(SESSION, USER).calm == pytest.approx(0.8)


def test_the_calm_sources_ride_beside_the_scale_and_combine_as_a_union(monkeypatch):
    """A day on two calm sources is two units of stress, whatever the version says."""
    import main
    from tests.test_access_control import _FakeSupabase
    monkeypatch.setattr(main, "_utc_now", lambda: datetime(2026, 9, 29, 12, tzinfo=timezone.utc))
    monkeypatch.setattr(main, "_school_timezone", lambda: timezone.utc)
    rows = [{"user_id": "a", "day": "2026-09-28", "channel": "cognitive", "score_scale_min": 2,
             "score_scale_max": 2, "calm_sources": ["sdk"]},
            {"user_id": "a", "day": "2026-09-29", "channel": "cognitive", "score_scale_min": 2,
             "score_scale_max": 2, "calm_sources": ["local"]},
            {"user_id": "b", "day": "2026-09-29", "channel": "cognitive", "score_scale_min": 2,
             "score_scale_max": 2, "calm_sources": None}]
    monkeypatch.setattr(main, "supabase", _FakeSupabase({"signal_daily_rollup": rows}))
    scales = main._scale_ranges_many(["a", "b"], 7)
    assert scales["a"] == {"min": 2, "max": 2, "calm_sources": ["local", "sdk"]}
    assert scales["b"] == {"min": 2, "max": 2, "calm_sources": []}, "no stress, no source"
    assert main._combine_ranges(scales.values())["calm_sources"] == ["local", "sdk"]
