"""Keeps the bridge running: run_bridge_supervised.ps1's restart policy, then slow retries, since nobody is watching."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

NO_RESTART_EXIT = 78  # main.cpp's kExitNoRestart: this environment cannot run the bridge
KEEP_RUN_LOGS = 10
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class RestartPolicy:
    max_restarts: int = 5  # more exits than this inside window_s switches to slow retries
    window_s: float = 600.0
    delay_s: float = 3.0
    slow_retry_s: float = 300.0


def describe_exit(code: int) -> str:
    """A code as Windows reports it: NTSTATUS crashes in hex, ordinary codes in decimal."""
    return f"0x{code & 0xFFFFFFFF:08X}" if code < 0 or code > 255 else str(code)


class BridgeSupervisor:
    """Runs command until stop is set, it exits cleanly, or it exits with NO_RESTART_EXIT."""

    def __init__(self, command: list[str], env: dict[str, str], log_dir: Path,
                 policy: RestartPolicy = RestartPolicy(), clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[threading.Event, float], bool] | None = None) -> None:
        self._command = command
        self._env = env
        self._log_dir = Path(log_dir)
        self._policy = policy
        self._clock = clock
        self._sleep = sleep or (lambda stop, seconds: stop.wait(seconds))
        self.runs = 0
        self.process: subprocess.Popen | None = None  # the current or last run

    def run(self, stop: threading.Event) -> str:
        """Supervises until one of the three ends; returns which: "stopped", "clean" or "no-restart"."""
        exits: deque[float] = deque()
        while not stop.is_set():
            self.runs += 1
            code = self._run_once(stop)
            if code is None:
                return "stopped"
            if code == 0:
                logger.info("bridge exited cleanly; not restarting")
                return "clean"
            if code == NO_RESTART_EXIT:
                logger.error("bridge reported a failure a restart cannot fix; not restarting. Its log says why: %s",
                             self._log_dir)
                return "no-restart"
            now = self._clock()
            exits.append(now)
            while exits and exits[0] < now - self._policy.window_s:
                exits.popleft()
            if len(exits) > self._policy.max_restarts:
                logger.error("bridge exited %d times in %.0f s; retrying every %.0f s", len(exits),
                             self._policy.window_s, self._policy.slow_retry_s)
                exits.clear()
                if self._sleep(stop, self._policy.slow_retry_s):
                    return "stopped"
            elif self._sleep(stop, self._policy.delay_s):
                return "stopped"
        return "stopped"

    def _run_once(self, stop: threading.Event) -> int | None:
        """The run's exit code, or None if stop ended it; a command that cannot start counts as a failed run."""
        self._log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._log_dir / f"bridge-{time.strftime('%Y%m%d-%H%M%S')}-{self.runs:05d}.log"
        self._prune_logs()
        logger.info("bridge run %d starting; output in %s", self.runs, log_path.name)
        try:
            with open(log_path, "wb") as out:
                proc = self.process = subprocess.Popen(self._command, env=self._env, stdin=subprocess.DEVNULL,
                                                       stdout=out, stderr=subprocess.STDOUT, creationflags=_NO_WINDOW)
        except OSError as exc:
            logger.error("bridge did not start: %s", exc)
            return -1
        while True:
            try:
                code = proc.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if stop.is_set():
                    proc.terminate()
                    proc.wait(timeout=10)
                    logger.info("bridge stopped")
                    return None
        logger.warning("bridge run %d exited with code %s", self.runs, describe_exit(code))
        return code

    def _prune_logs(self) -> None:
        """Keeps the newest KEEP_RUN_LOGS - 1, so the run about to start makes KEEP_RUN_LOGS."""
        logs = sorted(self._log_dir.glob("bridge-*.log"))
        for old in logs[:max(0, len(logs) - (KEEP_RUN_LOGS - 1))]:
            try:
                old.unlink()
            except OSError:
                pass  # still open elsewhere; the next run tries again
