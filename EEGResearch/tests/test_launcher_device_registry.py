"""A plain launcher run re-points the `default:` headband entry.

A `-Muse -Camera` run writes `default:muse@8765,camera:face@N`; the cleanup
on a later plain run stripped only the camera entry, and the registry wins
over EEG_SOURCE for that device, so every plain run after it started the
sidecar looking for a bridge that was not running. Both launchers' cleanup
functions are extracted from the scripts and driven against a temp .env.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("powershell")
BASH = shutil.which("bash")

CASES = [
    # (line in .env, headband for this run, expected line after) -- None = no line
    ("EEG_DEVICES=default:muse@8765", "default:sim", "EEG_DEVICES=default:sim"),
    ("EEG_DEVICES=default:muse@8765,camera:face@1", "default:sim", "EEG_DEVICES=default:sim"),
    ("EEG_DEVICES=default:sim,camera:face@0", "default:muse@8765", "EEG_DEVICES=default:muse@8765"),
    # A hand-written registry with no default entry is left alone.
    ("EEG_DEVICES=station1:muse@8765,station2:muse@8766", "default:sim",
     "EEG_DEVICES=station1:muse@8765,station2:muse@8766"),
    # Other stations survive beside the rewritten default.
    ("EEG_DEVICES=default:muse@8765,station2:muse@8766,camera:face@0", "default:sim",
     "EEG_DEVICES=default:sim,station2:muse@8766"),
    # No registry line: none is added (the sidecar synthesises one from EEG_SOURCE).
    (None, "default:sim", None),
]


CAMERA_CASES = [
    # (line, headband, camera, expected): the -Camera branch composes onto the
    # registry rather than overwriting it.
    ("EEG_DEVICES=station1:muse@8765,station2:muse@8766", "default:sim", "camera:face@0",
     "EEG_DEVICES=default:sim,station1:muse@8765,station2:muse@8766,camera:face@0"),
    ("EEG_DEVICES=default:sim,camera:face@1", "default:muse@8765", "camera:face@0",
     "EEG_DEVICES=default:muse@8765,camera:face@0"),
    ("EEG_DEVICES=default:muse@8765,station2:muse@8766", "default:sim", "camera:face@2",
     "EEG_DEVICES=default:sim,station2:muse@8766,camera:face@2"),
    # A fresh file with no registry line gets the pair the run needs.
    (None, "default:sim", "camera:face@0", "EEG_DEVICES=default:sim,camera:face@0"),
]


def _env(tmp_path: Path, line: str | None) -> Path:
    p = tmp_path / ".env"
    p.write_text("EEG_SOURCE=sim\n" + (f"{line}\n" if line else "") + "API_TOKEN=t\n", encoding="utf-8")
    return p


def _registry_line(p: Path) -> str | None:
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.startswith("EEG_DEVICES=")]
    return lines[-1] if lines else None


def _extract(text: str, start: str) -> str:
    """The function body from `start` to the first `}` at column 0."""
    m = re.search(re.escape(start) + r".*?^\}", text, re.S | re.M)
    assert m, start
    return m.group(0)


def _run_ps1(tmp_path: Path, env: Path, headband: str, camera: str | None) -> None:
    src = (ROOT / "start.ps1").read_text(encoding="utf-8")
    fns = _extract(src, "function Update-DeviceRegistry {") + "\n" + _extract(src, "function Set-EnvKey {")
    script = tmp_path / "t.ps1"
    args = f"'{env}' '{headband}'" + (f" '{camera}'" if camera else "")
    script.write_text(fns + f"\nUpdate-DeviceRegistry {args}\n", encoding="utf-8")
    subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                   check=True, capture_output=True, text=True)


@pytest.mark.skipif(sys.platform != "win32" or POWERSHELL is None, reason="start.ps1 is Windows")
@pytest.mark.parametrize("line,headband,expected", CASES)
def test_start_ps1_repoints_the_default_entry(tmp_path, line, headband, expected):
    env = _env(tmp_path, line)
    _run_ps1(tmp_path, env, headband, None)
    assert _registry_line(env) == expected
    assert "EEG_SOURCE=sim" in env.read_text(encoding="utf-8"), "other keys untouched"


@pytest.mark.skipif(sys.platform != "win32" or POWERSHELL is None, reason="start.ps1 is Windows")
@pytest.mark.parametrize("line,headband,camera,expected", CAMERA_CASES)
def test_start_ps1_camera_branch_composes_onto_the_registry(tmp_path, line, headband, camera, expected):
    env = _env(tmp_path, line)
    _run_ps1(tmp_path, env, headband, camera)
    assert _registry_line(env) == expected


def _sh_functions() -> str:
    src = (ROOT / "start.sh").read_text(encoding="utf-8")
    # The real set_env_key uses BSD `sed -i ''` (the script is macOS-only by
    # design), which GNU sed on a Windows Git Bash misreads. A portable
    # stand-in with the same contract keeps the function under test the real
    # one and the helper out of the way.
    shim = (
        "set_env_key() {\n"
        "    local path=\"$1\" key=\"$2\" value=\"$3\" tmp\n"
        "    [ -f \"$path\" ] || return 0\n"
        "    tmp=\"$(mktemp)\"\n"
        "    if grep -q \"^$key=\" \"$path\"; then\n"
        "        awk -v k=\"$key\" -v v=\"$value\" 'index($0, k\"=\")==1 {print k\"=\"v; next} {print}' \"$path\" > \"$tmp\"\n"
        "    else\n"
        "        cat \"$path\" > \"$tmp\"; printf '%s=%s\\n' \"$key\" \"$value\" >> \"$tmp\"\n"
        "    fi\n"
        "    cat \"$tmp\" > \"$path\"; rm -f \"$tmp\"\n"
        "}\n"
    )
    return _extract(src, "update_device_registry() {") + "\n" + shim


def _run_sh(tmp_path: Path, env: Path, headband: str, camera: str | None) -> None:
    script = tmp_path / "t.sh"
    args = f"'{env.as_posix()}' '{headband}'" + (f" '{camera}'" if camera else "")
    script.write_text(_sh_functions() + f"\nupdate_device_registry {args}\n", encoding="utf-8")
    subprocess.run([BASH, str(script)], check=True, capture_output=True, text=True)


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.parametrize("line,headband,expected", CASES)
def test_start_sh_repoints_the_default_entry(tmp_path, line, headband, expected):
    env = _env(tmp_path, line)
    _run_sh(tmp_path, env, headband, None)
    assert _registry_line(env) == expected
    assert "EEG_SOURCE=sim" in env.read_text(encoding="utf-8"), "other keys untouched"


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.parametrize("line,headband,camera,expected", CAMERA_CASES)
def test_start_sh_camera_branch_composes_onto_the_registry(tmp_path, line, headband, camera, expected):
    env = _env(tmp_path, line)
    _run_sh(tmp_path, env, headband, camera)
    assert _registry_line(env) == expected
