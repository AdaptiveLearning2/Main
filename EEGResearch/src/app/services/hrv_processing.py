"""RMSSD from the headband's optical channels, when it can be had at all.

RMSSD is the root-mean-square of *successive differences* between beat
intervals. That definition is why this is a separate module from the rate
derivation next door rather than a few more lines in it: the two need different
things from the signal and fail in different ways.

`ppg_processing` never identifies a single beat. It measures the dominant period
across a whole window by autocorrelation, so a handful of malformed beats barely
move the answer. RMSSD needs *every* beat, individually and correctly, and it
squares the difference between consecutive intervals -- so one missed beat, which
merges two intervals into a double-length one, contributes its whole error twice
over. In a 30-beat window a single miss is enough to dominate the result.

Measured: a per-channel peak detector produced 29-246ms across four channels
watching the same heart, against a physiological range of 20-50ms. The channels
disagreed by more than the entire range the metric is supposed to span.

What makes it usable
--------------------
Two steps, and neither alone is enough:

1. **Consensus.** Keep only beats that most channels agree happened. This is
   what fixes the missed and spurious beats; on the resting fixture it took
   RMSSD from 134ms to 65ms and brought the beat count to 34 against 35 expected.
2. **Averaging the beat time across the channels that saw it.** This is what
   fixes *when* the beat is marked. Averaging k independent marks scales timing
   jitter by 1/sqrt(k). With both, the resting fixture reads 39.0ms and the
   settled window of the through-exercise recording 45.7ms -- inside the
   physiological range for the first time.

What it was validated against
-----------------------------
Six simultaneous watch ECGs across one 8-minute seated recording, ~50s apart:
**r = 0.75, mean bias -1.3ms, RMS error 4.7ms**, every reported window within
15%, heart rate within 1 bpm on all six. Six references rather than three is what
made it a result -- against three points every candidate fix merely moved *which*
window was wrong, which is indistinguishable from an improvement.
`tests/test_hrv_against_dense_ecg.py` holds the table.

Those figures are for **30s** windows. `optics_processing` derives over the 25s
the rate estimator was validated on, where the same six windows give r = 0.78,
bias -3.1ms, RMS 5.2ms and a worst window of 21% -- correlation holds, accuracy
is slightly worse, and no window is gated out. The production path is pinned to
its own numbers in `test_optics_rmssd.py`; don't carry the 30s figures across.

So it ships, as an enrichment
-----------------------------
`heart_signals.stress_score` is defined on heart rate alone and RMSSD is added
when available. That is a hard requirement rather than a preference: this is
unavailable whenever the headband is off, and roughly one window in five is gated
out even when it is on. A score whose definition shifted when an input dropped
out would be unreadable across a session.

Two claims an earlier version of this docstring made, both now corrected:

- **The true RMSSD moves far more than one short capture implies** -- 29.2-48.9ms
  across 4.5 minutes here at near-constant heart rate. An earlier capture measured
  2.0ms across 95 seconds and that got generalised into "the true value is
  constant, so all spread in the derived value is its own error". True at 95
  seconds, false at five minutes: a substantial part of the spread previously
  blamed on this module was the wearer's heart.
- **The 50% outlier did not recur.** Five of five reported windows in the dense
  capture land within 15%, so it was one window in one recording rather than a
  characteristic failure -- and a discriminator built to catch it would have been
  fitted to noise.

And it inherits the motion defect whole
---------------------------------------
Worse than inherits: it hides it. On the window 25s after exercise, where the
rate derivation reports a confident and wrong 127 bpm, this module returns
**coverage 0.98 and RMSSD 25.6ms** -- the healthiest-looking output in the whole
fixture set, computed against an oscillation that was not the heart. Beat
coverage cannot catch it, because the beats are perfectly consistent with the
wrong rate.

So the gate below cannot be "does this look self-consistent". It has to defer to
the rate derivation's own confidence, which is why `estimate_hrv` takes the rate
rather than deriving a second opinion. That inherits the rate's scope exactly:
**seated use, which is validated; gait, which is not.** Nothing here narrows or
widens it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil

import numpy as np
from scipy.signal import find_peaks

from .ppg_processing import BANDPASS_ORDER, MAX_BPM, MIN_BPM, _parabolic_peak, bandpass

# Physiologically possible beat intervals, in ms. The bounds mirror MIN_BPM and
# MAX_BPM in ppg_processing: 42 bpm is 1429ms, 180 bpm is 333ms. An interval
# outside this is a detector artefact rather than a beat, and is dropped before
# it can contribute its square to the result.
MIN_IBI_MS = 60_000.0 / MAX_BPM
MAX_IBI_MS = 60_000.0 / MIN_BPM

# How far one interval may sit from the window's median before it is treated as
# a detector artefact rather than a beat. This is the filter that matters; see
# rmssd_from_beats for the measurement.
#
# 0.20 rather than tighter because the tightening stops paying: 0.30 gives a
# 29-68ms spread across the paired recording, 0.20 gives 29-63, and 0.15 gives
# 26-52 while starting to cut into genuine variability -- which is the quantity
# being measured, so an aggressive filter here manufactures a low answer rather
# than a correct one. 0.20 is also the conventional value, which matters for a
# number a clinician might one day compare against a published range.
IBI_DEVIATION_FRACTION = 0.20

# How close two channels' marks must be to count as the same beat. The pulse
# reaches the four emitters at slightly different times and each is marked with
# its own error, so this is a real tolerance rather than a rounding allowance.
# Measured insensitive between 40 and 80ms (39.0 - 41.5ms on the resting
# fixture); the detector is not balanced on this number.
BEAT_MATCH_TOLERANCE_S = 0.06

# Peak prominence in standard deviations of the filtered signal. Also measured
# insensitive -- 0.2 through 0.5 give identical results on the fixtures, because
# after the bandpass the pulse is the only thing left with this shape.
PEAK_PROMINENCE_SD = 0.3

# What fraction of the populated channels must have seen a beat. 0.75 is the
# tuned 3-of-4 expressed proportionally, and it is a fraction rather than a
# count so that it still means something on the 8- and 16-channel optics presets
# (`MUSE_OPTICS_PRESET` 1031-1034): a flat 3 was tuned against four channels and
# would be 3-of-16 there, which is barely an agreement requirement at all.
CONSENSUS_FRACTION = 0.75

# ...but never unanimity, where there are enough channels to spare one. Requiring
# every channel would discard real beats that one badly seated emitter missed,
# which reintroduces exactly the gap consensus exists to close.
MAX_CHANNELS_SPARED = 1

# Below this many channels with any detections, there is no consensus to take and
# the answer is a refusal rather than one channel's opinion.
#
# This is the gate, not a formality. A single channel run through the six
# ECG-referenced windows reports every one of them -- never refusing -- at up to
# +75% error, because with one channel neither the agreement requirement nor the
# cross-channel averaging does anything and what is left is the raw per-channel
# detector this module exists to replace. It cannot be caught downstream either:
# `estimate_window`'s agreement term is 1.00 by construction against a single
# waveform, so the rate confidence gate below can be passed at 1.00 by exactly
# the window that least deserves it. Same shape as the camera-rPPG confidence
# trap in CLAUDE.md, and the reason it has to be tested here.
MIN_POPULATED_CHANNELS = 2

# Detected beats as a fraction of what the rate implies. Below this, beats are
# being missed and every miss inflates RMSSD.
#
# 0.95 rather than something more forgiving because of what sits between: the
# settled window of the recovery fixture scores 0.91 with a *correct* 66.2 bpm
# and yields 134.6ms, three times the plausible value. The rate can be right
# while the beats are not, so this threshold is not redundant with the rate
# derivation's confidence -- it is the second of two independent gates.
MIN_BEAT_COVERAGE = 0.95

# And the other side of it, which the lower bound alone does not give. Coverage
# well above 1 means beats were detected that the rate says are not there --
# a double-detected dicrotic notch, or a rate an octave low -- and without this
# they are indistinguishable from a clean window, because every count-based
# statistic below them looks healthy.
#
# 1.15 rather than something nearer 1.0: a 25s window at 70 bpm expects 29.2
# beats and can honestly contain 30, so a single boundary beat is already 1.03.
# Measured across the two ECG-referenced recordings, genuine 4-channel windows
# reach 1.054 -- a tighter bound would reject real data. The failures this is
# aimed at are far above it: single-channel runs of the same windows produce
# 1.20-1.26, and a doubled rate lands near 2.0.
MAX_BEAT_COVERAGE = 1.15

# The rate derivation's own confidence, below which RMSSD is not attempted.
# Higher than its MIN_CONFIDENCE of 0.55: a window good enough to report a rate
# from is not automatically good enough to count individual beats in.
MIN_RATE_CONFIDENCE = 0.9

# Fewer successive differences than this and the mean of their squares is
# dominated by whichever one happened to be worst.
MIN_INTERVALS = 10


@dataclass
class HrvEstimate:
    """RMSSD for one window, or the reason there isn't one."""
    rmssd_ms: float | None
    # Beats accepted by consensus, as a fraction of what the rate implies should
    # be in the window. Near 1.0 means none were missed and none were invented.
    #
    # **None when no beat was ever counted**, which is not the same claim as 0.0
    # and must not be stored as one: the rate-confidence gate returns before
    # `consensus_beats` runs, so a 0.0 there would reach the database as "0% of
    # beats detected" on a window where detection was never attempted. Same
    # can't-tell-no-data-from-zero rule the write path follows everywhere else.
    coverage: float | None
    beat_count: int
    interval_count: int
    # Machine-readable cause, when there is one: "rate_confidence" |
    # "coverage" | "excess_beats" | "too_few_intervals" | "no_rate". Control flow
    # matches this; `reason` is for display only.
    rejected_by: str | None = None
    reason: str = ""
    beat_times_s: list[float] = field(default_factory=list)


