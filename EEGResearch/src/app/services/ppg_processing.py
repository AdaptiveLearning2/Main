"""Heart rate from the headband's optical channels.

Every decision here is forced by something measured on real recordings rather
than chosen for elegance. The fixtures in tests/fixtures carry the evidence and
tests/fixtures/README.md the reasoning; the short version is inline below,
because the obvious implementation is wrong in four separate ways and each one
produces a plausible number rather than an error.

What the input is
-----------------
OPTICS samples from a Muse S Athena on PRESET_1035: 4 channels at ~64Hz, in
microamps. Two wavelengths (730nm and 850nm) on left and right. On other Muse
models the same shape arrives via the PPG packet instead; this code does not
care which, only that it is a set of optical channels at a known rate.

Known limitation: the first window after vigorous motion
--------------------------------------------------------
Measured on the recovery fixture, the 25s immediately after exercise produces
~127 bpm against a true rate near 90 -- unanimously across all four channels,
and with a decisive enough autocorrelation peak to look like a real reading.
This code does not detect it.

Two candidate discriminators were tried and neither works:

  * Out-of-band power, as a motion proxy. It measures pulse strength rather
    than motion: the resting recording scored *higher* (0.092) than the
    contaminated window (0.046).
  * Counting peaks at multiples of the chosen lag as rivals. This does flag the
    octave error, but it also collapses confidence for a perfectly periodic
    signal, where the autocorrelation has equal peaks at every multiple. The
    two requirements are in direct conflict, and the harmonic relation is
    exactly what makes an octave error ambiguous in the first place.

What limits the damage is the tracker below, not the estimator: a window must
clear a higher bar to become the reference every later window is judged
against, so a wrong post-motion reading is reported once and does not capture
the anchor. The honest summary is that a rate read within ~30s of vigorous
movement should be treated as unreliable by whatever consumes it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import butter, filtfilt

# Physiological search range. 42-180 bpm is wide enough for a resting child and
# a sprinting one, and deliberately NOT narrowed to dodge interference: an
# earlier analysis narrowed it to 1.0-1.5Hz, which recovered the right answer
# only because that recording's rate happened to sit inside the band chosen.
MIN_BPM = 42.0
MAX_BPM = 180.0

# Bandpass corners. The low one removes baseline wander -- perfusion, breathing
# and micro-movement peak around 0.2Hz and the tail still dominates the pulse
# band at 0.7Hz, so an argmax on undetrended data returns the band edge rather
# than a heartbeat.
#
# The high one matters just as much and is easier to leave out. Autocorrelation
# is normalised by its zero-lag value, which is total power, so broadband noise
# all the way to Nyquist (32Hz) inflates the denominator and crushes the pulse
# peak. Without it the normalised peak at rest measured 0.00-0.29 and most
# channels produced nothing; the pulse was there, buried under noise it did not
# need to be compared against. 5Hz keeps the fundamental and its first harmonic,
# which is what gives the waveform its shape, and discards the rest.
BANDPASS_LOW_HZ = 0.6
BANDPASS_HIGH_HZ = 5.0
BANDPASS_ORDER = 4

# A known non-cardiac component at ~0.74Hz (44.5 bpm) appeared in every
# recording from this headband. It was ruled out as cardiac by experiment: it is
# present at rest and after exercise and does not move, while the true rate went
# 67.9 -> 76.4. Not excluded from the search -- 44.5 bpm is a legitimate rate for
# someone else -- but tracked so it can be reported as a competing peak.
KNOWN_INTERFERER_HZ = 0.742
INTERFERER_TOLERANCE_HZ = 0.05

# How far the rate may move between consecutive windows, in bpm per second.
# A heart accelerates fast under load but not arbitrarily fast; the octave
# errors this exists to reject are jumps of 2x or 0.5x within one window step.
MAX_BPM_CHANGE_PER_S = 3.0

# Below this, a window's estimate is not reported as a rate at all.
#
# 0.55 rather than something lower because of what sits just under it. In the
# 10-30s after exercise the estimator produced 58 and 55 bpm against a true rate
# near 90 -- confidently enough to pass a loose threshold, wrongly enough to
# matter, and no rule available here distinguishes them from a real slow pulse.
# Raising the bar drops those two windows and costs nothing else: the resting
# and settled recovery windows all clear it comfortably.
MIN_CONFIDENCE = 0.55


@dataclass
class ChannelEstimate:
    """One channel's view of one window."""
    index: int
    bpm: float | None
    # Ratio of the winning autocorrelation peak to the next distinct one. Near 1
    # means two candidates were nearly tied, which is what an octave ambiguity
    # looks like from inside a single channel.
    margin: float
    # Pulsatile amplitude over broadband noise in the search band.
    snr: float


