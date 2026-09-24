"""High-school topics; their solvers are pure integer arithmetic and refuse rather than guess."""
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
    """Naming the root is what makes a two-root equation scoreable."""
    assert hs_solvers.solve_quadratic(a, b, c, target) == (expected, None)


@pytest.mark.parametrize("a,b,c,why", [
    (0, 3, 6, "not a quadratic at all"),
    (1, 0, 1, "no real roots"),
    (1, -2, 1, "a repeated root, so 'larger' names nothing"),
    (1, 0, -2, "irrational roots"),
    (2, -3, 1, "roots 1 and 1/2 are not whole numbers"),
])
def test_a_quadratic_this_topic_cannot_score_is_refused(a, b, c, why):
    value, reason = hs_solvers.solve_quadratic(a, b, c, "larger")
    assert value is None, why
    assert reason, "a refusal must say why; the string is the only channel"


def test_the_repeated_root_refusal_is_not_an_arithmetic_accident():
    """With both roots equal, "the larger solution" names nothing."""
    assert hs_solvers.solve_quadratic(1, -2, 1, "larger")[0] is None
    assert hs_solvers.solve_quadratic(1, -2, 1, "smaller")[0] is None


def test_an_unknown_target_is_refused_rather_than_defaulting():
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
    """The generator requires this string verbatim in the question text."""
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
    """Refused as a question one layer up, not here."""
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
    f, g = [1, 0, 0], [2, 1]            # f(x) = x^2, g(x) = 2x + 1
    assert hs_solvers.solve_composition(f, g, 3) == (49, None)
    assert hs_solvers.solve_composition(g, f, 3) == (19, None)


def test_a_value_too_large_to_ask_about_is_refused():
    """Composition squares its input, so reasonable coefficients can reach 10^16."""
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
    """Pin the randomly chosen equation and root."""
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
    reply({"question_text": "Solve x^2 - 7x + 12 = 0. What is the larger solution?",
           "question_topic": "quadratics"})
    with pytest.raises(ValueError):
        quad.generate_quadratics_question([], [], "easy", "9th Grade")


# --- the coefficients are chosen here, not asked for ----------------------

@pytest.mark.parametrize("difficulty", ["easy", "medium", "hard"])
@pytest.mark.parametrize("band", ["early", "middle", "upper", "advanced"])
def test_every_built_equation_is_solvable(difficulty, band):
    """Built from two distinct integer roots; many draws, since the choice is random."""
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
    """A leading coefficient above 1 turns factoring into the AC method."""
    seen = {quad._choose_coefficients(difficulty, "advanced")[0]
            for _ in range(200)}
    assert seen == expected_scales


def test_easy_keeps_both_roots_positive_and_medium_does_not():
    def roots(difficulty):
        a, b, c = quad._choose_coefficients(difficulty, "advanced")
        return {hs_solvers.solve_quadratic(a, b, c, t)[0]
                for t in ("larger", "smaller")}

    assert all(min(roots("easy")) > 0 for _ in range(50))
    assert any(min(roots("medium")) < 0 for _ in range(50))


@pytest.fixture
def fixed_functions(monkeypatch):
    """f(x) = 3x^2 - 2x + 1, g(x) = 2x + 1, x = 3: f(3) = 22, f(g(3)) = 134."""
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
    # g(f(3)) = 45: composing the other way round.
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
    """The inner function is degree 1 so a composition stays under `MAX_ABS_RESULT` by construction."""
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
    """Selecting a block is a prompt; only checking the reply enforces it."""
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
    # An ordinary word ending in "g" immediately before a bracket.
    ("Solving (x + 1) first, if f(x) = x^2, what is f(4)?", False),
    ("Using (the) rule f(x) = 2x, what is f(3)?", False),
])
def test_a_second_function_is_recognised_without_catching_ordinary_words(
        text, flagged):
    assert funcs._mentions_second_function(text) is flagged


def test_an_evaluate_question_naming_a_second_function_retries(
        reply, fixed_functions):
    """`shown_matches_scored` checks scored functions appear, never that nothing else does."""
    # Differs from the accepted text below only by the g clause, so only the guard can reject it.
    reply({"question_text":
           "If f(x) = 3x^2 - 2x + 1 and g(x) = 2x - 3, what is f(3)?",
           "question_topic": "functions",
           "scenario": "evaluate"})
    with pytest.raises(ValueError):
        funcs.generate_functions_question([], [], "easy", "9th Grade")


