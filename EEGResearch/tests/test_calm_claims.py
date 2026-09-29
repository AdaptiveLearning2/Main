"""What a calm value claims about itself: measured, fresh, and the unit of its smoothed diagnostic."""

from __future__ import annotations

from math import exp

import pytest

from src.app.services.signal_processing import SignalProcessor
from tests.test_local_calm_review import BANDS, _local, _tick
from tests.test_signal_processing import Ticker, _spectrum


def test_the_amplitude_fallback_placeholder_is_not_a_measured_calm():
    """One seated electrode has no spread, so the fallback's calm is the 0.5 placeholder."""
    one_seated = {"hsi": [1.0, 4.0, 4.0, 4.0], "is_good": [1.0, 0.0, 0.0, 0.0]}
    f = Ticker().run(one_seated, 5)
    assert f["calm_score"] == pytest.approx(50.0)
    assert f["calm_measured"] is False
    two_seated = {"hsi": [1.0, 1.0, 4.0, 4.0], "is_good": [1.0, 1.0, 0.0, 0.0]}
    assert Ticker().run(two_seated, 5)["calm_measured"] is True


def test_calm_smoothing_counts_the_time_a_local_calm_was_missing():
    """After a gap the old value has aged; one tick's weight would leave calm reading the past."""
    t = _local()
    for _ in range(8):
        _tick(t, _spectrum(0.0))
    for _ in range(16):
        _tick(t, _spectrum(None, ready=False))
    f = _tick(t, _spectrum(0.6))
    gap = 17 * t.dt  # last estimate to this one
    smoothed = 0.6 * (1.0 - exp(-gap / SignalProcessor.RATIO_SMOOTHING_SECONDS))
    span = SignalProcessor.CALM_ALPHA_RESIDUAL_MAX - SignalProcessor.CALM_ALPHA_RESIDUAL_MIN
    assert f["calm_held_seconds"] == pytest.approx(0.0)
    assert f["calm_score"] == pytest.approx(100.0 * (0.5 + smoothed / span), abs=0.01)


def test_an_artifact_on_the_first_full_buffer_holds_a_placeholder_that_says_so():
    """The estimate on an artifact tick is discarded, so what is held is still the fill's 50."""
    t = _local()
    for _ in range(4):
        _tick(t, _spectrum(None, ready=False))
    emg = {**BANDS, "gamma": 0.9}
    f = t.processor.update(t.sample(), emg, spectrum=_spectrum(0.4))
    t.now += t.dt
    assert f["artifact_reason"] == "emg_gamma" and f["calm_score"] == pytest.approx(50.0)
    assert f["calm_measured"] is False and f["calm_held_seconds"] is None
    f = _tick(t, _spectrum(0.4))
    assert f["calm_measured"] is True and f["calm_held_seconds"] == pytest.approx(0.0)
    # Held after a measured calm: still measured, and aged from the last admitted estimate.
    f = t.processor.update(t.sample(), emg, spectrum=_spectrum(0.4))
    assert f["calm_measured"] is True and f["calm_held_seconds"] == pytest.approx(t.dt)


def test_a_local_estimate_on_a_tick_never_admitted_is_discarded_like_a_held_one():
    """Before anything is admitted the tick is raw-scored for a number, but its local calm is not fresh."""
    emg = {**BANDS, "gamma": 0.9}
    t = _local()
    f = t.processor.update(t.sample(), emg, spectrum=_spectrum(0.4))
    assert f["artifact_reason"] == "emg_gamma" and f["calm_score"] == pytest.approx(50.0)
    assert f["calm_measured"] is False and f["calm_held_seconds"] is None
    sdk = Ticker()
    assert sdk.processor.update(sdk.sample(), emg)["calm_measured"] is True, \
        "the SDK ratio is the tick's own reading"


def test_the_smoothed_calm_diagnostic_keeps_its_unit_on_each_source():
    """calm_log_ratio_smoothed is in calm_log_ratio's unit; the residual has its own key."""
    t = _local()
    for _ in range(8):
        f = _tick(t, _spectrum(0.3))
    assert f["calm_log_ratio_smoothed"] is None
    assert f["calm_alpha_residual_smoothed"] == pytest.approx(0.3)
    sdk = Ticker().run(BANDS, 8)
    assert sdk["calm_log_ratio_smoothed"] == pytest.approx(sdk["calm_log_ratio"])
    assert sdk["calm_alpha_residual_smoothed"] is None
