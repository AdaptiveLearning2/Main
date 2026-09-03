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
import LLM_spread_generation as spread  # noqa: E402


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


# --- spread (S-ID.2) ------------------------------------------------------

def test_every_pattern_has_an_exact_standard_deviation():
    """The property the topic rests on, checked against every entry rather
    than trusted.

    Most datasets have an irrational population standard deviation, and an
    answer rounded to whatever precision a formatter chose is the
    "answered correctly, marked wrong" failure wearing a decimal point. These
    patterns are hardcoded precisely so no search runs per request; that makes
    them data, and data goes stale silently.
    """
    for pattern in spread._DEVIATION_PATTERNS:
        assert sum(pattern) == 0, f"{pattern} does not centre on its mean"
        sd, reason = hs_solvers.population_sd([100 + d for d in pattern])
        assert sd is not None, f"{pattern}: {reason}"
        assert sd > 0
        # And scaling keeps it exact, which is what makes each line a family.
        scaled, _r = hs_solvers.population_sd([100 + 3 * d for d in pattern])
        assert scaled == sd * 3


@pytest.mark.parametrize("band", ["early", "middle", "upper", "advanced"])
def test_every_built_dataset_is_exactly_scoreable(band):
    """Built here, not asked for -- a model choosing its own values would
    miss an exact standard deviation on nearly every attempt, against a
    constraint it cannot see."""
    for _ in range(200):
        values = spread._choose_dataset(band)
        sd, reason = hs_solvers.population_sd(values)
        assert sd is not None, (values, reason)
        assert min(values) >= 0, f"a negative reading in {values}"


@pytest.mark.parametrize("values,why", [
    ([1, 2, 3], "irrational"),
    ([5, 5, 5], "no spread at all"),
    ([1, 2], "the mean is not whole"),
    ([7], "one value is not a spread"),
    ([], "nothing to measure"),
])
def test_a_dataset_with_no_exact_spread_is_refused(values, why):
    sd, reason = hs_solvers.population_sd(values)
    assert sd is None, why
    assert reason


def test_the_question_must_say_population():
    """Sample standard deviation over n-1 is what many high-school courses
    teach, and it gives a different number. A question saying only "standard
    deviation" has two defensible answers and scores one -- which is worse
    than the rounding problem this topic was deferred over, and only the
    wording fixes it."""
    data = "14, 16, 17, 18, 20"
    assert spread.shown_matches_scored(
        f"Scores: {data}. What is the population standard deviation?",
        [data]) is None
    assert spread.shown_matches_scored(
        f"Scores: {data}. What is the standard deviation?", [data])


def test_the_data_on_screen_must_be_the_data_being_scored():
    assert spread.shown_matches_scored(
        "A: 1, 2, 3. B: 4, 5, 6. Population standard deviation?",
        ["1, 2, 3", "4, 5, 6"]) is None
    assert spread.shown_matches_scored(
        "A: 1, 2, 3. Population standard deviation?",
        ["1, 2, 3", "4, 5, 6"]), "the second set is not on screen"


def test_the_variance_is_offered_as_the_distractor():
    """Forgetting the square root is the commonest error on this topic, so it
    is the option worth putting in front of a student."""
    values = [7, 9, 10, 11, 13]                     # sd 2, variance 4
    wrong = spread.generate_incorrect_answers(
        2, [hs_solvers.population_variance(values)])
    assert 4 in wrong


def test_no_distractor_is_a_negative_spread():
    """A spread cannot be negative, so an option that is would be eliminable
    without doing the arithmetic."""
    for solution in (1, 2, 3):
        for candidate in spread.generate_incorrect_answers(solution, [None]):
            assert float(candidate) >= 0, candidate


def test_containment_alone_would_accept_a_longer_data_set():
    """"14, 16, 17, 18, 20, 25" contains "14, 16, 17, 18, 20", so a check
    asking only whether each rendered set appears somewhere accepts a sixth
    value the solver never saw. The runs have to match exactly."""
    assert spread.shown_matches_scored(
        "Scores: 14, 16, 17, 18, 20, 25. Population standard deviation?",
        ["14, 16, 17, 18, 20"])
    assert spread.shown_matches_scored(
        "Scores: 12, 14, 16, 17, 18, 20. Population standard deviation?",
        ["14, 16, 17, 18, 20"]), "prepending a value is the same defect"


def test_the_two_sets_must_be_in_the_order_they_are_scored():
    assert spread.shown_matches_scored(
        "Set A: 4, 5, 6. Set B: 1, 2, 3. Population standard deviation?",
        ["1, 2, 3", "4, 5, 6"])