def detect_beats(x: np.ndarray, fs: float) -> np.ndarray:
    """Beat times in seconds for one channel, refined below the sample grid.

    The parabolic refinement is not a nicety here. At 64Hz the grid is 15.6ms
    apart and RMSSD values are 20-50ms, so rounding every beat to the nearest
    sample would put quantisation noise on the same scale as the quantity. It is
    not the dominant error -- missed beats are, by an order of magnitude -- but
    it is the one that would remain after they were fixed.
    """
    x = np.asarray(x, dtype=float)
    # `filtfilt`'s padlen, which it requires the input to exceed *strictly* --
    # equal is already too short. Written the same way as the guard in
    # `ppg_processing.estimate_channel`, and importing the order rather than
    # repeating it, so a filter change cannot desync the two.
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

    Three constraints at once, and the middle one is the reason this is not a
    single `min()`. The fraction scales with the preset; sparing one channel is
    what keeps a badly seated emitter from vetoing every beat -- but it is only
    affordable where losing one still leaves a real agreement, which is why the
    floor of two overrides it at two channels rather than collapsing to one.
    """
    spared = populated - MAX_CHANNELS_SPARED
    return max(MIN_POPULATED_CHANNELS,
               min(spared, ceil(CONSENSUS_FRACTION * populated)))


def consensus_beats(channels: np.ndarray, fs: float) -> list[float]:
    """Beat times agreed by most channels, each averaged over the channels that
    saw it.

    Both halves matter and they fix different errors: the agreement requirement
    removes beats that are not there and restores none that are missing, while
    the averaging reduces the timing jitter on the beats that survive. Measured
    separately on the resting fixture: consensus alone 134 -> 65ms, consensus
    plus averaging 65 -> 39ms.
    """
    channels = np.asarray(channels, dtype=float)
    if channels.ndim == 1:
        channels = channels[:, None]

    per_channel = [detect_beats(channels[:, c], fs) for c in range(channels.shape[1])]
    populated = [b for b in per_channel if b.size]
    if len(populated) < MIN_POPULATED_CHANNELS:
        # Not "fall back to what we have". With one channel this function is the
        # identity on that channel's detections, and it is the *only* thing
        # standing between them and a recorded number -- see MIN_POPULATED_CHANNELS.
        return []

    # The channel with the most detections is the roll-call, not channel 0.
    # Starting from a sparse channel silently caps the result at its beat count,
    # which reads as a clean recording rather than a poor reference.
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

    Intervals outside the physiological range are dropped rather than clamped.
    A clamped interval is a fabricated one, and it lands in the successive
    difference twice.
    """
    if len(beat_times_s) < 3:
        return None, 0
    ibi = np.diff(np.asarray(beat_times_s)) * 1000.0

    # Two filters, and the absolute one alone is nearly useless here.
    #
    # 333-1429ms spans 42-180 bpm, so at a resting interval near 860ms it
    # admits anything from a badly early beat to one that merged with its
    # neighbour. Measured with only that filter, RMSSD over a still subject at a
    # flat 70 bpm ranged 29-240ms across 34 windows -- against an ECG-measured
    # 29.9ms. The correct answer appeared occasionally and the rest was
    # contamination that passed the gate at coverage ~1.0.
    #
    # The relative filter is the standard HRV artefact criterion and it is what
    # makes the metric usable: the same 34 windows come back 29-63ms with it.
    #
    # A longer window does NOT substitute for this, which is worth knowing
    # because it is the intuitive fix. Measured at 30/45/60/90/120s, the spread
    # got *worse* and converged on ~170ms: the error is a small number of badly
    # timed beats rather than random jitter, so a longer window does not average
    # it down, it merely guarantees catching some.
    median_ibi = float(np.median(ibi))
    valid = (
        (ibi >= MIN_IBI_MS)
        & (ibi <= MAX_IBI_MS)
        & (np.abs(ibi - median_ibi) <= IBI_DEVIATION_FRACTION * median_ibi)
    )

    # Successive differences are taken WITHIN runs of adjacent valid intervals,
    # never across a dropped one. Filtering the array and then diffing it looks
    # equivalent and is not: it silently pairs two intervals that were never
    # consecutive, and inserts that fabricated difference into a metric defined
    # entirely by consecutive pairs. The error is largest exactly where the
    # signal was worst, so it inflates the windows already least trustworthy.
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
    # Successive differences, not surviving intervals. An interval stranded
    # between two dropped ones contributes nothing to the result, so counting it
    # towards MIN_INTERVALS would let six isolated pairs report twelve and clear
    # a gate whose whole purpose is that the mean of squares has enough terms.
    return float(np.sqrt(np.mean(all_diffs ** 2))), int(all_diffs.size)


