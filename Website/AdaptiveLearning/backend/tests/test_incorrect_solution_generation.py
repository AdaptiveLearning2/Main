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


# ── a distractor must not be told apart from the answer by its form ─────────

@pytest.mark.parametrize("answer", ["-1/4", "-3", "-7/2", "1/4", "3", "0"])
def test_rational_distractors_share_the_answers_sign(answer, monkeypatch):
    """A negative answer among three positive fractions was the only negative option."""
    for _ in range(20):
        wrong = inc.generate_incorrect_rational(answer)
        assert len(set(wrong)) == 3
        assert all((sp.sympify(w) < 0) == (sp.sympify(answer) < 0) for w in wrong), wrong


def test_the_rational_filler_keeps_the_sign(monkeypatch):
    """With the random draws exhausted, the offsets move away from zero."""
    monkeypatch.setattr(inc, "MAX_ATTEMPTS", 0)
    assert inc.generate_incorrect_rational("-1/4") == ["-5/4", "-9/4", "-13/4"]


def test_a_fractional_algebra_answer_is_offered_among_fractions(monkeypatch):
    """The worker answers "3/2"; decimal distractors left it the only fraction."""
    import json
    import llm_client
    import lesson_plan_context
    import LLM_algebra_generation as algebra
    payload = {"question_text": "Solve for x: 2x = 3", "question_topic": "algebra",
               "variables": ["2x", "=", "3"]}
    monkeypatch.setattr(llm_client, "generate_text", lambda *a, **k: json.dumps(payload))
    monkeypatch.setattr(lesson_plan_context, "append_lesson_context", lambda p, t, b: p)
    question = algebra.generate_algebra_question([], [], "hard", "8th Grade")
    assert question["correct_answer"] == "3/2"
    assert len(set(question["answer_options"])) == 4
    assert all("/" in option for option in question["answer_options"]), question["answer_options"]


def test_an_irrational_algebra_answer_is_retried(monkeypatch):
    """"sqrt(2)/2" took the fraction branch on its "/" and stood out as the only root."""
    import json
    import llm_client
    import lesson_plan_context
    import LLM_algebra_generation as algebra
    payload = {"question_text": "Solve for x: 2x = 3", "question_topic": "algebra",
               "variables": ["2x", "=", "3"]}
    monkeypatch.setattr(llm_client, "generate_text", lambda *a, **k: json.dumps(payload))
    monkeypatch.setattr(lesson_plan_context, "append_lesson_context", lambda p, t, b: p)
    solved = iter(["sqrt(2)/2", "3/2"])
    monkeypatch.setattr(algebra, "_solve_equation", lambda *a: next(solved))
    assert algebra.generate_algebra_question([], [], "hard", "8th Grade")["correct_answer"] == "3/2"


@pytest.mark.parametrize("text,variables,value", [
    ("Solve for x: x + 2.5 = 7", ["x", "+", "2.5", "=", "7"], "4.5"),
    ("Solve for x: 0.5x = 2", ["0.5x", "=", "2"], "4"),
])
def test_a_decimal_algebra_answer_is_served_among_decimals(monkeypatch, text, variables, value):
    """Floats: refused by the irrational retry, then shown as "4.50000000000000" beside "5.5"."""
    import answer_format
    import json
    import llm_client
    import lesson_plan_context
    import LLM_algebra_generation as algebra
    payload = {"question_text": text, "question_topic": "algebra", "variables": variables}
    asked = []
    monkeypatch.setattr(llm_client, "generate_text",
                        lambda *a, **k: asked.append(1) or json.dumps(payload))
    monkeypatch.setattr(lesson_plan_context, "append_lesson_context", lambda p, t, b: p)
    question = algebra.generate_algebra_question([], [], "hard", "8th Grade")
    options = question["answer_options"]
    assert len(asked) == 1 and question["correct_answer"] == value and value in options
    assert len(set(options)) == 4
    assert all(answer_format.format_value(o) == o and "/" not in o for o in options), options


@pytest.mark.parametrize("answer", ["1/2", "-7/3"])
def test_a_fractional_answer_gets_no_whole_number_distractor(answer, monkeypatch):
    """Every draw is 6/3, so without the guard the first distractor would be "2"."""
    draws = iter([6, 3] * 200)
    monkeypatch.setattr(inc.random, "randint", lambda a, b: next(draws))
    wrong = inc.generate_incorrect_rational(answer)
    assert all(not sp.sympify(w).is_integer for w in wrong), wrong
