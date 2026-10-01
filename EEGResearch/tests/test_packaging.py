"""The sidecar's install surface: camera extras stay optional, and every import-time dependency is a runtime one."""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tomllib
from importlib.metadata import packages_distributions, version
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CAMERA_PACKAGES = ("opencv-python", "opencv", "onnxruntime", "mediapipe")


def _pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pinned_in(lock_name: str) -> set[str]:
    """Normalised names pinned with `==`, `name[extra]==` included."""
    lock = (ROOT / lock_name).read_text(encoding="utf-8")
    return {_normalise(m.group(1)) for m in re.finditer(r"^([A-Za-z0-9._-]+)(?:\[[^\]]*\])?==", lock, re.M)}


def _is_optional_guard(stmt: ast.stmt) -> bool:
    """`if TYPE_CHECKING:` never runs; `try: import x / except ImportError` is optional unless it raises."""
    if isinstance(stmt, ast.If):
        test = stmt.test
        return getattr(test, "id", getattr(test, "attr", None)) == "TYPE_CHECKING"
    if isinstance(stmt, ast.Try):
        handlers = [h for h in stmt.handlers if h.type and any(
            getattr(n, "id", None) in ("ImportError", "ModuleNotFoundError") for n in ast.walk(h.type))]
        return bool(handlers) and not any(
            isinstance(n, ast.Raise) for h in handlers for s in h.body for n in ast.walk(s))
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
        "try:\n    import hard\nexcept ImportError:\n    raise RuntimeError('install it')\n"
        "if sys.platform == 'win32':\n    import platform_only\n"
        "def lazy():\n    import onnxruntime\n"
        "class Holder:\n    def method(self):\n        import mediapipe\n"
    )
    assert _import_time_modules(ast.parse(source).body) == {
        "httpx", "fastapi", "required", "hard", "platform_only"}


def test_every_import_time_dependency_is_a_runtime_dependency_and_pinned_in_every_lock():
    """A module-level import missing from [project].dependencies or a lock fails `import src.app.main` there."""
    modules = _third_party_import_time_modules()
    assert {"fastapi", "numpy", "pydantic"} <= modules, f"the scan found too little: {modules}"
    locks = sorted(p.name for p in ROOT.glob("requirements*.lock"))
    assert len(locks) >= 4, f"found too few locks: {locks}"

    declared = {_normalise(re.match(r"[A-Za-z0-9._-]+", d).group())
                for d in _pyproject()["project"]["dependencies"]}
    owners = packages_distributions()
    dists = {m: {_normalise(d) for d in owners.get(m, [])} for m in sorted(modules)}
    undeclared = {m: sorted(d) or "no installed distribution" for m, d in dists.items() if not d & declared}
    assert not undeclared, f"imported at module level but not in [project].dependencies: {undeclared}"
    pinned = {lock: _pinned_in(lock) for lock in locks}
    unpinned = {f"{lock}: {m}": sorted(d) or "no installed distribution"
                 for lock in locks for m, d in dists.items() if not d & pinned[lock]}
    assert not unpinned, f"imported at module level but not pinned: {unpinned}"


MISSING_DEPS = ROOT / "scripts" / "missing_runtime_deps.py"
BACKEND_REQUIREMENTS = ROOT.parent / "Website" / "AdaptiveLearning" / "backend" / "requirements.txt"
# A base install has no packaging distribution; blocking the import stands in for one.
BLOCK_PACKAGING = ("import runpy, sys; sys.modules['packaging'] = None; sys.argv = sys.argv[1:]; "
                   "runpy.run_path(sys.argv[0], run_name='__main__')")
# What the launchers probe: the sidecar's pyproject and the backend's requirements.txt.
KINDS = pytest.mark.parametrize("kind", ["pyproject", "requirements"])


def _manifest_with(tmp_path: Path, deps: list[str], kind: str) -> Path:
    if kind == "requirements":
        path = tmp_path / "requirements.txt"
        path.write_text("".join(f"{d}\n" for d in deps), encoding="utf-8")
        return path
    path = tmp_path / "pyproject.toml"
    listed = ", ".join(json.dumps(d) for d in deps)  # a JSON string is a TOML basic string
    path.write_text(f'[project]\nname = "x"\nversion = "0"\ndependencies = [{listed}]\n', encoding="utf-8")
    return path


def _missing(manifest: Path, blocked: bool = False, site: Path | None = None, absent: bool = False) -> list[str]:
    prefix = ["-c", BLOCK_PACKAGING] if blocked else []
    env = dict(os.environ, PYTHONPATH=str(site)) if site else None
    flags = ["--absent"] if absent else []
    run = subprocess.run([sys.executable, *prefix, str(MISSING_DEPS), str(manifest), *flags],
                         capture_output=True, text=True, check=True, env=env)
    return run.stdout.split()


@KINDS
def test_the_dependency_probe_names_what_this_interpreter_lacks(tmp_path, kind):
    manifest = _manifest_with(tmp_path, ["pytest>=1", "uvicorn[standard]>=0.30", "pydantic_settings>=2",
                                         "no-such-distribution-anywhere>=1", "no-such-dist-with-an-extra[more]==1"],
                              kind)
    # Named with its extra, since the backend's launcher installs exactly what is printed.
    assert _missing(manifest) == ["no-such-distribution-anywhere>=1", "no-such-dist-with-an-extra[more]==1"]


