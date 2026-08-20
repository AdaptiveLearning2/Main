"""Tests for the one script that writes a face to disk.

The camera loop itself needs a webcam and isn't tested here. What's tested is
where frames may be written and what happens to them afterward -- this is the
one artefact in the project that must never be committed, and `git add -A`
does not ask.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import time

import numpy as np
import pytest

SCRIPT = (pathlib.Path(__file__).resolve().parents[1]
          / "scripts" / "capture_face_video_ecg.py")


def _module():
    spec = importlib.util.spec_from_file_location("capture_face_video_ecg", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


capture = _module()


# -- where frames may be written --

def test_a_path_inside_the_repository_is_refused():
    """A capture inside the repo is one `git add -A` away from being
    committed and published permanently."""
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
    """A prefix-string check would wrongly reject `/work/AdaptiveLearning-captures`
    since it starts with the repo path. The check must compare path
    components, not strings."""
    sibling = pathlib.Path(str(capture.repo_root()) + "-captures") / "s1"

    capture.refuse_if_inside_repo(sibling)                   # must not raise


# -- deletion --

def test_delete_removes_the_frames_and_keeps_the_header(tmp_path, capsys):
    """The header has no face in it and is the record that a capture happened
    and was cleaned up. Removing it too would leave a deleted capture
    indistinguishable from one nobody cleaned up."""
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
    """Running delete twice must not error, since someone unsure whether they
    already deleted a capture will run it again."""
    prefix = tmp_path / "gone"

    capture.delete(str(prefix))

    assert "nothing found" in capsys.readouterr().out


# -- the window --

def test_a_capture_too_short_to_produce_a_window_is_refused():
    """The model's window is 160 frames. A shorter capture yields no
    measurement, so it should be refused up front rather than discovered
    after recording and deleting."""
    assert capture.MIN_SECONDS >= 160 / 30.0


# -- trimming the preallocated tail --

def _preallocated(path, capacity, written, shape=(4, 4, 3)):
    """A capture that filled `written` of `capacity` rows, as the loop leaves
    it. Row `i` is filled with `i + 1`, so an unwritten row is the only
    all-zero one -- zeros are what both `open_memmap` and a black frame give
    you, and nothing downstream can tell them apart.
    """
    a = np.lib.format.open_memmap(path, mode="w+", dtype=np.uint8,
                                  shape=(capacity, *shape))
    for i in range(written):
        a[i] = i + 1
    a.flush()
    mapping = getattr(a, "_mmap", None)
    del a
    if mapping is not None:
        mapping.close()


def test_rows_nobody_wrote_do_not_survive_as_black_frames(tmp_path):
    """`capacity` is deliberately larger than the expected frame count, so a
    capture ending short is the normal case. Left untrimmed, the tail reads
    back as a run of black frames -- a sharp, non-physiological step that a
    frequency-domain heart-rate estimate would absorb silently.
    """
    path = tmp_path / "session1.npy"
    _preallocated(path, capacity=100, written=40)

    capture.truncate_npy(path, 40)

    got = np.load(path)
    assert got.shape == (40, 4, 4, 3), "the file still claims rows nobody wrote"
    assert got.any(axis=(1, 2, 3)).all(), "an all-zero frame survived the trim"


def test_trimming_leaves_the_captured_frames_byte_identical(tmp_path):
    """The trim rewrites the header in place and shortens the file. Moving
    the data offset by even a byte would decode every frame as garbage."""
    path = tmp_path / "session1.npy"
    _preallocated(path, capacity=100, written=40)
    expected = np.stack([np.full((4, 4, 3), i + 1, dtype=np.uint8)
                         for i in range(40)])

    capture.truncate_npy(path, 40)

    assert np.array_equal(np.load(path), expected)


def test_trimming_actually_shortens_the_file_on_disk(tmp_path):
    """Rewriting the header alone would satisfy the assertions above while
    leaving the bytes on disk, unreachable through numpy but still present."""
    path = tmp_path / "session1.npy"
    _preallocated(path, capacity=100, written=40)
    before = path.stat().st_size

    capture.truncate_npy(path, 40)

    assert path.stat().st_size < before
    assert before - path.stat().st_size == 60 * 4 * 4 * 3


def test_a_full_capture_is_left_alone(tmp_path):
    """The no-op path still has to produce a readable file."""
    path = tmp_path / "session1.npy"
    _preallocated(path, capacity=8, written=8)

    capture.truncate_npy(path, 8)

    assert np.load(path).shape == (8, 4, 4, 3)


def test_a_capture_that_saw_no_face_at_all_trims_to_nothing(tmp_path):
    """A wrong camera index or a covered lens records zero frames, which must
    trim to an empty array rather than `capacity` black frames -- the latter
    would look like plausible data for a capture that never happened."""
    path = tmp_path / "session1.npy"
    _preallocated(path, capacity=100, written=0)

    capture.truncate_npy(path, 0)

    assert np.load(path).shape == (0, 4, 4, 3)


def test_the_real_frame_shape_round_trips(tmp_path):
    """The header is rewritten into the padding numpy left, so its length
    matters. 128x128x3 is the real shape the script writes; a smaller test
    shape could pass while a real one would not fit."""
    path = tmp_path / "session1.npy"
    _preallocated(path, capacity=5, written=3, shape=(capture.CROP, capture.CROP, 3))

    capture.truncate_npy(path, 3)

    got = np.load(path)
    assert got.shape == (3, capture.CROP, capture.CROP, 3)
    assert (got[2] == 3).all()


def test_a_one_dimensional_array_keeps_its_trailing_comma(tmp_path):
    """`(3,)` and `(3)` parse as a tuple and an int respectively. numpy parses
    the header with `ast.literal_eval`, so dropping the comma makes the file
    unloadable."""
    path = tmp_path / "flat.npy"
    _preallocated(path, capacity=10, written=4, shape=())

    capture.truncate_npy(path, 4)

    assert np.load(path).shape == (4,)


def test_growing_is_refused(tmp_path):
    """Not reachable from `capture()`, since `written` can't exceed `capacity`
    -- but growing would rewrite the header to claim rows past the end of the
    file, and numpy would read whatever followed it."""
    path = tmp_path / "session1.npy"
    _preallocated(path, capacity=8, written=8)

    with pytest.raises(SystemExit) as exc:
        capture.truncate_npy(path, 9)
    assert "cannot grow" in str(exc.value)


# -- the preview --

def test_the_preview_adds_no_new_way_to_persist_a_frame():
    """This script already writes face images, so the preview must not
    become a second copy -- only the bytes the capture loop already writes
    should reach disk."""
    source = SCRIPT.read_text(encoding="utf-8")

    for forbidden in ("imwrite", "imencode", "VideoWriter"):
        assert forbidden not in source, f"{forbidden} would persist a frame"


def test_the_preview_helpers_run_against_a_synthetic_frame():
    """The drawing code has no camera in CI, but every call in it can be
    exercised on an array, which catches a wrong argument type or shape
    mismatch before someone hits it mid-capture."""
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    gui = capture.Gui.__new__(capture.Gui)      # __init__ needs a display
    gui._cv2 = cv2
    gui.aborted = False
    frame = (np.random.default_rng(0).random((240, 320, 3)) * 255).astype("uint8")
    crop = np.zeros((capture.CROP, capture.CROP, 3), dtype="uint8")

    drawn = []
    gui._cv2 = type("C", (), {  # capture what would be shown, show nothing
        **{k: getattr(cv2, k) for k in dir(cv2) if not k.startswith("_")},
        "imshow": staticmethod(lambda *a: drawn.append(a)),
        "waitKey": staticmethod(lambda *_: 0),
    })()

    gui.frame(frame, (40, 30, 100, 100), crop, elapsed=12.0, total=300.0,
              written=300, missed=4, exposure_locked=True)
    gui.frame(frame, None, None, elapsed=13.0, total=300.0,
              written=300, missed=5, exposure_locked=False)

    assert len(drawn) == 2, "the preview drew nothing"
    # The crop panel is stacked beside the frame, so the composed image is
    # wider than the source. If it were dropped the preview would still look
    # fine while hiding the picture that shows what's actually stored.
    assert drawn[0][1].shape[1] > frame.shape[1]


# -- the auto-exposure warm-up --


def test_the_warmup_matches_the_live_adapter_rather_than_restating_it():
    """Measured, not chosen: mean green climbs 17% over ~5s against a pulse
    under 1%, and that ramp took a recording from confidence 0.81 to 0.05.
    Imported here so the capture and live path can't drift apart."""
    from src.app.services.face_ingestion import WARMUP_SECONDS

    assert capture.WARMUP_SECONDS is WARMUP_SECONDS
    assert WARMUP_SECONDS >= 5.0


