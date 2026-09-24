"""Choosing where on a face to measure colour, and measuring it (the input to POS).

Region math is plain numpy; `FaceLocator` imports OpenCV lazily. No frame, crop or
derived image ever leaves this module or is retained between calls.
"""

from __future__ import annotations

from dataclasses import dataclass

from typing import Any

import numpy as np

# (x0, y0, x1, y1) fractions of the face box: forehead and cheeks, avoiding eyes,
# mouth and hairline.
FOREHEAD = (0.30, 0.10, 0.70, 0.28)
LEFT_CHEEK = (0.15, 0.45, 0.38, 0.70)
RIGHT_CHEEK = (0.62, 0.45, 0.85, 0.70)
REGIONS = (FOREHEAD, LEFT_CHEEK, RIGHT_CHEEK)

# Luminance band kept before averaging: drops hair/shadow below, specular/clipped above.
MIN_LUMA = 40.0
MAX_LUMA = 240.0

# Below this fraction of usable pixels the mean is too noisy to trust.
MIN_USABLE_FRACTION = 0.25


@dataclass(frozen=True)
class RoiSample:
    """One frame's colour measurement."""
    rgb: tuple[float, float, float] | None
    # Fraction passing the luminance mask; a number so quality trends are visible.
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

    `frame` is (h, w, 3) RGB (converted from BGR at the capture boundary). Regions
    are pooled, weighted by usable pixel count.
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

    Haar cascade from the OpenCV wheel: nothing extra to download or vet. Detection
    runs every `redetect_every` frames, since constant re-detection jitters the box.
    """

    def __init__(self, redetect_every: int = 15, cascade: Any | None = None) -> None:
        """`cascade` is injectable (anything with `detectMultiScale`) for tests without OpenCV."""
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

        None means no measurement, not zero. The uint8 cast is required: the cascade
        asserts CV_8U and callers pass float luma.
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
            # Forget the stale box, so a student who left stops producing samples.
            self._last = None
            return None

        # Largest face = nearest = the student, not someone walking past behind them.
        self._last = tuple(int(v) for v in max(faces, key=lambda f: f[2] * f[3]))
        return self._last

    def reset(self) -> None:
        self._last = None
        self._since = 0
