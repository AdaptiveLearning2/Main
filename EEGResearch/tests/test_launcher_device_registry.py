"""Both launchers' registry and token functions, extracted and driven against a temp .env; see CLAUDE.md "The device registry"."""

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

# The function refused (false / non-zero) and left the file exactly as it was.
REFUSED = "REFUSED"

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
    # A named station already on the bridge address: refused, file untouched.
    ("EEG_DEVICES=default:sim,station1:muse@8765", "default:muse@8765", REFUSED),
]


CAMERA_CASES = [
    # (line, headband, camera, expected): -Camera composes onto the registry, never overwrites.
    ("EEG_DEVICES=station1:muse@8765,station2:muse@8766", "default:sim", "camera:face@0",
     "EEG_DEVICES=default:sim,station1:muse@8765,station2:muse@8766,camera:face@0"),
    ("EEG_DEVICES=default:sim,camera:face@1", "default:muse@8765", "camera:face@0",
     "EEG_DEVICES=default:muse@8765,camera:face@0"),
    ("EEG_DEVICES=default:muse@8765,station2:muse@8766", "default:sim", "camera:face@2",
     "EEG_DEVICES=default:sim,station2:muse@8766,camera:face@2"),
    # A fresh file with no registry line gets the pair the run needs.
    (None, "default:sim", "camera:face@0", "EEG_DEVICES=default:sim,camera:face@0"),
    # A named station on the headband's bridge port, with or without a default: entry: refused.
    ("EEG_DEVICES=station1:muse@8765,station2:muse@8766", "default:muse@8765", "camera:face@0", REFUSED),
    ("EEG_DEVICES=default:sim,station1:muse@8765", "default:muse@8765", "camera:face@0", REFUSED),
    # ...but a station on a different port does not stand in for the headband.
    ("EEG_DEVICES=station2:muse@8766", "default:muse@8765", "camera:face@0",
     "EEG_DEVICES=default:muse@8765,station2:muse@8766,camera:face@0"),
    # ...and two sim entries collide on nothing: there is no process behind sim.
    ("EEG_DEVICES=station1:sim", "default:sim", "camera:face@0",
     "EEG_DEVICES=default:sim,station1:sim,camera:face@0"),
]


def _env(tmp_path: Path, line: str | None) -> Path:
    p = tmp_path / ".env"
    p.write_text("EEG_SOURCE=sim\n" + (f"{line}\n" if line else "") + "API_TOKEN=t\n", encoding="utf-8")
    return p


def _registry_line(p: Path) -> str | None:
    lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.startswith("EEG_DEVICES=")]
    return lines[-1] if lines else None


def _first_line(text: str, pattern: str) -> int:
    for i, line in enumerate(text.splitlines(), 1):
        if re.search(pattern, line):
            return i
    raise AssertionError(pattern)


def _script_body(text: str) -> str:
    """What runs at the top level: every function definition removed, wherever it sits."""
    return re.sub(r"^(?:function [\w-]+ \{|\w+\(\) \{)\n.*?^\}\n", "", text, flags=re.S | re.M)


def test_the_registry_is_validated_before_any_write_and_applied_after_provisioning():
    """The check precedes every .env write; the apply follows camera model provisioning."""
    ps1 = _script_body((ROOT / "start.ps1").read_text(encoding="utf-8"))
    check = _first_line(ps1, r"Update-DeviceRegistry \$eegEnv .*-DryRun")
    writes = [_first_line(ps1, p) for p in (
        r"Set-EnvKey \$eegEnv", r"Set-Content \$eegEnv", r"Set-EnvKey \$backendEnv",
        r"Update-SidecarTokens \$eegEnv")]
    assert check < min(writes), (check, writes)
    lines = ps1.splitlines()
    applies = [i for i, l in enumerate(lines, 1)
               if re.search(r"Update-DeviceRegistry \$eegEnv", l) and "-DryRun" not in l]
    assert len(applies) == 2, "one apply per branch"
    face_on = _first_line(ps1, r'Set-EnvKey \$eegEnv "FACE_ENABLED" "true"')
    camera_apply = min(applies)
    assert camera_apply < face_on
    exits_between = [i for i in range(check, camera_apply) if re.search(r"^\s*exit 1", lines[i - 1])]
    assert exits_between, "the provisioning exits lie between the check and the apply"
    assert all(i < camera_apply for i in exits_between)

    sh = _script_body((ROOT / "start.sh").read_text(encoding="utf-8"))
    check = _first_line(sh, r'update_device_registry "\$EEG_ENV" .* check')
    writes = [_first_line(sh, p) for p in (
        r'set_env_key "\$EEG_ENV"', r"sed -i .*EEG_ENV", r'set_env_key "\$BACKEND_ENV"',
        r'ensure_sidecar_tokens "\$EEG_ENV"')]
    assert check < min(writes), (check, writes)
    lines = sh.splitlines()
    applies = [i for i, l in enumerate(lines, 1)
               if re.search(r'update_device_registry "\$EEG_ENV"', l) and " check" not in l]
    assert len(applies) == 2
    face_on = _first_line(sh, r'set_env_key "\$EEG_ENV" "FACE_ENABLED" "true"')
    camera_apply = min(applies)
    assert camera_apply < face_on
    exits_between = [i for i in range(check, camera_apply) if re.search(r"^\s*exit 1", lines[i - 1])]
    assert exits_between and all(i < camera_apply for i in exits_between)

    # The summary reads the key back rather than rebuilding it from two variables.
    assert "EEG_DEVICES = $headband" not in ps1 and "EEG_DEVICES = default:sim,camera" not in sh


