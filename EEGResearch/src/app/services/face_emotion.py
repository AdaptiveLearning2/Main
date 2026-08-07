"""FER+ emotion classification, pinned and verified.

Separate from the heart path on purpose. The two run off the same camera but are
independently switchable at capture time and independently consented — a student
may permit expression and refuse heart, or the reverse — so nothing here knows
about POS and nothing in POS knows about this.

Provenance, checked 2026-08-07
------------------------------
Apache 2.0, published by the ONNX Model Zoo, commercial use permitted with
attribution. This is the one third-party model in the facial pipeline that
survived review; the rPPG networks did not, which is why `pos_rppg` exists. See
the plan's Phase 4 dependency section.

What the original did and why none of it survives
-------------------------------------------------
The CLI this is ported from fetched 35 MB over `resolve/main/` — a moving
reference, so the bytes could change under us between one student's laptop and
the next — with no checksum, no timeout, no size cap, and then imported whatever
arrived into an inference session. Here the revision is pinned to a commit, the
SHA-256 is verified before the file is used, and a mismatch deletes the download
rather than leaving a partial or substituted model on disk for the next run to
trust.

It also swallowed every exception into a `reason` string. A model that failed to
load, a session that crashed, and a face the classifier was merely unsure about
all produced the same shape: `trusted: false` with prose. A whole session could
report no emotions and look exactly like a student with a neutral face. Here a
persistent failure is *degraded* — a distinct state, surfaced through
`get_meta()` — and only genuine low confidence is `trusted: false`.
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

# Pinned to a commit, not to `main`. A moving reference means the bytes a
# student's laptop downloads next term need not be the bytes reviewed today.
MODEL_REVISION = "4d016bfebdb4122b1f37511c5a9d40b5c87054a8"
MODEL_URL = (
    "https://huggingface.co/onnxmodelzoo/emotion-ferplus-8/resolve/"
    f"{MODEL_REVISION}/emotion-ferplus-8.onnx"
)
MODEL_SHA256 = "a2a2ba6a335a3b29c21acb6272f962bd3d47f84952aaffa03b60986e04efa61c"
MODEL_BYTES = 35_040_571

# A ceiling on what will be written to disk before hashing. Without it a
# redirect to something enormous is an unbounded write on a student's machine,
# and the checksum only tells you afterwards.
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
DOWNLOAD_TIMEOUT_S = 60

# FER+ output order. Fixed by the model; not a preference.
EMOTION_LABELS = (
    "neutral", "happy", "surprise", "sad",
    "angry", "disgust", "fear", "contempt",
)

# Below this the label is reported but marked untrusted. Distinct from
# `degraded`: an unsure classifier is working correctly.
MIN_CONFIDENCE = 0.50

# Consecutive inference failures before the classifier calls itself degraded.
# One is a bad crop; a run of them is a broken session, and the difference is
# what the original could not express.
FAILURE_TOLERANCE = 5

FACE_INPUT = 64


@dataclass(frozen=True)
class EmotionResult:
    label: str | None
    confidence: float | None
    trusted: bool
    # Machine-readable cause when there is no usable label: "low_confidence" |
    # "inference_failed" | "no_face". Control flow matches on this; `reason` is
    # for display. The original had only prose, so a caller wanting to
    # distinguish a broken model from an unsure one had to parse English.
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

    Intended as a setup-time step. Calling it at capture time would put a 35 MB
    transfer in front of a student's first session, and a network failure would
    surface as a broken feature rather than an incomplete install.
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
        # Deleted rather than left behind. A partial or substituted file on disk
        # would be trusted by the next run, which checks existence before it
        # checks anything else.
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
        """`session` is injectable so the classification logic, the confidence
        gate and the degraded state can be tested without onnxruntime, which is
        absent from CI by design."""
        if session is not None:
            self._session = session
        else:
            # Verified before onnxruntime is imported, not after. Checking first
            # is cheaper, and it means a bad model reports "unverified model"
            # rather than "onnxruntime missing" -- two very different problems
            # that would otherwise produce the same message on a machine lacking
            # the extra.
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
            # A shape mismatch is a wrong model, not a bad frame -- count it as
            # a failure so a substituted file becomes degraded rather than an
            # eternity of untrusted readings.
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

        `emotion_degraded` is the field the original could not express: a broken
        session and a neutral student both produced `trusted: false`, so a whole
        session of failures was indistinguishable from a calm one.
        """
        return {
            "emotion_classified": self._classified,
            "emotion_degraded": self.degraded,
            "emotion_last_error": self._last_error,
            "emotion_model_revision": MODEL_REVISION[:8],
        }