def test_the_sets_must_sit_under_the_labels_they_are_scored_under():
    """The one no ordering check catches: same sets, same order, labels
    swapped. The question then asks for A minus B while B minus A is scored,
    so the answer on screen is the negation of the one marked correct."""
    swapped = "Set B: 1, 2, 3. Set A: 4, 5, 6. Population standard deviation?"
    assert spread.shown_matches_scored(swapped, ["1, 2, 3", "4, 5, 6"]) is None, \
        "the runs alone are in the right order, which is the trap"
    assert spread.shown_matches_scored(
        swapped, ["1, 2, 3", "4, 5, 6"],
        ["Set A: 1, 2, 3", "Set B: 4, 5, 6"])


def test_two_correctly_labelled_sets_can_still_carry_the_wrong_question():
    """Binding the data is not binding the ask, and this is the gap that
    leaves.

    Set A (sd 2) and Set B (sd 5), correctly labelled and in the scored
    order, under a text asking for Set B's own standard deviation. Every data
    check passes; the answer on screen is 5 and 3 is scored. Worse than a
    plain wrong answer, because `near` offers each set's own spread as a
    distractor -- so 5 is on the option list and a student who answers the
    question actually shown is marked wrong for it.
    """
    shown = ["17, 19, 20, 21, 23", "13, 19, 21, 27"]
    labelled = [f"Set A: {shown[0]}", f"Set B: {shown[1]}"]
    asks_one = (f"Two machines. {labelled[0]}. {labelled[1]}. What is the "
                f"population standard deviation of Set B?")
    assert spread.shown_matches_scored(asks_one, shown, labelled) is None, \
        "the data checks alone accept it, which is the point"
    assert spread.shown_matches_scored(asks_one, shown, labelled,
                                       spread.TWO_SETS)


def test_the_comparison_must_run_in_the_direction_it_is_scored():
    """"How much larger is A than B" is the negation of what is scored, and
    reads as a perfectly ordinary question."""
    shown = ["1, 2, 3", "4, 5, 6"]
    labelled = ["Set A: 1, 2, 3", "Set B: 4, 5, 6"]
    body = f"{labelled[0]}. {labelled[1]}."
    assert spread.shown_matches_scored(
        f"{body} How much larger is the population standard deviation of "
        f"Set B than that of Set A?", shown, labelled, spread.TWO_SETS) is None
    assert spread.shown_matches_scored(
        f"{body} How much larger is the population standard deviation of "
        f"Set A than that of Set B?", shown, labelled, spread.TWO_SETS)
    # Direction is pinned by both labels, so either anchor alone still
    # refuses the reversal -- but only this one refuses a set compared with
    # itself, which is what a model repeating a label produces.
    assert spread.shown_matches_scored(
        f"{body} How much larger is the population standard deviation of "
        f"Set A than that of Set A?", shown, labelled, spread.TWO_SETS)


@pytest.mark.parametrize("ask", [
    "How much larger is the population standard deviation of Set B than that of Set A?",
    "By how much larger is the population standard deviation of Set B than that of Set A?",
    "How much greater is Set B's population standard deviation than Set A's?",
    "How much bigger is the population standard deviation of Set B compared to Set A?",
    # Neither of these reads as unusual, and an ordered regex pinning the
    # comparative ahead of the label refused both. Only the prompt was
    # holding that, which is one model away from three retries and a 503.
    "By how much does the population standard deviation of Set B exceed that of Set A?",
    "Set B's population standard deviation is how much larger than Set A's?",
])
def test_the_phrasings_a_model_actually_writes_are_accepted(ask):
    """A refusal costs a retry, so the accepted set has to cover the wording
    real replies use -- the first two are verbatim from Ollama and Claude."""
    shown = ["1, 2, 3", "4, 5, 6"]
    labelled = ["Set A: 1, 2, 3", "Set B: 4, 5, 6"]
    assert spread.shown_matches_scored(
        f"Set A: 1, 2, 3. Set B: 4, 5, 6. {ask}", shown, labelled,
        spread.TWO_SETS) is None, ask


def test_a_one_set_question_may_not_ask_a_comparison():
    """There is only one data set on screen, so a comparison can only be
    against a number the model invented -- and a bare number is not a run, so
    the data check would not see it."""
    assert spread.shown_matches_scored(
        "Readings: 1, 2, 3. How much larger is the population standard "
        "deviation than 10?", ["1, 2, 3"], (), spread.ONE_SET)


def test_a_comparative_in_the_context_does_not_refuse_a_one_set_question():
    """The guard belongs to the ask, not to the sentence before it.

    Consistency and spread are this topic's own subject matter and the prompt
    asks for varied context, so "how much more consistent" in a scene-setting
    clause breaks no stated rule. Searched over the whole text it refused
    anyway -- and three of those exhaust the retries and reach the student as
    a 503, on the two tiers that are ONE_SET.
    """
    assert spread.shown_matches_scored(
        "A coach checks how much more consistent the team is. Scores: 1, 2, "
        "3. What is the population standard deviation?",
        ["1, 2, 3"], (), spread.ONE_SET) is None


