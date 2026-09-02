"""`quadratics` and `functions` -- the two topics grades 9-12 gained.

Grades 9-12 had no content of their own. Every other topic tops out at grade 8
by the CCSS grade of the concept it can *score*, so an audit of 640 generated
questions found 81% of grade-9 questions three or more grades below grade, and
writing a harder requirement into every `advanced` tier moved it two points --
harder numbers inside 8.EE.7b are still 8.EE.7b.

The solvers are the interesting half, and both refuse rather than guess. Both
are also pure integer arithmetic: no sympy, so no bounded subprocess and
nothing that can hang, the same as `missing_number` and `patterns`.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import llm_client  # noqa: E402
import lesson_plan_context  # noqa: E402
import hs_solvers  # noqa: E402
import question_schemas  # noqa: E402
import grade_appropriateness as ga  # noqa: E402
import LLM_topic_decider as decider  # noqa: E402
import LLM_quadratics_generation as quad  # noqa: E402
import LLM_functions_generation as funcs  # noqa: E402


# --- the quadratic solver -------------------------------------------------

@pytest.mark.parametrize("a,b,c,target,expected", [
    (1, -5, 6, "larger", 3),
    (1, -5, 6, "smaller", 2),
    (1, 0, -9, "larger", 3),           # no linear term
    (1, 0, -9, "smaller", -3),
    (1, 5, 6, "larger", -2),           # both roots negative
    (1, 5, 6, "smaller", -3),
    (2, -6, 4, "larger", 2),           # a monic equation scaled through by 2
    (-1, 4, 0, "larger", 4),           # negative leading coefficient
    (-1, 4, 0, "smaller", 0),
])
def test_the_requested_root_is_the_one_returned(a, b, c, target, expected):
    """Which root is asked for is the whole reason a two-root equation is
    scoreable here at all -- `algebra` refuses a quadratic precisely because
    presenting one root marks the other correct choice wrong."""
    assert hs_solvers.solve_quadratic(a, b, c, target) == (expected, None)


@pytest.mark.parametrize("a,b,c,why", [
    (0, 3, 6, "not a quadratic at all"),
    (1, 0, 1, "no real roots"),
    (1, -2, 1, "a repeated root, so 'larger' names nothing"),
    (1, 0, -2, "irrational roots"),
    (2, -3, 1, "roots 1 and 1/2 are not whole numbers"),
])
def test_a_quadratic_this_topic_cannot_score_is_refused(a, b, c, why):
    """A reason, never a guess. Each of these is an equation a teacher could
    legitimately set and this solver cannot score, so each costs one retry."""
    value, reason = hs_solvers.solve_quadratic(a, b, c, "larger")
    assert value is None, why
    assert reason, "a refusal must say why; the string is the only channel"


def test_the_repeated_root_refusal_is_not_an_arithmetic_accident():
    """`x^2 - 2x + 1 = 0` has the perfectly good answer 1, and is still
    refused. Both roots are the same number, so "the larger solution" is a
    question a careful student cannot answer -- and every distractor would be
    a number that is simply not a root, which teaches nothing."""
    assert hs_solvers.solve_quadratic(1, -2, 1, "larger")[0] is None
    assert hs_solvers.solve_quadratic(1, -2, 1, "smaller")[0] is None


def test_an_unknown_target_is_refused_rather_than_defaulting():
    """A default would pick a root, and the caller asked about the other one."""
    assert hs_solvers.solve_quadratic(1, -5, 6, "biggest")[0] is None


# --- rendering, which is what the question text must match ----------------

@pytest.mark.parametrize("a,b,c,expected", [
    (1, -5, 6, "x^2 - 5x + 6 = 0"),
    (2, 3, -1, "2x^2 + 3x - 1 = 0"),
    (1, 0, -9, "x^2 - 9 = 0"),          # a zero term is absent, not "+ 0x"
    (-1, 4, 0, "-x^2 + 4x = 0"),
    (1, -1, 0, "x^2 - x = 0"),          # a coefficient of 1 is not written
])
def test_the_equation_is_rendered_as_a_person_writes_it(a, b, c, expected):
    """Naive formatting gives "x^2 + -5x + 6 = 0", which nobody writes. The
    generator requires this string verbatim in the question text, so every
    sign it gets wrong is a retry on every question with a negative middle
    coefficient."""
    assert hs_solvers.render_quadratic(a, b, c) == expected


@pytest.mark.parametrize("coefficients,expected", [
    ([3, -2, 1], "3x^2 - 2x + 1"),
    ([1, 0, -4], "x^2 - 4"),
    ([-1, 5], "-x + 5"),
    ([2, 0], "2x"),
])
def test_a_polynomial_is_rendered_the_same_way(coefficients, expected):
    assert hs_solvers.render_polynomial(coefficients) == expected


def test_rendering_and_evaluating_agree_about_a_leading_zero():
    """`[0, 3]` is the constant 3 to both, which is the property that matters:
    a reader sees "3" and the solver scores 3. It is refused as a *question*
    one layer up, not here -- drawability and askability are different."""
    assert hs_solvers.render_polynomial([0, 3]) == "3"
    assert hs_solvers.evaluate_polynomial([0, 3], 7) == (3, None)
    assert hs_solvers.is_constant_polynomial([0, 3])


# --- the function solver --------------------------------------------------

@pytest.mark.parametrize("coefficients,x,expected", [
    ([3, -2, 1], 4, 41),
    ([1, 0, 1], 0, 1),
    ([2, -3], -5, -13),
])
def test_a_function_is_evaluated_exactly(coefficients, x, expected):
    assert hs_solvers.evaluate_polynomial(coefficients, x) == (expected, None)


def test_composition_applies_the_inner_function_first():
    """f(g(x)), not g(f(x)) -- they differ by one character in the question
    text and by a lot in the answer, which is why the generator requires the
    rendered call to appear verbatim."""
    f, g = [1, 0, 0], [2, 1]            # f(x) = x^2, g(x) = 2x + 1
    assert hs_solvers.solve_composition(f, g, 3) == (49, None)
    assert hs_solvers.solve_composition(g, f, 3) == (19, None)


def test_a_value_too_large_to_ask_about_is_refused():
    """Composition squares its input, so an unbounded pair of quadratics
    reaches 10^16 from coefficients that each look reasonable. "What is
    f(g(7))" answered 48,271,009 is a question about owning a calculator."""
    assert hs_solvers.evaluate_polynomial([9999, 0, 0], 9999)[0] is None
    assert hs_solvers.solve_composition([1, 0, 0], [9999, 0, 0], 99)[0] is None


