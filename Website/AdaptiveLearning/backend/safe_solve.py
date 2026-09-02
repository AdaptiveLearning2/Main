"""Run a sympy parse/solve under a hard wall-clock bound.

`parse_expr` and `solve` are CPU-bound C-and-Python loops with no interrupt
point, so nothing in-process can stop them: a thread cannot be killed, and a
signal is not delivered on Windows while the interpreter is inside a long
integer computation. Measured here, `parse_expr("9**9**9")` never returns --
eager evaluation of a number with ~370 million digits.

That matters because the operand comes from the model. `GENERATION_LLM_TIMEOUT`
bounds the model call and nothing bounds what is done with the answer, so one
question could pin a core indefinitely -- and on the inline path (QUEUE_SIZE=0)
that is a request thread out of anyio's shared ~40-slot pool, held until the
process restarts.

A subprocess is the only thing that can actually be killed. It costs ~0.85s of
sympy import per call, measured on this machine against a ~1-3s model call, so
it is a real but minority addition to a question's latency -- and it is paid
only where an expression is evaluated, not on every generation.

Returns None on timeout or failure, which every caller already treats as "retry
this question".
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

# Wall-clock bound for one parse/solve. Generous against the ~10ms an ordinary
# expression takes: this is a backstop for a pathological input, not a
# performance budget, and cutting a legitimate solve short would reject a
# perfectly good question.
#
# Read through `_env_number` with a floor, which CLAUDE.md requires of every
# numeric setting and this file did not do. It was `float(os.getenv(...))` at
# import, so `SOLVE_TIMEOUT=abc` raised ValueError out of `import main` and took
# the whole backend down over one topic's solver knob -- exactly the failure
# that rule exists to prevent. `SOLVE_TIMEOUT=0` was worse than a crash: it
# imported fine and failed every expression question instantly, which reads as
# the model being unable to write a solvable expression.
#
# The floor is 1s rather than something smaller because this bound is only ever
# meant to catch a computation that will not finish at all; a value low enough
# to cut off real work is a misconfiguration, not a tuning choice.
#
# 3s, down from 10s. What this bounds is the *worst case*: a generator retries
# three times, so a reply that hangs cost ~30s of a request thread and now
# costs ~9s.
#
# This now bounds **the arithmetic alone**, which is what it always claimed to
# and never did. It used to cover the whole child process, and nearly all of
# that is importing sympy: measured 2026-08-31 across all five request shapes,
# a legitimate solve takes 0.64-1.00s wall, of which the arithmetic is ~10ms.
# So 3s was ~3x margin over *startup*, and any co-tenant on the machine spent
# it -- 8 of 8 solves killed at 3.0s with CPU hogs running.
#
# The worker prints a readiness line once sympy is loaded and the two phases
# are timed separately, so 3s is now ~300x margin over the maths it bounds.
# Re-measured under the same saturation: 7 of 8 succeed. At 2x
# oversubscription it is 1 of 8 and the split does not rescue that -- the
# machine cannot start interpreters fast enough, whatever the budgets say.
#
# The consequence to know before lowering it: this is the number that stops a
# runaway, and `SOLVE_STARTUP_BUDGET` is the one that absorbs a slow machine.
# They are no longer the same knob, so tightening this no longer risks failing
# every question on a loaded box -- and loosening it no longer buys any
# tolerance for one.
_CONFIGURED_TIMEOUT_S = llm_client._env_number("SOLVE_TIMEOUT", 3.0, float,
                                               minimum=1.0)

# How many worker processes may run at once.
#
# Concurrent solves contend for CPU, and while startup was inside the solve
# budget that collapsed for *all* of them together rather than degrading.
# Measured on this machine against an unbounded pool, before the phases were
# split:
#
#     8 concurrent   8/8 ok, slowest 1.12s
#    16 concurrent  16/16 ok, slowest 1.91s
#    32 concurrent   7/32 ok, slowest 3.07s     <- past the 3.0s budget
#    48 concurrent   0/48 ok
#
# Nothing bounded this. `GENERATION_MAX_CONCURRENCY` bounds model calls, and a
# solve is not a model call -- prefetch, the inline path and practice all reach
# here independently. So a class starting together took every solve-backed
# topic down at once, and since the solver's failure became `SolverUnavailable`
# it did so as a 503 with no retry rather than as a retry.
#
# Queueing is strictly better than refusing here: the work is ~1s and the
# caller wants an answer. 8 is the comfortable rung above, not the last one
# that worked.
#
# Splitting the phases has since made the cliff far less sharp -- contention
# now stretches startup, which has its own generous budget, rather than eating
# the solve's. The bound stays: it is what keeps a class starting together
# from putting a hundred interpreters on one machine, which is a resource
# question rather than a timeout one.
SOLVE_MAX_CONCURRENCY = llm_client._env_number(
    "SOLVE_MAX_CONCURRENCY", 8, int, minimum=1)

# The bound on *waiting*, which the per-solve budget deliberately does not
# cover: a caller that queues does not lose the time it waited from the time
# its subprocess is allowed to take, or a busy moment would fail solves that
# then had no budget left to run in. `llm_client` charges its model call the
# remainder for the opposite reason -- there the wait is the caller's own
# deadline; here the deadline exists to bound a CPU spin, and a spin does not
# start until the process does.
SOLVE_QUEUE_TIMEOUT_S = llm_client._env_number(
    "SOLVE_QUEUE_TIMEOUT", 20.0, float, minimum=0.1)

_solve_slots = threading.BoundedSemaphore(SOLVE_MAX_CONCURRENCY)


@contextlib.contextmanager
def _solve_slot(label):
    """One concurrency permit, released on every path.

    A context manager rather than an acquire/release pair for the reason
    `llm_client._generation_waiter` is one: the refusal raises *inside* the
    guarded region, which is the path most likely to run under load, and a
    hand-written release is exactly the one that gets skipped. The permit
    would never come back, and a `BoundedSemaphore` acquired non-blockingly
    turns that into solving being off for the life of the process.
    """
    if not _solve_slots.acquire(timeout=SOLVE_QUEUE_TIMEOUT_S):
        raise SolverUnavailable(
            f"no solver slot free within {SOLVE_QUEUE_TIMEOUT_S}s "
            f"({SOLVE_MAX_CONCURRENCY} concurrent): {label}")
    try:
        yield
    finally:
        _solve_slots.release()


# `SOLVE_RETRY_BUDGET_FACTOR` used to live here. It widened the *solve* budget
# on the second attempt, and there is no second attempt at a solve any more --
# see the retry reasoning in `_run`. Removed rather than left reading 3.0 and
# controlling nothing: a knob whose name promises tuning that is not available
# is worse than its absence.

# Launching Python and importing sympy, which is ~99% of an ordinary solve and
# is now budgeted separately from it. Generous on purpose: this is not the
# bound that catches a runaway -- `SOLVE_TIMEOUT` is -- it is the one that
# stops a spawn that will never come back from wedging a request thread. The
# measured cost is 0.32s on CI and 0.64-1.00s here, so 15s is ~15x the slowest
# figure and still bounded.
_CONFIGURED_STARTUP_BUDGET_S = llm_client._env_number(
    "SOLVE_STARTUP_BUDGET", 15.0, float, minimum=1.0)
SOLVE_STARTUP_BUDGET_S = _CONFIGURED_STARTUP_BUDGET_S

# The readiness marker the worker prints once sympy is loaded. Shared with
# `_solve_worker.READY`, and a rename on one side has to move the other -- the
# parent would otherwise read the marker as a final answer and report
# "unreadable output" for every solve.
_READY = "ready"

# How long the worker may take to exit after printing its answer. Flushing and
# interpreter teardown, nothing more -- but it is not instant, and treating a
# child that is merely still exiting as one that has to be killed turns every
# successful solve into a non-zero exit code.
_EXIT_GRACE_S = 5.0

_STARTUP_SAFETY_FACTOR = 3.0

# The probe's own budget, deliberately unrelated to the setting it validates:
# on a cold or heavily loaded machine a first subprocess can take several
# seconds, and a probe that timed out would report the very problem it is meant
# to measure.
_PROBE_TIMEOUT_S = 60.0

SOLVE_TIMEOUT_S = _CONFIGURED_TIMEOUT_S
STARTUP_COST_S = None

_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_solve_worker.py")


# A solved expression here is a number or a short polynomial. A result longer
# than this did not come from a question a student can be asked, and re-parsing
# it in the parent -- which is outside the subprocess bound -- is exactly the
# cost this module exists to avoid paying unboundedly.
MAX_RESULT_CHARS = 200


def _probe_startup():
    """Measure what starting a worker costs, and raise the budget if it is tight.

    Launching Python and importing sympy is ~99% of what an ordinary solve
    spends, and it is the part that stretches on a cold or loaded machine.
    `SOLVE_STARTUP_BUDGET` is what covers it.

    This used to clamp `SOLVE_TIMEOUT`, because startup sat inside that budget
    and a slow machine therefore broke it. The two phases are timed separately
    now, so clamping the solve budget would raise a number that no longer has
    anything to do with the measurement -- generously, and while leaving the
    budget that *does* cover startup to fail on its own.

    That makes this the one setting here whose breach fails *every* question at
    once, and fails it in a way that reads as the model being unable to write
    solvable maths. A comment saying "raise it, don't lower it" only reaches
    someone already debugging the wrong thing.

    So it is **clamped up, not refused**. Raising here would take the whole
    backend down over one topic's tuning knob -- the exact failure
    `_env_number`'s floor exists to prevent, and every non-generation surface
    (ingest, dashboards, consent) would go with it. Clamping keeps generation
    working on a machine slower than the one the default was measured on, and
    says so once, loudly, at boot rather than per question.

    A probe that cannot run at all leaves the configured value alone: that
    means the subprocess mechanism is broken, which is worth the log line, but
    guessing a budget from a failed measurement would be worse than keeping the
    one someone chose.
    """
    global SOLVE_STARTUP_BUDGET_S, STARTUP_COST_S
    started = time.monotonic()
    try:
        probed = _run({"scenario": "values", "values": ["1"]}, _PROBE_TIMEOUT_S,
                      "startup probe", startup_timeout=_PROBE_TIMEOUT_S)
    except SolverUnavailable as e:
        # `_run` raises this so a solve that could not run stops being reported
        # as a bad model reply. Here it must NOT propagate: this runs at import,
        # so letting it out would take the whole backend down -- every
        # non-generation surface with it -- which is the one thing the docstring
        # above says this function may not do. A mechanism that cannot run is
        # exactly the "probe failed" case below, so it is folded into it.
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
    # Clamps SOLVE_STARTUP_BUDGET now, not SOLVE_TIMEOUT. Startup used to sit
    # inside the solve budget, so a slow machine broke the solve budget and
    # this raised that; it no longer does, and clamping it would be protecting
    # the wrong number -- generously, and while leaving the budget that
    # actually covers startup to fail on its own.
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

    Six of the ten topics need exactly this and no more: they parse the model's
    values and then do ordinary arithmetic on them. `sympify` is the unbounded
    half -- `sympify("9**9**9")` never returns -- and the arithmetic afterwards
    is not, so bounding the parse alone covers them without moving six solvers
    into the worker the way geometry and angles needed.

    Floats rather than sympy's canonical strings: `str(sympify("0.75"))` is
    `"0.750000000000000"`, and every caller either does arithmetic or keeps the
    model's own string for display. Nothing needs the sympy object, which is
    what lets the parent avoid a second parse.
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
    """Geometry's solved value as a float, or None if it did not finish.

    A separate entry point rather than another `scenario` string, because this
    one carries a dict of the model's values rather than one expression.
    """
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
    """`str(solution)` for one expression, or None if it did not finish.

    None means "retry this question", which is what every caller already does
    with a reply it cannot use.
    """
    return _run({"expr": expression, "scenario": scenario}, timeout,
                f"{scenario}:{expression[:80]}")


class SolverUnavailable(llm_client.GenerationUnavailable):
    """The solver could not be *run*. Not the same as a reply it rejected.

    Five things used to return `None` here and only one of them was the model's
    fault: a worker that answered "that input is not solvable". The other four
    -- a timeout, a failure to spawn, a non-zero exit, unreadable output -- say
    nothing about the reply and everything about this machine.

    Collapsed into one `None`, the generator retried three times, each retry
    billing another model call that could not help, and then raised "Failed to
    generate valid JSON after retries" -- a claim about the model, for a
    subprocess that never ran. That misdiagnosis is the whole reason for this
    type: an intermittent, load-dependent timeout in the test suite reads as a
    defect in whichever topic drew the short straw.

    It subclasses `GenerationUnavailable` so it reaches a student as the 503
    that already means "this deployment cannot serve right now", rather than as
    a 500. Both `main.py` call sites catch that already.
    """


class _Timeout(Exception):
    """A phase ran out of budget. Carries which one, for the message."""

    def __init__(self, phase, budget):
        super().__init__(phase)
        self.phase, self.budget = phase, budget


class _Pipe:
    """A child stream drained by a thread, so it can be read with a deadline.

    A thread rather than `select`: this runs on Windows, where `select` takes
    sockets only. Draining continuously also keeps the child from blocking on
    a full pipe while the parent is waiting on a clock -- which is the classic
    way a two-phase protocol deadlocks instead of timing out.
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
        # The child died before reading. Its exit code is the real diagnosis,
        # and the caller checks that.
        pass
    return proc, stdout, stderr


