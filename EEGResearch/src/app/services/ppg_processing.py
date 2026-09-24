"""Heart rate from the headband's optical channels (Athena OPTICS, ~64 Hz, microamps).

Under motion this reports a confident wrong rate (step cadence), not silence;
nothing optical can tell the two apart. A confident value is not a correct one.
See docs/signals.md and tests/fixtures/README.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import butter, filtfilt

# Physiological search range; deliberately not narrowed to dodge interference.
MIN_BPM = 42.0
MAX_BPM = 180.0

# Hz. Low removes baseline wander; high keeps broadband noise from crushing the normalised ACF peak.
BANDPASS_LOW_HZ = 0.6
BANDPASS_HIGH_HZ = 5.0
BANDPASS_ORDER = 4

# Hz; a non-cardiac component on every recording from this headband. Tracked, not excluded.
KNOWN_INTERFERER_HZ = 0.742
INTERFERER_TOLERANCE_HZ = 0.05

# bpm per second between windows; rejects octave errors (2x / 0.5x jumps).
MAX_BPM_CHANGE_PER_S = 3.0

# Below this a window reports no rate; drops confident post-exercise misreads.
MIN_CONFIDENCE = 0.55

# bpm; ceiling on continuity however long the gap, since a stale anchor is not evidence.
MAX_CONTINUITY_JUMP_BPM = 40.0


@dataclass
class ChannelEstimate:
    """One channel's view of one window."""
    index: int
    bpm: float | None
    # Winning ACF peak over the next unrelated one; near 1 means an octave ambiguity.
    margin: float
    # Normalised ACF height at the chosen period (0..1): a periodicity strength, not a power ratio.
    snr: float


@dataclass
class HeartEstimate:
    """The fused answer for one window."""
    bpm: float | None
    confidence: float
    channels: list[ChannelEstimate] = field(default_factory=list)
    # For display only; never branch on it.
    reason: str = ""
    # "continuity" | "confidence" | "no_signal" | "unconfirmed_anchor". The last is
    # a withheld reading awaiting a second window, not an absent one.
    rejected_by: str | None = None

    @property
    def agreement(self) -> float:
        """Fraction of channels within 5 bpm of the reported rate."""
        return channel_agreement(self.bpm, self.channels)


def channel_agreement(bpm: float | None, channels: list[ChannelEstimate]) -> float:
    """Fraction of channels within 5 bpm of `bpm`.

    A quality signal, not a correctness one: under motion all channels agree on a wrong rate.
    """
    usable = [c for c in channels if c.bpm is not None]
    if not usable or bpm is None:
        return 0.0
    return sum(1 for c in usable if abs(c.bpm - bpm) <= 5.0) / len(usable)


def bandpass(x: np.ndarray, fs: float) -> np.ndarray:
    """Isolate the pulse band before anything else looks at the signal."""
    nyq = fs / 2
    b, a = butter(BANDPASS_ORDER,
                  [BANDPASS_LOW_HZ / nyq, min(BANDPASS_HIGH_HZ, nyq * 0.99) / nyq],
                  btype="band")
    return filtfilt(b, a, x)


def _peak_half_width(y: np.ndarray, i: int) -> float:
    """Measured half-width of the peak at index `i`, in lag samples.

    The harmonic exclusion band must cover the peak's own shoulder, or it becomes the runner-up.
    """
    # The 2.0 floor holds only while BANDPASS_HIGH_HZ caps the signal at 5 Hz; raise them together.
    half = y[i] * 0.5
    left = i
    while left > 0 and y[left - 1] < y[left] and y[left] > half:
        left -= 1
    right = i
    while right < len(y) - 1 and y[right + 1] < y[right] and y[right] > half:
        right += 1
    return max(2.0, float(max(i - left, right - i)))


def _parabolic_peak(y: np.ndarray, i: int) -> float:
    """Sub-sample peak position by fitting a parabola through three points.

    Lag quantisation at 64 Hz is on the order of the RMSSD values this feeds.
    """
    if i <= 0 or i >= len(y) - 1:
        return float(i)
    a, b, c = y[i - 1], y[i], y[i + 1]
    denom = a - 2 * b + c
    if denom == 0:
        return float(i)
    return float(i) + 0.5 * (a - c) / denom


