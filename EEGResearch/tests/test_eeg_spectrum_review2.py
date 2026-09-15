"""The local spectrum after its second review.

The rate check judges the span between two stamps against the samples
between them, not against how many stamps there are; a poison while the
buffer is still filling is reported as the artifact it is; and a fault in
the SDK's band dict does not poison a buffer of raw samples it never touched.
"""

from __future__ import annotations

from src.app.services.eeg_spectrum import CHANNELS, EPOCH_SECONDS, SAMPLE_RATE_HZ, SpectrumEstimator
from tests.test_eeg_spectrum import GOOD, pink, samples_from


def _full():
    n = int(EPOCH_SECONDS * SAMPLE_RATE_HZ)
    return samples_from({c: pink(n, i) for i, c in enumerate(CHANNELS)})


def test_a_256hz_buffer_with_half_its_stamps_absent_is_still_256hz():
    """push() admits an unstamped sample. Expected from the stamp *count*,
    a genuine buffer with every other stamp missing read 3.99 s against an
    expected 2.0 s and was refused as sample_rate."""
    samples = _full()
    for i, s in enumerate(samples):
        if i % 2 == 1:
            s.timestamp = None
    out = SpectrumEstimator().push(samples, GOOD)
    assert out["reason"] != "sample_rate"
    assert out["ready"] is True


def test_a_poison_while_filling_says_artifact_not_filling():
    """The estimate was correctly withheld either way; the field that
    explains the absence named the fill for up to 8 s."""
    est = SpectrumEstimator()
    samples = _full()
    half = len(samples) // 2
    est.push(samples[:half], GOOD)
    assert est.latest()["reason"] == "filling"
    est.poison()
    assert est.latest()["reason"] == "artifact"
    # A push that leaves the buffer still under capacity: only here is the
    # branch order observable, since a push that fills it makes the filling
    # branch false under either order.
    out = est.push(samples[half:half + 64], GOOD)
    assert out["ready"] is False and out["reason"] == "artifact"
    out = est.push(samples[half + 64:], GOOD)
    assert out["ready"] is False and out["reason"] == "artifact"

