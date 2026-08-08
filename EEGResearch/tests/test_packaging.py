"""The `face` extra stays optional.

The whole arrangement rests on one property: the sidecar installs, imports,
boots and passes its tests with no camera dependency present. That is CI's state
and the state of every headband-only deployment. These assertions are cheap and
the failure they prevent is expensive -- a camera dependency leaking into the
base install would be invisible in development, where it is present anyway.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAMERA_PACKAGES = ("opencv-python", "opencv", "onnxruntime", "mediapipe")


def _pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def test_no_camera_dependency_is_in_the_base_install():
    deps = " ".join(_pyproject()["project"]["dependencies"]).lower()
    for package in CAMERA_PACKAGES:
        assert package not in deps, f"{package} leaked into the base dependencies"


def test_no_camera_dependency_is_in_the_base_or_dev_lock():
    """A lock is what actually gets installed. An extra declared correctly in
    pyproject but compiled into the base lock would still put OpenCV on every
    machine."""
    for name in ("requirements.lock", "requirements-dev.lock"):
        text = (ROOT / name).read_text(encoding="utf-8").lower()
        for package in CAMERA_PACKAGES:
            assert f"\n{package}==" not in text, f"{package} is pinned in {name}"


def test_the_face_extra_exists_and_is_pinned_in_its_own_lock():
    extras = _pyproject()["project"]["optional-dependencies"]
    assert "face" in extras

    text = (ROOT / "requirements-face.lock").read_text(encoding="utf-8").lower()
    assert "\nopencv-python==" in text
    assert "\nonnxruntime==" in text


def test_opencv_is_held_below_five():
    """FaceLocator depends on `cv2.data.haarcascades` shipping inside the wheel
    and on the CAP_PROP_* constants used to lock exposure. Neither has been
    exercised against a 5.x release -- there is no OpenCV in CI, so a break
    would surface in front of a class rather than in a test run."""
    face = " ".join(_pyproject()["project"]["optional-dependencies"]["face"])
    assert "<5" in face, "the opencv major-version cap was removed"


def test_no_rppg_library_is_depended_on():
    """The pulse extraction is ours. Every packaged deep-learning rPPG carries
    weights trained on a dataset behind a per-requester agreement, and the
    classical ones fail on licence or packaging -- see the plan's Phase 4
    dependency section before adding one."""
    project = _pyproject()["project"]
    everything = " ".join(
        project["dependencies"]
        + [d for group in project["optional-dependencies"].values() for d in group]
    ).lower()
    for banned in ("open-rppg", "rppg", "vitallens", "pyvhr", "yarppg"):
        assert banned not in everything, f"{banned} was added as a dependency"
