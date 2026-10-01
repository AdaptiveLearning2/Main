"""Print the runtime requirements in pyproject that this interpreter does not meet, space-separated.

An editable install keeps the dependency list it was installed with, so both launchers ask this
before starting the sidecar and install whatever a pulled pyproject added, raised or extended.
"""

from __future__ import annotations

import sys
import tomllib
from importlib.metadata import PackageNotFoundError, requires, version
from pathlib import Path

try:
    from packaging.requirements import Requirement
except ImportError:  # not in the base install; pip vendors it, and the install step needs pip anyway
    from pip._vendor.packaging.requirements import Requirement


def _unmet(req: Requirement, seen: set[tuple[str, str]]) -> list[str]:
    """req if absent or too old, else what its extras add that is; the caller has checked its marker."""
    try:
        installed = version(req.name)
    except PackageNotFoundError:
        return [f"{req.name}{req.specifier}"]
    if not req.specifier.contains(installed, prereleases=True):
        return [f"{req.name}{req.specifier}"]
    unmet = []
    for extra in sorted(req.extras):
        if (req.name.lower(), extra) in seen:
            continue
        seen.add((req.name.lower(), extra))
        for line in requires(req.name) or []:
            dep = Requirement(line)
            # Only what the extra adds: the distribution's own dependencies came with it.
            if dep.marker and dep.marker.evaluate({"extra": extra}) and not dep.marker.evaluate({"extra": ""}):
                unmet += _unmet(dep, seen)
    return unmet


def missing(pyproject: Path) -> list[str]:
    """Unmet requirements; one whose marker excludes this interpreter is skipped."""
    deps = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["dependencies"]
    seen: set[tuple[str, str]] = set()
    unmet = []
    for req in map(Requirement, deps):
        if req.marker is None or req.marker.evaluate():
            unmet += _unmet(req, seen)
    return unmet


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "pyproject.toml"
    print(" ".join(missing(target)))
