"""The kit's entry: one sidecar and one supervised bridge per machine, no window, logs under %LOCALAPPDATA%.

`--stop` ends the running copy and waits for it; `--self-test REPORT` checks a build. See docs/signals.md.
"""

from __future__ import annotations

import argparse
import faulthandler
import logging
import logging.handlers
import os
import socket
import sys
import threading
from pathlib import Path

from src.kit import config as kit_config
from src.kit.supervisor import BridgeSupervisor

logger = logging.getLogger("src.kit")

INSTANCE_MUTEX = r"Global\AdaptiveLearningSensors"
STOP_EVENT = r"Global\AdaptiveLearningSensorsStop"
STOP_WAIT_S = 15.0
BRIDGE_EXE = "muse_native_bridge.exe"
_CONSOLE_LOG_BYTES = 1_000_000


def app_dir() -> Path:
    """The installed folder: the frozen exe's own, or KIT_APP_DIR for a run from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    if not os.environ.get("KIT_APP_DIR"):
        raise SystemExit("set KIT_APP_DIR to a staged kit folder to run the kit from source")
    return Path(os.environ["KIT_APP_DIR"]).resolve()


def data_dir() -> Path:
    """Per user: the working directory, which holds no .env, and the logs."""
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "AdaptiveLearning" / "Sensors"


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def uvicorn_log_config() -> dict:
    """uvicorn's records reach this process's handlers; access lines only for warnings, since the page polls."""
    return {"version": 1, "disable_existing_loggers": False,
            "loggers": {"uvicorn": {"level": "INFO"}, "uvicorn.error": {"level": "INFO"},
                        "uvicorn.access": {"level": "WARNING"}}}


def configure_logging(logs: Path) -> None:
    logs.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(logs / "sensors.log", maxBytes=1_000_000, backupCount=5,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)


def _ensure_stdio(console_log: Path) -> None:
    """A windowed exe starts with no stdout or stderr, and uvicorn and print() need both; they go to console_log."""
    if sys.stdout is not None and sys.stderr is not None:
        faulthandler.enable()
        return
    console_log.parent.mkdir(parents=True, exist_ok=True)
    try:
        if console_log.stat().st_size > _CONSOLE_LOG_BYTES:
            os.replace(console_log, console_log.with_suffix(".log.1"))
    except OSError:
        pass  # absent, or held open by the copy already running
    stream = open(console_log, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 -- for the process's life
    sys.stdout = sys.stdout or stream
    sys.stderr = sys.stderr or stream
    faulthandler.enable(file=stream)  # a crash in native code leaves its traceback here


def prepare_environment(environ, cfg: kit_config.KitConfig, app: Path) -> tuple[dict[str, str], list[str]]:
    """Leaves environ holding only kit.json's sidecar settings; returns the bridge's environment and what was cleared."""
    cleared = kit_config.clear_sidecar_settings(environ)
    bridge = kit_config.bridge_env(cfg, environ)  # before the sidecar's tokens go in, since the bridge reads neither
    environ.update(kit_config.sidecar_env(cfg, app))
    return bridge, cleared


def serve(app: Path) -> int:
    """The normal run, until --stop or a failure; 0 also when another copy already holds the machine."""
    from src.kit import winproc  # noqa: PLC0415

    instance = winproc.SingleInstance(INSTANCE_MUTEX)
    if not instance.acquired:
        print("another copy of the sensors is already running; this one exits", flush=True)
        return 0
    stop_signal = winproc.StopSignal(STOP_EVENT)  # straight after the mutex, so --stop can reach this copy
    logs = data_dir() / "logs"
    configure_logging(logs)
    winproc.set_error_mode()
    try:
        winproc.kill_children_with_me()
    except OSError as exc:
        logger.warning("the bridge is not tied to this process (%s); it is still stopped on a normal exit", exc)

    try:
        cfg, warnings = kit_config.load(app / kit_config.KIT_FILE)
    except kit_config.KitConfigError as exc:
        logger.error("refusing to start; %s:\n%s", app / kit_config.KIT_FILE, exc)
        return 1
    for warning in warnings:
        logger.warning(warning)

    work = data_dir()
    work.mkdir(parents=True, exist_ok=True)
    os.chdir(work)
    bridge_env, cleared = prepare_environment(os.environ, cfg, app)
    if cleared:
        logger.info("ignoring %s from this environment: kit.json decides", ", ".join(cleared))

    if not port_free(kit_config.SIDECAR_PORT):
        logger.error("port %d is taken, probably by a sidecar start.ps1 started; stop it and sign in again",
                     kit_config.SIDECAR_PORT)
        return 1
    if not port_free(kit_config.BRIDGE_PORT):
        logger.warning("port %d is taken; the bridge will be retried until it is free", kit_config.BRIDGE_PORT)

    stop = threading.Event()
    supervisor = BridgeSupervisor([str(app / "bridge" / BRIDGE_EXE)], bridge_env, logs)
    bridge = threading.Thread(target=supervisor.run, args=(stop,), name="bridge-supervisor")
    bridge.start()
    try:
        return run_sidecar(stop, stop_signal)
    finally:
        stop.set()
        bridge.join(timeout=30)
        logger.info("sensors stopped")


def run_sidecar(stop: threading.Event, stop_signal) -> int:
    """uvicorn in this thread until the stop signal; 1 if it never started."""
    import uvicorn  # noqa: PLC0415
    from src.app.main import app as sidecar  # noqa: PLC0415 -- reads the settings serve() just set

    server = uvicorn.Server(uvicorn.Config(sidecar, host="127.0.0.1", port=kit_config.SIDECAR_PORT, http="h11",
                                           ws="none", loop="asyncio", lifespan="on",
                                           log_config=uvicorn_log_config()))
    watcher = threading.Thread(target=_watch_for_stop, args=(server, stop, stop_signal), name="stop-watcher")
    watcher.start()
    try:
        server.run()
    except SystemExit as exc:  # how uvicorn ends when it cannot bind
        logger.error("sidecar did not start (exit %s)", exc.code)
        return 1
    finally:
        stop.set()
        watcher.join(timeout=5)
    return 0 if server.started else 1


def _watch_for_stop(server, stop: threading.Event, stop_signal) -> None:
    while not stop.is_set():
        if stop_signal.wait(1.0):
            logger.info("stop requested")
            server.should_exit = True
            stop.set()


def request_stop() -> int:
    """`--stop`: 0 once no copy is running, 1 if one still is after STOP_WAIT_S."""
    from src.kit import winproc  # noqa: PLC0415

    signalled = winproc.signal_stop(STOP_EVENT)
    released = winproc.wait_until_released(INSTANCE_MUTEX, STOP_WAIT_S if signalled else 2.0)
    print("stopped" if released else f"still running after {STOP_WAIT_S:.0f} s", flush=True)
    return 0 if released else 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="AdaptiveLearningSensors")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--stop", action="store_true", help="stop the running copy and wait until it has exited")
    choice.add_argument("--self-test", metavar="REPORT", type=Path, help="check this build; writes a JSON report")
    # Before parsing, since argparse reports a usage error on stderr; a self-test's output goes beside its report.
    at = argv.index("--self-test") + 1 if "--self-test" in argv else 0
    if 0 < at < len(argv):
        _ensure_stdio(Path(argv[at]).resolve().with_suffix(".console.log"))
    else:
        _ensure_stdio(data_dir() / "logs" / "console.log")
    args = parser.parse_args(argv)
    if args.stop:
        return request_stop()
    if args.self_test:
        from src.kit import selftest  # noqa: PLC0415

        return selftest.run(args.self_test.resolve(), app_dir())
    return serve(app_dir())
