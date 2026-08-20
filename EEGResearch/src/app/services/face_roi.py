"""Choosing where on a face to measure colour, and measuring it.

POS turns a sequence of mean RGB values into a pulse; this module produces those
values. A clean mean over well-chosen skin matters more to accuracy than any
refinement of the POS projection itself.

Split in two: region/pixel math is plain numpy, tested without a camera.
`FaceLocator` needs OpenCV and imports it lazily, so the sidecar still boots
where camera dependencies aren't installed (CI, or any camera-off deployment).

No frame, crop, or derived image ever leaves this module -- callers pass a frame
in and get numbers out, nothing is written to disk or retained between calls.
"""

from __future__ import annotations

from dataclasses import dataclass

from typing import Any

import numpy as np

# Fraction of a detected face box used for each measurement region.
#
# Forehead and cheeks, not the whole face: eyes blink (an unpredictable step
# straight into the pulse band), the mouth moves with speech, and the jaw/
# hairline bring in hair and shadow that dilute the mean with no pulse signal.
# What's left is the best-perfused skin that also moves least.
#
# Boxes are (x0, y0, x1, y1) as fractions of the face box.
FOREHEAD = (0.30, 0.10, 0.70, 0.28)
LEFT_CHEEK = (0.15, 0.45, 0.38, 0.70)
RIGHT_CHEEK = (0.62, 0.45, 0.85, 0.70)
REGIONS = (FOREHEAD, LEFT_CHEEK, RIGHT_CHEEK)

# Pixels outside this luminance band are dropped before averaging.
#
# Low end removes hair, shadow, glasses, beard. High end removes specular
# highlights -- reflections of the light source, not skin, so they carry the
# illumination's variation and none of the blood's. A blown-out pixel is also
# sensor-clipped, so its variation is actively wrong, not just useless.
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
    # Fraction of pixels that passed the luminance mask. Kept as a number, not a
    # boolean, so a caller can tell "the face left the frame" from "the lighting
    # got worse" and see a quality trend before it crosses a threshold.
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

    `frame` is (h, w, 3) in RGB order. OpenCV hands out BGR; convert at the capture
    boundary, not here, so this module has one colour convention and POS's
    projection matrix can't silently be fed reversed channels.

    Regions are pooled into one mean, weighted by usable pixel count, so a cheek
    half in shadow contributes proportionally less than a fully lit forehead.
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

    Uses the Haar frontal-face cascade that ships inside `opencv-python` -- a
    supply-chain choice, not an accuracy one: it's already present, downloads
    nothing, and needs no model file to vet. MediaPipe tracks better but is a
    second model to vet, for a case (seated student facing a screen) Haar
    already handles reasonably.

    Detection doesn't run every frame: it's slow relative to frame rate, a face
    seen 200ms ago is almost certainly still there, and constant re-detection
    makes the box jitter -- moving the measurement regions across different
    skin, which injects exactly the noise POS exists to remove.
    """

    def __init__(self, redetect_every: int = 15, cascade: Any | None = None) -> None:
        """`cascade` is injectable so `locate`'s logic (dtype cast, redetect
        interval, forgetting a stale box) can be tested without OpenCV
        installed. Anything with `detectMultiScale` works.
        """
        if cascade is not None:
            self._cascade = cascade
        else:
            import cv2  # noqa: PLC0415 -- lazy by design

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

        `gray` is a single-channel image, any numeric dtype. Returns None only
        when no face has ever been found or the last detection aged out with no
        replacement -- callers should treat that as "no measurement", not zero.

        The cast to uint8 is required, not defensive: callers compute luma as a
        float-weighted sum of channels, and OpenCV's cascade asserts
        `_image.depth() == CV_8U`, raising rather than converting.
        """
        if gray.dtype != np.uint8:
            gray = np.clip(gray, 0, 255).astype(np.uint8)

        if self._last is not None and self._since < self.redetect_every:
            self._since += 1
            return self._last

        faces = self._cascade.detectMultiScale(gray, scaleFactor=1.2,
                                               minNeighbors=5, minSize=(80, 80))
        self._since = 0
        if len(faces) == 0:
            # Forget the stale box rather than returning it indefinitely -- a student
            # who has left should stop producing samples, not keep emitting whatever
            # is now in that rectangle.
            self._last = None
            return None

        # Largest face = nearest = the student, not someone walking past behind them.
        self._last = tuple(int(v) for v in max(faces, key=lambda f: f[2] * f[3]))
        return self._last

    def reset(self) -> None:
        self._last = None
        self._since = 0
