"""Named face landmarks from MediaPipe Face Mesh (Phase 11, step 1).

`face_geometry` takes named landmarks and returns head pose and gaze. This is
what produces the names. It is deliberately the only file that knows a mesh
index from a face part, so swapping detector means rewriting this and nothing
else.

**The index table below is unverified against hardware.** MediaPipe 1.0.0 does
not ship the canonical face mesh as a data file, and there is no camera in CI or
on the machine this was written on, so the mapping from mesh index to face part
comes from the published Face Mesh topology rather than from anything measured
here. That is a real gap and it is the kind that fails silently: a left/right
swap produces a gaze that is *mirrored*, not obviously broken, and every
aggregate over it looks healthy.

So the mapping is not trusted. `check_topology` re-derives the relationships a
real face must satisfy -- eyes above mouth, nose between the eyes, chin below
everything -- and a set that violates them is refused rather than passed on. A
wrong index shows up on the first frame as a named refusal instead of as a
mirrored number on a dashboard six weeks later. It is not a substitute for the
manual camera check, which is what would confirm the table itself; it is what
makes shipping the table before that check safe.

Nothing here is imported unless a caller asks for a landmarker, matching
`face_roi.FaceLocator`: MediaPipe is a heavy optional dependency and a
deployment that never enables the camera must not need it installed.
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
# (`refine_landmarks=True`, which is what supplies 468-477).
#
# **Left and right are the subject's own**, matching `face_geometry`'s canonical
# model. MediaPipe numbers the mesh on the canonical face, so the subject's left
# eye appears on the *right* of a non-mirrored image -- the single most likely
# thing in this file to be backwards, and precisely what `check_topology`
# cannot detect on its own, since a mirrored face is still a valid face.
# Confirming it needs the manual camera check. The frame is not mirrored, so
# the subject's own left is the image right: looking hard left drives `gaze.x`
# POSITIVE and turning the head left drives `yaw` positive. Note that `gaze`
# cannot see a swap of these labels at all -- it averages both eyes in image
# coordinates -- so `head_pose` is what adjudicates, by refusing a mirrored set
# outright rather than answering it wrongly.
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

# Below this, a landmark is treated as absent rather than as a position. Face
# Mesh reports every point on every frame whether or not it can see it, so an
# occluded corner arrives as a confident-looking coordinate somewhere plausible.
# Absent is a state `face_geometry` handles -- it skips the point and reports
# how many it used -- and a fabricated one is not.
MIN_VISIBILITY = 0.5


def named_landmarks(points: Any, width: int, height: int) -> dict:
    """`{name: (x, y)}` in pixels, for the names this module knows.

    `points` is a sequence indexable by mesh index, each item exposing `x`, `y`
    and optionally `visibility` / `presence` -- the shape MediaPipe returns.
    Normalised coordinates are scaled by the frame size, because everything
    downstream works in pixels and a fit over normalised coordinates would
    silently stretch every face by the frame's aspect ratio.

    Names whose index is missing, non-finite or too poorly seen are **omitted**,
    not set to None: `face_geometry` counts the names it was given, and an
    entry present with no value would be counted as supplied.
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
        # Absent visibility means the detector does not report it, which is not
        # the same as reporting zero. Face Mesh's own landmarks often carry no
        # visibility at all, so a missing field must not reject every point.
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

    This exists because the index table above is unverified. It re-derives what
    must be true of any real face, from the landmarks themselves, so a wrong
    index is caught on the first frame rather than becoming a mirrored gaze
    nobody questions.

    Deliberately weak. Every check here holds for a face at any yaw, pitch or
    roll a neck allows, and for any face size -- a test that only passed for a
    square-on adult would reject the children this is for. It catches gross
    misassignment, which is the failure mode of a hand-written index table; it
    cannot catch a left/right mirror, because a mirrored face satisfies every
    relation below. Only the camera check catches that.
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

    # The nose sits between the eye corners horizontally. True at any yaw short
    # of profile, where the fit is refused anyway.
    xs = [landmarks[n][0] for n in ("left_eye_outer", "right_eye_outer")
          if n in landmarks]
    if len(xs) == 2 and "nose_tip" in landmarks:
        nose = landmarks["nose_tip"][0]
        if not (min(xs) <= nose <= max(xs)):
            return "nose_outside_eyes"

    # An iris lies within its own eye's corners. Catches an iris index paired
    # with the wrong eye, which is otherwise invisible: both are plausible
    # points on a face.
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

