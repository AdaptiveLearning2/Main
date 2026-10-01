"""start.ps1 -Hosted: refused before any write unless safe, and its sidecar keys win; see CLAUDE.md "Launcher flags"."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values

from src.app.config import Settings

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("powershell")
BASH = shutil.which("bash")
WINDOWS = pytest.mark.skipif(sys.platform != "win32" or POWERSHELL is None, reason="start.ps1 is Windows")

BACKEND = "https://main-u0ki.onrender.com"
ORIGIN = "https://adaptive.pages.dev"
TOKEN = "Ab3_dEf-" * 5 + "xyz"  # 43 characters of token_urlsafe's alphabet, as start.ps1 makes
LOCAL = Settings.model_fields["allowed_origins"].default

GOOD = {"hosted": True, "muse": True, "backend": BACKEND, "origin": ORIGIN, "token": TOKEN, "env": True}
ARG_CASES = [
    # (change from GOOD, the substring each expected refusal carries)
    ({}, []),
    ({"backend": BACKEND + "/", "origin": ORIGIN + "/"}, []),
    ({"muse": False}, ["needs -Muse"]),
    ({"backend": "http://main-u0ki.onrender.com"}, ["-BackendUrl"]),
    ({"backend": BACKEND + "/api"}, ["-BackendUrl"]),
    ({"backend": ""}, ["-BackendUrl"]),
    ({"backend": "https://user:pw@main-u0ki.onrender.com"}, ["-BackendUrl"]),
    ({"origin": ORIGIN + "/login"}, ["-FrontendOrigin"]),
    ({"origin": "http://adaptive.pages.dev"}, ["-FrontendOrigin"]),
    ({"origin": "https://user@adaptive.pages.dev"}, ["-FrontendOrigin"]),
    ({"token": ""}, ["-LearnerToken"]),
    ({"token": "replace-me-learner-token"}, ["-LearnerToken"]),
    ({"token": "has a space"}, ["-LearnerToken"]),
    ({"env": False}, [".env.example"]),
    ({"muse": False, "env": False}, ["needs -Muse", ".env.example"]),
    # Without -Hosted nothing is required, but a hosted argument alone is a mistake worth saying.
    ({"hosted": False, "muse": False, "backend": "", "origin": "", "token": ""}, []),
    ({"hosted": False}, ["without -Hosted"]),
]


def _extract(text: str, start: str) -> str:
    """The function body from `start` to the first `}` at column 0."""
    m = re.search(re.escape(start) + r".*?^\}", text, re.S | re.M)
    assert m, start
    return m.group(0)


def _ps(tmp_path: Path, body: str, *functions: str, env: dict | None = None) -> subprocess.CompletedProcess:
    src = (ROOT / "start.ps1").read_text(encoding="utf-8")
    script = tmp_path / "t.ps1"
    script.write_text("\n".join(_extract(src, f"function {f} {{") for f in functions) + "\n" + body,
                      encoding="utf-8")
    return subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                          capture_output=True, text=True, env=env)


def _launch(tmp_path: Path, sidecar_env: str, backend: str, origin: str, token: str):
    """start.ps1 -Hosted -Muse, copied into a bare tree. Returns (process, sidecar .env, files before).

    Past the check it writes the .env, then stops at the missing libMuse SDK before any window opens.
    """
    shutil.copy(ROOT / "start.ps1", tmp_path / "start.ps1")
    env = tmp_path / "EEGResearch" / ".env"
    env.parent.mkdir()
    env.write_text(sidecar_env, encoding="utf-8")
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))
    r = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        str(tmp_path / "start.ps1"), "-Hosted", "-Muse", "-BackendUrl", backend,
                        "-FrontendOrigin", origin, "-LearnerToken", token],
                       capture_output=True, text=True, timeout=120)
    return r, env, before


@WINDOWS
@pytest.mark.parametrize("change,expected", ARG_CASES)
def test_hosted_arguments_are_refused_for_exactly_these_reasons(tmp_path, change, expected):
    a = {**GOOD, **change}
    env = tmp_path / ".env"
    if a["env"]:
        env.write_text("EEG_SOURCE=sim\n", encoding="utf-8")
    flag = lambda b: "$true" if b else "$false"  # noqa: E731
    r = _ps(tmp_path, (
        f"$errs = Test-HostedArgs -hosted {flag(a['hosted'])} -muse {flag(a['muse'])} "
        f"-backendUrl '{a['backend']}' -frontendOrigin '{a['origin']}' -learnerToken '{a['token']}' "
        f"-eegEnv '{env}'\n"
        "foreach ($e in $errs) { \"ERR=$e\" }\n"), "Test-HostedArgs")
    assert r.returncode == 0, r.stderr
    errors = [l[4:] for l in r.stdout.splitlines() if l.startswith("ERR=")]
    assert len(errors) == len(expected), errors
    for fragment in expected:
        assert any(fragment in e for e in errors), (fragment, errors)


SIDECAR_ENV = "EEG_SOURCE=sim\nAPI_TOKEN=replace-me-learner-token\nADMIN_TOKEN=replace-me-admin-token\n"


@WINDOWS
@pytest.mark.parametrize("change,message", [
    ({"backend": "http://main-u0ki.onrender.com"}, "-BackendUrl"),
    ({"backend": BACKEND + "/api"}, "-BackendUrl"),
    ({"origin": ORIGIN + "/login"}, "-FrontendOrigin"),
    ({"token": "replace-me-learner-token"}, "-LearnerToken"),
])
def test_a_refused_hosted_launch_writes_nothing_and_starts_nothing(tmp_path, change, message):
    a = {**GOOD, **change}
    r, env, before = _launch(tmp_path, SIDECAR_ENV, a["backend"], a["origin"], a["token"])
    assert r.returncode == 1, r.stdout
    assert message in r.stdout
    assert env.read_text(encoding="utf-8") == SIDECAR_ENV
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before


@WINDOWS
@pytest.mark.parametrize("source_line", ["EEG_SOURCE=sim\n", "EEG_SOURCE = sim\n", ""],
                         ids=["plain", "spaced", "missing"])
def test_a_hosted_launch_never_leaves_the_sidecar_on_the_simulator(tmp_path, source_line):
    sidecar_env = source_line + "API_TOKEN=replace-me-learner-token\nADMIN_TOKEN=replace-me-admin-token\n"
    r, env, _ = _launch(tmp_path, sidecar_env, BACKEND, ORIGIN, TOKEN)
    assert "libMuse SDK not found" in r.stdout, (r.stdout, r.stderr)  # where the bare tree stops
    v = dotenv_values(env)  # read as the sidecar reads it, last assignment winning
    assert v["EEG_SOURCE"] == "muse"
    # The learner token lands before the token step, which then generates only ADMIN_TOKEN.
    assert v["API_TOKEN"] == TOKEN and "Generated API_TOKEN" not in r.stdout
    assert v["ADMIN_TOKEN"] not in (TOKEN, "replace-me-admin-token")


@WINDOWS
@pytest.mark.parametrize("admin", ["replace-me-admin-token", TOKEN, "machine-own-admin-" + "a" * 30])
def test_the_hosted_keys_reach_every_copy_and_are_written_as_a_browser_sends_them(tmp_path, admin):
    eeg, backend, frontend = tmp_path / "eeg.env", tmp_path / "backend.env", tmp_path / "frontend.env"
    eeg.write_text(f"EEG_SOURCE=muse\nAPI_TOKEN=locally-generated-{'b' * 30}\nADMIN_TOKEN={admin}\n"
                   "PUSH_ENABLED=false\nBACKEND_URL=http://127.0.0.1:8000\n", encoding="utf-8")
    backend.write_text("EEG_API_TOKEN=old-local\nEEG_ADMIN_TOKEN=old-admin\n", encoding="utf-8")
    frontend.write_text("VITE_EEG_LOCAL_TOKEN=old-local\n", encoding="utf-8")
    r = _ps(tmp_path,
            f"Set-HostedToken '{eeg}' '{backend}' '{frontend}' '{TOKEN}'\n"
            f"Update-SidecarTokens '{eeg}' '{backend}'\n"
            f"Set-HostedSidecarEnv '{eeg}' 'HTTPS://Main-u0ki.onrender.com/' 'HTTPS://Adaptive.Pages.Dev:443/'\n",
            "Set-HostedToken", "Set-HostedSidecarEnv", "Set-EnvKey", "Get-EnvValue", "New-SidecarToken",
            "Update-SidecarTokens")
    assert r.returncode == 0, r.stderr
    v, b, f = dotenv_values(eeg), dotenv_values(backend), dotenv_values(frontend)
    # Every copy matches, or a later local run on this machine is refused by its own sidecar.
    assert v["API_TOKEN"] == b["EEG_API_TOKEN"] == f["VITE_EEG_LOCAL_TOKEN"] == TOKEN
    # CORS compares origins exactly, as a browser sends them: lower case, no default port.
    assert (v["PUSH_ENABLED"], v["BACKEND_URL"], v["ALLOWED_ORIGINS"]) == ("true", BACKEND, ORIGIN)
    assert v["EEG_SOURCE"] == "muse", "keys the hosted mode does not own are left alone"
    assert v["ADMIN_TOKEN"] != TOKEN and not v["ADMIN_TOKEN"].startswith("replace-me")
    if admin.startswith("machine-own-"):
        assert v["ADMIN_TOKEN"] == admin, "a real admin token is kept"
    else:
        assert b["EEG_ADMIN_TOKEN"] == v["ADMIN_TOKEN"], "a remade admin token reaches the backend too"


@WINDOWS
@pytest.mark.parametrize("current,expected", [
    (f"ALLOWED_ORIGINS={ORIGIN}", f"{ORIGIN},{LOCAL}"),
    ("ALLOWED_ORIGINS=http://localhost:4173", f"http://localhost:4173,{LOCAL}"),
    (f"ALLOWED_ORIGINS={LOCAL}", LOCAL),
    (f"ALLOWED_ORIGINS=http://localhost:5173, {ORIGIN}",
     f"http://localhost:5173,{ORIGIN},http://127.0.0.1:5173,http://localhost:8000"),
    ("", LOCAL),
], ids=["hosted-origin-kept", "hand-added-kept", "already-local", "spaces-and-order", "missing"])
def test_a_local_run_adds_the_local_origins_and_keeps_every_other(tmp_path, current, expected):
    env = tmp_path / ".env"
    env.write_text(f"EEG_SOURCE=sim\n{current}\n", encoding="utf-8")
    r = _ps(tmp_path, f"Add-LocalOrigins '{env}' '{LOCAL}'\n", "Add-LocalOrigins", "Set-EnvKey", "Get-EnvValue")
    assert r.returncode == 0, r.stderr
    assert dotenv_values(env)["ALLOWED_ORIGINS"] == expected


SESSION = {"EEG_SOURCE": "sim", "API_TOKEN": "t", "EEG_DEVICES": "default:sim"}


@WINDOWS
def test_the_hosted_sidecar_window_reads_its_env_file_not_this_session(tmp_path):
    # The real window command, run in a child as Start-Window runs it; uvicorn is a probe of the settings.
    eeg, bin_ = tmp_path / "EEGResearch", tmp_path / "bin"
    (eeg / ".venv" / "Scripts").mkdir(parents=True)
    bin_.mkdir()
    probe = tmp_path / "probe.py"
    probe.write_text("from src.app.config import Settings\ns = Settings()\n"
                     "print(f'SETTINGS={s.eeg_source}|{s.api_token}|{s.eeg_devices}')\n", encoding="utf-8")
    (bin_ / "uvicorn.cmd").write_text(f'@"{sys.executable}" "{probe}"\r\n', encoding="utf-8")
    (eeg / ".venv" / "Scripts" / "Activate.ps1").write_text(f"$env:PATH = '{bin_};' + $env:PATH\n", encoding="utf-8")
    (eeg / ".env").write_text(f"EEG_SOURCE=muse\nAPI_TOKEN={TOKEN}\nADMIN_TOKEN=machine-own-{'a' * 30}\n",
                              encoding="utf-8")
    aliases = {f.alias.upper() for f in Settings.model_fields.values() if f.alias}
    env = {k: v for k, v in os.environ.items() if k.upper() not in aliases}
    env.update(SESSION, PYTHONPATH=str(ROOT / "EEGResearch"))
    r = _ps(tmp_path, (
        f"$cmd = Get-SidecarCommand $true '{sys.executable}' '{eeg}'\n"
        f"powershell -NoProfile -Command \"cd '{eeg}'; $cmd\"\n"
        "\"PARENT=$env:EEG_SOURCE\"\n"), "Get-SidecarCommand", "Invoke-Quiet", env=env)
    assert r.returncode == 0, r.stderr
    assert f"SETTINGS=muse|{TOKEN}|" in r.stdout.splitlines(), (r.stdout, r.stderr)
    assert f"clearing {', '.join(sorted(SESSION))} in the sidecar's window" in r.stdout
    assert "PARENT=sim" in r.stdout, "the window that ran start.ps1 keeps its own variables"


def _script_body(text: str) -> str:
    """What runs at the top level: every function definition removed."""
    return re.sub(r"^function [\w-]+ \{\n.*?^\}\n", "", text, flags=re.S | re.M)


def test_the_hosted_keys_win_over_both_camera_branches():
    # Structural only because the branches need a venv and a camera; every other property above runs.
    ps1 = (ROOT / "start.ps1").read_text(encoding="utf-8")
    lines = _script_body(ps1).splitlines()
    hosted = next(i for i, l in enumerate(lines, 1) if re.search(r"^\s*Set-HostedSidecarEnv \$eegEnv", l))
    local = [i for i, l in enumerate(lines, 1)
             if re.search(r'Set-EnvKey \$eegEnv "(BACKEND_URL|PUSH_ENABLED)"', l)]
    assert local and hosted > max(local), (hosted, local)
    assert re.search(r'^\$localOrigins = "([^"]+)"', ps1, re.M).group(1) == LOCAL
    assert any(re.search(r"^\s*Add-LocalOrigins \$eegEnv \$localOrigins", l) for l in lines)


def test_the_sidecar_window_runs_the_command_that_clears_the_session():
    # Structural only because a bare launch stops at the libMuse check, before any window opens.
    lines = _script_body((ROOT / "start.ps1").read_text(encoding="utf-8")).splitlines()
    made = next(i for i, l in enumerate(lines) if re.match(r"\$eegCmd = Get-SidecarCommand \$Hosted ", l))
    assert re.match(r'Start-Window "EEG Backend :8001" \$eegDir \$eegCmd$', lines[made + 1])


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_start_sh_refuses_hosted_before_doing_anything(tmp_path):
    # A copy alone in a temp dir, PATH empty: if the refusal broke, nothing real could start.
    script = tmp_path / "start.sh"
    shutil.copy(ROOT / "start.sh", script)
    r = subprocess.run([BASH, str(script), "--hosted"], cwd=tmp_path, env={"PATH": ""},
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "--hosted needs a headband" in r.stdout
    assert [p.name for p in tmp_path.iterdir()] == ["start.sh"]
