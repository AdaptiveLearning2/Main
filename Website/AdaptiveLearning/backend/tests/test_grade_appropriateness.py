"""Checks what a generated question actually contains, and names the four
ways a lesson-plan cell can fail to provide content.

A prompt instruction does not guarantee an 8B model follows it, so this
backstops it in code. The detector's value depends on a low false-positive
rate: rejecting a good question burns a retry and looks just like a model
that can't follow instructions. So the "must not flag" tests matter as much
as the "must flag" ones, and `6 x 4` is the case that must never be caught.
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
    # "x" as a multiplication sign, spaced and unspaced. Patterns are
    # anchored so a plain /x/ regex would not reject these.
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
    """algebra's early-band content is one-step equations with x, so a
    blanket rule would reject the topic's own questions. `_allowed_topics`
    is what keeps grades 1-5 away from algebra, not this check."""
    assert "algebra" not in ga.FORBIDDEN_BANDS
    for band in ("early", "middle", "upper", "advanced"):
        assert ga.find_violation("Solve for x: x + 2 = 5.", "algebra", band) is None


# --- early-band expressions: forbidden operators -------------------------
#
# Measured, not hypothetical: on llama3.1:8b, 2 of 8 generated grade-1
# questions used parentheses even though the prompt forbade them. The
# model followed the older-student examples in expr_prompt over the rule.

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
    """The operator rule only applies to the early band. Middle band and up
    must allow parentheses and multiplication, since `order_of_operations`
    needs them."""
    assert ga.find_violation("Solve (2+15)*8-(49-23)+19.",
                             "expressions", band, "hard") is None


# --- a grade string is read numerically, and an unreadable one is young --

import grade_levels  # noqa: E402
import LLM_topic_decider as td  # noqa: E402
import LLM_angle_relationship_generation as ang  # noqa: E402

# The angle solve path moved to `angle_solvers` so the bounded worker can
# import it without dragging in supabase, flask and dotenv. These tests follow
# it: they are about the arithmetic and the degenerate-figure rule, not about
# which module those live in.
import angle_solvers  # noqa: E402


@pytest.mark.parametrize("grade,number", [
    ("1st grade", 1), ("Grade 1", 1), ("grade 1", 1), ("1", 1),
    ("5th Grade", 5), ("Grade 6", 6), ("8th grade", 8),
    ("Highschool", 9), ("High School", 9), ("College", 13),
    ("Kindergarten", 0),
    # Unreadable, rather than guessed at.
    ("", None), (None, None), ("no idea", None),
    # A stray number that is not a school grade must not become one.
    ("2026 cohort", None),
])
def test_a_grade_is_read_numerically(grade, number):
    assert grade_levels.grade_number(grade) is number


@pytest.mark.parametrize("grade", ["Grade 1", "grade 1", "1", "", None, "no idea"])
def test_a_grade_the_dropdown_did_not_write_is_still_kept_from_algebra(grade):
    """`profiles.grade_level` is free text; only the dropdown writes "1st
    grade" form. "Grade 1" used to miss every `_allowed_topics` branch and
    fall through to the permissive default, making a 1st grader eligible
    for algebra. An unreadable grade is now treated as the youngest."""
    allowed = td._allowed_topics(grade)
    assert "algebra" not in allowed
    assert "probability" not in allowed
    # Compared against grade 1's own list rather than a copy of it: the claim
    # is that an unreadable grade is treated as the youngest, and spelling out
    # the membership made a literal that had to be edited every time grade 1
    # gained a topic. `geometry` is still absent from both, which is the part
    # this test is really about.
    assert set(allowed) == set(td._allowed_topics("1"))
    assert "geometry" not in allowed


@pytest.mark.parametrize("grade", ["Grade 1", "1", "", None, "no idea"])
def test_an_unreadable_grade_gets_early_content_not_advanced(grade):
    """Fixing only the topic gate was not enough: `_grade_band` also fell
    through to "advanced" for an unreadable grade, handing a 1st grader
    advanced-complexity text."""
    assert grade_levels.grade_band(grade) == "early"
    assert ang._grade_band(grade) == "early"


@pytest.mark.parametrize("grade,required", [
    ("1st grade", True), ("4th grade", True), ("5th grade", True),
    ("6th grade", False), ("7th grade", False), ("College", False),
    ("Highschool", False),
    # Read numerically, so the dropdown's exact wording is not load-bearing.
    ("Grade 1", True), ("grade 4", True), ("Grade 6", False), ("1", True),
    # An unreadable grade is treated as the youngest; it used to fall
    # through to "decimals allowed".
    ("", True), (None, True), ("no idea", True),
])
def test_whole_number_answers_are_required_only_through_5th_grade(grade, required):
    assert ang._requires_whole_number_solution(grade) is required


def test_the_cutoff_splits_the_middle_band_which_is_why_it_is_grade_keyed():
    """4th, 5th and 6th grade all fall in `middle`, but the whole-number
    rule splits them. No band boundary fits, so this is keyed on the raw
    grade instead, like `_allowed_topics`."""
    bands = {g: ang._grade_band(g) for g in ("4th grade", "5th grade", "6th grade")}
    assert set(bands.values()) == {"middle"}
    assert ang._requires_whole_number_solution("5th grade") is True
    assert ang._requires_whole_number_solution("6th grade") is False


def test_the_measured_decimal_case_is_the_one_that_gets_rejected():
    """(5x + 15) + (3x - 20) = 90 gives 11.875, a real observed answer.
    Whole numbers required through 5th grade, ordinary after."""
    solved, _ = angle_solvers.solve_scenario("algebra_complementary",
                                             ["5x + 15", "3x - 20"])
    assert not float(solved).is_integer()
    assert ang._requires_whole_number_solution("5th grade")
    assert not ang._requires_whole_number_solution("6th grade")


def test_a_whole_number_answer_passes_at_every_grade():
    solved, _ = angle_solvers.solve_scenario("complementary", ["35"])
    assert float(solved).is_integer()
    assert ang.format_answer(solved) == "55"


# --- degenerate angle configurations ------------------------------------

def _solved(scenario, variables):
    """The scenario solved *without* the validity check applied.

    `solve_scenario` folds `invalid_reason` in and returns None when a figure
    is degenerate, which is the right shape for its callers -- but these tests
    are about that check, so they need the arithmetic answer it rejects. The
    solvers are reached directly for that reason, not to dodge the worker: this
    is pure arithmetic over expressions that are already parsed.
    """
    parsed = angle_solvers.preprocess_variables(variables)
    raw = {
        "complementary": lambda v: angle_solvers.complementary_angle(v[0]),
        "supplementary": lambda v: angle_solvers.supplementary_angle(v[0]),
        "linear_pair": lambda v: angle_solvers.linear_pair(v[0]),
        "triangle_sum": lambda v: angle_solvers.triangle_missing_angle(v[0], v[1]),
        "algebra_complementary":
            lambda v: angle_solvers.solve_complementary(v[0], v[1]),
    }[scenario](parsed)
    return scenario, parsed, float(raw)


def test_the_measured_degenerate_triangle_is_refused():
    """A triangle with angles 75 and 105 was generated with a "third
    angle" of 0. The arithmetic is correct; there is no such triangle."""
    scenario_name, parsed, solution = _solved("triangle_sum", ["75", "105"])
    assert solution == 0
    assert angle_solvers.invalid_reason(scenario_name, parsed, solution) is not None


