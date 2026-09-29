"""Local spectrum: rate refusal, artifact poisoning, per-signal contact, temporal-only buffer."""

from __future__ import annotations

from datetime import timedelta

from src.app.models import EegSample
from src.app.services.contact import seated_channels
from src.app.services.eeg_spectrum import CHANNELS, EPOCH_SECONDS, SAMPLE_RATE_HZ, SpectrumEstimator
from src.app.services.signal_processing import SignalProcessor
from tests.test_eeg_spectrum import GOOD, T0, pink, samples_from


def test_a_stream_that_is_not_256hz_is_refused_not_analysed():
    """The simulator's one sample per 4 Hz tick, windowed by count, would pass as 256 Hz."""
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
    """One blink moves the residual further than the whole eyes-open/closed effect."""
    est = SpectrumEstimator()
    n = int(EPOCH_SECONDS * SAMPLE_RATE_HZ)
    samples = samples_from({c: pink(2 * n, i) for i, c in enumerate(CHANNELS)})
    assert est.push(samples[:n], GOOD)["ready"] is True
    est.poison()
    assert est.latest()["ready"] is False and est.latest()["reason"] == "artifact"
    half = est.push(samples[n:n + n // 2], GOOD)
    assert half["ready"] is False and half["reason"] == "artifact"
    assert est.push(samples[n + n // 2:], GOOD)["ready"] is True


def test_a_channel_unseated_anywhere_in_the_buffer_is_left_out_until_those_samples_leave():
    """Contact is per tick, the buffer is 4 s: a reseat does not vouch for what railed before it."""
    est = SpectrumEstimator()
    n = int(EPOCH_SECONDS * SAMPLE_RATE_HZ)
    samples = samples_from({c: pink(3 * n, i) for i, c in enumerate(CHANNELS)})
    railed = n - 64
    tp10_off = {"hsi": [1.0, 1.0, 1.0, 4.0], "is_good": [1.0, 1.0, 1.0, 0.0]}
    est.push(samples[:railed], tp10_off)
    assert est.push(samples[railed:n], GOOD)["channels_used"] == 1
    assert est.push(samples[n:railed + n - 1], GOOD)["channels_used"] == 1
    assert est.push(samples[railed + n - 1:railed + n], GOOD)["channels_used"] == 2
    est.reset()
    assert est.push(samples[:n], GOOD)["channels_used"] == 2


def test_a_malformed_is_good_does_not_erase_the_hsi_verdict():
    seated = seated_channels({"hsi": [1.0, 1.0, 1.0, 4.0], "is_good": ["x", None, 1.0, "y"]})
    assert seated == [True, True, True, False]
    assert seated_channels({}) is None


def test_a_nan_contact_entry_unseats_the_electrode_as_every_other_reader_does():
    """A NaN is_good already reads as bad in _sample_is_usable and _contact_ratio; so must this."""
    nan = float("nan")
    assert seated_channels({"is_good": [nan, 1.0, 1.0, 1.0]}) == [False, True, True, True]
    assert seated_channels({"hsi": [nan, 1.0, 1.0, 1.0]}) == [False, True, True, True]
    railing = EegSample(timestamp=T0, channel_tp9=1682.0, channel_af7=800.0, channel_af8=801.0,
                        channel_tp10=802.0)
    kept = SignalProcessor._good_channel_values(railing, {"hsi": [1.0] * 4,
                                                          "is_good": [nan, 1.0, 1.0, 1.0]})
    assert kept == [800.0, 801.0, 802.0]


def test_the_processor_and_the_spectrum_read_contact_through_one_check():
    """A malformed is_good must not readmit the electrode hsi says is railing, in either reader."""
    sample = EegSample(timestamp=T0, channel_tp9=1.0, channel_af7=2.0, channel_af8=3.0,
                       channel_tp10=4.0)
    meta = {"hsi": [1.0, 1.0, 1.0, 4.0], "is_good": ["x", None, 1.0, "y"]}
    kept = SignalProcessor._good_channel_values(sample, meta)
    assert kept == [1.0, 2.0, 3.0]
    everyone_off = {"hsi": [4.0] * 4, "is_good": [0.0] * 4}
    assert len(SignalProcessor._good_channel_values(sample, everyone_off)) == 4, "never empty"


def test_only_the_temporal_channels_are_buffered():
    assert set(SpectrumEstimator()._buf) == {"tp9", "tp10"}
