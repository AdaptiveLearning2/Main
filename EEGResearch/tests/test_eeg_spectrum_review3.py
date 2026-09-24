"""The rate check's tolerance scales with the buffer, not the gap between its stamps."""

from __future__ import annotations

from src.app.services.eeg_spectrum import SpectrumEstimator
from tests.test_eeg_spectrum import GOOD
from tests.test_eeg_spectrum_review import EPOCH_SECONDS, SAMPLE_RATE_HZ, CHANNELS, EegSample, pink, T0, timedelta
from tests.test_eeg_spectrum_review2 import _full


def test_stamps_clustered_in_one_burst_do_not_refuse_a_full_buffer():
    """Stamps inside one BLE burst, or two samples sharing a delivery stamp."""
    samples = _full()
    keep = set(range(500, 512))
    for i, s in enumerate(samples):
        if i not in keep:
            s.timestamp = None
    assert SpectrumEstimator().push(samples, GOOD)["reason"] != "sample_rate"
    samples = _full()
    for i, s in enumerate(samples):
        if i not in (600, 601):
            s.timestamp = None
    samples[601].timestamp = samples[600].timestamp
    assert SpectrumEstimator().push(samples, GOOD)["reason"] != "sample_rate"


def test_the_simulator_is_still_refused():
    n = int(EPOCH_SECONDS * SAMPLE_RATE_HZ)
    chans = {c: pink(n, i) for i, c in enumerate(CHANNELS)}
    slow = [EegSample(timestamp=T0 + timedelta(seconds=i * 0.25),
                      channel_tp9=float(chans["tp9"][i]), channel_af7=float(chans["af7"][i]),
                      channel_af8=float(chans["af8"][i]), channel_tp10=float(chans["tp10"][i]))
            for i in range(n)]
    assert SpectrumEstimator().push(slow, GOOD)["reason"] == "sample_rate"
