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
import json
import os
import subprocess
import sys
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
# The headroom is smaller than it looks, and the reason is worth knowing before
# lowering it further. The budget covers the whole child process, and nearly
# all of that is importing sympy, not solving: measured 2026-08-31 across all
# five request shapes, a legitimate solve takes **0.64-1.00s wall**, of which
# the arithmetic is ~10ms. So 3s is ~3x margin over startup, not 300x over the
# maths. A slower or loaded machine can push startup past 2s, and if it ever
# exceeds this the symptom is every question failing to generate -- which reads
# as the model being unable to write solvable questions rather than as a
# misconfigured timeout.
#
# That instruction is not enough on its own, which is why `_probe_startup`
# below exists: a comment saying "raise it" only helps someone who reads it
# *after* every question has started failing, and the symptom points at the
# model rather than at this line. The floor is measured instead of trusted.
_CONFIGURED_TIMEOUT_S = llm_client._env_number("SOLVE_TIMEOUT", 3.0, float,
                                               minimum=1.0)

# How much of the budget must remain for the arithmetic after the child has
# finished starting up. 3x is generous against a solve that measures ~10ms, and
# the point is not the arithmetic -- it is that a budget below this is not
# separating a runaway from an ordinary question, it is cutting off both.
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
    """Measure what a trivial solve costs, and raise the budget if it is tight.

    The whole subprocess -- launching Python and importing sympy -- is inside
    the timeout, and that startup is ~99% of what an ordinary solve spends.
    So `SOLVE_TIMEOUT` is not really a bound on the arithmetic; it is a bound
    on startup plus a little, and startup is the part that stretches on a cold
    or loaded machine.

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
    global SOLVE_TIMEOUT_S, STARTUP_COST_S
    started = time.monotonic()
    probed = _run({"scenario": "values", "values": ["1"]}, _PROBE_TIMEOUT_S,
                  "startup probe")
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
    if _CONFIGURED_TIMEOUT_S < floor:
        SOLVE_TIMEOUT_S = floor
        print(f"[safe_solve] SOLVE_TIMEOUT={_CONFIGURED_TIMEOUT_S}s is below "
              f"{_STARTUP_SAFETY_FACTOR:g}x the measured subprocess startup of "
              f"{elapsed:.2f}s on this machine. Raised to {floor:.2f}s for this "
              f"process. Left alone it would have failed every question that "
              f"needs a solve, which reads as a model problem rather than a "
              f"configuration one. Set SOLVE_TIMEOUT to at least {floor:.1f} "
              f"to silence this.")


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


def _run(request: dict, timeout, label: str):
    """One worker call. The result string, or None if it did not produce one.

    Shared by both entry points so the kill, the exit-code check and the
    length cap cannot end up applying to one solve path and not the other --
    which is how the unbounded half of geometry survived a round of this.
    """
    budget = SOLVE_TIMEOUT_S if timeout is None else timeout
    try:
        proc = subprocess.run(
            [sys.executable, _WORKER],
            input=json.dumps(request), capture_output=True, text=True,
            timeout=budget,
            # Inherit nothing the child does not need.
            env={"PATH": os.environ.get("PATH", ""),
                 "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")},
        )
    except subprocess.TimeoutExpired:
        # subprocess.run kills the child here, which is the whole point: the
        # in-process equivalent leaves the spin running for the life of the
        # worker.
        print(f"[safe_solve] exceeded {budget}s and was killed: {label!r}")
        return None
    except Exception as e:                      # pragma: no cover - defensive
        print(f"[safe_solve] could not run: {type(e).__name__}: {e}")
        return None

    if proc.returncode != 0:
        print(f"[safe_solve] worker exited {proc.returncode}: "
              f"{(proc.stderr or '').strip()[:200]}")
        return None
    try:
        answer = json.loads(proc.stdout)
    except ValueError:
        print(f"[safe_solve] unreadable worker output: {proc.stdout[:200]!r}")
        return None
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
if os.getenv("SOLVE_STARTUP_PROBE", "1").strip() not in ("0", "false", "no"):
    _probe_startup()
