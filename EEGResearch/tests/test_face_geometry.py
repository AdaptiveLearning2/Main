"""Head pose and gaze from landmarks, without a landmark model.

Everything here is plain numpy on synthetic input, like `test_face_roi`. There
is no detector involved: the module takes named landmarks and returns geometry,
which is the half that can be wrong in a way a test catches.

**Poses are round-tripped, not asserted from a table.** A known rotation is
applied to the canonical face, projected orthographically, and recovered — so
the sign conventions are derived from the definition rather than copied from a
docstring. A sign error is invisible in every aggregate and surfaces only as a
mirrored gaze on somebody's dashboard, which is exactly the class of mistake a
hand-written expectation reproduces instead of catching.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from src.app.services.face_geometry import (
    CANONICAL_FACE,
    MIN_POSE_POINTS,
    POSE_LANDMARKS,
    gaze,
    head_pose,
)


def _rotation(yaw_deg: float = 0.0, pitch_deg: float = 0.0,
              roll_deg: float = 0.0) -> np.ndarray:
    """R = Rz(roll) · Ry(yaw) · Rx(pitch), in the module's image convention."""
    y, p, r = (math.radians(a) for a in (yaw_deg, pitch_deg, roll_deg))
    rx = np.array([[1, 0, 0],
                   [0, math.cos(p), -math.sin(p)],
                   [0, math.sin(p), math.cos(p)]])
    ry = np.array([[math.cos(y), 0, math.sin(y)],
                   [0, 1, 0],
                   [-math.sin(y), 0, math.cos(y)]])
    rz = np.array([[math.cos(r), -math.sin(r), 0],
                   [math.sin(r), math.cos(r), 0],
                   [0, 0, 1]])
    return rz @ ry @ rx


def _project(rotation: np.ndarray, scale: float = 2.0,
             offset=(320.0, 240.0), names=POSE_LANDMARKS) -> dict:
    """The canonical face rotated, scaled, shifted, and flattened to the image.

    Scale and offset are non-trivial on purpose: a fit that silently assumed a
    centred, unit-scaled face would pass against (1.0, (0, 0)) and fail on any
    real frame.
    """
    out = {}
    for name in names:
        point = rotation @ np.array(CANONICAL_FACE[name], dtype=float)
        out[name] = (point[0] * scale + offset[0], point[1] * scale + offset[1])
    return out


# ── pose round-trips ────────────────────────────────────────────────────────

def test_a_face_looking_at_the_camera_reads_as_square_on():
    pose = head_pose(_project(_rotation()))

    assert pose.ok
    assert pose.yaw == pytest.approx(0.0, abs=0.5)
    assert pose.pitch == pytest.approx(0.0, abs=0.5)
    assert pose.roll == pytest.approx(0.0, abs=0.5)
    assert pose.landmarks_used == len(POSE_LANDMARKS)


@pytest.mark.parametrize("yaw", [-30.0, -15.0, 15.0, 30.0])
def test_yaw_is_recovered_with_its_sign(yaw):
    pose = head_pose(_project(_rotation(yaw_deg=yaw)))

    assert pose.yaw == pytest.approx(yaw, abs=1.5)
    assert pose.pitch == pytest.approx(0.0, abs=1.5)
    assert pose.roll == pytest.approx(0.0, abs=1.5)


@pytest.mark.parametrize("pitch", [-20.0, -10.0, 10.0, 20.0])
def test_pitch_is_recovered_with_its_sign(pitch):
    pose = head_pose(_project(_rotation(pitch_deg=pitch)))

    assert pose.pitch == pytest.approx(pitch, abs=1.5)
    assert pose.yaw == pytest.approx(0.0, abs=1.5)


@pytest.mark.parametrize("roll", [-25.0, -10.0, 10.0, 25.0])
def test_roll_is_recovered_with_its_sign(roll):
    pose = head_pose(_project(_rotation(roll_deg=roll)))

    assert pose.roll == pytest.approx(roll, abs=1.5)
    assert pose.yaw == pytest.approx(0.0, abs=1.5)


def test_three_rotations_at_once_are_all_recovered():
    """Axes are not independent, so recovering each alone proves less than it
    looks: an extraction that mixed two of them would still pass every
    single-axis case above."""
    pose = head_pose(_project(_rotation(yaw_deg=20.0, pitch_deg=-12.0, roll_deg=8.0)))

    assert pose.yaw == pytest.approx(20.0, abs=2.0)
    assert pose.pitch == pytest.approx(-12.0, abs=2.0)
    assert pose.roll == pytest.approx(8.0, abs=2.0)


def test_pose_is_independent_of_where_the_face_is_in_the_frame():
    """A student sitting off to one side is not a student turning their head.
    Centring is what separates them, and without it every off-centre face reads
    as rotated."""
    rotation = _rotation(yaw_deg=18.0)
    near = head_pose(_project(rotation, offset=(100.0, 90.0)))
    far = head_pose(_project(rotation, offset=(540.0, 400.0)))

    assert near.yaw == pytest.approx(far.yaw, abs=0.1)


