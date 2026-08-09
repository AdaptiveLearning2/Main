"""Region selection and colour measurement, without a camera.

Everything here is plain numpy on synthetic frames. `FaceLocator` is not
exercised because it needs OpenCV, which is deliberately absent from CI and from
any deployment that never enables the camera — see the `face` extra. Its
behaviour is specified in its docstring and belongs to the manual verification
step, not to a test that would silently skip.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.app.services.face_roi import (
    MAX_LUMA,
    MIN_LUMA,
    MIN_USABLE_FRACTION,
    mean_rgb,
    region_boxes,
)

FACE = (100, 50, 200, 260)          # x, y, w, h


def _frame(colour=(180, 120, 110), size=(480, 640)) -> np.ndarray:
    return np.tile(np.array(colour, dtype=np.uint8), (*size, 1))


def test_regions_sit_inside_the_face_box():
    x, y, w, h = FACE
    for x0, y0, x1, y1 in region_boxes(FACE):
        assert x <= x0 < x1 <= x + w
        assert y <= y0 < y1 <= y + h


def test_regions_avoid_the_eyes_and_mouth():
    """The reason for three small boxes rather than one large one.

    Eyes blink, which injects a step into the pulse band at an unpredictable
    rate; the mouth moves with speech. Both bands are stated as fractions of
    face height: eyes sit near 0.30-0.42, mouth near 0.72-0.85."""
    _, y, _, h = FACE
    eyes = (y + 0.30 * h, y + 0.42 * h)
    mouth = (y + 0.72 * h, y + 0.85 * h)

    for _, y0, _, y1 in region_boxes(FACE):
        for lo, hi in (eyes, mouth):
            assert y1 <= lo or y0 >= hi, f"region {y0}-{y1} overlaps {lo}-{hi}"


def test_measures_a_flat_colour_exactly():
    out = mean_rgb(_frame((180, 120, 110)), FACE)
    assert out.ok
    assert out.rgb == pytest.approx((180, 120, 110), abs=0.5)
    assert out.usable_fraction == 1.0


def test_rejects_a_frame_that_is_not_rgb():
    with pytest.raises(ValueError):
        mean_rgb(np.zeros((480, 640)), FACE)


def test_dark_pixels_are_excluded_rather_than_averaged_in():
    """Hair, shadow and spectacle frames carry no pulse. Averaging them in does
    not merely add noise -- it scales the whole measurement toward a colour
    that never varies, shrinking the fractional modulation POS depends on."""
    frame = _frame((180, 120, 110))
    frame[:, :400] = (10, 10, 10)              # dark over the left half

    out = mean_rgb(frame, FACE)
    assert out.usable_fraction < 1.0
    if out.ok:
        assert min(out.rgb) > MIN_LUMA, "a dark pixel leaked into the mean"


def test_specular_highlights_are_excluded():
    """A blown-out pixel reflects the light source, not the skin, so it carries
    the illumination's variation and none of the blood's. It is also clipped by
    the sensor, making its variation actively wrong rather than merely absent."""
    frame = _frame((180, 120, 110))
    boxes = region_boxes(FACE)
    x0, y0, x1, y1 = boxes[0]
    frame[y0:y1, x0:x1] = 255                  # forehead entirely blown out

    out = mean_rgb(frame, FACE)
    assert out.usable_fraction < 1.0
    if out.ok:
        assert max(out.rgb) < MAX_LUMA


def test_no_measurement_when_too_little_usable_skin():
    """Half a face out of frame, or the box landed on something that is not a
    face. The contract is a missing sample, never a zero -- a zero would enter
    POS as a real measurement and put a step into the waveform."""
    frame = _frame((5, 5, 5))                  # everything below MIN_LUMA
    out = mean_rgb(frame, FACE)
    assert not out.ok
    assert out.rgb is None
    assert out.usable_fraction < MIN_USABLE_FRACTION


def test_a_face_box_off_the_edge_of_the_frame_is_clipped_not_wrapped():
    """Negative or oversized coordinates must clip. Numpy slicing with a
    negative start silently wraps to the far edge of the array, which would
    measure the wrong pixels rather than fail."""
    out = mean_rgb(_frame((180, 120, 110)), (-50, -30, 200, 260))
    assert out.ok, "a partially off-frame face should still measure"
    assert out.rgb == pytest.approx((180, 120, 110), abs=0.5), (
        "wrapped slicing would have measured pixels from the opposite edge"
    )


def test_pooling_weights_by_usable_pixels_not_by_region():
    """Three regions, pooled as one mean rather than averaged as three.

    A cheek half in shadow should contribute proportionally less than a fully
    lit forehead. Averaging per-region first would give the shadowed cheek's
    surviving pixels a third of the weight regardless of how few there were."""
    frame = _frame((200, 200, 200))
    boxes = region_boxes(FACE)
    x0, y0, x1, y1 = boxes[1]                  # left cheek -> a different colour
    frame[y0:y1, x0:x1] = (100, 100, 100)

    pooled = mean_rgb(frame, FACE).rgb[0]

    areas = [(bx1 - bx0) * (by1 - by0) for bx0, by0, bx1, by1 in boxes]
    weighted = (200 * (areas[0] + areas[2]) + 100 * areas[1]) / sum(areas)
    unweighted = (200 + 100 + 200) / 3

    assert pooled == pytest.approx(weighted, abs=1.0)
    if abs(weighted - unweighted) > 1.0:
        assert pooled != pytest.approx(unweighted, abs=1.0)


def test_a_pulse_survives_the_measurement_chain():
    """End to end at this layer: modulate a synthetic face and confirm the
    measured mean carries the modulation through to POS."""
    from src.app.services.ppg_processing import estimate_window
    from src.app.services.pos_rppg import pos_pulse

    fps, bpm = 30.0, 72.0
    t = np.arange(int(20 * fps)) / fps
    wave = 0.005 * np.sin(2 * np.pi * bpm / 60.0 * t)

    samples = []
    base = np.array([180.0, 120.0, 110.0])
    weights = np.array([0.10, 0.60, 0.30])
    for value in wave:
        frame = np.clip(base * (1 + value * weights), 0, 255).astype(np.uint8)
        out = mean_rgb(np.tile(frame, (480, 640, 1)), FACE)
        assert out.ok
        samples.append(out.rgb)

    assert estimate_window(pos_pulse(np.array(samples), fps), fps).bpm == pytest.approx(
        bpm, abs=3.0
    )


# ── the locator, with the cascade injected ───────────────────────────────────
#
# None of these could be written before `FaceLocator` took a cascade. Its
# constructor imported cv2 and loaded the Haar XML unconditionally, so it could
# not be built in CI, so every test in front of it used a fake locator and the
# real `locate` was covered by nothing at all. Three defects in this pipeline
# came out of that gap. These are the tests that gap was hiding.

class FakeCascade:
    """Records what it was handed and returns a scripted detection."""

    def __init__(self, boxes=((100, 100, 200, 200),)):
        self.boxes = boxes
        self.calls = 0
        self.dtypes: list[str] = []

    def detectMultiScale(self, gray, **kwargs):  # noqa: N802 -- OpenCV's name
        self.calls += 1
        self.dtypes.append(gray.dtype.name)
        return list(self.boxes)


def test_the_cascade_is_only_ever_handed_uint8():
    """The defect this cast exists for.

    Both callers compute luma as a weighted channel sum, which is float, and
    OpenCV's cascade asserts `_image.depth() == CV_8U` and raises rather than
    converting. On a real camera that took the capture thread down on the first
    frame; every test in front of it used a fake locator and saw nothing.
    """
    from src.app.services.face_roi import FaceLocator

    # redetect_every=0 so every call reaches the cascade rather than the cached
    # box -- otherwise this asserts on one call and calls it two.
    cascade = FakeCascade()
    locator = FaceLocator(redetect_every=0, cascade=cascade)

    locator.locate(np.full((480, 640), 128.0, dtype=np.float32))
    locator.locate(np.full((480, 640), 128.0, dtype=np.float64))

    assert cascade.dtypes == ["uint8", "uint8"]


def test_out_of_range_luma_is_clipped_not_wrapped():
    """A cast alone would wrap 300.0 to 44 and turn a blown-out highlight into
    a mid-grey, inventing detail where the sensor saturated."""
    from src.app.services.face_roi import FaceLocator

    cascade = FakeCascade()
    FaceLocator(redetect_every=0, cascade=cascade).locate(np.full((480, 640), 300.0))

    assert cascade.dtypes == ["uint8"]


def test_detection_is_not_run_on_every_frame():
    """It is slow relative to the frame rate, and a box that is re-derived every
    frame jitters -- which walks the measurement regions across different skin
    and injects exactly the in-band noise POS exists to remove."""
    from src.app.services.face_roi import FaceLocator

    cascade = FakeCascade()
    locator = FaceLocator(redetect_every=5, cascade=cascade)

    boxes = [locator.locate(np.zeros((480, 640), dtype=np.uint8)) for _ in range(10)]

    assert cascade.calls == 2, "the cascade ran more often than the interval"
    assert all(b == (100, 100, 200, 200) for b in boxes)


def test_a_stale_box_is_forgotten_rather_than_reused_forever():
    """A student who has left must stop producing samples, not keep emitting the
    colour of whatever now occupies that rectangle."""
    from src.app.services.face_roi import FaceLocator

    cascade = FakeCascade()
    locator = FaceLocator(redetect_every=0, cascade=cascade)
    assert locator.locate(np.zeros((480, 640), dtype=np.uint8)) is not None

    cascade.boxes = ()
    assert locator.locate(np.zeros((480, 640), dtype=np.uint8)) is None
    assert locator.locate(np.zeros((480, 640), dtype=np.uint8)) is None


def test_injecting_a_cascade_keeps_opencv_out_of_the_process():
    """Which is the point: the detection logic is testable with no camera stack
    installed, so CI covers it rather than skipping it."""
    import subprocess
    import sys
    import textwrap
    from pathlib import Path

    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            import sys
            sys.path.insert(0, sys.argv[1])
            import numpy as np
            from src.app.services.face_roi import FaceLocator

            class Cascade:
                def detectMultiScale(self, gray, **kw):
                    assert gray.dtype == np.uint8
                    return [(1, 2, 3, 4)]

            assert FaceLocator(redetect_every=0, cascade=Cascade()).locate(
                np.zeros((64, 64), dtype=np.float32)
            ) == (1, 2, 3, 4)
            assert "cv2" not in sys.modules, "constructing the locator imported cv2"
            print("clean")
        """), str(Path(__file__).resolve().parents[1])],
        capture_output=True, text=True,
    )
    assert "clean" in result.stdout, result.stderr
