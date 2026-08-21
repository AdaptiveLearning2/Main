"""Tests head pose and gaze from landmarks, without a landmark model.

Everything here is plain numpy on synthetic input. There's no detector
involved: the module takes named landmarks and returns geometry.

Poses are round-tripped rather than asserted from a table: a known rotation
is applied to the canonical face, projected orthographically, and recovered,
so the sign conventions come from the definition itself, not a copied
expectation that could reproduce the same sign error.
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
    """The canonical face rotated, scaled, shifted, and flattened to the
    image. Scale and offset are non-trivial on purpose, so a fit that
    silently assumed a centred, unit-scaled face wouldn't pass.
    """
    out = {}
    for name in names:
        point = rotation @ np.array(CANONICAL_FACE[name], dtype=float)
        out[name] = (point[0] * scale + offset[0], point[1] * scale + offset[1])
    return out


# -- pose round-trips --

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
    looks -- an extraction that mixed two axes could still pass every
    single-axis case above."""
    pose = head_pose(_project(_rotation(yaw_deg=20.0, pitch_deg=-12.0, roll_deg=8.0)))

    assert pose.yaw == pytest.approx(20.0, abs=2.0)
    assert pose.pitch == pytest.approx(-12.0, abs=2.0)
    assert pose.roll == pytest.approx(8.0, abs=2.0)


def test_pose_is_independent_of_where_the_face_is_in_the_frame():
    """A student sitting off to one side is not a student turning their
    head. Without centring, every off-centre face would read as rotated."""
    rotation = _rotation(yaw_deg=18.0)
    near = head_pose(_project(rotation, offset=(100.0, 90.0)))
    far = head_pose(_project(rotation, offset=(540.0, 400.0)))

    assert near.yaw == pytest.approx(far.yaw, abs=0.1)


def test_pose_is_independent_of_how_close_the_face_is():
    """Scale is solved for, so a child's smaller face and an adult leaning
    in should give the same angles for the same pose."""
    rotation = _rotation(pitch_deg=15.0)
    small = head_pose(_project(rotation, scale=1.0))
    large = head_pose(_project(rotation, scale=4.0))

    assert small.pitch == pytest.approx(large.pitch, abs=0.1)


# -- refusals --

def test_too_few_landmarks_refuses_rather_than_guessing():
    """A pose from four coplanar eye corners is under-determined about the
    axis through them while still looking confident, so it must be refused
    with a named reason, not returned silently."""
    partial = _project(_rotation(), names=POSE_LANDMARKS[:MIN_POSE_POINTS - 1])

    pose = head_pose(partial)

    assert not pose.ok
    assert pose.rejected_by == "too_few_landmarks"
    assert pose.yaw is None and pose.pitch is None and pose.roll is None


def test_a_missing_landmark_is_skipped_not_placed_at_the_origin():
    """Absent is not zero -- a landmark dropped to (0, 0) would drag the fit
    toward the top-left corner while still returning an answer."""
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
    """Every point on one line leaves rotation about that line unobservable,
    and the fit would still produce numbers -- so this must be checked
    rather than left to the caller."""
    flat = {name: (float(i * 10), 100.0)
            for i, name in enumerate(POSE_LANDMARKS)}

    pose = head_pose(flat)

    assert not pose.ok and pose.rejected_by == "degenerate"


@pytest.mark.parametrize("yaw", [91.0, 100.0, 120.0, 150.0])
def test_a_face_turned_past_ninety_degrees_is_refused_not_mirrored(yaw):
    """`cos_yaw` is a hypot and never negative, so recovered yaw is always in
    (-90, 90) -- a face turned further comes back on the other branch,
    silently (a true yaw of 120 reported 60 with `ok=True`). A student
    turning to talk to someone beside them will hit this regularly, so it
    must be refused rather than reported wrong.
    """
    pose = head_pose(_project(_rotation(yaw_deg=yaw)))

    assert not pose.ok, f"true yaw {yaw} came back as {pose.yaw}"
    assert pose.rejected_by == "implausible_pose"


def test_the_wrong_branch_corrupts_all_three_angles_and_is_caught():
    """Pitch and roll are recovered from terms carrying an implicit
    cos(yaw), so crossing the boundary swings both by 180 degrees: a true
    (91, 15, 10) reported (89, -165, -170) -- a 2 degree change in truth
    moving two angles by half a turn, confidently.
    """
    pose = head_pose(_project(_rotation(yaw_deg=91.0, pitch_deg=15.0, roll_deg=10.0)))

    assert not pose.ok and pose.rejected_by == "implausible_pose"


def test_a_steep_but_real_turn_still_measures():
    """The guard must not eat the range it protects -- 85 degrees is a hard
    look sideways and is on the correct branch, so it must still measure."""
    pose = head_pose(_project(_rotation(yaw_deg=85.0, pitch_deg=10.0)))

    assert pose.ok
    assert pose.yaw == pytest.approx(85.0, abs=2.0)
    assert pose.pitch == pytest.approx(10.0, abs=3.0)


# -- gaze --

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
    """A hand, hair or a head turn regularly hides one eye. Refusing the
    whole reading for that would discard a perfectly measurable eye."""
    g = gaze(_eye("left", iris_dx=-10.0))

    assert g.ok and g.eyes_used == 1
    assert g.x == pytest.approx(-0.5, abs=0.01)


