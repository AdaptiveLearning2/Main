"""The verdict logic of `scripts/verify_landmarks.py`.

The camera-reading loop isn't tested here; what matters is the half that turns
numbers into a pass/fail decision, since that decision is a safety gate against
a mirrored landmark index table. A false PASS here is worse than no script at
all -- it would report "verified against a real face" when it wasn't.
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
    positive: the frame isn't mirrored, so your own left is the image right,
    and both are measured in image coordinates."""
    code = verify._verdict(
        _step(),
        _step(gaze_x=+0.6),
        _step(yaw=+25.0),
    )

    assert code == 0


def test_gaze_tracking_the_wrong_way_fails():
    """gaze.x negative while looking toward your own left means the iris is
    moving the wrong way -- a mirrored frame, or an iris index that isn't the
    iris. Not a left/right label swap: `gaze` averages both eyes in image
    coordinates, so permuting the labels is invisible to this check
    (`test_gaze_cannot_see_a_left_right_swap` in test_face_geometry covers
    that separately)."""
    code = verify._verdict(
        _step(),
        _step(gaze_x=-0.6),
        _step(yaw=+25.0),
    )

    assert code == 1


def test_an_inverted_pose_fit_fails_independently_of_the_eyes():
    """Gaze (iris offsets) and yaw (a handed model fit) are independent, so a
    check that only looked at gaze would pass a face whose yaw is backwards --
    gaze can't detect a mirror at all."""
    code = verify._verdict(
        _step(),
        _step(gaze_x=+0.6),
        _step(yaw=-25.0),
    )

    assert code == 1


def test_a_movement_too_small_to_read_is_not_a_pass():
    """Treating "did not move" as success would mark a table verified without
    it ever having been exercised."""
    code = verify._verdict(
        _step(),
        _step(gaze_x=+0.02),
        _step(yaw=+1.0),
    )

    assert code == 1


def test_no_face_is_inconclusive_not_a_failure_of_the_table():
    """A camera problem isn't evidence about the index mapping; reporting it
    as a failure would send someone editing a table that may be fine."""
    code = verify._verdict(_step(frames=0), _step(), _step())

    assert code == 2


def test_a_crooked_sitter_still_passes_the_square_on_step():
    """Nobody sits like a tripod -- a tolerance tight enough to fail a real
    person makes the script useless: dismissed rather than acted on."""
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
    """A refused window has no value; averaging it as 0.0 would drag a real
    look back toward centre, reporting "barely moved" for someone who moved."""
    assert verify._median([None, -0.6, None, -0.5]) == pytest.approx(-0.55)
    assert verify._median([None, None]) is None
    assert verify._median([]) is None


def test_a_failed_square_on_skips_the_yaw_check_but_not_the_gaze_check(capsys):
    """Gaze and yaw depend on different things, so gating them together would
    throw away a usable answer. `gaze` comes from eye/iris landmarks alone and
    never touches the rotation fit, so it stays valid even when square-on
    fails. `yaw` comes straight from that fit, so it's meaningless once
    square-on has already failed.
    """
    code = verify._verdict(
        _step(yaw=60.0),               # square-on fails: pose fit is wrong
        _step(gaze_x=+0.6),            # eyes are still readable
        _step(yaw=-40.0),              # would otherwise print PASS/FAIL
    )
    out = capsys.readouterr().out

    assert code == 1
    assert "SKIP" in out and "yaw comes from the pose fit" in out
    assert "looking left drives gaze.x positive" in out


def test_nothing_printed_needs_more_than_ascii():
    """A Windows console can't encode box-drawing characters or arrows, so a
    non-ASCII string in output would crash with UnicodeEncodeError before the
    check even runs.

    Checked against `ascii`, not `cp1252` -- cp1252 contains an em dash, which
    is weaker than what the script needs: cmd.exe codepages like cp437/cp850
    don't contain it either.

    pytest captures output through a UTF-8 buffer, so this reads the source
    directly instead of capturing a run.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    body = source.split('"""', 2)[2]      # docstring itself is never printed
    body.encode("ascii")                  # raises if any runtime string can't


