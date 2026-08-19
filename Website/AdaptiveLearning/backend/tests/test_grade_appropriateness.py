"""The backstop on what a generated question contains, and the four ways a
lesson-plan cell can decline to contribute anything.

Both halves exist because a prompt instruction is not an enforcement
mechanism against an 8B model -- the same reason `_safe_topic` and
`_pick_scenario` exist. What is asserted here is narrow on purpose: the
detector's value is entirely in its false-positive rate, since a check that
rejects good questions burns retries and is indistinguishable from a model
that cannot follow instructions. So the "must not flag" half of this file is
the load-bearing half, and `6 x 4` is the case it exists for.
"""

import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import grade_appropriateness as ga  # noqa: E402
import lesson_plan_context as lpc  # noqa: E402


# --- the detector: what it must catch ------------------------------------

@pytest.mark.parametrize("text,topic,band", [
    # The two documented leaks this was written for.
    ("Simplify 2x + 3x.", "expressions", "early"),
    ("Simplify 2x + 3x.", "expressions", "middle"),
    ("Two angles are complementary: (x + 10) and (2x - 20). Find x.",
     "angle_relationships", "middle"),
    # Variable notation reaching a topic that never legitimately uses it.
    ("If x = 5, order these values.", "ordering", "early"),
    ("Solve for n: the mean is 12.", "mean", "middle"),
])
def test_variable_notation_is_refused_where_the_band_forbids_it(text, topic, band):
    assert ga.find_violation(text, topic, band) is not None


# --- the detector: what it must NOT catch --------------------------------

@pytest.mark.parametrize("text,topic,band", [
    # "x" as a multiplication sign, spaced and unspaced. This is the reason
    # the patterns are anchored the way they are: a naive /x/ would reject
    # every early-band multiplication question ever generated.
    ("What is 6 x 4?", "expressions", "early"),
    ("What is 6x4?", "expressions", "early"),
    # Ordinary arithmetic that happens to contain the letters x, y or n.
    ("Find the next number: 2, 4, 6, ?", "ordering", "early"),
    ("There are 5 boxes and 3 more. How many?", "expressions", "early"),
    ("A rectangle is 3 yards by 4 yards. What is its area?", "geometry", "early"),
    ("Evaluate (4+6)*3-5.", "expressions", "middle"),
    # Bands that are allowed the notation.
    ("Simplify 2x + 3x.", "expressions", "upper"),
    ("A right triangle has legs a and b. Find c.", "geometry", "upper"),
])
def test_ordinary_questions_are_not_refused(text, topic, band):
    assert ga.find_violation(text, topic, band) is None


def test_algebra_is_exempt_at_every_band():
    """algebra's own early-band content is one-step equations with x, framed
    as a missing-number fact. A blanket rule would reject every question the
    topic exists to ask, so the topic is absent from FORBIDDEN_BANDS -- and
    the gate that protects grades 1-5 from algebra is `_allowed_topics`,
    which stops the topic being chosen at all."""
    assert "algebra" not in ga.FORBIDDEN_BANDS
    for band in ("early", "middle", "upper", "advanced"):
        assert ga.find_violation("Solve for x: x + 2 = 5.", "algebra", band) is None


# --- early-band expressions: forbidden operators -------------------------
#
# Measured, not hypothetical. Against the seeded lesson plans on
# llama3.1:8b (2026-08-18, grade 1 / easy), 2 of 8 generated questions came
# back with parentheses -- "Solve 5 + (2 - 1)." -- and a separate run gave
# "Evaluate (3+2)*4-1.", both while the prompt in the same call forbade
# them. The scenario examples in expr_prompt are written for older students
# and the model followed their shape over the constraint.

@pytest.mark.parametrize("text,difficulty", [
    ("Solve 5 + (2 - 1).",   "easy"),    # the real observed failure
    ("Evaluate (3+2)*4-1.",  "easy"),    # the other real observed failure
    ("What is 8 / 2 + 1?",   "easy"),    # division, never allowed early
    ("What is 8 / 2 + 1?",   "hard"),    # not even at hard
    ("What is 3 * 4 + 1?",   "easy"),    # multiplication above easy's rule
    ("What is 3 * 4 + 1?",   "medium"),
    ("What is 3 * 4 + 1?",   None),      # absent difficulty reads as strict
])
def test_forbidden_operators_are_refused_for_early_expressions(text, difficulty):
    assert ga.find_violation(text, "expressions", "early", difficulty) is not None