def test_the_same_text_without_the_second_function_is_accepted(
        reply, fixed_functions):
    """Positive control for the test above."""
    reply({"question_text": "If f(x) = 3x^2 - 2x + 1, what is f(3)?",
           "question_topic": "functions",
           "scenario": "evaluate"})
    question = funcs.generate_functions_question([], [], "easy", "9th Grade")
    assert question["correct_answer"] == "22"


def test_compose_is_still_allowed_to_name_g(reply, fixed_functions):
    reply({"question_text":
           "If f(x) = 3x^2 - 2x + 1 and g(x) = 2x + 1, what is f(g(3))?",
           "question_topic": "functions",
           "scenario": "compose"})
    question = funcs.generate_functions_question([], [], "medium", "9th Grade")
    assert question["correct_answer"] == "134"


@pytest.mark.parametrize("band", ["early", "middle", "upper", "advanced"])
@pytest.mark.parametrize("scenario", ["evaluate", "compose"])
def test_the_identity_is_never_built(band, scenario):
    """`f(x) = x` asks nothing, and on compose it erases the swapped-order distractor."""
    for _ in range(2000):
        f, g, _x = funcs._choose_functions(scenario, band)
        for name, poly in (("f", f), ("g", g)):
            if poly is None:
                continue
            assert not (len(poly) == 2 and abs(poly[0]) == 1
                        and poly[1] == 0), f"{name} = {poly} is the identity"


def test_a_constant_function_is_never_built():
    """"If f(x) = 7, what is f(4)" asks nothing about function notation."""
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
    allowed = set(decider._allowed_topics("9th Grade"))
    assert {"quadratics", "functions"} <= allowed


def test_an_unreadable_grade_does_not_reach_the_high_school_topics():
    """`grade_levels` treats an unreadable grade as the youngest."""
    allowed = decider._allowed_topics("not a grade at all")
    assert "quadratics" not in allowed
    assert "functions" not in allowed


@pytest.mark.parametrize("topic", ["quadratics", "functions"])
def test_the_high_school_topics_are_absent_from_forbidden_bands(topic):
    """Like `algebra`: `TOPIC_MIN_GRADE`, not a band rule, keeps them from young grades."""
    assert topic not in ga.FORBIDDEN_BANDS


# --- schemas --------------------------------------------------------------

@pytest.mark.parametrize("scenario", ["evaluate", "compose"])
def test_the_functions_schema_asks_for_the_sentence_and_the_scenario(scenario):
    """Coefficients are handed over; `scenario` stays as a cross-check on the block read."""
    assert set(question_schemas.functions(scenario)["properties"]) == {
        "question_text", "question_topic", "scenario"}


def test_the_quadratics_schema_asks_for_nothing_but_the_sentence():
    """Equation and root are decided before the call, so neither is the model's to return."""
    assert set(question_schemas.quadratics()["properties"]) == {
        "question_text", "question_topic"}


# --- spread (S-ID.2) ------------------------------------------------------

def test_every_pattern_has_an_exact_standard_deviation():
    """A rounded irrational sd marks correct answers wrong; the hardcoded patterns avoid it."""
    for pattern in spread._DEVIATION_PATTERNS:
        assert sum(pattern) == 0, f"{pattern} does not centre on its mean"
        sd, reason = hs_solvers.population_sd([100 + d for d in pattern])
        assert sd is not None, f"{pattern}: {reason}"
        assert sd > 0
        # Scaling keeps it exact.
        scaled, _r = hs_solvers.population_sd([100 + 3 * d for d in pattern])
        assert scaled == sd * 3


@pytest.mark.parametrize("band", ["early", "middle", "upper", "advanced"])
def test_every_built_dataset_is_exactly_scoreable(band):
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
    """Sample sd (n-1) gives a different number, so the wording must say which."""
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
    """Forgetting the square root is the commonest error."""
    values = [7, 9, 10, 11, 13]                     # sd 2, variance 4
    wrong = spread.generate_incorrect_answers(
        2, [hs_solvers.population_variance(values)])
    assert 4 in wrong


def test_no_distractor_is_a_negative_spread():
    for solution in (1, 2, 3):
        for candidate in spread.generate_incorrect_answers(solution, [None]):
            assert float(candidate) >= 0, candidate


