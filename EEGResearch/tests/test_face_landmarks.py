"""Landmark naming, scaling and topology checks, without MediaPipe."""

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


# Built by name and placed by index, so tests need the index table used consistently, not correct.
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
        # A short mesh is the real "refine_landmarks off" case: skip, don't pad.
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
    """Normalised coordinates would stretch faces by aspect ratio, reading as a real head tilt."""
    wide = named_landmarks(_mesh(), 1280, 480)
    tall = named_landmarks(_mesh(), 640, 960)

    assert wide["nose_tip"][0] == pytest.approx(640.0)
    assert tall["nose_tip"][1] == pytest.approx(480.0)


def test_a_poorly_seen_landmark_is_omitted_not_placed():
    """Face Mesh places occluded points too, and `face_geometry` counts every name it receives."""
    named = named_landmarks(_mesh(visibility=MIN_VISIBILITY - 0.1), 640, 480)

    assert named == {}


def test_visibility_is_only_applied_when_the_detector_reports_it():
    named = named_landmarks(_mesh(visibility=None), 640, 480)

    assert len(named) == len(MEDIAPIPE_INDICES)


def test_a_short_mesh_is_survived_rather_than_raising():
    """Iris landmarks exist only with refine_landmarks on."""
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
# The index table is unverified against hardware; these check a wrong index is caught.

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
    """Both points are plausible alone; unchecked, this is a wrong gaze reading, not a missing one."""
    wrong = _pixels()
    wrong["left_iris"], wrong["right_iris"] = wrong["right_iris"], wrong["left_iris"]

    assert check_topology(wrong) == "left_iris_outside_eye"


def test_a_hard_sideways_look_is_not_mistaken_for_a_wrong_index():
    """The iris reaches the eye corner and noise carries it slightly past."""
    looking = _pixels()
    outer, inner = looking["left_eye_outer"][0], looking["left_eye_inner"][0]
    looking["left_iris"] = (outer + 0.2 * (outer - inner), looking["left_iris"][1])

    assert check_topology(looking) is None


def test_a_partial_face_is_not_refused_for_what_it_lacks():
    partial = {k: v for k, v in _pixels().items()
               if k not in ("chin", "left_iris", "mouth_left")}

    assert check_topology(partial) is None


def test_topology_holds_for_a_tilted_face():
    rolled = {name: (x + (y - 240.0) * 0.3, y) for name, (x, y) in _pixels().items()}

    assert check_topology(rolled) is None


# ── the boundary, and the log ───────────────────────────────────────────────

def test_a_landmark_exactly_at_the_threshold_is_visible():
    """The check is `< MIN_VISIBILITY`; `<=` would silently drop a band of usable landmarks."""
    named = named_landmarks(_mesh(visibility=MIN_VISIBILITY), 640, 480)

    assert len(named) == len(MEDIAPIPE_INDICES)


class _FakeMesh:
    """Stands in for MediaPipe so `locate()` can be tested without a real Face Mesh."""

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
    """Logs once, but the rejection count keeps rising so a standing fault is distinguishable."""
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
    """No face and a topology refusal need different reasons; a near-profile face refuses while in frame."""
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
    landmarker = FaceMeshLandmarker(mesh=_FakeMesh(None))
    landmarker.locate(object(), 640, 480)
    assert landmarker.last_reason == "no_face"

    landmarker._mesh = _FakeMesh(_mesh())
    landmarker.locate(object(), 640, 480)

    assert landmarker.last_reason is None


# ── setup-time model provisioning ───────────────────────────────────────────

def test_the_model_url_is_pinned_not_latest():
    """A checksum pinned to a moving URL fails as a "mismatch" on the next release."""
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
    """The sidecar must never fetch during a lesson."""
    from src.app.services.face_landmarks import ensure_model

    with pytest.raises(FileNotFoundError, match="missing or unverified"):
        ensure_model(tmp_path / "face_landmarker.task", allow_download=False)


def test_a_corrupt_model_is_deleted_rather_than_left_to_be_trusted(tmp_path):
    from src.app.services.face_landmarks import MODEL_BYTES, ensure_model

    corrupt = tmp_path / "face_landmarker.task"
    corrupt.write_bytes(b"\x00" * MODEL_BYTES)     # right size, wrong bytes

    with pytest.raises(FileNotFoundError):
        ensure_model(corrupt, allow_download=False)

    assert not corrupt.exists(), "a file that failed verification survived"


def test_a_tampered_model_is_refused_at_load_not_only_at_setup(tmp_path, monkeypatch):
    """A file swapped after setup would otherwise load and produce wrong landmarks."""
    from src.app.services.face_landmarks import MODEL_BYTES, _TasksMesh

    tampered = tmp_path / "face_landmarker.task"
    tampered.write_bytes(b"\x01" * MODEL_BYTES)      # right size, wrong bytes

    with pytest.raises(ValueError, match="refusing to load unverified"):
        _TasksMesh(str(tampered))


def test_a_locked_model_file_reports_what_happened(tmp_path, monkeypatch):
    """Windows locks open files; an earlier `-Gaze` sidecar may still hold the model."""
    from src.app.services import face_landmarks as fl

    stale = tmp_path / "face_landmarker.task"
    stale.write_bytes(b"not the model")
    monkeypatch.setattr(Path, "unlink",
                        lambda self, **kw: (_ for _ in ()).throw(
                            PermissionError("used by another process")))

    with pytest.raises(OSError, match="could not replace the landmark model"):
        fl.ensure_model(stale, allow_download=False)