@pytest.mark.skipif(sys.platform != "win32" or POWERSHELL is None, reason="start.ps1 is Windows")
@pytest.mark.parametrize("line,headband,camera,expected", CAMERA_CASES + [(l, h, None, e) for l, h, e in CASES])
def test_start_ps1_dry_run_refuses_the_same_cases_and_never_writes(tmp_path, line, headband, camera, expected):
    env = _env(tmp_path, line)
    before = env.read_text(encoding="utf-8")
    accepted = _run_ps1(tmp_path, env, headband, camera, dry_run=True)
    assert accepted == (expected != REFUSED)
    assert env.read_text(encoding="utf-8") == before, "a dry run writes nothing either way"


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.parametrize("line,headband,camera,expected", CAMERA_CASES + [(l, h, None, e) for l, h, e in CASES])
def test_start_sh_check_refuses_the_same_cases_and_never_writes(tmp_path, line, headband, camera, expected):
    env = _env(tmp_path, line)
    before = env.read_text(encoding="utf-8")
    accepted = _run_sh(tmp_path, env, headband, camera, dry_run=True)
    assert accepted == (expected != REFUSED)
    assert env.read_text(encoding="utf-8") == before


def _extract(text: str, start: str) -> str:
    """The function body from `start` to the first `}` at column 0."""
    m = re.search(re.escape(start) + r".*?^\}", text, re.S | re.M)
    assert m, start
    return m.group(0)


def _run_ps1(tmp_path: Path, env: Path, headband: str, camera: str | None, dry_run: bool = False) -> bool:
    """True if the function accepted the registry, False if it refused."""
    src = (ROOT / "start.ps1").read_text(encoding="utf-8")
    fns = _extract(src, "function Update-DeviceRegistry {") + "\n" + _extract(src, "function Set-EnvKey {")
    script = tmp_path / "t.ps1"
    args = f"'{env}' '{headband}'" + (f" '{camera}'" if camera else "") + (" -DryRun" if dry_run else "")
    script.write_text(fns + f"\nif (-not (Update-DeviceRegistry {args})) {{ exit 3 }}\n", encoding="utf-8")
    r = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                       capture_output=True, text=True)
    assert r.returncode in (0, 3), r.stderr
    return r.returncode == 0


def _check(env: Path, before: str, accepted: bool, expected) -> None:
    if expected == REFUSED:
        assert not accepted, "a conflicting registry must be refused"
        assert env.read_text(encoding="utf-8") == before, "a refused run writes nothing"
        return
    assert accepted
    assert _registry_line(env) == expected
    assert "EEG_SOURCE=sim" in env.read_text(encoding="utf-8"), "other keys untouched"


@pytest.mark.skipif(sys.platform != "win32" or POWERSHELL is None, reason="start.ps1 is Windows")
@pytest.mark.parametrize("line,headband,expected", CASES)
def test_start_ps1_repoints_the_default_entry(tmp_path, line, headband, expected):
    env = _env(tmp_path, line)
    before = env.read_text(encoding="utf-8")
    _check(env, before, _run_ps1(tmp_path, env, headband, None), expected)


@pytest.mark.skipif(sys.platform != "win32" or POWERSHELL is None, reason="start.ps1 is Windows")
@pytest.mark.parametrize("line,headband,camera,expected", CAMERA_CASES)
def test_start_ps1_camera_branch_composes_onto_the_registry(tmp_path, line, headband, camera, expected):
    env = _env(tmp_path, line)
    before = env.read_text(encoding="utf-8")
    _check(env, before, _run_ps1(tmp_path, env, headband, camera), expected)


def _sh_functions() -> str:
    src = (ROOT / "start.sh").read_text(encoding="utf-8")
    # The real set_env_key uses BSD `sed -i ''`, which Git Bash's GNU sed misreads; same-contract stand-in.
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


