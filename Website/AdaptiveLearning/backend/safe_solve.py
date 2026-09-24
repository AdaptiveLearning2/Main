"""Run a sympy parse/solve on model output in a killable subprocess.

`parse_expr`/`solve` have no interrupt point (`parse_expr("9**9**9")` never
returns), and neither a thread nor a Windows signal can stop them.
Callers treat None as "retry this question"; `SolverUnavailable` means the
worker could not be run.
"""
import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
import time

import llm_client

# Seconds for the arithmetic alone (~10ms normally), timed from worker readiness;
# stops a runaway. `SOLVE_STARTUP_BUDGET` is the one that absorbs a slow machine.
_CONFIGURED_TIMEOUT_S = llm_client._env_number("SOLVE_TIMEOUT", 3.0, float,
                                               minimum=1.0)

# Worker processes at once; callers queue past it. Separate from
# `GENERATION_MAX_CONCURRENCY`, which bounds model calls only.
SOLVE_MAX_CONCURRENCY = llm_client._env_number(
    "SOLVE_MAX_CONCURRENCY", 8, int, minimum=1)

# Seconds to wait for a slot; not deducted from the solve budget, which bounds a
# CPU spin that has not started yet.
SOLVE_QUEUE_TIMEOUT_S = llm_client._env_number(
    "SOLVE_QUEUE_TIMEOUT", 20.0, float, minimum=0.1)

_solve_slots = threading.BoundedSemaphore(SOLVE_MAX_CONCURRENCY)


@contextlib.contextmanager
def _solve_slot(label):
    """One concurrency permit, released on every path.

    Raises `SolverUnavailable` if none frees within `SOLVE_QUEUE_TIMEOUT_S`.
    """
    if not _solve_slots.acquire(timeout=SOLVE_QUEUE_TIMEOUT_S):
        raise SolverUnavailable(
            f"no solver slot free within {SOLVE_QUEUE_TIMEOUT_S}s "
            f"({SOLVE_MAX_CONCURRENCY} concurrent): {label}")
    try:
        yield
    finally:
        _solve_slots.release()


# Seconds to launch Python and import sympy (~99% of a solve). Generous on
# purpose; `_probe_startup` raises it if this machine is slower.
_CONFIGURED_STARTUP_BUDGET_S = llm_client._env_number(
    "SOLVE_STARTUP_BUDGET", 15.0, float, minimum=1.0)
SOLVE_STARTUP_BUDGET_S = _CONFIGURED_STARTUP_BUDGET_S

# Must equal `_solve_worker.READY`; rename both together.
_READY = "ready"

# Seconds for the worker to flush and exit after answering, before it is killed.
_EXIT_GRACE_S = 5.0

_STARTUP_SAFETY_FACTOR = 3.0

# Independent of the budget it validates, or a tight budget times the probe out.
_PROBE_TIMEOUT_S = 60.0

SOLVE_TIMEOUT_S = _CONFIGURED_TIMEOUT_S
STARTUP_COST_S = None

_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_solve_worker.py")


# Longer results are discarded: the parent re-parses outside the subprocess bound.
MAX_RESULT_CHARS = 200


def _probe_startup():
    """Measure worker startup and clamp `SOLVE_STARTUP_BUDGET` up if it is tight.

    Clamps rather than raises: this runs at import, and raising would take the
    whole backend down. A probe that cannot run leaves the budget unchanged.
    """
    global SOLVE_STARTUP_BUDGET_S, STARTUP_COST_S
    started = time.monotonic()
    try:
        probed = _run({"scenario": "values", "values": ["1"]}, _PROBE_TIMEOUT_S,
                      "startup probe", startup_timeout=_PROBE_TIMEOUT_S)
    except SolverUnavailable as e:
        # Must not propagate: this runs at import.
        print(f"[safe_solve] startup probe could not run: {e}")
        probed = None
    elapsed = time.monotonic() - started
    if probed is None:
        print(f"[safe_solve] startup probe failed after {elapsed:.1f}s -- the "
              f"solve subprocess could not run, so every question that needs "
              f"one will fail. Leaving SOLVE_TIMEOUT at "
              f"{_CONFIGURED_TIMEOUT_S}s; a budget guessed from a failed "
              f"measurement would be worse than the one configured.")
        return

    STARTUP_COST_S = elapsed
    floor = elapsed * _STARTUP_SAFETY_FACTOR
    if SOLVE_STARTUP_BUDGET_S < floor:
        SOLVE_STARTUP_BUDGET_S = floor
        print(f"[safe_solve] SOLVE_STARTUP_BUDGET={_CONFIGURED_STARTUP_BUDGET_S}s "
              f"is below {_STARTUP_SAFETY_FACTOR:g}x the measured subprocess "
              f"startup of {elapsed:.2f}s on this machine. Raised to "
              f"{floor:.2f}s for this process. Left alone it would have failed "
              f"every question that needs a solve, which reads as a model "
              f"problem rather than a configuration one. Set "
              f"SOLVE_STARTUP_BUDGET to at least {floor:.1f} to silence this.")