# ── the preview ────────────────────────────────────────────────────────────

def test_the_preview_writes_nothing_to_disk():
    """The script promises to record nothing; a frame is already decoded and
    drawn on, so saving one is a single call away. Cheap and coarse on
    purpose: it can't prove absence, but it fails the moment someone reaches
    for the obvious save call.
    """
    source = SCRIPT.read_text(encoding="utf-8")

    for forbidden in ("imwrite", "imencode", "VideoWriter"):
        assert forbidden not in source, f"{forbidden} would persist a frame"


def test_the_preview_sees_frames_with_no_face_rather_than_freezing():
    """A stretch with no face usually means the subject left the frame, which
    they most need to see. Drawing only when a face is found would freeze the
    preview on the last good frame, looking like a hang."""
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


# ── the emotion path ───────────────────────────────────────────────────────

def test_the_emotion_check_is_skipped_rather_than_failed_without_a_model(tmp_path,
                                                                        monkeypatch):
    """A gaze-only install legitimately has no FER+ model, since it's a
    separate 35 MB download. Failing here would tie the landmark check to a
    channel it isn't about."""
    monkeypatch.setenv("FACE_EMOTION_MODEL_PATH", str(tmp_path / "absent.onnx"))

    assert verify._emotion_classifier() is None


def test_a_model_that_will_not_load_is_reported_not_raised(tmp_path, monkeypatch,
                                                           capsys):
    """A corrupt or truncated model must not take the whole camera check down
    -- the three main steps don't involve emotion at all."""
    bad = tmp_path / "emotion.onnx"
    bad.write_bytes(b"not a model")
    monkeypatch.setenv("FACE_EMOTION_MODEL_PATH", str(bad))

    assert verify._emotion_classifier() is None
    assert "would not load" in capsys.readouterr().out


def test_the_emotion_check_claims_plumbing_and_not_accuracy():
    """FER+ has no ground truth you can assert from a chair, and its accuracy
    on this product's users -- children, including those with learning
    disabilities -- is a documented weakness no self-check addresses. A check
    that reads as validating emotion would be worse than no check.
    """
    source = SCRIPT.read_text(encoding="utf-8")

    assert "plumbing only" in source
    assert "says nothing about whether the " in source


# ── the cross-check's own verdict ───────────────────────────────────────────
#
# These branches were previously reachable only by reproducing the failure on
# real hardware, so neither a happy-path hardware run nor CI could catch a bug
# in them.


def _agree(frames=60, mesh=60, haar=60, crops=60, crops_refused=0, labels=60,
           refusals=None, confidences=(0.9,), emotion_available=True):
    return {"frames": frames, "mesh": mesh, "haar": haar, "crops": crops,
            "crops_refused": crops_refused, "labels": labels,
            "emotion_refusals": refusals or {}, "confidences": list(confidences),
            "emotion_available": emotion_available}


def test_every_crop_refused_reports_rather_than_crashing(capsys):
    """`to_gray64` will not upsample, so if every Haar box is under 64x64 the
    emotion channel records nothing -- this check is what reports that."""
    code = verify._cross_check_verdict(_agree(crops=0, crops_refused=60, labels=0))
    out = capsys.readouterr().out

    assert code == 1
    assert "every crop was refused as too small" in out
    assert "will not upsample" in out


def test_a_model_that_errors_on_a_real_crop_fails():
    """`inference_failed` means a broken install. `low_confidence` is the
    model doing its job, and must not fail the run."""
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
    """A likely lighting or framing issue, not a broken table -- the emotion
    half has nothing to say without a box."""
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
    """This checks that crops reach the model. Declining to label them is a
    valid reading, not a failure -- the check must not depend on the subject
    pulling a face the model recognizes."""
    code = verify._cross_check_verdict(
        _agree(labels=0, confidences=(), refusals={"low_confidence": 60}))

    assert code is None
    assert "declined to label" in capsys.readouterr().out