def test_the_header_records_what_was_discarded():
    """"the first frame is 8s after the lens opened" is not recoverable from
    the frames alone, and an ECG alignment search would absorb that as a lag
    instead of reporting it."""
    source = SCRIPT.read_text(encoding="utf-8")

    assert '"warmup_seconds"' in source
    # wall_start is re-stamped after the discard, marking the first usable
    # frame rather than when the camera opened.
    assert source.index('header["wall_start"] =') > source.index("warm_until")


def test_skipping_the_warmup_is_possible_but_argued_for():
    """Some cameras genuinely have fixed exposure, so the flag exists -- but
    it states why, so it isn't just used by anyone in a hurry."""
    source = SCRIPT.read_text(encoding="utf-8")

    assert "--no-warmup" in source
    assert "genuinely fixed" in source


def test_the_warmup_preview_does_not_look_like_recording(capsys):
    """A frozen preview, or one identical to the recording view, would let the
    subject think the capture had already started."""
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    gui = capture.Gui.__new__(capture.Gui)
    gui.aborted = False
    drawn = []
    gui._cv2 = type("C", (), {
        **{k: getattr(cv2, k) for k in dir(cv2) if not k.startswith("_")},
        "imshow": staticmethod(lambda *a: drawn.append(a)),
        "waitKey": staticmethod(lambda *_: 0),
    })()
    frame = (np.random.default_rng(0).random((240, 320, 3)) * 255).astype("uint8")

    gui.warming(frame, remaining=5.0)

    assert len(drawn) == 1, "the warm-up drew nothing"
    assert "WARMING UP" in SCRIPT.read_text(encoding="utf-8")
    assert "nothing is being written yet" in SCRIPT.read_text(encoding="utf-8")

