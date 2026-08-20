"""Head pose and gaze direction from face landmarks.

This is the measurable half. It turns named 2D landmarks into a head rotation
and an eye-gaze offset, and stops there -- **it does not score attention**.
The geometry has a right answer that can be checked; the inference from
"looking 20 degrees left" to "not attending" is a judgement about a child, and
that judgement should be revisable without re-deriving the geometry, and
should never reach a parent as a percentage before being measured against a
reference.

**Pure numpy, no landmark model.** `face_roi.FaceLocator` needs OpenCV, which
is absent from CI, so anything importing it can't be tested there. This
arithmetic stays free of that dependency: a caller supplies landmarks in the
contract below, from whatever detector it has.

Why not `cv2.solvePnP`: it needs a camera matrix we don't have. A student's
webcam intrinsics vary by device, so the focal length would have to be
guessed, and a wrong focal length doesn't fail obviously -- it produces a
systematically wrong pose that still looks like a face turning. An
orthographic (weak-perspective) fit needs no intrinsics: it recovers rotation
and scale from the landmark correspondences alone, at the cost of accuracy at
close range and large angles. For a child at arm's length from a laptop,
that's the better trade. solvePnP becomes the better answer if intrinsics
ever become available.

Sign conventions: image coordinates are **x right, y down**, matching every
frame array in this codebase. The canonical model faces the camera with +z
toward the lens. The frame is **not mirrored**, so a subject facing the lens
has their own left on the image right -- which is why `CANONICAL_FACE` puts
the subject's left at positive x (see the note there; the fit solves for a
rotation, and a rotation can't reflect, so the opposite handedness can't be
fitted at all).

- **yaw** > 0: face turns toward the right of the image (the subject's own left)
- **pitch** > 0: face points upward (chin away from chest)
- **roll** > 0: head tips so the subject's right eye rises in the image

Round-trip tests (apply a known rotation, project, recover it) are weaker
than they look: they're self-consistent under a mirrored model too, since
they pin the decomposition against itself rather than against a camera.
`test_the_model_handedness_matches_a_real_frame` and its neighbours instead
construct a frame from the image convention, which is what actually pins the
sign conventions above.

**Yaw is measurable only within +/-90 degrees.** Beyond that the
decomposition returns the other branch of a two-fold ambiguity no rotation
matrix can resolve, so `head_pose` refuses with `implausible_pose` rather
than report a mirrored angle -- a face turned that far has most landmarks
occluded anyway. See the check in `head_pose`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# The landmarks this module needs, by name. A caller maps its detector's
# indices onto these, which keeps the detector swappable -- nothing here knows
# about MediaPipe's 468 points or any other model's numbering.
#
# Eight points, chosen because every landmark model exposes them and they span
# the face in all three axes -- points clustered on one plane would leave
# rotation about one axis nearly unconstrained.
POSE_LANDMARKS = (
    "left_eye_outer", "left_eye_inner", "right_eye_inner", "right_eye_outer",
    "nose_tip", "mouth_left", "mouth_right", "chin",
)

# A canonical face, facing the camera, in arbitrary units (roughly millimetres
# on an adult). Only the *shape* matters -- scale is solved for, so a child's
# smaller face fits the same model.
#
# **Left and right are the subject's, and the subject's left sits at POSITIVE
# x.** That's required, not a free choice: the fit solves for a rotation, and a
# rotation can't reflect, so if the model's handedness disagreed with the
# frame's, no pose would exist and every frame would be refused. In a
# non-mirrored frame (what OpenCV hands over), a subject facing the lens has
# their own left on the image right, at larger x, so the model must too.
#
# These are the standard correspondences used with the 3D morphable-model mean
# face. They are not measured from this product's users -- an adult mean face
# fitted to a child's proportions biases pose slightly, a bounded and
# systematic error rather than noise. Correcting it needs a reference
# recording of this product's users.
CANONICAL_FACE = {
    "left_eye_outer":  (45.0, -34.0, -12.0),
    "left_eye_inner":  (15.0, -34.0,  -6.0),
    "right_eye_inner": (-15.0, -34.0,  -6.0),
    "right_eye_outer": (-45.0, -34.0, -12.0),
    "nose_tip":        (0.0,   0.0,  22.0),
    "mouth_left":      (27.0,  38.0,  -3.0),
    "mouth_right":     (-27.0,  38.0,  -3.0),
    "chin":            (0.0,  75.0,  -8.0),
}

# Below this many usable named points the fit is under-determined. Four is the
# arithmetic minimum for a 2x3 linear map; six is required because four
# coplanar points (which the eye corners very nearly are) leave rotation about
# the line through them almost unconstrained while still looking confident --
# cheaper to refuse than to risk that.
MIN_POSE_POINTS = 6

# How near to degenerate the observed points may be before the fit is refused.
# Ratio of smallest to largest singular value of the centred canonical subset:
# near zero means the chosen points are collinear or coplanar in the
# projection, so at least one axis of rotation isn't observable.
MIN_CONDITION = 0.02

# Beyond this many degrees of pitch or roll, the Euler recovery has picked the
# wrong branch of a two-fold ambiguity rather than measured a real head -- a
# neck doesn't bend that far. Radians, to compare against atan2 directly.
MAX_PLAUSIBLE_TILT = math.radians(90.0)


@dataclass
class HeadPose:
    """Rotation of the head relative to facing the camera, in degrees."""
    yaw: float | None
    pitch: float | None
    roll: float | None
    # Fraction of requested landmarks that were usable. Reported rather than
    # folded into a threshold, so a caller can tell "measured from six points"
    # from "measured from eight" -- not the same measurement.
    landmarks_used: int = 0
    rejected_by: str | None = None

    @property
    def ok(self) -> bool:
        return self.yaw is not None


@dataclass
class Gaze:
    """Eye direction as an offset within the eye opening, -1..1 per axis.

    Not an angle -- converting to one needs eyeball radius and camera
    geometry, neither known here, and the offset is what a downstream score
    would use anyway. `x` > 0 is toward the right of the image, `y` > 0 down,
    matching the pose convention above.
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
    least squares gives the 2x3 map s·R[:2], and the third row of R is the
    cross product of the first two -- not observable from a projection, but
    determined anyway since the other two rows are orthonormal.

    Returns None when the observed points are too near degenerate. Checked
    against the *canonical* subset's conditioning, not the observed one: a
    face turned far enough to flatten in the image is still a valid
    measurement, but a badly chosen set of points is not.
    """
    canonical = canonical - canonical.mean(axis=0)
    observed = observed - observed.mean(axis=0)

    singular = np.linalg.svd(canonical, compute_uv=False)
    if singular[0] <= 0 or singular[-1] / singular[0] < MIN_CONDITION:
        return None

    # lstsq rather than an explicit pseudo-inverse: the normal equations square
    # the condition number, which is the wrong thing to do to a system already
    # nearly degenerate.
    mapping, *_ = np.linalg.lstsq(canonical, observed, rcond=None)
    mapping = mapping.T                                    # (2, 3)

    r0 = mapping[0]
    n0 = np.linalg.norm(r0)
    if n0 == 0:
        return None
    r0 = r0 / n0

    # Gram-Schmidt rather than normalising row 1 independently: the two rows
    # come from a least-squares fit and aren't exactly orthogonal, and
    # non-orthonormal rows yield Euler angles that are quietly wrong rather
    # than obviously so.
    r1 = mapping[1] - np.dot(mapping[1], r0) * r0
    n1 = np.linalg.norm(r1)
    if n1 == 0:
        return None
    r1 = r1 / n1

    return np.vstack([r0, r1, np.cross(r0, r1)])


def head_pose(landmarks: dict[str, tuple[float, float]]) -> HeadPose:
    """Yaw, pitch and roll in degrees from named 2D landmarks.

    `landmarks` maps names in `POSE_LANDMARKS` to image coordinates. Missing
    names are skipped rather than treated as zero -- placing a missing
    landmark at the origin would drag the fit toward the top-left corner
    while still returning a confident-looking pose.
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

    # ZYX Euler extraction, for R = Rz(roll) · Ry(yaw) · Rx(pitch). Writing
    # that product out gives R[2,0] = -sin(yaw), R[2,1] = cos(yaw)·sin(pitch),
    # R[2,2] = cos(yaw)·cos(pitch), R[0,0] = cos(roll)·cos(yaw) and
    # R[1,0] = sin(roll)·cos(yaw), which is where each element below comes
    # from.
    cos_yaw = math.hypot(rotation[2, 1], rotation[2, 2])
    if cos_yaw < 0.0001:
        # Gimbal lock at yaw = ±90°: face fully in profile, so pitch and roll
        # are the same rotation and can't be separated. Refuse rather than
        # split one angle arbitrarily between two columns a reader would compare.
        return HeadPose(None, None, None, len(names), "gimbal_lock")

    yaw = math.atan2(-rotation[2, 0], cos_yaw)
    pitch = math.atan2(rotation[2, 1], rotation[2, 2])
    roll = math.atan2(rotation[1, 0], rotation[0, 0])

    # Two-fold ambiguity no rotation matrix can resolve on its own:
    # (yaw, pitch, roll) and (180°−yaw, pitch+180°, roll+180°) are the same
    # rotation. `cos_yaw` is a hypot, never negative, so yaw above always lands
    # in (−90°, 90°) -- a face genuinely turned further comes back on the wrong
    # branch silently. Measured: a true yaw of 120° reports 60°, and a true
    # (91°, 15°, 10°) reports (89°, −165°, −170°).
    #
    # A neck doesn't pitch or roll past a right angle, so |pitch| or |roll|
    # beyond 90° means the other branch was real. Refused rather than
    # corrected: a face turned that far has most landmarks occluded, so the
    # fit's input is unreliable whichever branch is chosen -- better to lose
    # the window than publish a confident wrong angle.
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
    # within, and dividing by a near-zero one would produce a huge offset.
    if half_width <= 0 or half_height <= 0:
        return None

    return (float((arr[4][0] - centre[0]) / half_width),
            float((arr[4][1] - (arr[2][1] + arr[3][1]) / 2.0) / half_height))


def gaze(landmarks: dict[str, tuple[float, float]]) -> Gaze:
    """Mean iris offset across whichever eyes are measurable.

    Both eyes when available, one when only one is -- a hand, hair or head
    turn regularly hides one eye, and refusing the whole reading would
    discard a perfectly measurable one. `eyes_used` says which case applied,
    since a one-eyed estimate is noisier.

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
    # landmarks is normal detector wobble at the extremes of a real look, not
    # a failure.
    return Gaze(round(float(np.clip(mean[0], -1.0, 1.0)), 3),
                round(float(np.clip(mean[1], -1.0, 1.0)), 3),
                len(offsets))
