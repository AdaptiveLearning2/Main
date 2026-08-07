"""Choosing where on a face to measure colour, and measuring it.

POS turns a sequence of mean RGB values into a pulse. This module produces those
values. It is a larger share of the work than the algorithm is, and more of the
accuracy: a clean mean over well-chosen skin is worth more than any refinement
of the projection.

Split deliberately in two. Everything that reasons about *regions and pixels* is
plain numpy and is tested without a camera. Only `FaceLocator`, which finds the
face, needs OpenCV, and it imports it lazily so the sidecar still boots on a
machine with no camera dependencies installed — which is the state of CI, and of
any deployment that never enables the camera.

**No frame, crop, or derived image leaves this module.** Callers pass a frame in
and receive numbers out. Nothing here writes to disk or retains an image between
calls; that is the product constraint the whole facial pipeline is built around,
and it is cheapest to honour at the point where pixels are first touched.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Fraction of a detected face box used for each measurement region.
#
# Forehead and cheeks, and not the whole face, for a specific reason each: the
# eyes blink, which injects a step at an unpredictable rate straight into the
# pulse band; the mouth moves with speech and expression; the jaw and hairline
# bring in hair and shadow whose colour has no pulse at all and only dilutes the
# mean. What is left is the best-perfused skin that also moves least.
#
# Boxes are (x0, y0, x1, y1) as fractions of the face box.
FOREHEAD = (0.30, 0.10, 0.70, 0.28)
LEFT_CHEEK = (0.15, 0.45, 0.38, 0.70)
RIGHT_CHEEK = (0.62, 0.45, 0.85, 0.70)
REGIONS = (FOREHEAD, LEFT_CHEEK, RIGHT_CHEEK)

# Pixels outside this luminance band are dropped before averaging.
#
# The low end removes hair, shadow, spectacle frames and beard; the high end
# removes specular highlights, which are reflections of the light source rather
# than of skin and therefore carry the illumination's variation and none of the
# blood's. A blown-out pixel is also clipped by the sensor, so its variation is
# not merely useless but actively wrong.
MIN_LUMA = 40.0
MAX_LUMA = 240.0

# Below this fraction of usable pixels the mean is too noisy to trust -- the
# face is half out of frame, badly lit, or the box has landed on something that
# is not a face.
MIN_USABLE_FRACTION = 0.25


@dataclass(frozen=True)
class RoiSample:
    """One frame's colour measurement."""
    rgb: tuple[float, float, float] | None
    # Fraction of pixels in the regions that passed the luminance mask. Carried
    # rather than folded into a boolean so a caller can distinguish "the face
    # left the frame" from "the lighting got worse", and so a quality trend is
    # visible before it crosses a threshold.
    usable_fraction: float

    @property
    def ok(self) -> bool:
        return self.rgb is not None


def region_boxes(face: tuple[int, int, int, int]) -> list[tuple[int, int, int, int]]:
    """Absolute pixel boxes for the measurement regions within a face box.

    `face` is (x, y, w, h) as OpenCV reports it.
    """
    x, y, w, h = face
    out = []
    for x0, y0, x1, y1 in REGIONS:
        out.append((int(x + x0 * w), int(y + y0 * h),
                    int(x + x1 * w), int(y + y1 * h)))
    return out


def mean_rgb(frame: np.ndarray, face: tuple[int, int, int, int]) -> RoiSample:
    """Mean RGB over the measurement regions of one frame.

    `frame` is (h, w, 3) in **RGB** order. OpenCV hands out BGR; convert at the
    capture boundary rather than here, so this module has one colour convention
    and POS's projection matrix cannot silently be fed reversed channels.

    Regions are pooled into a single mean rather than averaged separately.
    Pooling weights by usable pixel count, which is what we want: a cheek that
    is half in shadow should contribute proportionally less than a fully lit
    forehead, not equally.
    """
    if frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError(f"expected (h, w, 3) RGB frame, got {frame.shape}")

    height, width = frame.shape[:2]
    totals = np.zeros(3)
    kept = 0
    seen = 0

    for x0, y0, x1, y1 in region_boxes(face):
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(width, x1), min(height, y1)
        if x1 <= x0 or y1 <= y0:
            continue
        patch = frame[y0:y1, x0:x1].reshape(-1, 3).astype(float)
        seen += len(patch)

        luma = patch.mean(axis=1)
        usable = patch[(luma >= MIN_LUMA) & (luma <= MAX_LUMA)]
        if len(usable):
            totals += usable.sum(axis=0)
            kept += len(usable)

    if seen == 0:
        return RoiSample(None, 0.0)

    fraction = kept / seen
    if fraction < MIN_USABLE_FRACTION or kept == 0:
        return RoiSample(None, fraction)
    return RoiSample(tuple(totals / kept), fraction)


class FaceLocator:
    """Finds a face box, with OpenCV imported only when one is constructed.

    Uses the Haar frontal-face cascade that ships inside `opencv-python`. That
    choice is about supply chain rather than accuracy: it is already present
    once OpenCV is installed, downloads nothing at runtime, and needs no model
    file to pin or checksum. MediaPipe would track better and is also Apache
    2.0, but it is a second model to vet, and a seated student facing a screen
    is the case Haar handles least badly.

    Detection is not run on every frame. It is slow relative to the frame rate,
    and a face that was at a location 200 ms ago is almost certainly still
    there; re-detecting constantly also makes the box jitter, and a box that
    jitters moves the measurement regions across different skin, which injects
    exactly the kind of in-band noise POS is there to remove.
    """

    def __init__(self, redetect_every: int = 15) -> None:
        import cv2                                   # noqa: PLC0415 -- lazy by design

        self._cv2 = cv2
        self._cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        if self._cascade.empty():
            raise RuntimeError("OpenCV Haar cascade failed to load")
        self.redetect_every = redetect_every
        self._last: tuple[int, int, int, int] | None = None
        self._since = 0

    def locate(self, gray: np.ndarray) -> tuple[int, int, int, int] | None:
        """Face box for this frame, reusing the previous one between detections.

        `gray` is a single-channel image. Returns None only when no face has
        been found yet or the last detection has aged out without a replacement
        -- a caller should treat that as "no measurement", never as a zero.
        """
        if self._last is not None and self._since < self.redetect_every:
            self._since += 1
            return self._last

        faces = self._cascade.detectMultiScale(gray, scaleFactor=1.2,
                                               minNeighbors=5, minSize=(80, 80))
        self._since = 0
        if len(faces) == 0:
            # Deliberately forget the stale box rather than returning it
            # indefinitely. A student who has left should stop producing
            # samples, not keep emitting the colour of whatever is now in that
            # rectangle.
            self._last = None
            return None

        # Largest, which is the nearest face -- the student rather than someone
        # walking past behind them.
        self._last = tuple(int(v) for v in max(faces, key=lambda f: f[2] * f[3]))
        return self._last

    def reset(self) -> None:
        self._last = None
        self._since = 0
