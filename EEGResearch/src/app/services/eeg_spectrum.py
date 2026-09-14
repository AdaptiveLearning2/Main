"""Our own spectrum from the raw 256 Hz stream, and the one number it is for.

The SDK's band powers are a four-channel average of raw power, and on this
headband at the contact the product actually gets they could not see the
alpha rhythm that is plainly there in the raw stream: eyes closed, TP9 and
TP10 carry a 10 Hz peak 4.6x above the 1/f background that is absent eyes
open (tests/fixtures/EEG_REFERENCE.md, raw-stream capture). Two things hide
it from the SDK figure -- the frontal channels do not carry it and dilute the
average, and raw band power is dominated by the aperiodic 1/f slope, which
moves more between the two states than any band does.

So this measures alpha the other way round: per channel, at the temporal pair
only, as the residual of log10 power over 8-12 Hz above a 1/f fit made with
the alpha band excluded. That residual separated eyes closed from open at AUC
0.92 on 4 s epochs -- the same 4 s the ratio smoothing already uses -- and is
what `calm` reads when `EEG_SPECTRUM_SOURCE=local`. It is a *calm* marker
only: silent arithmetic raised neither beta nor gamma on the same capture, so
nothing here claims to measure effort, and `focus` stays on the SDK ratio,
documented as unmeasured.

Welch over 2 s Hann windows with 50% overlap on a 4 s buffer, numpy only, a
few hundred microseconds per tick. The buffer is by sample count at the
bridge's nominal rate: the bridge's timestamps record BLE delivery, not
sampling (CLAUDE.md, the heart window), so they cannot place a sample and the
count is the only index. A signal-loss reset empties it, so two recordings
are never windowed as one.
"""

from __future__ import annotations

from math import isfinite
from typing import Any

import numpy as np

SAMPLE_RATE_HZ = 256.0
EPOCH_SECONDS = 4.0
WINDOW_SECONDS = 2.0
# The fit range and the band it must not see: fitting through the alpha
# band would absorb the peak into the slope, which is the whole mistake a raw
# band ratio makes. 2 Hz is above the blink and drift power the fit must not
# chase either; 40 Hz stays under the notch and the EMG-dominated top.
FIT_LO_HZ, FIT_HI_HZ = 2.0, 40.0
FIT_EXCLUDE_HZ = (7.0, 13.0)
ALPHA_HZ = (8.0, 12.0)
# TP9 and TP10 carry the rhythm; AF7/AF8 carry the blink and the muscle.
TEMPORAL = ("tp9", "tp10")
CHANNELS = ("tp9", "af7", "af8", "tp10")


def welch_log_psd(x: np.ndarray, fs: float = SAMPLE_RATE_HZ,
                  window_seconds: float = WINDOW_SECONDS) -> tuple[np.ndarray, np.ndarray]:
    """Welch PSD of one channel: Hann windows, 50% overlap, mean of the
    periodograms. Returns (frequencies, log10 power). DC removed first, since
    the raw channels sit at ~800 uV of ADC offset."""
    n = int(round(window_seconds * fs))
    x = np.asarray(x, dtype=float)
    x = x - x.mean()
    hop = n // 2
    win = np.hanning(n)
    scale = fs * float((win ** 2).sum())
    acc = None
    count = 0
    for start in range(0, len(x) - n + 1, hop):
        seg = x[start:start + n] * win
        p = (np.abs(np.fft.rfft(seg)) ** 2) / scale
        acc = p if acc is None else acc + p
        count += 1
    if acc is None:
        raise ValueError("not enough samples for one window")
    psd = acc / count
    # One-sided: double everything but DC and Nyquist.
    psd[1:-1] *= 2.0
    f = np.fft.rfftfreq(n, d=1.0 / fs)
    return f, np.log10(np.maximum(psd, 1e-30))


