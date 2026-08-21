"""RMSSD from the headband's optical channels, when it can be had at all.

RMSSD is the root-mean-square of *successive differences* between beat
intervals -- a different question from the rate derivation next door, so it
lives in its own module. `ppg_processing` measures the dominant period over
a whole window by autocorrelation, so a few malformed beats barely move its
answer. RMSSD needs *every* beat correct: one missed beat merges two
intervals into a double-length one and contributes its error twice, enough
to dominate a 30-beat window.

Measured: a per-channel peak detector produced 29-246ms across four
channels watching the same heart, against a physiological range of 20-50ms
-- disagreement bigger than the metric's whole valid range.

What makes it usable
--------------------
Two steps, neither enough alone:

1. **Consensus.** Keep only beats most channels agree happened. Fixes
   missed/spurious beats -- on the resting fixture, RMSSD 134ms -> 65ms,
   beat count 34 against 35 expected.
2. **Averaging the beat time across channels that saw it.** Fixes *when*
   the beat is marked; averaging k independent marks scales timing jitter
   by 1/sqrt(k). With both: 65ms -> 39.0ms resting, 45.7ms on the settled
   window of a through-exercise recording -- inside physiological range.

What it was validated against
-----------------------------
Six simultaneous watch ECGs across one 8-minute seated recording:
**r = 0.75, mean bias -1.3ms, RMS error 4.7ms**, every window within 15%,
heart rate within 1 bpm on all six. See `tests/test_hrv_against_dense_ecg.py`.

Those figures are for **30s** windows. The production path derives over 25s
(matching the rate estimator): r = 0.78, bias -3.1ms, RMS 5.2ms, worst
window 21%, nothing gated out -- correlation holds, accuracy is slightly
worse. Pinned separately in `test_optics_rmssd.py`; don't reuse the 30s
figures for the shipped path.

So it ships, as an enrichment
-----------------------------
`heart_signals.stress_score` is defined on heart rate alone, with RMSSD
added when available -- a hard requirement, not a preference, since RMSSD is
unavailable whenever the headband is off and roughly one window in five is
gated out even when it's on. A score whose definition shifted when an input
dropped out would be unreadable across a session.

Two things worth knowing about the true signal
------------------------------------------------
- **True RMSSD moves more than a short capture implies**: 29.2-48.9ms across
  4.5 minutes at near-constant heart rate. A shorter 95-second capture that
  measured 2.0ms does not mean the true value is constant -- a lot of the
  spread previously blamed on this module was the wearer's actual heart.
- **A one-off 50% outlier did not recur** across five reported windows in
  the dense capture (all within 15%), so it was one bad window, not a
  characteristic failure.

And it inherits the motion defect whole
---------------------------------------
Worse: it hides it. On the window 25s after exercise, where the rate
derivation reports a confident and wrong 127 bpm, this module returns
**coverage 0.98 and RMSSD 25.6ms** -- the healthiest-looking output in the
whole fixture set, computed against an oscillation that was not the heart.
Beat coverage can't catch it, since the beats are perfectly consistent with
the wrong rate.

So the gate here can't be "does this look self-consistent" -- it has to
defer to the rate derivation's own confidence, which is why `estimate_hrv`
takes the rate rather than deriving a second opinion. It inherits the
rate's scope exactly: **validated for seated use, not for gait.**
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil

import numpy as np
from scipy.signal import find_peaks

from .ppg_processing import BANDPASS_ORDER, MAX_BPM, MIN_BPM, _parabolic_peak, bandpass

# Physiologically possible beat intervals, in ms, mirroring MIN_BPM/MAX_BPM
# in ppg_processing (42 bpm = 1429ms, 180 bpm = 333ms). An interval outside
# this is a detector artefact, dropped before it can contribute its square.
MIN_IBI_MS = 60_000.0 / MAX_BPM
MAX_IBI_MS = 60_000.0 / MIN_BPM

# How far one interval may sit from the window's median before it's treated
# as an artefact rather than a beat. This is the filter that matters.
#
# 0.20, not tighter, because tightening stops paying off: 0.30 gives a
# 29-68ms spread on the paired recording, 0.20 gives 29-63, and 0.15 gives
# 26-52 while starting to cut into genuine variability -- the quantity being
# measured. 0.20 is also the conventional clinical value.
IBI_DEVIATION_FRACTION = 0.20

# How close two channels' marks must be to count as the same beat. The
# pulse reaches the four emitters at slightly different times, each with
# its own detection error, so this is a real tolerance, not rounding.
# Measured insensitive between 40-80ms (39.0-41.5ms on the resting fixture).
BEAT_MATCH_TOLERANCE_S = 0.06

# Peak prominence in standard deviations of the filtered signal. Measured
# insensitive too -- 0.2 through 0.5 give identical results on the fixtures,
# since after the bandpass the pulse is the only thing left with this shape.
PEAK_PROMINENCE_SD = 0.3

# Fraction of populated channels that must have seen a beat. 0.75 is the
# tuned 3-of-4, expressed as a fraction so it still means something on the
# 8- and 16-channel optics presets (`MUSE_OPTICS_PRESET` 1031-1034) -- a
# flat count of 3 would be 3-of-16 there, barely an agreement requirement.
CONSENSUS_FRACTION = 0.75

# ...but never unanimity, where there are channels to spare. Requiring every
# channel would discard real beats one badly seated emitter missed.
MAX_CHANNELS_SPARED = 1

# Below this many channels with any detections, there's no consensus to
# take, so refuse rather than report one channel's opinion.
#
# This is the real gate. A single channel run through the six
# ECG-referenced windows reports every one -- never refusing -- at up to
# +75% error, because with one channel neither the agreement requirement
# nor the cross-channel averaging does anything. It's also not caught
# downstream: `estimate_window`'s agreement term is 1.00 by construction
# against a single waveform, so the confidence gate below can be passed at
# 1.00 by exactly the window that deserves it least.
MIN_POPULATED_CHANNELS = 2

# Detected beats as a fraction of what the rate implies. Below this, beats
# are being missed and every miss inflates RMSSD.
#
# 0.95, not looser, because the settled window of the recovery fixture
# scores 0.91 with a *correct* 66.2 bpm and yields 134.6ms -- three times
# the plausible value. The rate can be right while the beats aren't, so
# this is a second, independent gate, not redundant with rate confidence.
MIN_BEAT_COVERAGE = 0.95

# The upper bound the lower one alone can't give. Coverage well above 1
# means beats were detected that the rate says aren't there (a
# double-detected notch, or a rate an octave low), invisible to every
# count-based statistic below it.
#
# 1.15, not nearer 1.0: a 25s window at 70 bpm expects 29.2 beats and can
# honestly hold 30, so a single boundary beat is already 1.03. Genuine
# 4-channel windows measured up to 1.054; single-channel runs of the same
# windows produce 1.20-1.26, and a doubled rate lands near 2.0.
MAX_BEAT_COVERAGE = 1.15

# The rate derivation's own confidence, below which RMSSD isn't attempted.
# Higher than its MIN_CONFIDENCE of 0.55: good enough to report a rate from
# isn't automatically good enough to count individual beats in.
MIN_RATE_CONFIDENCE = 0.9

# Fewer successive differences than this and the mean of squares is
# dominated by whichever one happened to be worst.
MIN_INTERVALS = 10


@dataclass
class HrvEstimate:
    """RMSSD for one window, or the reason there isn't one."""
    rmssd_ms: float | None
    # Beats accepted by consensus, as a fraction of what the rate implies
    # should be in the window. Near 1.0 means none missed, none invented.
    #
    # **None when no beat was ever counted**, not the same as 0.0: the
    # rate-confidence gate returns before `consensus_beats` runs, so a 0.0
    # here would wrongly read as "0% of beats detected" when detection was
    # never attempted.
    coverage: float | None
    beat_count: int
    interval_count: int
    # Machine-readable cause, when there is one: "rate_confidence" |
    # "coverage" | "excess_beats" | "too_few_intervals" | "no_rate". Control
    # flow matches this; `reason` is for display only.
    rejected_by: str | None = None
    reason: str = ""
    beat_times_s: list[float] = field(default_factory=list)