def test_pose_is_independent_of_how_close_the_face_is():
    """Scale is solved for, so a child's smaller face and an adult leaning in
    give the same angles from the same pose."""
    rotation = _rotation(pitch_deg=15.0)
    small = head_pose(_project(rotation, scale=1.0))
    large = head_pose(_project(rotation, scale=4.0))

    assert small.pitch == pytest.approx(large.pitch, abs=0.1)


# ── refusals ────────────────────────────────────────────────────────────────

def test_too_few_landmarks_refuses_rather_than_guessing():
    """Named, not silent. A pose from four coplanar eye corners is
    under-determined about the axis through them while still looking
    confident — the same shape as a confident wrong heart rate."""
    partial = _project(_rotation(), names=POSE_LANDMARKS[:MIN_POSE_POINTS - 1])

    pose = head_pose(partial)

    assert not pose.ok
    assert pose.rejected_by == "too_few_landmarks"
    assert pose.yaw is None and pose.pitch is None and pose.roll is None


def test_a_missing_landmark_is_skipped_not_placed_at_the_origin():
    """Absent is not zero. A landmark dropped to (0, 0) drags the fit toward
    the top-left corner and still returns an answer."""
    full = _project(_rotation(yaw_deg=15.0))
    without = {k: v for k, v in full.items() if k != "chin"}

    pose = head_pose(without)

    assert pose.ok
    assert pose.landmarks_used == len(POSE_LANDMARKS) - 1
    assert pose.yaw == pytest.approx(15.0, abs=2.0)


def test_a_non_finite_landmark_is_refused():
    corrupt = _project(_rotation())
    corrupt["nose_tip"] = (float("nan"), 100.0)

    pose = head_pose(corrupt)

    assert not pose.ok and pose.rejected_by == "bad_landmarks"


def test_collinear_landmarks_are_refused():
    """Every point on one line leaves rotation about that line unobservable.
    The fit still produces numbers, which is why this is checked rather than
    left to the caller to notice."""
    flat = {name: (float(i * 10), 100.0)
            for i, name in enumerate(POSE_LANDMARKS)}

    pose = head_pose(flat)

    assert not pose.ok and pose.rejected_by == "degenerate"


# ── gaze ────────────────────────────────────────────────────────────────────

def _eye(side: str, iris_dx: float = 0.0, iris_dy: float = 0.0) -> dict:
    """One eye 40 wide and 16 tall, with the iris offset from its centre."""
    x0 = 200.0 if side == "left" else 400.0
    return {
        f"{side}_eye_outer": (x0 - 20.0, 150.0),
        f"{side}_eye_inner": (x0 + 20.0, 150.0),
        f"{side}_eye_upper": (x0, 142.0),
        f"{side}_eye_lower": (x0, 158.0),
        f"{side}_iris": (x0 + iris_dx, 150.0 + iris_dy),
    }


def test_a_centred_iris_reads_as_looking_ahead():
    g = gaze({**_eye("left"), **_eye("right")})

    assert g.ok
    assert g.x == pytest.approx(0.0, abs=0.01)
    assert g.y == pytest.approx(0.0, abs=0.01)
    assert g.eyes_used == 2


def test_gaze_carries_the_sign_of_the_offset():
    right = gaze({**_eye("left", iris_dx=10.0), **_eye("right", iris_dx=10.0)})
    down = gaze({**_eye("left", iris_dy=4.0), **_eye("right", iris_dy=4.0)})

    assert right.x == pytest.approx(0.5, abs=0.01)   # half the eye half-width
    assert down.y == pytest.approx(0.5, abs=0.01)


def test_one_visible_eye_still_measures():
    """A hand, hair or a head turn regularly hides one. Refusing the reading
    for that discards a measurable eye."""
    g = gaze(_eye("left", iris_dx=-10.0))

    assert g.ok and g.eyes_used == 1
    assert g.x == pytest.approx(-0.5, abs=0.01)


def test_a_closed_eye_is_not_a_gaze_direction():
    """Zero opening means there is nothing to measure a position within, and
    dividing by it turns a blink into a large offset."""
    closed = _eye("left")
    closed["left_eye_upper"] = (200.0, 150.0)
    closed["left_eye_lower"] = (200.0, 150.0)

    assert not gaze(closed).ok
    assert gaze(closed).rejected_by == "no_eye"


def test_gaze_is_clamped_rather_than_unbounded():
    """An iris tracked just outside the corner landmarks is detector wobble at
    the extreme of a real look, not a failure — but it must not scale without
    limit either."""
    g = gaze(_eye("left", iris_dx=200.0))

    assert g.x == 1.0


def test_no_eyes_reports_why():
    g = gaze({})

    assert not g.ok and g.rejected_by == "no_eye"
    assert g.eyes_used == 0
