"""Naming, scaling and sanity-checking mesh landmarks, without MediaPipe.

`FaceMeshLandmarker` itself is not exercised — it needs MediaPipe and a camera,
like `FaceLocator`, and belongs to the manual verification step. What is tested
is everything it delegates to: which index becomes which name, how normalised
coordinates become pixels, and the topology check that exists because the index
table could not be verified against hardware.

That last one carries the weight here. The table is written from published Face
Mesh topology rather than measured, so the tests treat it as suspect: they check
that a wrong index is *caught*, not that the table is right — which is a claim
only a camera can settle.
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
    """What MediaPipe hands back: normalised coordinates, maybe a visibility."""

    def __init__(self, x: float, y: float, visibility=None):
        self.x = x
        self.y = y
        if visibility is not None:
            self.visibility = visibility


# A plausible face in normalised coordinates: eyes above mouth, mouth above
# chin, nose between the eyes. Built by name and then scattered into a mesh
# array, so the test never depends on the index table being right — only on it
# being *used* consistently.
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
        # A mesh shorter than the iris indices is the real `refine_landmarks
        # off` case, not a broken fixture -- skip rather than pad, or the test
        # for it would be building a mesh that cannot exist.
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
    """Everything downstream works in pixels. Fitting a pose to normalised
    coordinates stretches every face by the frame's aspect ratio — which looks
    like a real, consistent head tilt rather than like a bug."""
    wide = named_landmarks(_mesh(), 1280, 480)
    tall = named_landmarks(_mesh(), 640, 960)

    assert wide["nose_tip"][0] == pytest.approx(640.0)
    assert tall["nose_tip"][1] == pytest.approx(480.0)


def test_a_poorly_seen_landmark_is_omitted_not_placed():
    """Face Mesh reports every point whether or not it can see it, so an
    occluded corner arrives as a confident coordinate somewhere plausible.
    `face_geometry` counts the names it was given, so a present-but-invented
    entry would be counted as supplied."""
    named = named_landmarks(_mesh(visibility=MIN_VISIBILITY - 0.1), 640, 480)

    assert named == {}


def test_visibility_is_only_applied_when_the_detector_reports_it():
    """Face Mesh landmarks often carry no visibility at all. Treating absent as
    zero would reject every point on every frame."""
    named = named_landmarks(_mesh(visibility=None), 640, 480)

    assert len(named) == len(MEDIAPIPE_INDICES)


def test_a_short_mesh_is_survived_rather_than_raising():
    """Iris landmarks only exist with refine_landmarks on. A build without them
    should lose the iris names, not the frame."""
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
# The index table is unverified against hardware, so these test that a wrong
# index is caught — not that the table is right, which only a camera settles.

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
    """Otherwise invisible: both are entirely plausible points on a face, and
    the resulting gaze is wrong rather than absent."""
    wrong = _pixels()
    wrong["left_iris"], wrong["right_iris"] = wrong["right_iris"], wrong["left_iris"]

    assert check_topology(wrong) == "left_iris_outside_eye"


def test_a_hard_sideways_look_is_not_mistaken_for_a_wrong_index():
    """The iris genuinely reaches the corner and detector wobble carries it
    just past. A check that refused this would reject exactly the looks the
    gaze signal exists to measure."""
    looking = _pixels()
    outer, inner = looking["left_eye_outer"][0], looking["left_eye_inner"][0]
    looking["left_iris"] = (outer + 0.2 * (outer - inner), looking["left_iris"][1])

    assert check_topology(looking) is None


def test_a_partial_face_is_not_refused_for_what_it_lacks():
    """Occlusion is ordinary. A check that needed every landmark would refuse
    every frame where a hand crossed the chin."""
    partial = {k: v for k, v in _pixels().items()
               if k not in ("chin", "left_iris", "mouth_left")}

    assert check_topology(partial) is None


def test_topology_holds_for_a_tilted_face():
    """Every relation must survive the head angles a neck allows, or the check
    would reject real poses — which is worse than the mistake it guards."""
    rolled = {name: (x + (y - 240.0) * 0.3, y) for name, (x, y) in _pixels().items()}

    assert check_topology(rolled) is None


# ── the boundary, and the log ───────────────────────────────────────────────

def test_a_landmark_exactly_at_the_threshold_is_visible():
    """`< MIN_VISIBILITY` rejects, so the threshold itself is kept. Pinned
    rather than left implied: flipping this to `<=` drops a whole band of
    marginal-but-usable landmarks, and nothing else would notice."""
    named = named_landmarks(_mesh(visibility=MIN_VISIBILITY), 640, 480)

    assert len(named) == len(MEDIAPIPE_INDICES)


class _FakeMesh:
    """Stands in for MediaPipe. `locate()` is testable through this — only
    constructing a real Face Mesh is not."""

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
    """At the capture loop's rate this is tens of identical lines a second,
    which buries the one thing worth reading. It is a standing condition, not
    an event — but the count has to keep rising, or "wrong on one frame" and
    "wrong on every frame all session" would read identically."""
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
    """`{}` covers two unrelated events and a caller cannot act on them the
    same way: the detector saw no face, or it saw one and the landmark set was
    refused. Reported identically, a topology refusal reads as "no face" and
    sends someone to check their lighting.

    Not hypothetical. Near profile the nose tip crosses the far eye corner, so
    `nose_outside_eyes` is the *correct* answer for a face plainly in frame —
    which is the case where conflating the two is most misleading.
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
    """It describes the last frame, not the session. Left standing, a single
    early refusal would keep explaining every subsequent success."""
    landmarker = FaceMeshLandmarker(mesh=_FakeMesh(None))
    landmarker.locate(object(), 640, 480)
    assert landmarker.last_reason == "no_face"

    landmarker._mesh = _FakeMesh(_mesh())
    landmarker.locate(object(), 640, 480)

    assert landmarker.last_reason is None


