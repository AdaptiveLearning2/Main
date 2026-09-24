"""Head pose and gaze direction from face landmarks -- geometry only; it does not score attention.

Pure numpy (no OpenCV) so it is testable in CI. Weak-perspective fit rather than
solvePnP, which needs camera intrinsics we don't have. Image coords are x right,
y down, not mirrored. yaw > 0: toward image right; pitch > 0: up; roll > 0: subject's
right eye rises. Yaw is measurable only within +/-90 degrees (`implausible_pose`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# Named landmarks a caller maps its detector onto; eight points spanning all three axes.
POSE_LANDMARKS = (
    "left_eye_outer", "left_eye_inner", "right_eye_inner", "right_eye_outer",
    "nose_tip", "mouth_left", "mouth_right", "chin",
)

# Canonical adult mean face (~mm); scale is solved for. The subject's left MUST sit at
# positive x: a rotation can't reflect, so the wrong handedness fits no pose at all.
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

# Six, not the arithmetic four: four near-coplanar eye corners leave one rotation unconstrained.
MIN_POSE_POINTS = 6

# Min ratio of smallest to largest singular value of the centred canonical subset.
MIN_CONDITION = 0.02

# Pitch or roll past this means the Euler recovery took the wrong branch. Radians.
MAX_PLAUSIBLE_TILT = math.radians(90.0)


@dataclass
class HeadPose:
    """Rotation of the head relative to facing the camera, in degrees."""
    yaw: float | None
    pitch: float | None
    roll: float | None
    # Landmarks the fit used: six points and eight are not the same measurement.
    landmarks_used: int = 0
    rejected_by: str | None = None

    @property
    def ok(self) -> bool:
        return self.yaw is not None


@dataclass
class Gaze:
    """Eye direction as an offset within the eye opening, -1..1 per axis.

    Not an angle (needs eyeball radius and camera geometry). x > 0 right, y > 0 down.
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

    Weak perspective: observed ≈ s · R[:2] · canonical + t; row 3 is the cross product.
    None when the *canonical* subset is near degenerate (a flattened face is still valid).
    """
    canonical = canonical - canonical.mean(axis=0)
    observed = observed - observed.mean(axis=0)

    singular = np.linalg.svd(canonical, compute_uv=False)
    if singular[0] <= 0 or singular[-1] / singular[0] < MIN_CONDITION:
        return None

    # lstsq, not normal equations, which square the condition number.
    mapping, *_ = np.linalg.lstsq(canonical, observed, rcond=None)
    mapping = mapping.T                                    # (2, 3)

    r0 = mapping[0]
    n0 = np.linalg.norm(r0)
    if n0 == 0:
        return None
    r0 = r0 / n0

    # Gram-Schmidt: non-orthonormal rows give quietly wrong Euler angles.
    r1 = mapping[1] - np.dot(mapping[1], r0) * r0
    n1 = np.linalg.norm(r1)
    if n1 == 0:
        return None
    r1 = r1 / n1

    return np.vstack([r0, r1, np.cross(r0, r1)])


def head_pose(landmarks: dict[str, tuple[float, float]]) -> HeadPose:
    """Yaw, pitch and roll in degrees from named 2D landmarks.

    Missing names are skipped, never treated as zero (the origin would drag the fit).
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

    # ZYX Euler extraction for R = Rz(roll) · Ry(yaw) · Rx(pitch).
    cos_yaw = math.hypot(rotation[2, 1], rotation[2, 2])
    if cos_yaw < 0.0001:
        # Gimbal lock at yaw = ±90°: pitch and roll can't be separated.
        return HeadPose(None, None, None, len(names), "gimbal_lock")

    yaw = math.atan2(-rotation[2, 0], cos_yaw)
    pitch = math.atan2(rotation[2, 1], rotation[2, 2])
    roll = math.atan2(rotation[1, 0], rotation[0, 0])

    # (yaw, pitch, roll) and (180°−yaw, pitch+180°, roll+180°) are one rotation, and yaw
    # always lands in (−90°, 90°). |pitch| or |roll| past 90° means the other branch: refuse.
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
    # A closed eye has no opening to measure within.
    if half_width <= 0 or half_height <= 0:
        return None

    return (float((arr[4][0] - centre[0]) / half_width),
            float((arr[4][1] - (arr[2][1] + arr[3][1]) / 2.0) / half_height))


def gaze(landmarks: dict[str, tuple[float, float]]) -> Gaze:
    """Mean iris offset across whichever eyes are measurable (`eyes_used` says how many).

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
    # Clamped, not rejected: slight overshoot is detector wobble, not failure.
    return Gaze(round(float(np.clip(mean[0], -1.0, 1.0)), 3),
                round(float(np.clip(mean[1], -1.0, 1.0)), 3),
                len(offsets))
