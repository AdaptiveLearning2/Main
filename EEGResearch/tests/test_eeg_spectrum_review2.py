"""Local spectrum: rate check on stamp spans, and a poison while filling reads as artifact."""

from __future__ import annotations

from src.app.services.eeg_spectrum import CHANNELS, EPOCH_SECONDS, SAMPLE_RATE_HZ, SpectrumEstimator
from tests.test_eeg_spectrum import GOOD, pink, samples_from


def _full():
    n = int(EPOCH_SECONDS * SAMPLE_RATE_HZ)
    return samples_from({c: pink(n, i) for i, c in enumerate(CHANNELS)})


def test_a_256hz_buffer_with_half_its_stamps_absent_is_still_256hz():
    """push() admits unstamped samples, so the rate is judged on span, not stamp count."""
    samples = _full()
    for i, s in enumerate(samples):
        if i % 2 == 1:
            s.timestamp = None
    out = SpectrumEstimator().push(samples, GOOD)
    assert out["reason"] != "sample_rate"
    assert out["ready"] is True


def test_a_poison_while_filling_says_artifact_not_filling():
    est = SpectrumEstimator()
    samples = _full()
    half = len(samples) // 2
    est.push(samples[:half], GOOD)
    assert est.latest()["reason"] == "filling"
    est.poison()
    assert est.latest()["reason"] == "artifact"
    # Still under capacity: the only case where the branch order is observable.
    out = est.push(samples[half:half + 64], GOOD)
    assert out["ready"] is False and out["reason"] == "artifact"
    out = est.push(samples[half + 64:], GOOD)
    assert out["ready"] is False and out["reason"] == "artifact"