def estimate_channel(x: np.ndarray, fs: float, index: int = 0) -> ChannelEstimate:
    """Rate for one channel, by autocorrelation.

    Not a spectral argmax, which cannot prefer f over a strong 2f harmonic.
    """
    x = np.asarray(x, dtype=float)
    # Before the filter, not after: np.mean of an empty array warns.
    if x.size == 0 or not np.any(x):
        return ChannelEstimate(index, None, 0.0, 0.0)
    # filtfilt's padlen is 3 * (2N+1) coefficients = 27 for order 4.
    if len(x) > 3 * (2 * BANDPASS_ORDER + 1):
        x = bandpass(x, fs)
    x = x - x.mean()

    # Full autocorrelation via FFT, normalised so lag 0 is 1.
    n = int(2 ** math.ceil(math.log2(len(x) * 2)))
    spec = np.fft.rfft(x, n)
    acf = np.fft.irfft(spec * np.conj(spec), n)[: len(x)]
    if acf[0] <= 0:
        return ChannelEstimate(index, None, 0.0, 0.0)
    acf = acf / acf[0]

    lag_min = int(fs * 60.0 / MAX_BPM)
    lag_max = int(fs * 60.0 / MIN_BPM)
    lag_max = min(lag_max, len(acf) - 2)
    if lag_max <= lag_min:
        return ChannelEstimate(index, None, 0.0, 0.0)

    window = acf[lag_min:lag_max]

    # The FIRST strong peak, not the tallest: multiples are often taller, so argmax reports a subharmonic.
    peaks = []
    for i in range(1, len(window) - 1):
        if window[i] >= window[i - 1] and window[i] > window[i + 1]:
            peaks.append(i)
    if not peaks:
        return ChannelEstimate(index, None, 0.0, 0.0)

    tallest = max(window[i] for i in peaks)
    if tallest <= 0:
        return ChannelEstimate(index, None, 0.0, 0.0)
    # Skips noise ripple while a fundamental slightly shorter than its harmonic still wins.
    strong = [i for i in peaks if window[i] >= 0.75 * tallest]
    best = strong[0]

    lag = _parabolic_peak(acf, lag_min + best)
    bpm = 60.0 * fs / lag if lag > 0 else None

    snr = float(window[best])

    # Margin against the tallest unrelated peak. Integer multiples of the lag are
    # excluded: they are evidence for this period, not rivals.
    best_lag = lag_min + best
    lags = np.arange(len(window)) + lag_min
    # Same absolute width at every multiple, not a fraction of best_lag.
    ratio = lags / best_lag
    # Cap keeps a broad peak from masking a genuine competing period between multiples.
    lag_tol = min(_peak_half_width(window, best), 0.4 * best_lag)
    near_multiple = np.abs(lags - np.round(ratio) * best_lag) <= lag_tol
    unrelated = ~near_multiple
    runner_up = float(window[unrelated].max()) if unrelated.any() else 0.0
    margin = float(window[best] / runner_up) if runner_up > 0 else float("inf")

    if bpm is None or not (MIN_BPM <= bpm <= MAX_BPM):
        return ChannelEstimate(index, None, margin, snr)
    return ChannelEstimate(index, float(bpm), margin, snr)