def safe_sympify_values(values, timeout=None):
    """The model's numbers, parsed in the worker, as floats.

    `["1/2", "0.75", "3"]` in, `[0.5, 0.75, 3.0]` out; None if any value is not
    a finite number, or if the parse did not finish.
    """
    if not isinstance(values, (list, tuple)) or not values:
        return None
    solved = _run({"scenario": "values", "values": list(values)}, timeout,
                  f"values:{len(values)}")
    if solved is None:
        return None
    try:
        parsed = json.loads(solved)
    except ValueError:
        return None
    if not isinstance(parsed, list) or len(parsed) != len(values):
        return None
    return parsed


def safe_solve_angle(angle_scenario: str, variables: list,
                     timeout: float | None = None):
    """The angle scenario's answer as a float, or None if it did not finish."""
    solved = _run({"scenario": "angle", "angle_scenario": angle_scenario,
                   "variables": variables}, timeout,
                  f"angle:{angle_scenario}")
    if solved is None:
        return None
    try:
        return float(solved)
    except ValueError:
        print(f"[safe_solve] worker returned a non-number: {solved[:60]!r}")
        return None


def safe_solve_geometry(geometry_scenario: str, variables: dict,
                        timeout: float | None = None):
    """Geometry's solved value as a float, or None if it did not finish."""
    solved = _run({"scenario": "geometry",
                   "geometry_scenario": geometry_scenario,
                   "variables": variables}, timeout,
                  f"geometry:{geometry_scenario}")
    if solved is None:
        return None
    try:
        return float(solved)
    except ValueError:
        print(f"[safe_solve] worker returned a non-number: {solved[:60]!r}")
        return None


def safe_solve(expression: str, scenario: str, timeout: float | None = None):
    """`str(solution)` for one expression, or None if it did not finish."""
    return _run({"expr": expression, "scenario": scenario}, timeout,
                f"{scenario}:{expression[:80]}")


class SolverUnavailable(llm_client.GenerationUnavailable):
    """The solver could not be *run*. Not the same as a reply it rejected.

    Timeout, spawn failure, non-zero exit or unreadable output: a fault of this
    machine, not the model. Subclasses `GenerationUnavailable` so it reaches a
    student as a 503.
    """


class _Timeout(Exception):
    """A phase ran out of budget. Carries which one, for the message."""

    def __init__(self, phase, budget):
        super().__init__(phase)
        self.phase, self.budget = phase, budget


class _Pipe:
    """A child stream drained by a thread, so it can be read with a deadline.

    Not `select`, which takes sockets only on Windows. Continuous draining also
    stops the child blocking on a full pipe.
    """

    def __init__(self, stream):
        self._lines: queue.Queue = queue.Queue()
        self._collected = []
        self._stream = stream
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()

    def _drain(self):
        try:
            for line in self._stream:
                self._collected.append(line)
                self._lines.put(line)
        except Exception:                       # pragma: no cover - defensive
            pass
        finally:
            self._lines.put(None)               # EOF

    def line(self, deadline):
        """The next line, or None at EOF. Raises `queue.Empty` past `deadline`."""
        return self._lines.get(timeout=max(0.0, deadline - time.monotonic()))

    def text(self):
        self._thread.join(timeout=0.5)
        return "".join(self._collected)


def _spawn(request: dict):
    """Start the worker with the request already on its stdin."""
    proc = subprocess.Popen(
        [sys.executable, _WORKER],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
        # Inherit nothing the child does not need.
        env={"PATH": os.environ.get("PATH", ""),
             "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")},
    )
    stdout, stderr = _Pipe(proc.stdout), _Pipe(proc.stderr)
    try:
        proc.stdin.write(json.dumps(request))
        proc.stdin.close()
    except OSError:                             # pragma: no cover - defensive
        # Child died before reading; the caller checks its exit code.
        pass
    return proc, stdout, stderr


