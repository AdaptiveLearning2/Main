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
    assert "\nopencv-contrib-python==" in text
    assert "\nonnxruntime==" in text


def test_opencv_is_held_below_five():
    """FaceLocator depends on `cv2.data.haarcascades` shipping inside the wheel
    and on the CAP_PROP_* constants used to lock exposure. Neither has been
    exercised against a 5.x release -- there is no OpenCV in CI, so a break
    would surface in front of a class rather than in a test run."""
    for extra in ("face", "gaze"):
        deps = " ".join(_pyproject()["project"]["optional-dependencies"][extra])
        assert "<5" in deps, f"the opencv major-version cap was removed from {extra}"


def test_the_gaze_extra_exists_and_is_pinned_in_its_own_lock():
    """Its own extra, not folded into `face`: mediapipe is ~50 MB and a second
    ML runtime for a channel that is off by default.

    The lock is what actually gets installed, and mediapipe was declared in no
    dependency spec at all until this existed -- so `pip install -e ".[face]"`,
    the command the start scripts print, never installed it, and gaze failed at
    runtime as `landmarker_unavailable`, indistinguishable from a missing model.
    """
    extras = _pyproject()["project"]["optional-dependencies"]
    assert "gaze" in extras

    text = (ROOT / "requirements-gaze.lock").read_text(encoding="utf-8").lower()
    assert "\nmediapipe==" in text


def test_the_gaze_lock_covers_the_command_the_scripts_print():
    """`.[face,gaze]` is what an operator is told to run, so that is what the
    lock has to describe. A gaze-only resolve would not contain opencv-python at
    all and would hide the collision the next test is about."""
    text = (ROOT / "requirements-gaze.lock").read_text(encoding="utf-8").lower()

    assert "--extra=face" in text and "--extra=gaze" in text
    assert "\nopencv-contrib-python==" in text


def test_there_is_exactly_one_cv2_provider():
    """`opencv-python` and `opencv-contrib-python` install the same `cv2`
    module, contrib being the superset. With both on a machine, whichever landed
    last owns the import -- and `.[face,gaze]` produced exactly that, 4.14 of
    one beside 5.0 of the other, silently defeating the `<5` cap above.

    `face` moved onto contrib rather than mediapipe being constrained: one
    package, one version, nothing to race. Verified against a camera after the
    swap, because the cap's stated reason is behaviour no test here covers.
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