@dataclass
class HeartEstimate:
    """The fused answer for one window."""
    bpm: float | None
    confidence: float
    channels: list[ChannelEstimate] = field(default_factory=list)
    # Set when the estimate was rejected or adjusted, naming which rule fired.
    # A caller showing "no reading" to a teacher should be able to say why.
    reason: str = ""

    @property
    def agreement(self) -> float:
        """Fraction of channels within 5 bpm of the reported rate.

        A quality signal, NOT a correctness one. In the first 25s after motion
        every channel agreed on ~127 bpm and every channel was wrong by an
        octave -- they all make the same error, so unanimity proves only that
        the channels saw the same thing."""
        usable = [c for c in self.channels if c.bpm is not None]
        if not usable or self.bpm is None:
            return 0.0
        return sum(1 for c in usable if abs(c.bpm - self.bpm) <= 5.0) / len(usable)


def bandpass(x: np.ndarray, fs: float) -> np.ndarray:
    """Isolate the pulse band before anything else looks at the signal."""
    nyq = fs / 2
    b, a = butter(BANDPASS_ORDER,
                  [BANDPASS_LOW_HZ / nyq, min(BANDPASS_HIGH_HZ, nyq * 0.99) / nyq],
                  btype="band")
    return filtfilt(b, a, x)