def test_a_degree_above_two_is_refused():
    assert hs_solvers.evaluate_polynomial([1, 1, 1, 1], 2)[0] is None


# --- parsing model output, which is what replaces the subprocess ----------

@pytest.mark.parametrize("raw,expected", [
    ("12", 12), ("-7", -7), ("0", 0),
    ("99999", None),        # past four digits
    ("9**9**9", None),      # the operand safe_solve exists for
    (" 12 ", None), ("+3", None), ("1.5", None), ("x", None),
    (None, None), (True, None),
])
def test_only_a_bounded_integer_is_accepted(raw, expected):
    """This is what lets these two topics skip the bounded subprocess: there
    is no parser to hand `9**9**9` to, because nothing reaches `int()` until
    it has matched a four-digit pattern."""
    assert hs_solvers.parse_int(raw) == expected


def test_one_unusable_coefficient_rejects_the_whole_list():
    """Taking the readable ones would silently change the polynomial's degree,
    and the question would be scored against a function nobody wrote."""
    assert hs_solvers.parse_int_list(["1", "x", "3"], 3) is None
    assert hs_solvers.parse_int_list(["1", "2", "3", "4"], 3) is None
    assert hs_solvers.parse_int_list([], 3) is None
    assert hs_solvers.parse_int_list(["1", "-2"], 3) == [1, -2]


# --- shown versus scored --------------------------------------------------

def test_the_equation_on_screen_must_be_the_one_being_scored():
    assert quad.shown_matches_scored(
        "Solve x^2 - 5x + 6 = 0. What is the larger solution?",
        1, -5, 6, "larger") is None
    assert quad.shown_matches_scored(
        "Solve x^2 - 5x + 7 = 0. What is the larger solution?",
        1, -5, 6, "larger")


