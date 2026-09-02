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


@pytest.fixture
def fixed_quadratic(monkeypatch):
    """Pin the equation and the root, both of which are chosen randomly.

    The generator settles them before it calls the model, so a test that did
    not pin them would assert against whichever equation today's shuffle
    produced.
    """
    monkeypatch.setattr(quad, "_choose_coefficients", lambda *_a: (1, -5, 6))
    monkeypatch.setattr(quad.random, "choice", lambda seq: "larger")


def _quadratic_reply(target):
    return {"question_text":
            f"Solve x^2 - 5x + 6 = 0. What is the {target} solution?",
            "question_topic": "quadratics"}


def test_a_valid_quadratic_is_served_with_its_answer_among_options(
        reply, fixed_quadratic):
    reply(_quadratic_reply("larger"))
    question = quad.generate_quadratics_question([], [], "easy", "9th Grade")
    assert question["correct_answer"] == "3"
    assert question["correct_answer"] in question["answer_options"]
    assert len(set(question["answer_options"])) == 4
    assert question["question_topic"] == "quadratics"


def test_the_other_root_is_offered_as_a_distractor(reply, fixed_quadratic):
    """The mistake worth putting in front of a student: solved correctly, read
    "larger" as "smaller"."""
    reply(_quadratic_reply("larger"))
    question = quad.generate_quadratics_question([], [], "easy", "9th Grade")
    assert "2" in question["answer_options"]


def test_a_quadratic_whose_text_asks_for_the_other_root_retries(
        reply, fixed_quadratic):
    reply(_quadratic_reply("smaller"))
    with pytest.raises(ValueError):
        quad.generate_quadratics_question([], [], "easy", "9th Grade")


def test_a_text_that_drops_the_equation_it_was_handed_retries(
        reply, fixed_quadratic):
    """The only thing the model can still get wrong. It is given the equation
    and asked to write the sentence around it, so a text carrying a different
    one is not a harder question -- it is a question about an equation nobody
    is scoring."""
    reply({"question_text": "Solve x^2 - 7x + 12 = 0. What is the larger solution?",
           "question_topic": "quadratics"})
    with pytest.raises(ValueError):
        quad.generate_quadratics_question([], [], "easy", "9th Grade")


# --- the coefficients are chosen here, not asked for ----------------------

@pytest.mark.parametrize("difficulty", ["easy", "medium", "hard"])
@pytest.mark.parametrize("band", ["early", "middle", "upper", "advanced"])
def test_every_built_equation_is_solvable(difficulty, band):
    """The property the whole redesign rests on. Measured across three
    promptings, llama3.1:8b produced a factorable quadratic 0 of 3, 2 of 3 and
    1 of 4 times -- nearly every failure `irrational roots`, since a freely
    chosen b and c almost never leave a perfect-square discriminant. Built
    from two distinct integer roots it cannot fail, and every one of those
    retries was a billed call that could not have succeeded.

    Run over many draws rather than one: the failure this replaces was
    probabilistic, so a single sample would not have caught it either.
    """
    for _ in range(200):
        a, b, c = quad._choose_coefficients(difficulty, band)
        for target in ("larger", "smaller"):
            value, reason = hs_solvers.solve_quadratic(a, b, c, target)
            assert value is not None, (a, b, c, target, reason)


@pytest.mark.parametrize("difficulty,expected_scales", [
    ("easy", {1}), ("medium", {1}), ("hard", {2, 3, 4}),
])
def test_the_hard_tier_is_the_one_with_a_leading_coefficient(difficulty,
                                                             expected_scales):
    """A leading coefficient above 1 is what turns factoring into the AC
    method, and is the rung the model was *least* able to produce -- it is
    reachable only because the equation is built here."""
    seen = {quad._choose_coefficients(difficulty, "advanced")[0]
            for _ in range(200)}
    assert seen == expected_scales


def test_easy_keeps_both_roots_positive_and_medium_does_not():
    """Difficulty has to mean something, and before this it meant whatever the
    model reached for -- two of its `hard` replies were `a = 1` with both
    roots positive, which is the easy tier wearing a hard label."""
    def roots(difficulty):
        a, b, c = quad._choose_coefficients(difficulty, "advanced")
        return {hs_solvers.solve_quadratic(a, b, c, t)[0]
                for t in ("larger", "smaller")}

    assert all(min(roots("easy")) > 0 for _ in range(50))
    assert any(min(roots("medium")) < 0 for _ in range(50))


@pytest.fixture
def fixed_functions(monkeypatch):
    """Pin the functions and the input, which the generator now chooses.

    `f(x) = 3x^2 - 2x + 1` and `g(x) = 2x + 1`, evaluated at 3 -- so
    `evaluate` scores f(3) = 22 and `compose` scores f(g(3)) = f(7) = 134.
    """
    monkeypatch.setattr(
        funcs, "_choose_functions",
        lambda scenario, band: ([3, -2, 1],
                                [2, 1] if scenario == funcs.COMPOSE else None,
                                3))


