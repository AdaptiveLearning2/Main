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

import logging
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
# Confirming it needs the manual camera check: sit square on, look hard left,
# and confirm `gaze.x` goes negative.
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

    def __init__(self, mesh: Any | None = None) -> None:
        # Reasons already reported, so a wrong index table does not become a
        # per-frame error flood. At the capture loop's rate that is tens of
        # identical lines a second, which buries the one thing worth reading
        # and makes the log useless for whatever else is wrong.
        self._reported: set[str] = set()
        self._rejections = 0
        if mesh is not None:
            self._mesh = mesh
            return
        import mediapipe as mp                             # noqa: PLC0415

        self._mesh = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=False,
            max_num_faces=1,
            # The iris landmarks (468-477) only exist with this on, and gaze is
            # the whole point.
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

    def locate(self, frame: Any, width: int, height: int) -> dict:
        """Named landmarks for the first face found, or `{}`.

        Returns empty rather than raising on a frame with no face: no face is
        an ordinary outcome several times a minute, and the caller already
        counts it.
        """
        result = self._mesh.process(frame)
        faces = getattr(result, "multi_face_landmarks", None)
        if not faces:
            return {}

        named = named_landmarks(faces[0].landmark, width, height)
        wrong = check_topology(named)
        if wrong is not None:
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
        return named

    @property
    def rejections(self) -> int:
        """How many frames the topology check has refused.

        Exposed because the log is now deduplicated: without a count, "wrong
        on one frame" and "wrong on every frame since the session began" read
        identically, and they are very different facts.
        """
        return self._rejections
