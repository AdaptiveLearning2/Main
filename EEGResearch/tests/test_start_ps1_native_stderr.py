"""`start.ps1` never redirects a native command's stderr under Stop: on PS 5.1 that aborts it."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
START = ROOT / "start.ps1"
POWERSHELL = shutil.which("powershell")

# Writes one line to each stream and fails, as `ollama list` does with no server.
NOISY = 'cmd /c "echo out & echo err 1>&2 & exit 1"'


def _ps(script: str) -> subprocess.CompletedProcess:
    return subprocess.run([POWERSHELL, "-NoProfile", "-Command", script],
                          capture_output=True, text=True, timeout=60)


def _function(name: str) -> str:
    text = START.read_text(encoding="utf-8")
    m = re.search(rf"^function {name} \{{.*?^\}}", text, re.S | re.M)
    assert m, f"start.ps1 has no {name}"
    return m.group(0)


@pytest.mark.skipif(POWERSHELL is None, reason="Windows PowerShell only")
def test_the_quiet_wrapper_survives_stderr_under_stop_where_a_bare_redirect_aborts():
    bare = _ps(f"$ErrorActionPreference = 'Stop'; $x = {NOISY} 2>$null; 'survived'")
    if "survived" in bare.stdout:
        pytest.skip("this PowerShell does not abort on the bare redirect, so it proves nothing here")

    out = _ps(f"$ErrorActionPreference = 'Stop'\n{_function('Invoke-Quiet')}\n"
              f"$x = Invoke-Quiet {{ {NOISY} }}; \"got:$x\"; 'survived'; $ErrorActionPreference")
    assert "got:out" in out.stdout and "survived" in out.stdout, out.stderr
    # The caller's preference is put back, not left at Continue.
    assert out.stdout.strip().splitlines()[-1] == "Stop"


def test_no_native_call_in_start_ps1_redirects_stderr_outside_the_wrapper():
    """A text scan, stated: a PowerShell AST needs PowerShell. Comments are skipped (a mention)."""
    wrapper = _function("Invoke-Quiet")
    body = START.read_text(encoding="utf-8").replace(wrapper, "")
    offenders = [line.strip() for line in body.splitlines()
                 if "2>$null" in line.split("#", 1)[0]]
    assert offenders == []