def test_a_valid_function_question_is_served_with_its_answer(reply,
                                                             fixed_functions):
    reply({"question_text": "If f(x) = 3x^2 - 2x + 1, what is f(3)?",
           "question_topic": "functions",
           "scenario": "evaluate"})
    question = funcs.generate_functions_question([], [], "easy", "9th Grade")
    assert question["correct_answer"] == "22"
    assert question["correct_answer"] in question["answer_options"]
    assert len(set(question["answer_options"])) == 4


def test_a_composition_is_served_with_its_answer(reply, fixed_functions):
    reply({"question_text":
           "If f(x) = 3x^2 - 2x + 1 and g(x) = 2x + 1, what is f(g(3))?",
           "question_topic": "functions",
           "scenario": "compose"})
    question = funcs.generate_functions_question([], [], "medium", "9th Grade")
    assert question["correct_answer"] == "134"
    # g(f(3)) = g(22) = 45 -- composing the other way round, which is the
    # mistake this scenario invites.
    assert "45" in question["answer_options"]


def test_a_composition_text_that_swaps_the_order_retries(reply,
                                                         fixed_functions):
    """"f(g(3))" and "g(f(3))" differ by one character and by 89 here."""
    reply({"question_text":
           "If f(x) = 3x^2 - 2x + 1 and g(x) = 2x + 1, what is g(f(3))?",
           "question_topic": "functions",
           "scenario": "compose"})
    with pytest.raises(ValueError):
        funcs.generate_functions_question([], [], "medium", "9th Grade")


@pytest.mark.parametrize("scenario,band", [
    (funcs.EVALUATE, "advanced"), (funcs.COMPOSE, "advanced"),
    (funcs.EVALUATE, "early"), (funcs.COMPOSE, "upper"),
])
def test_every_built_function_question_is_answerable(scenario, band):
    """The property the hand-over rests on, and the reason the inner function
    of a composition is always degree 1: composition squares its input, so an
    unbounded pair reaches 10^16 from coefficients that each look reasonable
    and `MAX_ABS_RESULT` refuses them. Bounded by construction, not by drawing
    until something fits."""
    for _ in range(200):
        f, g, x = funcs._choose_functions(scenario, band)
        assert x != 0, "f(0) is the constant term and asks nothing"
        assert len(f) >= 2 and f[0] != 0, f
        if scenario == funcs.COMPOSE:
            assert len(g) == 2, "the inner function is what bounds the result"
            value, reason = hs_solvers.solve_composition(f, g, x)
        else:
            assert g is None
            value, reason = hs_solvers.evaluate_polynomial(f, x)
        assert value is not None, (f, g, x, reason)


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


@pytest.mark.parametrize("text,flagged", [
    ("If f(x) = x^2 and g(x) = 2x - 3, what is f(4)?", True),
    ("If f(x) = x^2, what is g(4)?", True),
    ("If f(x) = x^2, what is f(4)?", False),
    # The false positive worth avoiding: an ordinary word ending in "g"
    # immediately before a bracket.
    ("Solving (x + 1) first, if f(x) = x^2, what is f(4)?", False),
    ("Using (the) rule f(x) = 2x, what is f(3)?", False),
])
def test_a_second_function_is_recognised_without_catching_ordinary_words(
        text, flagged):
    assert funcs._mentions_second_function(text) is flagged


def test_an_evaluate_question_naming_a_second_function_retries(
        reply, fixed_functions):
    """`_EVALUATE_BLOCK` says `Do NOT include "g"`, which is prompt text --
    and the lesson plan is appended after it and cannot know which scenario
    was chosen, so it can only be written to name no function the block did
    not. This is what makes that hold.

    `shown_matches_scored` cannot: it checks every scored function *appears*,
    never that nothing else does, so a text defining both f and g passes it
    while the solver reads f alone and the student is shown a function that
    plays no part in the answer.
    """
    # Differs from the accepted text below by the g clause and nothing else:
    # it carries the right function and asks for the right value, so
    # `shown_matches_scored` is satisfied and only the second-function guard
    # can reject it.
    #
    # Written that way deliberately. The first version of this test omitted
    # `fixed_functions`, so the generator drew its own functions, the reply
    # matched none of them, and all three attempts died in
    # `shown_matches_scored` -- it raised ValueError, passed, and would have
    # gone on passing with the guard deleted. That is the shape this file
    # warns about elsewhere, and it survived an earlier mutation check
    # because the check ran *before* the redesign that moved the coefficients
    # into code. A mutation check is a point-in-time property; a later
    # refactor can hollow out a test without touching it.
    reply({"question_text":
           "If f(x) = 3x^2 - 2x + 1 and g(x) = 2x - 3, what is f(3)?",
           "question_topic": "functions",
           "scenario": "evaluate"})
    with pytest.raises(ValueError):
        funcs.generate_functions_question([], [], "easy", "9th Grade")


