"""Tests naming, scaling, and sanity-checking of mesh landmarks, without MediaPipe.

`FaceMeshLandmarker` itself needs MediaPipe and a camera, so it is not tested
directly here. These tests cover what it delegates to: mapping index to name,
converting normalised coordinates to pixels, and the topology check.

The index table is written from published Face Mesh topology, not measured
against hardware, so these tests only check that a wrong index gets caught,
not that the table itself is correct.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.app.services.face_landmarks import (
    MEDIAPIPE_INDICES,
    FaceMeshLandmarker,
    MIN_VISIBILITY,
    check_topology,
    named_landmarks,
)


class _Point:
    """What MediaPipe returns: normalised coordinates, and maybe a visibility."""

    def __init__(self, x: float, y: float, visibility=None):
        self.x = x
        self.y = y
        if visibility is not None:
            self.visibility = visibility


# A plausible face in normalised coordinates: eyes above mouth, mouth above
# chin, nose between the eyes. Built by name, then placed into a mesh array by
# index, so tests don't depend on the index table being correct — only used
# consistently.
FACE = {
    "left_eye_outer": (0.62, 0.40), "left_eye_inner": (0.545, 0.40),
    "left_eye_upper": (0.58, 0.385), "left_eye_lower": (0.58, 0.415),
    "left_iris": (0.58, 0.40),
    "right_eye_outer": (0.38, 0.40), "right_eye_inner": (0.455, 0.40),
    "right_eye_upper": (0.42, 0.385), "right_eye_lower": (0.42, 0.415),
    "right_iris": (0.42, 0.40),
    "nose_tip": (0.50, 0.50),
    "mouth_left": (0.56, 0.62), "mouth_right": (0.44, 0.62),
    "chin": (0.50, 0.75),
}


def _mesh(face=FACE, visibility=None, size=478):
    points = [_Point(0.5, 0.5, visibility) for _ in range(size)]
    for name, (x, y) in face.items():
        index = MEDIAPIPE_INDICES[name]
        # A mesh shorter than the iris indices is the real "refine_landmarks off"
        # case, so skip rather than pad it.
        if index < size:
            points[index] = _Point(x, y, visibility)
    return points


def _pixels(face=FACE, width=640, height=480):
    return {n: (x * width, y * height) for n, (x, y) in face.items()}


# ── naming and scaling ──────────────────────────────────────────────────────

def test_every_name_is_read_from_its_own_index():
    named = named_landmarks(_mesh(), 640, 480)

    assert set(named) == set(MEDIAPIPE_INDICES)
    for name, (nx, ny) in FACE.items():
        assert named[name] == pytest.approx((nx * 640, ny * 480))


def test_coordinates_are_scaled_by_the_frame_not_left_normalised():
    """Downstream code works in pixels. Fitting a pose to normalised coordinates
    would stretch faces by the frame's aspect ratio, which looks like a real
    head tilt rather than a bug."""
    wide = named_landmarks(_mesh(), 1280, 480)
    tall = named_landmarks(_mesh(), 640, 960)

    assert wide["nose_tip"][0] == pytest.approx(640.0)
    assert tall["nose_tip"][1] == pytest.approx(480.0)


def test_a_poorly_seen_landmark_is_omitted_not_placed():
    """Face Mesh reports every point whether or not it can see it, so an
    occluded landmark still arrives as a plausible-looking coordinate.
    `face_geometry` counts the names it receives, so a made-up entry would be
    counted as real."""
    named = named_landmarks(_mesh(visibility=MIN_VISIBILITY - 0.1), 640, 480)

    assert named == {}


def test_visibility_is_only_applied_when_the_detector_reports_it():
    """Face Mesh landmarks often have no visibility value at all. Treating a
    missing value as zero would reject every point on every frame."""
    named = named_landmarks(_mesh(visibility=None), 640, 480)

    assert len(named) == len(MEDIAPIPE_INDICES)


def test_a_short_mesh_is_survived_rather_than_raising():
    """Iris landmarks only exist with refine_landmarks on. Without them, only
    the iris names should be missing, not the whole frame."""
    named = named_landmarks(_mesh(size=468), 640, 480)

    assert "nose_tip" in named
    assert "left_iris" not in named and "right_iris" not in named


def test_a_non_finite_coordinate_is_dropped():
    points = _mesh()
    points[MEDIAPIPE_INDICES["chin"]] = _Point(float("nan"), 0.75)

    named = named_landmarks(points, 640, 480)

    assert "chin" not in named
    assert "nose_tip" in named


def test_a_zero_sized_frame_yields_nothing():
    assert named_landmarks(_mesh(), 0, 480) == {}
    assert named_landmarks(None, 640, 480) == {}


# ── the topology check ──────────────────────────────────────────────────────
#
# The index table is unverified against hardware, so these tests only check
# that a wrong index gets caught, not that the table itself is correct.

def test_a_plausible_face_passes():
    assert check_topology(_pixels()) is None


def test_eyes_and_mouth_the_wrong_way_up_are_refused():
    """The shape a swapped eye/mouth index block produces."""
    swapped = _pixels()
    for eye, mouth in (("left_eye_outer", "mouth_left"),
                       ("right_eye_outer", "mouth_right")):
        swapped[eye], swapped[mouth] = swapped[mouth], swapped[eye]
    swapped["left_eye_inner"], swapped["mouth_right"] = (
        swapped["mouth_right"], swapped["left_eye_inner"])

    assert check_topology(swapped) == "eyes_below_mouth"


def test_a_chin_above_the_mouth_is_refused():
    wrong = _pixels()
    wrong["chin"] = (320.0, 200.0)

    assert check_topology(wrong) == "mouth_below_chin"


def test_a_nose_outside_the_eyes_is_refused():
    """What picking a cheek or an ear index for the nose looks like."""
    wrong = _pixels()
    wrong["nose_tip"] = (600.0, 240.0)

    assert check_topology(wrong) == "nose_outside_eyes"


def test_an_iris_paired_with_the_wrong_eye_is_refused():
    """Both points are plausible on their own, so this error would otherwise go
    unnoticed and produce a wrong gaze reading instead of a missing one."""
    wrong = _pixels()
    wrong["left_iris"], wrong["right_iris"] = wrong["right_iris"], wrong["left_iris"]

    assert check_topology(wrong) == "left_iris_outside_eye"


def test_a_hard_sideways_look_is_not_mistaken_for_a_wrong_index():
    """The iris genuinely reaches the eye corner, and detector noise can carry
    it slightly past. Rejecting this would reject the exact looks gaze tracking
    is meant to measure."""
    looking = _pixels()
    outer, inner = looking["left_eye_outer"][0], looking["left_eye_inner"][0]
    looking["left_iris"] = (outer + 0.2 * (outer - inner), looking["left_iris"][1])

    assert check_topology(looking) is None


def test_a_partial_face_is_not_refused_for_what_it_lacks():
    """Occlusion is normal. Requiring every landmark would reject every frame
    where, say, a hand crosses the chin."""
    partial = {k: v for k, v in _pixels().items()
               if k not in ("chin", "left_iris", "mouth_left")}

    assert check_topology(partial) is None


def test_topology_holds_for_a_tilted_face():
    """The topology relations must survive normal head angles, or the check
    would reject real poses instead of catching bad ones."""
    rolled = {name: (x + (y - 240.0) * 0.3, y) for name, (x, y) in _pixels().items()}

    assert check_topology(rolled) is None


# ── the boundary, and the log ───────────────────────────────────────────────

def test_a_landmark_exactly_at_the_threshold_is_visible():
    """The check is `< MIN_VISIBILITY`, so a value exactly at the threshold
    should pass. Pinned here since flipping this to `<=` would silently drop a
    band of usable landmarks."""
    named = named_landmarks(_mesh(visibility=MIN_VISIBILITY), 640, 480)

    assert len(named) == len(MEDIAPIPE_INDICES)


class _FakeMesh:
    """Stands in for MediaPipe so `locate()` can be tested without a real Face
    Mesh."""

    def __init__(self, points):
        self._points = points

    def process(self, _frame):
        landmarks = self._points

        class _Result:
            multi_face_landmarks = ([type("F", (), {"landmark": landmarks})()]
                                    if landmarks is not None else None)
        return _Result()


def test_a_frame_with_no_face_is_empty_not_an_error(caplog):
    landmarker = FaceMeshLandmarker(mesh=_FakeMesh(None))

    assert landmarker.locate(object(), 640, 480) == {}
    assert landmarker.rejections == 0


def test_a_good_frame_returns_named_landmarks():
    landmarker = FaceMeshLandmarker(mesh=_FakeMesh(_mesh()))

    named = landmarker.locate(object(), 640, 480)

    assert set(named) == set(MEDIAPIPE_INDICES)


def test_a_bad_index_table_is_reported_once_not_once_per_frame(caplog):
    """At the capture loop's frame rate, logging every rejection would produce
    tens of identical lines a second. It should log once, but the rejection
    count must keep rising so a one-off error is distinguishable from a
    standing fault."""
    broken = _mesh({**FACE, "nose_tip": (0.95, 0.50)})   # nose outside the eyes
    landmarker = FaceMeshLandmarker(mesh=_FakeMesh(broken))

    with caplog.at_level("ERROR"):
        for _ in range(25):
            assert landmarker.locate(object(), 640, 480) == {}

    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1, f"logged {len(errors)} times for one standing fault"
    assert "nose_outside_eyes" in errors[0].getMessage()
    assert landmarker.rejections == 25


def test_an_empty_result_says_which_kind_of_empty_it_is():
    """An empty result can mean two different things: no face was detected, or
    a face was found but its landmarks failed the topology check. These need
    different reasons, or a topology refusal would look like "no face" and
    send someone to check their lighting instead of the real cause. This is a
    real case: a face near profile can put the nose tip past the far eye
    corner, correctly triggering `nose_outside_eyes` on a face that is plainly
    in frame.
    """
    no_face = FaceMeshLandmarker(mesh=_FakeMesh(None))
    refused = FaceMeshLandmarker(
        mesh=_FakeMesh(_mesh({**FACE, "nose_tip": (0.95, 0.50)})))
    good = FaceMeshLandmarker(mesh=_FakeMesh(_mesh()))

    assert no_face.locate(object(), 640, 480) == {}
    assert refused.locate(object(), 640, 480) == {}
    assert good.locate(object(), 640, 480) != {}

    assert no_face.last_reason == "no_face"
    assert refused.last_reason == "nose_outside_eyes"
    assert good.last_reason is None, "a frame that worked has nothing to explain"


def test_the_reason_is_cleared_by_a_good_frame():
    """The reason describes only the last frame, not the whole session. If it
    weren't cleared, one early refusal would keep being reported after later
    frames succeed."""
    landmarker = FaceMeshLandmarker(mesh=_FakeMesh(None))
    landmarker.locate(object(), 640, 480)
    assert landmarker.last_reason == "no_face"

    landmarker._mesh = _FakeMesh(_mesh())
    landmarker.locate(object(), 640, 480)

    assert landmarker.last_reason is None


# ── setup-time model provisioning ───────────────────────────────────────────

def test_the_model_url_is_pinned_not_latest():
    """`/latest/` and `/1/` serve the same bytes today, but a checksum pinned
    to a moving URL would break on the next release, and fail as a "checksum
    mismatch" that reads like a compromised download instead of a version
    bump."""
    from src.app.services.face_landmarks import MODEL_URL

    assert "/latest/" not in MODEL_URL
    assert MODEL_URL.startswith("https://")


def test_verify_rejects_a_file_of_the_wrong_size_without_hashing_it(tmp_path):
    from src.app.services.face_landmarks import verify

    wrong = tmp_path / "face_landmarker.task"
    wrong.write_bytes(b"not the model")

    assert verify(wrong) is False
    assert verify(tmp_path / "absent.task") is False


def test_ensure_model_refuses_rather_than_downloading_when_told_not_to(tmp_path):
    """Lets a caller check whether the model is present without reaching the
    network. The sidecar needs this since it must never fetch during a
    lesson."""
    from src.app.services.face_landmarks import ensure_model

    with pytest.raises(FileNotFoundError, match="missing or unverified"):
        ensure_model(tmp_path / "face_landmarker.task", allow_download=False)


def test_a_corrupt_model_is_deleted_rather_than_left_to_be_trusted(tmp_path):
    """A partial or substituted file left on disk would otherwise be trusted
    on the next run."""
    from src.app.services.face_landmarks import MODEL_BYTES, ensure_model

    corrupt = tmp_path / "face_landmarker.task"
    corrupt.write_bytes(b"\x00" * MODEL_BYTES)     # right size, wrong bytes

    with pytest.raises(FileNotFoundError):
        ensure_model(corrupt, allow_download=False)

    assert not corrupt.exists(), "a file that failed verification survived"


def test_a_tampered_model_is_refused_at_load_not_only_at_setup(tmp_path, monkeypatch):
    """`ensure_model` only checks the file at install time. Without a check at
    load time too, a truncated or hand-swapped `.task` file would load without
    complaint and produce wrong landmarks instead of an error — exactly what
    the checksum is meant to prevent. `face_emotion` verifies at load for the
    same reason; this is the landmark equivalent.
    """
    from src.app.services.face_landmarks import MODEL_BYTES, _TasksMesh

    tampered = tmp_path / "face_landmarker.task"
    tampered.write_bytes(b"\x01" * MODEL_BYTES)      # right size, wrong bytes

    with pytest.raises(ValueError, match="refusing to load unverified"):
        _TasksMesh(str(tampered))


def test_a_locked_model_file_reports_what_happened(tmp_path, monkeypatch):
    """Windows locks open files, and each sidecar runs in its own window, so an
    earlier `-Gaze` session still holding the model file could turn this into
    an unhandled PermissionError instead of a clear setup error."""
    from src.app.services import face_landmarks as fl

    stale = tmp_path / "face_landmarker.task"
    stale.write_bytes(b"not the model")
    monkeypatch.setattr(Path, "unlink",
                        lambda self, **kw: (_ for _ in ()).throw(
                            PermissionError("used by another process")))

    with pytest.raises(OSError, match="could not replace the landmark model"):
        fl.ensure_model(stale, allow_download=False)
