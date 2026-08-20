"""Tests that the `face` extra stays optional.

The sidecar must install, import, boot, and pass its tests with no camera
dependency present -- that's CI's state and every headband-only deployment's
state. A camera dependency leaking into the base install would be invisible
in development, where it's present anyway, so these checks are cheap
insurance against an expensive failure.
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
    """A lock is what actually gets installed. An extra declared correctly
    in pyproject but compiled into the base lock would still put OpenCV on
    every machine."""
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
    """FaceLocator depends on `cv2.data.haarcascades` shipping in the wheel
    and on the CAP_PROP_* constants used to lock exposure. Neither has been
    tested against a 5.x release, since OpenCV isn't in CI, so a break here
    would surface in front of a class instead of in a test run."""
    for extra in ("face", "gaze"):
        deps = " ".join(_pyproject()["project"]["optional-dependencies"][extra])
        assert "<5" in deps, f"the opencv major-version cap was removed from {extra}"


def test_the_gaze_extra_exists_and_is_pinned_in_its_own_lock():
    """Its own extra, not folded into `face`: mediapipe is ~50 MB and a
    second ML runtime for a channel that's off by default.

    Before this existed, mediapipe wasn't declared in any dependency spec, so
    `pip install -e ".[face]"` (the command the start scripts print) never
    installed it, and gaze failed at runtime as `landmarker_unavailable`,
    indistinguishable from a missing model.
    """
    extras = _pyproject()["project"]["optional-dependencies"]
    assert "gaze" in extras

    text = (ROOT / "requirements-gaze.lock").read_text(encoding="utf-8").lower()
    assert "\nmediapipe==" in text


def test_the_gaze_lock_covers_the_command_the_scripts_print():
    """`.[face,gaze]` is what an operator is told to run, so that's what the
    lock has to describe. A gaze-only resolve wouldn't contain opencv-python
    at all, and would hide the collision the next test checks for."""
    text = (ROOT / "requirements-gaze.lock").read_text(encoding="utf-8").lower()

    assert "--extra=face" in text and "--extra=gaze" in text
    assert "\nopencv-contrib-python==" in text


def test_there_is_exactly_one_cv2_provider():
    """`opencv-python` and `opencv-contrib-python` install the same `cv2`
    module, contrib being the superset. With both installed, whichever landed
    last owns the import -- `.[face,gaze]` once produced exactly that, 4.14
    of one beside 5.0 of the other, silently defeating the `<5` cap above.

    Fixed by moving `face` onto contrib rather than constraining mediapipe:
    one package, one version, nothing to race.
    """
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
    """Pulse extraction is implemented in-house. Every packaged deep-learning
    rPPG carries weights trained on a dataset behind a per-requester
    agreement, and the classical ones fail on licence or packaging -- check
    that before adding one."""
    project = _pyproject()["project"]
    everything = " ".join(
        project["dependencies"]
        + [d for group in project["optional-dependencies"].values() for d in group]
    ).lower()
    for banned in ("open-rppg", "rppg", "vitallens", "pyvhr", "yarppg"):
        assert banned not in everything, f"{banned} was added as a dependency"