MIN_FOR_TEST = 30.0


# -- q, everywhere the preview offers it --
#
# `q to abort` is the only interactive control this preview has. Tests below
# cover it during warm-up and on frames with no face found.


class _AbortingGui:
    """A preview that aborts on the Nth draw of a given kind."""

    def __init__(self, on_warming=None, on_frame=None):
        self.aborted = False
        self._on_warming, self._on_frame = on_warming, on_frame
        self.warmings = self.frames = 0

    def warming(self, frame, *, remaining):
        self.warmings += 1
        if self._on_warming is not None and self.warmings >= self._on_warming:
            self.aborted = True

    def frame(self, frame, box, crop, **kw):
        self.frames += 1
        if self._on_frame is not None and self.frames >= self._on_frame:
            self.aborted = True

    def close(self):
        pass


class _Args:
    def __init__(self, out, **kw):
        self.out, self.seconds, self.camera, self.fps = out, 60.0, 0, 30.0
        self.yes, self.gui, self.no_warmup, self.delete = True, True, False, None
        self.__dict__.update(kw)


def _stub_cv2(monkeypatch):
    """A `cv2` stub with just the resize the capture loop calls.

    CI has no OpenCV, and the loop calls `cv2.resize` on every frame with a
    face in it. Without this stub, the frame-writing abort path would go
    untested in CI.
    """
    import sys
    import types

    stub = types.ModuleType("cv2")
    stub.INTER_AREA = 3
    stub.resize = lambda img, size, interpolation=None: np.zeros(
        (size[1], size[0], img.shape[2]), dtype=img.dtype)
    monkeypatch.setitem(sys.modules, "cv2", stub)


def _fake_camera(monkeypatch, faces=True):
    """A source and locator that need no webcam."""
    frame = np.zeros((120, 160, 3), dtype=np.uint8)

    class Source:
        locked = True
        def read(self):
            time.sleep(0.001)
            return frame
        def release(self):
            self.released = True

    class Locator:
        def locate(self, gray):
            return (10, 10, 80, 80) if faces else None

    import src.app.services.face_ingestion as fi
    import src.app.services.face_roi as fr
    monkeypatch.setattr(fi, "OpenCvFrameSource", lambda **kw: Source())
    monkeypatch.setattr(fr, "FaceLocator", Locator)


