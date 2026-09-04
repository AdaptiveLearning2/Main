"""The bridge supervisor restarts a crashed exe, a bounded number of times.

Nothing else supervises muse_native_bridge.exe; start.ps1 runs it through
scripts/run_bridge_supervised.ps1. These drive that script against a stand-in
exe (a .cmd that exits with a chosen code) rather than the bridge, since what
is under test is the loop: restart on a non-zero exit, stop on a clean one,
give up past the cap, and say so each time.

Windows only -- the script is PowerShell and so is the bridge.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_bridge_supervised.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or POWERSHELL is None,
    reason="the supervisor is PowerShell, like the bridge it wraps",
)


def _stub_exe(tmp_path: Path, exit_code: int, runs_file: Path) -> Path:
    """A .cmd that records each run and exits with `exit_code`."""
    exe = tmp_path / "fake_bridge.cmd"
    exe.write_text(f"@echo off\r\necho run>> \"{runs_file}\"\r\nexit /b {exit_code}\r\n")
    return exe


def _run(exe: Path, **params):
    args = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), "-Exe", str(exe)]
    for k, v in params.items():
        args += [f"-{k}", str(v)]
    return subprocess.run(args, capture_output=True, text=True, timeout=120,
                          env={**os.environ, "MUSE_ENABLE_OPTICS": "1"})


def test_a_crashing_bridge_is_restarted_up_to_the_cap_then_left_alone(tmp_path):
    runs = tmp_path / "runs.txt"
    exe = _stub_exe(tmp_path, 3, runs)
    res = _run(exe, MaxRestarts=2, RestartWindowSeconds=600, RestartDelaySeconds=0)
    # First run plus MaxRestarts restarts, then one more exit tips it over the cap.
    assert runs.read_text().count("run") == 3
    assert res.returncode == 3, res.stdout + res.stderr
    assert "giving up" in res.stdout
    # Every exit is printed with its code: the crash a restart hides from the
    # student stays readable in the window.
    assert res.stdout.count("exited at") == 3
    assert "with code 3" in res.stdout


def test_a_clean_exit_is_not_restarted(tmp_path):
    runs = tmp_path / "runs.txt"
    exe = _stub_exe(tmp_path, 0, runs)
    res = _run(exe, MaxRestarts=5, RestartDelaySeconds=0)
    assert runs.read_text().count("run") == 1
    assert res.returncode == 0
    assert "not restarting" in res.stdout


def test_a_missing_exe_is_refused_rather_than_looped(tmp_path):
    res = _run(tmp_path / "nowhere.exe", RestartDelaySeconds=0)
    assert res.returncode == 2
    assert "exe not found" in res.stdout


def test_an_exe_that_exists_but_cannot_start_is_a_failure_with_a_code(tmp_path):
    """Test-Path clears a file that is not runnable -- an interrupted build,
    an antivirus-truncated binary. Invoking it raises rather than running,
    and $LASTEXITCODE keeps $null, which printed as a blank code and made
    `exit $null` report success for a bridge that never started."""
    exe = tmp_path / "broken.exe"
    exe.write_bytes(b"")
    res = _run(exe, MaxRestarts=1, RestartDelaySeconds=0)
    assert res.returncode == 3, res.stdout + res.stderr
    assert "did not start" in res.stdout
    assert "with code 3" in res.stdout
    assert "giving up" in res.stdout
    # Never printed a blank code.
    assert "with code  after" not in res.stdout


def test_start_ps1_launches_the_bridge_through_the_supervisor():
    """The loop only helps if start.ps1 uses it. The env-var prefixes are built
    around `$bridgeCmd`, so the supervisor has to be what that command runs."""
    src = (Path(__file__).resolve().parents[2] / "start.ps1").read_text(encoding="utf-8")
    assert "run_bridge_supervised.ps1" in src
    assert "$bridgeCmd = \"& '$bridgeSupervisor' -Exe '$bridgeExe'\"" in src