def test_asking_for_the_other_root_is_refused():
    """The failure no check on the equation alone can see: a well-formed
    question, correctly solved, scored against the root it did not ask for."""
    assert quad.shown_matches_scored(
        "Solve x^2 - 5x + 6 = 0. What is the smaller solution?",
        1, -5, 6, "larger")
    assert quad.shown_matches_scored(
        "Solve x^2 - 5x + 6 = 0. Give the solution.",
        1, -5, 6, "larger"), "a text naming neither root is not scoreable either"


def test_a_function_question_must_show_every_function_it_scores():
    shown = ["f(x) = x^2 + 1", "g(x) = 2x - 3"]
    assert funcs.shown_matches_scored(
        "If f(x) = x^2 + 1 and g(x) = 2x - 3, what is f(g(4))?",
        shown, "f(g(4))") is None
    assert funcs.shown_matches_scored(
        "If f(x) = x^2 + 1, what is f(g(4))?", shown, "f(g(4))")
    assert funcs.shown_matches_scored(
        "If f(x) = x^2 + 1 and g(x) = 2x - 3, what is g(f(4))?",
        shown, "f(g(4))")


# --- end to end through the retry loop ------------------------------------

@pytest.fixture
def reply(monkeypatch):
    monkeypatch.setattr(lesson_plan_context, "append_lesson_context",
                        lambda p, t, b: p)

    def _use(payload):
        monkeypatch.setattr(llm_client, "generate_text",
                            lambda *a, **k: json.dumps(payload))
    return _use


def _quadratic_reply(target):
    return {"question_text":
            f"Solve x^2 - 5x + 6 = 0. What is the {target} solution?",
            "question_topic": "quadratics",
            "coefficients": {"a": "1", "b": "-5", "c": "6"}}


def test_a_valid_quadratic_is_served_with_its_answer_among_options(reply,
                                                                   monkeypatch):
    # The target is chosen randomly per question, so pin it: otherwise this
    # asserts on whichever root today's shuffle asked for.
    monkeypatch.setattr(quad.random, "choice", lambda seq: "larger")
    reply(_quadratic_reply("larger"))
    question = quad.generate_quadratics_question([], [], "easy", "9th Grade")
    assert question["correct_answer"] == "3"
    assert question["correct_answer"] in question["answer_options"]
    assert len(set(question["answer_options"])) == 4
    assert question["question_topic"] == "quadratics"


def test_the_other_root_is_offered_as_a_distractor(reply, monkeypatch):
    """The mistake worth putting in front of a student: solved correctly, read
    "larger" as "smaller"."""
    monkeypatch.setattr(quad.random, "choice", lambda seq: "larger")
    reply(_quadratic_reply("larger"))
    question = quad.generate_quadratics_question([], [], "easy", "9th Grade")
    assert "2" in question["answer_options"]


def test_a_quadratic_whose_text_asks_for_the_other_root_retries(reply,
                                                                monkeypatch):
    monkeypatch.setattr(quad.random, "choice", lambda seq: "larger")
    reply(_quadratic_reply("smaller"))
    with pytest.raises(ValueError):
        quad.generate_quadratics_question([], [], "easy", "9th Grade")


def test_an_unscoreable_quadratic_retries_rather_than_raising(reply,
                                                              monkeypatch):
    """`x^2 + 1 = 0` has no real roots. Not a raise out of the generator -- a
    retry, which is what `_prefetch_worker` already handles."""
    monkeypatch.setattr(quad.random, "choice", lambda seq: "larger")
    reply({"question_text": "Solve x^2 + 1 = 0. What is the larger solution?",
           "question_topic": "quadratics",
           "coefficients": {"a": "1", "b": "0", "c": "1"}})
    with pytest.raises(ValueError):
        quad.generate_quadratics_question([], [], "easy", "9th Grade")


