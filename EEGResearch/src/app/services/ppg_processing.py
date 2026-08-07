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

Motion, and the one window nothing here catches
-----------------------------------------------
The hardest case in the fixtures is the ~30s after vigorous exercise. The
window starting the moment the wearer sat down reads **127 bpm against a true
rate near 90, on all four channels**, and no in-window test found so far
separates it from a real 127:

- cross-channel agreement cannot -- every channel makes the same error;
- out-of-band power cannot -- it measures pulse strength, not motion, and the
  resting recording scores *higher* than the contaminated one;
- the peak margin cannot -- it is 3.3 there, as decisive as a clean window.

The peak margin does reject the two windows after it, which read 58 and 55 bpm
against the same true ~90. What handles the first window is the tracker rather
than the estimator: it takes 127 as an anchor, rejects the next two windows as
discontinuous, and re-acquires, so the error clears within about 30s and the
decay that follows is tracked from 83 bpm down to the resting rate.

Motion is worse than that, and it is not solvable here
------------------------------------------------------
A continuous recording through rest -> exercise -> recovery (optics_through_
exercise fixture) reports **162-167 bpm at confidence 1.00 for six consecutive
windows** while the wearer's watch read 104. That is their step cadence. It
bears no harmonic relation to the true rate -- 166/104 = 1.60 -- so no
first-peak rule, octave test or margin can see it: the optical signal really
does contain a strong clean periodicity at 2.77Hz, and this module correctly
reports the oscillator it was given.

Nothing derived from the optical channels distinguishes those two oscillators.
The fix is the headband's accelerometer, which the bridge does not yet capture;
it is the only available signal independent of the periodicity in question.

Two consequences for callers, both the opposite of the intuitive assumption:
motion does not degrade into silence, it degrades into confidence 1.00; and a
confident value is therefore not evidence of a correct one. Anything recording
a rate must discard rather than store when movement is plausible.

A previous revision claimed the margin caught this window. It did not. That
rejection came from an exclusion band narrower than the ACF peak it was
excluding, which pushed the margin toward 1.0 on *every* short lag -- rejecting
the motion window and every rate above about 100 bpm alike. Removing the
too-narrow band restored the high rates and took the false discriminator with
it. Anything claiming to catch this window should be checked against
120-180 bpm before it is believed.
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

# Ceiling on how far continuity will stretch, however long the gap. Beyond this
# the anchor has stopped being evidence about the present.
MAX_CONTINUITY_JUMP_BPM = 40.0


@dataclass
class ChannelEstimate:
    """One channel's view of one window."""
    index: int
    bpm: float | None
    # Ratio of the winning autocorrelation peak to the next distinct one. Near 1
    # means two candidates were nearly tied, which is what an octave ambiguity
    # looks like from inside a single channel.
    margin: float
    # Normalised autocorrelation height at the chosen period: 1.0 would be a
    # perfectly repeating waveform, a clear pulse sits around 0.3-0.6, noise
    # well below 0.1. Named snr for brevity; it is a periodicity strength, not
    # a power ratio.
    snr: float


@dataclass
class HeartEstimate:
    """The fused answer for one window."""
    bpm: float | None
    confidence: float
    channels: list[ChannelEstimate] = field(default_factory=list)
    # Human-readable, for display. Never branch on it: an earlier version keyed
    # re-acquisition off `"moved more than" in reason`, so rewording a message
    # would have silently disabled it with no test noticing.
    reason: str = ""
    # Machine-readable cause, when there is one: "continuity" | "confidence" |
    # "no_signal". This is what control flow matches on.
    rejected_by: str | None = None

    @property
    def agreement(self) -> float:
        """Fraction of channels within 5 bpm of the reported rate."""
        return channel_agreement(self.bpm, self.channels)


