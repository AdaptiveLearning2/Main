"""The guards on the one script that writes a face to disk.

The camera loop is not tested — it needs a webcam, like `FaceLocator` itself.
What is tested is the part that decides *where* frames may go and what happens
to them afterwards, because that is the half carrying the promise made in the
script's own docstring: a consenting adult, one measurement, deleted after.

A path check is worth a test in a way most path checks are not. Everything else
this project writes is committable by design; this is the one artefact that must
never be, and `git add -A` does not ask.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

SCRIPT = (pathlib.Path(__file__).resolve().parents[1]
          / "scripts" / "capture_face_video_ecg.py")


def _module():
    spec = importlib.util.spec_from_file_location("capture_face_video_ecg", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


capture = _module()


# ── where frames may be written ─────────────────────────────────────────────

def test_a_path_inside_the_repository_is_refused():
    """`git add -A` does not ask. A capture that lands anywhere git can reach
    is one command away from being published, permanently, in a repository
    whose whole point is that it holds no footage."""
    inside = capture.repo_root() / "EEGResearch" / "tests" / "fixtures" / "session1"

    with pytest.raises(SystemExit) as exc:
        capture.refuse_if_inside_repo(inside)
    assert "refusing to write inside the repository" in str(exc.value)


def test_the_repo_root_itself_is_refused():
    with pytest.raises(SystemExit):
        capture.refuse_if_inside_repo(capture.repo_root() / "x")


def test_a_path_outside_the_repository_is_allowed(tmp_path):
    capture.refuse_if_inside_repo(tmp_path / "session1")     # must not raise


def test_a_sibling_directory_is_not_mistaken_for_the_repo(tmp_path):
    """Prefix matching would reject `/work/AdaptiveLearning-captures` because it
    starts with the repo path. The check is on path components, not strings."""
    sibling = pathlib.Path(str(capture.repo_root()) + "-captures") / "s1"

    capture.refuse_if_inside_repo(sibling)                   # must not raise


# ── deletion ────────────────────────────────────────────────────────────────

def test_delete_removes_the_frames_and_keeps_the_header(tmp_path, capsys):
    """The header has no face in it and is the record that a capture happened
    and was cleaned up. That is worth more than the tidiness of removing it —
    a deleted capture with no trace is indistinguishable from one that was
    never cleaned up at all."""
    prefix = tmp_path / "session1"
    (tmp_path / "session1.npy").write_bytes(b"frames")
    (tmp_path / "session1.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "session1.json").write_text(json.dumps({"nominal_fps": 30}),
                                            encoding="utf-8")

    capture.delete(str(prefix))

    assert not (tmp_path / "session1.npy").exists()
    assert not (tmp_path / "session1.jsonl").exists()
    header = json.loads((tmp_path / "session1.json").read_text(encoding="utf-8"))
    assert header["nominal_fps"] == 30
    assert "frames_deleted_at" in header, "no record that the frames went"


def test_delete_is_safe_to_run_twice(tmp_path, capsys):
    """Someone unsure whether they already deleted a capture will run it again.
    That must not be an error, or they will learn to ignore the output."""
    prefix = tmp_path / "gone"

    capture.delete(str(prefix))

    assert "nothing found" in capsys.readouterr().out


# ── the window ──────────────────────────────────────────────────────────────

def test_a_capture_too_short_to_produce_a_window_is_refused():
    """The model's window is 160 frames. A capture shorter than that yields no
    measurement at all, and finding that out after recording — and after
    deleting, since the frames should not be kept — wastes the session."""
    assert capture.MIN_SECONDS >= 160 / 30.0
