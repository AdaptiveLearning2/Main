"""start.ps1 -Hosted: refused before any write unless safe, and its sidecar keys win; see CLAUDE.md "Launcher flags"."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import zipfile
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


LEFTOVERS = {"EEG_SOURCE": "sim", "API_TOKEN": "t", "EEG_DEVICES": "default:sim", "MUSE_BRIDGE_PORT": "8766"}
_BRIDGE_SRC = "".join(p.read_text(encoding="utf-8", errors="ignore")
                      for p in (ROOT / "EEGResearch" / "native_bridge" / "src").rglob("*") if p.is_file())
# Direct getenv names plus every "MUSE_*" literal: a name read through a helper (env_flag_off) has no getenv beside it.
BRIDGE_VARS = sorted((set(re.findall(r'getenv\("([A-Z_]+)"\)', _BRIDGE_SRC))
                      | set(re.findall(r'"(MUSE_[A-Z_]+)"', _BRIDGE_SRC))) - {"LOCALAPPDATA"})


def _clean_env(where: str, leftovers: dict) -> dict:
    """This environment minus every name either window reads; `session` puts the leftovers back."""
    names = {f.alias.upper() for f in Settings.model_fields.values() if f.alias} | set(BRIDGE_VARS)
    env = {k: v for k, v in os.environ.items() if k.upper() not in names}
    env["PYTHONPATH"] = str(ROOT / "EEGResearch")
    if where == "session":
        env.update(leftovers)
    return env


def _window(where: str, leftovers: dict, workdir: Path) -> str:
    """$cmd in a child as Start-Window opens it; `profile` sets the leftovers in it before the command."""
    pre = "".join(f"`$env:{k}='{v}'; " for k, v in leftovers.items()) if where == "profile" else ""
    return f"powershell -NoProfile -Command \"{pre}cd '{workdir}'; $cmd\"\n"


@WINDOWS
@pytest.mark.parametrize("where", ["session", "profile"])
def test_the_hosted_sidecar_window_reads_its_env_file_not_this_session(tmp_path, where):
    # The real window command; uvicorn is a probe printing the settings the sidecar would load.
    eeg, bin_ = tmp_path / "EEGResearch", tmp_path / "bin"
    (eeg / ".venv" / "Scripts").mkdir(parents=True)
    bin_.mkdir()
    probe = tmp_path / "probe.py"
    probe.write_text("from src.app.config import Settings\ns = Settings()\nprint(f'SETTINGS={s.eeg_source}|"
                     "{s.api_token}|{s.eeg_devices}|{s.muse_bridge_port}')\n", encoding="utf-8")
    (bin_ / "uvicorn.cmd").write_text(f'@"{sys.executable}" "{probe}"\r\n', encoding="utf-8")
    (eeg / ".venv" / "Scripts" / "Activate.ps1").write_text(f"$env:PATH = '{bin_};' + $env:PATH\n", encoding="utf-8")
    (eeg / ".env").write_text(f"EEG_SOURCE=muse\nAPI_TOKEN={TOKEN}\nADMIN_TOKEN=machine-own-{'a' * 30}\n",
                              encoding="utf-8")
    r = _ps(tmp_path, (
        "$ErrorActionPreference = 'Stop'\n"
        f"$cmd = Get-SidecarCommand $true '{sys.executable}' '{eeg}'\n"
        + _window(where, LEFTOVERS, eeg) + "\"PARENT=$env:EEG_SOURCE\"\n"),
        "Get-SidecarCommand", "Get-ClearCommand", "Invoke-Quiet", env=_clean_env(where, LEFTOVERS))
    assert r.returncode == 0, r.stderr
    # 8765: the bridge's own default, which its window falls back to once cleared too.
    assert f"SETTINGS=muse|{TOKEN}||8765" in r.stdout.splitlines(), (r.stdout, r.stderr)
    named = f"clearing {', '.join(sorted(LEFTOVERS))} in the sidecar's window"
    assert (named in r.stdout) == (where == "session"), r.stdout
    assert f"PARENT={'sim' if where == 'session' else ''}" in r.stdout.splitlines(), "the launching window is left alone"


@WINDOWS
@pytest.mark.parametrize("where", ["session", "profile"])
@pytest.mark.parametrize("optics,preset,expected", [
    (False, "", {}),
    (True, "", {"MUSE_ENABLE_OPTICS": "1"}),
    (True, "1034", {"MUSE_ENABLE_OPTICS": "1", "MUSE_OPTICS_PRESET": "1034"}),
], ids=["no-optics", "optics", "optics-preset"])
def test_the_hosted_bridge_window_reads_only_what_the_flags_set(tmp_path, where, optics, preset, expected):
    # Every name the bridge's source reads is left over as "9"; the stub supervisor prints what the bridge gets.
    assert {"MUSE_BRIDGE_PORT", "MUSE_AUTO_RECONNECT"} <= set(BRIDGE_VARS), BRIDGE_VARS  # a scan that misses passes
    stub = tmp_path / "supervisor.ps1"
    stub.write_text("param([string]$Exe)\n" + "".join(f'"{n}=" + $env:{n}\n' for n in BRIDGE_VARS), encoding="utf-8")
    leftovers = {n: "9" for n in BRIDGE_VARS}
    r = _ps(tmp_path, (
        "$ErrorActionPreference = 'Stop'\n"
        f"$cmd = Get-BridgeCommand $true ${optics} '{preset}' '{stub}' 'bridge.exe'\n"
        + _window(where, leftovers, tmp_path)), "Get-BridgeCommand", "Get-ClearCommand", env=_clean_env(where, leftovers))
    assert r.returncode == 0, r.stderr
    seen = dict(l.split("=", 1) for l in r.stdout.splitlines() if l.split("=", 1)[0] in BRIDGE_VARS)
    assert seen == {n: expected.get(n, "") for n in BRIDGE_VARS}


@WINDOWS
@pytest.mark.parametrize("python", ["missing.exe", "fails.cmd"])
def test_a_hosted_run_stops_when_the_sidecars_settings_cannot_be_read(tmp_path, python):
    (tmp_path / "fails.cmd").write_text("@echo No module named src 1>&2\r\n@exit /b 1\r\n", encoding="utf-8")
    r = _ps(tmp_path, ("$ErrorActionPreference = 'Stop'\n"
                       f"$cmd = Get-SidecarCommand $true '{tmp_path / python}' '{tmp_path}'\n"
                       "'NOT REACHED'\n"), "Get-SidecarCommand", "Get-ClearCommand", "Invoke-Quiet")
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "could not read the sidecar's settings" in r.stdout and "NOT REACHED" not in r.stdout


@pytest.fixture(scope="module")
def built_venv(tmp_path_factory):
    """A project folder with a real, empty venv, so Check-Venv probes instead of rebuilding."""
    project = tmp_path_factory.mktemp("venv") / "project"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(project / ".venv")], check=True)
    return project


# The probe's first answer and its later ones; the probes made, A with --absent; then the outcome.
# The backend's exact pins install only what is absent, by name, and warn about one held at another version.
VENV_CASES = pytest.mark.parametrize("manifest,before,after,probes,installs,refused,warned", [
    ("pyproject.toml", "", "", "F", 0, False, False),
    ("pyproject.toml", "httpx", "", "FF", 1, False, False),
    ("pyproject.toml", "httpx", "httpx", "FF", 1, True, False),
    ("requirements.txt", "", "", "AF", 0, False, False),
    ("requirements.txt", "httpx uvicorn[standard]==0.53.0", "", "AAF", 1, False, False),
    ("requirements.txt", "httpx", "httpx", "AA", 1, True, False),
    ("requirements.txt", "", "fastapi==9", "AF", 0, False, True),
], ids=["pyproject-complete", "pyproject-healed", "pyproject-still-missing", "requirements-complete",
        "requirements-healed", "requirements-still-missing", "requirements-other-version"])
FIX = {"pyproject.toml": "pip install -e .", "requirements.txt": 'pip install "httpx"'}
GLOB_BAIT = "uvicorns==0.53.0"  # what `uvicorn[standard]==0.53.0` matches if a shell globs it
WARNING = "differs from these requirements.txt pins: fastapi==9"
PROBE_FILES = pytest.mark.parametrize("manifest,content", [
    ("pyproject.toml", '[project]\nname = "x"\nversion = "0"\ndependencies = [\n  "pytest>=1",\n  "pydantic>=9999",\n'
                       '  "no-such-distribution-anywhere>=1",\n  "nor-this-one>=2"\n]\n'),
    ("requirements.txt", "pytest>=1\npydantic>=9999\nno-such-distribution-anywhere>=1\nnor-this-one>=2\n"),
], ids=["pyproject", "requirements"])
# pydantic is installed but older than asked, so it is named unless only absent packages are.
PROBE_ANSWERS = pytest.mark.parametrize("absent,expected", [
    (False, ["pydantic>=9999", "no-such-distribution-anywhere>=1", "nor-this-one>=2"]),
    (True, ["no-such-distribution-anywhere>=1", "nor-this-one>=2"]),
], ids=["all", "absent"])


def _with_probe(tmp_path: Path) -> Path:
    """An EEGResearch folder beside the script under test, holding the probe where both launchers look."""
    scripts = tmp_path / "EEGResearch" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(ROOT / "EEGResearch" / "scripts" / "missing_runtime_deps.py", scripts)
    return scripts.parent


@WINDOWS
@VENV_CASES
def test_check_venv_installs_what_a_pulled_dependency_list_added(tmp_path, built_venv, manifest, before, after,
                                                                 probes, installs, refused, warned):
    for name in FIX:
        (built_venv / name).unlink(missing_ok=True)
    (built_venv / manifest).write_text("", encoding="utf-8")
    # `python` on PATH must match the venv's version, or Check-Venv rebuilds instead of probing.
    env = dict(os.environ, PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    r = _ps(tmp_path, (
        "$script:probes = 0\n"
        "function Get-MissingDeps { param($python, $manifest, [switch]$Absent); $script:probes++\n"
        "    Write-Host \"PROBE $python $manifest $(if ($Absent) { 'A' } else { 'F' })\"\n"
        f"    if ($script:probes -eq 1) {{ @('{before}' -split ' ') | Where-Object {{ $_ }} }}"
        f" else {{ @('{after}' -split ' ') | Where-Object {{ $_ }} }} }}\n"
        "function Install-VenvDeps { param($dir, [string[]]$packages)\n"
        "    \"INSTALL $($packages.Count) $($packages -join ' ')\".TrimEnd() }\n"
        f"Check-Venv '{built_venv}'\n"
        "'RETURNED'\n"), "Check-Venv", "Invoke-Quiet", env=env)
    out = r.stdout.splitlines()
    python = built_venv / ".venv" / "Scripts" / "python.exe"
    assert [line for line in out if line.startswith("PROBE ")] == [
        f"PROBE {python} {built_venv / manifest} {kind}" for kind in probes], r.stdout
    named = f"{len(before.split())} {before}" if manifest == "requirements.txt" else "0"
    assert [line for line in out if line.startswith("INSTALL")] == [f"INSTALL {named}"] * installs, r.stdout
    assert (r.returncode, "RETURNED" in out) == ((1, False) if refused else (0, True)), (r.stdout, r.stderr)
    assert (WARNING in r.stdout) == warned, r.stdout
    if refused:
        assert "still lacks httpx" in r.stdout and FIX[manifest] in r.stdout


@WINDOWS
@PROBE_FILES
@PROBE_ANSWERS
def test_get_missing_deps_reports_every_requirement_the_probe_prints(tmp_path, manifest, content, absent, expected):
    _with_probe(tmp_path)
    (tmp_path / manifest).write_text(content, encoding="utf-8")
    switch = " -Absent" if absent else ""
    r = _ps(tmp_path, (f"$m = @(Get-MissingDeps '{sys.executable}' '{tmp_path / manifest}'{switch})\n"
                       "\"COUNT=$($m.Count)\"; $m\n"), "Get-MissingDeps", "Invoke-Quiet")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == [f"COUNT={len(expected)}", *expected]


def _wheel(folder: Path, name: str, version: str) -> None:
    """A pure wheel built by hand, so pip installs it offline and with no build backend."""
    stem = f"{name.replace('-', '_')}-{version}"
    with zipfile.ZipFile(folder / f"{stem}-py3-none-any.whl", "w") as whl:
        whl.writestr(f"{name.replace('-', '_')}/__init__.py", "")
        whl.writestr(f"{stem}.dist-info/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        whl.writestr(f"{stem}.dist-info/WHEEL",
                     "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        whl.writestr(f"{stem}.dist-info/RECORD", "")


@WINDOWS
def test_check_venv_installs_missing_backend_packages_and_leaves_one_held_at_another_version(tmp_path):
    """Real pip, offline. The pinned 2.0 is on hand, so only an install by name keeps the 1.0 a developer chose."""
    _with_probe(tmp_path)
    backend, wheels = tmp_path / "backend", tmp_path / "wheels"
    wheels.mkdir()
    for name, version in (("tiny-held", "1.0"), ("tiny-held", "2.0"), ("tiny-added", "1.0"), ("tiny-also", "1.0")):
        _wheel(wheels, name, version)
    subprocess.run([sys.executable, "-m", "venv", str(backend / ".venv")], check=True)
    env = dict(os.environ, PIP_NO_INDEX="1", PIP_FIND_LINKS=str(wheels), PIP_DISABLE_PIP_VERSION_CHECK="1",
               PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    pip = [str(backend / ".venv" / "Scripts" / "python.exe"), "-m", "pip"]
    subprocess.run([*pip, "install", "-q", "tiny-held==1.0"], check=True, env=env)
    (backend / "requirements.txt").write_text("tiny-held==2.0\ntiny-added==1.0\ntiny-also==1.0\n", encoding="utf-8")
    body = '$ErrorActionPreference = "Stop"\n' + f"Check-Venv '{backend}'\n'RETURNED'\n"
    r = _ps(tmp_path, body, "Check-Venv", "Get-MissingDeps", "Install-VenvDeps", "Invoke-Quiet", env=env)
    assert r.returncode == 0 and "RETURNED" in r.stdout, (r.stdout, r.stderr)
    assert "Installing what this venv lacks: tiny-added==1.0, tiny-also==1.0" in r.stdout, r.stdout
    assert "differs from these requirements.txt pins: tiny-held==2.0" in r.stdout, r.stdout
    frozen = subprocess.run([*pip, "list", "--format=freeze"], capture_output=True, text=True, env=env).stdout
    assert {"tiny-held==1.0", "tiny-added==1.0", "tiny-also==1.0"} <= set(frozen.split()), frozen


def _stub(path: Path, body: str) -> None:
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _sh_venv(tmp_path: Path, python: str, name: str = "EEGResearch") -> Path:
    """A project whose .venv/bin/python is a shell stub running `python`, laid out as start.sh expects."""
    project = tmp_path / name
    (project / ".venv" / "bin").mkdir(parents=True)
    (project / ".venv" / "bin" / "activate").write_text("", encoding="utf-8")
    _stub(project / ".venv" / "bin" / "python", python)
    return project


def _sh(tmp_path: Path, body: str, *functions: str) -> subprocess.CompletedProcess:
    src = (ROOT / "start.sh").read_text(encoding="utf-8")
    script = tmp_path / "t.sh"
    script.write_text("".join(_extract(src, f"{fn}() {{") + "\n" for fn in functions) + body,
                      encoding="utf-8", newline="\n")
    return subprocess.run([BASH, str(script)], capture_output=True, text=True, timeout=60)


@pytest.mark.skipif(BASH is None, reason="needs bash")
@VENV_CASES
def test_start_sh_check_venv_installs_what_a_pulled_dependency_list_added(tmp_path, manifest, before, after,
                                                                          probes, installs, refused, warned):
    mode = "editable" if manifest == "pyproject.toml" else "requirements"
    project = _sh_venv(tmp_path, "echo 3.14")  # PYTHON is this stub too, so check_venv probes, not rebuilds
    (tmp_path / GLOB_BAIT).write_text("", encoding="utf-8")
    probed, calls = (tmp_path / "probed").as_posix(), (tmp_path / "calls").as_posix()
    r = _sh(tmp_path, (
        f"cd '{tmp_path.as_posix()}'\n"
        f"missing_deps() {{ echo \"$*\" >> '{calls}'\n"
        f"    if [ -e '{probed}' ]; then echo '{after}'; else : > '{probed}'; echo '{before}'; fi; }}\n"
        'install_venv_deps() { echo "INSTALL $2 $(($# - 2))${3:+ ${*:3}}"; }\n'
        f"PYTHON='{(project / '.venv' / 'bin' / 'python').as_posix()}'\n"
        f"check_venv '{project.as_posix()}' {mode}\n"
        "echo RETURNED\n"), "check_venv")
    out = r.stdout.splitlines()
    named = f"{len(before.split())} {before}" if mode == "requirements" else "0"
    assert [line for line in out if line.startswith("INSTALL")] == [f"INSTALL {mode} {named}"] * installs, r.stdout
    assert (r.returncode, "RETURNED" in out) == ((1, False) if refused else (0, True)), (r.stdout, r.stderr)
    probe = f"{project.as_posix()} {project.as_posix()}/{manifest}"
    assert Path(calls).read_text(encoding="utf-8").splitlines() == [
        probe + (" --absent" if kind == "A" else "") for kind in probes]
    assert (WARNING in r.stdout) == warned, r.stdout
    if refused:
        assert "still lacks httpx" in r.stdout and FIX[manifest] in r.stdout


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.parametrize("mode", ["editable", "requirements"])
def test_start_sh_check_venv_builds_a_missing_venv_and_installs_into_it(tmp_path, mode):
    project = tmp_path / "EEGResearch"
    project.mkdir()
    _stub(tmp_path / "python3", 'echo "VENV $(basename "$(pwd)") $*"')
    r = _sh(tmp_path, ('install_venv_deps() { echo "INSTALL $2"; }\n'
                       f"PYTHON='{(tmp_path / 'python3').as_posix()}'\n"
                       f"check_venv '{project.as_posix()}' {mode}\n"), "check_venv")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines()[-2:] == ["VENV EEGResearch -m venv .venv", f"INSTALL {mode}"], r.stdout


@pytest.mark.skipif(BASH is None, reason="needs bash")
@PROBE_FILES
@PROBE_ANSWERS
def test_start_sh_missing_deps_reports_every_requirement_the_probe_prints(tmp_path, manifest, content, absent,
                                                                          expected):
    project = _sh_venv(tmp_path, f"exec '{Path(sys.executable).as_posix()}' \"$@\"", name="project")
    eeg = _with_probe(tmp_path)  # apart from the venv's folder, as the backend's is
    (project / manifest).write_text(content, encoding="utf-8")
    flag = " --absent" if absent else ""
    r = _sh(tmp_path, (f"EEG_DIR='{eeg.as_posix()}'\n"
                       f"missing_deps '{project.as_posix()}' '{(project / manifest).as_posix()}'{flag}\n"),
            "missing_deps")
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == expected


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.parametrize("call,args", [
    ("editable", "install -e . -q"),
    ("requirements", "install -r requirements.txt -q"),
    ("requirements 'tiny==1' 'uvicorn[standard]==0.53.0'", "install tiny==1 uvicorn[standard]==0.53.0 -q"),
], ids=["editable", "requirements", "named"])
def test_start_sh_installs_from_the_project_folder(tmp_path, call, args):
    project = _sh_venv(tmp_path, "exit 1")
    (project / "here").write_text("project", encoding="utf-8")
    (project / GLOB_BAIT).write_text("", encoding="utf-8")
    _stub(project / ".venv" / "bin" / "pip", 'echo "$(cat here) $*"')  # `here` resolves only from the project
    r = _sh(tmp_path, f"install_venv_deps '{project.as_posix()}' {call}\n", "install_venv_deps")
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == [f"project {args}"]


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


@pytest.mark.parametrize("var,call,window", [
    ("eegCmd", "Get-SidecarCommand", "EEG Backend :8001"),
    ("bridgeCmd", "Get-BridgeCommand", "Muse Bridge :8765"),
])
def test_both_windows_run_the_commands_that_clear_the_session(var, call, window):
    # Structural only because a bare launch stops at the libMuse check, before any window opens.
    lines = _script_body((ROOT / "start.ps1").read_text(encoding="utf-8")).splitlines()
    made = next(i for i, l in enumerate(lines) if re.match(rf"\s*\${var} = {call} \$Hosted\.IsPresent ", l))
    used = next(i for i, l in enumerate(lines) if re.match(rf'\s*Start-Window "{window}" \$eegDir \${var}$', l))
    assert made < used and not any(re.match(rf"\s*\${var} =", l) for l in lines[made + 1:used])


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
