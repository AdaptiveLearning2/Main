"""Head pose and gaze direction from face landmarks (Phase 11, geometry half).

This is the measurable half. It turns a set of named 2D landmarks into a head
rotation and an eye-gaze offset, and it stops there -- **it does not score
attention**. That separation is deliberate and is the plan's own guard: the
geometry has a right answer and can be checked against one, while the inference
from "looking 20 degrees left" to "not attending" is a judgement about a child,
least valid for exactly this product's users. Keeping them apart means the
judgement can be revised without re-deriving anything, and that no percentage
reaches a parent before it has been measured against a reference.

**Pure numpy, and no landmark model.** `face_roi.FaceLocator` needs OpenCV,
which is an optional extra deliberately absent from CI, so anything importing it
cannot be tested there. The arithmetic here is the part that can be wrong in a
way a test would catch, so it is kept free of that dependency: a caller supplies
landmarks in the contract below, from whatever detector it has. The detector is
a separate change (MediaPipe Face Mesh is the candidate -- Apache 2.0, models
downloadable without an agreement, which is the constraint that blocked
RhythmMamba, since the sidecar installs on a student's laptop).

Why not `cv2.solvePnP`, which the plan suggested
------------------------------------------------
solvePnP needs a camera matrix, and we do not have one. A student's webcam
intrinsics are unknown and vary by device, so the focal length would have to be
guessed -- and a wrong focal length does not produce an obvious failure, it
produces a systematically wrong pose that still looks like a face turning.

An orthographic (weak-perspective) fit needs no intrinsics at all: it recovers
rotation and scale from the landmark correspondences alone. What it gives up is
perspective, so it degrades at close range and large angles, where a real
projection would foreshorten more than a linear one predicts. For a child at a
desk, roughly arm's length from a laptop, that is the better trade -- a bounded
approximation beats an unbounded guess. If intrinsics ever become available
(a calibration step, or a known device), solvePnP becomes the better answer.

Sign conventions
----------------
Image coordinates: **x right, y down**, as every frame array in this codebase
is indexed. The canonical model is a face looking straight at the camera, with
+z out of the face toward the lens.

- **yaw** > 0: the subject's face turns toward the right-hand side of the image
- **pitch** > 0: the face tilts downward (chin toward chest)
- **roll** > 0: the head tips so the subject's right eye rises in the image

These are asserted by round-trip in the tests -- a known rotation is applied to
the canonical model, projected, and recovered -- rather than stated and hoped
for, because a sign error here is invisible in every aggregate and only shows up
as a mirrored gaze on somebody's dashboard.

**Yaw is measurable only within +/-90 degrees.** Beyond that the decomposition
returns the other branch of a two-fold ambiguity that no rotation matrix can
resolve on its own, so `head_pose` refuses with `implausible_pose` rather than
reporting a mirrored angle. A face turned that far has most of these landmarks
occluded anyway. See the check in `head_pose`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# The landmarks this module needs, by name. A caller maps its detector's
# indices onto these; nothing here knows about MediaPipe's 468 points or any
# other model's numbering, which is what keeps the detector swappable.
#
# Eight points, chosen to be the ones every landmark model exposes and to span
# the face in all three axes -- a set clustered on one plane gives a rotation
# that is well determined about one axis and nearly free about the others.
POSE_LANDMARKS = (
    "left_eye_outer", "left_eye_inner", "right_eye_inner", "right_eye_outer",
    "nose_tip", "mouth_left", "mouth_right", "chin",
)

# A canonical face, facing the camera, in arbitrary units (roughly millimetres
# on an adult). Only the *shape* matters: scale is solved for, so a child's
# smaller face fits the same model. Left and right are the **subject's**, so
# `left_eye_outer` sits at negative x when the subject faces the lens.
#
# These are the standard correspondences used with the 3D morphable-model mean
# face; they are not measured from this product's users, and that is a known
# approximation -- an adult mean face fitted to a child's proportions biases
# pose slightly. It is bounded and systematic rather than noisy, and correcting
# it needs the same reference recording the attention score needs.
CANONICAL_FACE = {
    "left_eye_outer":  (-45.0, -34.0, -12.0),
    "left_eye_inner":  (-15.0, -34.0,  -6.0),
    "right_eye_inner": (15.0, -34.0,  -6.0),
    "right_eye_outer": (45.0, -34.0, -12.0),
    "nose_tip":        (0.0,   0.0,  22.0),
    "mouth_left":      (-27.0,  38.0,  -3.0),
    "mouth_right":     (27.0,  38.0,  -3.0),
    "chin":            (0.0,  75.0,  -8.0),
}

# Below this many usable named points the fit is under-determined. Four is the
# arithmetic minimum for a 2x3 linear map; six is required here because four
# coplanar points -- which the eye corners very nearly are -- leave the rotation
# about the line through them almost unconstrained while still producing a
# confident-looking answer. That is the same failure shape as a confident wrong
# heart rate, and it is cheaper to refuse.
MIN_POSE_POINTS = 6

# How near to degenerate the observed points may be before the fit is refused.
# The ratio of the smallest to largest singular value of the centred canonical
# subset: near zero means the chosen points are collinear or coplanar in the
# projection, so at least one axis of rotation is not observable.
MIN_CONDITION = 0.02

# Beyond this many degrees of pitch or roll, the Euler recovery has picked the
# wrong branch of a two-fold ambiguity rather than measured a real head — a neck
# does not bend that far. See the check in `head_pose` for why an assumption of
# this kind cannot be avoided, only declared. Radians, to compare against the
# atan2 results directly.
MAX_PLAUSIBLE_TILT = math.radians(90.0)


@dataclass
class HeadPose:
    """Rotation of the head relative to facing the camera, in degrees."""
    yaw: float | None
    pitch: float | None
    roll: float | None
    # Fraction of requested landmarks that were usable. Reported rather than
    # folded into a threshold, so a caller can tell "measured from six points"
    # from "measured from eight" -- they are not the same measurement.
    landmarks_used: int = 0
    rejected_by: str | None = None

    @property
    def ok(self) -> bool:
        return self.yaw is not None


@dataclass
class Gaze:
    """Eye direction as an offset within the eye opening, -1..1 per axis.

    Not an angle. Converting to one needs eye-ball radius and camera geometry,
    neither of which is known here, and the offset is what a downstream score
    would use anyway. `x` > 0 is toward the right of the image, `y` > 0 down --
    the same convention as the pose above.
    """
    x: float | None
    y: float | None
    eyes_used: int = 0
    rejected_by: str | None = None

    @property
    def ok(self) -> bool:
        return self.x is not None


def _rotation_from_correspondences(canonical: np.ndarray,
                                   observed: np.ndarray) -> np.ndarray | None:
    """Rotation matrix from 3D model points to their orthographic projection.

    Weak perspective: observed ≈ s · R[:2] · canonical + t. Centring removes t,
    least squares gives the 2x3 map s·R[:2], and the third row of R is the cross
    product of the first two -- it is not observable from a projection and does
    not need to be, since it is determined by the other two being orthonormal.

    Returns None when the observed points are too near degenerate to determine
    it. The check is on the *canonical* subset's conditioning rather than the
    observed one, because a face turned far enough to flatten in the image is
    still a valid measurement; a badly chosen set of points is not.
    """
    canonical = canonical - canonical.mean(axis=0)
    observed = observed - observed.mean(axis=0)

    singular = np.linalg.svd(canonical, compute_uv=False)
    if singular[0] <= 0 or singular[-1] / singular[0] < MIN_CONDITION:
        return None

    # Least-squares 2x3 map. lstsq rather than an explicit pseudo-inverse: the
    # normal equations square the condition number, which is exactly the wrong
    # thing to do to a nearly-degenerate system we are already worried about.
    mapping, *_ = np.linalg.lstsq(canonical, observed, rcond=None)
    mapping = mapping.T                                    # (2, 3)

    r0 = mapping[0]
    n0 = np.linalg.norm(r0)
    if n0 == 0:
        return None
    r0 = r0 / n0

    # Gram-Schmidt rather than normalising row 1 independently: the two rows
    # come from a least-squares fit and are not exactly orthogonal, and a
    # rotation matrix whose rows are not orthonormal yields Euler angles that
    # are quietly wrong rather than obviously so.
    r1 = mapping[1] - np.dot(mapping[1], r0) * r0
    n1 = np.linalg.norm(r1)
    if n1 == 0:
        return None
    r1 = r1 / n1

    return np.vstack([r0, r1, np.cross(r0, r1)])


def head_pose(landmarks: dict[str, tuple[float, float]]) -> HeadPose:
    """Yaw, pitch and roll in degrees from named 2D landmarks.

    `landmarks` maps names in `POSE_LANDMARKS` to image coordinates. Missing
    names are skipped rather than treated as zero -- a landmark the detector did
    not find is absent, and placing it at the origin would drag the fit toward
    the top-left corner while still returning a confident-looking pose.
    """
    names = [n for n in POSE_LANDMARKS
             if n in landmarks and landmarks[n] is not None]
    if len(names) < MIN_POSE_POINTS:
        return HeadPose(None, None, None, len(names), "too_few_landmarks")

    canonical = np.array([CANONICAL_FACE[n] for n in names], dtype=float)
    observed = np.array([landmarks[n] for n in names], dtype=float)
    if not np.isfinite(observed).all():
        return HeadPose(None, None, None, len(names), "bad_landmarks")

    rotation = _rotation_from_correspondences(canonical, observed)
    if rotation is None:
        return HeadPose(None, None, None, len(names), "degenerate")

    # ZYX Euler extraction, for R = Rz(roll) · Ry(yaw) · Rx(pitch). Writing that
    # product out gives R[2,0] = -sin(yaw), R[2,1] = cos(yaw)·sin(pitch),
    # R[2,2] = cos(yaw)·cos(pitch), R[0,0] = cos(roll)·cos(yaw) and
    # R[1,0] = sin(roll)·cos(yaw) -- which is where each element below comes
    # from. Derived rather than recalled: the first version of this used the
    # right magnitudes with three inverted signs, and every one of them was
    # caught by the round-trip tests and by nothing else.
    cos_yaw = math.hypot(rotation[2, 1], rotation[2, 2])
    if cos_yaw < 0.0001:
        # Gimbal lock at yaw = ±90°: with the face fully in profile, pitch and
        # roll are the same rotation and cannot be separated. Refusing beats
        # splitting one angle arbitrarily between two columns a reader would
        # compare.
        return HeadPose(None, None, None, len(names), "gimbal_lock")

    yaw = math.atan2(-rotation[2, 0], cos_yaw)
    pitch = math.atan2(rotation[2, 1], rotation[2, 2])
    roll = math.atan2(rotation[1, 0], rotation[0, 0])

    # The two-fold ambiguity, which no rotation matrix can resolve on its own:
    # (yaw, pitch, roll) and (180°−yaw, pitch+180°, roll+180°) are the *same*
    # rotation. `cos_yaw` is a hypot and therefore never negative, so the yaw
    # above is always in (−90°, 90°) — and a face genuinely turned further than
    # that comes back on the wrong branch, silently. Measured: a true yaw of
    # 120° reports 60°, and a true (91°, 15°, 10°) reports (89°, −165°, −170°),
    # so a 2° change in the truth swings pitch and roll by 180° with nothing
    # marked wrong.
    #
    # Some external assumption is unavoidable here; what matters is saying so.
    # A neck does not pitch or roll past a right angle, so |pitch| or |roll|
    # beyond 90° means the other branch was the real one. Refused rather than
    # corrected: the arithmetic could be flipped back, but a face turned more
    # than 90° has most of these landmarks occluded, so the *input* to the fit
    # is unreliable whichever branch is chosen — and this codebase would rather
    # lose the window than publish a confident wrong angle.
    if abs(pitch) > MAX_PLAUSIBLE_TILT or abs(roll) > MAX_PLAUSIBLE_TILT:
        return HeadPose(None, None, None, len(names), "implausible_pose")

    return HeadPose(round(math.degrees(yaw), 2),
                    round(math.degrees(pitch), 2),
                    round(math.degrees(roll), 2),
                    len(names))


def _eye_offset(outer, inner, upper, lower, iris) -> tuple[float, float] | None:
    """Iris position within one eye opening, -1..1 on each axis."""
    pts = (outer, inner, upper, lower, iris)
    if any(p is None for p in pts):
        return None
    arr = np.array(pts, dtype=float)
    if not np.isfinite(arr).all():
        return None

    centre = (arr[0] + arr[1]) / 2.0
    half_width = abs(arr[1][0] - arr[0][0]) / 2.0
    half_height = abs(arr[3][1] - arr[2][1]) / 2.0
    # A closed or nearly closed eye has no opening to measure a position
    # within, and dividing by it produces a large offset from a blink.
    if half_width <= 0 or half_height <= 0:
        return None

    return (float((arr[4][0] - centre[0]) / half_width),
            float((arr[4][1] - (arr[2][1] + arr[3][1]) / 2.0) / half_height))


def gaze(landmarks: dict[str, tuple[float, float]]) -> Gaze:
    """Mean iris offset across whichever eyes are measurable.

    Both eyes when both are available, one when only one is -- a hand, a hair
    or a head turn regularly hides one, and refusing the whole reading for that
    would discard a measurable eye. `eyes_used` says which case it was, because
    a one-eyed estimate is noisier and a caller may want to weight it.

    Expects `{left,right}_eye_{outer,inner,upper,lower}` and `{left,right}_iris`.
    """
    offsets = []
    for side in ("left", "right"):
        offset = _eye_offset(
            landmarks.get(f"{side}_eye_outer"),
            landmarks.get(f"{side}_eye_inner"),
            landmarks.get(f"{side}_eye_upper"),
            landmarks.get(f"{side}_eye_lower"),
            landmarks.get(f"{side}_iris"),
        )
        if offset is not None:
            offsets.append(offset)

    if not offsets:
        return Gaze(None, None, 0, "no_eye")

    mean = np.mean(np.array(offsets, dtype=float), axis=0)
    # Clamped, not rejected: an iris tracked slightly outside the corner
    # landmarks is a normal detector wobble at the extremes of a real look, not
    # a failure, and letting it run past 1 would make a hard look sideways
    # arbitrarily large.
    return Gaze(round(float(np.clip(mean[0], -1.0, 1.0)), 3),
                round(float(np.clip(mean[1], -1.0, 1.0)), 3),
                len(offsets))
