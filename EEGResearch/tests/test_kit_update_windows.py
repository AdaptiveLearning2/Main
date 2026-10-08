"""The self-updater's Windows half: who is signed in, who this process is, the proxy, refused modes, the Update scan."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path, PureWindowsPath

import pytest
from launcher_cases import ROOT

from src.kit import update, winproc

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows sessions, tokens and registry")
EEG = ROOT / "EEGResearch"


def _whoami(*args: str) -> str:
    return subprocess.run(["whoami", *args], capture_output=True, text=True, check=True).stdout


def test_this_session_is_signed_in_with_a_sign_in_time():
    mine = winproc.own_session()
    if mine == 0:
        pytest.skip("running in a service session, which has no sign-in")
    sessions = {s.id: s for s in winproc.signed_in_sessions()}
    assert mine in sessions and sessions[mine].active
    assert 0 not in sessions  # services' session: nobody signs in to it
    logon = sessions[mine].logon
    assert logon is not None and time.time() - 400 * 86400 < logon <= time.time()


def test_system_and_elevation_agree_with_whoami():
    assert winproc.running_as_system() == ("S-1-5-18" in _whoami("/user"))
    groups = _whoami("/groups")
    assert winproc.running_elevated() == ("S-1-16-12288" in groups or "S-1-16-16384" in groups)


def test_the_machine_guid_reads():
    assert re.fullmatch(r"[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}", winproc.machine_guid() or "", re.I)


def test_the_winhttp_proxy_agrees_with_netsh():
    shown = subprocess.run(["netsh", "winhttp", "show", "proxy"], capture_output=True, text=True).stdout
    proxy = winproc.winhttp_proxy()
    if "Direct access" in shown:
        assert proxy is None
    else:
        assert proxy and proxy.startswith("http")


def test_a_student_running_an_update_mode_gets_2_and_nothing_is_written(tmp_path):
    app = tmp_path / update.APP_FOLDER
    app.mkdir()
    env = {**os.environ, "KIT_APP_DIR": str(app), "PYTHONPATH": str(EEG)}
    for mode in update.MODES:
        result = subprocess.run([sys.executable, "-m", "src.kit", mode], cwd=EEG, env=env, capture_output=True,
                                timeout=60)
        assert result.returncode == 2, (mode, result.stderr)
    assert list(app.iterdir()) == []


def test_an_installer_action_launches_whether_or_not_its_installer_is_there(tmp_path):
    updates = PureWindowsPath(tmp_path / "Program Files x" / "AdaptiveLearning Sensors" / "updates")
    Path(updates).mkdir(parents=True)
    command, arguments = update.installer_action(os.environ["SystemRoot"], updates / "apply.exe", updates / "apply.log")
    line = f'"{command}" {arguments}'  # as Task Scheduler starts it: the quoted command, then its arguments
    assert subprocess.run(line, capture_output=True, timeout=30).returncode == 0  # absent: nothing to do
    # robocopy as the installer exits 16, which cmd itself never does: it launched, from a path with spaces.
    shutil.copy(Path(os.environ["SystemRoot"]) / "System32" / "robocopy.exe", Path(updates) / "apply.exe")
    assert subprocess.run(line, capture_output=True, timeout=30).returncode == 16


def test_the_idle_flag_reaches_a_reader_in_another_process(tmp_path):
    mine = winproc.own_session()
    flag = winproc.IdleFlag(mine)
    read = [sys.executable, "-c", f"from src.kit import winproc; print(winproc.session_idle({mine}))"]
    env = {**os.environ, "PYTHONPATH": str(EEG)}
    for value in (True, False):
        flag.set(value)
        assert subprocess.run(read, cwd=EEG, env=env, capture_output=True, text=True).stdout.strip() == str(value)
    assert winproc.session_idle(987654) is None  # no kit there to report
    assert winproc.idle_seconds() >= 0


def test_an_idle_flag_another_user_owns_is_ignored(monkeypatch):
    mine = winproc.own_session()
    flag = winproc.IdleFlag(mine)
    flag.set(True)
    assert winproc.owner_sid(flag.handle) == winproc.session_user_sid(mine)
    assert winproc.session_idle(mine) is True
    system = bytes([1, 1, 0, 0, 0, 0, 0, 5, 18, 0, 0, 0])  # S-1-5-18: as if someone else's session
    monkeypatch.setattr(winproc, "session_user_sid", lambda session: system)
    assert winproc.session_idle(mine) is None


def test_the_scan_finds_a_secret_in_either_encoding_or_a_settings_file(tmp_path):
    sys.path.insert(0, str(EEG / "installer"))
    import kit_build  # noqa: PLC0415 -- imports winreg

    token, key = "Tok3n_" * 7, "K3y-" * 10
    folder = tmp_path / "kit"
    (folder / "_internal").mkdir(parents=True)
    (folder / "_internal" / "lib.dll").write_bytes(b"\0" * 100 + b"clean")
    assert kit_build.scan(folder, [token, key]) == 0
    for name, body in [("a.bin", token.encode("ascii")), ("b.bin", key.encode("utf-16-le"))]:
        (folder / "_internal" / name).write_bytes(b"\0" * 10 + body + b"\0")
        assert kit_build.scan(folder, [token, key]) == 1
        (folder / "_internal" / name).unlink()
    (folder / "update.json").write_text("{}", encoding="utf-8")
    assert kit_build.scan(folder, [token, key]) == 1
    (folder / "update.json").unlink()
    assert kit_build.scan(folder, []) == 1  # nothing to look for is not a pass
