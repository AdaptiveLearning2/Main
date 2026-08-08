"""Pulse waveform from face-video colour, by POS (plane-orthogonal-to-skin).

This is the front half of camera heart rate. It turns a stream of mean RGB
values from a face region into a 1-D pulse waveform — the same shape the
headband's optical sensor produces directly — and hands it to
`ppg_processing.estimate_window`, which is already validated to within 1 bpm of
ECG. Nothing here derives a rate; there is exactly one rate derivation in this
codebase and this feeds it.

Why an algorithm rather than a package
--------------------------------------
Seven rPPG packages were surveyed before writing this (see the plan's Phase 4
dependency section). Every deep-learning one carries weights trained on a
physiological dataset gated behind a per-requester agreement, and the classical
ones fail on packaging or licence: `yarppg` has no POS, `rPPG-Toolbox` is under
RAIL, whose §3.2.a.ii restricts inferring and *storing* health data — which is
precisely what this product does — and `heartbeat` is GPL-3.0, which would
oblige us to open-source a platform we ship to student devices.

POS itself is a published linear method (Wang et al. 2017, "Algorithmic
Principles of Remote PPG", IEEE TBME 64(7)). Algorithms are not copyrightable
and this is implemented from the paper, so it carries no licence obligation and
adds no dependency beyond numpy.

What POS actually does, and why not just the green channel
----------------------------------------------------------
The pulse changes facial skin colour by well under 1%. Sitting on top of that,
much larger, is *specular* variation: lighting shifts, the screen's brightness
changing as the page redraws, the student leaning. That noise is broadly
achromatic — it moves all three channels together.

POS exploits the fact that the pulse is *not* achromatic: haemoglobin absorbs
green far more than red, so a real pulse moves the channels in a fixed, known
direction while illumination noise moves them along the intensity axis. Project
onto a plane orthogonal to that intensity axis and the noise largely cancels
while the pulse survives.

That projection is the whole method:

    P = [[ 0,  1, -1],
         [-2,  1,  1]]

Row 1 is green-minus-blue, row 2 is a chrominance contrast. Neither has any
component along (1,1,1), so a change that scales all channels equally lands at
zero in both.

The two rows are then combined with `alpha = std(S1) / std(S2)`, which is what
makes POS adaptive rather than a fixed matrix: the ratio is recomputed per
window, so it tracks the wearer's own skin tone and the room's lighting instead
of assuming a population average.
"""

from __future__ import annotations

import numpy as np

# Sliding window length in seconds. 1.6 s is the value from the paper, and it is
# not arbitrary: it is a little longer than the longest plausible beat interval
# (1.43 s at MIN_BPM = 42), so every window is guaranteed to contain at least one
# complete cycle for the temporal normalisation to be meaningful.
WINDOW_SECONDS = 1.6

# The projection. Fixed by the method, not a tuning knob -- both rows are
# orthogonal to (1,1,1), which is what rejects achromatic illumination change.
PROJECTION = np.array([[0.0, 1.0, -1.0],
                       [-2.0, 1.0, 1.0]])


def pos_pulse(rgb: np.ndarray, fps: float) -> np.ndarray:
    """Pulse waveform from a sequence of mean-RGB samples.

    `rgb` is (n_frames, 3) in R, G, B order — one mean colour per frame over the
    face region. Returns a 1-D waveform of the same length, suitable for
    `ppg_processing.estimate_window`.

    The output is a *relative* signal with no physical unit. Only its timing
    carries information, which is all the rate derivation uses.

    There is no streaming variant. An earlier revision had one that returned a
    single window's contribution and claimed to produce "the same signal for
    constant work per frame" -- it did not. This overlap-adds up to `window`
    estimates per frame, so the two differ in both amplitude and noise, and a
    live reading would have disagreed with any offline re-analysis of the same
    recording. Constant-work streaming is worth having, but it has to be an
    incremental form of *this* sum, not a different signal wearing its name.
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

        # Temporal normalisation, per channel, within this window only.
        #
        # This is what removes the DC skin tone and makes the projection
        # comparable across people: after it, each channel is a fractional
        # deviation from its own recent average rather than an absolute
        # brightness. Doing it per window rather than globally is deliberate --
        # a global mean would let a lighting change in one part of the session
        # bias every other part.
        mean = block.mean(axis=0)
        if np.any(mean == 0):
            continue
        normalised = block / mean

        projected = PROJECTION @ normalised.T          # (2, window)

        # Adaptive combination. alpha rescales the second row to match the
        # first's variance before summing, which is what tunes the method to
        # this wearer's skin tone and this room's light. A fixed weight here
        # would be CHROM, and would need a per-population constant.
        s1, s2 = projected[0], projected[1]
        sd2 = s2.std()
        alpha = (s1.std() / sd2) if sd2 > 0 else 0.0
        combined = s1 + alpha * s2

        # Overlap-add. Each frame is covered by up to `window` overlapping
        # estimates and they are summed, which averages away per-window noise.
        # The mean is removed first so that windows with different DC levels do
        # not add a staircase into the result.
        pulse[start:end] += combined - combined.mean()

    return pulse