@pytest.mark.parametrize("scenario,variables", [
    ("triangle_sum",  ["90", "90"]),    # third angle is 0
    ("triangle_sum",  ["100", "120"]),  # third angle is negative
    ("complementary", ["90"]),          # the same failure, one scenario over
    ("complementary", ["120"]),
    ("supplementary", ["180"]),
    ("linear_pair",   ["180"]),
])
def test_degenerate_configurations_are_refused(scenario, variables):
    """Keyed on each scenario's angle total, not just triangles --
    `complementary` given 90 fails the same way."""
    scenario_name, parsed, solution = _solved(scenario, variables)
    assert angle_solvers.invalid_reason(scenario_name, parsed, solution) is not None


@pytest.mark.parametrize("scenario,variables", [
    ("triangle_sum",  ["75", "60"]),
    ("triangle_sum",  ["72", "93"]),
    ("complementary", ["35"]),
    ("supplementary", ["90"]),   # 90/90 is a legitimate supplementary pair
    ("linear_pair",   ["75"]),
])
def test_valid_configurations_are_not_refused(scenario, variables):
    scenario_name, parsed, solution = _solved(scenario, variables)
    assert angle_solvers.invalid_reason(scenario_name, parsed, solution) is None


def test_algebra_complementary_is_judged_on_its_angles_not_on_x():
    """`solution` there is x, which is unbounded -- 0 < x < 90 would be the
    wrong test. The two expressions evaluated at x are the angles."""
    scenario_name, parsed, solution = _solved("algebra_complementary", ["x + 20", "4x - 15"])
    assert angle_solvers.invalid_reason(scenario_name, parsed, solution) is None
    # x = 0 is ordinary; the angles it produces (100 and -10) are not.
    bad_scenario, bad_parsed, bad_solution = _solved("algebra_complementary", ["2x + 100", "-x - 10"])
    assert bad_solution == 0
    assert angle_solvers.invalid_reason(bad_scenario, bad_parsed, bad_solution) is not None


def test_an_unrecognised_scenario_is_retryable_rather_than_fatal():
    """None sends the loop round again; raising would take out a question
    that a regenerate would have fixed."""
    value, reason = angle_solvers.solve_scenario("nope", ["1"])
    assert value is None
    # The reason travels with it: the worker's only channel back is a string,
    # and a placeholder there made every failure look the same.
    assert "nope" in reason


def test_a_topic_with_no_rule_is_never_a_violation():
    assert ga.find_violation("Simplify 2x + 3x.", "not_a_real_topic", "early") is None


def test_empty_question_text_is_not_a_violation():
    """A missing question_text is a JSON problem caught elsewhere, not a
    grade violation -- reporting it here would point a reader the wrong
    way."""
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
    reason = lpc._lookup("ordering", "early")[1]

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
