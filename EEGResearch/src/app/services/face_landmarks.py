"""Named face landmarks from MediaPipe Face Mesh; the only mesh-index-to-face-part mapping.

The index table is unverified against hardware, so `check_topology` refuses sets
no real face could produce. It cannot catch a left/right mirror; only the camera
check can. MediaPipe is imported only when a landmarker is requested.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Mesh index per name, with iris refinement (468-477). Left/right are the subject's own,
# so the subject's left eye is on the image right; a swap here is the likeliest error.
MEDIAPIPE_INDICES = {
    "left_eye_outer": 263,
    "left_eye_inner": 362,
    "left_eye_upper": 386,
    "left_eye_lower": 374,
    "left_iris": 473,
    "right_eye_outer": 33,
    "right_eye_inner": 133,
    "right_eye_upper": 159,
    "right_eye_lower": 145,
    "right_iris": 468,
    "nose_tip": 1,
    "mouth_left": 291,
    "mouth_right": 61,
    "chin": 152,
}

# Below this a landmark is absent: Face Mesh reports occluded points as confident fakes.
MIN_VISIBILITY = 0.5


def named_landmarks(points: Any, width: int, height: int) -> dict:
    """`{name: (x, y)}` in pixels, for the names this module knows.

    Scaled to pixels, or the fit stretches every face by the aspect ratio. Unusable
    names are omitted, not None: `face_geometry` counts the names it was given.
    """
    out: dict[str, tuple[float, float]] = {}
    if points is None or width <= 0 or height <= 0:
        return out

    for name, index in MEDIAPIPE_INDICES.items():
        try:
            point = points[index]
        except (IndexError, KeyError, TypeError):
            continue

        seen = getattr(point, "visibility", None)
        if seen is None:
            seen = getattr(point, "presence", None)
        # A missing visibility field is not zero visibility.
        if seen is not None and seen < MIN_VISIBILITY:
            continue

        x, y = getattr(point, "x", None), getattr(point, "y", None)
        if x is None or y is None:
            continue
        x, y = float(x) * width, float(y) * height
        if not (x == x and y == y):        # NaN, without importing math
            continue
        out[name] = (x, y)
    return out


def check_topology(landmarks: dict) -> str | None:
    """Reason the named set cannot be a face, or None if it could be.

    Deliberately weak: holds for any head pose and face size a child has.
    Catches gross misassignment, not a mirror.
    """
    def mid_y(*names):
        ys = [landmarks[n][1] for n in names if n in landmarks]
        return sum(ys) / len(ys) if ys else None

    eyes = mid_y("left_eye_outer", "left_eye_inner",
                 "right_eye_outer", "right_eye_inner")
    mouth = mid_y("mouth_left", "mouth_right")
    chin = mid_y("chin")

    # y grows downward, so "above" is a smaller y.
    if eyes is not None and mouth is not None and eyes >= mouth:
        return "eyes_below_mouth"
    if mouth is not None and chin is not None and mouth >= chin:
        return "mouth_below_chin"

    # The nose sits between the eye corners at any yaw short of profile.
    xs = [landmarks[n][0] for n in ("left_eye_outer", "right_eye_outer")
          if n in landmarks]
    if len(xs) == 2 and "nose_tip" in landmarks:
        nose = landmarks["nose_tip"][0]
        if not (min(xs) <= nose <= max(xs)):
            return "nose_outside_eyes"

    # An iris lies within its own eye's corners (catches an iris paired with the wrong eye).
    for side in ("left", "right"):
        iris = landmarks.get(f"{side}_iris")
        outer = landmarks.get(f"{side}_eye_outer")
        inner = landmarks.get(f"{side}_eye_inner")
        if iris and outer and inner:
            lo, hi = sorted((outer[0], inner[0]))
            # Margin for a hard sideways look plus detector wobble.
            span = hi - lo
            if span > 0 and not (lo - 0.35 * span <= iris[0] <= hi + 0.35 * span):
                return f"{side}_iris_outside_eye"

    return None


MODEL_ENV = "FACE_LANDMARK_MODEL_PATH"

# Pinned to `/1/`, not `/latest/`, so the checksum cannot go stale.
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
             "face_landmarker/float16/1/face_landmarker.task")
MODEL_SHA256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"
MODEL_BYTES = 3_758_596

# Bounds the write before the digest (the real control) is checked.
MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024
DOWNLOAD_TIMEOUT_S = 60


def default_model_path() -> Path:
    """Where the landmark bundle is looked for when nothing says otherwise."""
    return Path(__file__).resolve().parents[3] / "models" / "face_landmarker.task"


def verify(path: Path) -> bool:
    """Whether the file on disk is the model this code was written against."""
    path = Path(path)
    if not path.exists() or path.stat().st_size != MODEL_BYTES:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest() == MODEL_SHA256


def ensure_model(path: Path | None = None, *, allow_download: bool = True) -> Path:
    """Return a verified model path, downloading once if permitted.

    Setup-time only (`start.ps1 -Gaze`); `FaceMeshLandmarker` never calls it.
    """
    path = Path(path) if path is not None else default_model_path()
    if verify(path):
        return path
    if path.exists():
        logger.warning("landmark model at %s failed verification; discarding", path)
        try:
            path.unlink()
        except OSError as exc:
            # Windows locks open files; name the cause rather than a raw PermissionError.
            raise OSError(
                f"could not replace the landmark model at {path}: {exc}. "
                f"Stop any running sidecar and re-run."
            ) from exc
    if not allow_download:
        raise FileNotFoundError(f"landmark model missing or unverified at {path}")

    if not MODEL_URL.startswith("https://"):
        raise ValueError("refusing to fetch the model over a non-TLS URL")

    logger.info("downloading face landmark model (%.1f MB)", MODEL_BYTES / 1e6)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=DOWNLOAD_TIMEOUT_S) as src, \
                tmp.open("wb") as dst:
            written = 0
            while chunk := src.read(1 << 20):
                written += len(chunk)
                if written > MAX_DOWNLOAD_BYTES:
                    raise ValueError("landmark model download exceeded its size cap")
                dst.write(chunk)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    if not verify(path):
        # Deleted, so a partial or substituted file is never trusted next run.
        path.unlink(missing_ok=True)
        raise ValueError(
            f"landmark model checksum mismatch; expected {MODEL_SHA256[:16]}..."
        )
    return path


class _TasksMesh:
    """MediaPipe's Tasks `FaceLandmarker` behind the legacy `process()` shape.

    MediaPipe 1.0.0 removed `mp.solutions`. `VIDEO` mode needs non-decreasing
    timestamps, so they are clamped (two frames can round to the same ms).
    """

    def __init__(self, model_path: str | None = None) -> None:
        # Verified before importing MediaPipe, so the error names the real cause.
        path = Path(model_path or os.environ.get(MODEL_ENV) or default_model_path())
        if path.is_file() and not verify(path):
            # Checked at load too, not only at install.
            raise ValueError(
                f"refusing to load unverified landmark model at {path}; "
                f"expected sha256 {MODEL_SHA256[:16]}... -- re-provision it "
                f"with ./start.ps1 -Gaze"
            )
        if not path.is_file():
            raise FileNotFoundError(
                f"no face landmark model at {path}.\n"
                f"MediaPipe 1.0.0 does not ship one -- the Tasks API loads it "
                f"from a file. Fetch it once (about 3.8 MB):\n"
                f"    mkdir -p \"{path.parent}\"\n"
                f"    curl -L -o \"{path}\" {MODEL_URL}\n"
                f"or set {MODEL_ENV} to a copy you already have."
            )

        import numpy as np                                  # noqa: PLC0415
        import mediapipe as mp                              # noqa: PLC0415
        from mediapipe.tasks import python as mp_python     # noqa: PLC0415
        from mediapipe.tasks.python import vision           # noqa: PLC0415

        self._np = np
        self._mp = mp

        self._landmarker = vision.FaceLandmarker.create_from_options(
            vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=str(path)),
                running_mode=vision.RunningMode.VIDEO,
                num_faces=1,
                min_face_detection_confidence=0.5,
                min_tracking_confidence=0.5,
                # Geometry is derived in face_geometry, not from MediaPipe's matrix.
                output_face_blendshapes=False,
                output_facial_transformation_matrixes=False,
            )
        )
        self._last_ms = -1

    def process(self, frame: Any) -> Any:
        """The legacy return shape: `.multi_face_landmarks[0].landmark`."""
        mp = self._mp
        # SRGB = uint8 RGB; the C++ boundary requires a contiguous array.
        image = mp.Image(image_format=mp.ImageFormat.SRGB,
                         data=self._np.ascontiguousarray(frame, dtype="uint8"))
        ms = max(int(time.perf_counter() * 1000), self._last_ms + 1)
        self._last_ms = ms
        result = self._landmarker.detect_for_video(image, ms)
        faces = getattr(result, "face_landmarks", None) or []
        if not faces:
            return type("R", (), {"multi_face_landmarks": None})()
        return type("R", (), {
            "multi_face_landmarks": [type("F", (), {"landmark": faces[0]})()]
        })()


class FaceMeshLandmarker:
    """MediaPipe Face Mesh, wrapped to return named landmarks.

    `mesh` is injectable so `locate()` is testable without MediaPipe.
    """

    def __init__(self, mesh: Any | None = None,
                 model_path: str | None = None) -> None:
        # Reasons already logged, so a wrong table doesn't flood the log at frame rate.
        self._reported: set[str] = set()
        self._rejections = 0
        # Why the last frame produced nothing. See locate().
        self.last_reason: str | None = "no_face"
        if mesh is not None:
            self._mesh = mesh
            return
        self._mesh = _TasksMesh(model_path)

    def locate(self, frame: Any, width: int, height: int) -> dict:
        """Named landmarks for the first face found, or `{}` (never raises on no face).

        `last_reason` says which empty: "no_face" or a `check_topology` refusal;
        None means a face was returned.
        """
        result = self._mesh.process(frame)
        faces = getattr(result, "multi_face_landmarks", None)
        if not faces:
            self.last_reason = "no_face"
            return {}

        named = named_landmarks(faces[0].landmark, width, height)
        wrong = check_topology(named)
        if wrong is not None:
            self.last_reason = wrong
            self._rejections += 1
            # Logged once per reason; `rejections` keeps the count.
            if wrong not in self._reported:
                self._reported.add(wrong)
                logger.error("landmark topology rejected: %s "
                             "(the mesh index table is likely wrong; further "
                             "occurrences of this reason are counted, not logged)",
                             wrong)
            return {}
        self.last_reason = None
        return named

    @property
    def rejections(self) -> int:
        """How many frames the topology check has refused (the log is deduplicated)."""
        return self._rejections
