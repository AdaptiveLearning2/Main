"""Named face landmarks from MediaPipe Face Mesh.

`face_geometry` takes named landmarks and returns head pose and gaze. This
file produces the names -- the only place that maps a mesh index to a face
part, so swapping detectors only means rewriting this file.

**The index table below is unverified against hardware.** MediaPipe 1.0.0
doesn't ship the canonical mesh as a data file, and there's no camera in CI,
so the mapping comes from published topology, not measurement. This can fail
silently: a left/right swap produces a *mirrored* gaze, not an obviously
broken one, so every aggregate over it still looks healthy.

So the mapping isn't trusted outright. `check_topology` re-derives the
relationships a real face must satisfy (eyes above mouth, nose between the
eyes, chin below everything) and refuses a set that violates them -- a wrong
index shows up as a first-frame refusal instead of a mirrored number nobody
questions. It doesn't replace the manual camera check that confirms the
table itself; it's what makes shipping before that check safe.

Nothing here is imported unless a caller asks for a landmarker: MediaPipe is
a heavy optional dependency a headband-only deployment shouldn't need.
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

# Mesh index per landmark name, for MediaPipe Face Mesh with iris refinement
# (`refine_landmarks=True`, which supplies 468-477).
#
# **Left and right are the subject's own**, matching `face_geometry`'s
# canonical model. MediaPipe numbers the mesh on the canonical face, so the
# subject's left eye appears on the *right* of a non-mirrored image -- the
# most likely thing here to be backwards, and something `check_topology`
# can't catch on its own, since a mirrored face is still a valid face. The
# frame is not mirrored: looking hard left drives `gaze.x` positive and
# turning the head left drives `yaw` positive. `gaze` itself can't detect a
# label swap (it averages both eyes in image coordinates), so `head_pose`
# adjudicates by refusing a mirrored set outright.
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

# Below this, treat a landmark as absent rather than as a position. Face Mesh
# reports every point on every frame regardless of whether it can see it, so
# an occluded corner arrives as a confident-looking but fake coordinate.
MIN_VISIBILITY = 0.5


def named_landmarks(points: Any, width: int, height: int) -> dict:
    """`{name: (x, y)}` in pixels, for the names this module knows.

    `points` is a sequence indexable by mesh index, each item exposing `x`,
    `y` and optionally `visibility` / `presence` -- the shape MediaPipe
    returns. Normalised coordinates are scaled to pixels here since a fit
    over normalised coordinates would silently stretch every face by the
    frame's aspect ratio.

    Names whose index is missing, non-finite or too poorly seen are
    **omitted**, not set to None: `face_geometry` counts the names it was
    given, and a None entry would be counted as supplied.
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
        # A missing visibility field is not the same as zero visibility --
        # Face Mesh often reports no visibility at all, so a missing field
        # must not reject every point.
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

    Exists because the index table above is unverified: this re-derives what
    must be true of any real face, so a wrong index is caught on the first
    frame instead of becoming a mirrored gaze nobody questions.

    Deliberately weak. Every check holds for any yaw/pitch/roll a neck
    allows and any face size, since a check tuned to a square-on adult would
    reject the children this is for. It catches gross misassignment, not a
    left/right mirror (a mirrored face satisfies every relation below) --
    only the camera check catches that.
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

    # The nose sits between the eye corners horizontally, at any yaw short of
    # profile (where the fit is refused anyway).
    xs = [landmarks[n][0] for n in ("left_eye_outer", "right_eye_outer")
          if n in landmarks]
    if len(xs) == 2 and "nose_tip" in landmarks:
        nose = landmarks["nose_tip"][0]
        if not (min(xs) <= nose <= max(xs)):
            return "nose_outside_eyes"

    # An iris lies within its own eye's corners -- catches an iris index
    # paired with the wrong eye, which would otherwise be invisible since
    # both points look plausible on a face.
    for side in ("left", "right"):
        iris = landmarks.get(f"{side}_iris")
        outer = landmarks.get(f"{side}_eye_outer")
        inner = landmarks.get(f"{side}_eye_inner")
        if iris and outer and inner:
            lo, hi = sorted((outer[0], inner[0]))
            # A margin, because a hard sideways look genuinely puts the iris
            # against the corner, and detector wobble can carry it just past.
            span = hi - lo
            if span > 0 and not (lo - 0.35 * span <= iris[0] <= hi + 0.35 * span):
                return f"{side}_iris_outside_eye"

    return None


MODEL_ENV = "FACE_LANDMARK_MODEL_PATH"

# Pinned to `/1/`, not `/latest/`. Google serves both with the same bytes
# today, but `latest` is a moving target, and a checksum pinned against it
# would fail on the next release. Same reasoning as the emotion model's URL.
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
             "face_landmarker/float16/1/face_landmarker.task")
MODEL_SHA256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"
MODEL_BYTES = 3_758_596

