"""Model files under a non-ASCII directory load, with the real OpenCV and MediaPipe.

On Windows their C APIs cannot open such a path, so the files are read by Python and
handed over as bytes. Off Windows the path tests pass either way; skipped without the
camera extras (CI).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy as np
import pytest

CASCADE = "haarcascade_frontalface_default.xml"


@pytest.fixture
def unicode_dir(tmp_path):
    # ë is in code page 1252, and the path-based loaders still fail on it.
    target = tmp_path / "Zoë"
    target.mkdir()
    return target


def test_the_haar_cascade_loads_from_a_non_ascii_directory(unicode_dir, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    from src.app.services.face_roi import FaceLocator

    shutil.copyfile(os.path.join(cv2.data.haarcascades, CASCADE), unicode_dir / CASCADE)
    monkeypatch.setattr(cv2.data, "haarcascades", str(unicode_dir) + os.sep)

    locator = FaceLocator(redetect_every=0)

    assert locator.locate(np.zeros((480, 640), dtype=np.uint8)) is None


def test_a_missing_cascade_still_fails_to_load(unicode_dir, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    from src.app.services.face_roi import FaceLocator

    monkeypatch.setattr(cv2.data, "haarcascades", str(unicode_dir) + os.sep)

    with pytest.raises(RuntimeError, match="Haar cascade failed to load"):
        FaceLocator()


def test_the_landmark_model_loads_from_a_non_ascii_directory(unicode_dir):
    pytest.importorskip("mediapipe")
    from src.app.services.face_landmarks import (
        FaceMeshLandmarker,
        default_model_path,
        verify,
    )

    source = default_model_path()
    if not verify(source):
        pytest.skip(f"no verified landmark model at {source}")
    model = unicode_dir / "face_landmarker.task"
    shutil.copyfile(source, model)

    landmarker = FaceMeshLandmarker(model_path=str(model))

    assert landmarker.locate(np.zeros((480, 640, 3), dtype=np.uint8), 640, 480) == {}
    assert landmarker.last_reason == "no_face"


def test_the_models_loaded_from_bytes_still_find_a_real_face(unicode_dir, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    pytest.importorskip("mediapipe")
    matplotlib = pytest.importorskip("matplotlib")
    from src.app.services.face_landmarks import FaceMeshLandmarker, default_model_path, verify
    from src.app.services.face_roi import FaceLocator

    # A blank frame cannot tell a loaded model from one that never detects; matplotlib (a
    # mediapipe dependency) ships a portrait.
    photo = Path(matplotlib.get_data_path()) / "sample_data" / "grace_hopper.jpg"
    source = default_model_path()
    if not photo.is_file() or not verify(source):
        pytest.skip("needs matplotlib's sample portrait and a verified landmark model")
    shutil.copyfile(os.path.join(cv2.data.haarcascades, CASCADE), unicode_dir / CASCADE)
    monkeypatch.setattr(cv2.data, "haarcascades", str(unicode_dir) + os.sep)
    model = unicode_dir / "face_landmarker.task"
    shutil.copyfile(source, model)
    bgr = cv2.imdecode(np.fromfile(photo, dtype=np.uint8), cv2.IMREAD_COLOR)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    box = FaceLocator(redetect_every=0).locate(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
    found = FaceMeshLandmarker(model_path=str(model)).locate(rgb, rgb.shape[1], rgb.shape[0])

    assert box is not None and box[2] > rgb.shape[1] // 4, box  # face-sized, not a stray patch
    assert found, "the landmarker found no face in a portrait"


def test_the_landmarker_is_given_the_bytes_that_were_verified(tmp_path, monkeypatch):
    pytest.importorskip("mediapipe")
    import hashlib
    from types import SimpleNamespace

    from mediapipe.tasks import python as mp_python
    from src.app.services import face_landmarks as fl

    source = fl.default_model_path()
    if not fl.verify(source):
        pytest.skip(f"no verified landmark model at {source}")
    model = tmp_path / "face_landmarker.task"
    shutil.copyfile(source, model)

    def hash_then_swap_the_file(data):
        digest = hashlib.sha256(data)
        model.write_bytes(b"\x01" * fl.MODEL_BYTES)    # swapped between check and use
        return digest

    received = {}
    real_base_options = mp_python.BaseOptions

    def capture(**kwargs):
        received.update(kwargs)
        return real_base_options(**kwargs)

    monkeypatch.setattr(fl, "hashlib", SimpleNamespace(sha256=hash_then_swap_the_file))
    monkeypatch.setattr(mp_python, "BaseOptions", capture)

    fl._TasksMesh(str(model))

    on_disk = hashlib.sha256(model.read_bytes()).hexdigest()
    assert on_disk != fl.MODEL_SHA256, "the file was not swapped, so this proves nothing"
    assert hashlib.sha256(received["model_asset_buffer"]).hexdigest() == fl.MODEL_SHA256
