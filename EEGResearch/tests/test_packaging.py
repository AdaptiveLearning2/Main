"""The camera extras stay optional: CI and headband-only installs have no camera dependency."""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAMERA_PACKAGES = ("opencv-python", "opencv", "onnxruntime", "mediapipe")


def _pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _is_optional_guard(stmt: ast.stmt) -> bool:
    """`if TYPE_CHECKING:` never runs; `try: import x / except ImportError` is optional by design."""
    if isinstance(stmt, ast.If):
        test = stmt.test
        return getattr(test, "id", getattr(test, "attr", None)) == "TYPE_CHECKING"
    if isinstance(stmt, ast.Try):
        caught = [n for h in stmt.handlers if h.type for n in ast.walk(h.type)]
        return any(getattr(n, "id", None) in ("ImportError", "ModuleNotFoundError") for n in caught)
    return False


def _import_time_modules(stmts: list[ast.stmt]) -> set[str]:
    """Top-level names imported when the module loads; function bodies are lazy and skipped."""
    found: set[str] = set()
    for stmt in stmts:
        if isinstance(stmt, ast.Import):
            found.update(alias.name.split(".")[0] for alias in stmt.names)
        elif isinstance(stmt, ast.ImportFrom):
            if stmt.level == 0 and stmt.module:
                found.add(stmt.module.split(".")[0])
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        else:
            blocks = [getattr(stmt, f, []) for f in ("body", "orelse", "finalbody")]
            blocks += [h.body for h in getattr(stmt, "handlers", [])]
            if _is_optional_guard(stmt):
                blocks = blocks[1:]
            for block in blocks:
                found |= _import_time_modules(block)
    return found


def _third_party_import_time_modules() -> set[str]:
    found: set[str] = set()
    for path in (ROOT / "src" / "app").rglob("*.py"):
        found |= _import_time_modules(ast.parse(path.read_text(encoding="utf-8")).body)
    return {m for m in found if m not in sys.stdlib_module_names and m not in ("src", "__future__")}


def test_the_scan_skips_lazy_and_guarded_imports():
    source = (
        "import httpx\n"
        "from fastapi import FastAPI\n"
        "if TYPE_CHECKING:\n    import typed_only\n"
        "try:\n    import cv2\nexcept ImportError:\n    cv2 = None\n"
        "try:\n    import required\nexcept ValueError:\n    pass\n"
        "def lazy():\n    import onnxruntime\n"
        "class Holder:\n    def method(self):\n        import mediapipe\n"
    )
    assert _import_time_modules(ast.parse(source).body) == {"httpx", "fastapi", "required"}


def test_every_import_time_dependency_is_pinned_in_the_base_lock():
    """A module-level import missing from requirements.lock fails `import src.app.main` on install."""
    modules = _third_party_import_time_modules()
    assert {"fastapi", "numpy", "pydantic"} <= modules, f"the scan found too little: {modules}"

    lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    pinned = {_normalise(m.group(1)) for m in re.finditer(r"^([A-Za-z0-9._-]+)==", lock, re.M)}
    owners = packages_distributions()
    missing = {}
    for module in sorted(modules):
        dists = {_normalise(d) for d in owners.get(module, [])}
        if not dists & pinned:
            missing[module] = sorted(dists) or "no installed distribution"
    assert not missing, f"imported at module level but not pinned in requirements.lock: {missing}"


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
