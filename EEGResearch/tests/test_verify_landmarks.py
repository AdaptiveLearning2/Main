"""The verdict logic of `scripts/verify_landmarks.py`.

The camera half cannot be tested here, and does not need to be — it is a loop
that reads frames. What is worth testing is the half that turns numbers into a
decision, because that decision is a safety gate: it is the only thing standing
between a mirrored index table and a `gaze_x` column that four surfaces render.

A false PASS here is worse than no script at all. It would convert "unverified,
do not wire this in" into "verified against a real face", which is exactly the
sentence someone would act on.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_landmarks.py"


def _module():
    spec = importlib.util.spec_from_file_location("verify_landmarks", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify = _module()


def _step(frames=120, yaw=0.0, pitch=0.0, roll=0.0, gaze_x=0.0, gaze_y=0.0):
    return {"frames": frames, "yaw": yaw, "pitch": pitch, "roll": roll,
            "gaze_x": gaze_x, "gaze_y": gaze_y,
            "pose_refusals": [], "gaze_refusals": []}


def test_a_correct_table_passes():
    """Looking left drives both gaze.x and yaw negative, which is the
    convention `face_geometry` documents."""
    code = verify._verdict(
        _step(),
        _step(gaze_x=-0.6),
        _step(yaw=-25.0),
    )

    assert code == 0


def test_mirrored_eyes_fail_rather_than_pass():
    """The failure this whole script exists to catch. gaze.x positive when
    looking left means the eye and iris indices are swapped — and the reading
    is *mirrored*, not absent, so nothing downstream would ever question it."""
    code = verify._verdict(
        _step(),
        _step(gaze_x=+0.6),
        _step(yaw=-25.0),
    )

    assert code == 1


def test_mirrored_outline_fails_independently_of_the_eyes():
    """The eye indices and the outline indices are different regions of the
    table, so one being right says nothing about the other. A check that only
    looked at gaze would pass a face whose yaw was backwards."""
    code = verify._verdict(
        _step(),
        _step(gaze_x=-0.6),
        _step(yaw=+25.0),
    )

    assert code == 1


def test_a_movement_too_small_to_read_is_not_a_pass():
    """Neither direction was demonstrated. Treating "did not move" as success
    is how a table nobody actually exercised gets marked verified."""
    code = verify._verdict(
        _step(),
        _step(gaze_x=-0.02),
        _step(yaw=-1.0),
    )

    assert code == 1


def test_no_face_is_inconclusive_not_a_failure_of_the_table():
    """A camera problem is not evidence about the index mapping, and reporting
    it as a failure would send someone editing a table that may be fine."""
    code = verify._verdict(_step(frames=0), _step(), _step())

    assert code == 2


def test_a_crooked_sitter_still_passes_the_square_on_step():
    """Nobody sits like a tripod. A square-on tolerance tight enough to fail a
    real person would make the script useless in the way that matters: it would
    be dismissed rather than acted on."""
    code = verify._verdict(
        _step(yaw=-8.0, pitch=6.0, roll=-5.0),
        _step(gaze_x=-0.5),
        _step(yaw=-20.0),
    )

    assert code == 0


def test_a_missing_reading_is_a_failure_not_a_pass():
    code = verify._verdict(_step(), _step(gaze_x=None), _step(yaw=None))

    assert code == 1


def test_the_median_ignores_refused_frames_rather_than_counting_them_as_zero():
    """A refused window has no value, and averaging it as 0.0 would drag a real
    look back toward centre — reporting "barely moved" for someone who moved."""
    assert verify._median([None, -0.6, None, -0.5]) == pytest.approx(-0.55)
    assert verify._median([None, None]) is None
    assert verify._median([]) is None
