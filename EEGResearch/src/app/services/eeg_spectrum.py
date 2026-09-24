"""Our own spectrum from the raw 256 Hz stream, for the local `calm`.

Alpha is measured per channel at the temporal pair only, as the residual of log10
power over 8-12 Hz above a 1/f fit that excludes the alpha band. A calm marker only;
the 1/f slope is carried for comparison, not scored. Welch, 2 s Hann, 50% overlap,
4 s buffer. See docs/signals.md for the measurements and gates.
"""

from __future__ import annotations

import logging
from datetime import datetime
from math import isfinite
from typing import Any

import numpy as np

SAMPLE_RATE_HZ = 256.0
EPOCH_SECONDS = 4.0
WINDOW_SECONDS = 2.0
# Fit range (Hz), excluding alpha so the peak is not absorbed into the slope;
# 2 Hz clears blink/drift power, 40 Hz stays under the notch and EMG.
FIT_LO_HZ, FIT_HI_HZ = 2.0, 40.0
FIT_EXCLUDE_HZ = (7.0, 13.0)
ALPHA_HZ = (8.0, 12.0)
# TP9 and TP10 carry the rhythm; AF7/AF8 carry the blink and the muscle.
TEMPORAL = ("tp9", "tp10")
# The bridge's channel order, for indexing hsi/is_good.
CHANNELS = ("tp9", "af7", "af8", "tp10")
# Allowed fractional mismatch between a full buffer's timestamp span and 256 Hz.
RATE_TOLERANCE = 0.25


def welch_log_psd(x: np.ndarray, fs: float = SAMPLE_RATE_HZ,
                  window_seconds: float = WINDOW_SECONDS) -> tuple[np.ndarray, np.ndarray]:
    """Welch PSD of one channel. Returns (frequencies, log10 power).

    DC is removed first: the raw channels sit at ~800 uV of ADC offset."""
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


def one_over_f_fit(f: np.ndarray, log_psd: np.ndarray) -> tuple[float, float]:
    """(slope, intercept) of log10 power vs log10 frequency, alpha excluded.

    The one place the fit is written; the analysis script reuses it."""
    lo, hi = FIT_EXCLUDE_HZ
    mask = (f >= FIT_LO_HZ) & (f <= FIT_HI_HZ) & ~((f >= lo) & (f <= hi))
    slope, intercept = np.polyfit(np.log10(f[mask]), log_psd[mask], 1)
    return float(slope), float(intercept)


def alpha_residual(f: np.ndarray, log_psd: np.ndarray) -> tuple[float, float]:
    """(alpha residual, 1/f slope): mean log10 power over ALPHA_HZ above
    one_over_f_fit."""
    slope, intercept = one_over_f_fit(f, log_psd)
    fitted = intercept + slope * np.log10(np.maximum(f, 1e-9))
    band = (f >= ALPHA_HZ[0]) & (f < ALPHA_HZ[1])
    return float(np.mean(log_psd[band] - fitted[band])), float(slope)


def poisons_buffer(artifact_reason: str | None) -> bool:
    """Whether an artifact-held tick poisons the raw buffer.

    malformed_bands does not: it is a fault in the SDK's band dict, not the samples."""
    return artifact_reason not in (None, "malformed_bands")


