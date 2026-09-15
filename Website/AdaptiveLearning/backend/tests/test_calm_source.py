"""Calm has two sources on two scales, and every reader has to know which.

The sidecar's SDK ratio and its local alpha residual are different numbers
on different spans: the stressed line is per source in both packages, the
decider reads the source off the rows, and the row records the source, its
scale and whether the calm was measured at all.
"""

from __future__ import annotations

import os

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
    assert local["raw"]["calm_source"] == "local" and local["raw"]["score_scale"] == 3
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
