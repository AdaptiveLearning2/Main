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
documented as unmeasured. The 1/f slope separates the two states even more
than the residual does, and is carried on the payload for comparison, but is
not scored: whether that separation is neural or the blink rate is not
something one capture can say.

Welch over 2 s Hann windows with 50% overlap on a 4 s buffer, numpy only, a
few hundred microseconds per tick. Only the two temporal channels are
buffered. The buffer is by sample count at the bridge's nominal rate: the
bridge's timestamps record BLE delivery, not sampling (CLAUDE.md, the heart
window), so they cannot place a sample and the count is the only index --
but their *span* over a full buffer is checked against what 256 Hz would
give, and an estimate is refused when it is far off. Without that the
simulator's one sample per 4 Hz tick filled the buffer with 256 s of signal
analysed as four, and published a plausible residual with nothing behind it.
A signal-loss reset empties the buffer, so two recordings are never windowed
as one, and an artifact tick poisons the buffer until the samples it landed
on have left it: measured, one blink moved the residual further than the
whole eyes-open to eyes-closed effect, and the gate holds only the tick it
landed on.
"""

from __future__ import annotations

from datetime import datetime
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
# The bridge's channel order, for indexing hsi/is_good.
CHANNELS = ("tp9", "af7", "af8", "tp10")
# A full buffer's timestamp span may differ from (n-1)/fs by this fraction
# before the samples are judged not to be a 256 Hz stream at all.
RATE_TOLERANCE = 0.25


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
    """Rolling 4 s buffer per temporal channel; `latest()` is the estimate.

    `push()` takes every sample the tick drained -- the sidecar already
    drains the bridge queue in full and scores only the last sample, so this
    is where a consumer of the raw stream belongs (CLAUDE.md). A channel the
    tick's contact data marks unseated is left out of the pair for that
    estimate rather than averaged in: a railing electrode has power at every
    frequency and would read as no alpha whatever the other one says.

    `latest()["reason"]` says why an estimate is not ready: `filling`,
    `sample_rate`, `artifact`, or `no_channel`.
    """

    def __init__(self, sample_rate_hz: float = SAMPLE_RATE_HZ,
                 epoch_seconds: float = EPOCH_SECONDS) -> None:
        self.sample_rate_hz = sample_rate_hz
        self.capacity = int(round(epoch_seconds * sample_rate_hz))
        self._buf: dict[str, list[float]] = {c: [] for c in TEMPORAL}
        self._ts: list[datetime] = []
        # Samples pushed so far, and the count at which the most recent
        # artifact tick's samples will have left the buffer.
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
        """The tick just scored was an artifact (a blink, a clench, a jolt):
        no estimate until every sample now in the buffer has left it. The
        artifact gate holds one tick; the window would otherwise carry the
        blink for four seconds of estimates."""
        self._clean_after = self._pushed + self.capacity
        # Whatever the buffer was doing -- ready, or still filling -- the
        # reason an estimate is absent from here on is the artifact; a poison
        # while filling reported "filling" for up to 8 s.
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
        The stamps are bursty (BLE delivery), but over a full buffer the span
        averages out; a stream delivering one sample per tick spans minutes."""
        idx = [i for i, t in enumerate(self._ts) if isinstance(t, datetime)]
        if len(idx) < 2:
            return True  # nothing to judge by; the sidecar's adapters stamp every sample
        span = (self._ts[idx[-1]] - self._ts[idx[0]]).total_seconds()
        # Expected from the *positions* of the two stamps, not from how many
        # stamps there are: push() admits an unstamped sample, and counting
        # stamps refused a genuine 256 Hz buffer with half of them absent.
        expected = (idx[-1] - idx[0]) / self.sample_rate_hz
        # The bar is a fraction of the *buffer's* duration, not of the gap
        # between the two stamps: scaled with that gap it collapsed to a
        # millisecond when the surviving stamps sat in one BLE burst, or
        # when two adjacent samples shared a delivery stamp, and refused a
        # genuine 256 Hz buffer as sample_rate. The simulator's one sample
        # per tick still spans minutes against a 4 s bar.
        bar = RATE_TOLERANCE * len(self._ts) / self.sample_rate_hz
        return abs(span - expected) <= bar

    def _seated(self, meta: dict[str, Any]) -> dict[str, bool]:
        """Per channel, whether the tick's contact data vouches for it. With
        no contact data every channel counts, as _good_channel_values does.
        The two signals are read independently: a malformed is_good used to
        erase the hsi verdict along with its own, admitting a railing
        electrode the docstring says is excluded."""
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
        # Artifact before filling: a poison during the fill is the reason
        # the estimate is absent, and the fill is merely also true.
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
                # One non-finite sample is a frame the bridge could not
                # deliver; the FFT of it is NaN everywhere.
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