def test_a_valid_function_question_is_served_with_its_answer(reply):
    reply({"question_text": "If f(x) = 3x^2 - 2x + 1, what is f(4)?",
           "question_topic": "functions",
           "scenario": "evaluate",
           "f": ["3", "-2", "1"],
           "input": "4"})
    question = funcs.generate_functions_question([], [], "easy", "9th Grade")
    assert question["correct_answer"] == "41"
    assert question["correct_answer"] in question["answer_options"]
    assert len(set(question["answer_options"])) == 4


def test_a_composition_is_served_with_its_answer(reply):
    reply({"question_text": "If f(x) = x^2 and g(x) = 2x + 1, what is f(g(3))?",
           "question_topic": "functions",
           "scenario": "compose",
           "f": ["1", "0", "0"],
           "g": ["2", "1"],
           "input": "3"})
    question = funcs.generate_functions_question([], [], "medium", "9th Grade")
    assert question["correct_answer"] == "49"
    assert "19" in question["answer_options"], (
        "composing the other way round is the mistake this scenario invites")


def test_a_reply_naming_the_wrong_scenario_retries(reply):
    """Selecting a block is a prompt-level act; only checking the reply is
    enforcement. Haiku has returned the wrong scenario in two other topics."""
    reply({"question_text": "If f(x) = 3x^2 - 2x + 1, what is f(4)?",
           "question_topic": "functions",
           "scenario": "evaluate",          # medium asks for `compose`
           "f": ["3", "-2", "1"],
           "input": "4"})
    with pytest.raises(ValueError):
        funcs.generate_functions_question([], [], "medium", "9th Grade")


def test_a_constant_function_retries(reply):
    """"If f(x) = 7, what is f(4)" asks nothing about function notation, and
    it renders perfectly -- the tell is only in the coefficient list."""
    reply({"question_text": "If f(x) = 7, what is f(4)?",
           "question_topic": "functions",
           "scenario": "evaluate",
           "f": ["0", "7"],
           "input": "4"})
    with pytest.raises(ValueError):
        funcs.generate_functions_question([], [], "easy", "9th Grade")


# --- the grade gate -------------------------------------------------------

@pytest.mark.parametrize("topic", ["quadratics", "functions"])
def test_the_high_school_topics_are_withheld_below_grade_9(topic):
    for grade in ("1st Grade", "5th Grade", "8th Grade"):
        assert topic not in decider._allowed_topics(grade), grade
    for grade in ("9th Grade", "11th Grade", "Highschool"):
        assert topic in decider._allowed_topics(grade), grade


def test_grade_9_now_has_content_of_its_own():
    """The point of the whole change. Before it, every topic a 9th grader
    could be served topped out at grade 8 by the CCSS grade of its concept."""
    allowed = set(decider._allowed_topics("9th Grade"))
    assert {"quadratics", "functions"} <= allowed


def test_an_unreadable_grade_does_not_reach_the_high_school_topics():
    """`grade_levels` treats an unreadable grade as the youngest, and these
    are the topics furthest from that student."""
    allowed = decider._allowed_topics("not a grade at all")
    assert "quadratics" not in allowed
    assert "functions" not in allowed


@pytest.mark.parametrize("topic", ["quadratics", "functions"])
def test_the_high_school_topics_are_absent_from_forbidden_bands(topic):
    """Deliberately absent, exactly as `algebra` is. Variable notation is what
    these topics *are* -- `f(x)` and `x^2` -- so a band rule would refuse every
    question either one exists to ask. What keeps them away from a 6-year-old
    is `TOPIC_MIN_GRADE`, not `grade_appropriateness`."""
    assert topic not in ga.FORBIDDEN_BANDS


# --- schemas --------------------------------------------------------------

def test_the_compose_schema_carries_g_and_the_evaluate_one_does_not():
    """A closed object, so an `evaluate` reply cannot smuggle a second
    function past the solver -- which would render one function on screen and
    score another."""
    assert "g" in question_schemas.functions("compose")["properties"]
    assert "g" not in question_schemas.functions("evaluate")["properties"]


def test_the_quadratics_schema_does_not_let_the_model_pick_the_root():
    """Which root is asked for is decided before the call and written into the
    prompt. In the schema it would be the model's to choose, and a reply
    naming one root in the text and the other in a field is the one failure
    the coefficients cannot reveal."""
    assert "target" not in question_schemas.quadratics()["properties"]
