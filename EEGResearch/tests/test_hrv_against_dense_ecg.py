"""Optical RMSSD against six simultaneous ECG readings across one 8-minute recording, with a no-ECG control stretch."""

from __future__ import annotations

import gzip
from pathlib import Path

import numpy as np
import pytest
from scipy.signal import butter, filtfilt, find_peaks

from src.app.services.hrv_processing import estimate_hrv, rmssd_from_beats
from src.app.services.ppg_processing import estimate_window
from test_ppg_processing import _load

FIXTURES = Path(__file__).parent / "fixtures"
ECG_FS = 500.0
WINDOW_S = 30

# (fixture, offset into optics_ecg_dense.jsonl.gz)
PAIRS = [("ecg_dense_t52.csv.gz", 52), ("ecg_dense_t100.csv.gz", 100),
         ("ecg_dense_t150.csv.gz", 150), ("ecg_dense_t199.csv.gz", 199),
         ("ecg_dense_t250.csv.gz", 250), ("ecg_dense_t299.csv.gz", 299)]

# Control stretch: no ECG taken, deliberately no ground truth.
CONTROL_START = 335


def _load_ecg(name: str) -> np.ndarray:
    with gzip.open(FIXTURES / name, "rt", encoding="utf-8") as fh:
        return np.array([float(line) for line in fh
                         if line.strip() and not line.startswith("#")])


def _ecg_beats(x: np.ndarray) -> list[float]:
    """Finds R-peak times, using a different algorithm than the optical beat detector."""
    b, a = butter(3, [5 / (ECG_FS / 2), 40 / (ECG_FS / 2)], btype="band")
    y = np.abs(filtfilt(b, a, x) / np.std(filtfilt(b, a, x)))
    peaks, _ = find_peaks(y, distance=int(0.3 * ECG_FS), prominence=2.0)
    out = []
    for i in peaks:
        if 0 < i < len(y) - 1:
            a0, b0, c0 = y[i - 1], y[i], y[i + 1]
            denom = a0 - 2 * b0 + c0
            out.append(i + (0.5 * (a0 - c0) / denom if denom else 0.0))
    return [t / ECG_FS for t in out]


def _optics(offset_s: int):
    data, fs = _load("optics_ecg_dense.jsonl.gz")
    window = data[int(offset_s * fs):int((offset_s + WINDOW_S) * fs)]
    rate = estimate_window(window, fs)
    return rate, estimate_hrv(window, fs, rate.bpm, rate.confidence)


@pytest.mark.parametrize("name,offset", PAIRS)
def test_heart_rate_matches_the_ecg(name, offset):
    ecg_bpm = 60.0 / np.median(np.diff(_ecg_beats(_load_ecg(name))))
    rate, _ = _optics(offset)
    assert rate.bpm == pytest.approx(ecg_bpm, abs=2.0)


def test_the_true_rmssd_moves_a_lot_over_minutes():
    """A derived RMSSD that varies over minutes is not necessarily estimator error."""
    values = [rmssd_from_beats(_ecg_beats(_load_ecg(n)))[0] for n, _ in PAIRS]
    assert max(values) - min(values) > 15.0, (
        f"expected substantial real variation, got {values}"
    )


def test_rmssd_tracks_the_reference_within_fifteen_percent():
    """Every reported window; look for a real defect before loosening the tolerance."""
    errors = []
    for name, offset in PAIRS:
        ecg_rmssd, _ = rmssd_from_beats(_ecg_beats(_load_ecg(name)))
        _, hrv = _optics(offset)
        if hrv.rmssd_ms is None:
            continue          # gated windows are covered below
        errors.append(abs(hrv.rmssd_ms - ecg_rmssd) / ecg_rmssd)

    assert len(errors) >= 5, f"expected most windows to report, got {len(errors)}"
    assert max(errors) < 0.20, (
        f"errors {[f'{e:.0%}' for e in errors]}"
    )


def test_the_estimate_follows_the_reference_rather_than_the_mean():
    """A tolerance check alone would pass a constant 39 ms."""
    ecg_vals, optic_vals = [], []
    for name, offset in PAIRS:
        _, hrv = _optics(offset)
        if hrv.rmssd_ms is None:
            continue
        ecg_vals.append(rmssd_from_beats(_ecg_beats(_load_ecg(name)))[0])
        optic_vals.append(hrv.rmssd_ms)
    r = float(np.corrcoef(ecg_vals, optic_vals)[0, 1])
    assert r > 0.5, f"correlation {r:.2f} -- not tracking the reference"


def test_the_bias_is_small_relative_to_the_error():
    """The residual is scatter, not a scale factor, so there is no calibration constant."""
    diffs = []
    for name, offset in PAIRS:
        _, hrv = _optics(offset)
        if hrv.rmssd_ms is None:
            continue
        diffs.append(hrv.rmssd_ms - rmssd_from_beats(_ecg_beats(_load_ecg(name)))[0])
    bias, rms = float(np.mean(diffs)), float(np.sqrt(np.mean(np.square(diffs))))
    assert abs(bias) < 0.5 * rms, f"bias {bias:.1f}ms vs rms {rms:.1f}ms"


def test_the_control_stretch_reports_without_ground_truth():
    """Pins behaviour only; there is no reference to assert accuracy against."""
    data, fs = _load("optics_ecg_dense.jsonl.gz")
    values = []
    for start in range(CONTROL_START, int(len(data) / fs) - WINDOW_S, 10):
        window = data[int(start * fs):int((start + WINDOW_S) * fs)]
        rate = estimate_window(window, fs)
        out = estimate_hrv(window, fs, rate.bpm, rate.confidence)
        if out.rmssd_ms is not None:
            values.append(out.rmssd_ms)
    assert len(values) >= 5
    assert all(10.0 < v < 60.0 for v in values), values
