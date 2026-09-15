"""The local spectrum after its first review: what an estimate may claim.

A stream that is not 256 Hz is refused rather than analysed, an artifact
poisons the window until its samples have left, each contact signal is read
on its own, and only the temporal channels are buffered.
"""

from __future__ import annotations

from datetime import timedelta

from src.app.models import EegSample
from src.app.services.eeg_spectrum import CHANNELS, EPOCH_SECONDS, SAMPLE_RATE_HZ, SpectrumEstimator
from tests.test_eeg_spectrum import GOOD, T0, pink, samples_from


def test_a_stream_that_is_not_256hz_is_refused_not_analysed():
    """The simulator delivers one sample per 4 Hz tick. Windowed by count
    at 256 Hz, 256 s of it was analysed as four and published a plausible
    residual with nothing behind it, scored under the local source."""
    est = SpectrumEstimator()
    n = int(EPOCH_SECONDS * SAMPLE_RATE_HZ)
    chans = {c: pink(n, i) for i, c in enumerate(CHANNELS)}
    slow = [EegSample(timestamp=T0 + timedelta(seconds=i * 0.25),
                      channel_tp9=float(chans["tp9"][i]), channel_af7=float(chans["af7"][i]),
                      channel_af8=float(chans["af8"][i]), channel_tp10=float(chans["tp10"][i]))
            for i in range(n)]
    out = est.push(slow, GOOD)
    assert out["ready"] is False and out["reason"] == "sample_rate"
    assert SpectrumEstimator().push(samples_from(chans), GOOD)["ready"] is True


def test_an_artifact_poisons_the_buffer_until_its_samples_have_left():
    """The gate holds one tick; the window kept the blink for four seconds
    of estimates, and one blink moved the residual further than the whole
    eyes-open to eyes-closed effect."""
    est = SpectrumEstimator()
    n = int(EPOCH_SECONDS * SAMPLE_RATE_HZ)
    samples = samples_from({c: pink(2 * n, i) for i, c in enumerate(CHANNELS)})
    assert est.push(samples[:n], GOOD)["ready"] is True
    est.poison()
    assert est.latest()["ready"] is False and est.latest()["reason"] == "artifact"
    half = est.push(samples[n:n + n // 2], GOOD)
    assert half["ready"] is False and half["reason"] == "artifact"
    assert est.push(samples[n + n // 2:], GOOD)["ready"] is True


def test_a_malformed_is_good_does_not_erase_the_hsi_verdict():
    seated = SpectrumEstimator()._seated({"hsi": [1.0, 1.0, 1.0, 4.0],
                                          "is_good": ["x", None, 1.0, "y"]})
    assert seated["tp10"] is False and seated["tp9"] is True


def test_only_the_temporal_channels_are_buffered():
    assert set(SpectrumEstimator()._buf) == {"tp9", "tp10"}