def test_a_closed_eye_is_not_a_gaze_direction():
    """Zero eye opening means there's nothing to measure a position within,
    and dividing by it would turn a blink into a large offset."""
    closed = _eye("left")
    closed["left_eye_upper"] = (200.0, 150.0)
    closed["left_eye_lower"] = (200.0, 150.0)

    assert not gaze(closed).ok
    assert gaze(closed).rejected_by == "no_eye"


def test_gaze_is_clamped_rather_than_unbounded():
    """An iris tracked just outside the corner landmarks is detector wobble
    at the extreme of a real look, not a failure -- but the value must not
    scale without limit either."""
    g = gaze(_eye("left", iris_dx=200.0))

    assert g.x == 1.0


def test_no_eyes_reports_why():
    g = gaze({})

    assert not g.ok and g.rejected_by == "no_eye"
    assert g.eyes_used == 0


# -- against a real frame, not against the model --
#
# Everything above rotates CANONICAL_FACE and recovers the rotation, which is
# self-consistent even under a mirrored model and so cannot catch a mirrored
# handedness on its own -- a real camera caught it instead: a person sitting
# perfectly square on produced `implausible_pose` on 120 of 120 frames.
#
# These tests build the observed points from the *image* convention instead
# -- x right, y down, frame not mirrored, so the subject's own left is on the
# image right -- and assert what the module docstring promises about
# direction.

def _facing_camera() -> dict:
    """A square-on face as a real non-mirrored frame presents it.

    Written out by hand rather than projected from CANONICAL_FACE, so the
    test isn't blind to the model itself being wrong.
    """
    return {
        # subject's RIGHT side -> image LEFT (smaller x)
        "right_eye_outer": (230.0, 172.0), "right_eye_inner": (290.0, 172.0),
        "mouth_right":     (266.0, 316.0),
        # subject's LEFT side -> image RIGHT (larger x)
        "left_eye_outer":  (410.0, 172.0), "left_eye_inner":  (350.0, 172.0),
        "mouth_left":      (374.0, 316.0),
        "nose_tip":        (320.0, 240.0), "chin": (320.0, 390.0),
    }


def test_the_model_handedness_matches_a_real_frame():
    """A rotation cannot reflect. If CANONICAL_FACE puts the subject's left
    at negative x while the camera puts it at positive x, no pose maps one
    to the other and every frame is refused -- including a subject sitting
    perfectly still, square on, in good light.
    """
    pose = head_pose(_facing_camera())

    assert pose.rejected_by is None, (
        "a square-on face is unfittable, which means the canonical model's "
        "handedness disagrees with the frame's")
    assert pose.ok
    assert abs(pose.yaw) < 5.0 and abs(pose.pitch) < 5.0 and abs(pose.roll) < 5.0


def test_turning_toward_your_own_left_gives_positive_yaw():
    """The subject's own left is the image right, so turning that way is a
    turn toward the right-hand side of the image. A mirrored index table
    would break this assertion.
    """
    turned = _facing_camera()
    # Turning toward their own left swings the nose toward the image right and
    # foreshortens the far (image-left) side of the face.
    turned["nose_tip"] = (352.0, 240.0)
    turned["right_eye_outer"] = (252.0, 172.0)
    turned["mouth_right"] = (280.0, 316.0)

    pose = head_pose(turned)

    assert pose.ok, pose.rejected_by
    assert pose.yaw > 5.0, f"turning toward their own left gave yaw={pose.yaw}"


def test_looking_toward_your_own_left_gives_positive_gaze_x():
    """`gaze.x` is measured in image x -- `_eye_offset` divides by an
    absolute width, so there is no per-eye sign flip. The subject's own left
    is the image right, so looking that way moves both irises to larger x
    and gaze.x should be positive.
    """
    lm = dict(_facing_camera())
    for side, (lo, hi) in (("right", (230.0, 290.0)), ("left", (350.0, 410.0))):
        lm[f"{side}_eye_upper"] = ((lo + hi) / 2, 162.0)
        lm[f"{side}_eye_lower"] = ((lo + hi) / 2, 182.0)
        lm[f"{side}_iris"] = ((lo + hi) / 2 + 15.0, 172.0)   # toward image right

    assert gaze(lm).x > 0.3


def test_gaze_cannot_see_a_left_right_swap_and_must_not_claim_to():
    """Both eyes' offsets are computed identically in image coordinates and
    averaged, so permuting the left/right labels returns the same number to
    the last bit -- gaze alone cannot detect a mirrored index table.
    """
    lm = dict(_facing_camera())
    for side, (lo, hi) in (("right", (230.0, 290.0)), ("left", (350.0, 410.0))):
        lm[f"{side}_eye_upper"] = ((lo + hi) / 2, 162.0)
        lm[f"{side}_eye_lower"] = ((lo + hi) / 2, 182.0)
        lm[f"{side}_iris"] = ((lo + hi) / 2 + 15.0, 172.0)

    swapped = {k.replace("left", "TMP").replace("right", "left")
                .replace("TMP", "right"): v for k, v in lm.items()}

    assert gaze(swapped).x == gaze(lm).x


def test_a_mirrored_index_table_is_refused_by_the_pose_fit():
    """Mirrored labels make the correspondence unfittable by a rotation, so
    the pose fit refuses rather than answering wrongly -- the same guard
    that catches a wrong-handed model also catches this.
    """
    lm = _facing_camera()
    swapped = {k.replace("left", "TMP").replace("right", "left")
                .replace("TMP", "right"): v for k, v in lm.items()}

    assert head_pose(swapped).rejected_by is not None