def detect_beats(x: np.ndarray, fs: float) -> np.ndarray:
    """Beat times in seconds for one channel, refined below the sample grid.

    The parabolic refinement matters here: at 64Hz the grid is 15.6ms apart
    and RMSSD values are 20-50ms, so rounding to the nearest sample would
    put quantisation noise on the same scale as the quantity. Missed beats
    are still the dominant error, by an order of magnitude, but this is the
    one that would remain after they're fixed.
    """
    x = np.asarray(x, dtype=float)
    # `filtfilt`'s padlen, which requires the input to exceed it *strictly*
    # (equal is already too short). Imports the order from ppg_processing
    # instead of repeating it so a filter change can't desync the two.
    if x.size <= 3 * (2 * BANDPASS_ORDER + 1):
        return np.empty(0)
    y = bandpass(x, fs)
    sd = float(np.std(y))
    if sd <= 0:
        return np.empty(0)
    y = y / sd

    peaks, _ = find_peaks(
        y,
        distance=max(1, int(fs * 60.0 / MAX_BPM)),
        prominence=PEAK_PROMINENCE_SD,
    )

    refined = [_parabolic_peak(y, i) for i in peaks]
    return np.asarray(refined) / fs


def _channels_needed(populated: int) -> int:
    """How many channels must agree, given how many produced any detections.

        populated   2   3   4   8   16
        needed      2   2   3   6   12

    Three constraints combined, not a single `min()`: the fraction scales
    with the preset, sparing one channel keeps a badly seated emitter from
    vetoing every beat, and the floor of two stops that sparing from
    collapsing agreement to one channel when only two are populated.
    """
    spared = populated - MAX_CHANNELS_SPARED
    return max(MIN_POPULATED_CHANNELS,
               min(spared, ceil(CONSENSUS_FRACTION * populated)))


