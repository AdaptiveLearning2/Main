"""Model files under a non-ASCII directory load, with the real OpenCV and MediaPipe.

On Windows their C APIs cannot open such a path, so the files are read by Python
and handed over as bytes. Skipped where the camera extras are not installed (CI).
"""

from __future__ import annotations

import os
import shutil

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
