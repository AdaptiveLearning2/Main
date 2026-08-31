"""Algebra scores exactly one numeric solution, or it retries.

The solve ran after the retry loop, so anything it could not handle was an
exception out of the generator rather than another attempt. `solve` returning
`[]` -- an equation with no solution, like `x+1 = x+2` -- became `None` and
then `float(None)` inside the distractor generator: a TypeError from the
endpoint, measured at 1 of 3 generations against Haiku 4.5, on the topic this
product serves most.

The multiple-solutions case is the one CLAUDE.md already described and nothing
enforced: this topic is "one linear equation with one solution -- a quadratic
would present one root as the answer and mark the other correct choice wrong."
A constraint documented and unenforced is a wrong answer waiting for the model
to write a quadratic.
"""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import LLM_algebra_generation as alg  # noqa: E402


def test_an_ordinary_linear_equation_solves():
    """The bar for every rejection below: none of them may cost this."""
    assert alg._solve_equation(["2", "x", "+", "3", "=", "11"], 1) == "4"


@pytest.mark.parametrize("variables,why", [
    (["x", "+", "1", "=", "x", "+", "2"], "no solution -- the TypeError"),
    (["x", "**", "2", "=", "4"], "two roots -- scored one, marked the other wrong"),
    (["2", "x", "+", "3"], "no equals sign -- split() raised on unpacking"),
    (["1", "=", "x", "=", "2"], "two equals signs -- same unpacking raise"),
    (["a", "+", "b", "=", "c"], "solution is not a number"),
])
def test_an_unscorable_equation_is_a_retry_not_a_raise(variables, why):
    assert alg._solve_equation(variables, 1) is None, why


def test_a_rejection_returns_none_rather_than_raising():
    """The retry loop tests `is None`. A raise would escape it, which is the
    behaviour every case above used to have."""
    try:
        assert alg._solve_equation(["!", "?", "="], 1) is None
    except Exception as e:                       # pragma: no cover
        pytest.fail(f"raised instead of returning None: {type(e).__name__}: {e}")
