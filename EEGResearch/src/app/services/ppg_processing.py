"""Heart rate from the headband's optical channels.

Every decision here is forced by measurements on real recordings, not chosen
for elegance -- the obvious implementation is wrong in four ways, each
producing a plausible-looking number rather than an error. Evidence is in
tests/fixtures, reasoning in tests/fixtures/README.md; a short version follows.

What the input is
-----------------
OPTICS samples from a Muse S Athena on PRESET_1035: 4 channels at ~64Hz, in
microamps, two wavelengths (730nm/850nm) on left and right. Other Muse models
deliver the same shape via the PPG packet instead -- this code only cares that
it's a set of optical channels at a known rate.

Motion, and the one window no single window catches
----------------------------------------------------
The hardest case: the window starting the moment a wearer sits down after
exercise reads 127 bpm on all four channels against a true rate near 90, at
confidence 1.00. Nothing inside that one window catches it -- cross-channel
agreement can't (every channel makes the same error), out-of-band power can't
(the resting recording scores higher), and the peak margin can't (3.3, as
decisive as a clean window).

What catches it is the tracker, across windows: an unanchored candidate is held
until a second window agrees, so a periodicity gone a step later is never
published. Motion settling fades a step later; a heartbeat doesn't. The same
rule applies to re-acquisition after a dropped lock, and matters more there,
since whatever re-acquires comes from exactly the population this rule
distrusts.

The cost is one window of latency at session start -- a delay, not a refusal:
a genuinely fast rate still gets published, just one window late.

Motion is worse than that, and it is not solvable here
--------------------------------------------------------
A continuous rest -> exercise -> recovery recording reports 162-167 bpm at
confidence 1.00 for six consecutive windows against a watch-verified 104 --
that's step cadence, not heart rate. 166/104 = 1.60 bears no harmonic relation
to the truth, so no first-peak rule, octave test, or margin can see it: the
optical signal genuinely contains a strong clean periodicity at 2.77Hz, and
this module correctly reports the oscillator it was given.

Nothing derived from the optical channels alone distinguishes those two
oscillators. The fix would be the headband's accelerometer, which the bridge
doesn't capture yet -- it's the only signal independent of this periodicity.

So: motion degrades into a confident wrong answer, not silence. A confident
value is not evidence of a correct one, and anything recording a rate must
discard rather than store when movement is plausible. Any future fix claiming
to catch this window must be checked against 120-180 bpm rates too, since an
earlier attempt at a fix (too-narrow exclusion band) rejected real high rates
along with the motion artifact.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import butter, filtfilt

# Physiological search range, wide enough for a resting child and a sprinting
# one. Deliberately not narrowed to dodge interference -- a narrower band only
# "works" by coincidentally containing the rate of whatever recording tested it.
MIN_BPM = 42.0
MAX_BPM = 180.0

# Bandpass corners. The low one removes baseline wander -- perfusion, breathing
# and micro-movement peak around 0.2Hz and still dominate at 0.7Hz, so an argmax
# on undetrended data returns the band edge, not a heartbeat.
#
# The high one matters just as much. Autocorrelation normalises by total power
# (the zero-lag value), so broadband noise up to Nyquist (32Hz) inflates the
# denominator and crushes the pulse peak -- without this cutoff the normalised
# peak at rest measured 0.00-0.29 and most channels produced nothing. 5Hz keeps
# the fundamental and its first harmonic and discards the rest.
BANDPASS_LOW_HZ = 0.6
BANDPASS_HIGH_HZ = 5.0
BANDPASS_ORDER = 4

# A known non-cardiac component at ~0.74Hz (44.5 bpm) appears in every recording
# from this headband. Ruled out as cardiac by experiment: present at rest and
# after exercise, never moving, while the true rate went 67.9 -> 76.4. Not
# excluded from search -- 44.5 bpm is a real rate for someone else -- but tracked
# so it can be reported as a competing peak.
KNOWN_INTERFERER_HZ = 0.742
INTERFERER_TOLERANCE_HZ = 0.05

# How far the rate may move between consecutive windows, in bpm per second. A
# heart accelerates fast under load but not arbitrarily fast -- this exists to
# reject octave errors, which show up as a 2x or 0.5x jump in one window step.
MAX_BPM_CHANGE_PER_S = 3.0

# Below this, a window's estimate is not reported as a rate at all.
#
# Set at 0.55 rather than lower because of what sits just under it: in the
# 10-30s after exercise the estimator produced 58 and 55 bpm against a true rate
# near 90, confidently enough to pass a looser threshold but wrong enough to
# matter, with no other rule here to catch them. This bar drops those two
# windows while resting and settled-recovery windows still clear it comfortably.
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
    # means two candidates nearly tied -- what an octave ambiguity looks like
    # from inside a single channel.
    margin: float
    # Normalised autocorrelation height at the chosen period: 1.0 = perfectly
    # repeating waveform, a clear pulse sits around 0.3-0.6, noise well below
    # 0.1. Named snr for brevity; it's a periodicity strength, not a power ratio.
    snr: float


@dataclass
class HeartEstimate:
    """The fused answer for one window."""
    bpm: float | None
    confidence: float
    channels: list[ChannelEstimate] = field(default_factory=list)
    # Human-readable, for display. Never branch on this text -- use rejected_by.
    reason: str = ""
    # Machine-readable cause, when there is one: "continuity" | "confidence" |
    # "no_signal" | "unconfirmed_anchor". This is what control flow matches on.
    #
    # `unconfirmed_anchor` is a withheld reading, not an absent one: the window
    # produced a rate and the tracker is waiting for a second window to agree.
    # Reporting it as no_signal would falsely say the sensor saw nothing.
    rejected_by: str | None = None

    @property
    def agreement(self) -> float:
        """Fraction of channels within 5 bpm of the reported rate."""
        return channel_agreement(self.bpm, self.channels)


def channel_agreement(bpm: float | None, channels: list[ChannelEstimate]) -> float:
    """Fraction of channels within 5 bpm of `bpm`.

    A quality signal, not a correctness one: all four channels have agreed on a
    wrong rate twice (~127 bpm after motion, ~166 bpm during exercise against a
    watch-verified 104). Unanimity proves the channels saw the same thing, and
    under motion that thing isn't the heart.

    Module-level so scoring a candidate and reporting the final rate share one
    definition; the scorer runs before a HeartEstimate exists.
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

    The exclusion band around each harmonic must be at least as wide as the peak
    it excludes, or the peak's own shoulder leaks out and becomes the
    "unrelated" runner-up, driving the margin toward 1.0 for a good estimate.

    Neither a fixed width nor one proportional to best_lag works: an ACF peak's
    width is set by the filtered signal's bandwidth, constant regardless of
    rate, so a proportional band is too narrow at short lags -- which is why
    130-170 bpm used to go silent even with a right estimate.

    Measured directly instead: walk out from the peak until the ACF falls to
    half its height or starts climbing again (the next peak).
    """
    # The 2.0 floor doubles as the self-exclusion width (ratio 1 is just another
    # multiple). It's adequate only because BANDPASS_HIGH_HZ caps the signal at
    # 5Hz, keeping an ACF peak from being narrower than a couple of lag samples.
    # If that ceiling ever rises, raise this floor with it, or every margin
    # collapses silently at short lags.
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

    At 64Hz the lag grid is 15.6ms apart. Rounding to the nearest lag quantises
    every beat interval by up to 7.8ms -- the same order as the RMSSD values
    this eventually feeds -- so this recovers a fraction of a sample instead.
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

    Autocorrelation rather than a spectral argmax because the dominant failure
    on this hardware is the octave error (reading 2x or 0.5x the true rate). A
    harmonic-rich pulse waveform puts real energy at 2f, and an FFT argmax has no
    way to prefer f -- it just takes the taller peak. Autocorrelation peaks at
    the true period and its multiples, so taking the first strong peak resolves
    the ambiguity by construction.
    """
    x = np.asarray(x, dtype=float)
    # Before the filter, not after: np.mean of an empty array warns.
    if x.size == 0 or not np.any(x):
        return ChannelEstimate(index, None, 0.0, 0.0)
    # filtfilt's padlen is 3 * max(len(a), len(b)); a Butterworth of order N has
    # 2N+1 coefficients, so the floor is 3*(2N+1) = 27 for order 4, not 3*2N.
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

    # The FIRST strong peak, not the tallest -- this is the whole reason for
    # using autocorrelation. The ACF peaks at the true period and every multiple
    # of it, and multiples are often taller (a longer lag correlates more of the
    # waveform's slow structure), so argmax would report a subharmonic. Scanning
    # forward and stopping at the first peak clearing the threshold picks the
    # fundamental by construction.
    peaks = []
    for i in range(1, len(window) - 1):
        if window[i] >= window[i - 1] and window[i] > window[i + 1]:
            peaks.append(i)
    if not peaks:
        return ChannelEstimate(index, None, 0.0, 0.0)

    tallest = max(window[i] for i in peaks)
    if tallest <= 0:
        return ChannelEstimate(index, None, 0.0, 0.0)
    # 0.75 of the tallest peak: high enough to skip noise ripple, low enough that
    # a fundamental slightly shorter than its own harmonic still wins.
    strong = [i for i in peaks if window[i] >= 0.75 * tallest]
    best = strong[0]

    lag = _parabolic_peak(acf, lag_min + best)
    bpm = 60.0 * fs / lag if lag > 0 else None

    # Periodicity strength: normalised ACF height at the chosen lag. 1.0 would be
    # a perfectly repeating waveform; a clear pulse here sits around 0.3-0.6,
    # noise well below 0.1.
    snr = float(window[best])

    # Margin against the tallest unrelated competing peak. Near 1.0 means two
    # genuinely different periods were nearly tied -- what an octave ambiguity
    # looks like from inside one channel.
    #
    # Peaks at integer multiples of the chosen lag are excluded because they're
    # evidence FOR this period, not against it -- an autocorrelation repeats at
    # every multiple of the true period. Counting them as rivals made a
    # perfectly periodic signal score worst of all (a pure sine gets equal peaks
    # at every multiple: margin 1.0, confidence zero).
    best_lag = lag_min + best
    lags = np.arange(len(window)) + lag_min
    # The exclusion width is the peak's own measured half-width, applied as the
    # same absolute number of lag samples at every multiple -- not a fraction of
    # best_lag, which would widen with the multiple and inflate the margin, and
    # would be too narrow at short lags to cover the peak's own shoulder.
    ratio = lags / best_lag
    # Knowingly asymmetric: the width is measured on the chosen peak but applied
    # at every multiple, while real harmonic peaks broaden roughly with the
    # multiple (beat-interval jitter accumulates with lag). So the band can run
    # slightly narrow at ratio 3-4, which only lowers the margin (costs a
    # reportable window) rather than producing a wrong rate. Not observed on any
    # fixture.
    #
    # The cap bounds the other direction: a very broad peak must not mask the
    # midpoint between two multiples, where a genuine competing period would sit.
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
    agreement can't: every channel agreed on a rate an octave high in the 25s
    after exercise on a real recording. Agreement only proves the channels saw
    the same thing, not that it was a heartbeat.
    """
    channels = np.asarray(channels, dtype=float)
    if channels.ndim == 1:
        channels = channels[:, None]

    estimates = [estimate_channel(channels[:, c], fs, c) for c in range(channels.shape[1])]
    usable = [e for e in estimates if e.bpm is not None]
    if not usable:
        return HeartEstimate(None, 0.0, estimates, "no channel produced a rate",
                             rejected_by="no_signal")

    # Median over channels: robust to one bad emitter without needing to
    # nominate a preferred channel in advance -- which emitter is best-seated
    # varies between sessions.
    bpm = float(np.median([e.bpm for e in usable]))

    within = channel_agreement(bpm, estimates)
    # Three terms, each catching a failure the others miss.
    #
    #   within  -- channel agreement. Catches one badly seated emitter. Does NOT
    #              catch an octave error: on the recovery fixture, all four
    #              channels agreed on a rate an octave high.
    #   snr     -- normalised ACF height, 0..1. Catches a window with no pulse
    #              in it; a clear pulse sits around 0.3-0.6 here.
    #   margin  -- how decisively the chosen period beat its nearest unrelated
    #              rival. Catches the 10-35s post-exercise windows where two
    #              periods were nearly tied. Does NOT catch the window at t=0;
    #              see the module docstring.
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
        # Capped: uncapped, a 60s gap would allow 180 bpm of movement, letting
        # every octave error through while still trusting a now-stale anchor.
        # Past the cap the tracker abandons the anchor instead of stretching it.
        allowed = min(MAX_BPM_CHANGE_PER_S * seconds_since_previous,
                      MAX_CONTINUITY_JUMP_BPM)
        if abs(bpm - previous_bpm) > allowed:
            # Rejected, not "corrected": trying the other octave and adopting
            # whichever lands in range risks rewriting a correct rate to match a
            # wrong anchor. A continuity rule may discard an estimate; it must
            # not invent one, since the anchor it defends may itself be wrong.
            return HeartEstimate(
                None, 0.0, estimates,
                f"rejected {bpm:.1f} bpm: moved more than "
                f"{allowed:.0f} bpm from {previous_bpm:.1f}",
                rejected_by="continuity",
            )

    return HeartEstimate(bpm, confidence, estimates, reason)


class HeartRateTracker:
    """Sequential estimation with continuity, and recovery from a bad lock.

    Continuity rejects octave errors, but it needs an anchor, and the anchor can
    be wrong: the window right after motion can produce a confident, unanimous,
    incorrect rate. Anchored to that, every later correct estimate looks like a
    discontinuity and gets discarded, silently costing the next minute.

    So a run of consecutive rejections is treated as evidence against the
    anchor, not the data, and the tracker re-acquires.
    """

    def __init__(self, reacquire_after: int = 2, anchor_min_confidence: float = 0.65):
        self.bpm: float | None = None
        self.reacquire_after = reacquire_after
        # A window must clear a higher bar than "reportable" to become the
        # anchor every later window is judged against.
        self.anchor_min_confidence = anchor_min_confidence
        self._rejections = 0
        # A candidate anchor not yet seen twice. Nothing is published from it
        # until it is.
        self._pending: float | None = None

    def update(self, channels: np.ndarray, fs: float, seconds_since_previous: float) -> HeartEstimate:
        est = estimate_window(channels, fs, self.bpm, seconds_since_previous)

        if est.bpm is None:
            # Only a continuity rejection is evidence against the anchor. A
            # low-confidence or no-signal window says nothing about whether the
            # anchor is still right -- the wearer may have just moved briefly --
            # so those leave the counter alone. Two disagreements still mean
            # two, whether or not an unreadable window fell between them.
            if self.bpm is not None and est.rejected_by == "continuity":
                self._rejections += 1
                if self._rejections >= self.reacquire_after:
                    self.bpm = None
                    self._rejections = 0
                    fresh = estimate_window(channels, fs, None, 0.0)
                    if fresh.bpm is None:
                        self._pending = None
                        return fresh
                    # Re-acquisition goes through the same corroboration, and
                    # needs it most: a lock is dropped because two windows
                    # disagreed with it, which is what a mid-lesson motion event
                    # looks like -- so whatever re-acquires is drawn from
                    # exactly the population this rule distrusts.
                    self._pending = fresh.bpm
                    return HeartEstimate(
                        None, fresh.confidence, fresh.channels,
                        f"re-acquiring: holding {fresh.bpm:.1f} bpm until a "
                        f"second window agrees",
                        rejected_by="unconfirmed_anchor")
            # A window that produced nothing breaks the chain: a candidate is
            # only evidence about the present while the windows around it are
            # readable, and across a gap it's just an old number.
            self._pending = None
            return est

        self._rejections = 0
        if self.bpm is not None:
            self.bpm = est.bpm
            return est

        # ── unanchored: nothing is published until two windows agree ──
        #
        # The first window of a session, and the first after a dropped lock, has
        # no context and is also most likely to be contaminated -- a session
        # starts with the headband going on and a body settling. On the recovery
        # fixture the first 25s after exercise reads 127 bpm against a true rate
        # near 90, on all four channels, at confidence 1.00.
        #
        # No in-window test separates that from a real 127 (agreement,
        # out-of-band power, peak margin were all tried and failed -- see module
        # docstring). This is a cross-window rule instead: not "is this
        # periodicity cardiac" but "is it still there a step later". Motion
        # settling isn't; a heartbeat is.
        #
        # Costs one window of latency at session start; a genuinely fast rate is
        # published a step late, not rejected outright.
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
    """Whether a rate coincides with the non-cardiac component seen on this
    hardware. Not a rejection -- 44.5 bpm is a real rate for some people -- but
    callers may want to weight it down when a competing candidate exists."""
    return abs(bpm / 60.0 - KNOWN_INTERFERER_HZ) <= INTERFERER_TOLERANCE_HZ