def consensus_beats(channels: np.ndarray, fs: float) -> list[float]:
    """Beat times agreed by most channels, each averaged over the channels that
    saw it.

    Both halves matter and fix different errors: agreement removes beats
    that aren't there (but restores none that are missing), while averaging
    reduces timing jitter on the beats that survive. Measured separately on
    the resting fixture: consensus alone 134 -> 65ms, plus averaging 65 -> 39ms.
    """
    channels = np.asarray(channels, dtype=float)
    if channels.ndim == 1:
        channels = channels[:, None]

    per_channel = [detect_beats(channels[:, c], fs) for c in range(channels.shape[1])]
    populated = [b for b in per_channel if b.size]
    if len(populated) < MIN_POPULATED_CHANNELS:
        # Not "fall back to what we have" -- with one channel this function
        # would just return that channel's own detections unfiltered. See
        # MIN_POPULATED_CHANNELS for why that's refused, not returned.
        return []

    # Roll call from the channel with the most detections, not channel 0 --
    # starting from a sparse channel would silently cap the result at its
    # beat count, reading as a clean recording rather than a poor reference.
    reference = max(populated, key=len)
    needed = _channels_needed(len(populated))

    agreed: list[float] = []
    for t in reference:
        marks = []
        for beats in populated:
            nearest = beats[np.argmin(np.abs(beats - t))]
            if abs(nearest - t) < BEAT_MATCH_TOLERANCE_S:
                marks.append(nearest)
        if len(marks) >= needed:
            agreed.append(float(np.mean(marks)))
    return agreed


