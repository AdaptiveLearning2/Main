"""RMSSD from the headband's optical channels, when it can be had at all.

RMSSD needs every beat correct, so beats are kept only on cross-channel consensus
and their times averaged across the channels that saw them. An enrichment to
`stress_score`, never its definition. Gated on the rate derivation's own
confidence; validated for seated use, not gait. See docs/signals.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil

import numpy as np
from scipy.signal import find_peaks

from .ppg_processing import BANDPASS_ORDER, MAX_BPM, MIN_BPM, _parabolic_peak, bandpass

# Physiological beat intervals (ms), mirroring MIN_BPM/MAX_BPM; outside is an artefact.
MIN_IBI_MS = 60_000.0 / MAX_BPM
MAX_IBI_MS = 60_000.0 / MIN_BPM

# Max fractional deviation from the window's median interval; the filter that matters.
IBI_DEVIATION_FRACTION = 0.20

# Seconds within which two channels' marks count as the same beat.
BEAT_MATCH_TOLERANCE_S = 0.06

# Peak prominence in standard deviations of the filtered signal.
PEAK_PROMINENCE_SD = 0.3

# Fraction of populated channels that must see a beat (3-of-4, scales to 8/16-channel presets).
CONSENSUS_FRACTION = 0.75

# ...but never unanimity: one badly seated emitter may miss a beat.
MAX_CHANNELS_SPARED = 1

# The real gate: with one channel, consensus and averaging do nothing and confidence reads 1.00.
MIN_POPULATED_CHANNELS = 2

# Min detected beats / rate-implied beats; the rate can be right while beats are missed.
MIN_BEAT_COVERAGE = 0.95

# Max coverage: a double-detected notch or an octave-low rate; one boundary beat is ~1.03.
MAX_BEAT_COVERAGE = 1.15

# Rate confidence below which RMSSD isn't attempted (above the rate's own 0.55).
MIN_RATE_CONFIDENCE = 0.9

# Min successive differences, so no single one dominates the mean of squares.
MIN_INTERVALS = 10


@dataclass
class HrvEstimate:
    """RMSSD for one window, or the reason there isn't one."""
    rmssd_ms: float | None
    # Consensus beats / rate-implied beats. None when no beat was ever counted (not 0.0).
    coverage: float | None
    beat_count: int
    interval_count: int
    # "rate_confidence" | "coverage" | "excess_beats" | "too_few_intervals" | "no_rate".
    rejected_by: str | None = None
    reason: str = ""
    beat_times_s: list[float] = field(default_factory=list)


def detect_beats(x: np.ndarray, fs: float) -> np.ndarray:
    """Beat times in seconds for one channel, refined below the sample grid.

    Parabolic refinement: the 64 Hz grid (15.6 ms) is on the scale of RMSSD itself.
    """
    x = np.asarray(x, dtype=float)
    # filtfilt's padlen must be strictly exceeded; order shared with ppg_processing.
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
    """
    spared = populated - MAX_CHANNELS_SPARED
    return max(MIN_POPULATED_CHANNELS,
               min(spared, ceil(CONSENSUS_FRACTION * populated)))


def consensus_beats(channels: np.ndarray, fs: float) -> list[float]:
    """Beat times agreed by most channels, each averaged over the channels that saw it.

    Agreement removes invented beats; averaging reduces timing jitter.
    """
    channels = np.asarray(channels, dtype=float)
    if channels.ndim == 1:
        channels = channels[:, None]

    per_channel = [detect_beats(channels[:, c], fs) for c in range(channels.shape[1])]
    populated = [b for b in per_channel if b.size]
    if len(populated) < MIN_POPULATED_CHANNELS:
        # Refused, not one channel's unfiltered detections.
        return []

    # Roll call from the busiest channel, or a sparse one would cap the result.
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
    """RMSSD in ms, and the number of successive differences it was computed over.

    Out-of-range intervals are dropped, not clamped (a clamped one is fabricated).
    """
    if len(beat_times_s) < 3:
        return None, 0
    ibi = np.diff(np.asarray(beat_times_s)) * 1000.0

    # Absolute range plus the relative (median) filter; the absolute one alone admits
    # merged beats. A longer window is no substitute.
    median_ibi = float(np.median(ibi))
    valid = (
        (ibi >= MIN_IBI_MS)
        & (ibi <= MAX_IBI_MS)
        & (np.abs(ibi - median_ibi) <= IBI_DEVIATION_FRACTION * median_ibi)
    )

    # Differences only WITHIN runs of adjacent valid intervals: filtering then diffing
    # would pair intervals that were never consecutive.
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
    # Count differences, not surviving intervals: a stranded interval contributes nothing.
    return float(np.sqrt(np.mean(all_diffs ** 2))), int(all_diffs.size)


def estimate_hrv(
    channels: np.ndarray,
    fs: float,
    bpm: float | None,
    rate_confidence: float,
) -> HrvEstimate:
    """RMSSD for one window, gated on the rate derivation having succeeded.

    `bpm` / `rate_confidence` come from `estimate_window` on the same samples, not
    re-derived. Rate confidence and beat coverage are independent gates; both apply.
    """
    channels = np.asarray(channels, dtype=float)
    if channels.ndim == 1:
        channels = channels[:, None]
    duration_s = len(channels) / fs if fs > 0 else 0.0

    if bpm is None:
        return HrvEstimate(None, None, 0, 0, "no_rate",
                           "no heart rate for this window")

    if rate_confidence < MIN_RATE_CONFIDENCE:
        # coverage=None: no beat was counted, which differs from none found.
        return HrvEstimate(None, None, 0, 0, "rate_confidence",
                           f"rate confidence {rate_confidence:.2f} below "
                           f"{MIN_RATE_CONFIDENCE}")

    beats = consensus_beats(channels, fs)
    expected = bpm / 60.0 * duration_s
    coverage = len(beats) / expected if expected > 0 else 0.0

    # Bounded both ways: missing beats inflate RMSSD, invented ones deflate it.
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
