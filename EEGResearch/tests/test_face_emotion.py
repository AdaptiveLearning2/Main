"""Tests FER+ classification and model provisioning, without onnxruntime.

The session is injected, so the confidence gate, the degraded state and the
tensor shaping are all exercised on a machine with no ONNX Runtime -- CI's
state. Downloads are tested against local files rather than the network,
since fetching 35 MB would be slow, flaky, and fail offline for the wrong
reason.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest

from src.app.services.face_emotion import (
    EMOTION_LABELS,
    FACE_INPUT,
    FAILURE_TOLERANCE,
    MIN_CONFIDENCE,
    MODEL_BYTES,
    MODEL_REVISION,
    MODEL_SHA256,
    MODEL_URL,
    EmotionClassifier,
    ensure_model,
    softmax,
    to_tensor,
    verify,
)


class FakeSession:
    """Returns fixed logits, or raises."""

    def __init__(self, logits=None, raises=None, input_name="Input3"):
        self.logits = logits
        self.raises = raises
        self._input_name = input_name
        self.calls = 0

    def get_inputs(self):
        return [type("I", (), {"name": self._input_name})()]

    def run(self, _outputs, feed):
        self.calls += 1
        if self.raises:
            raise self.raises
        return [np.array(self.logits, dtype=np.float32)]


def _crop(value: float = 120.0) -> np.ndarray:
    return np.full((FACE_INPUT, FACE_INPUT), value, dtype=np.float32)


def _logits_favouring(label: str, margin: float = 6.0) -> list[float]:
    out = [0.0] * len(EMOTION_LABELS)
    out[EMOTION_LABELS.index(label)] = margin
    return out


# -- provenance --

def test_the_model_url_is_pinned_to_a_commit_not_to_main():
    """`resolve/main/` is a moving reference -- the bytes a student's laptop
    fetches next term need not match the bytes reviewed today."""
    assert "/resolve/main/" not in MODEL_URL
    assert MODEL_REVISION in MODEL_URL
    assert len(MODEL_REVISION) == 40
    assert MODEL_URL.startswith("https://")


def test_the_checksum_is_a_full_sha256():
    assert len(MODEL_SHA256) == 64
    int(MODEL_SHA256, 16)


# -- arithmetic --

def test_softmax_sums_to_one_and_favours_the_largest():
    out = softmax(np.array([1.0, 3.0, 2.0]))
    assert out.sum() == pytest.approx(1.0)
    assert int(np.argmax(out)) == 1


def test_softmax_survives_extreme_logits():
    """Subtracting the max stops exp() overflowing to inf and the whole
    distribution becoming nan, which would otherwise surface as a confident
    wrong label rather than an error."""
    out = softmax(np.array([1e4, 0.0, -1e4]))
    assert np.isfinite(out).all()
    assert out.sum() == pytest.approx(1.0)


def test_tensor_shape_matches_the_model_input():
    assert to_tensor(_crop()).shape == (1, 1, FACE_INPUT, FACE_INPUT)


def test_a_wrongly_sized_crop_is_rejected_rather_than_reshaped():
    with pytest.raises(ValueError):
        to_tensor(np.zeros((32, 32)))


# -- classification --

def test_classifies_a_confident_face():
    clf = EmotionClassifier(Path("unused"), session=FakeSession(_logits_favouring("happy")))
    out = clf.classify(_crop())
    assert out.label == "happy"
    assert out.trusted and out.rejected_by is None
    assert out.confidence > MIN_CONFIDENCE


def test_low_confidence_reports_the_label_but_marks_it_untrusted():
    """The label can still show a trend, but must not be counted as a
    trusted reading."""
    flat = [0.0] * len(EMOTION_LABELS)
    clf = EmotionClassifier(Path("unused"), session=FakeSession(flat))
    out = clf.classify(_crop())
    assert out.label in EMOTION_LABELS
    assert not out.trusted
    assert out.rejected_by == "low_confidence"


def test_no_face_is_distinguishable_from_an_unsure_one():
    clf = EmotionClassifier(Path("unused"), session=FakeSession(_logits_favouring("sad")))
    assert clf.classify(None).rejected_by == "no_face"


def test_control_flow_matches_a_field_not_a_sentence():
    """A caller distinguishing a broken model from an unsure one needs a
    structured field, not a prose message to parse."""
    clf = EmotionClassifier(Path("unused"), session=FakeSession(raises=RuntimeError("boom")))
    out = clf.classify(_crop())
    assert out.rejected_by == "inference_failed"
    assert isinstance(out.reason, str) and out.reason


# -- distinguishing broken from merely untrusted --

def test_a_broken_session_becomes_degraded_not_merely_untrusted():
    """A crashing session must be distinguishable from a calm, low-confidence
    face -- both would otherwise look like `trusted: false`, hiding a broken
    model behind a plausible reading."""
    clf = EmotionClassifier(Path("unused"), session=FakeSession(raises=RuntimeError("boom")))
    assert not clf.degraded

    for _ in range(FAILURE_TOLERANCE):
        clf.classify(_crop())

    assert clf.degraded
    meta = clf.get_meta()
    assert meta["emotion_degraded"] is True
    assert "boom" in meta["emotion_last_error"]


def test_one_failure_is_not_degraded():
    """A single bad crop is ordinary; only a run of failures is a broken
    session."""
    clf = EmotionClassifier(Path("unused"), session=FakeSession(raises=ValueError("x")))
    clf.classify(_crop())
    assert not clf.degraded
    assert FAILURE_TOLERANCE > 1


def test_a_recovery_clears_the_failure_run():
    session = FakeSession(raises=RuntimeError("boom"))
    clf = EmotionClassifier(Path("unused"), session=session)
    for _ in range(FAILURE_TOLERANCE - 1):
        clf.classify(_crop())

    session.raises = None
    session.logits = _logits_favouring("neutral")
    clf.classify(_crop())

    assert not clf.degraded


def test_a_wrong_output_shape_counts_as_a_failure_not_a_low_score():
    """A substituted or mismatched model returns the wrong number of outputs.
    Treating that as low confidence would give endless untrusted readings
    instead of a degraded flag naming the real problem."""
    clf = EmotionClassifier(Path("unused"), session=FakeSession([1.0, 2.0, 3.0]))
    for _ in range(FAILURE_TOLERANCE):
        out = clf.classify(_crop())
    assert out.rejected_by == "inference_failed"
    assert clf.degraded


def test_meta_reports_the_pinned_revision():
    clf = EmotionClassifier(Path("unused"), session=FakeSession(_logits_favouring("happy")))
    assert clf.get_meta()["emotion_model_revision"] == MODEL_REVISION[:8]


# -- provisioning --

def test_verify_rejects_a_file_of_the_wrong_size(tmp_path):
    p = tmp_path / "m.onnx"
    p.write_bytes(b"nowhere near 35 MB")
    assert not verify(p)


def test_verify_rejects_a_missing_file(tmp_path):
    assert not verify(tmp_path / "absent.onnx")


def test_an_unverified_file_is_deleted_rather_than_left_for_the_next_run(tmp_path):
    """The next run checks existence before anything else, so a partial or
    substituted file left on disk would be trusted."""
    p = tmp_path / "m.onnx"
    p.write_bytes(b"corrupt")
    with pytest.raises(FileNotFoundError):
        ensure_model(p, allow_download=False)
    assert not p.exists()


def test_ensure_model_accepts_a_file_that_hashes_correctly(tmp_path, monkeypatch):
    """Constructed rather than downloaded, since fetching 35 MB would be
    slow, flaky, and fail for the wrong reason offline."""
    import src.app.services.face_emotion as fe

    payload = b"pretend onnx" * 100
    p = tmp_path / "m.onnx"
    p.write_bytes(payload)

    monkeypatch.setattr(fe, "MODEL_BYTES", len(payload))
    monkeypatch.setattr(fe, "MODEL_SHA256", hashlib.sha256(payload).hexdigest())

    assert fe.verify(p)
    assert fe.ensure_model(p, allow_download=False) == p


def test_loading_an_unverified_model_is_refused(tmp_path):
    """Even if provisioning is bypassed, the classifier must not hand an
    unverified file to onnxruntime."""
    p = tmp_path / "m.onnx"
    p.write_bytes(b"not the model")
    with pytest.raises(ValueError, match="unverified"):
        EmotionClassifier(p)


def test_no_camera_dependency_is_imported():
    """Importing this module must not drag in onnxruntime or cv2.

    Checked in a subprocess, since `sys.modules` is process-global: once any
    other test in the run has imported either one, an in-process assertion
    would measure test ordering instead of this module.
    """
    import subprocess
    import sys
    import textwrap

    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            import sys
            sys.path.insert(0, sys.argv[1])
            import src.app.services.face_emotion  # noqa: F401
            leaked = [m for m in ("onnxruntime", "cv2") if m in sys.modules]
            assert not leaked, f"importing face_emotion pulled in {leaked}"
            print("clean")
        """),
        # Pass the repo root as an argument rather than relying on cwd, which
        # would only work when pytest runs from EEGResearch/.
        str(Path(__file__).resolve().parents[1])],
        capture_output=True, text=True,
    )
    assert "clean" in result.stdout, result.stderr