def test_containment_alone_would_accept_a_longer_data_set():
    """The runs have to match exactly, not merely appear."""
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
    """Same sets, same order, labels swapped: the scored answer is negated."""
    swapped = "Set B: 1, 2, 3. Set A: 4, 5, 6. Population standard deviation?"
    assert spread.shown_matches_scored(swapped, ["1, 2, 3", "4, 5, 6"]) is None, \
        "the runs alone are in the right order, which is the trap"
    assert spread.shown_matches_scored(
        swapped, ["1, 2, 3", "4, 5, 6"],
        ["Set A: 1, 2, 3", "Set B: 4, 5, 6"])


def test_two_correctly_labelled_sets_can_still_carry_the_wrong_question():
    """Binding the data is not binding the ask: here 5 is shown as an option and 3 is scored."""
    shown = ["17, 19, 20, 21, 23", "13, 19, 21, 27"]
    labelled = [f"Set A: {shown[0]}", f"Set B: {shown[1]}"]
    asks_one = (f"Two machines. {labelled[0]}. {labelled[1]}. What is the "
                f"population standard deviation of Set B?")
    assert spread.shown_matches_scored(asks_one, shown, labelled) is None, \
        "the data checks alone accept it, which is the point"
    assert spread.shown_matches_scored(asks_one, shown, labelled,
                                       spread.TWO_SETS)


def test_the_comparison_must_run_in_the_direction_it_is_scored():
    shown = ["1, 2, 3", "4, 5, 6"]
    labelled = ["Set A: 1, 2, 3", "Set B: 4, 5, 6"]
    body = f"{labelled[0]}. {labelled[1]}."
    assert spread.shown_matches_scored(
        f"{body} How much larger is the population standard deviation of "
        f"Set B than that of Set A?", shown, labelled, spread.TWO_SETS) is None
    assert spread.shown_matches_scored(
        f"{body} How much larger is the population standard deviation of "
        f"Set A than that of Set B?", shown, labelled, spread.TWO_SETS)
    # Needs both labels pinned: a set compared with itself.
    assert spread.shown_matches_scored(
        f"{body} How much larger is the population standard deviation of "
        f"Set A than that of Set A?", shown, labelled, spread.TWO_SETS)


@pytest.mark.parametrize("ask", [
    "How much larger is the population standard deviation of Set B than that of Set A?",
    "By how much larger is the population standard deviation of Set B than that of Set A?",
    "How much greater is Set B's population standard deviation than Set A's?",
    "How much bigger is the population standard deviation of Set B compared to Set A?",
    # Comparative after the label: an ordered regex refuses these.
    "By how much does the population standard deviation of Set B exceed that of Set A?",
    "Set B's population standard deviation is how much larger than Set A's?",
])
def test_the_phrasings_a_model_actually_writes_are_accepted(ask):
    """A refusal costs a retry; the first two are verbatim model replies."""
    shown = ["1, 2, 3", "4, 5, 6"]
    labelled = ["Set A: 1, 2, 3", "Set B: 4, 5, 6"]
    assert spread.shown_matches_scored(
        f"Set A: 1, 2, 3. Set B: 4, 5, 6. {ask}", shown, labelled,
        spread.TWO_SETS) is None, ask


def test_a_one_set_question_may_not_ask_a_comparison():
    """With one set on screen, a comparison is against an invented number the data check cannot see."""
    assert spread.shown_matches_scored(
        "Readings: 1, 2, 3. How much larger is the population standard "
        "deviation than 10?", ["1, 2, 3"], (), spread.ONE_SET)


def test_a_comparative_in_the_context_does_not_refuse_a_one_set_question():
    """The guard belongs to the ask, not to the sentence before it."""
    assert spread.shown_matches_scored(
        "A coach checks how much more consistent the team is. Scores: 1, 2, "
        "3. What is the population standard deviation?",
        ["1, 2, 3"], (), spread.ONE_SET) is None


def test_a_comparative_in_the_context_does_not_excuse_a_two_set_ask():
    shown = ["1, 2, 3", "4, 5, 6"]
    labelled = ["Set A: 1, 2, 3", "Set B: 4, 5, 6"]
    assert spread.shown_matches_scored(
        f"{labelled[0]}. {labelled[1]}. The analyst asks how much larger "
        f"Set B is than Set A. What is the population standard deviation of "
        f"Set B?", shown, labelled, spread.TWO_SETS)


@pytest.mark.parametrize("joiner", [" and ", ", "])
def test_the_data_may_share_a_sentence_with_the_question(joiner):
    """Direction is read from the first label the question names; the data carries labels too."""
    shown = ["1, 2, 3", "4, 5, 6"]
    labelled = ["Set A: 1, 2, 3", "Set B: 4, 5, 6"]
    text = (f"An inspector compares two machines, {labelled[0]}{joiner}"
            f"{labelled[1]} -- how much larger is the population standard "
            f"deviation of Set B than that of Set A?")
    assert spread.shown_matches_scored(
        text, shown, labelled, spread.TWO_SETS) is None


