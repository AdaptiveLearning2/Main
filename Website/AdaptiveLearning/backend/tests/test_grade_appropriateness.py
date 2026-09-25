"""Grade-appropriateness detector (false positives cost a retry) and lesson-plan cell failure reasons."""

import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import grade_appropriateness as ga  # noqa: E402
import lesson_plan_context as lpc  # noqa: E402


# --- the detector: what it must catch ------------------------------------

@pytest.mark.parametrize("text,topic,band", [
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
    # "x" as a multiplication sign, spaced and unspaced.
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
    """Algebra's own questions use x; `_allowed_topics` keeps young grades away from it."""
    assert "algebra" not in ga.FORBIDDEN_BANDS
    for band in ("early", "middle", "upper", "advanced"):
        assert ga.find_violation("Solve for x: x + 2 = 5.", "algebra", band) is None


def test_the_band_table_names_exactly_the_topics_that_call_the_check():
    """An entry no generator reaches reads as a check that is wired and is not.

    A topic passed as a variable is read from that module's `TOPICS` literal.
    """
    import ast
    import pathlib
    called = set()
    for path in pathlib.Path(ga.__file__).parent.glob("LLM_*_generation.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        topics = {n.targets[0].id: n.value for n in tree.body if isinstance(n, ast.Assign)
                  and isinstance(n.targets[0], ast.Name)}.get("TOPICS")
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "refuse" and len(node.args) > 1):
                if isinstance(node.args[1], ast.Constant):
                    called.add(node.args[1].value)
                else:
                    assert topics is not None, f"{path.name} passes a topic with no TOPICS"
                    called.update(ast.literal_eval(topics))
    assert called == set(ga.FORBIDDEN_BANDS)


# --- early-band expressions: forbidden operators -------------------------

@pytest.mark.parametrize("text,difficulty", [
    ("Solve 5 + (2 - 1).",   "easy"),    # observed failure
    ("Evaluate (3+2)*4-1.",  "easy"),    # observed failure
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
    # early/hard admits multiplication facts.
    ("What is 3 * 4 + 1?",  "hard"),
])
def test_permitted_early_expressions_are_not_refused(text, difficulty):
    assert ga.find_violation(text, "expressions", "early", difficulty) is None


@pytest.mark.parametrize("band", ["middle", "upper", "advanced"])
def test_older_bands_keep_their_operators(band):
    """`order_of_operations` needs parentheses and multiplication from middle band up."""
    assert ga.find_violation("Solve (2+15)*8-(49-23)+19.",
                             "expressions", band, "hard") is None


# --- a grade string is read numerically, and an unreadable one is young --

import grade_levels  # noqa: E402
import LLM_topic_decider as td  # noqa: E402
import LLM_angle_relationship_generation as ang  # noqa: E402

# The angle solve path lives here so the bounded worker can import it without supabase.
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
    """`profiles.grade_level` is free text; an unreadable grade is treated as the youngest."""
    allowed = td._allowed_topics(grade)
    assert "algebra" not in allowed
    assert "probability" not in allowed
    # Compared against grade 1's own list, not a literal copy of it.
    assert set(allowed) == set(td._allowed_topics("1"))
    assert "geometry" not in allowed


@pytest.mark.parametrize("grade", ["Grade 1", "1", "", None, "no idea"])
def test_an_unreadable_grade_gets_early_content_not_advanced(grade):
    assert grade_levels.grade_band(grade) == "early"
    assert ang._grade_band(grade) == "early"


@pytest.mark.parametrize("grade,required", [
    ("1st grade", True), ("4th grade", True), ("5th grade", True),
    ("6th grade", False), ("7th grade", False), ("College", False),
    ("Highschool", False),
    # Read numerically, so the dropdown's exact wording is not load-bearing.
    ("Grade 1", True), ("grade 4", True), ("Grade 6", False), ("1", True),
    # An unreadable grade is treated as the youngest.
    ("", True), (None, True), ("no idea", True),
])
def test_whole_number_answers_are_required_only_through_5th_grade(grade, required):
    assert ang._requires_whole_number_solution(grade) is required


def test_the_cutoff_splits_the_middle_band_which_is_why_it_is_grade_keyed():
    bands = {g: ang._grade_band(g) for g in ("4th grade", "5th grade", "6th grade")}
    assert set(bands.values()) == {"middle"}
    assert ang._requires_whole_number_solution("5th grade") is True
    assert ang._requires_whole_number_solution("6th grade") is False


def test_the_measured_decimal_case_is_the_one_that_gets_rejected():
    """(5x + 15) + (3x - 20) = 90 gives 11.875, an observed answer."""
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
    """The scenario solved *without* the `invalid_reason` check that `solve_scenario` folds in."""
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
    """Angles 75 and 105 leave a "third angle" of 0: correct arithmetic, no triangle."""
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
    """`solution` there is x, which is unbounded; the angles are the expressions at x."""
    scenario_name, parsed, solution = _solved("algebra_complementary", ["x + 20", "4x - 15"])
    assert angle_solvers.invalid_reason(scenario_name, parsed, solution) is None
    # x = 0 is ordinary; the angles it produces (100 and -10) are not.
    bad_scenario, bad_parsed, bad_solution = _solved("algebra_complementary", ["2x + 100", "-x - 10"])
    assert bad_solution == 0
    assert angle_solvers.invalid_reason(bad_scenario, bad_parsed, bad_solution) is not None


def test_an_unrecognised_scenario_is_retryable_rather_than_fatal():
    """None sends the loop round again; raising would not."""
    value, reason = angle_solvers.solve_scenario("nope", ["1"])
    assert value is None
    # The worker's only channel back is this string.
    assert "nope" in reason


def test_a_topic_with_no_rule_is_never_a_violation():
    assert ga.find_violation("Simplify 2x + 3x.", "not_a_real_topic", "early") is None


def test_empty_question_text_is_not_a_violation():
    """Missing question_text is a JSON problem caught elsewhere."""
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
    """The module caches for 30s."""
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
    monkeypatch.setattr(lpc, "_get_client", lambda: client)
    lpc.get_lesson_context("ordering", "early")
    assert ("ordering", "early") not in lpc._cache


def test_lesson_plan_text_is_clamped(monkeypatch):
    monkeypatch.setattr(lpc, "_get_client",
                        lambda: _FakeClient([{"objectives": "x" * 9000, "notes": None}]))
    assert len(lpc.get_lesson_context("ordering", "early")) == lpc._MAX_CONTEXT_CHARS


def test_a_failed_lookup_leaves_the_prompt_untouched(monkeypatch):
    """Grounding is an enrichment, never a gate."""
    monkeypatch.setattr(lpc, "_get_client", lambda: _FakeClient([]))
    prompt = "original prompt"
    assert lpc.append_lesson_context(prompt, "ordering", "early") == prompt
