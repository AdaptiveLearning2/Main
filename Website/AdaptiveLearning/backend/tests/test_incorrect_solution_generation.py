"""Every distractor generator terminates, whatever the solution's shape."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402
import sympy as sp  # noqa: E402

import incorrect_solution_generation as inc  # noqa: E402


@pytest.mark.parametrize("expression", [
    "5*x",        # a single term, `2x+3x` simplified
    "-4*x",       # ...and negative, so sign_error lands back on a neighbour
    "x",          # coefficient 1
    "0",          # nothing to perturb at all
    "2*x + 3",    # an Add
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
    """Pins the `is_Mul` branch, which the deterministic filler would otherwise mask."""
    expr = sp.sympify("5*x")
    reachable = {str(inc.wrong_coefficient(expr)) for _ in range(200)}
    assert len(reachable) >= 3, f"only {reachable} reachable"


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_a_non_finite_solution_is_refused_rather_than_looped_over(bad):
    """`f"{inf + n:.2f}"` is always "inf"; reachable via a model-supplied 1e200 side."""
    with pytest.raises(ValueError):
        inc.generate_general_incorrect_answers(bad)


def test_the_numeric_filler_is_bounded_even_if_the_guard_is_bypassed(monkeypatch):
    """Belt and braces behind the `isfinite` guard."""
    import math
    monkeypatch.setattr(math, "isfinite", lambda _v: True)
    # Returning at all is the assertion.
    inc.generate_general_incorrect_answers(float("inf"))
