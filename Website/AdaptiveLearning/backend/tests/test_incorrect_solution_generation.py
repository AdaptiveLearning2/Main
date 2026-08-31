"""Every distractor generator terminates, whatever the solution's shape.

All three were `while len(results) < N` with no bound. The symbolic one was not
merely theoretically unbounded -- it hung deterministically on the commonest
answer it is asked about. `wrong_coefficient` only perturbed an `Add`, so for
`5*x` (simplifying `2x+3x`) it returned the expression unchanged, leaving
`sign_error`'s negation as the only reachable alternative: two distinct values
where the caller needs three.

Measured on this codebase: 28 minutes at 100% CPU before being killed by hand.
It read as intermittent only because `_pick_scenario` reaches `simplify` about
one time in three, and only above the "middle" grade band.

Every test here would hang rather than fail against the old code, which is the
argument for the bound rather than for better mutations: a wrong answer is a
test failure, and a loop that cannot finish is a build nobody can read.
"""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402
import sympy as sp  # noqa: E402

import incorrect_solution_generation as inc  # noqa: E402


@pytest.mark.parametrize("expression", [
    "5*x",        # the one that hung: a single term, `2x+3x` simplified
    "-4*x",       # ...and negative, so sign_error lands back on a neighbour
    "x",          # coefficient 1
    "0",          # nothing to perturb at all
    "2*x + 3",    # an Add, which is what the original handled
    "7",          # a bare constant
])
def test_three_distinct_wrong_answers_for_any_shape(expression):
    wrong = inc.generate_symbolic_incorrect_answers(sp.sympify(expression))
    assert len(set(wrong)) == 3, f"{expression} produced {wrong}"
    assert str(sp.sympify(expression)) not in wrong, \
        "the correct answer was offered as a distractor"


@pytest.mark.parametrize("answer", ["0", "1", "12.5", "100"])
def test_the_numeric_generator_terminates_and_is_distinct(answer):
    wrong = inc.generate_general_incorrect_answers(answer)
    assert len(set(wrong)) == 3, wrong


@pytest.mark.parametrize("answer", ["1/2", "3/4", "1"])
def test_the_rational_generator_terminates_and_is_distinct(answer):
    wrong = inc.generate_incorrect_rational(answer)
    assert len(set(wrong)) == 3, wrong


def test_a_single_term_has_more_than_two_reachable_variants():
    """The direct cause, pinned separately from its symptom.

    `wrong_coefficient` returning its argument unchanged is what made the set
    of reachable values smaller than `count`. A future edit that drops the
    `is_Mul` branch would pass every test above -- the deterministic filler
    covers for it -- while putting the loop back one bad refactor from hanging.
    """
    expr = sp.sympify("5*x")
    reachable = {str(inc.wrong_coefficient(expr)) for _ in range(200)}
    assert len(reachable) >= 3, f"only {reachable} reachable"
