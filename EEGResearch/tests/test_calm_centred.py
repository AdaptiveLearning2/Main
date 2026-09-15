"""The payload says whether each score is centred on the session yet.

Pre-latch was a 45-second opening window. On the local source the calm
latch needs 45 covered seconds of ticks that carried a calm, and a poisoned
tick carries none, so a session can run to its end scored against the
population midpoint -- indistinguishable on the row from a centred one.
"""

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
    """`_centre("calm")` reads `_calm_ready` on both sources, so the flag
    reports that and never focus's latch as a proxy: with the calm latch
    withheld on the sdk source, the row must say calm is not centred while
    the scorer is still returning the population midpoint."""
    t = Ticker()
    t.run(BANDS, 4 * int(SignalProcessor.BASELINE_SECONDS) + 8)
    p = t.processor
    assert p._baseline_ready and p._calm_ready
    p._calm_ready = False
    f = t.run(BANDS, 1)
    assert f["focus_centred"] is True
    assert f["calm_centred"] is False, "the flag the scorer read, not the sibling's"
