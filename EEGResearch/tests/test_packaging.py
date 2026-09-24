"""The camera extras stay optional: CI and headband-only installs have no camera dependency."""

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
    """The lock is what gets installed, whatever pyproject declares."""
    for name in ("requirements.lock", "requirements-dev.lock"):
        text = (ROOT / name).read_text(encoding="utf-8").lower()
        for package in CAMERA_PACKAGES:
            assert f"\n{package}==" not in text, f"{package} is pinned in {name}"


def test_the_face_extra_exists_and_is_pinned_in_its_own_lock():
    extras = _pyproject()["project"]["optional-dependencies"]
    assert "face" in extras

    text = (ROOT / "requirements-face.lock").read_text(encoding="utf-8").lower()
    assert "\nopencv-contrib-python==" in text
    assert "\nonnxruntime==" in text


def test_opencv_is_held_below_five():
    """FaceLocator needs `cv2.data.haarcascades` and CAP_PROP_*, untested on 5.x and not in CI."""
    for extra in ("face", "gaze"):
        deps = " ".join(_pyproject()["project"]["optional-dependencies"][extra])
        assert "<5" in deps, f"the opencv major-version cap was removed from {extra}"


def test_the_gaze_extra_exists_and_is_pinned_in_its_own_lock():
    """Its own extra: mediapipe is ~50 MB and a second ML runtime for an off-by-default channel."""
    extras = _pyproject()["project"]["optional-dependencies"]
    assert "gaze" in extras

    text = (ROOT / "requirements-gaze.lock").read_text(encoding="utf-8").lower()
    assert "\nmediapipe==" in text


def test_the_gaze_lock_covers_the_command_the_scripts_print():
    """A gaze-only resolve would hide the cv2 collision the next test checks for."""
    text = (ROOT / "requirements-gaze.lock").read_text(encoding="utf-8").lower()

    assert "--extra=face" in text and "--extra=gaze" in text
    assert "\nopencv-contrib-python==" in text


def test_there_is_exactly_one_cv2_provider():
    extras = _pyproject()["project"]["optional-dependencies"]
    both = " ".join(dep for e in ("face", "gaze")
                    for dep in extras[e]).replace(" ", "")
    assert "opencv-python>" not in both and "opencv-python=" not in both, (
        "the plain opencv-python distribution is back alongside contrib; both "
        "install `cv2` and whichever lands last owns the import")

    providers = set()
    for line in (ROOT / "requirements-gaze.lock").read_text(encoding="utf-8").splitlines():
        for name in ("opencv-python==", "opencv-contrib-python=="):
            if line.lower().startswith(name):
                providers.add(name.rstrip("="))
    assert providers == {"opencv-contrib-python"}, (
        f"expected exactly one cv2 provider, got {providers or 'none'}")


def test_no_rppg_library_is_depended_on():
    """Pulse extraction is in-house: packaged rPPG libraries fail on dataset licence or packaging."""
    project = _pyproject()["project"]
    everything = " ".join(
        project["dependencies"]
        + [d for group in project["optional-dependencies"].values() for d in group]
    ).lower()
    for banned in ("open-rppg", "rppg", "vitallens", "pyvhr", "yarppg"):
        assert banned not in everything, f"{banned} was added as a dependency"
