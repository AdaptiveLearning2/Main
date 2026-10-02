"""The student kit: kit.json as start.ps1 -Hosted checks it, what each process is given, the supervisor, the build."""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid

import pytest
from launcher_cases import ADDRESS_CASES, BACKEND, BRIDGE_VARS, NORMALISED, ORIGIN, ROOT, TOKEN
from test_bridge_token_path import _EXE as _BRIDGE_EXE

from src.app.config import Settings, parse_eeg_devices
from src.kit import config as kit_config
from src.kit import launcher, selftest
from src.kit.config import KitConfig, KitConfigError, check
from src.kit.supervisor import NO_RESTART_EXIT, BridgeSupervisor, RestartPolicy, describe_exit

WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="Windows process plumbing")
FIELD = {"backend": "backend_url", "origin": "frontend_origin", "token": "learner_token"}
GOOD = {"backend_url": BACKEND, "frontend_origin": ORIGIN, "learner_token": TOKEN}
# The base interpreter: a venv's python.exe is a redirector whose own kill-on-close job would hide a missing one.
PYTHON = getattr(sys, "_base_executable", None) or sys.executable
BUILD_SCRIPT = ROOT / "EEGResearch" / "scripts" / "build_student_kit.ps1"
POWERSHELL = shutil.which("powershell")
NOISY = 'cmd /c "echo out & echo err 1>&2 & exit 0"'  # succeeds while writing to stderr, as PyInstaller does


def _cfg(**change) -> KitConfig:
    return check({**GOOD, **change})[0]


@pytest.mark.parametrize("change,refused", ADDRESS_CASES)
def test_kit_json_is_refused_for_exactly_the_reasons_start_ps1_refuses(change, refused):
    raw = {**GOOD, **{FIELD[k]: v for k, v in change.items()}}
    if not refused:
        check(raw)
        return
    with pytest.raises(KitConfigError) as refusal:
        check(raw)
    assert sorted(line.split(" ", 1)[0] for line in str(refusal.value).splitlines()) == sorted(FIELD[f] for f in refused)


@pytest.mark.parametrize("given,expected", NORMALISED)
def test_kit_json_addresses_are_written_as_a_browser_sends_them(given, expected):
    cfg = _cfg(backend_url=given, frontend_origin=given)
    assert (cfg.backend_url, cfg.frontend_origin) == (expected, expected)


@pytest.mark.parametrize("change,refused", [
    ({"backendurl": BACKEND}, "Unknown setting(s) backendurl"),
    ({"camera_index": -1}, "camera_index"),
    ({"camera_index": "0"}, "camera_index"),
    ({"camera_index": True}, "camera_index"),
    ({"optics_preset": "1037"}, "optics_preset"),
    ({"optics_preset": 1035}, "optics_preset"),
    ({"version": 3}, "version"),
], ids=["misspelt", "negative-camera", "camera-as-text", "camera-as-bool", "preset-off-the-ladder",
        "preset-as-number", "version-as-number"])
def test_kit_json_refuses_a_misspelt_or_mistyped_setting(change, refused):
    with pytest.raises(KitConfigError, match=re.escape(refused)):
        check({**GOOD, **change})


def test_a_16_channel_optics_preset_is_kept_with_a_warning():
    cfg, warnings = check({**GOOD, "optics_preset": "1031"})
    assert cfg.optics_preset == "1031" and any("16 CH" in w for w in warnings)
    assert check({**GOOD, "optics_preset": "1034"})[1] == []


def test_kit_json_reads_back_what_was_written_and_names_a_broken_file(tmp_path):
    path = tmp_path / "kit.json"
    cfg = _cfg(backend_url="HTTPS://Main-u0ki.onrender.com/", camera_index=2, version="0.1.0")
    kit_config.write(path, cfg)
    assert kit_config.load(path) == (cfg, [])
    for content, reason in [("{", "not valid JSON"), ("[]", "one JSON object")]:
        path.write_text(content, encoding="utf-8")
        with pytest.raises(KitConfigError, match=reason):
            kit_config.load(path)
    with pytest.raises(KitConfigError, match="Cannot read"):
        kit_config.load(tmp_path / "absent.json")


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16"], ids=["utf-8", "utf-8-bom", "utf-16-bom"])
def test_kit_json_loads_however_a_windows_editor_saved_it(tmp_path, encoding):
    """Notepad writes UTF-8; PowerShell 5.1 writes a BOM with -Encoding UTF8, and UTF-16 with Out-File or >."""
    path = tmp_path / "kit.json"
    cfg = _cfg(camera_index=1, version="0.1.0")
    kit_config.write(path, cfg)
    path.write_text(path.read_text(encoding="utf-8"), encoding=encoding)
    assert kit_config.load(path) == (cfg, [])