def _parabolic_peak(y: np.ndarray, i: int) -> float:
    """Sub-sample peak position by fitting a parabola through three points.

    At 64Hz the lag grid is 15.6ms apart. Beat intervals land between grid
    points, and rounding them to the nearest lag quantises every interval by up
    to 7.8ms -- the same order as the RMSSD values this eventually feeds. The fit
    recovers a fraction of a sample and costs three multiplications.
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

    Autocorrelation rather than a spectral argmax, because the dominant failure
    on this hardware is the octave error: reading a rate at 2x or 0.5x the truth.
    A harmonic-rich pulse waveform puts real energy at 2f, and an FFT argmax has
    no way to prefer f -- it sees two peaks and takes the taller. Autocorrelation
    peaks at the true period AND its multiples, so taking the *first* strong peak
    resolves the ambiguity in the right direction by construction.
    """
    x = np.asarray(x, dtype=float)
    x = bandpass(x, fs) if len(x) > 3 * BANDPASS_ORDER * 2 else x - x.mean()
    x = x - x.mean()
    if x.size == 0 or not np.any(x):
        return ChannelEstimate(index, None, 0.0, 0.0)

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

    # The FIRST strong peak, not the tallest. This is the whole reason for using
    # autocorrelation: the ACF peaks at the true period *and* at every multiple
    # of it, and the multiples are often taller because a longer lag correlates
    # more of the waveform's slow structure. Taking argmax therefore reports a
    # subharmonic -- half the true rate -- which is exactly the error this
    # function exists to avoid. Scanning forward and stopping at the first peak
    # that clears the threshold picks the fundamental by construction.
    peaks = []
    for i in range(1, len(window) - 1):
        if window[i] >= window[i - 1] and window[i] > window[i + 1]:
            peaks.append(i)
    if not peaks:
        return ChannelEstimate(index, None, 0.0, 0.0)

    tallest = max(window[i] for i in peaks)
    if tallest <= 0:
        return ChannelEstimate(index, None, 0.0, 0.0)
    # 0.75 of the tallest peak: high enough to skip noise ripple, low enough
    # that a fundamental slightly shorter than its own harmonic still wins.
    strong = [i for i in peaks if window[i] >= 0.75 * tallest]
    best = strong[0]

    lag = _parabolic_peak(acf, lag_min + best)
    bpm = 60.0 * fs / lag if lag > 0 else None

    # Periodicity strength: the normalised ACF height at the chosen lag. 1.0
    # would be a perfectly repeating waveform; a clear pulse in these recordings
    # sits around 0.3-0.6 and noise well below 0.1.
    snr = float(window[best])

    # Margin against the tallest *unrelated* competing peak. Near 1.0 means two
    # genuinely different periods were nearly tied, which is what an octave
    # ambiguity looks like from inside one channel.
    #
    # Peaks at integer multiples of the chosen lag are excluded, because they
    # are evidence FOR this period rather than against it -- an autocorrelation
    # repeats at every multiple of the true period. Counting them as rivals made
    # a perfectly periodic signal score worst of all: a pure sine has equal
    # peaks at every multiple, giving margin 1.0 and confidence zero.
    best_lag = lag_min + best
    lags = np.arange(len(window)) + lag_min
    ratio = lags / best_lag
    near_multiple = np.abs(ratio - np.round(ratio)) < 0.12
    near_self = np.abs(lags - best_lag) <= max(2, int(0.15 * best_lag))
    unrelated = ~(near_multiple | near_self)
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

    `channels` is (samples, n_channels).

    The continuity check is what actually rejects octave errors. Cross-channel
    agreement cannot: measured on a real recording, every channel agreed on a
    rate an octave high in the 25s after exercise. Agreement tells you the
    channels saw the same thing, not that the thing was a heartbeat.
    """
    channels = np.asarray(channels, dtype=float)
    if channels.ndim == 1:
        channels = channels[:, None]

    estimates = [estimate_channel(channels[:, c], fs, c) for c in range(channels.shape[1])]
    usable = [e for e in estimates if e.bpm is not None]
    if not usable:
        return HeartEstimate(None, 0.0, estimates, "no channel produced a rate")

    # Median over channels: robust to one bad emitter without needing a
    # preferred channel nominated in advance. Which emitter is best-seated
    # varies between sessions.
    bpm = float(np.median([e.bpm for e in usable]))

    within = sum(1 for e in usable if abs(e.bpm - bpm) <= 5.0) / len(usable)
    # Three terms, because each catches a failure the others miss.
    #
    #   within  -- channel agreement. Catches one badly seated emitter. Does NOT
    #              catch an octave error: measured on the recovery fixture, all
    #              four channels agreed on a rate an octave high.
    #   snr     -- normalised ACF height, already 0..1. Catches a window with no
    #              pulse in it. A clear pulse sits around 0.3-0.6 here.
    #   margin  -- how decisively the chosen period beat its nearest rival.
    #              This is the one that catches octave ambiguity: in the 25s
    #              after exercise the mean margin was 1.03, meaning the winner
    #              barely beat a competitor, against 1.7-2.3 on clean windows.
    mean_snr = float(np.mean([e.snr for e in usable]))
    mean_margin = float(np.mean([min(e.margin, 4.0) for e in usable]))
    margin_term = max(0.0, min(1.0, (mean_margin - 1.0) / 0.7))
    confidence = float(within * min(1.0, mean_snr / 0.4) * margin_term)

    if confidence < MIN_CONFIDENCE:
        return HeartEstimate(None, confidence, estimates,
                             f"confidence {confidence:.2f} below {MIN_CONFIDENCE}")

    reason = ""
    if previous_bpm is not None and seconds_since_previous > 0:
        allowed = MAX_BPM_CHANGE_PER_S * seconds_since_previous
        if abs(bpm - previous_bpm) > allowed:
            # Rejected, not "corrected". An earlier version tried the octave on
            # the other side and adopted whichever landed inside the allowed
            # range -- which, anchored to a wrong value, rewrote a correct 92.0
            # into 46.0. A continuity rule may discard an estimate; it must not
            # invent one, because the anchor it is defending may itself be wrong.
            return HeartEstimate(
                None, 0.0, estimates,
                f"rejected {bpm:.1f} bpm: moved more than "
                f"{allowed:.0f} bpm from {previous_bpm:.1f}",
            )

    return HeartEstimate(bpm, confidence, estimates, reason)


class HeartRateTracker:
    """Sequential estimation with continuity, and recovery from a bad lock.

    Continuity is what rejects octave errors, but it needs an anchor and the
    anchor can be wrong -- the window right after motion produces a confident,
    unanimous, incorrect rate. Anchored to that, every later correct estimate
    looks like a discontinuity and gets discarded, so one bad window silently
    costs the next minute.

    So a run of consecutive rejections is treated as evidence against the
    anchor rather than against the data, and the tracker re-acquires.
    """

    def __init__(self, reacquire_after: int = 2, anchor_min_confidence: float = 0.65):
        self.bpm: float | None = None
        self.reacquire_after = reacquire_after
        # A window must be better than merely reportable to become the thing
        # every later window is judged against.
        self.anchor_min_confidence = anchor_min_confidence
        self._rejections = 0

    def update(self, channels: np.ndarray, fs: float, seconds_since_previous: float) -> HeartEstimate:
        est = estimate_window(channels, fs, self.bpm, seconds_since_previous)

        if est.bpm is None:
            if self.bpm is not None and "moved more than" in est.reason:
                self._rejections += 1
                if self._rejections >= self.reacquire_after:
                    # Drop the anchor and take this window on its own merits.
                    self.bpm = None
                    self._rejections = 0
                    est = estimate_window(channels, fs, None, 0.0)
                    if est.bpm is not None:
                        est.reason = "re-acquired after repeated rejections"
                        self.bpm = est.bpm
                    return est
            return est

        self._rejections = 0
        if self.bpm is not None or est.confidence >= self.anchor_min_confidence:
            self.bpm = est.bpm
        return est


def near_known_interferer(bpm: float) -> bool:
    """Whether a rate coincides with the non-cardiac component seen on this
    hardware. Not a rejection -- 44.5 bpm is a real rate for some people -- but
    a caller may want to weight it down when a competing candidate exists."""
    return abs(bpm / 60.0 - KNOWN_INTERFERER_HZ) <= INTERFERER_TOLERANCE_HZ
