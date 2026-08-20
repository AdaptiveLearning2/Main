"""FER+ emotion classification, pinned and verified.

Separate from the heart path on purpose: the two run off the same camera but
are independently switchable and independently consented (a student may
permit expression and refuse heart, or the reverse), so nothing here knows
about POS and nothing in POS knows about this.

Provenance: Apache 2.0, published by the ONNX Model Zoo, commercial use
permitted with attribution. This is the one third-party model in the facial
pipeline that passed review; the rPPG networks didn't, which is why
`pos_rppg` exists instead.

The model download is pinned to a commit (not a moving reference like
`resolve/main/`), verified by SHA-256 before use, with a size cap and a
timeout. A checksum mismatch deletes the download rather than leaving a
partial or substituted model on disk.

A load failure, a crashed inference session, and a face the classifier is
merely unsure about are three different things and are reported as such:
`degraded` (surfaced through `get_meta()`) covers a persistent failure, and
only genuine low confidence is `trusted: false`.
"""

from __future__ import annotations

import hashlib
import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Pinned to a commit, not `main` -- a moving reference would let the bytes
# downloaded next term differ from what was reviewed today.
MODEL_REVISION = "4d016bfebdb4122b1f37511c5a9d40b5c87054a8"
MODEL_URL = (
    "https://huggingface.co/onnxmodelzoo/emotion-ferplus-8/resolve/"
    f"{MODEL_REVISION}/emotion-ferplus-8.onnx"
)
MODEL_SHA256 = "a2a2ba6a335a3b29c21acb6272f962bd3d47f84952aaffa03b60986e04efa61c"
MODEL_BYTES = 35_040_571

# Caps the download before hashing, so a redirect to something huge can't
# become an unbounded write -- the checksum alone would only catch it after.
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
DOWNLOAD_TIMEOUT_S = 60

# FER+ output order, fixed by the model.
EMOTION_LABELS = (
    "neutral", "happy", "surprise", "sad",
    "angry", "disgust", "fear", "contempt",
)

# Below this the label is reported but marked untrusted. Distinct from
# `degraded`: an unsure classifier is still working correctly.
MIN_CONFIDENCE = 0.50

# Consecutive inference failures before the classifier calls itself degraded.
# One is a bad crop; a run of them means a broken session.
FAILURE_TOLERANCE = 5

FACE_INPUT = 64


@dataclass(frozen=True)
class EmotionResult:
    label: str | None
    confidence: float | None
    trusted: bool
    # Machine-readable cause when there is no usable label: "low_confidence" |
    # "inference_failed" | "no_face". Control flow matches on this; `reason` is
    # for display.
    rejected_by: str | None = None
    reason: str = ""


def softmax(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float64).ravel()
    values = values - values.max()
    exp = np.exp(values)
    total = exp.sum()
    if not np.isfinite(total) or total <= 0:
        return np.full(values.shape, 1.0 / len(values))
    return exp / total


def to_gray64(frame: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray | None:
    """Crop a face out of an RGB frame and box-resample it to 64x64 grayscale.

    Numpy rather than `cv2.resize`: keeps the crop path testable without
    OpenCV, and avoids a second cv2 call per classified frame.

    Box averaging rather than nearest-neighbour: a face crop is typically
    150-250 px square, so nearest-neighbour throws away most pixels and makes
    the result depend on where the sample grid lands -- adding noise on every
    small head movement.
    """
    x, y, w, h = box
    height, width = frame.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(width, x + w), min(height, y + h)
    if x1 - x0 < FACE_INPUT or y1 - y0 < FACE_INPUT:
        # Smaller than the model's own input, so reaching 64x64 would mean
        # upsampling -- inventing detail never captured, which the classifier
        # would then label with full confidence. Treat a distant or
        # half-cropped face as a missing measurement, not a low-quality one.
        return None

    # Luma-weighted, matching what FER+ was trained on -- a flat RGB mean is a
    # different, redder image (most of a face is skin tone).
    gray = frame[y0:y1, x0:x1].astype(np.float32) @ np.array(
        [0.299, 0.587, 0.114], dtype=np.float32
    )
    rows = np.linspace(0, gray.shape[0], FACE_INPUT + 1).astype(int)
    cols = np.linspace(0, gray.shape[1], FACE_INPUT + 1).astype(int)
    # Guard against a zero-width bin when the crop is barely larger than 64 px.
    rows[1:] = np.maximum(rows[1:], rows[:-1] + 1)
    cols[1:] = np.maximum(cols[1:], cols[:-1] + 1)

    # reduceat instead of a nested loop over the 4096 output cells -- the loop
    # cost 22 ms per crop, meaningful CPU for a few classifications a second.
    row_sums = np.add.reduceat(gray, rows[:-1], axis=0)
    block_sums = np.add.reduceat(row_sums, cols[:-1], axis=1)
    counts = np.outer(np.diff(rows), np.diff(cols))
    return (block_sums / counts).astype(np.float32)


def to_tensor(gray64: np.ndarray) -> np.ndarray:
    """A 64x64 grayscale crop to the model's 1x1x64x64 float input."""
    gray64 = np.asarray(gray64, dtype=np.float32)
    if gray64.shape != (FACE_INPUT, FACE_INPUT):
        raise ValueError(f"expected ({FACE_INPUT}, {FACE_INPUT}) grayscale, got {gray64.shape}")
    return gray64[None, None, :, :]


def verify(path: Path) -> bool:
    """Whether the file on disk is the model we pinned."""
    if not path.exists() or path.stat().st_size != MODEL_BYTES:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest() == MODEL_SHA256


def ensure_model(path: Path, *, allow_download: bool = True) -> Path:
    """Return a verified model path, downloading once if permitted.

    A setup-time step. Calling it at capture time would put a 35 MB transfer
    in front of a student's first session, and a network failure would look
    like a broken feature rather than an incomplete install.
    """
    path = Path(path)
    if verify(path):
        return path
    if path.exists():
        logger.warning("emotion model at %s failed verification; discarding", path)
        path.unlink()
    if not allow_download:
        raise FileNotFoundError(f"emotion model missing or unverified at {path}")

    if not MODEL_URL.startswith("https://"):
        raise ValueError("refusing to fetch the model over a non-TLS URL")

    logger.info("downloading emotion model (%.1f MB, revision %s)",
                MODEL_BYTES / 1e6, MODEL_REVISION[:8])
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=DOWNLOAD_TIMEOUT_S) as src, \
                tmp.open("wb") as dst:
            written = 0
            while chunk := src.read(1 << 20):
                written += len(chunk)
                if written > MAX_DOWNLOAD_BYTES:
                    raise ValueError("emotion model download exceeded its size cap")
                dst.write(chunk)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    if not verify(path):
        # Deleted rather than left behind -- the next run checks existence
        # before anything else and would trust a partial or substituted file.
        path.unlink(missing_ok=True)
        raise ValueError(
            f"emotion model checksum mismatch; expected {MODEL_SHA256[:16]}..."
        )
    return path