def test_the_build_writes_kit_json_through_the_same_check(tmp_path):
    out = tmp_path / "kit.json"
    common = ["--frontend-origin", ORIGIN, "--learner-token", TOKEN]
    assert kit_config.main(["write", str(out), "--backend-url", "HTTPS://Main-u0ki.onrender.com/", *common,
                            "--camera-index", "1", "--version", "0.1.0"]) == 0
    assert kit_config.load(out)[0] == KitConfig(BACKEND, ORIGIN, TOKEN, 1, "", "0.1.0")
    assert kit_config.main(["check", "--backend-url", BACKEND + "/api", *common]) == 1


@pytest.fixture
def environ():
    """os.environ, put back afterwards: the launcher's preparation edits it in place."""
    saved = dict(os.environ)
    yield os.environ
    os.environ.clear()
    os.environ.update(saved)


def test_every_key_the_sidecar_is_given_is_a_setting_it_reads(tmp_path):
    aliases = {f.alias for f in Settings.model_fields.values() if f.alias}
    assert set(kit_config.sidecar_env(_cfg(), tmp_path)) <= aliases


@pytest.mark.parametrize("preset", ["", "1034"], ids=["bridge-default-preset", "preset"])
def test_leftovers_reach_neither_process_and_the_sidecar_reads_kit_json(environ, tmp_path, monkeypatch, preset):
    monkeypatch.chdir(tmp_path)  # no .env, as in the kit's data folder
    assert {"MUSE_BRIDGE_PORT", "MUSE_AUTO_RECONNECT"} <= set(BRIDGE_VARS), BRIDGE_VARS  # a scan that misses passes
    aliases = [f.alias for f in Settings.model_fields.values() if f.alias]
    environ.update({name: "9" for name in aliases + BRIDGE_VARS})
    environ["LOCALAPPDATA"] = str(tmp_path / "home")
    app = tmp_path / "app"

    bridge, cleared = launcher.prepare_environment(environ, _cfg(camera_index=2, optics_preset=preset), app)

    assert set(aliases) <= set(cleared)
    s = Settings()
    assert (s.api_token, s.eeg_source, s.eeg_devices) == (TOKEN, "muse", "default:muse@8765,camera:face@2")
    assert (s.backend_url, s.allowed_origins, s.push_enabled) == (BACKEND, ORIGIN, True)
    assert (s.host, s.port, s.muse_bridge_port, s.face_camera_index) == ("127.0.0.1", 8001, 8765, 2)
    assert s.face_landmark_model_path == str(app / "models" / kit_config.LANDMARK_MODEL)
    assert s.face_emotion_model_path == str(app / "models" / kit_config.EMOTION_MODEL)
    assert {d: c.kind for d, c in parse_eeg_devices(s).items()} == {"default": "muse", "camera": "face"}
    assert s.admin_token not in (TOKEN, "9") and len(s.admin_token) >= 32
    expected = {"MUSE_ENABLE_OPTICS": "1", **({"MUSE_OPTICS_PRESET": preset} if preset else {})}
    assert {k: v for k, v in bridge.items() if k in BRIDGE_VARS or k.startswith("MUSE_")} == expected
    assert bridge["LOCALAPPDATA"] == str(tmp_path / "home"), "the bridge writes its token under it"
    assert not {"API_TOKEN", "ADMIN_TOKEN"} & set(bridge), "the bridge reads neither token"


def test_the_admin_token_is_fresh_each_start_long_and_never_the_learner_token(tmp_path):
    first, second = (kit_config.sidecar_env(_cfg(), tmp_path)["ADMIN_TOKEN"] for _ in range(2))
    assert first != second and min(len(first), len(second)) >= 32 and TOKEN not in (first, second)


def _exits(code: int) -> list[str]:
    return [sys.executable, "-c", f"import sys; print('bridge ran'); sys.exit({code})"]