def alpha_residual(f: np.ndarray, log_psd: np.ndarray) -> tuple[float, float]:
    """(alpha residual, 1/f slope): mean log10 power over ALPHA_HZ above a
    straight-line fit of log10 power against log10 frequency over the fit
    range with FIT_EXCLUDE_HZ left out."""
    lo, hi = FIT_EXCLUDE_HZ
    mask = (f >= FIT_LO_HZ) & (f <= FIT_HI_HZ) & ~((f >= lo) & (f <= hi))
    slope, intercept = np.polyfit(np.log10(f[mask]), log_psd[mask], 1)
    fitted = intercept + slope * np.log10(np.maximum(f, 1e-9))
    band = (f >= ALPHA_HZ[0]) & (f < ALPHA_HZ[1])
    return float(np.mean(log_psd[band] - fitted[band])), float(slope)


class SpectrumEstimator:
    """Rolling 4 s buffer per channel; `latest()` is the current estimate.

    `push()` takes every sample the tick drained -- the sidecar already
    drains the bridge queue in full and scores only the last sample, so this
    is where a consumer of the raw stream belongs (CLAUDE.md). A channel the
    tick's contact data marks unseated is left out of the pair for that
    estimate rather than averaged in: a railing electrode has power at every
    frequency and would read as no alpha whatever the other one says.
    """

    def __init__(self, sample_rate_hz: float = SAMPLE_RATE_HZ,
                 epoch_seconds: float = EPOCH_SECONDS) -> None:
        self.sample_rate_hz = sample_rate_hz
        self.capacity = int(round(epoch_seconds * sample_rate_hz))
        self._buf: dict[str, list[float]] = {c: [] for c in CHANNELS}
        self._latest: dict[str, Any] = self._empty()

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"ready": False, "alpha_residual_temporal": None,
                "slope_temporal": None, "channels_used": 0}

    def reset(self) -> None:
        """A signal-loss gap: whatever spans it is two recordings."""
        for c in CHANNELS:
            self._buf[c].clear()
        self._latest = self._empty()

    def push(self, samples: list[Any], meta: dict[str, Any] | None) -> dict[str, Any]:
        """Append the tick's samples and recompute. Returns the estimate."""
        for s in samples:
            for c in CHANNELS:
                v = getattr(s, f"channel_{c}", None)
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    v = float("nan")
                self._buf[c].append(v)
        for c in CHANNELS:
            if len(self._buf[c]) > self.capacity:
                del self._buf[c][:len(self._buf[c]) - self.capacity]
        self._latest = self._estimate(meta or {})
        return self._latest

    def latest(self) -> dict[str, Any]:
        return self._latest

    def _seated(self, meta: dict[str, Any]) -> dict[str, bool]:
        """Per channel, whether the tick's contact data vouches for it. With
        no contact data every channel counts, as _good_channel_values does."""
        hsi = meta.get("hsi")
        is_good = meta.get("is_good")
        out = {}
        for i, c in enumerate(CHANNELS):
            ok = True
            try:
                if isinstance(hsi, list) and len(hsi) == len(CHANNELS) and float(hsi[i]) > 2.0:
                    ok = False
                if isinstance(is_good, list) and len(is_good) == len(CHANNELS) and float(is_good[i]) < 1.0:
                    ok = False
            except (TypeError, ValueError):
                ok = True
            out[c] = ok
        return out

    def _estimate(self, meta: dict[str, Any]) -> dict[str, Any]:
        if any(len(self._buf[c]) < self.capacity for c in TEMPORAL):
            return self._empty()
        seated = self._seated(meta)
        residuals, slopes = [], []
        for c in TEMPORAL:
            if not seated[c]:
                continue
            x = np.asarray(self._buf[c])
            if not np.all(np.isfinite(x)):
                # One non-finite sample is a frame the bridge could not
                # deliver; the FFT of it is NaN everywhere.
                continue
            f, log_psd = welch_log_psd(x, self.sample_rate_hz)
            r, s = alpha_residual(f, log_psd)
            if isfinite(r):
                residuals.append(r)
                slopes.append(s)
        if not residuals:
            return self._empty()
        return {
            "ready": True,
            "alpha_residual_temporal": round(float(np.mean(residuals)), 4),
            "slope_temporal": round(float(np.mean(slopes)), 3),
            "channels_used": len(residuals),
        }
