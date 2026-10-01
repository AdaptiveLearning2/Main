"""start.ps1 -Hosted: refused before any write unless safe, and its sidecar keys win; see CLAUDE.md "Launcher flags"."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src.app.config import Settings

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("powershell")
BASH = shutil.which("bash")
WINDOWS = pytest.mark.skipif(sys.platform != "win32" or POWERSHELL is None, reason="start.ps1 is Windows")

BACKEND = "https://main-u0ki.onrender.com"
ORIGIN = "https://adaptive.pages.dev"
TOKEN = "Ab3_dEf-" * 5 + "xyz"  # 43 characters of token_urlsafe's alphabet, as start.ps1 makes

GOOD = {"hosted": True, "muse": True, "backend": BACKEND, "origin": ORIGIN, "token": TOKEN, "env": True}
ARG_CASES = [
    # (change from GOOD, the substring each expected refusal carries)
    ({}, []),
    ({"backend": BACKEND + "/", "origin": ORIGIN + "/"}, []),
    ({"muse": False}, ["needs -Muse"]),
    ({"backend": "http://main-u0ki.onrender.com"}, ["-BackendUrl"]),
    ({"backend": ""}, ["-BackendUrl"]),
    ({"origin": ORIGIN + "/login"}, ["-FrontendOrigin"]),
    ({"origin": "http://adaptive.pages.dev"}, ["-FrontendOrigin"]),
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


def _ps(tmp_path: Path, body: str, *functions: str) -> subprocess.CompletedProcess:
    src = (ROOT / "start.ps1").read_text(encoding="utf-8")
    script = tmp_path / "t.ps1"
    script.write_text("\n".join(_extract(src, f"function {f} {{") for f in functions) + "\n" + body,
                      encoding="utf-8")
    return subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                          capture_output=True, text=True)


def _values(path: Path) -> dict[str, str]:
    """Each key's last assignment, as dotenv reads it."""
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)


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


@WINDOWS
@pytest.mark.parametrize("admin", ["replace-me-admin-token", TOKEN, "machine-own-admin-" + "a" * 30])
def test_the_hosted_keys_are_written_and_admin_stays_this_machines_own(tmp_path, admin):
    eeg = tmp_path / "eeg.env"
    eeg.write_text(
        "EEG_SOURCE=muse\nAPI_TOKEN=locally-generated-" + "b" * 30 + f"\nADMIN_TOKEN={admin}\n"
        "PUSH_ENABLED=false\nBACKEND_URL=http://127.0.0.1:8000\n", encoding="utf-8")
    r = _ps(tmp_path,
            f"Set-HostedSidecarEnv '{eeg}' '{BACKEND}/' '{ORIGIN}/' '{TOKEN}'\n"
            f"Update-SidecarTokens '{eeg}' '{tmp_path / 'absent-backend.env'}'\n",
            "Set-HostedSidecarEnv", "Set-EnvKey", "Get-EnvValue", "New-SidecarToken", "Update-SidecarTokens")
    assert r.returncode == 0, r.stderr
    v = _values(eeg)
    assert v["API_TOKEN"] == TOKEN
    assert (v["PUSH_ENABLED"], v["BACKEND_URL"], v["ALLOWED_ORIGINS"]) == ("true", BACKEND, ORIGIN)
    assert v["EEG_SOURCE"] == "muse", "keys the hosted mode does not own are left alone"
    # The sidecar refuses a placeholder, and an admin token equal to the public learner one.
    assert v["ADMIN_TOKEN"] != TOKEN and not v["ADMIN_TOKEN"].startswith("replace-me")
    if admin.startswith("machine-own-"):
        assert v["ADMIN_TOKEN"] == admin, "a real admin token is kept"


def _script_body(text: str) -> str:
    """What runs at the top level: every function definition removed."""
    return re.sub(r"^function [\w-]+ \{\n.*?^\}\n", "", text, flags=re.S | re.M)


def test_hosted_is_checked_before_any_write_and_its_keys_win_over_both_branches():
    body = (ROOT / "start.ps1").read_text(encoding="utf-8")
    lines = _script_body(body).splitlines()

    def first(pattern: str) -> int:
        return next(i for i, l in enumerate(lines, 1) if re.search(pattern, l))

    check = first(r"Test-HostedArgs ")
    assert check < min(first(p) for p in (
        r"Set-EnvKey \$eegEnv", r"Set-Content \$eegEnv", r"Update-SidecarTokens \$eegEnv", r"Start-Window"))
    hosted = first(r"^\s*Set-HostedSidecarEnv \$eegEnv")
    local = [i for i, l in enumerate(lines, 1)
             if re.search(r'Set-EnvKey \$eegEnv "(BACKEND_URL|PUSH_ENABLED|API_TOKEN)"', l)]
    assert local and hosted > max(local), (hosted, local)
    # The learner token just changed, so the admin token is re-checked against it afterwards.
    assert any(i > hosted and re.search(r"Update-SidecarTokens \$eegEnv", l) for i, l in enumerate(lines, 1))


def test_a_local_run_restores_the_sidecars_own_default_origins():
    ps1 = (ROOT / "start.ps1").read_text(encoding="utf-8")
    m = re.search(r'^\$localOrigins = "([^"]+)"', ps1, re.M)
    assert m and m.group(1) == Settings.model_fields["allowed_origins"].default
    assert re.search(r'^\s*Set-EnvKey \$eegEnv "ALLOWED_ORIGINS" \$localOrigins', _script_body(ps1), re.M)


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