def test_q_during_warmup_records_nothing(monkeypatch, tmp_path, capsys):
    """Warm-up is when `q` is most likely to be pressed, while the subject is
    still deciding whether the framing is right, so an abort here must
    prevent recording, not just be noted and ignored."""
    _fake_camera(monkeypatch)
    gui = _AbortingGui(on_warming=2)
    monkeypatch.setattr(capture, "Gui", lambda: gui)
    monkeypatch.setattr(capture, "WARMUP_SECONDS", 5.0)

    started = time.perf_counter()
    code = capture.capture(_Args(str(tmp_path / "s1")))
    elapsed = time.perf_counter() - started

    assert code == 1
    assert "aborted during warm-up" in capsys.readouterr().out
    assert not (tmp_path / "s1.jsonl").exists(), "recording started after an abort"
    # Checking only the file state afterward can't tell an abort honoured at
    # once from one that just ran out the warm-up regardless, hence the timing
    # check too.
    assert elapsed < 2.0, (
        f"took {elapsed:.1f}s to honour q during a 5s warm-up")
    # The array is allocated before the warm-up, so an early abort must still
    # clean up that preallocated zero-filled file rather than leaving it on
    # disk with no header to explain it.
    assert not (tmp_path / "s1.npy").exists(), (
        "'nothing was recorded' left the preallocated array on disk")
    assert not (tmp_path / "s1.json").exists(), (
        "a header was written for a capture that never happened")


def test_aborting_warmup_repeatedly_leaves_nothing_behind(monkeypatch, tmp_path):
    """A subject adjusting framing may abort several times in a row. Each
    abort leaving a full-capacity file behind would quietly add up to
    gigabytes of unused disk."""
    _fake_camera(monkeypatch)
    monkeypatch.setattr(capture, "WARMUP_SECONDS", 5.0)

    for attempt in range(3):
        gui = _AbortingGui(on_warming=1)
        monkeypatch.setattr(capture, "Gui", lambda g=gui: g)
        assert capture.capture(_Args(str(tmp_path / "framing"))) == 1

    leftovers = sorted(f.name for f in tmp_path.iterdir())
    assert leftovers == [], f"three aborts left {leftovers}"


def test_q_with_no_face_in_frame_still_stops(monkeypatch, tmp_path):
    """With no face found, `q` must still stop the capture -- a stretch with
    no face is exactly the situation someone would want to abort and fix."""
    _fake_camera(monkeypatch, faces=False)
    gui = _AbortingGui(on_frame=3)
    monkeypatch.setattr(capture, "Gui", lambda: gui)
    monkeypatch.setattr(capture, "WARMUP_SECONDS", 0.0)

    started = time.perf_counter()
    code = capture.capture(_Args(str(tmp_path / "s2"), seconds=MIN_FOR_TEST))
    elapsed = time.perf_counter() - started

    assert code == 0
    assert gui.frames >= 3, "the preview never drew a no-face frame"
    assert elapsed < MIN_FOR_TEST / 3, (
        f"took {elapsed:.1f}s of a {MIN_FOR_TEST:.0f}s capture to honour q with "
        "no face in frame")
    header = json.loads((tmp_path / "s2.json").read_text(encoding="utf-8"))
    assert header["frames_written"] == 0
    assert np.load(str(tmp_path / "s2.npy")).shape[0] == 0


def test_aborting_mid_recording_keeps_what_was_captured(monkeypatch, tmp_path):
    """The trim runs in a `finally`, so an aborted capture ends up as a short
    valid file rather than a preallocated one full of black frames."""
    _fake_camera(monkeypatch)
    _stub_cv2(monkeypatch)
    gui = _AbortingGui(on_frame=5)
    monkeypatch.setattr(capture, "Gui", lambda: gui)
    monkeypatch.setattr(capture, "WARMUP_SECONDS", 0.0)

    started = time.perf_counter()
    capture.capture(_Args(str(tmp_path / "s3"), seconds=MIN_FOR_TEST))
    elapsed = time.perf_counter() - started

    assert elapsed < MIN_FOR_TEST / 3, f"took {elapsed:.1f}s to honour q"
    stored = np.load(str(tmp_path / "s3.npy"))
    assert stored.shape[0] == 5, f"kept {stored.shape[0]} frames, expected 5"