def estimate_hrv(
    channels: np.ndarray,
    fs: float,
    bpm: float | None,
    rate_confidence: float,
) -> HrvEstimate:
    """RMSSD for one window, gated on the rate derivation having succeeded.

    `bpm` and `rate_confidence` come from `ppg_processing.estimate_window` over
    the same samples. This deliberately does not re-derive them: the two must
    agree about whether the window is usable, and a second opinion computed here
    would be a second thing to keep in step.

    **The rate being right does not make the beats right, and vice versa.** Both
    directions are observed in the fixtures -- a window with a correct 66.2 bpm
    yields 134.6ms from beats that are 9% short, and a window with a wrong
    127 bpm yields a healthy-looking 25.6ms from beats that are perfectly
    consistent with the wrong rate. So both gates are applied and neither is
    inferred from the other.
    """
    channels = np.asarray(channels, dtype=float)
    if channels.ndim == 1:
        channels = channels[:, None]
    duration_s = len(channels) / fs if fs > 0 else 0.0

    if bpm is None:
        return HrvEstimate(None, None, 0, 0, "no_rate",
                           "no heart rate for this window")

    # First gate: the rate derivation's own verdict. Checked before any beat
    # work because a window it distrusts cannot produce a trustworthy RMSSD, and
    # computing one anyway would mean a number exists that must then be
    # suppressed somewhere downstream.
    if rate_confidence < MIN_RATE_CONFIDENCE:
        # `coverage=None`, not 0.0 -- no beat was counted here, which is a
        # different claim from none having been found.
        return HrvEstimate(None, None, 0, 0, "rate_confidence",
                           f"rate confidence {rate_confidence:.2f} below "
                           f"{MIN_RATE_CONFIDENCE}")

    beats = consensus_beats(channels, fs)
    expected = bpm / 60.0 * duration_s
    coverage = len(beats) / expected if expected > 0 else 0.0

    # Second gate, independent of the first: are these the beats, all of them and
    # only them? Missing beats inflate RMSSD and can be missing from a window
    # whose rate is perfectly correct; invented ones deflate it and look
    # healthier than the truth, which is why the band is bounded both ways.
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
