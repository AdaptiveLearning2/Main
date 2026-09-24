"""Heart rate and RMSSD against a simultaneous 500 Hz single-lead ECG over `optics_ecg_paired.jsonl.gz`."""

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

# Recorded outside the optics capture: used only to see how much real RMSSD drifts over 19 minutes.
UNPAIRED = ["ecg_unpaired_before.csv.gz", "ecg_unpaired_after1.csv.gz",
            "ecg_unpaired_after2.csv.gz"]
WINDOW_S = 30


def _load_ecg(name: str) -> np.ndarray:
    with gzip.open(FIXTURES / name, "rt", encoding="utf-8") as fh:
        return np.array([float(line) for line in fh
                         if line.strip() and not line.startswith("#")])


def _ecg_beats(x: np.ndarray) -> list[float]:
    """Finds R-peak times with a different algorithm than the optical detector, so a shared bug cannot agree with itself."""
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
    beats = _ecg_beats(_load_ecg(ecg_name))
    ecg_bpm = 60.0 / np.median(np.diff(beats))
    rate, _ = _optics(offset)
    assert rate.bpm == pytest.approx(ecg_bpm, abs=2.0)


def test_the_true_rmssd_barely_moves_over_the_paired_window():
    """Over 95 s, wider spread in a derived value is error, not physiology."""
    values = [rmssd_from_beats(_ecg_beats(_load_ecg(name)))[0]
              for name, _ in PAIRS]
    assert max(values) - min(values) < 5.0, f"ECG RMSSD varied: {values}"


def test_the_true_rmssd_does_move_over_twenty_minutes():
    """A tolerance from the 95 s scale cannot apply to values minutes apart."""
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
    """The outlier is bimodal, not a bias to calibrate out."""
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
    """Nothing in-window distinguishes the bad one; if all three ever pass, tighten this."""
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