class SpectrumEstimator:
    """Rolling 4 s buffer per temporal channel; `latest()` is the estimate.

    `push()` takes every sample the tick drained. A channel the tick's contact data
    marks unseated is left out rather than averaged in.
    `latest()["reason"]`: `filling`, `sample_rate`, `artifact`, or `no_channel`.
    """

    def __init__(self, sample_rate_hz: float = SAMPLE_RATE_HZ,
                 epoch_seconds: float = EPOCH_SECONDS,
                 poison_seconds: float | None = None) -> None:
        self.sample_rate_hz = sample_rate_hz
        self.capacity = int(round(epoch_seconds * sample_rate_hz))
        # Seconds an artifact withholds estimates (default: the whole buffer). Floored at
        # one sample and required finite, else it falls back with a warning, never raises.
        requested = float(epoch_seconds if poison_seconds is None else poison_seconds)
        samples = int(round(requested * sample_rate_hz)) if isfinite(requested) else 0
        if samples < 1:
            logging.getLogger(__name__).warning(
                "EEG_SPECTRUM_POISON_SECONDS=%r is not a usable poison length "
                "(needs a finite value of at least one sample); using the buffer, %.1f s",
                poison_seconds, epoch_seconds)
            requested = float(epoch_seconds)
            samples = int(round(requested * sample_rate_hz))
        self.poison_seconds = requested
        self.poison_samples = samples
        self._buf: dict[str, list[float]] = {c: [] for c in TEMPORAL}
        self._ts: list[datetime] = []
        # Samples pushed, and the count at which the last artifact's samples have left.
        self._pushed = 0
        self._clean_after = 0
        self._latest: dict[str, Any] = self._empty("filling")

    @staticmethod
    def _empty(reason: str) -> dict[str, Any]:
        return {"ready": False, "reason": reason, "alpha_residual_temporal": None,
                "slope_temporal": None, "channels_used": 0}

    def reset(self) -> None:
        """A signal-loss gap: whatever spans it is two recordings."""
        for c in TEMPORAL:
            self._buf[c].clear()
        self._ts.clear()
        self._pushed = 0
        self._clean_after = 0
        self._latest = self._empty("filling")

    def poison(self) -> None:
        """The tick just scored was an artifact: no estimate until its samples
        have left the buffer (the gate itself holds only one tick)."""
        self._clean_after = self._pushed + self.poison_samples
        # Reported as "artifact" even while still filling.
        self._latest = self._empty("artifact")

    def push(self, samples: list[Any], meta: dict[str, Any] | None) -> dict[str, Any]:
        """Append the tick's samples and recompute. Returns the estimate."""
        for s in samples:
            for c in TEMPORAL:
                v = getattr(s, f"channel_{c}", None)
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    v = float("nan")
                self._buf[c].append(v)
            self._ts.append(getattr(s, "timestamp", None))
        self._pushed += len(samples)
        for c in TEMPORAL:
            if len(self._buf[c]) > self.capacity:
                del self._buf[c][:len(self._buf[c]) - self.capacity]
        if len(self._ts) > self.capacity:
            del self._ts[:len(self._ts) - self.capacity]
        self._latest = self._estimate(meta or {})
        return self._latest

    def latest(self) -> dict[str, Any]:
        return self._latest

    def _rate_plausible(self) -> bool:
        """Whether the buffer's timestamp span is what a 256 Hz stream gives.

        Stamps are bursty BLE delivery, but average out over a full buffer."""
        idx = [i for i, t in enumerate(self._ts) if isinstance(t, datetime)]
        if len(idx) < 2:
            return True  # nothing to judge by; the sidecar's adapters stamp every sample
        span = (self._ts[idx[-1]] - self._ts[idx[0]]).total_seconds()
        # From the stamps' positions, not their count: unstamped samples are admitted.
        expected = (idx[-1] - idx[0]) / self.sample_rate_hz
        # A fraction of the buffer's duration, not of the stamp gap, which can be ~0 in one burst.
        bar = RATE_TOLERANCE * len(self._ts) / self.sample_rate_hz
        return abs(span - expected) <= bar

    def _seated(self, meta: dict[str, Any]) -> dict[str, bool]:
        """Per channel, whether the tick's contact data vouches for it.

        No contact data: every channel counts. hsi and is_good are read independently."""
        hsi = meta.get("hsi")
        is_good = meta.get("is_good")
        out = {}
        for i, c in enumerate(CHANNELS):
            ok = True
            if isinstance(hsi, list) and len(hsi) == len(CHANNELS):
                try:
                    if float(hsi[i]) > 2.0:
                        ok = False
                except (TypeError, ValueError):
                    pass
            if isinstance(is_good, list) and len(is_good) == len(CHANNELS):
                try:
                    if float(is_good[i]) < 1.0:
                        ok = False
                except (TypeError, ValueError):
                    pass
            out[c] = ok
        return out

    def _estimate(self, meta: dict[str, Any]) -> dict[str, Any]:
        # Artifact is reported before filling.
        if self._pushed < self._clean_after:
            return self._empty("artifact")
        if any(len(self._buf[c]) < self.capacity for c in TEMPORAL):
            return self._empty("filling")
        if not self._rate_plausible():
            return self._empty("sample_rate")
        seated = self._seated(meta)
        residuals, slopes = [], []
        for c in TEMPORAL:
            if not seated[c]:
                continue
            x = np.asarray(self._buf[c])
            if not np.all(np.isfinite(x)):
                # One non-finite sample makes the FFT NaN everywhere.
                continue
            f, log_psd = welch_log_psd(x, self.sample_rate_hz)
            r, s = alpha_residual(f, log_psd)
            if isfinite(r):
                residuals.append(r)
                slopes.append(s)
        if not residuals:
            return self._empty("no_channel")
        return {
            "ready": True,
            "reason": None,
            "alpha_residual_temporal": round(float(np.mean(residuals)), 4),
            "slope_temporal": round(float(np.mean(slopes)), 3),
            "channels_used": len(residuals),
        }