def estimate_window(
    channels: np.ndarray,
    fs: float,
    previous_bpm: float | None = None,
    seconds_since_previous: float = 0.0,
) -> HeartEstimate:
    """Fuse per-channel estimates into one rate for this window.

    `channels` is (samples, n_channels). Continuity, not channel agreement, rejects octave errors.
    """
    channels = np.asarray(channels, dtype=float)
    if channels.ndim == 1:
        channels = channels[:, None]

    estimates = [estimate_channel(channels[:, c], fs, c) for c in range(channels.shape[1])]
    usable = [e for e in estimates if e.bpm is not None]
    if not usable:
        return HeartEstimate(None, 0.0, estimates, "no channel produced a rate",
                             rejected_by="no_signal")

    # Median: robust to one bad emitter without nominating a preferred channel.
    bpm = float(np.median([e.bpm for e in usable]))

    within = channel_agreement(bpm, estimates)
    # within catches a badly seated emitter, snr a window with no pulse, margin a
    # near-tie between periods. None catches the post-motion t=0 window.
    mean_snr = float(np.mean([e.snr for e in usable]))
    mean_margin = float(np.mean([min(e.margin, 4.0) for e in usable]))
    margin_term = max(0.0, min(1.0, (mean_margin - 1.0) / 0.7))
    confidence = float(within * min(1.0, mean_snr / 0.4) * margin_term)

    if confidence < MIN_CONFIDENCE:
        return HeartEstimate(None, confidence, estimates,
                             f"confidence {confidence:.2f} below {MIN_CONFIDENCE}",
                             rejected_by="confidence")

    reason = ""
    if previous_bpm is not None and seconds_since_previous > 0:
        # Capped; past the cap the tracker abandons the anchor instead of stretching it.
        allowed = min(MAX_BPM_CHANGE_PER_S * seconds_since_previous,
                      MAX_CONTINUITY_JUMP_BPM)
        if abs(bpm - previous_bpm) > allowed:
            # Rejected, never octave-"corrected": the anchor itself may be wrong.
            return HeartEstimate(
                None, 0.0, estimates,
                f"rejected {bpm:.1f} bpm: moved more than "
                f"{allowed:.0f} bpm from {previous_bpm:.1f}",
                rejected_by="continuity",
            )

    return HeartEstimate(bpm, confidence, estimates, reason)


class HeartRateTracker:
    """Sequential estimation with continuity, and recovery from a bad lock.

    Consecutive continuity rejections count against the anchor, not the data, and trigger re-acquisition.
    """

    def __init__(self, reacquire_after: int = 2, anchor_min_confidence: float = 0.65):
        self.bpm: float | None = None
        self.reacquire_after = reacquire_after
        # Higher bar to become the anchor than to be reportable.
        self.anchor_min_confidence = anchor_min_confidence
        self._rejections = 0
        # Candidate anchor not yet seen twice; nothing is published from it.
        self._pending: float | None = None

    def update(self, channels: np.ndarray, fs: float, seconds_since_previous: float) -> HeartEstimate:
        est = estimate_window(channels, fs, self.bpm, seconds_since_previous)

        if est.bpm is None:
            # Only a continuity rejection counts against the anchor; unreadable windows leave the counter.
            if self.bpm is not None and est.rejected_by == "continuity":
                self._rejections += 1
                if self._rejections >= self.reacquire_after:
                    self.bpm = None
                    self._rejections = 0
                    fresh = estimate_window(channels, fs, None, 0.0)
                    if fresh.bpm is None:
                        self._pending = None
                        return fresh
                    # Re-acquisition needs the same corroboration: it follows exactly the motion this distrusts.
                    self._pending = fresh.bpm
                    return HeartEstimate(
                        None, fresh.confidence, fresh.channels,
                        f"re-acquiring: holding {fresh.bpm:.1f} bpm until a "
                        f"second window agrees",
                        rejected_by="unconfirmed_anchor")
            # A window that produced nothing breaks the candidate chain.
            self._pending = None
            return est

        self._rejections = 0
        if self.bpm is not None:
            self.bpm = est.bpm
            return est

        # ── unanchored: nothing is published until two windows agree ──
        # Motion settling fades a step later; a heartbeat does not. Costs one window of latency.
        previous, self._pending = self._pending, est.bpm
        allowed = min(MAX_BPM_CHANGE_PER_S * seconds_since_previous,
                      MAX_CONTINUITY_JUMP_BPM)
        if previous is not None and abs(est.bpm - previous) <= allowed:
            if est.confidence >= self.anchor_min_confidence:
                self.bpm = est.bpm
            return est
        return HeartEstimate(
            None, est.confidence, est.channels,
            f"holding {est.bpm:.1f} bpm until a second window agrees",
            rejected_by="unconfirmed_anchor")


def near_known_interferer(bpm: float) -> bool:
    """Whether a rate coincides with this hardware's non-cardiac component.

    Not a rejection: 44.5 bpm is a real rate for some people.
    """
    return abs(bpm / 60.0 - KNOWN_INTERFERER_HZ) <= INTERFERER_TOLERANCE_HZ
