"""POS pulse extraction.

No camera and no fixture yet — these are synthetic RGB sequences with a known
pulse and known nuisances. That is enough to pin the arithmetic and, more
usefully, to prove the property POS exists for: rejecting illumination change
that the green channel alone cannot.

Physiological accuracy is *not* established here and cannot be by synthetic
data. That needs the camera-plus-ECG capture described in the plan's
verification step, and until it happens nothing in this module should feed a
recorded value.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.app.services.ppg_processing import estimate_window
from src.app.services.pos_rppg import WINDOW_SECONDS, pos_pulse, pos_pulse_last_window

FPS = 30.0

# Relative pulse amplitude per channel. Haemoglobin absorbs green most, red
# least, which is the asymmetry POS relies on -- an achromatic (1,1,1) signature
# would be indistinguishable from a lighting change and unrecoverable in
# principle, not just by this implementation.
PULSE_RGB = np.array([0.10, 0.60, 0.30])


def _face_rgb(bpm: float, seconds: float = 20.0, fps: float = FPS,
              pulse_amplitude: float = 0.005, illumination: float = 0.0,
              illumination_hz: float = 1.4, noise: float = 0.0,
              seed: int = 0) -> np.ndarray:
    """Mean-RGB over a face region, as a camera would report it.

    Defaults are deliberately hostile in one respect: `pulse_amplitude` 0.005 is
    a 0.5% modulation, which is the real order of magnitude. A test written with
    a 10% pulse would pass on almost any method.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * fps)) / fps
    skin = np.array([180.0, 120.0, 110.0])          # a plausible mean skin RGB

    pulse = np.sin(2 * np.pi * bpm / 60.0 * t)
    frames = skin * (1.0 + pulse_amplitude * np.outer(pulse, PULSE_RGB))

    if illumination:
        # Achromatic: scales all three channels together, exactly what a screen
        # redraw, a flickering light or the student rocking slightly does.
        #
        # Default 1.4 Hz -- deliberately *inside* the 0.6-5 Hz pulse band. A slow
        # drift would be the easy case: `ppg_processing` bandpasses it away
        # before the rate derivation ever sees it, and green-only would pass
        # too. In-band interference is the case that needs the projection.
        drift = 1.0 + illumination * np.sin(2 * np.pi * illumination_hz * t)
        frames = frames * drift[:, None]

    if noise:
        frames = frames + noise * rng.standard_normal(frames.shape)

    return frames


def _bpm(pulse: np.ndarray, fps: float = FPS) -> float | None:
    """Rate via the one derivation this codebase has, not a second one."""
    return estimate_window(pulse, fps).bpm


# ── the arithmetic ───────────────────────────────────────────────────────────

def test_rejects_input_that_is_not_rgb():
    with pytest.raises(ValueError):
        pos_pulse(np.zeros((100, 4)), FPS)


def test_too_short_an_input_yields_silence_not_an_error():
    """A session's first second has fewer frames than one window. That is
    ordinary, not exceptional, so it returns zeros rather than raising."""
    short = _face_rgb(70.0, seconds=WINDOW_SECONDS / 2)
    out = pos_pulse(short, FPS)
    assert out.shape == (len(short),)
    assert not out.any()


def test_output_is_zero_mean():
    """Overlap-add subtracts each window's mean, so no DC survives into the
    result. A DC offset would not break the autocorrelation downstream, but it
    would make the waveform useless for anything that plots it."""
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
    """The reason for the projection, stated as a test.

    A 5% achromatic flicker at 84/min against a 0.5% pulse at 72 -- the
    interference is ten times the signal and sits inside the pulse band, so no
    filter can remove it. POS can, because the flicker has no component in the
    plane it projects onto."""
    rgb = _face_rgb(72.0, pulse_amplitude=0.005, illumination=0.05,
                    illumination_hz=1.4)
    assert _bpm(pos_pulse(rgb, FPS)) == pytest.approx(72.0, abs=3.0)


def test_a_slow_drift_is_the_easy_case_and_does_not_test_the_projection():
    """Recorded because the first version of this suite used a 0.11 Hz drift and
    proved nothing: `ppg_processing` bandpasses 0.6-5 Hz, so slow achromatic
    drift never reaches the rate derivation at all. Green-only passed it too.

    Kept as an explicit statement of what is *not* being demonstrated, so the
    in-band test above is not mistaken for a weaker claim."""
    rgb = _face_rgb(72.0, pulse_amplitude=0.005, illumination=0.05,
                    illumination_hz=0.11)
    green_only = rgb[:, 1] - rgb[:, 1].mean()
    assert _bpm(green_only) == pytest.approx(72.0, abs=3.0)


def test_the_green_channel_alone_fails_where_pos_succeeds():
    """Establishes that the previous test is measuring POS and not the ease of
    the synthetic signal.

    Green-only is the naive alternative and the one worth ruling out
    explicitly: with the interference inside the pulse band it has no way to
    tell a heartbeat from a flicker, and locks onto the stronger one."""
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


# ── the streaming form ───────────────────────────────────────────────────────

def test_streaming_form_matches_the_batch_form_on_the_newest_sample():
    """`pos_pulse_last_window` exists so a 30 fps callback does constant work.
    It must produce the same window contribution the batch form would, or the
    live signal and any offline re-analysis would quietly disagree."""
    rgb = _face_rgb(75.0, seconds=10.0)
    window = int(WINDOW_SECONDS * FPS)

    streamed = pos_pulse_last_window(rgb, FPS)

    block = rgb[-window:]
    from src.app.services.pos_rppg import PROJECTION
    projected = PROJECTION @ (block / block.mean(axis=0)).T
    s1, s2 = projected[0], projected[1]
    combined = s1 + (s1.std() / s2.std()) * s2
    expected = combined[-1] - combined.mean()

    assert streamed == pytest.approx(expected, rel=1e-9)


def test_streaming_form_is_silent_before_a_full_window():
    assert pos_pulse_last_window(_face_rgb(70.0, seconds=1.0), FPS) == 0.0