# Well above the real model size, well below anything that fills a disk.
# The digest is the real security control; this just bounds what a
# redirected or hostile URL can write before the digest is checked.
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

    A **setup-time** step, mirroring `face_emotion.ensure_model`, called from
    `start.ps1 -Gaze`. `FaceMeshLandmarker` deliberately never calls it: the
    sidecar must not reach the internet the first time a lesson opens a
    camera, since a network failure there would look like a broken feature
    rather than an incomplete install.
    """
    path = Path(path) if path is not None else default_model_path()
    if verify(path):
        return path
    if path.exists():
        logger.warning("landmark model at %s failed verification; discarding", path)
        try:
            path.unlink()
        except OSError as exc:
            # Windows locks open files, so an earlier `-Gaze` session still
            # holding the model open would otherwise surface as a raw
            # PermissionError instead of a clear message.
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
        # Delete rather than leave behind: the next run checks existence
        # first, so a partial or substituted file would otherwise be trusted.
        path.unlink(missing_ok=True)
        raise ValueError(
            f"landmark model checksum mismatch; expected {MODEL_SHA256[:16]}..."
        )
    return path


class _TasksMesh:
    """MediaPipe's Tasks `FaceLandmarker` behind the legacy `process()` shape.

    **MediaPipe 1.0.0 removed `mp.solutions` entirely**, the legacy Solutions
    API this module was originally written against. `mp.solutions.face_mesh`
    now raises `AttributeError`, which reads like a broken install and
    isn't one -- the Tasks API replaces it, with a different call shape and
    a model bundle no longer compiled into the wheel.

    Adapted here instead of rewriting `locate()`, which is the half with
    tests: porting the untested half to fit the tested half keeps existing
    tests exercising real code, and confines the API change to construction.

    `VIDEO` mode, not `IMAGE`, since it tracks between frames. It requires
    timestamps that never go backwards, so they're clamped rather than
    trusted -- `perf_counter` is monotonic, but two fast frames can round to
    the same millisecond, which would otherwise raise mid-capture.
    """

    def __init__(self, model_path: str | None = None) -> None:
        # Model is resolved and verified **before** MediaPipe is imported,
        # matching `face_emotion`: checking first means a bad model reports
        # "unverified model" rather than "mediapipe missing" (two different
        # problems), and lets this check run on machines without MediaPipe,
        # since CI has none.
        path = Path(model_path or os.environ.get(MODEL_ENV) or default_model_path())
        if path.is_file() and not verify(path):
            # Checked again here, not only in `ensure_model`, which only
            # protects the moment of install. A truncated or hand-swapped
            # `.task` would otherwise load without complaint and produce
            # wrong landmarks instead of an absent reading.
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
                # Not needed and not free -- geometry is derived from the
                # landmarks in face_geometry instead of trusting MediaPipe's
                # own matrix, which makes camera assumptions we don't share.
                output_face_blendshapes=False,
                output_facial_transformation_matrixes=False,
            )
        )
        self._last_ms = -1

    def process(self, frame: Any) -> Any:
        """The legacy return shape: `.multi_face_landmarks[0].landmark`."""
        mp = self._mp
        # SRGB means uint8 RGB, matching what OpenCvFrameSource hands out.
        # Contiguity is required at the C++ boundary; a cropped/sliced array
        # isn't contiguous.
        image = mp.Image(image_format=mp.ImageFormat.SRGB,
                         data=self._np.ascontiguousarray(frame, dtype="uint8"))
        ms = max(int(time.perf_counter() * 1000), self._last_ms + 1)
        self._last_ms = ms
        result = self._landmarker.detect_for_video(image, ms)
        faces = getattr(result, "face_landmarks", None) or []
        if not faces:
            return type("R", (), {"multi_face_landmarks": None})()
        # Tasks returns a plain list of landmarks per face; wrap it to match
        # the legacy API's `.landmark` attribute.
        return type("R", (), {
            "multi_face_landmarks": [type("F", (), {"landmark": faces[0]})()]
        })()


class FaceMeshLandmarker:
    """MediaPipe Face Mesh, wrapped to return named landmarks.

    Constructed lazily and injectable, like `FaceLocator`: importing
    MediaPipe is a heavy dependency a headband-only deployment doesn't need,
    and tests need to drive the pipeline without it.

    Only **constructing** a real Face Mesh is untestable here (it needs
    MediaPipe and a camera); `locate()` is exercised through an injected
    mesh instead.
    """

    def __init__(self, mesh: Any | None = None,
                 model_path: str | None = None) -> None:
        # Tracks reasons already logged, so a wrong index table doesn't flood
        # the log with tens of identical lines a second at capture rate.
        self._reported: set[str] = set()
        self._rejections = 0
        # Why the last frame produced nothing. See locate().
        self.last_reason: str | None = "no_face"
        if mesh is not None:
            self._mesh = mesh
            return
        self._mesh = _TasksMesh(model_path)

    def locate(self, frame: Any, width: int, height: int) -> dict:
        """Named landmarks for the first face found, or `{}`.

        Returns empty rather than raising on a frame with no face -- that's
        an ordinary outcome several times a minute.

        **`last_reason` says which kind of empty**, since `{}` covers two
        different events: no face detected, or a face detected but
        `check_topology` refused the landmark set (which happens routinely
        near profile, where `nose_outside_eyes` becomes true). Collapsing
        both to "no face" would send someone to check lighting when the real
        cause is elsewhere. `None` means a face was returned successfully.
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
            # Logged once per reason, not once per frame: this means the
            # index table is wrong (a standing condition), not a one-off, so
            # repeating it every frame would just bury it in copies. The
            # count below still tracks how often it happens.
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
        """How many frames the topology check has refused.

        Exposed because the log is deduplicated -- without this count,
        "wrong on one frame" and "wrong on every frame" would look the same.
        """
        return self._rejections
