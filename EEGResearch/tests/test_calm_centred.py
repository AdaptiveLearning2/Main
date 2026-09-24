"""The payload says whether each score is centred on the session yet."""

from __future__ import annotations

from src.app.services.signal_processing import SignalProcessor
from tests.test_local_calm_review import BANDS, _local, _tick
from tests.test_signal_processing import Ticker, _spectrum


def test_calm_centred_follows_the_calm_latch_on_the_local_source():
    t = _local()
    f = _tick(t, _spectrum(None, ready=False))
    assert f["focus_centred"] is False and f["calm_centred"] is False
    for _ in range(4 * int(SignalProcessor.BASELINE_SECONDS) + 8):
        f = _tick(t, _spectrum(None, ready=False))
    assert f["focus_centred"] is True, "focus latched on its own coverage"
    assert f["calm_centred"] is False, "no tick has carried a calm: still the midpoint"
    for _ in range(4 * int(SignalProcessor.BASELINE_SECONDS) + 8):
        f = _tick(t, _spectrum(0.3))
    assert f["calm_centred"] is True


def test_on_the_sdk_source_both_scores_centre_together():
    t = Ticker()
    f = t.run(BANDS, 1)
    assert f["focus_centred"] is False and f["calm_centred"] is False
    f = t.run(BANDS, 4 * int(SignalProcessor.BASELINE_SECONDS) + 8)
    assert f["focus_centred"] is True and f["calm_centred"] is True


def test_calm_centred_is_the_calm_latch_on_every_source():
    """The flag reports `_calm_ready`, never focus's latch as a proxy."""
    t = Ticker()
    t.run(BANDS, 4 * int(SignalProcessor.BASELINE_SECONDS) + 8)
    p = t.processor
    assert p._baseline_ready and p._calm_ready
    p._calm_ready = False
    f = t.run(BANDS, 1)
    assert f["focus_centred"] is True
    assert f["calm_centred"] is False, "the flag the scorer read, not the sibling's"