@pytest.mark.parametrize("text,difficulty", [
    ("What is 7 + 8 - 4?",  "easy"),
    ("What is 9 minus 3 plus 2?", "easy"),
    ("Solve 14 + 9 - 3.",   "medium"),
    # early/hard admits multiplication facts, so this one is legitimate.
    ("What is 3 * 4 + 1?",  "hard"),
])
def test_permitted_early_expressions_are_not_refused(text, difficulty):
    assert ga.find_violation(text, "expressions", "early", difficulty) is None


@pytest.mark.parametrize("band", ["middle", "upper", "advanced"])
def test_older_bands_keep_their_operators(band):
    """The operator rule is early-band only. Parentheses and multiplication
    are the whole point of `order_of_operations`, which middle band upward
    is meant to see -- a rule that leaked upward would reject every
    question that scenario exists to ask."""
    assert ga.find_violation("Solve (2+15)*8-(49-23)+19.",
                             "expressions", band, "hard") is None


# --- angle answers: whole numbers through 5th grade ----------------------

import LLM_angle_relationship_generation as ang  # noqa: E402


@pytest.mark.parametrize("grade,required", [
    ("1st grade", True), ("4th grade", True), ("5th grade", True),
    ("6th grade", False), ("7th grade", False), ("College", False),
    ("Highschool", False), ("", False), (None, False),
])
def test_whole_number_answers_are_required_only_through_5th_grade(grade, required):
    assert ang._requires_whole_number_solution(grade) is required


def test_the_cutoff_splits_the_middle_band_which_is_why_it_is_grade_keyed():
    """4th, 5th and 6th all sit in `middle`, and the rule divides them. No
    band boundary is in the right place, so keying this on _grade_band()
    would either impose whole numbers on a 6th grader or allow decimals for
    a 4th -- the same reason _allowed_topics is grade-keyed."""
    bands = {g: ang._grade_band(g) for g in ("4th grade", "5th grade", "6th grade")}
    assert set(bands.values()) == {"middle"}
    assert ang._requires_whole_number_solution("5th grade") is True
    assert ang._requires_whole_number_solution("6th grade") is False


def test_the_measured_decimal_case_is_the_one_that_gets_rejected():
    """(5x + 15) + (3x - 20) = 90 gives 11.875 -- the real observed answer,
    reported as 11.88. Whole through 5th grade, ordinary from 6th."""
    solved = ang.normalize_solution(ang._solve_scenario(
        {"scenario": "algebra_complementary", "variables": ["5x + 15", "3x - 20"]}))
    assert not float(solved).is_integer()
    assert ang._requires_whole_number_solution("5th grade")
    assert not ang._requires_whole_number_solution("6th grade")


def test_a_whole_number_answer_passes_at_every_grade():
    solved = ang.normalize_solution(ang._solve_scenario(
        {"scenario": "complementary", "variables": ["35"]}))
    assert float(solved).is_integer()
    assert ang.format_answer(solved) == "55"


# --- degenerate angle configurations ------------------------------------

def _solved(scenario, variables):
    q = {"scenario": scenario, "variables": variables}
    return q, ang.normalize_solution(ang._solve_scenario(q))


def test_the_measured_degenerate_triangle_is_refused():
    """"A triangle has angles 75 and 105. What is the third angle?" was
    generated with the answer 0 (2026-08-18). The arithmetic is right and
    there is no such triangle."""
    q, solution = _solved("triangle_sum", ["75", "105"])
    assert solution == 0
    assert ang._invalid_angle_reason(q, solution) is not None


@pytest.mark.parametrize("scenario,variables", [
    ("triangle_sum",  ["90", "90"]),    # third angle is 0
    ("triangle_sum",  ["100", "120"]),  # third angle is negative
    ("complementary", ["90"]),          # the same failure, one scenario over
    ("complementary", ["120"]),
    ("supplementary", ["180"]),
    ("linear_pair",   ["180"]),
])
def test_degenerate_configurations_are_refused(scenario, variables):
    """Keyed on each scenario's total rather than written for triangles
    alone -- `complementary` with a given angle of 90 fails identically."""
    q, solution = _solved(scenario, variables)
    assert ang._invalid_angle_reason(q, solution) is not None


@pytest.mark.parametrize("scenario,variables", [
    ("triangle_sum",  ["75", "60"]),
    ("triangle_sum",  ["72", "93"]),
    ("complementary", ["35"]),
    ("supplementary", ["90"]),   # 90/90 is a legitimate supplementary pair
    ("linear_pair",   ["75"]),
])
def test_valid_configurations_are_not_refused(scenario, variables):
    q, solution = _solved(scenario, variables)
    assert ang._invalid_angle_reason(q, solution) is None


