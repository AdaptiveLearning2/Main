"""Pulse waveform from face-video colour, by POS (Wang et al. 2017, IEEE TBME 64(7)).

Dormant: `FACE_HEART_ENABLED` defaults to false and should stay there -- against
ECG the camera signal was absent, not mis-derived. Feeds the one rate derivation,
`ppg_processing.estimate_window`. Implemented from the paper (algorithms aren't
copyrightable): no licence obligation, numpy only.
"""

from __future__ import annotations

import numpy as np

# Seconds, from the paper; covers one full beat at MIN_BPM = 42.
WINDOW_SECONDS = 1.6

# Fixed by the method: both rows are orthogonal to (1,1,1), rejecting achromatic illumination change.
PROJECTION = np.array([[0.0, 1.0, -1.0],
                       [-2.0, 1.0, 1.0]])


def resample_uniform(
    timestamps: np.ndarray, rgb: np.ndarray, target_fps: float
) -> tuple[np.ndarray, float]:
    """Put an unevenly-sampled colour series onto a uniform time grid.

    Required: webcam frame intervals are bimodal, and POS and autocorrelation
    assume even sampling. Returns the resampled series and the grid rate used.
    """
    timestamps = np.asarray(timestamps, dtype=float)
    rgb = np.asarray(rgb, dtype=float)
    if len(timestamps) != len(rgb):
        raise ValueError("timestamps and rgb must be the same length")
    if len(timestamps) < 2:
        return rgb, target_fps

    span = timestamps[-1] - timestamps[0]
    if span <= 0:
        return rgb, target_fps

    grid = np.arange(timestamps[0], timestamps[-1], 1.0 / target_fps)
    if len(grid) < 2:
        return rgb, target_fps

    out = np.column_stack([
        np.interp(grid, timestamps, rgb[:, c]) for c in range(rgb.shape[1])
    ])
    return out, target_fps


def largest_gap(timestamps: np.ndarray) -> float:
    """The biggest interval in a series, in seconds; callers gate on it, resampling does not."""
    timestamps = np.asarray(timestamps, dtype=float)
    return float(np.diff(timestamps).max()) if len(timestamps) > 1 else 0.0


def pos_pulse(rgb: np.ndarray, fps: float) -> np.ndarray:
    """Pulse waveform from (n_frames, 3) mean-RGB samples, same length, unitless.

    No streaming variant: any future one must be an incremental form of this
    overlap-add, or live readings would disagree with offline re-analysis.
    """
    rgb = np.asarray(rgb, dtype=float)
    if rgb.ndim != 2 or rgb.shape[1] != 3:
        raise ValueError(f"expected (n_frames, 3) RGB, got {rgb.shape}")

    n = len(rgb)
    window = int(WINDOW_SECONDS * fps)
    if n < window or window < 2:
        return np.zeros(n)

    pulse = np.zeros(n)
    for end in range(window, n + 1):
        start = end - window
        block = rgb[start:end]

        # Per-window normalisation, so a lighting change in one part cannot bias the rest.
        mean = block.mean(axis=0)
        if np.any(mean == 0):
            continue
        normalised = block / mean

        projected = PROJECTION @ normalised.T          # (2, window)

        # Adaptive alpha tunes to this wearer and room; a fixed weight would be CHROM.
        s1, s2 = projected[0], projected[1]
        sd2 = s2.std()
        alpha = (s1.std() / sd2) if sd2 > 0 else 0.0
        combined = s1 + alpha * s2

        # Overlap-add, mean removed so differing DC levels do not staircase.
        pulse[start:end] += combined - combined.mean()

    return pulse
