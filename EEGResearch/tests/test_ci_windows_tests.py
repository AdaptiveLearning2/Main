"""Every test file that only runs on Windows is in CI's Windows job: the ubuntu job, which runs the rest, skips it."""

from __future__ import annotations

import re
from pathlib import Path

from launcher_cases import ROOT

TESTS = Path(__file__).resolve().parent
# The skip conditions Windows-only files use here: a text scan, so a new spelling of one goes unseen.
WINDOWS_ONLY = re.compile(r'sys\.platform != "win32"|POWERSHELL is None|shutil\.which\("powershell"\)')


def test_every_windows_only_test_file_runs_in_the_windows_ci_job():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    windows_only = sorted(p.name for p in TESTS.glob("test_*.py")
                          if p.name != Path(__file__).name and WINDOWS_ONLY.search(p.read_text(encoding="utf-8")))
    assert len(windows_only) >= 8  # a floor: a pattern that stopped matching would otherwise pass on nothing
    assert [name for name in windows_only if f"tests/{name}" not in ci] == []