class _Sleeps:
    """Records each wait instead of sleeping, and stops supervision at the limit-th one."""

    def __init__(self, limit: int) -> None:
        self.waits: list[float] = []
        self.limit = limit

    def __call__(self, stop: threading.Event, seconds: float) -> bool:
        self.waits.append(seconds)
        if len(self.waits) >= self.limit:
            stop.set()
        return stop.is_set()


def test_a_crashing_bridge_is_restarted_then_retried_slowly_and_never_given_up(tmp_path):
    sleeps = _Sleeps(limit=6)
    policy = RestartPolicy(max_restarts=2, delay_s=0.5, slow_retry_s=300)
    supervisor = BridgeSupervisor(_exits(3), dict(os.environ), tmp_path, policy, sleep=sleeps)
    assert supervisor.run(threading.Event()) == "stopped"
    # A third exit is one more than two restarts: a slow wait, then a fresh budget.
    assert (sleeps.waits, supervisor.runs) == ([0.5, 0.5, 300, 0.5, 0.5, 300], 6)


@pytest.mark.parametrize("code,ended", [(0, "clean"), (NO_RESTART_EXIT, "no-restart")])
def test_a_clean_exit_or_one_a_restart_cannot_fix_ends_supervision(tmp_path, code, ended):
    sleeps = _Sleeps(limit=99)
    supervisor = BridgeSupervisor(_exits(code), dict(os.environ), tmp_path, sleep=sleeps)
    assert supervisor.run(threading.Event()) == ended
    assert (supervisor.runs, sleeps.waits) == (1, [])


@pytest.mark.parametrize("step,waits", [(700, [0.5, 0.5, 0.5]), (400, [0.5, 300, 0.5])], ids=["apart", "close"])
def test_only_exits_inside_the_window_count_toward_the_slow_retry(tmp_path, step, waits):
    now = [0.0]

    def clock() -> float:
        now[0] += step
        return now[0]

    sleeps = _Sleeps(limit=3)
    policy = RestartPolicy(max_restarts=1, window_s=600, delay_s=0.5, slow_retry_s=300)
    BridgeSupervisor(_exits(3), dict(os.environ), tmp_path, policy, clock=clock, sleep=sleeps).run(threading.Event())
    assert sleeps.waits == waits


def test_a_bridge_that_cannot_start_is_logged_and_retried_like_a_crash(tmp_path, caplog):
    sleeps = _Sleeps(limit=2)
    supervisor = BridgeSupervisor([str(tmp_path / "missing.exe")], dict(os.environ), tmp_path,
                                  RestartPolicy(delay_s=0.5), sleep=sleeps)
    with caplog.at_level(logging.ERROR, logger="src.kit.supervisor"):
        assert supervisor.run(threading.Event()) == "stopped"
    assert sleeps.waits == [0.5, 0.5] and caplog.text.count("bridge did not start") == 2


def test_stop_ends_a_running_bridge_promptly(tmp_path):
    stop = threading.Event()
    supervisor = BridgeSupervisor([sys.executable, "-c", "import time; time.sleep(60)"], dict(os.environ), tmp_path)
    ended: list[str] = []
    thread = threading.Thread(target=lambda: ended.append(supervisor.run(stop)))
    thread.start()
    deadline = time.monotonic() + 30
    while supervisor.process is None or supervisor.process.poll() is not None:
        assert time.monotonic() < deadline, "the bridge never started"
        time.sleep(0.05)
    stop.set()
    thread.join(15)
    assert ended == ["stopped"] and supervisor.process.poll() is not None


def test_each_run_logs_to_its_own_file_and_only_the_newest_ten_are_kept(tmp_path):
    sleeps = _Sleeps(limit=12)
    supervisor = BridgeSupervisor(_exits(3), dict(os.environ), tmp_path, RestartPolicy(max_restarts=99, delay_s=0),
                                  sleep=sleeps)
    supervisor.run(threading.Event())
    logs = sorted(tmp_path.glob("bridge-*.log"))
    assert (len(logs), supervisor.runs) == (10, 12)
    assert logs[-1].name.endswith("-00012.log")
    assert all(log.read_text().strip() == "bridge ran" for log in logs)


@pytest.mark.skipif(sys.platform != "win32" or _BRIDGE_EXE is None,
                    reason="needs a muse_native_bridge.exe on Windows built after the last change to native_bridge/src")