def _await_answer(proc, stdout, startup_budget, solve_budget):
    """`(answer_line, phase)`: startup budget until the readiness line, then
    the solve budget. Raises `_Timeout`.

    A first line that is not the readiness marker is a final answer (the
    malformed-request path answers before importing sympy).
    """
    deadline = time.monotonic() + startup_budget
    try:
        first = stdout.line(deadline)
    except queue.Empty:
        raise _Timeout("startup", startup_budget) from None
    if first is None:
        return None, "startup"                  # exited without a word
    try:
        if not json.loads(first).get(_READY):
            return first, "startup"             # refused before loading sympy
    except ValueError:
        return first, "startup"                 # unreadable; the caller says so

    deadline = time.monotonic() + solve_budget
    try:
        answer = stdout.line(deadline)
    except queue.Empty:
        raise _Timeout("the solve", solve_budget) from None
    return answer, "the solve"


def _kill(proc):
    proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:           # pragma: no cover - defensive
        pass


def _run(request: dict, timeout, label: str, startup_timeout=None):
    """One worker call. The result string, or None if the worker ran and
    rejected the input.

    Raises `SolverUnavailable` when the worker could not be run at all.
    """
    budget = SOLVE_TIMEOUT_S if timeout is None else timeout
    # Only `_probe_startup` passes this, so a tight budget cannot time out its own probe.
    startup = SOLVE_STARTUP_BUDGET_S if startup_timeout is None \
        else startup_timeout

    # Only a startup timeout is retried: that is contention and passes. A solve
    # timeout (3s vs ~10ms of arithmetic) is a genuine spin and would spin again.
    attempts = 2
    reaped = False
    for index in range(attempts):
        last = index == attempts - 1
        # Outside the try, so a queue refusal is not relabelled "could not be
        # started". Per attempt, and released before parsing the output.
        with _solve_slot(label):
            try:
                proc, stdout, stderr = _spawn(request)
            except Exception as e:              # pragma: no cover - defensive
                raise SolverUnavailable(
                    f"the solver could not be started: "
                    f"{type(e).__name__}: {e}") from e
            try:
                raw, phase = _await_answer(proc, stdout, startup, budget)
            except _Timeout as t:
                _kill(proc)
                if t.phase == "startup" and not last:
                    print(f"[safe_solve] exceeded {t.budget:g}s in startup "
                          f"({label}); retrying once -- startup is where "
                          f"contention shows, and contention passes")
                    continue
                raise SolverUnavailable(
                    f"the solver exceeded {t.budget:g}s in {t.phase} and was "
                    f"killed ({label}), after {index + 1} attempt(s)"
                ) from None
            # Let the child flush and exit; killing it now would read as a
            # non-zero exit on a successful solve.
            try:
                proc.wait(timeout=_EXIT_GRACE_S)
            except subprocess.TimeoutExpired:
                # Answered but would not exit: the answer stands, and the
                # exit-code check below must ignore our kill.
                _kill(proc)
                reaped = True
            break

    returncode = proc.returncode
    if returncode != 0 and not reaped:
        raise SolverUnavailable(
            f"the solver exited {returncode}: "
            f"{(stderr.text() or '').strip()[:200]}")
    if raw is None:
        raise SolverUnavailable(
            f"the solver produced no answer after {phase} ({label})")
    try:
        answer = json.loads(raw)
    except ValueError:
        raise SolverUnavailable(
            f"the solver produced unreadable output: "
            f"{raw[:200]!r}") from None
    if not answer.get("ok"):
        print(f"[safe_solve] {answer.get('error')}")
        return None
    result = answer.get("result") or ""
    if len(result) > MAX_RESULT_CHARS:
        print(f"[safe_solve] result of {len(result)} chars is not a usable "
              f"answer; discarding")
        return None
    return result


# Runs at import so a too-slow machine says so at boot. `SOLVE_STARTUP_PROBE=0`
# skips it for processes that never solve.
def _startup_probe_enabled():
    """Whether the boot-time probe should run.

    Separate from `STARTUP_COST_S is None`, which also means the probe failed.
    """
    return os.getenv("SOLVE_STARTUP_PROBE", "1").strip().lower()         not in ("0", "false", "no")


if _startup_probe_enabled():
    _probe_startup()
