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

The most useful number here is the ECG spread: **2.0 ms across three readings
spanning three minutes**. The wearer's true RMSSD was essentially constant, so
the 29-63 ms spread the derivation produces over the same recording is entirely
its own error. Before this fixture there was no way to separate the two, and a
wide spread could always be excused as real variability.
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


def test_the_ecg_shows_the_true_rmssd_barely_moves():
    """What makes the spread below attributable to the estimator.

    Three readings over three minutes agree to 2ms. Any wider spread in a
    derived value over the same recording is error, not physiology -- which is
    exactly the excuse that was available before this fixture existed."""
    values = [rmssd_from_beats(_ecg_beats(_load_ecg(name)))[0]
              for name, _ in PAIRS]
    assert max(values) - min(values) < 5.0, f"ECG RMSSD varied: {values}"


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