def test_the_kit_does_not_restart_the_real_bridge_when_its_token_folder_is_missing(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = kit_config.bridge_env(_cfg(), dict(os.environ))
    env.update(LOCALAPPDATA=str(blocker / "sub"), MUSE_BRIDGE_PORT=str(port))
    sleeps = _Sleeps(limit=3)
    supervisor = BridgeSupervisor([str(_BRIDGE_EXE)], env, tmp_path / "logs", sleep=sleeps)
    assert supervisor.run(threading.Event()) == "no-restart"
    assert (supervisor.runs, sleeps.waits) == (1, [])


REAL_BRIDGE = pytest.mark.skipif(sys.platform != "win32" or _BRIDGE_EXE is None,
                                 reason="needs a muse_native_bridge.exe built after the last change to its source")


def _real_bridge_env(home, **extra) -> tuple[int, dict[str, str]]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = kit_config.bridge_env(_cfg(), dict(os.environ))
    env.update(LOCALAPPDATA=str(home), MUSE_BRIDGE_PORT=str(port), **extra)
    return port, env


def _bridge_token(home, port: int) -> str:
    token = home / "AdaptiveLearning" / f"muse_bridge_{port}.token"
    deadline = time.monotonic() + 20
    while not (token.is_file() and token.stat().st_size):
        assert time.monotonic() < deadline, "no bridge token after 20 s"
        time.sleep(0.1)
    return token.read_text(encoding="ascii").strip()


@REAL_BRIDGE
def test_the_bridge_run_log_keeps_its_start_up_lines_when_the_supervisor_stops_it(tmp_path):
    port, env = _real_bridge_env(tmp_path)
    supervisor = BridgeSupervisor([str(_BRIDGE_EXE)], env, tmp_path / "logs")
    stop = threading.Event()
    runner = threading.Thread(target=supervisor.run, args=(stop,))
    runner.start()
    try:
        selftest._bridge_status(port, _bridge_token(tmp_path, port))  # its main loop answers, so start-up has printed
    finally:
        stop.set()
        runner.join(20)
    (log,) = (tmp_path / "logs").glob("bridge-*.log")
    assert f"listening on 127.0.0.1:{port}" in log.read_text(encoding="utf-8", errors="replace")


@REAL_BRIDGE
def test_the_bridge_scans_only_when_asked_and_stops_once_nobody_asks(tmp_path):
    port, env = _real_bridge_env(tmp_path, MUSE_SCAN_IDLE_MS="3000")
    bridge = subprocess.Popen([str(_BRIDGE_EXE)], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        token = _bridge_token(tmp_path, port)
        with socket.create_connection(("127.0.0.1", port), timeout=10) as conn:
            conn.sendall(f"AUTH {token}\n".encode())
            lines = (json.loads(line) for line in conn.makefile("rb") if line.startswith(b"{"))
            statuses = (s for s in lines if "bridge_mode" in s)  # 5 a second while no headband streams
            first = next(statuses)
            if first["bridge_mode"] != "libmuse":
                pytest.skip("scanning is libMuse's; this is the synthetic build")
            assert first["scanning"] is False  # up, as from sign-in, and quiet
            conn.sendall(b'{"cmd":"refresh"}\n')
            # From the first line showing the scan: lines sent before the bridge read the command come first.
            after = itertools.dropwhile(lambda s: not s["scanning"], itertools.islice(statuses, 40))
            held = list(itertools.islice(after, 5))
            assert len(held) == 5 and all(s["scanning"] for s in held)  # held a second: pairing waits up to 12 s
            assert any(not s["scanning"] for s in after)  # then dropped, 3 s after the refresh
    finally:
        bridge.kill()
        bridge.wait(10)


@pytest.mark.parametrize("code,text", [(1, "1"), (78, "78"), (-1073741515, "0xC0000135"), (3221225781, "0xC0000135")])
def test_exit_codes_read_as_windows_reports_them(code, text):
    assert describe_exit(code) == text


def test_a_taken_port_is_reported_and_a_released_one_is_not():
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen()
        port = held.getsockname()[1]
        assert launcher.port_free(port) is False
    assert launcher.port_free(port) is True


def _names() -> tuple[str, str]:
    tag = uuid.uuid4().hex
    return rf"Local\AdaptiveLearningSensorsTest-{tag}", rf"Local\AdaptiveLearningSensorsTestStop-{tag}"


def _python(code: str, *args: str) -> subprocess.Popen:
    env = dict(os.environ, PYTHONPATH=str(ROOT / "EEGResearch"))
    return subprocess.Popen([PYTHON, "-c", code, *args], env=env, stdout=subprocess.PIPE, text=True)


def _exited_within(pid: int, seconds: float) -> bool:
    from src.kit import winproc  # noqa: PLC0415

    handle = winproc._k32.OpenProcess(winproc.SYNCHRONIZE, False, pid)
    if not handle:
        return True
    try:
        return winproc._k32.WaitForSingleObject(handle, int(seconds * 1000)) == winproc.WAIT_OBJECT_0
    finally:
        winproc._k32.CloseHandle(handle)


_PROBE = "import sys\nfrom src.kit.winproc import SingleInstance\nprint(SingleInstance(sys.argv[1]).acquired)\n"
_HOLD = ("import sys, time\nfrom src.kit.winproc import SingleInstance\nheld = SingleInstance(sys.argv[1])\n"
         "print(held.acquired, flush=True)\ntime.sleep(60)\n")


@WINDOWS
def test_a_second_copy_finds_the_first_and_one_started_later_takes_over():
    mutex, _ = _names()
    first = _python(_HOLD, mutex)
    try:
        assert first.stdout.readline().strip() == "True"
        assert _python(_PROBE, mutex).communicate(timeout=30)[0].strip() == "False"
    finally:
        first.kill()
        first.wait(10)
    assert _python(_PROBE, mutex).communicate(timeout=30)[0].strip() == "True"


@WINDOWS
def test_the_bridge_dies_with_the_launcher_however_the_launcher_ends():
    parent = _python("import subprocess, sys, time\nfrom src.kit import winproc\nwinproc.kill_children_with_me()\n"
                     "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                     "print(child.pid, flush=True)\ntime.sleep(60)\n")
    try:
        child = int(parent.stdout.readline())
        assert not _exited_within(child, 0.5), "the child should still be running"
    finally:
        parent.kill()  # TerminateProcess: nothing in the parent runs on the way out
        parent.wait(10)
    assert _exited_within(child, 10)


_RUNNING = ("import sys, time\nfrom pathlib import Path\nfrom src.kit.winproc import SingleInstance, StopSignal\n"
            "held, stop = SingleInstance(sys.argv[1]), StopSignal(sys.argv[2])\nprint(held.acquired, flush=True)\n")


@WINDOWS
def test_stop_returns_only_once_the_running_copy_has_finished_shutting_down(tmp_path, monkeypatch):
    mutex, event = _names()
    monkeypatch.setattr(launcher, "INSTANCE_MUTEX", mutex)
    monkeypatch.setattr(launcher, "STOP_EVENT", event)
    done = tmp_path / "shut-down"
    copy = _python(_RUNNING + "while not stop.wait(0.1):\n    pass\ntime.sleep(1)\nPath(sys.argv[3]).write_text('x')\n",
                   mutex, event, str(done))
    try:
        assert copy.stdout.readline().strip() == "True"
        assert launcher.request_stop() == 0
        assert done.is_file(), "returned before the running copy had finished"
    finally:
        copy.kill()
        copy.wait(10)


@WINDOWS
def test_stop_succeeds_when_nothing_runs_and_fails_on_a_copy_that_will_not_stop(monkeypatch):
    mutex, event = _names()
    monkeypatch.setattr(launcher, "INSTANCE_MUTEX", mutex)
    monkeypatch.setattr(launcher, "STOP_EVENT", event)
    monkeypatch.setattr(launcher, "STOP_WAIT_S", 1.0)
    assert launcher.request_stop() == 0
    copy = _python(_RUNNING + "time.sleep(60)\n", mutex, event)
    try:
        assert copy.stdout.readline().strip() == "True"
        assert launcher.request_stop() == 1
    finally:
        copy.kill()
        copy.wait(10)


def _powershell(script: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([POWERSHELL, "-NoProfile", "-Command", script], capture_output=True, text=True, timeout=60,
                          cwd=cwd)


def _build_function(name: str) -> str:
    found = re.search(rf"^function {name} \{{.*?^\}}", BUILD_SCRIPT.read_text(encoding="utf-8"), re.S | re.M)
    assert found, f"build_student_kit.ps1 has no {name}"
    return found.group(0)


def _build_step(command: str, redirect: str = "") -> subprocess.CompletedProcess:
    """The build script's Invoke-Step around COMMAND, at top level under Stop as the script calls it."""
    return _powershell(f"$ErrorActionPreference = 'Stop'\n{_build_function('Invoke-Step')}\n"
                       f"$x = Invoke-Step 'step' {{ {command} }} {redirect}\n\"after:$($x -join '|')\"\n"
                       "$ErrorActionPreference")


@pytest.mark.skipif(POWERSHELL is None, reason="Windows PowerShell only")
def test_a_build_step_survives_captured_stderr_and_fails_on_a_bad_exit_or_an_exe_that_never_started():
    if "survived" in _powershell(f"$ErrorActionPreference = 'Stop'; $x = {NOISY} *>&1; 'survived'").stdout:
        pytest.skip("this PowerShell does not abort on captured stderr, so it proves nothing here")
    noisy = _build_step(NOISY, "*>&1")
    lines = noisy.stdout.strip().splitlines()
    assert len(lines) >= 2 and lines[-2].startswith("after:"), noisy.stdout + noisy.stderr
    assert "out" in lines[-2] and "err" in lines[-2]  # stderr is kept, not discarded
    assert lines[-1] == "Stop"  # the caller's preference is untouched
    # No try around these: inside one, PowerShell would stop on the missing exe whatever the step did.
    for command, reason in (("cmd /c 'exit 3'", "step failed (exit 3)"),
                            ("& 'C:\\no\\such\\tool.exe'", "step failed: the command did not start")):
        failed = _build_step(command)
        assert "after:" not in failed.stdout and reason in failed.stderr, (command, failed.stdout, failed.stderr)


@pytest.mark.skipif(POWERSHELL is None, reason="Windows PowerShell only")
def test_the_build_hands_a_token_starting_with_a_dash_and_an_empty_preset_to_the_settings_check(tmp_path):
    dash = "-" + TOKEN[1:]  # token_urlsafe starts one token in 64 with "-"
    kit = tmp_path / "kit.json"
    out = _powershell(f"{_build_function('Get-KitArgs')}\n"
                      f"$a = Get-KitArgs '{BACKEND}' '{ORIGIN}' '{dash}' 2 '' '0.1.0'\n"
                      f"& '{PYTHON}' -m src.kit.config write '{kit}' @a; exit $LASTEXITCODE", cwd=ROOT / "EEGResearch")
    assert out.returncode == 0, out.stdout + out.stderr
    assert kit_config.load(kit)[0] == KitConfig(BACKEND, ORIGIN, dash, 2, "", "0.1.0")


def _starts_in(seconds: float):
    """An ASGI app whose lifespan startup takes this long."""
    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            await receive()
            await asyncio.sleep(seconds)
            await send({"type": "lifespan.startup.complete"})
            await receive()
            await send({"type": "lifespan.shutdown.complete"})
    return app


def test_the_server_check_keeps_what_uvicorn_logs_after_configuring_its_logging():
    import uvicorn  # noqa: PLC0415

    with selftest._server_warnings() as lines:
        uvicorn.Config(_starts_in(0), log_config=launcher.uvicorn_log_config())  # its dictConfig, as the check's
        logging.getLogger("uvicorn.error").error("[Errno 10048] address already in use")
    assert any("10048" in line for line in lines)


def test_a_slow_server_start_fails_the_check_and_leaves_no_server_running(monkeypatch):
    monkeypatch.setattr(selftest, "SERVER_START_S", 0.3)
    with pytest.raises(selftest.CheckFailed, match="did not start"):
        selftest._serve_once(_starts_in(1.5), "/")
    assert not [t for t in threading.enumerate() if t.name == "selftest-uvicorn" and t.is_alive()]


def test_the_modules_check_fails_a_kit_dll_from_elsewhere_and_only_reports_a_foreign_one():
    av, system_crt = r"C:\Program Files\SomeAV\hook64.dll", r"C:\Windows\System32\MSVCP140.dll"
    other_python, other_opencv = r"C:\Python314\python314.dll", r"C:\Tools\opencv_world.dll"
    loaded = [r"C:\Kit\_internal\python314.dll", r"C:\Windows\System32\kernel32.dll",
              r"C:\Windows\System32\dbghelp.dll", av, system_crt, other_python, other_opencv]
    kit_names = {"python314.dll", "dbghelp.dll", "opencv_world.dll", "msvcp140.dll"}
    failing, foreign = selftest._outside_kit(loaded, r"C:\Kit", r"C:\Windows", kit_names)
    assert failing == [system_crt, other_python, other_opencv]
    assert foreign == [av]
