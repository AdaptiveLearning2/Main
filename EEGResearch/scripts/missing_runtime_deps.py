"""Print the runtime dependencies pyproject names that this interpreter cannot find, space-separated.

An editable install keeps the dependency list it was installed with, so start.ps1 asks this before
starting the sidecar and installs whatever a pulled pyproject added.
"""

from __future__ import annotations

import re
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def missing(pyproject: Path) -> list[str]:
    """Names with no installed distribution; version floors are not checked."""
    deps = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["dependencies"]
    absent = []
    for dep in deps:
        name = re.match(r"[A-Za-z0-9._-]+", dep).group()
        try:
            version(name)
        except PackageNotFoundError:
            absent.append(name)
    return absent


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "pyproject.toml"
    print(" ".join(missing(target)))