def _run_sh(tmp_path: Path, env: Path, headband: str, camera: str | None, dry_run: bool = False) -> bool:
    """True if the function accepted the registry, False if it refused."""
    script = tmp_path / "t.sh"
    args = f"'{env.as_posix()}' '{headband}' '{camera or ''}'" + (" check" if dry_run else "")
    script.write_text(_sh_functions() + f"\nupdate_device_registry {args} || exit 3\n", encoding="utf-8")
    r = subprocess.run([BASH, str(script)], capture_output=True, text=True)
    assert r.returncode in (0, 3), r.stderr
    return r.returncode == 0


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.parametrize("line,headband,expected", CASES)
def test_start_sh_repoints_the_default_entry(tmp_path, line, headband, expected):
    env = _env(tmp_path, line)
    before = env.read_text(encoding="utf-8")
    _check(env, before, _run_sh(tmp_path, env, headband, None), expected)


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.parametrize("line,headband,camera,expected", CAMERA_CASES)
def test_start_sh_camera_branch_composes_onto_the_registry(tmp_path, line, headband, camera, expected):
    env = _env(tmp_path, line)
    before = env.read_text(encoding="utf-8")
    _check(env, before, _run_sh(tmp_path, env, headband, camera), expected)


# ── the sidecar tokens: remade exactly when the sidecar would refuse them ───────

REAL = "k" * 43
TOKEN_CASES = [
    # (API_TOKEN value, ADMIN_TOKEN value, which keys must be remade)
    ("replace-me", "replace-me-admin", {"API_TOKEN", "ADMIN_TOKEN"}),
    # The sidecar lowercases and trims before it refuses, so the launcher must too.
    ("Replace-Me", REAL + "a", {"API_TOKEN"}),
    ("  replace-me  ", REAL + "a", {"API_TOKEN"}),
    ("", REAL + "a", {"API_TOKEN"}),
    (REAL, REAL, {"ADMIN_TOKEN"}),
    (REAL, REAL + "a", set()),
    # Tokens are case-sensitive: these differ, so the sidecar accepts them and neither is remade.
    (REAL.upper(), REAL, set()),
]


def _token_envs(tmp_path: Path, api: str, admin: str) -> tuple[Path, Path]:
    eeg, backend = tmp_path / "eeg.env", tmp_path / "backend.env"
    eeg.write_text(f"API_TOKEN={api}\nADMIN_TOKEN={admin}\nEEG_SOURCE=sim\n", encoding="utf-8")
    backend.write_text("SUPABASE_URL=http://localhost:54321\n", encoding="utf-8")
    return eeg, backend


def _values(path: Path) -> dict[str, str]:
    return dict(l.split("=", 1) for l in path.read_text(encoding="utf-8").splitlines() if "=" in l)


def _check_tokens(eeg: Path, backend: Path, api: str, admin: str, remade: set[str]) -> None:
    after, mirrored = _values(eeg), _values(backend)
    for key, before, backend_key in (("API_TOKEN", api, "EEG_API_TOKEN"), ("ADMIN_TOKEN", admin, "EEG_ADMIN_TOKEN")):
        if key in remade:
            assert after[key] != before and len(after[key]) == 43, key
            assert mirrored[backend_key] == after[key], f"{backend_key} must match the new {key}"
        else:
            assert after[key] == before, f"{key} was a usable token and must be kept"
            assert backend_key not in mirrored
    assert after["API_TOKEN"] != after["ADMIN_TOKEN"]


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.parametrize("api,admin,remade", TOKEN_CASES)
def test_start_sh_remakes_exactly_the_tokens_the_sidecar_refuses(tmp_path, api, admin, remade):
    eeg, backend = _token_envs(tmp_path, api, admin)
    fn = _extract((ROOT / "start.sh").read_text(encoding="utf-8"), "ensure_sidecar_tokens() {")
    script = tmp_path / "t.sh"
    python = Path(sys.executable).as_posix()
    script.write_text(_sh_functions() + fn + f"\nPYTHON='{python}'\n"
                      f"ensure_sidecar_tokens '{eeg.as_posix()}' '{backend.as_posix()}'\n", encoding="utf-8")
    r = subprocess.run([BASH, str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    _check_tokens(eeg, backend, api, admin, remade)


@pytest.mark.skipif(sys.platform != "win32" or POWERSHELL is None, reason="start.ps1 is Windows")
@pytest.mark.parametrize("api,admin,remade", TOKEN_CASES)
def test_start_ps1_remakes_exactly_the_tokens_the_sidecar_refuses(tmp_path, api, admin, remade):
    eeg, backend = _token_envs(tmp_path, api, admin)
    src = (ROOT / "start.ps1").read_text(encoding="utf-8")
    fns = "\n".join(_extract(src, f"function {name} {{") for name in
                    ("Set-EnvKey", "Get-EnvValue", "New-SidecarToken", "Update-SidecarTokens"))
    script = tmp_path / "t.ps1"
    script.write_text(fns + f"\nUpdate-SidecarTokens '{eeg}' '{backend}'\n", encoding="utf-8")
    r = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    _check_tokens(eeg, backend, api, admin, remade)
