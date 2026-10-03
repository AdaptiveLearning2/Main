"""Keeps the kit's processes running with run_bridge_supervised.ps1's restart policy, then slow retries."""

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


class Supervisor:
    """Runs command until stop is set, it exits cleanly, or it exits with NO_RESTART_EXIT.

    On stop it gets graceful_s to exit by itself (the sidecar sees the stop event too) before it is terminated.
    """

    def __init__(self, command: list[str], env: dict[str, str], log_dir: Path,
                 policy: RestartPolicy = RestartPolicy(), clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[threading.Event, float], bool] | None = None, name: str = "bridge",
                 graceful_s: float = 0.0, cwd: Path | None = None) -> None:
        self._command = command
        self._env = env
        self._log_dir = Path(log_dir)
        self._policy = policy
        self._clock = clock
        self._sleep = sleep or (lambda stop, seconds: stop.wait(seconds))
        self.name = name
        self._graceful_s = graceful_s
        self._cwd = cwd
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
                logger.info("%s exited cleanly; not restarting", self.name)
                return "clean"
            if code == NO_RESTART_EXIT:
                logger.error("%s reported a failure a restart cannot fix; not restarting. Its log says why: %s",
                             self.name, self._log_dir)
                return "no-restart"
            now = self._clock()
            exits.append(now)
            while exits and exits[0] < now - self._policy.window_s:
                exits.popleft()
            if len(exits) > self._policy.max_restarts:
                logger.error("%s exited %d times in %.0f s; retrying every %.0f s", self.name, len(exits),
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
        log_path = self._log_dir / f"{self.name}-{time.strftime('%Y%m%d-%H%M%S')}-{self.runs:05d}.log"
        self._prune_logs()
        logger.info("%s run %d starting; output in %s", self.name, self.runs, log_path.name)
        try:
            with open(log_path, "wb") as out:
                proc = self.process = subprocess.Popen(self._command, env=self._env, cwd=self._cwd,
                                                       stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                                       creationflags=_NO_WINDOW)
        except OSError as exc:
            logger.error("%s did not start: %s", self.name, exc)
            return -1
        while True:
            try:
                code = proc.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if stop.is_set():
                    self._end(proc)
                    logger.info("%s stopped", self.name)
                    return None
        logger.warning("%s run %d exited with code %s", self.name, self.runs, describe_exit(code))
        return code

    def _end(self, proc: subprocess.Popen) -> None:
        try:
            proc.wait(timeout=self._graceful_s)
        except subprocess.TimeoutExpired:
            # Said, because an ended sidecar's push client never made its final flush.
            if self._graceful_s:
                logger.warning("%s did not stop within %.0f s; ending it", self.name, self._graceful_s)
            proc.terminate()
            proc.wait(timeout=10)

    def _prune_logs(self) -> None:
        """Keeps the newest KEEP_RUN_LOGS - 1, so the run about to start makes KEEP_RUN_LOGS."""
        logs = []
        for path in self._log_dir.glob(f"{self.name}-*.log"):
            try:
                logs.append((path.stat().st_mtime_ns, path.name, path))
            except OSError:
                pass  # removed meanwhile
        # By when written, not by name: a name carries local time, which goes back an hour each autumn.
        for _, _, old in sorted(logs)[:max(0, len(logs) - (KEEP_RUN_LOGS - 1))]:
            try:
                old.unlink()
            except OSError:
                pass  # still open elsewhere; the next run tries again