# ── setup-time model provisioning ───────────────────────────────────────────

def test_the_model_url_is_pinned_not_latest():
    """`/latest/` and `/1/` serve the same bytes today, but a checksum pinned
    against a moving URL is a setup failure waiting for the next release --
    and it would fail as "checksum mismatch", which reads as a compromised
    download rather than an upstream version bump."""
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
    """The flag that lets a caller ask "is it here?" without reaching the
    network -- which is what the sidecar needs, since it must never fetch during
    a lesson."""
    from src.app.services.face_landmarks import ensure_model

    with pytest.raises(FileNotFoundError, match="missing or unverified"):
        ensure_model(tmp_path / "face_landmarker.task", allow_download=False)


def test_a_corrupt_model_is_deleted_rather_than_left_to_be_trusted(tmp_path):
    """`verify` checks existence before anything else, so a partial or
    substituted file left on disk would be trusted by the next run."""
    from src.app.services.face_landmarks import MODEL_BYTES, ensure_model

    corrupt = tmp_path / "face_landmarker.task"
    corrupt.write_bytes(b"\x00" * MODEL_BYTES)     # right size, wrong bytes

    with pytest.raises(FileNotFoundError):
        ensure_model(corrupt, allow_download=False)

    assert not corrupt.exists(), "a file that failed verification survived"


def test_a_tampered_model_is_refused_at_load_not_only_at_setup(tmp_path, monkeypatch):
    """`ensure_model` runs once, at install. On its own it protects that moment
    and nothing after it — a truncated, half-written or hand-swapped `.task`
    would load without complaint and produce landmarks that are wrong rather
    than absent, which is the whole failure a checksum exists to prevent.

    `face_emotion` verifies before constructing its session for the same reason;
    this is the landmark half of that rule.
    """
    from src.app.services.face_landmarks import MODEL_BYTES, _TasksMesh

    tampered = tmp_path / "face_landmarker.task"
    tampered.write_bytes(b"\x01" * MODEL_BYTES)      # right size, wrong bytes

    with pytest.raises(ValueError, match="refusing to load unverified"):
        _TasksMesh(str(tampered))


def test_a_locked_model_file_reports_what_happened(tmp_path, monkeypatch):
    """Windows locks an open file, and this project's convention is a sidecar
    per window — so an earlier `-Gaze` session still holding the model turns a
    designed failure mode into an unhandled PermissionError out of a setup
    script."""
    from src.app.services import face_landmarks as fl

    stale = tmp_path / "face_landmarker.task"
    stale.write_bytes(b"not the model")
    monkeypatch.setattr(Path, "unlink",
                        lambda self, **kw: (_ for _ in ()).throw(
                            PermissionError("used by another process")))

    with pytest.raises(OSError, match="could not replace the landmark model"):
        fl.ensure_model(stale, allow_download=False)