# Pinned to `/1/`, **not** `/latest/`. Google serves both and they are the same
# bytes today, but `latest` is by definition a moving target and a checksum
# pinned against one is a setup failure waiting for the next release. The
# emotion model has the same property for the same reason -- its URL carries a
# git revision rather than a branch.
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
             "face_landmarker/float16/1/face_landmarker.task")
MODEL_SHA256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"
MODEL_BYTES = 3_758_596

# Well above the real size and well below anything that would fill a disk. The
# size check is not the security control -- the digest is -- but it bounds what
# a redirected or hostile URL can make this process write before the digest is
# ever computed.
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

    A **setup-time** step, mirroring `face_emotion.ensure_model`, and called
    from `start.ps1 -Gaze`. `FaceMeshLandmarker` deliberately does not call it:
    a sidecar that installs on a student's laptop must not reach the internet
    the first time a lesson opens a camera, and a network failure there would
    look like the feature being broken rather than the install being incomplete.
    """
    path = Path(path) if path is not None else default_model_path()
    if verify(path):
        return path
    if path.exists():
        logger.warning("landmark model at %s failed verification; discarding", path)
        try:
            path.unlink()
        except OSError as exc:
            # Windows locks an open file, and this project's own convention is
            # a sidecar per window -- so an earlier `-Gaze` session still
            # holding the model open turns a designed failure mode into an
            # unhandled PermissionError from a setup script. Reported as what
            # it is instead.
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
        # Deleted rather than left behind: the next run checks existence before
        # it checks anything else, so a partial or substituted file on disk
        # would be trusted.
        path.unlink(missing_ok=True)
        raise ValueError(
            f"landmark model checksum mismatch; expected {MODEL_SHA256[:16]}..."
        )
    return path


class _TasksMesh:
    """MediaPipe's Tasks `FaceLandmarker` behind the legacy `process()` shape.

    **MediaPipe 1.0.0 removed `mp.solutions` entirely** -- the whole legacy
    Solutions API, including the `FaceMesh` this module was written against.
    `mp.solutions.face_mesh` raises `AttributeError: module 'mediapipe' has no
    attribute 'solutions'`, which reads like a broken install and is not one.
    The Tasks API replaces it, with a different call shape and a model bundle
    that is no longer compiled into the wheel.

    Adapting here rather than rewriting `locate()` is deliberate. `locate()` is
    the half with tests, and the shape those tests inject is this one; porting
    the untested half to fit the tested half keeps every existing test
    exercising real code, and confines the API change to construction, which is
    the part that was already documented as untestable without hardware.

    `VIDEO` rather than `IMAGE`: it tracks between frames, which is what
    `static_image_mode=False` bought before. It requires timestamps that never
    go backwards, so they are clamped rather than trusted -- `perf_counter` is
    monotonic, but rounding two fast frames to the same millisecond is not, and
    the failure is an exception mid-capture rather than a bad landmark.
    """

    def __init__(self, model_path: str | None = None) -> None:
        # The model is resolved and verified **before** MediaPipe is imported,
        # matching `face_emotion`, which does the same ahead of onnxruntime and
        # explains why: checking first is cheaper, and it means a bad model
        # reports "unverified model" rather than "mediapipe missing" -- two
        # very different problems that would otherwise produce the same message
        # on a machine lacking the extra.
        #
        # It also decides whether this is testable. MediaPipe is deliberately
        # absent from CI, so a check placed after the import can only be
        # exercised on a machine that has it -- which is how the first version
        # of this went red.
        path = Path(model_path or os.environ.get(MODEL_ENV) or default_model_path())
        if path.is_file() and not verify(path):
            # Checked again here, not only in `ensure_model`. That is a
            # setup-time call, so on its own it protects the moment of install
            # and nothing after it: a truncated, half-written or hand-swapped
            # `.task` would load without complaint and produce landmarks that
            # are wrong rather than absent -- silently wrong gaze numbers being
            # exactly what a checksum exists to prevent.
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
                # Not needed and not free. The geometry is derived from the
                # landmarks here, deliberately -- see face_geometry, which fits
                # an orthographic pose rather than trusting a matrix whose
                # camera assumptions are not ours.
                output_face_blendshapes=False,
                output_facial_transformation_matrixes=False,
            )
        )
        self._last_ms = -1

    def process(self, frame: Any) -> Any:
        """The legacy return shape: `.multi_face_landmarks[0].landmark`."""
        mp = self._mp
        # SRGB means uint8 RGB, which is what OpenCvFrameSource already hands
        # out. Contiguity is required by the C++ boundary and a cropped or
        # sliced array is not.
        image = mp.Image(image_format=mp.ImageFormat.SRGB,
                         data=self._np.ascontiguousarray(frame, dtype="uint8"))
        ms = max(int(time.perf_counter() * 1000), self._last_ms + 1)
        self._last_ms = ms
        result = self._landmarker.detect_for_video(image, ms)
        faces = getattr(result, "face_landmarks", None) or []
        if not faces:
            return type("R", (), {"multi_face_landmarks": None})()
        # Tasks returns a plain list of landmarks per face; the legacy API
        # wrapped it in an object with a `.landmark` attribute.
        return type("R", (), {
            "multi_face_landmarks": [type("F", (), {"landmark": faces[0]})()]
        })()


class FaceMeshLandmarker:
    """MediaPipe Face Mesh, wrapped to return named landmarks.

    Constructed lazily and injectable, for the same reason `FaceLocator` is:
    importing MediaPipe costs a heavy dependency that a headband-only
    deployment must not need, and a test must be able to drive the pipeline
    without one.

    Only the **construction** of a real Face Mesh is untestable here, needing
    MediaPipe and a camera; `locate()` is exercised through an injected mesh,
    which is the point of the injection. An earlier version of this docstring
    said the whole class was untested, which overstated the gap and would have
    discouraged anyone from covering the part that can be.
    """

    def __init__(self, mesh: Any | None = None,
                 model_path: str | None = None) -> None:
        # Reasons already reported, so a wrong index table does not become a
        # per-frame error flood. At the capture loop's rate that is tens of
        # identical lines a second, which buries the one thing worth reading
        # and makes the log useless for whatever else is wrong.
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

        Returns empty rather than raising on a frame with no face: no face is
        an ordinary outcome several times a minute, and the caller already
        counts it.

        **`last_reason` says which kind of empty**, because `{}` covers two
        unrelated events: the detector saw no face, or it saw one and
        `check_topology` refused the landmark set. Rendering both as "no face"
        sends someone to check their lighting when the answer is that a named
        point is somewhere a face cannot put it -- and at strong yaw the second
        happens routinely, since `nose_outside_eyes` becomes true near profile.
        `None` means a face was returned.
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
            # Once per reason, not once per frame. This means the index table
            # is wrong rather than that the student moved, so it has to be
            # visible -- but it is a standing condition, not an event, and
            # repeating it every frame buries it in its own copies. The count
            # is kept so the repetition is still measurable.
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

        Exposed because the log is now deduplicated: without a count, "wrong
        on one frame" and "wrong on every frame since the session began" read
        identically, and they are very different facts.
        """
        return self._rejections