def test_a_comparative_in_the_context_does_not_excuse_a_two_set_ask():
    """The same scoping the other way round. A two-set reply whose context
    mentions the comparison, but whose question asks for one set's own value,
    is the wrong question however the context reads."""
    shown = ["1, 2, 3", "4, 5, 6"]
    labelled = ["Set A: 1, 2, 3", "Set B: 4, 5, 6"]
    assert spread.shown_matches_scored(
        f"{labelled[0]}. {labelled[1]}. The analyst asks how much larger "
        f"Set B is than Set A. What is the population standard deviation of "
        f"Set B?", shown, labelled, spread.TWO_SETS)


def test_naming_both_sets_is_not_asking_how_much():
    """"Which is larger" names both labels in the scored order and is not
    this question -- its answer is a label, and a number is scored."""
    shown = ["1, 2, 3", "4, 5, 6"]
    labelled = ["Set A: 1, 2, 3", "Set B: 4, 5, 6"]
    assert spread.shown_matches_scored(
        f"{labelled[0]}. {labelled[1]}. Which has the larger population "
        f"standard deviation, Set B or Set A?", shown, labelled,
        spread.TWO_SETS)


def test_a_lone_number_in_the_prose_is_not_a_data_set():
    """"each of 5 games" is wording, not data. A run needs a comma, so the
    strictness above does not refuse an ordinary sentence."""
    assert spread.shown_matches_scored(
        "In each of 5 games a team scored: 1, 2, 3. "
        "What is the population standard deviation?", ["1, 2, 3"]) is None


# --- spread, end to end through the retry loop ------------------------------

def _data_lines(prompt):
    """The DATA block the generator handed the model, one line per set."""
    lines = []
    for line in prompt.split("DATA:\n", 1)[1].splitlines():
        if not line.strip():
            break
        lines.append(line.strip())
    return lines


@pytest.fixture
def compliant_model(monkeypatch):
    """A model that does exactly what it is told: writes the data it was
    given, under the labels it was given, and says "population".

    Worth a double rather than a fixed string, because the datasets are drawn
    randomly -- so this exercises the generator over many real draws instead
    of the one dataset a hardcoded reply could pin.
    """
    monkeypatch.setattr(lesson_plan_context, "append_lesson_context",
                        lambda p, t, b: p)
    seen = {}

    def _generate(prompt, **_kw):
        lines = _data_lines(prompt)
        seen["data"] = lines
        body = " ".join(f"{line}." for line in lines)
        # The ask has to match the scenario too, not just the data -- which is
        # what `shown_matches_scored`'s scenario arm enforces.
        ask = ("How much larger is the population standard deviation of Set B "
               "than that of Set A?" if len(lines) == 2 else
               "What is the population standard deviation?")
        return json.dumps({
            "question_text": f"A study records some readings. {body} {ask}",
            "question_topic": "spread",
            "scenario": ("compare_spread" if len(lines) == 2
                         else "population_sd"),
        })

    monkeypatch.setattr(llm_client, "generate_text", _generate)
    return seen


def _sd_of(line):
    values = [int(v) for v in line.split(":")[-1].split(",")]
    return hs_solvers.population_sd(values)[0]


def test_a_spread_question_is_served_with_its_answer_among_options(
        compliant_model):
    question = spread.generate_spread_question([], [], "easy", "9th Grade")
    assert question["question_topic"] == "spread"
    assert question["correct_answer"] in question["answer_options"]
    assert len(set(question["answer_options"])) == 4
    assert question["correct_answer"] == str(
        _sd_of(compliant_model["data"][0]))


@pytest.mark.parametrize("run", range(25))
def test_the_hard_tier_answers_how_much_larger_and_never_how_much_smaller(
        compliant_model, run):
    """The two sets are drawn independently, so whichever happens to have the
    larger spread has to become Set B before the question is written -- the
    swap in `generate_spread_question`.

    Without it, "how much larger is Set B than Set A" is scored as a negative
    number whenever the draw came out the other way round. That is a wrong
    answer twice over: nothing on screen asks for a signed difference, and a
    lone negative among three non-negative distractors is pickable without
    doing any arithmetic.

    Repeated over draws rather than over branches, and the distinction
    matters: the second set is scaled by 2 or 3, so it is the wider one in
    98.5% of draws and this almost always exercises the *no swap* path.
    `test_the_wider_set_becomes_set_b_when_the_draw_comes_out_backwards`
    is what covers the swap itself -- measured, because repeating a test 25
    times reads like coverage of both branches and here it is not.
    """
    question = spread.generate_spread_question([], [], "hard", "9th Grade")
    set_a, set_b = compliant_model["data"]
    assert set_a.startswith("Set A:") and set_b.startswith("Set B:")

    answer = float(question["correct_answer"])
    assert answer > 0, f"scored {answer} for {set_a} / {set_b}"
    assert answer == abs(_sd_of(set_b) - _sd_of(set_a))
    assert _sd_of(set_b) > _sd_of(set_a), "the wider set must be Set B"
    assert all(float(o) >= 0 for o in question["answer_options"])


