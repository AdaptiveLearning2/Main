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
SOLVE_TIMEOUT_S = llm_client._env_number("SOLVE_TIMEOUT", 10.0, float, minimum=1.0)

_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "_solve_worker.py")


# A solved expression here is a number or a short polynomial. A result longer
# than this did not come from a question a student can be asked, and re-parsing
# it in the parent -- which is outside the subprocess bound -- is exactly the
# cost this module exists to avoid paying unboundedly.
MAX_RESULT_CHARS = 200


def safe_solve(expression: str, scenario: str, timeout: float | None = None):
    """`str(solution)` for one expression, or None if it did not finish.

    None means "retry this question", which is what every caller already does
    with a reply it cannot use.
    """
    budget = SOLVE_TIMEOUT_S if timeout is None else timeout
    payload = json.dumps({"expr": expression, "scenario": scenario})
    try:
        proc = subprocess.run(
            [sys.executable, _WORKER],
            input=payload, capture_output=True, text=True, timeout=budget,
            # Inherit nothing the child does not need.
            env={"PATH": os.environ.get("PATH", ""),
                 "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")},
        )
    except subprocess.TimeoutExpired:
        # subprocess.run kills the child here, which is the whole point: the
        # in-process equivalent leaves the spin running for the life of the
        # worker.
        print(f"[safe_solve] {scenario} exceeded {budget}s and was killed: "
              f"{expression[:80]!r}")
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
