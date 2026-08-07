"""The only ground truth in this repo.

Everything else about heart rate here is checked against another derivation, a
watch's own summary figure, or physiological plausibility. These three fixtures
are a simultaneous single-lead ECG at 500 Hz, recorded over the *same seconds* as
`optics_ecg_paired.jsonl.gz`, so for the first time a derived value can be
compared against a measurement of the same beats rather than against an
expectation.

Alignment was by wall clock: each ECG's `Created time` landed a consistent 35 s
after the mark taken when the reading was started, across all three, which is
what makes the offset trustworthy rather than assumed.

Personal identifiers were removed from the CSV headers before committing. The
watch reports a name and date of birth in every export and neither belongs here.

What this establishes
---------------------
Heart rate is right: within 1 bpm on all three windows.

RMSSD is close on two of three windows -- 33.7 vs 31.5 and 33.2 vs 31.3, both
within 7% -- and 50% high on the third (49.9 vs 33.3). No in-window property
separates the bad one: coverage 0.97 against 1.01, rate confidence 1.00 in both.

The most useful numbers here are the reference's own two spreads, because an
error is unreadable without them:

  - **2.0 ms** across the three paired readings, spanning 95 seconds. The true
    value is near-constant on that scale, so the 29-63 ms spread the derivation
    produces over the same recording is entirely its own error. Before this
    there was no way to separate the two, and a wide spread could always be
    excused as real variability.
  - **9.7 ms** across six readings spanning 19 minutes, while heart rate drifts
    70 to 75. So "RMSSD is stable" holds only at the scale of a window. A
    tolerance taken from the first number must not be applied to values minutes
    apart.

Read against those, the two good windows are off by 2.2 and 1.9 ms -- *inside
the reference's own short-term noise*, so there is nothing further to measure
without a better reference. The third is off by 16.6 ms, more than the entire
19-minute physiological range. The failure is bimodal: right, or badly wrong.
It is not a bias to calibrate out.
"""

from __future__ import annotations

import gzip
from pathlib import Path

import numpy as np
import pytest
from scipy.signal import butter, filtfilt, find_peaks

from src.app.services.hrv_processing import (
    IBI_DEVIATION_FRACTION,
    estimate_hrv,
    rmssd_from_beats,
)
from src.app.services.ppg_processing import estimate_window
from test_ppg_processing import _load

FIXTURES = Path(__file__).parent / "fixtures"
ECG_FS = 500.0

# (ECG fixture, offset into optics_ecg_paired.jsonl.gz)
PAIRS = [("ecg_ref_t40.csv.gz", 40), ("ecg_ref_t93.csv.gz", 93),
         ("ecg_ref_t136.csv.gz", 136)]

# Recorded outside the optics capture, so they pair with nothing. They exist to
# answer a question the paired three cannot: how much a real RMSSD moves. Six
# readings over 19 minutes give 24.2-33.9ms; the three paired ones, spanning
# 95 seconds, give 31.3-33.3ms. Both numbers are needed to read an error --
# the short-term figure says what the estimator must beat, the long-term one
# says what a session-level average may legitimately drift by.
UNPAIRED = ["ecg_unpaired_before.csv.gz", "ecg_unpaired_after1.csv.gz",
            "ecg_unpaired_after2.csv.gz"]
WINDOW_S = 30


def _load_ecg(name: str) -> np.ndarray:
    with gzip.open(FIXTURES / name, "rt", encoding="utf-8") as fh:
        return np.array([float(line) for line in fh
                         if line.strip() and not line.startswith("#")])


def _ecg_beats(x: np.ndarray) -> list[float]:
    """R-peak times. Deliberately a different algorithm from the optical beat
    detector -- a shared implementation would let a bug agree with itself.

    Validated against the watch's own reported average heart rate, which is
    computed by the device from the same trace: 71.8 vs 71 on the first
    recording taken."""
    b, a = butter(3, [5 / (ECG_FS / 2), 40 / (ECG_FS / 2)], btype="band")
    y = filtfilt(b, a, x)
    y = np.abs(y / np.std(y))
    peaks, _ = find_peaks(y, distance=int(0.3 * ECG_FS), prominence=2.0)
    refined = []
    for i in peaks:
        if 0 < i < len(y) - 1:
            a0, b0, c0 = y[i - 1], y[i], y[i + 1]
            denom = a0 - 2 * b0 + c0
            refined.append(i + (0.5 * (a0 - c0) / denom if denom else 0.0))
    return [t / ECG_FS for t in refined]


def _optics(offset_s: int):
    data, fs = _load("optics_ecg_paired.jsonl.gz")
    window = data[int(offset_s * fs):int((offset_s + WINDOW_S) * fs)]
    rate = estimate_window(window, fs)
    return rate, estimate_hrv(window, fs, rate.bpm, rate.confidence)