def test_algebra_complementary_is_judged_on_its_angles_not_on_x():
    """`solution` there is x, which is unbounded -- 0 < x < 90 would be the
    wrong test. The two expressions evaluated at x are the angles."""
    q, solution = _solved("algebra_complementary", ["x + 20", "4x - 15"])
    assert ang._invalid_angle_reason(q, solution) is None
    # x = 0 is a perfectly ordinary value; the angles it produces (100 and
    # -10) are not, and that is what has to be caught.
    bad, bad_solution = _solved("algebra_complementary", ["2x + 100", "-x - 10"])
    assert bad_solution == 0
    assert ang._invalid_angle_reason(bad, bad_solution) is not None


def test_an_unrecognised_scenario_is_retryable_rather_than_fatal():
    """None sends the loop round again; raising would take out a question
    that a regenerate would have fixed."""
    assert ang._solve_scenario({"scenario": "nope", "variables": ["1"]}) is None


def test_a_topic_with_no_rule_is_never_a_violation():
    assert ga.find_violation("Simplify 2x + 3x.", "not_a_real_topic", "early") is None


def test_empty_question_text_is_not_a_violation():
    """A missing question_text is a JSON problem, caught by the key
    validation above this check -- not something to report as a grade
    violation, which would send a reader looking in the wrong place."""
    for empty in (None, ""):
        assert ga.find_violation(empty, "expressions", "early") is None


# --- the four ways a cell contributes nothing ----------------------------

class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, data, raises):
        self._data, self._raises = data, raises

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        if self._raises:
            raise RuntimeError("connection refused")
        return _FakeResponse(self._data)


class _FakeClient:
    def __init__(self, data, raises=False):
        self._data, self._raises = data, raises

    def table(self, *a, **k):
        return _FakeQuery(self._data, self._raises)


@pytest.fixture(autouse=True)
def _clear_lesson_plan_cache():
    """The module caches for 30s, so a test reading a different fake client
    would otherwise get the previous test's answer."""
    lpc._cache.clear()
    yield
    lpc._cache.clear()


@pytest.mark.parametrize("client,expected_reason,expects_text", [
    (_FakeClient([{"objectives": "Compare whole numbers.", "notes": None}]),
     lpc.FOUND, True),
    (_FakeClient([]),                                     lpc.NO_ROW, False),
    (_FakeClient([{"objectives": "", "notes": None}]),    lpc.BLANK_ROW, False),
    (_FakeClient(None, raises=True),                      lpc.READ_FAILED, False),
    (None,                                                lpc.NO_CREDENTIALS, False),
])
def test_every_way_of_contributing_nothing_is_named_separately(
        monkeypatch, client, expected_reason, expects_text):
    """An unseeded cell, a half-finished edit, an outage and a misconfigured
    process all degrade to the same prompt -- and all used to be the same
    silent None. They are operationally different problems, so they get
    different names."""
    monkeypatch.setattr(lpc, "_get_client", lambda: client)

    text = lpc.get_lesson_context("ordering", "early")
    lpc._cache.clear()
    reason = lpc.lookup_reason("ordering", "early")

    assert reason == expected_reason
    assert (text is not None) is expects_text


@pytest.mark.parametrize("client", [
    _FakeClient(None, raises=True),
    None,
])
def test_a_transient_failure_is_not_cached(monkeypatch, client):
    """Caching an outage would keep answering None for the TTL after the
    database came back, and caching absent credentials would outlive
    credentials that are loaded later in the process's life."""
    monkeypatch.setattr(lpc, "_get_client", lambda: client)
    lpc.get_lesson_context("ordering", "early")
    assert ("ordering", "early") not in lpc._cache


def test_lesson_plan_text_is_clamped(monkeypatch):
    """Dashboard-authored text still reaches a prompt, so it is bounded like
    every other prompt input here."""
    monkeypatch.setattr(lpc, "_get_client",
                        lambda: _FakeClient([{"objectives": "x" * 9000, "notes": None}]))
    assert len(lpc.get_lesson_context("ordering", "early")) == lpc._MAX_CONTEXT_CHARS


def test_a_failed_lookup_leaves_the_prompt_untouched(monkeypatch):
    """The whole module fails open: grounding is an enrichment, never a gate,
    so nothing it does may block a question from being generated."""
    monkeypatch.setattr(lpc, "_get_client", lambda: _FakeClient([]))
    prompt = "original prompt"
    assert lpc.append_lesson_context(prompt, "ordering", "early") == prompt
