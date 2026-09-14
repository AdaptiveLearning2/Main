"""The local spectrum after its second review.

The rate check judges the span between two stamps against the samples
between them, not against how many stamps there are; a poison while the
buffer is still filling is reported as the artifact it is; and a fault in
the SDK's band dict does not poison a buffer of raw samples it never touched.
"""

from __future__ import annotations

import inspect

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
    out = est.push(samples[half:], GOOD)
    assert out["ready"] is False and out["reason"] == "artifact"


def test_malformed_bands_does_not_poison_the_raw_buffer():
    """malformed_bands is a fault in the SDK band dict, not in the samples.
    Poisoning on it cost 31 ticks (3.88 s) of estimates the buffer could
    have given -- on the local source, ticks with no calm at all."""
    from src.app.services.stream_manager import DeviceSession
    src = inspect.getsource(DeviceSession._loop)
    assert 'features.get("artifact_reason") not in (None, "malformed_bands")' in src
