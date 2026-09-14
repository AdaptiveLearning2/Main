"""The local calm source after its first review: what a calm value may claim.

Calm latches on its own coverage and keeps collecting after focus has; a
placeholder says it is one and a gap makes calm unmeasured again; a carried
calm reports how long; the stressed line is per source and pinned to the
backend's table; the simulator never feeds the spectrum and an artifact
poisons it.
"""

from __future__ import annotations

import inspect

import pytest

from src.app.services.adaptation import STRESSED_CALM_MAX
from src.app.services.signal_processing import SignalProcessor
from tests.test_signal_processing import CONTACT_GOOD, RELAXED, Ticker, _engine, _spectrum

BANDS = {**RELAXED, **CONTACT_GOOD}


def _local() -> Ticker:
    t = Ticker()
    t.processor = SignalProcessor(clock=lambda: t.now, calm_source="local")
    return t


def _tick(t: Ticker, spectrum):
    f = t.processor.update(t.sample(), BANDS, spectrum=spectrum)
    t.now += t.dt
    return f


def test_the_simulator_never_feeds_the_spectrum_and_an_artifact_poisons_it():
    from src.app.services.stream_manager import DeviceSession
    src = inspect.getsource(DeviceSession._loop)
    assert 'if self.device_config.kind == "muse":' in src
    assert "self.spectrum.push(samples, raw_meta)" in src
    assert "self.spectrum.poison()" in src


def test_calm_latches_on_its_own_coverage_and_keeps_collecting_after_focus_has():
    """A session latched with 181 focus samples and one calm sample, that
    one value being the calm centre for good."""
    t = _local()
    for _ in range(4 * int(SignalProcessor.BASELINE_SECONDS) + 8):
        _tick(t, _spectrum(None, ready=False))
    assert t.processor._baseline_ready and not t.processor._calm_ready
    assert t.processor._baseline_calm_mean is None
    _tick(t, _spectrum(0.3))
    assert not t.processor._calm_ready, "one calm sample is not a centre"
    for _ in range(4 * int(SignalProcessor.BASELINE_SECONDS) + 8):
        f = _tick(t, _spectrum(0.3))
    assert t.processor._calm_ready
    assert t.processor._baseline_calm_mean == pytest.approx(0.3)
    # 45 covered seconds at one second a tick at most is at least 45 samples.
    assert len(t.processor._baseline_calm) >= 45
    assert f["calm_measured"] is True
    # And ramped onto its own centre: a constant residual reads 50.
    for _ in range(4 * int(SignalProcessor.BASELINE_RAMP_SECONDS) + 8):
        f = _tick(t, _spectrum(0.3))
    assert f["calm_score"] == pytest.approx(50.0, abs=1.0)


def test_a_placeholder_calm_says_so_and_a_gap_makes_calm_unmeasured_again():
    """The opening fill wrote a fabricated 50, the same value a genuine
    residual of zero produces; and after every gap, which the SDK path
    never had."""
    t = _local()
    f = _tick(t, _spectrum(None, ready=False))
    assert f["calm_score"] == pytest.approx(50.0) and f["calm_measured"] is False
    assert f["spectrum_reason"] == "filling"
    for _ in range(4):
        f = _tick(t, _spectrum(0.0))
    assert f["calm_measured"] is True and f["calm_held_seconds"] == pytest.approx(0.0)
    t.processor.reset()
    f = _tick(t, _spectrum(None, ready=False))
    assert f["calm_measured"] is False and f["calm_held_seconds"] is None
    assert Ticker().run(BANDS, 1)["calm_measured"] is True, "the SDK source is measured from tick one"


def test_a_carried_calm_reports_how_long_it_has_been_carried():
    t = _local()
    for _ in range(4):
        _tick(t, _spectrum(0.2))
    for _ in range(40):  # 10 s unready
        f = _tick(t, _spectrum(None, ready=False))
    assert f["calm_measured"] is True
    assert f["calm_held_seconds"] == pytest.approx(10.0, abs=0.3)
    assert f["spectrum_reason"] == "filling"


def test_the_stressed_line_is_per_calm_source_and_pinned_to_the_backend():
    """0.377 is 0.311 Bels below centre on the SDK span and 0.148 below it
    on the local one, where silent arithmetic then read stressed."""
    assert STRESSED_CALM_MAX == {"sdk": 0.377, "local": 0.25}
    calm_30 = {"focus_score": 40.0, "calm_score": 30.0, "confidence": 90.0}

    def label(features):
        eng, _ = _engine()
        eng.persist_ticks = 1
        return eng.infer_state(features).label

    assert label({**calm_30, "calm_source": "sdk"}) == "stressed"
    assert label({**calm_30, "calm_source": "local"}) == "neutral"
    assert label({**calm_30, "calm_source": "local", "calm_score": 20.0}) == "stressed"
    assert label({**calm_30, "calm_measured": False}) == "neutral", "a placeholder is neither"