def test_removing_the_data_does_not_excuse_a_reversed_question():
    """The strip must not cost the property it exists to preserve."""
    shown = ["1, 2, 3", "4, 5, 6"]
    labelled = ["Set A: 1, 2, 3", "Set B: 4, 5, 6"]
    assert spread.shown_matches_scored(
        f"An inspector compares {labelled[0]} and {labelled[1]} -- how much "
        f"larger is the population standard deviation of Set A than that of "
        f"Set B?", shown, labelled, spread.TWO_SETS)


def test_an_ask_phrased_as_an_instruction_is_still_the_ask():
    """`_ASKED` matches only clauses ending in "?"; both scenario arms must handle an imperative."""
    assert spread.shown_matches_scored(
        "Machine readings: 17, 19, 20, 21, 23. Calculate how much larger the "
        "population standard deviation is than 10.",
        ["17, 19, 20, 21, 23"], (), spread.ONE_SET)

    shown = ["1, 2, 3", "4, 5, 6"]
    labelled = ["Set A: 1, 2, 3", "Set B: 4, 5, 6"]
    assert spread.shown_matches_scored(
        f"{labelled[0]}. {labelled[1]}. Calculate how much larger the "
        f"population standard deviation of Set B is than that of Set A.",
        shown, labelled, spread.TWO_SETS) is None


def test_the_fallback_is_the_closing_sentence_and_not_the_whole_text():
    """A whole-text fallback would let a comparative in the context refuse the question."""
    assert spread.shown_matches_scored(
        "A coach checks how much more consistent the team is. Scores: 1, 2, "
        "3. Give the population standard deviation.",
        ["1, 2, 3"], (), spread.ONE_SET) is None


def test_an_instruction_is_read_the_same_way_a_question_would_be():
    for tail in (".", "?"):
        assert spread.shown_matches_scored(
            f"Readings: 1, 2, 3. Say how much larger the population standard "
            f"deviation is than 10{tail}", ["1, 2, 3"], (), spread.ONE_SET)
        assert spread.shown_matches_scored(
            f"Readings: 1, 2, 3. Give the population standard deviation{tail}",
            ["1, 2, 3"], (), spread.ONE_SET) is None


def test_naming_both_sets_is_not_asking_how_much():
    """"Which is larger" is answered by a label, and a number is scored."""
    shown = ["1, 2, 3", "4, 5, 6"]
    labelled = ["Set A: 1, 2, 3", "Set B: 4, 5, 6"]
    assert spread.shown_matches_scored(
        f"{labelled[0]}. {labelled[1]}. Which has the larger population "
        f"standard deviation, Set B or Set A?", shown, labelled,
        spread.TWO_SETS)


def test_a_lone_number_in_the_prose_is_not_a_data_set():
    """A run needs a comma, so "each of 5 games" is wording, not data."""
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
    """A model that writes the data and labels it was given; the datasets are drawn randomly."""
    monkeypatch.setattr(lesson_plan_context, "append_lesson_context",
                        lambda p, t, b: p)
    seen = {}

    def _generate(prompt, **_kw):
        lines = _data_lines(prompt)
        seen["data"] = lines
        body = " ".join(f"{line}." for line in lines)
        # The ask must match the scenario, not just the data.
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
    """Almost always the no-swap path; the swap itself is pinned by the next test."""
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
    """The swap is the rare path in random draws, so both datasets are supplied."""
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
    """Correctly labelled, correctly ordered, wrong question."""
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
    """S-ID.2's verb is comparing spread across data sets."""
    assert spread.DIFFICULTY_SCENARIOS["hard"] == spread.TWO_SETS
    assert spread.DIFFICULTY_SCENARIOS["easy"] == spread.ONE_SET


def test_spread_is_offered_only_from_grade_nine():
    for grade in ("5th Grade", "8th Grade"):
        assert "spread" not in decider._allowed_topics(grade)
    for grade in ("9th Grade", "12th Grade"):
        assert "spread" in decider._allowed_topics(grade)


def test_spread_is_absent_from_forbidden_bands():
    """`TOPIC_MIN_GRADE`, not a band rule, keeps it from young grades."""
    assert "spread" not in ga.FORBIDDEN_BANDS
