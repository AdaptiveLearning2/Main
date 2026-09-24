"""Head pose and gaze from synthetic landmarks; poses are round-tripped, not tabulated."""

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
    """The canonical face rotated and projected; non-trivial scale/offset on purpose."""
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
    """An extraction mixing two axes could pass every single-axis case."""
    pose = head_pose(_project(_rotation(yaw_deg=20.0, pitch_deg=-12.0, roll_deg=8.0)))

    assert pose.yaw == pytest.approx(20.0, abs=2.0)
    assert pose.pitch == pytest.approx(-12.0, abs=2.0)
    assert pose.roll == pytest.approx(8.0, abs=2.0)


def test_pose_is_independent_of_where_the_face_is_in_the_frame():
    rotation = _rotation(yaw_deg=18.0)
    near = head_pose(_project(rotation, offset=(100.0, 90.0)))
    far = head_pose(_project(rotation, offset=(540.0, 400.0)))

    assert near.yaw == pytest.approx(far.yaw, abs=0.1)


def test_pose_is_independent_of_how_close_the_face_is():
    rotation = _rotation(pitch_deg=15.0)
    small = head_pose(_project(rotation, scale=1.0))
    large = head_pose(_project(rotation, scale=4.0))

    assert small.pitch == pytest.approx(large.pitch, abs=0.1)


# -- refusals --

def test_too_few_landmarks_refuses_rather_than_guessing():
    """Four coplanar eye corners under-determine the pose while looking confident."""
    partial = _project(_rotation(), names=POSE_LANDMARKS[:MIN_POSE_POINTS - 1])

    pose = head_pose(partial)

    assert not pose.ok
    assert pose.rejected_by == "too_few_landmarks"
    assert pose.yaw is None and pose.pitch is None and pose.roll is None


def test_a_missing_landmark_is_skipped_not_placed_at_the_origin():
    """A landmark at (0, 0) would drag the fit toward the corner and still answer."""
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
    """Rotation about the line is unobservable, yet the fit would still produce numbers."""
    flat = {name: (float(i * 10), 100.0)
            for i, name in enumerate(POSE_LANDMARKS)}

    pose = head_pose(flat)

    assert not pose.ok and pose.rejected_by == "degenerate"


@pytest.mark.parametrize("yaw", [91.0, 100.0, 120.0, 150.0])
def test_a_face_turned_past_ninety_degrees_is_refused_not_mirrored(yaw):
    """`cos_yaw` is never negative, so a face turned past 90 comes back on the other branch."""
    pose = head_pose(_project(_rotation(yaw_deg=yaw)))

    assert not pose.ok, f"true yaw {yaw} came back as {pose.yaw}"
    assert pose.rejected_by == "implausible_pose"


def test_the_wrong_branch_corrupts_all_three_angles_and_is_caught():
    """Pitch and roll carry an implicit cos(yaw), so crossing 90 swings both by 180 degrees."""
    pose = head_pose(_project(_rotation(yaw_deg=91.0, pitch_deg=15.0, roll_deg=10.0)))

    assert not pose.ok and pose.rejected_by == "implausible_pose"


def test_a_steep_but_real_turn_still_measures():
    """85 degrees is on the correct branch, so the guard must not refuse it."""
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
    g = gaze(_eye("left", iris_dx=-10.0))

    assert g.ok and g.eyes_used == 1
    assert g.x == pytest.approx(-0.5, abs=0.01)


def test_a_closed_eye_is_not_a_gaze_direction():
    """Dividing by a zero opening would turn a blink into a large offset."""
    closed = _eye("left")
    closed["left_eye_upper"] = (200.0, 150.0)
    closed["left_eye_lower"] = (200.0, 150.0)

    assert not gaze(closed).ok
    assert gaze(closed).rejected_by == "no_eye"


def test_gaze_is_clamped_rather_than_unbounded():
    """An iris just past the corners is detector wobble, not a failure, but must not scale."""
    g = gaze(_eye("left", iris_dx=200.0))

    assert g.x == 1.0


def test_no_eyes_reports_why():
    g = gaze({})

    assert not g.ok and g.rejected_by == "no_eye"
    assert g.eyes_used == 0


# -- against a real frame, not against the model --
# Round-trips cannot catch a mirrored model; these use image coordinates
# (x right, y down, not mirrored: the subject's left is image right).

def _facing_camera() -> dict:
    """A square-on face in a non-mirrored frame, by hand so a wrong model is visible."""
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
    """A rotation cannot reflect, so wrong handedness refuses every frame."""
    pose = head_pose(_facing_camera())

    assert pose.rejected_by is None, (
        "a square-on face is unfittable, which means the canonical model's "
        "handedness disagrees with the frame's")
    assert pose.ok
    assert abs(pose.yaw) < 5.0 and abs(pose.pitch) < 5.0 and abs(pose.roll) < 5.0


def test_turning_toward_your_own_left_gives_positive_yaw():
    turned = _facing_camera()
    # Nose toward image right; the far (image-left) side foreshortens.
    turned["nose_tip"] = (352.0, 240.0)
    turned["right_eye_outer"] = (252.0, 172.0)
    turned["mouth_right"] = (280.0, 316.0)

    pose = head_pose(turned)

    assert pose.ok, pose.rejected_by
    assert pose.yaw > 5.0, f"turning toward their own left gave yaw={pose.yaw}"


def test_looking_toward_your_own_left_gives_positive_gaze_x():
    """`gaze.x` is in image x, with no per-eye sign flip."""
    lm = dict(_facing_camera())
    for side, (lo, hi) in (("right", (230.0, 290.0)), ("left", (350.0, 410.0))):
        lm[f"{side}_eye_upper"] = ((lo + hi) / 2, 162.0)
        lm[f"{side}_eye_lower"] = ((lo + hi) / 2, 182.0)
        lm[f"{side}_iris"] = ((lo + hi) / 2 + 15.0, 172.0)   # toward image right

    assert gaze(lm).x > 0.3


def test_gaze_cannot_see_a_left_right_swap_and_must_not_claim_to():
    """Both eyes are computed identically and averaged, so a label swap is invisible to gaze."""
    lm = dict(_facing_camera())
    for side, (lo, hi) in (("right", (230.0, 290.0)), ("left", (350.0, 410.0))):
        lm[f"{side}_eye_upper"] = ((lo + hi) / 2, 162.0)
        lm[f"{side}_eye_lower"] = ((lo + hi) / 2, 182.0)
        lm[f"{side}_iris"] = ((lo + hi) / 2 + 15.0, 172.0)

    swapped = {k.replace("left", "TMP").replace("right", "left")
                .replace("TMP", "right"): v for k, v in lm.items()}

    assert gaze(swapped).x == gaze(lm).x


def test_a_mirrored_index_table_is_refused_by_the_pose_fit():
    lm = _facing_camera()
    swapped = {k.replace("left", "TMP").replace("right", "left")
                .replace("TMP", "right"): v for k, v in lm.items()}

    assert head_pose(swapped).rejected_by is not None