@KINDS
def test_the_dependency_probe_skips_a_requirement_whose_marker_excludes_this_interpreter(tmp_path, kind):
    manifest = _manifest_with(tmp_path, ['no-such-backport; python_version < "3.11"',
                                         'no-such-port; sys_platform == "no-such-os"',
                                         'no-such-dist-that-applies; python_version >= "3.11"'], kind)
    assert _missing(manifest) == ["no-such-dist-that-applies"]


@KINDS
def test_the_dependency_probe_names_an_installed_distribution_older_than_asked(tmp_path, kind):
    assert _missing(_manifest_with(tmp_path, ["pytest>=1", "pydantic>=9999"], kind)) == ["pydantic>=9999"]


def test_the_dependency_probe_reads_a_requirements_file_as_pip_does(tmp_path):
    path = tmp_path / "requirements.txt"
    path.write_text("\n".join([
        "# a comment, then blank lines", "", "   ",
        f"pytest=={version('pytest')}  # met exactly",
        "pydantic==1.0",  # installed, but newer than pinned
        'no-such-url-dist @ https://x.invalid/a.zip#frag ; python_version < "3"',  # unspaced: part of the URL
        "  no-such-distribution-anywhere==1",
    ]) + "\n", encoding="utf-8")
    assert _missing(path) == ["pydantic==1.0", "no-such-distribution-anywhere==1"]


def test_the_dependency_probe_reads_every_line_of_the_backends_requirements(tmp_path):
    """Stand-ins at version 0 for the real file's pins, so every line read comes back and a skipped one cannot."""
    lines = [line.strip() for line in BACKEND_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    pins = [re.fullmatch(r"([A-Za-z0-9._-]+)(\[[^\]]*\])?(==\S+)", line) for line in lines]
    assert len(lines) >= 10 and all(pins), f"a line the stand-ins cannot model: {lines}"
    for pin in pins:
        info = tmp_path / "site" / f"{_normalise(pin[1]).replace('-', '_')}-0.dist-info"
        info.mkdir(parents=True)
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {pin[1]}\nVersion: 0\n", encoding="utf-8")
    assert _missing(BACKEND_REQUIREMENTS, site=tmp_path / "site") == lines  # extras kept, so each installs as pinned


@KINDS
def test_the_dependency_probe_names_what_an_extra_adds_that_this_interpreter_lacks(tmp_path, kind):
    """A stand-in distribution, so the extra's contents do not depend on what this interpreter has."""
    info = tmp_path / "site" / "probe_extra_fixture-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("\n".join([
        "Metadata-Version: 2.1", "Name: probe-extra-fixture", "Version: 1.0", "Provides-Extra: more",
        'Requires-Dist: pytest>=1; extra == "more"',
        'Requires-Dist: no-such-distribution-anywhere>=2; extra == "more"',
        'Requires-Dist: no-such-backport; python_version < "3.11" and extra == "more"',
        'Requires-Dist: probe-extra-fixture[more]; extra == "more"',  # a cycle has to end
        'Requires-Dist: no-such-base-dependency; python_version >= "3.11"',  # its own, not the extra's
    ]) + "\n", encoding="utf-8")
    manifest = _manifest_with(tmp_path, ["probe-extra-fixture[more]>=1"], kind)
    assert _missing(manifest, site=tmp_path / "site") == ["no-such-distribution-anywhere>=2"]


def test_the_dependency_probe_names_only_what_is_not_installed_when_asked(tmp_path):
    """--absent, as the launchers ask it of the backend's exact pins; an installed extra is still looked into."""
    info = tmp_path / "site" / "probe_extra_fixture-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("\n".join([
        "Metadata-Version: 2.1", "Name: probe-extra-fixture", "Version: 1.0", "Provides-Extra: more",
        'Requires-Dist: pytest==1.0; extra == "more"',  # installed, at another version
        'Requires-Dist: no-such-distribution-anywhere>=2; extra == "more"',
    ]) + "\n", encoding="utf-8")
    manifest = _manifest_with(tmp_path, ["pydantic==1.0", "probe-extra-fixture[more]==2.0", "no-such-dist==1"],
                              "requirements")
    unmet = _missing(manifest, site=tmp_path / "site", absent=True)
    assert unmet == ["no-such-distribution-anywhere>=2", "no-such-dist==1"]


@KINDS
def test_the_dependency_probe_runs_where_the_packaging_distribution_is_absent(tmp_path, kind):
    manifest = _manifest_with(tmp_path, ['no-such-backport; python_version < "3.11"', "pydantic>=9999"], kind)
    assert _missing(manifest, blocked=True) == ["pydantic>=9999"]


def test_the_dependency_probe_finds_every_runtime_dependency_of_this_install():
    """Tests run on an install carrying every runtime dependency, so any name reported is a lookup bug."""
    assert _missing(ROOT / "pyproject.toml") == []


def test_a_pin_with_extras_counts_as_pinned():
    lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    assert re.search(r"^uvicorn\[standard\]==", lock, re.M), "uvicorn is no longer pinned with extras"
    assert "uvicorn" in _pinned_in("requirements.lock")


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
