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
    """Looking and turning toward your own left drives both gaze.x and yaw
    *positive*: the frame is not mirrored, so your own left is the image right,
    and both quantities are measured in image coordinates.

    These were asserted negative until a camera said otherwise on 2026-08-12.
    A correct index table failed, and the script named a specific edit to make
    — swapping `left_*` and `right_*` — which would have broken it."""
    code = verify._verdict(
        _step(),
        _step(gaze_x=+0.6),
        _step(yaw=+25.0),
    )

    assert code == 0


def test_gaze_tracking_the_wrong_way_fails():
    """gaze.x negative while looking toward your own left means the iris is
    moving the wrong way in image x — a mirrored frame, or an iris index that
    is not the iris.

    Note what this is *not*: a left/right label swap. `gaze` averages both eyes
    in image coordinates, so permuting the labels returns an identical number
    and no threshold here can see it. `test_gaze_cannot_see_a_left_right_swap`
    in test_face_geometry pins that."""
    code = verify._verdict(
        _step(),
        _step(gaze_x=-0.6),
        _step(yaw=+25.0),
    )

    assert code == 1


def test_an_inverted_pose_fit_fails_independently_of_the_eyes():
    """Gaze and yaw come from different halves — iris offsets versus a fit
    against a model with a handedness — so one being right says nothing about
    the other. A check that only looked at gaze would pass a face whose yaw was
    backwards, and gaze is the half that cannot see a mirror at all."""
    code = verify._verdict(
        _step(),
        _step(gaze_x=+0.6),
        _step(yaw=-25.0),
    )

    assert code == 1


