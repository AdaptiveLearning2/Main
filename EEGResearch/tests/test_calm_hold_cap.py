"""A calm carried past CALM_HOLD_MAX_SECONDS is not a reading for the engine either."""

from __future__ import annotations

from src.app.services.adaptation import CALM_HOLD_MAX_SECONDS, AdaptationEngine


def _feat(**over):
    base = {"focus_score": 50.0, "calm_score": 10.0, "confidence": 90.0,
            "calm_source": "local", "calm_measured": True}
    return {**base, **over}


def _settled(features):
    eng = AdaptationEngine(clock=lambda: 0.0)
    for _ in range(eng.persist_ticks):
        state = eng.infer_state(features)
    return state.label


def test_a_calm_carried_past_the_cap_is_neutral_not_stressed():
    assert CALM_HOLD_MAX_SECONDS == 10.0, "must equal signal_mapping.CALM_HOLD_MAX_SECONDS"
    assert _settled(_feat(calm_held_seconds=2.0)) == "stressed"
    assert _settled(_feat(calm_held_seconds=CALM_HOLD_MAX_SECONDS)) == "stressed", "at the cap is held"
    assert _settled(_feat(calm_held_seconds=CALM_HOLD_MAX_SECONDS + 0.5)) == "neutral"
    assert _settled(_feat(calm_score=90.0, focus_score=90.0, calm_held_seconds=11.0)) == "neutral", \
        "nor focused"
    assert _settled(_feat(calm_held_seconds=None)) == "stressed", "no hold figure is the SDK source"