def channel_agreement(bpm: float | None, channels: list[ChannelEstimate]) -> float:
    """Fraction of channels within 5 bpm of `bpm`.

    A quality signal, NOT a correctness one. Measured twice: all four channels
    agreed on ~127 bpm in the 25s after motion, and on ~166 bpm during exercise
    against a watch-verified 104. Unanimity proves the channels saw the same
    thing, and under motion the same thing is not the heart.

    Module-level so scoring a candidate rate and reporting the final one share
    one definition; the scorer runs before a HeartEstimate exists.
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
    """How wide the peak at index `i` actually is, in lag samples.

    The exclusion band around each harmonic has to be at least as wide as the
    peak it is meant to exclude, or the peak's own shoulder leaks out and
    becomes the "unrelated" runner-up -- which drives the margin toward 1.0 for
    a perfectly good estimate.

    Neither a fixed width nor one proportional to best_lag does this. An ACF
    peak's width is set by the *bandwidth* of the filtered signal, which is the
    same regardless of the rate, so at short lags a proportional band is
    narrower than the peak. That is why 130-170 bpm went silent: the estimate
    was right, the confidence was not.

    Measured here instead: walk out from the peak until the ACF has fallen to
    half its height or starts climbing again (the next peak).
    """
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
    # Before the filter, not after: np.mean of an empty array warns.
    if x.size == 0 or not np.any(x):
        return ChannelEstimate(index, None, 0.0, 0.0)
    # filtfilt's padlen is 3 * max(len(a), len(b)) and a Butterworth of order N
    # has 2N+1 coefficients, so the floor is 3*(2N+1) = 27 for order 4 -- not
    # 3*2N. At 25-27 samples the old bound let a call through that raises.
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
    # The exclusion width is the peak's own measured half-width, the same
    # absolute number of lag samples at every multiple. Not a fraction of
    # best_lag: that widens with the multiple (at ratio 4 a 0.12 band spans
    # +/-0.48*best_lag of raw lag, inflating the margin) and, at short lags,
    # is narrower than the peak so the peak's shoulder becomes its own rival.
    ratio = lags / best_lag
    # Capped at 0.4*best_lag so a very broad peak cannot mask the midpoint
    # between two multiples, which is where a genuine competing period would sit.
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
        return HeartEstimate(None, 0.0, estimates, "no channel produced a rate",
                             rejected_by="no_signal")

    # Median over channels: robust to one bad emitter without needing a
    # preferred channel nominated in advance. Which emitter is best-seated
    # varies between sessions.
    bpm = float(np.median([e.bpm for e in usable]))

    within = channel_agreement(bpm, estimates)
    # Three terms, because each catches a failure the others miss.
    #
    #   within  -- channel agreement. Catches one badly seated emitter. Does NOT
    #              catch an octave error: measured on the recovery fixture, all
    #              four channels agreed on a rate an octave high.
    #   snr     -- normalised ACF height, already 0..1. Catches a window with no
    #              pulse in it. A clear pulse sits around 0.3-0.6 here.
    #   margin  -- how decisively the chosen period beat its nearest unrelated
    #              rival. Catches the 10-35s post-exercise windows, where two
    #              genuinely different periods were nearly tied. It does NOT
    #              catch the window at t=0; see the module docstring.
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
        # Capped. Unbounded, a 60s gap allows 180 bpm of movement, which lets
        # every octave error through while still treating the stale anchor as
        # authoritative -- the rule becomes permissive exactly when its
        # reference is least trustworthy. Past the cap the anchor should be
        # abandoned rather than stretched, which the tracker does.
        allowed = min(MAX_BPM_CHANGE_PER_S * seconds_since_previous,
                      MAX_CONTINUITY_JUMP_BPM)
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
                rejected_by="continuity",
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
            # Only a continuity rejection is evidence against the anchor. A
            # low-confidence or no-signal window says nothing about whether the
            # anchor is still right -- the wearer may simply have moved for a
            # moment -- so those leave the counter alone rather than resetting
            # it or advancing it. Two disagreements still mean two, whether or
            # not an unreadable window fell between them.
            if self.bpm is not None and est.rejected_by == "continuity":
                self._rejections += 1
                if self._rejections >= self.reacquire_after:
                    self.bpm = None
                    self._rejections = 0
                    fresh = estimate_window(channels, fs, None, 0.0)
                    if fresh.bpm is not None:
                        fresh.reason = "re-acquired after repeated rejections"
                        # Same bar as any other anchor. This is the path most
                        # likely to be taken right after a bad lock, so letting
                        # it adopt a merely-reportable window would put the
                        # weakest test on the most exposed path.
                        if fresh.confidence >= self.anchor_min_confidence:
                            self.bpm = fresh.bpm
                    return fresh
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