def test_a_movement_too_small_to_read_is_not_a_pass():
    """Neither direction was demonstrated. Treating "did not move" as success
    is how a table nobody actually exercised gets marked verified."""
    code = verify._verdict(
        _step(),
        _step(gaze_x=+0.02),
        _step(yaw=+1.0),
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
        _step(gaze_x=+0.5),
        _step(yaw=+20.0),
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


def test_a_failed_square_on_skips_the_yaw_check_but_not_the_gaze_check(capsys):
    """The two depend on different things, so gating them together would throw
    away a usable answer.

    `gaze` is computed from the eye and iris landmarks alone and never touches
    the rotation fit, so a wrong canonical model cannot flip its sign — the
    iris-tracking check still works. (It is not a mirror check; nothing about
    gaze is.) `yaw` comes straight out of that fit, so it is
    meaningless until square-on passes, and printing PASS beside a warning
    saying not to trust it is how someone reads the wrong half of a failed run.
    """
    code = verify._verdict(
        _step(yaw=60.0),               # square-on fails: the pose fit is wrong
        _step(gaze_x=+0.6),            # ...but the eyes are still readable
        _step(yaw=-40.0),              # ...and this would otherwise print PASS/FAIL
    )
    out = capsys.readouterr().out

    assert code == 1
    assert "SKIP" in out and "yaw comes from the pose fit" in out
    assert "looking left drives gaze.x positive" in out


def test_nothing_printed_needs_more_than_ascii():
    """A Windows console cannot encode box-drawing characters or arrows. The
    failure is a UnicodeEncodeError on the first line of output, before the
    check runs — on the project's first-class dev platform, for a script whose
    whole premise is being cheap to run.

    **`ascii`, not `cp1252`.** The bar was cp1252 and that is weaker than the
    rule the script states: cp1252 happens to contain an em dash, so one got
    into a runtime print twenty lines below the comment forbidding it and this
    test passed. cp437 and cp850 — both reachable in a `cmd.exe` console — do
    not contain it, and neither does a pipe to a file under an ASCII locale.

    pytest captures output through a UTF-8 buffer, so no other test here can
    see this; the source is checked directly instead.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    body = source.split('"""', 2)[2]      # the docstring is never printed
    body.encode("ascii")                  # raises if any runtime string cannot


# ── the preview ─────────────────────────────────────────────────────────────

def test_the_preview_writes_nothing_to_disk():
    """The script's headline promise is that it records nothing, and adding a
    window is exactly the change that would quietly break it — a frame is
    already decoded and drawn on, so saving one is a single call away.

    Cheap and coarse on purpose: it cannot prove absence, but it fails the
    moment someone reaches for the obvious call.
    """
    source = SCRIPT.read_text(encoding="utf-8")

    for forbidden in ("imwrite", "imencode", "VideoWriter"):
        assert forbidden not in source, f"{forbidden} would persist a frame"


def test_the_preview_sees_frames_with_no_face_rather_than_freezing():
    """A stretch with no face is the state a subject most needs to see, because
    it is usually them having left the frame. Drawing only when a face is found
    leaves the window frozen on the last good frame, which looks like the script
    having hung."""
    class _Source:
        def __init__(self): self.left = 3
        def read(self):
            self.left -= 1
            return object() if self.left >= 0 else None

    class _Landmarker:
        def locate(self, frame, w, h): return {}          # never finds a face

    class _Gui:
        aborted = False
        def __init__(self): self.drawn = 0
        def frame(self, *a, **k): self.drawn += 1

    gui = _Gui()
    samples = verify._collect(_Landmarker(), _Source(), 0.2, 640, 480, gui=gui,
                              step="x", instruction="y", watch="yaw", target=0.0)

    assert samples == [], "a frame with no face is not a sample"
    assert gui.drawn > 0, "the preview never saw the frames with no face"


# ── the emotion path ────────────────────────────────────────────────────────

def test_the_emotion_check_is_skipped_rather_than_failed_without_a_model(tmp_path,
                                                                        monkeypatch):
    """A gaze-only install legitimately has no FER+ model — it is a separate
    35 MB download. Failing there would make the landmark check depend on a
    channel it is not about."""
    monkeypatch.setenv("FACE_EMOTION_MODEL_PATH", str(tmp_path / "absent.onnx"))

    assert verify._emotion_classifier() is None


def test_a_model_that_will_not_load_is_reported_not_raised(tmp_path, monkeypatch,
                                                           capsys):
    """A corrupt or truncated model must not take the whole camera check down —
    the three steps it exists for do not involve emotion at all."""
    bad = tmp_path / "emotion.onnx"
    bad.write_bytes(b"not a model")
    monkeypatch.setenv("FACE_EMOTION_MODEL_PATH", str(bad))

    assert verify._emotion_classifier() is None
    assert "would not load" in capsys.readouterr().out


def test_the_emotion_check_claims_plumbing_and_not_accuracy():
    """The guard that keeps this honest.

    FER+ has no ground truth you can assert from a chair, and its accuracy on
    this product's users — children, and children with learning disabilities —
    is the documented weakness no self-check addresses. A check that read as
    validating emotion would be worse than no check, which is the trap step 2
    of this script fell into for a fortnight.
    """
    source = SCRIPT.read_text(encoding="utf-8")

    assert "plumbing only" in source
    assert "says nothing about whether the " in source


# ── the cross-check's own verdict ───────────────────────────────────────────
#
# These exist because a `NameError` shipped in the branch below: `\n` from a
# botched edit, in the one path that reports a real failure. Every branch here
# was previously reachable only by having the failure on real hardware, so the
# happy-path hardware run that this script's own record is built on could not
# have caught it, and neither could CI.


def _agree(frames=60, mesh=60, haar=60, crops=60, crops_refused=0, labels=60,
           refusals=None, confidences=(0.9,), emotion_available=True):
    return {"frames": frames, "mesh": mesh, "haar": haar, "crops": crops,
            "crops_refused": crops_refused, "labels": labels,
            "emotion_refusals": refusals or {}, "confidences": list(confidences),
            "emotion_available": emotion_available}


def test_every_crop_refused_reports_rather_than_crashing(capsys):
    """The branch that shipped broken. `to_gray64` will not upsample, so at a
    framing where every Haar box is under 64x64 the emotion channel records
    nothing — and this is the only thing that would say so."""
    code = verify._cross_check_verdict(_agree(crops=0, crops_refused=60, labels=0))
    out = capsys.readouterr().out

    assert code == 1
    assert "every crop was refused as too small" in out
    assert "will not upsample" in out


def test_a_model_that_errors_on_a_real_crop_fails():
    """`inference_failed` is a broken install. `low_confidence` is the model
    doing its job, and must not fail the run."""
    broken = verify._cross_check_verdict(
        _agree(refusals={"inference_failed": 60}, labels=0, confidences=()))
    unsure = verify._cross_check_verdict(
        _agree(refusals={"low_confidence": 60}, labels=0, confidences=()))

    assert broken == 1
    assert unsure is None, "an unsure classifier is not a broken one"


def test_a_haar_miss_where_the_mesh_saw_a_face_fails(capsys):
    code = verify._cross_check_verdict(_agree(mesh=60, haar=0))

    assert code == 1
    assert "not the lighting" in capsys.readouterr().out


def test_no_frames_is_inconclusive_not_a_failure():
    assert verify._cross_check_verdict(_agree(frames=0, mesh=0, haar=0)) == 2


def test_neither_detector_seeing_a_face_is_not_a_failure(capsys):
    """Lighting and framing, not a broken table — and the emotion half has
    nothing to say without a box."""
    code = verify._cross_check_verdict(_agree(mesh=0, haar=0, crops=0, labels=0))

    assert code is None
    assert "check lighting and framing" in capsys.readouterr().out


def test_a_missing_emotion_model_skips_and_carries_on(capsys):
    code = verify._cross_check_verdict(_agree(emotion_available=False))
    out = capsys.readouterr().out

    assert code is None
    assert "SKIP" in out
    assert "plumbing only" not in out, "claimed a check it did not run"


def test_the_happy_path_reports_the_confidence_range_and_the_caveat(capsys):
    code = verify._cross_check_verdict(_agree(confidences=(0.94, 0.99)))
    out = capsys.readouterr().out

    assert code is None
    assert "0.94-0.99" in out
    assert "plumbing only" in out


def test_a_classifier_that_declines_to_label_is_still_a_pass(capsys):
    """Crops reaching the model is what this checks. Declining to label them is
    a reading it is entitled to make, and failing on it would make the check
    depend on the subject pulling a face the model recognises."""
    code = verify._cross_check_verdict(
        _agree(labels=0, confidences=(), refusals={"low_confidence": 60}))

    assert code is None
    assert "declined to label" in capsys.readouterr().out