def test_the_same_text_without_the_second_function_is_accepted(
        reply, fixed_functions):
    """The positive control for the test above, and the reason it has teeth:
    the two replies differ only by the g clause, so a failure of the first
    can only be the guard."""
    reply({"question_text": "If f(x) = 3x^2 - 2x + 1, what is f(3)?",
           "question_topic": "functions",
           "scenario": "evaluate"})
    question = funcs.generate_functions_question([], [], "easy", "9th Grade")
    assert question["correct_answer"] == "22"


def test_compose_is_still_allowed_to_name_g(reply, fixed_functions):
    """The teeth. Applying the check to both scenarios would refuse every
    composition, which is the topic's whole grade-9 claim."""
    reply({"question_text":
           "If f(x) = 3x^2 - 2x + 1 and g(x) = 2x + 1, what is f(g(3))?",
           "question_topic": "functions",
           "scenario": "compose"})
    question = funcs.generate_functions_question([], [], "medium", "9th Grade")
    assert question["correct_answer"] == "134"


@pytest.mark.parametrize("band", ["early", "middle", "upper", "advanced"])
@pytest.mark.parametrize("scenario", ["evaluate", "compose"])
def test_the_identity_is_never_built(band, scenario):
    """`f(x) = x` renders fine, evaluates fine and asks nothing -- the
    constant's twin, and newly reachable once the generator started picking
    the coefficients itself. A non-zero leading coefficient rules out the
    constant and lets `[1, 0]` through, measured at about 1 draw in 300.

    Worse on compose: `f(g(x))` is then identically `g(x)`, and the swapped
    distractor disappears silently, because `g(f(x))` equals the answer and
    the near-miss list drops any candidate equal to it. The question loses
    the one distractor that tests whether the order was read.

    Many draws, because the failure was probabilistic -- a single sample
    would have missed it, which is how it shipped.
    """
    for _ in range(2000):
        f, g, _x = funcs._choose_functions(scenario, band)
        for name, poly in (("f", f), ("g", g)):
            if poly is None:
                continue
            assert not (len(poly) == 2 and abs(poly[0]) == 1
                        and poly[1] == 0), f"{name} = {poly} is the identity"


def test_a_composition_still_composes_something():
    """The consequence, stated as the property rather than the mechanism: an
    identity outer function makes f(g(x)) equal g(x) for every input."""
    for _ in range(500):
        f, g, x = funcs._choose_functions(funcs.COMPOSE, "advanced")
        composed, _r = hs_solvers.solve_composition(f, g, x)
        inner, _r2 = hs_solvers.evaluate_polynomial(g, x)
        outer_of_inner_alone = inner
        if composed == outer_of_inner_alone:
            # Possible by coincidence, but not because f does nothing.
            assert not (len(f) == 2 and abs(f[0]) == 1 and f[1] == 0), f


def test_a_constant_function_is_never_built():
    """"If f(x) = 7, what is f(4)" asks nothing about function notation.

    This was a retry test against a reply carrying `f: ["0", "7"]`, and the
    redesign made it vacuous rather than wrong: the generator no longer reads
    a coefficient list from the reply, so that payload was ignored and the
    ValueError came from the text not matching whatever had been drawn. The
    guard it named had no caller left either.

    The property survives the redesign in a stronger form -- a constant is
    now unrepresentable rather than rejected -- so this asserts *that*,
    against the function that builds them.
    """
    for band in ("early", "middle", "upper", "advanced"):
        for _ in range(100):
            f, _g, _x = funcs._choose_functions(funcs.EVALUATE, band)
            assert len(f) >= 2 and f[0] != 0, f
            assert len(f) >= 2 and f[0] != 0, f


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

@pytest.mark.parametrize("scenario", ["evaluate", "compose"])
def test_the_functions_schema_asks_for_the_sentence_and_the_scenario(scenario):
    """The coefficients and the input are handed over, not requested. Asked
    for its own, the model also had to reproduce `render_polynomial`'s exact
    spacing and signs, since the text must contain that string verbatim --
    and `compose` failed 3 of 3 on llama3.1:8b doing precisely that.

    `scenario` stays, unlike quadratics' `target`: it is a cheap cross-check
    that the model read the block it was handed."""
    assert set(question_schemas.functions(scenario)["properties"]) == {
        "question_text", "question_topic", "scenario"}


def test_the_quadratics_schema_asks_for_nothing_but_the_sentence():
    """Both the equation and the root are decided before the call, so neither
    is the model's to return. `coefficients` was in this schema and is what
    the measurement removed; `target` was never in it, because a reply naming
    one root in the text and another in a field is the one disagreement the
    coefficients could not have revealed."""
    assert set(question_schemas.quadratics()["properties"]) == {
        "question_text", "question_topic"}