def rmssd_from_beats(beat_times_s: list[float]) -> tuple[float | None, int]:
    """RMSSD in ms, and the number of intervals it was computed over.

    Intervals outside the physiological range are dropped, not clamped -- a
    clamped interval is a fabricated one, and it lands in the successive
    difference twice.
    """
    if len(beat_times_s) < 3:
        return None, 0
    ibi = np.diff(np.asarray(beat_times_s)) * 1000.0

    # Two filters; the absolute one alone is nearly useless here. 333-1429ms
    # spans 42-180 bpm, so at a resting interval near 860ms it admits
    # anything from a badly early beat to a merged one. With only that
    # filter, RMSSD over a still subject at a flat 70 bpm ranged 29-240ms
    # across 34 windows against an ECG-measured 29.9ms.
    #
    # The relative filter is the standard HRV artefact criterion and is what
    # makes this usable: the same 34 windows come back 29-63ms with it.
    #
    # A longer window does NOT substitute for this. Measured at
    # 30/45/60/90/120s, the spread got *worse*, converging on ~170ms -- the
    # error is a small number of badly timed beats, not random jitter, so a
    # longer window just guarantees catching more of them.
    median_ibi = float(np.median(ibi))
    valid = (
        (ibi >= MIN_IBI_MS)
        & (ibi <= MAX_IBI_MS)
        & (np.abs(ibi - median_ibi) <= IBI_DEVIATION_FRACTION * median_ibi)
    )

    # Successive differences are taken WITHIN runs of adjacent valid
    # intervals, never across a dropped one. Filtering the array then
    # diffing it looks equivalent and isn't: it would silently pair two
    # intervals that were never consecutive, inflating exactly the windows
    # whose signal was already worst.
    diffs: list[np.ndarray] = []
    run: list[float] = []
    for value, ok in zip(ibi, valid):
        if ok:
            run.append(float(value))
            continue
        if len(run) >= 2:
            diffs.append(np.diff(np.asarray(run)))
        run = []
    if len(run) >= 2:
        diffs.append(np.diff(np.asarray(run)))

    if not diffs:
        return None, 0
    all_diffs = np.concatenate(diffs)
    # Count successive differences, not surviving intervals -- an interval
    # stranded between two dropped ones contributes nothing to the result,
    # so counting it toward MIN_INTERVALS would let isolated pairs clear a
    # gate meant to ensure the mean of squares has enough terms.
    return float(np.sqrt(np.mean(all_diffs ** 2))), int(all_diffs.size)


def estimate_hrv(
    channels: np.ndarray,
    fs: float,
    bpm: float | None,
    rate_confidence: float,
) -> HrvEstimate:
    """RMSSD for one window, gated on the rate derivation having succeeded.

    `bpm` and `rate_confidence` come from `ppg_processing.estimate_window`
    over the same samples, deliberately not re-derived here: the two must
    agree on whether the window is usable, and a second opinion would be a
    second thing to keep in step.

    **The rate being right does not make the beats right, and vice versa.**
    A window with a correct 66.2 bpm can yield 134.6ms from beats that are
    9% short, and a window with a wrong 127 bpm can yield a healthy-looking
    25.6ms from beats perfectly consistent with the wrong rate. So both
    gates apply, and neither is inferred from the other.
    """
    channels = np.asarray(channels, dtype=float)
    if channels.ndim == 1:
        channels = channels[:, None]
    duration_s = len(channels) / fs if fs > 0 else 0.0

    if bpm is None:
        return HrvEstimate(None, None, 0, 0, "no_rate",
                           "no heart rate for this window")

    # First gate: the rate derivation's own verdict, checked before any beat
    # work -- a window it distrusts can't produce a trustworthy RMSSD.
    if rate_confidence < MIN_RATE_CONFIDENCE:
        # `coverage=None`, not 0.0 -- no beat was counted here, which is a
        # different claim from none having been found.
        return HrvEstimate(None, None, 0, 0, "rate_confidence",
                           f"rate confidence {rate_confidence:.2f} below "
                           f"{MIN_RATE_CONFIDENCE}")

    beats = consensus_beats(channels, fs)
    expected = bpm / 60.0 * duration_s
    coverage = len(beats) / expected if expected > 0 else 0.0

    # Second gate, independent of the first: are these all the beats and
    # only the beats? Missing beats inflate RMSSD even when the rate is
    # correct; invented ones deflate it and look healthier than the truth --
    # hence bounding both ways.
    if coverage < MIN_BEAT_COVERAGE:
        return HrvEstimate(None, coverage, len(beats), 0, "coverage",
                           f"only {coverage:.0%} of expected beats detected",
                           beat_times_s=beats)

    if coverage > MAX_BEAT_COVERAGE:
        return HrvEstimate(None, coverage, len(beats), 0, "excess_beats",
                           f"{coverage:.0%} of expected beats detected -- more "
                           f"beats than the rate accounts for",
                           beat_times_s=beats)

    rmssd, intervals = rmssd_from_beats(beats)
    if rmssd is None or intervals < MIN_INTERVALS:
        return HrvEstimate(None, coverage, len(beats), intervals,
                           "too_few_intervals",
                           f"{intervals} usable intervals, need {MIN_INTERVALS}",
                           beat_times_s=beats)

    return HrvEstimate(rmssd, coverage, len(beats), intervals,
                       beat_times_s=beats)