class EmotionClassifier:
    """FER+ over a grayscale face crop, with ONNX Runtime imported lazily.

    Holds no image. A crop is passed in, reduced to a label and a probability,
    and dropped -- the same contract as the colour path.
    """

    def __init__(self, model_path: Path, *, session: Any = None) -> None:
        """`session` is injectable so classification, the confidence gate and
        the degraded state can be tested without onnxruntime, which is absent
        from CI by design."""
        if session is not None:
            self._session = session
        else:
            # Verified before onnxruntime is imported: cheaper, and it means a
            # bad model reports "unverified model" rather than "onnxruntime
            # missing" on a machine lacking the extra.
            if not verify(Path(model_path)):
                raise ValueError(f"refusing to load unverified model at {model_path}")

            import onnxruntime as ort            # noqa: PLC0415 -- lazy by design

            self._session = ort.InferenceSession(
                str(model_path), providers=["CPUExecutionProvider"]
            )
        self._input_name = self._session.get_inputs()[0].name
        self._consecutive_failures = 0
        self._last_error: str | None = None
        self._classified = 0

    def classify(self, gray64: np.ndarray | None) -> EmotionResult:
        if gray64 is None:
            return EmotionResult(None, None, False, "no_face", "no usable face crop")

        try:
            logits = np.asarray(
                self._session.run(None, {self._input_name: to_tensor(gray64)})[0]
            ).ravel()
        except Exception as exc:                       # noqa: BLE001
            self._consecutive_failures += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.exception("emotion inference failed")
            return EmotionResult(None, None, False, "inference_failed", self._last_error)

        if logits.size != len(EMOTION_LABELS):
            # A shape mismatch means a wrong model, not a bad frame -- count it
            # as a failure so a substituted file becomes degraded rather than
            # an endless stream of untrusted readings.
            self._consecutive_failures += 1
            self._last_error = (
                f"model returned {logits.size} outputs, expected {len(EMOTION_LABELS)}"
            )
            return EmotionResult(None, None, False, "inference_failed", self._last_error)

        self._consecutive_failures = 0
        self._classified += 1

        probabilities = softmax(logits)
        index = int(np.argmax(probabilities))
        confidence = float(probabilities[index])
        label = EMOTION_LABELS[index]

        if confidence < MIN_CONFIDENCE:
            return EmotionResult(
                label, confidence, False, "low_confidence",
                f"{label} at {confidence:.0%}, below {MIN_CONFIDENCE:.0%}",
            )
        return EmotionResult(label, confidence, True, None,
                             f"{label} at {confidence:.0%}")

    @property
    def degraded(self) -> bool:
        return self._consecutive_failures >= FAILURE_TOLERANCE

    def get_meta(self) -> dict[str, Any]:
        """Emotion-path health, for the ingestion payload.

        `emotion_degraded` keeps a broken session distinguishable from a
        genuinely calm student -- both would otherwise read as `trusted: false`.
        """
        return {
            "emotion_classified": self._classified,
            "emotion_degraded": self.degraded,
            "emotion_last_error": self._last_error,
            "emotion_model_revision": MODEL_REVISION[:8],
        }