# -- cropping --

def test_a_crop_is_box_averaged_not_sampled():
    """A face crop is typically 150-250 px, so nearest-neighbour would
    discard most pixels and depend on where the sample grid lands -- which
    shifts with every small head movement, adding noise to a classifier
    already sampled only a few times a second."""
    from src.app.services.face_emotion import to_gray64

    ramp = np.zeros((480, 640, 3), dtype=np.uint8)
    ramp[:, :, 0] = np.linspace(0, 255, 640).astype(np.uint8)

    out = to_gray64(ramp, (100, 50, 300, 300))
    column_means = out.mean(axis=0)
    assert np.all(np.diff(column_means) > 0), "the gradient was not preserved"


def test_a_crop_smaller_than_the_model_input_is_refused():
    """Reaching 64x64 from a smaller crop means upsampling -- inventing
    detail the sensor never captured, which the classifier would then label
    with full confidence. A distant or half-cropped face should be treated
    as a missing measurement instead."""
    from src.app.services.face_emotion import to_gray64

    frame = np.full((480, 640, 3), 128, dtype=np.uint8)
    assert to_gray64(frame, (10, 10, FACE_INPUT - 1, 200)) is None
    assert to_gray64(frame, (10, 10, 200, FACE_INPUT - 1)) is None
    assert to_gray64(frame, (10, 10, FACE_INPUT, FACE_INPUT)) is not None


def test_a_crop_never_produces_nan():
    """Whatever the geometry, every output cell must average at least one
    real pixel. A nan would reach softmax and come back as a uniform
    distribution -- a confident-looking 12.5% for every emotion."""
    from src.app.services.face_emotion import to_gray64

    frame = np.random.default_rng(0).integers(0, 255, (480, 640, 3), dtype=np.uint8)
    for box in [(0, 0, 64, 64), (0, 0, 65, 67), (100, 100, 128, 129),
                (600, 440, 200, 200), (-20, -20, 200, 200)]:
        out = to_gray64(frame, box)
        if out is not None:
            assert np.isfinite(out).all(), f"nan from box {box}"
