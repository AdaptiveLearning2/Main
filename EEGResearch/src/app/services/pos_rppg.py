"""Pulse waveform from face-video colour, by POS (plane-orthogonal-to-skin).

Dormant: a correct implementation of a published method, but not wired into
anything that ships. `FACE_HEART_ENABLED` defaults to false and should stay
there. Against a simultaneous ECG the camera path reported 47.7 bpm at
confidence 0.74 against a true 88 -- an absent signal, not a derivation error
(confirmed in the raw R/G/B channels before POS touches them). See
`tests/fixtures/FACE_RPPG_ECG.md`.

Kept rather than deleted: the failure is the sensor, not the algorithm, and on
a camera with locked exposure this is still the right approach. The tests below
only prove POS computes correctly on synthetic input -- not that it measures a
real heart rate.

This is the front half of camera heart rate: it turns a stream of mean RGB
values from a face region into a 1-D pulse waveform (the same shape the
headband's optical sensor produces) and hands it to
`ppg_processing.estimate_window`, which is validated to within 1 bpm of ECG.
There is exactly one rate derivation in this codebase, and this feeds it.

Why an algorithm rather than a package
--------------------------------------
Every deep-learning rPPG package carries weights gated behind a per-requester
data agreement. Of the classical ones, `yarppg` has no POS, `rPPG-Toolbox` is
under a RAIL licence that restricts inferring and storing health data (which is
what this product does), and `heartbeat` is GPL-3.0, which would force this
platform open-source. POS itself is a published linear method (Wang et al.
2017, IEEE TBME 64(7)) implemented from the paper -- algorithms aren't
copyrightable, so this carries no licence obligation and adds no dependency
beyond numpy.

What POS does, and why not just the green channel
---------------------------------------------------
The pulse changes facial skin colour by well under 1%. On top of that, much
larger, sits specular variation -- lighting shifts, screen brightness, the
student leaning -- which is broadly achromatic (moves all three channels
together). Haemoglobin absorbs green far more than red, so a real pulse moves
channels in a fixed known direction while illumination noise moves along the
intensity axis. Projecting onto a plane orthogonal to that axis cancels most of
the noise while the pulse survives:

    P = [[ 0,  1, -1],
         [-2,  1,  1]]

Row 1 is green-minus-blue, row 2 is a chrominance contrast; neither has any
component along (1,1,1), so a change scaling all channels equally lands at
zero in both.

The two rows are combined with `alpha = std(S1) / std(S2)`, recomputed per
window, which is what makes POS adaptive: it tracks the wearer's skin tone and
the room's lighting instead of assuming a population average.
"""

from __future__ import annotations

import numpy as np

# Sliding window length in seconds, from the paper. Slightly longer than the
# longest plausible beat interval (1.43 s at MIN_BPM = 42), so every window
# contains at least one full cycle for the temporal normalisation to work.
WINDOW_SECONDS = 1.6

# The projection. Fixed by the method, not a tuning knob -- both rows are
# orthogonal to (1,1,1), which is what rejects achromatic illumination change.
PROJECTION = np.array([[0.0, 1.0, -1.0],
                       [-2.0, 1.0, 1.0]])


def resample_uniform(
    timestamps: np.ndarray, rgb: np.ndarray, target_fps: float
) -> tuple[np.ndarray, float]:
    """Put an unevenly-sampled colour series onto a uniform time grid.

    Not optional: a real webcam asked for 30 fps delivered bimodal intervals
    (78% at 31 ms, 21% at 47 ms, occasional stalls past 100 ms) -- a mixture of
    two spacings, not jitter around one mean. Treating either the mean rate
    (28 Hz) or the mode (32 Hz) as uniform distorts intervals by up to 50%.

    Both POS and the downstream autocorrelation assume even sampling, and no
    quality gate can repair uneven input -- it can only refuse it. Interpolating
    makes the even-sampling assumption true instead of merely asserted.

    Linear interpolation is enough: the pulse is heavily oversampled at 30 Hz
    against a 1-2 Hz signal, so straight-line error is far below the noise
    floor. Returns the resampled series and the grid rate used.
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
    """The biggest interval in a series, in seconds.

    Interpolation across a missed frame or two is fine; across a long stall it's
    invention, not measurement, and lands in the pulse band as a slow ramp.
    Callers gate on this value; resampling itself doesn't, since placing samples
    and judging whether there are enough of them are separate questions.
    """
    timestamps = np.asarray(timestamps, dtype=float)
    return float(np.diff(timestamps).max()) if len(timestamps) > 1 else 0.0


def pos_pulse(rgb: np.ndarray, fps: float) -> np.ndarray:
    """Pulse waveform from a sequence of mean-RGB samples.

    `rgb` is (n_frames, 3) in R, G, B order -- one mean colour per frame over the
    face region. Returns a 1-D waveform of the same length, suitable for
    `ppg_processing.estimate_window`.

    The output is a relative signal with no physical unit -- only its timing
    carries information, which is all the rate derivation uses.

    There is no streaming variant. A per-frame streaming version would differ in
    amplitude and noise from this overlap-add (which sums up to `window`
    estimates per frame), so a live reading would disagree with an offline
    re-analysis of the same recording. Any future streaming form must be an
    incremental version of this same sum, not a different signal.
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

        # Temporal normalisation, per channel, within this window only. Removes
        # DC skin tone so the projection compares across people: each channel
        # becomes a fractional deviation from its own recent average. Done per
        # window, not globally, so a lighting change in one part of the session
        # can't bias every other part.
        mean = block.mean(axis=0)
        if np.any(mean == 0):
            continue
        normalised = block / mean

        projected = PROJECTION @ normalised.T          # (2, window)

        # Adaptive combination: alpha rescales the second row to match the first's
        # variance before summing, tuning to this wearer's skin tone and this
        # room's light. A fixed weight here would be CHROM, needing a population
        # constant instead.
        s1, s2 = projected[0], projected[1]
        sd2 = s2.std()
        alpha = (s1.std() / sd2) if sd2 > 0 else 0.0
        combined = s1 + alpha * s2

        # Overlap-add: each frame is covered by up to `window` overlapping
        # estimates, summed to average away per-window noise. The mean is
        # removed first so windows with different DC levels don't add a
        # staircase into the result.
        pulse[start:end] += combined - combined.mean()

    return pulse
