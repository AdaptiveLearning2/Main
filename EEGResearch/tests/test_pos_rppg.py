"""Tests for POS pulse extraction.

No camera and no fixture yet, so these use synthetic RGB sequences with a
known pulse and known nuisances. That's enough to check the arithmetic and to
prove what POS exists for: rejecting illumination change that the green
channel alone can't.

Physiological accuracy is not established here and can't be by synthetic
data -- that needs a real camera-plus-ECG capture, and until that happens
nothing in this module should feed a recorded value.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.app.services.ppg_processing import estimate_window
from src.app.services.pos_rppg import WINDOW_SECONDS, pos_pulse

FPS = 30.0

# Relative pulse amplitude per channel. Haemoglobin absorbs green most, red
# least -- the asymmetry POS relies on. An achromatic (1,1,1) signature would
# be indistinguishable from a lighting change and unrecoverable in principle.
PULSE_RGB = np.array([0.10, 0.60, 0.30])


def _face_rgb(bpm: float, seconds: float = 20.0, fps: float = FPS,
              pulse_amplitude: float = 0.005, illumination: float = 0.0,
              illumination_hz: float = 1.4, noise: float = 0.0,
              seed: int = 0) -> np.ndarray:
    """Mean-RGB over a face region, as a camera would report it.

    `pulse_amplitude` defaults to 0.005 (0.5% modulation), the real order of
    magnitude -- a test written with a 10% pulse would pass on almost any
    method.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * fps)) / fps
    skin = np.array([180.0, 120.0, 110.0])          # a plausible mean skin RGB

    pulse = np.sin(2 * np.pi * bpm / 60.0 * t)
    frames = skin * (1.0 + pulse_amplitude * np.outer(pulse, PULSE_RGB))

    if illumination:
        # Achromatic: scales all three channels together, like a screen
        # redraw, a flickering light, or the student rocking slightly.
        #
        # Default 1.4 Hz is deliberately inside the 0.6-5 Hz pulse band. A
        # slow drift would be the easy case, since `ppg_processing`
        # bandpasses it away before the rate derivation sees it, and
        # green-only would pass too. In-band interference is the case that
        # needs the projection.
        drift = 1.0 + illumination * np.sin(2 * np.pi * illumination_hz * t)
        frames = frames * drift[:, None]

    if noise:
        frames = frames + noise * rng.standard_normal(frames.shape)

    return frames


def _bpm(pulse: np.ndarray, fps: float = FPS) -> float | None:
    """Rate via the codebase's one derivation, not a second one."""
    return estimate_window(pulse, fps).bpm


# ── the arithmetic ───────────────────────────────────────────────────────────

def test_rejects_input_that_is_not_rgb():
    with pytest.raises(ValueError):
        pos_pulse(np.zeros((100, 4)), FPS)


def test_too_short_an_input_yields_silence_not_an_error():
    """A session's first second has fewer frames than one window. That's
    ordinary, not exceptional, so it should return zeros, not raise."""
    short = _face_rgb(70.0, seconds=WINDOW_SECONDS / 2)
    out = pos_pulse(short, FPS)
    assert out.shape == (len(short),)
    assert not out.any()


def test_output_is_zero_mean():
    """Overlap-add subtracts each window's mean, so no DC survives into the
    result. A DC offset wouldn't break the autocorrelation downstream, but
    it would make the waveform useless for anything that plots it."""
    out = pos_pulse(_face_rgb(72.0), FPS)
    assert abs(out.mean()) < 0.01 * out.std()


def test_a_constant_image_produces_no_pulse():
    flat = np.tile([180.0, 120.0, 110.0], (600, 1))
    assert not np.any(np.abs(pos_pulse(flat, FPS)) > 1e-9)


# ── the property POS exists for ──────────────────────────────────────────────

@pytest.mark.parametrize("bpm", [55.0, 72.0, 95.0])
def test_recovers_a_known_rate_from_a_half_percent_pulse(bpm):
    rgb = _face_rgb(bpm, pulse_amplitude=0.005)
    assert _bpm(pos_pulse(rgb, FPS)) == pytest.approx(bpm, abs=3.0)


def test_survives_in_band_illumination_larger_than_the_pulse():
    """A 5% achromatic flicker at 84/min against a 0.5% pulse at 72 -- the
    interference is ten times the signal and sits inside the pulse band, so
    no filter can remove it. POS can, because the flicker has no component
    in the plane it projects onto."""
    rgb = _face_rgb(72.0, pulse_amplitude=0.005, illumination=0.05,
                    illumination_hz=1.4)
    assert _bpm(pos_pulse(rgb, FPS)) == pytest.approx(72.0, abs=3.0)


def test_a_slow_drift_is_the_easy_case_and_does_not_test_the_projection():
    """An earlier version of this suite used a 0.11 Hz drift and proved
    nothing: `ppg_processing` bandpasses 0.6-5 Hz, so slow achromatic drift
    never reaches the rate derivation. Green-only passed it too.

    Kept as an explicit statement of what this does *not* demonstrate, so
    the in-band test above isn't mistaken for a weaker claim."""
    rgb = _face_rgb(72.0, pulse_amplitude=0.005, illumination=0.05,
                    illumination_hz=0.11)
    green_only = rgb[:, 1] - rgb[:, 1].mean()
    assert _bpm(green_only) == pytest.approx(72.0, abs=3.0)


def test_the_green_channel_alone_fails_where_pos_succeeds():
    """Confirms the previous test measures POS and not just the ease of the
    synthetic signal. Green-only is the naive alternative worth ruling out:
    with interference inside the pulse band it has no way to tell a
    heartbeat from a flicker, and locks onto the stronger one."""
    rgb = _face_rgb(72.0, pulse_amplitude=0.005, illumination=0.05,
                    illumination_hz=1.4)

    green_only = rgb[:, 1] - rgb[:, 1].mean()
    green_bpm = _bpm(green_only)
    pos_bpm = _bpm(pos_pulse(rgb, FPS))

    assert pos_bpm == pytest.approx(72.0, abs=3.0)
    assert green_bpm is None or abs(green_bpm - 72.0) > 5.0, (
        f"green-only got {green_bpm}, so this scenario does not discriminate"
    )


def test_survives_sensor_noise():
    rgb = _face_rgb(68.0, pulse_amplitude=0.005, noise=0.3, seed=3)
    assert _bpm(pos_pulse(rgb, FPS)) == pytest.approx(68.0, abs=4.0)
