"""`run_pilot_muse.ps1` must not take the website backend down: 8000 is its port, not the sidecar's."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_pilot_muse.ps1"
POWERSHELL = shutil.which("powershell")

pytestmark = pytest.mark.skipif(POWERSHELL is None, reason="Windows PowerShell only")


def _run(*args: str) -> subprocess.CompletedProcess:
    # Tokens empty on purpose: any run that gets past the port guard stops at the token check.
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), *args,
         "-LearnerToken", "", "-AdminToken", ""],
        capture_output=True, text=True, timeout=60)


def test_stopping_listeners_on_the_website_backends_port_is_refused_first():
    out = _run("-ApiPort", "8000", "-StopExistingApiOnPort")
    assert out.returncode != 0
    assert "website backend" in (out.stdout + out.stderr)


def test_the_default_port_is_the_sidecars_so_the_flag_alone_is_safe():
    """With no -ApiPort the guard does not fire; the run stops at the token check instead."""
    out = _run("-StopExistingApiOnPort")
    text = out.stdout + out.stderr
    assert "website backend" not in text
    assert "LearnerToken not set" in text
