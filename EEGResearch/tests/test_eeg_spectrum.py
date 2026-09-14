"""The local spectrum: 1/f-relative alpha at the temporal pair.

Synthetic signals, built so the answer is known: pink noise with and without
a 10 Hz sinusoid on the temporal channels. The reference numbers the design
rests on -- a 10 Hz peak 4.6x above the 1/f fit eyes closed, none eyes open,
AUC 0.92 at 4 s -- are from a recording of a person and live in
tests/fixtures/EEG_REFERENCE.md, not here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from src.app.models import EegSample
from src.app.services.eeg_spectrum import (
    CHANNELS, EPOCH_SECONDS, SAMPLE_RATE_HZ, SpectrumEstimator, alpha_residual, welch_log_psd,
)

T0 = datetime(2026, 9, 14, 20, 0, tzinfo=timezone.utc)
GOOD = {"hsi": [1.0, 1.0, 1.0, 1.0], "is_good": [1.0, 1.0, 1.0, 1.0]}


def pink(n: int, seed: int, fs: float = SAMPLE_RATE_HZ) -> np.ndarray:
    """1/f^2 in power (slope -2, roughly the eyes-open capture), unit-ish
    scale, DC offset like the bridge's ~800 uV."""
    rng = np.random.default_rng(seed)
    white = rng.standard_normal(n)
    spec = np.fft.rfft(white)
    f = np.fft.rfftfreq(n, d=1.0 / fs)
    spec[1:] /= f[1:]  # amplitude 1/f -> power 1/f^2
    spec[0] = 0.0
    x = np.fft.irfft(spec, n=n)
    return 800.0 + 20.0 * x / x.std()


def with_alpha(x: np.ndarray, amplitude: float, fs: float = SAMPLE_RATE_HZ) -> np.ndarray:
    t = np.arange(len(x)) / fs
    return x + amplitude * np.sin(2 * np.pi * 10.0 * t)


def samples_from(channels: dict[str, np.ndarray]) -> list[EegSample]:
    n = len(next(iter(channels.values())))
    return [EegSample(timestamp=T0 + timedelta(seconds=i / SAMPLE_RATE_HZ),
                      channel_tp9=float(channels["tp9"][i]), channel_af7=float(channels["af7"][i]),
                      channel_af8=float(channels["af8"][i]), channel_tp10=float(channels["tp10"][i]))
            for i in range(n)]


def test_a_10hz_rhythm_reads_as_alpha_above_the_background_and_pink_noise_does_not():
    n = int(EPOCH_SECONDS * SAMPLE_RATE_HZ)
    f, quiet = welch_log_psd(pink(n, 1))
    f, loud = welch_log_psd(with_alpha(pink(n, 1), 15.0))
    r_quiet, s_quiet = alpha_residual(f, quiet)
    r_loud, s_loud = alpha_residual(f, loud)
    assert abs(r_quiet) < 0.15, "pink noise alone is on the 1/f fit"
    assert r_loud > 0.4, "a rhythm is a residual above it"
    # The fit excludes the alpha band, so the peak does not steal the slope.
    assert s_loud == pytest.approx(s_quiet, abs=0.3)


def test_the_estimator_needs_a_full_epoch_then_reads_the_temporal_pair_only():
    est = SpectrumEstimator()
    n = int(EPOCH_SECONDS * SAMPLE_RATE_HZ)
    chans = {c: pink(n, i) for i, c in enumerate(CHANNELS)}
    # Alpha on the temporal pair only; the frontal pair gets a rhythm too,
    # which must not be what is read.
    chans["tp9"] = with_alpha(chans["tp9"], 15.0)
    chans["tp10"] = with_alpha(chans["tp10"], 15.0)
    chans["af7"] = with_alpha(chans["af7"], -30.0)
    samples = samples_from(chans)
    half = est.push(samples[: n // 2], GOOD)
    assert half["ready"] is False and half["alpha_residual_temporal"] is None
    full = est.push(samples[n // 2:], GOOD)
    assert full["ready"] is True and full["channels_used"] == 2
    assert full["alpha_residual_temporal"] > 0.4
    est_quiet = SpectrumEstimator()
    quiet = est_quiet.push(samples_from({c: pink(n, i + 10) for i, c in enumerate(CHANNELS)}), GOOD)
    assert abs(quiet["alpha_residual_temporal"]) < 0.15


def test_an_unseated_or_non_finite_temporal_channel_is_left_out_not_averaged_in():
    """A railing electrode has power at every frequency and reads as no
    alpha whatever the other one says."""
    est = SpectrumEstimator()
    n = int(EPOCH_SECONDS * SAMPLE_RATE_HZ)
    chans = {c: pink(n, i) for i, c in enumerate(CHANNELS)}
    chans["tp9"] = with_alpha(chans["tp9"], 15.0)
    chans["tp10"] = 800.0 + 300.0 * np.random.default_rng(5).standard_normal(n)  # white: no alpha
    both = est.push(samples_from(chans), GOOD)
    tp10_out = SpectrumEstimator().push(samples_from(chans),
                                        {"hsi": [1.0, 1.0, 1.0, 4.0], "is_good": [1.0, 1.0, 1.0, 0.0]})
    assert tp10_out["channels_used"] == 1
    assert tp10_out["alpha_residual_temporal"] > both["alpha_residual_temporal"]
    chans["tp10"][100] = float("nan")
    nan_out = SpectrumEstimator().push(samples_from(chans), GOOD)
    assert nan_out["channels_used"] == 1
    neither = SpectrumEstimator().push(samples_from(chans),
                                       {"hsi": [4.0, 1.0, 1.0, 4.0], "is_good": [0.0, 1.0, 1.0, 0.0]})
    assert neither["ready"] is False


def test_a_reset_empties_the_buffer_so_two_recordings_are_never_one_epoch():
    est = SpectrumEstimator()
    n = int(EPOCH_SECONDS * SAMPLE_RATE_HZ)
    samples = samples_from({c: pink(n, i) for i, c in enumerate(CHANNELS)})
    est.push(samples, GOOD)
    assert est.latest()["ready"] is True
    est.reset()
    assert est.latest()["ready"] is False
    assert est.push(samples[: n // 2], GOOD)["ready"] is False


def test_the_buffer_is_by_sample_count_and_bounded():
    est = SpectrumEstimator()
    n = int(EPOCH_SECONDS * SAMPLE_RATE_HZ)
    samples = samples_from({c: pink(3 * n, i) for i, c in enumerate(CHANNELS)})
    est.push(samples, GOOD)
    assert all(len(est._buf[c]) == n for c in CHANNELS)