@pytest.mark.parametrize("ecg_name,offset", PAIRS)
def test_heart_rate_matches_the_ecg(ecg_name, offset):
    """The strong result, and the one a product could rely on today."""
    beats = _ecg_beats(_load_ecg(ecg_name))
    ecg_bpm = 60.0 / np.median(np.diff(beats))
    rate, _ = _optics(offset)
    assert rate.bpm == pytest.approx(ecg_bpm, abs=2.0)


def test_the_true_rmssd_barely_moves_over_the_paired_window():
    """What makes the spread below attributable to the estimator.

    Three readings over 95 seconds agree to 2ms. Any wider spread in a derived
    value over the same recording is error, not physiology -- which is exactly
    the excuse that was available before this fixture existed."""
    values = [rmssd_from_beats(_ecg_beats(_load_ecg(name)))[0]
              for name, _ in PAIRS]
    assert max(values) - min(values) < 5.0, f"ECG RMSSD varied: {values}"


def test_the_true_rmssd_does_move_over_twenty_minutes():
    """The other half, and the reason the test above is scoped to 95 seconds.

    Six readings over 19 minutes span 24.2-33.9ms while heart rate drifts 70 to
    75. So "RMSSD is stable" is only true on the scale the paired windows cover;
    a tolerance derived from it must not be applied to values minutes apart."""
    values = [rmssd_from_beats(_ecg_beats(_load_ecg(name)))[0]
              for name, _ in PAIRS]
    values += [rmssd_from_beats(_ecg_beats(_load_ecg(name)))[0]
               for name in UNPAIRED]
    assert len(values) == 6
    spread = max(values) - min(values)
    assert 5.0 < spread < 20.0, (
        f"expected real drift over 19 minutes, got {spread:.1f}ms"
    )


def test_the_two_good_windows_are_within_the_references_own_noise():
    """The result that says the estimator is at the reference's floor.

    Absolute errors on the two good windows are 2.2 and 1.9ms, against a
    reference whose own three readings span 2.0ms over the same period. There
    is nothing left to measure there -- a tighter claim would need a better
    reference, not a better estimator.

    The third window is off by 16.6ms, which is larger than the entire 19-minute
    physiological range. The failure is bimodal, not a bias to calibrate out."""
    errors = []
    for name, offset in PAIRS:
        ecg_rmssd, _ = rmssd_from_beats(_ecg_beats(_load_ecg(name)))
        _, hrv = _optics(offset)
        errors.append(abs(hrv.rmssd_ms - ecg_rmssd))
    errors.sort()
    assert errors[0] < 3.0 and errors[1] < 3.0, f"absolute errors {errors}"
    assert errors[2] > 10.0, (
        f"the outlier is expected to be far away, not marginal: {errors}"
    )


def test_rmssd_is_close_on_most_windows_and_wrong_on_one():
    """Recorded exactly as measured, including the failure.

    Two windows land within 7% of a simultaneous ECG. The third is 50% high,
    and nothing available in the window distinguishes it -- coverage 0.97
    against 1.01, rate confidence 1.00 in both. A rolling median across windows
    does not rescue it either: consecutive windows overlap, so one contaminating
    beat survives into all of them.

    Asserted as "most windows, not all" rather than a blanket tolerance, because
    a blanket tolerance would either fail on real data or be so loose it asserts
    nothing. If this ever passes for all three, tighten it."""
    errors = []
    for name, offset in PAIRS:
        ecg_rmssd, _ = rmssd_from_beats(_ecg_beats(_load_ecg(name)))
        _, hrv = _optics(offset)
        assert hrv.rmssd_ms is not None, f"no RMSSD reported at t={offset}"
        errors.append(abs(hrv.rmssd_ms - ecg_rmssd) / ecg_rmssd)

    within_10pct = sum(1 for e in errors if e < 0.10)
    assert within_10pct >= 2, (
        f"expected at least two windows within 10%, got errors "
        f"{[f'{e:.0%}' for e in errors]}"
    )
    assert max(errors) < 0.75, (
        f"the known-bad window got worse: {[f'{e:.0%}' for e in errors]}"
    )


def test_the_relative_filter_is_what_makes_this_close():
    """Without it the same windows are unusable, against the same reference."""
    data, fs = _load("optics_ecg_paired.jsonl.gz")
    from src.app.services.hrv_processing import consensus_beats

    unfiltered = []
    for _, offset in PAIRS:
        window = data[int(offset * fs):int((offset + WINDOW_S) * fs)]
        ibi = np.diff(consensus_beats(window, fs)) * 1000.0
        diffs = np.diff(ibi)
        unfiltered.append(float(np.sqrt(np.mean(diffs ** 2))))

    ecg = [rmssd_from_beats(_ecg_beats(_load_ecg(name)))[0] for name, _ in PAIRS]
    assert max(unfiltered) > 2 * max(ecg), (
        "the unfiltered case should be far from the reference; if it is not, "
        f"IBI_DEVIATION_FRACTION ({IBI_DEVIATION_FRACTION}) may no longer matter"
    )