def test_the_wider_set_becomes_set_b_when_the_draw_comes_out_backwards(
        compliant_model, monkeypatch):
    """The swap, pinned rather than waited for.

    The second dataset is drawn at scale 2 or 3, so it is the wider one in
    all but ~1.5% of draws -- which means the branch that reorders them is
    the rare path, and a test relying on the draw to reach it would pass
    without ever running the line it is about. Both datasets are supplied
    here so it runs every time.
    """
    wide = [13, 19, 21, 27]                 # mean 20, sd 5
    narrow = [17, 19, 20, 21, 23]           # mean 20, sd 2
    drawn = iter((wide, narrow))
    monkeypatch.setattr(spread, "_choose_dataset",
                        lambda *_a, **_k: next(drawn))

    question = spread.generate_spread_question([], [], "hard", "9th Grade")
    set_a, set_b = compliant_model["data"]
    assert set_a == "Set A: 17, 19, 20, 21, 23", "the narrower set is A"
    assert set_b == "Set B: 13, 19, 21, 27"
    assert question["correct_answer"] == "3"


def test_a_text_that_swaps_the_two_sets_retries(monkeypatch):
    """The failure the labels exist to stop, driven end to end."""
    monkeypatch.setattr(lesson_plan_context, "append_lesson_context",
                        lambda p, t, b: p)

    def _swap(prompt, **_kw):
        first, second = _data_lines(prompt)
        return json.dumps({
            "question_text": f"Two machines. {second} {first} How much larger "
                             f"is the population standard deviation of Set B "
                             f"than that of Set A?",
            "question_topic": "spread",
            "scenario": "compare_spread",
        })

    monkeypatch.setattr(llm_client, "generate_text", _swap)
    with pytest.raises(ValueError):
        spread.generate_spread_question([], [], "hard", "9th Grade")


def test_a_two_set_text_asking_for_one_sets_own_spread_retries(monkeypatch):
    """The finding, driven end to end: correctly labelled, correctly ordered,
    wrong question."""
    monkeypatch.setattr(lesson_plan_context, "append_lesson_context",
                        lambda p, t, b: p)

    def _wrong_ask(prompt, **_kw):
        first, second = _data_lines(prompt)
        return json.dumps({
            "question_text": f"Two machines. {first} {second} What is the "
                             f"population standard deviation of Set B?",
            "question_topic": "spread",
            "scenario": "compare_spread",
        })

    monkeypatch.setattr(llm_client, "generate_text", _wrong_ask)
    with pytest.raises(ValueError):
        spread.generate_spread_question([], [], "hard", "9th Grade")


def test_a_text_that_adds_a_value_to_the_data_retries(monkeypatch):
    monkeypatch.setattr(lesson_plan_context, "append_lesson_context",
                        lambda p, t, b: p)
    monkeypatch.setattr(llm_client, "generate_text", lambda prompt, **_kw:
                        json.dumps({
                            "question_text":
                                f"Readings: {_data_lines(prompt)[0]}, 99. "
                                f"What is the population standard deviation?",
                            "question_topic": "spread",
                            "scenario": "population_sd"}))
    with pytest.raises(ValueError):
        spread.generate_spread_question([], [], "easy", "9th Grade")


def test_the_hard_tier_compares_two_sets():
    """S-ID.2's actual verb is comparing spread across data sets; one set is
    the ingredient. It is the hard tier because it is two full computations
    and a subtraction."""
    assert spread.DIFFICULTY_SCENARIOS["hard"] == spread.TWO_SETS
    assert spread.DIFFICULTY_SCENARIOS["easy"] == spread.ONE_SET


def test_spread_is_offered_only_from_grade_nine():
    for grade in ("5th Grade", "8th Grade"):
        assert "spread" not in decider._allowed_topics(grade)
    for grade in ("9th Grade", "12th Grade"):
        assert "spread" in decider._allowed_topics(grade)


def test_spread_is_absent_from_forbidden_bands():
    """Like `algebra`, `quadratics` and `functions`: nothing here uses
    variable notation, and the gate that keeps it from a 6-year-old is
    `TOPIC_MIN_GRADE`."""
    assert "spread" not in ga.FORBIDDEN_BANDS