def _await_answer(proc, stdout, startup_budget, solve_budget):
    """`(answer_line, phase)` -- the worker's result, timed in two phases.

    This is the whole point of the change. The old bound covered launching
    Python and importing sympy as well as the arithmetic, and startup is ~99%
    of an ordinary solve -- so `SOLVE_TIMEOUT` was a bound on *startup plus a
    little*, and any co-tenant on the machine spent it. Measured: 8 of 8
    solves killed at 3.0s with CPU hogs running, on arithmetic that takes
    ~10ms.

    The worker now prints a readiness line once sympy is loaded. Startup gets
    its own generous budget, and the solve budget starts from readiness, which
    is what it was always meant to bound.

    The malformed-request path prints its answer *before* importing anything,
    so a first line that is not the readiness marker is a final answer.
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

    Raises `SolverUnavailable` when the worker could not be run at all --
    see that class. Shared by both entry points so the kill, the exit-code
    check and the length cap cannot end up applying to one solve path and not
    the other, which is how the unbounded half of geometry survived a round of
    this.
    """
    budget = SOLVE_TIMEOUT_S if timeout is None else timeout
    # `startup_timeout` exists for `_probe_startup` alone. Its budget is
    # deliberately unrelated to the setting it validates -- bounded by the one
    # it is measuring, a too-small `SOLVE_STARTUP_BUDGET` makes the probe time
    # out reporting the very problem it exists to detect, and the clamp that
    # would have fixed it never runs.
    startup = SOLVE_STARTUP_BUDGET_S if startup_timeout is None \
        else startup_timeout

    # Only a *startup* timeout is retried, and splitting the phases is what
    # inverted that.
    #
    # Before the split the budget was ~99% startup, so "timeout" almost always
    # meant contention, and retrying was how a moment of it was survived. Now
    # the two are distinguishable and they mean opposite things:
    #
    #   * a **solve** timeout is 3s against ~10ms of arithmetic -- 300x margin,
    #     so it is a genuine spin. A spin spins again. Retrying costs a second
    #     full startup and another budget to reach the same kill, and the
    #     student waits through both for the same 503.
    #   * a **startup** timeout is the machine failing to launch Python and
    #     import sympy in 15s. That is contention, it passes, and it is the
    #     case the retry was built for -- observed once under 18 CPU hogs and
    #     rescued.
    #
    # So the retry moved to where the evidence puts it. The worst-case hold on
    # an anyio threadpool slot drops with it: a spin is 18s absolute rather
    # than 42s, and ~4s in practice, on the hottest path in the product.
    #
    # Why any of this is worth the words: ten of the fourteen topics reach the
    # worker, and a timeout *raises* rather than returning None -- so whatever
    # this branch decides is the difference between a retry and a 503 in front
    # of a student, across most of the question bank at once.
    #
    # `SOLVE_RETRY_BUDGET_FACTOR` went with the old shape. It widened the solve
    # budget on the second attempt, and there is no second attempt at a solve
    # any more -- a knob that controls nothing is worse than none, since the
    # next reader assumes the tuning it names is available.
    attempts = 2
    reaped = False
    for index in range(attempts):
        last = index == attempts - 1
        # The permit is taken *outside* the try, so a queue refusal is not
        # caught by the `except Exception` below and re-labelled "could not be
        # started" -- it was never started, and saying so wrongly is what this
        # whole type exists to stop. Taken per attempt rather than around both,
        # so a retrying caller does not hold a slot through a wait it already
        # knows failed. Released before the output is parsed: that is a JSON
        # load on a capped string, and holding a slot through it would shrink
        # the effective concurrency for no reason.
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
                # Killed here, which is the whole point: the in-process
                # equivalent leaves the spin running for the life of the
                # worker.
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
            # The answer is out but the child has not necessarily exited yet:
            # it still has to flush and tear down the interpreter. Killing it
            # here -- which a bare `poll() is None` check does -- makes every
            # successful solve look like a non-zero exit.
            try:
                proc.wait(timeout=_EXIT_GRACE_S)
            except subprocess.TimeoutExpired:
                # It answered and then would not leave. The answer is already
                # in hand and is good; this is only about not leaking a
                # process -- so the kill must not then be read as the solve
                # having failed, which is what the exit-code check below did.
                # A correct answer came back as `the solver exited -9`, and
                # the student got a 503 for it.
                _kill(proc)
                reaped = True
            break

    returncode = proc.returncode
    # Skipped when we are the ones who killed it after it answered: the exit
    # code then describes our signal, not the solve. It still applies to every
    # other path, where a non-zero exit is the diagnosis for a worker that
    # produced nothing usable.
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


# Run at import, so a machine too slow for the configured budget says so at
# boot rather than on a student's first question. `SOLVE_STARTUP_PROBE=0`
# skips it -- for a process that imports this module and never solves, where
# the ~0.8s would be paid for nothing.
def _startup_probe_enabled():
    """Whether the boot-time probe should run.

    A named predicate rather than an inline check, so a test can tell "the
    probe is switched off" from "the probe ran and failed". Inferring that from
    `STARTUP_COST_S is None` conflates the two, and they mean opposite things:
    one is a deliberate trade, the other is a broken subprocess.
    """
    return os.getenv("SOLVE_STARTUP_PROBE", "1").strip().lower()         not in ("0", "false", "no")


if _startup_probe_enabled():
    _probe_startup()
