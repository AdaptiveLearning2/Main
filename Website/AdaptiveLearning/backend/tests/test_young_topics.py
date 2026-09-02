"""`missing_number` and `patterns` -- the two topics grade 1 gained.

Grade 1 could be served exactly two topics, `ordering` and `expressions`, so a
6-year-old saw the same two on rotation. Both of these are real grade-1
standards (1.OA.8, 1.NBT.1) whose answer is a single whole number an exact
solver can produce, which is the constraint that rules out most of 1.G and
1.OA -- a shape-partitioning question has no number to score.

The solvers are the interesting half. Both refuse rather than guess, and both
are total: no search, no sympy, so no bounded subprocess and nothing that can
hang.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import llm_client  # noqa: E402
import lesson_plan_context  # noqa: E402
import LLM_topic_decider as decider  # noqa: E402
import LLM_missing_number_generation as missing  # noqa: E402
import LLM_patterns_generation as patterns  # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --- missing_number -------------------------------------------------------

@pytest.mark.parametrize("tokens,expected", [
    (["8", "+", "?", "=", "11"], 3),
    (["?", "+", "3", "=", "9"], 6),
    (["3", "+", "4", "=", "?"], 7),
    (["12", "-", "?", "=", "5"], 7),
    (["?", "-", "4", "=", "6"], 10),
    (["4", "*", "?", "=", "20"], 5),
])
def test_the_unknown_is_found_wherever_it_sits(tokens, expected):
    """Rearranged, never searched -- one arithmetic step whichever slot is
    blank. A search would be a loop whose termination depends on the numbers,
    which is what hung this codebase before."""
    assert missing.solve_missing(tokens) == expected


@pytest.mark.parametrize("tokens,why", [
    (["8", "+", "?", "=", "2"], "the blank would be negative"),
    (["4", "*", "?", "=", "21"], "the blank would not be a whole number"),
    (["0", "*", "?", "=", "5"], "no number satisfies it"),
    (["8", "+", "?", "=", "?"], "two blanks"),
    (["8", "+", "4", "=", "12"], "no blank at all"),
    (["a", "+", "?", "=", "3"], "not a number"),
    (["8", "+", "?", "=", "11", "9"], "wrong length"),
    (["8", "/", "?", "=", "2"], "an operator this topic does not use"),
    ("8 + ? = 11", "not a list"),
])
def test_an_equation_that_determines_no_whole_answer_is_refused(tokens, why):
    """`None`, not a raise: the caller is inside the retry loop, so an unusable
    reply must cost an attempt rather than leaving the generator as a 500."""
    assert missing.solve_missing(tokens) is None, why


def test_the_equation_on_screen_must_be_the_one_being_scored():
    """The worst failure available here -- a question answered correctly and
    marked wrong. The whole question *is* the equation, so a text disagreeing
    with the scored variables is not a cosmetic slip."""
    tokens = ["8", "+", "?", "=", "11"]
    assert missing.shown_matches_scored("What goes in the blank? 8 + ? = 11",
                                        tokens) is None
    # Whitespace is normalised, so line wrapping does not cost a retry.
    assert missing.shown_matches_scored("What goes in the blank?\n8  +  ? =  11",
                                        tokens) is None
    assert missing.shown_matches_scored("What goes in the blank? 8 + ? = 12",
                                        tokens)
    assert missing.shown_matches_scored("Question 2: 8 + ? = 11", tokens)


@pytest.mark.parametrize("grade,expected", [
    ("1st Grade", "multiplication"),
    ("2nd Grade", "multiplication"),
    ("3rd Grade", None),
    ("Grade 1", "multiplication"),   # grade_levels reads the digit either way
])
def test_multiplication_is_forbidden_at_grades_1_and_2_only(grade, expected):
    """Reproduced in review: `3 * ? = 12` cleared solve_missing (which accepts
    "*"), grade_appropriateness (which only looks for variable notation) and
    shown_matches_scored, and was served two years above 1.OA.8. Grade 3 is
    fine -- 3.OA.4 unknown-factor multiplication is grade-3 content.
    """
    tokens = ["3", "*", "?", "=", "12"]
    assert missing._forbidden_operator(tokens, grade) == expected


def test_an_unreadable_grade_is_treated_as_the_youngest_for_multiplication():
    """`profiles.grade_level` is free text, so a value carrying no readable
    grade at all is a real state. `grade_number` answers None there, and None
    is the signal to treat the student as the youngest rather than to guess --
    so it must forbid multiplication, not fall through to unrestricted."""
    tokens = ["3", "*", "?", "=", "12"]
    assert missing._forbidden_operator(tokens, "not a grade at all") == "multiplication"


def test_multiplication_is_refused_end_to_end_at_grade_1(reply):
    reply({"question_text": "What number goes in the blank? 3 * ? = 12",
           "question_topic": "missing_number",
           "variables": ["3", "*", "?", "=", "12"]})
    with pytest.raises(ValueError):
        missing.generate_missing_number_question([], [], "hard", "1st Grade")


def test_multiplication_is_still_served_at_grade_3(reply):
    reply({"question_text": "What number goes in the blank? 3 * ? = 12",
           "question_topic": "missing_number",
           "variables": ["3", "*", "?", "=", "12"]})
    question = missing.generate_missing_number_question([], [], "hard", "3rd Grade")
    assert question["correct_answer"] == "4"


# --- patterns -------------------------------------------------------------

@pytest.mark.parametrize("values,expected", [
    (["3", "6", "9", "?", "15"], 12),
    (["2", "4", "6", "8", "?"], 10),
    (["?", "10", "15", "20"], 5),
    (["5", "10", "?", "20", "25"], 15),
    (["0", "?", "20", "30"], 10),
])
def test_the_missing_term_is_found_wherever_it_sits(values, expected):
    assert patterns.solve_pattern(values) == expected


def test_a_sequence_that_is_not_arithmetic_is_refused_rather_than_guessed():
    """The load-bearing one. `2, 4, 6, ?, 9` has a first-pair step of 2 and is
    not an arithmetic sequence, so it determines no single answer.

    Deriving the step from the first pair alone would answer 8 confidently for
    a question with no right answer -- a confident wrong answer, which is worse
    than a refused question by exactly the margin this codebase keeps
    rediscovering. The step is checked against every known term instead.
    """
    assert patterns.solve_pattern(["2", "4", "6", "?", "9"]) is None


@pytest.mark.parametrize("values,why", [
    (["1", "2", "?"], "too short to show a pattern"),
    (["10", "8", "?", "4"], "descending"),
    (["1", "?", "2", "?"], "two blanks"),
    (["1", "2", "3", "4"], "no blank at all"),
    (["1", "2", "3", "a"], "not a number"),
    (["1", "1", "1", "?"], "a zero step is not a pattern to continue"),
    (["1", "2", "?", "4", "5", "6", "7", "8", "9"], "too long"),
])
def test_a_sequence_that_determines_no_answer_is_refused(values, why):
    assert patterns.solve_pattern(values) is None, why


def test_the_sequence_on_screen_must_be_the_one_being_scored():
    values = ["3", "6", "9", "?", "15"]
    assert patterns.shown_matches_scored("What is missing? 3, 6, 9, ?, 15",
                                         values) is None
    assert patterns.shown_matches_scored("What is missing? 3, 6, 9, ?, 18",
                                         values)


# --- both, end to end through the retry loop ------------------------------

@pytest.fixture
def reply(monkeypatch):
    monkeypatch.setattr(lesson_plan_context, "append_lesson_context",
                        lambda p, t, b: p)

    def _use(payload):
        monkeypatch.setattr(llm_client, "generate_text",
                            lambda *a, **k: json.dumps(payload))
    return _use


def test_a_valid_missing_number_reply_is_served_with_its_answer_among_options(reply):
    reply({"question_text": "What number goes in the blank? 8 + ? = 11",
           "question_topic": "missing_number",
           "variables": ["8", "+", "?", "=", "11"]})
    question = missing.generate_missing_number_question([], [], "easy", "1st Grade")
    assert question["correct_answer"] == "3"
    assert question["correct_answer"] in question["answer_options"]
    assert len(set(question["answer_options"])) == 4
    assert question["question_topic"] == "missing_number"


def test_a_valid_patterns_reply_is_served_with_its_answer_among_options(reply):
    reply({"question_text": "What number is missing? 3, 6, 9, ?, 15",
           "question_topic": "patterns",
           "values": ["3", "6", "9", "?", "15"]})
    question = patterns.generate_patterns_question([], [], "easy", "1st Grade")
    assert question["correct_answer"] == "12"
    assert question["correct_answer"] in question["answer_options"]
    assert len(set(question["answer_options"])) == 4


@pytest.mark.parametrize("module,entry,payload", [
    (missing, "generate_missing_number_question",
     {"question_text": "What number goes in the blank? 8 + ? = 12",
      "question_topic": "missing_number",
      "variables": ["8", "+", "?", "=", "11"]}),
    (patterns, "generate_patterns_question",
     {"question_text": "What number is missing? 3, 6, 9, ?, 18",
      "question_topic": "patterns",
      "values": ["3", "6", "9", "?", "15"]}),
])
def test_a_reply_whose_text_disagrees_with_its_data_retries(module, entry,
                                                            payload, reply):
    """Not a raise out of the generator -- a retry, which is what
    `_prefetch_worker` already handles."""
    reply(payload)
    with pytest.raises(ValueError):
        getattr(module, entry)([], [], "easy", "1st Grade")


def test_algebraic_notation_is_refused_at_these_grades(reply):
    """The topic is one notation away from `algebra` (6.EE.7). `?` is what
    keeps it at grade 1, so a reply reaching for `2x` has left the topic --
    and `grade_appropriateness` is what notices, since the prompt only asks."""
    reply({"question_text": "What number goes in the blank? 2x + ? = 11",
           "question_topic": "missing_number",
           "variables": ["8", "+", "?", "=", "11"]})
    with pytest.raises(ValueError):
        missing.generate_missing_number_question([], [], "easy", "1st Grade")


# --- the wiring a new topic needs -----------------------------------------

def test_a_new_topic_carries_a_math_topics_row():
    """`record_topic_attempt` resolves a question's topic by joining
    `math_topics.topic_name = questions.subject`, and attributes nothing when
    that finds no row -- silently, since the helper never raises.

    So a topic shipped without its row generates, serves and scores questions
    while crediting the student's work to nothing. That is what
    `20260907000000` had to repair for `rationals`, and shipping a new topic
    without a row would be a fresh instance of it rather than a lesson learned.

    The original ten are exempt: they were seeded before migrations tracked
    this table, so no migration mentions them. Anything added since must bring
    one.
    """
    SEEDED_BEFORE_MIGRATIONS_TRACKED_THEM = {
        "geometry", "algebra", "expressions", "ordering", "rationals",
        "mean", "median", "mode", "probability", "angle_relationships",
    }
    migrations = os.path.join(os.path.dirname(BACKEND), "..", "..",
                              "supabase", "migrations")
    sql = ""
    for name in sorted(os.listdir(migrations)):
        if name.endswith(".sql"):
            sql += open(os.path.join(migrations, name), encoding="utf-8").read()

    for topic in decider.ALL_TOPICS:
        if topic in SEEDED_BEFORE_MIGRATIONS_TRACKED_THEM:
            continue
        assert f"'{topic}'" in sql, (
            f"{topic} has no math_topics row in any migration. Without one, "
            "record_topic_attempt credits every answer on it to nothing.")


def test_every_topic_can_be_generated():
    """`question_generation` dispatches on a `match`. A topic in ALL_TOPICS
    with no case used to fall through it and raise UnboundLocalError on
    `return response` -- a 500 naming a variable rather than the topic nobody
    wired. It raises by name now, and this is what says so."""
    source = open(os.path.join(BACKEND, "LLM_topic_decider.py"),
                  encoding="utf-8").read()
    for topic in decider.ALL_TOPICS:
        assert f'case "{topic}":' in source, f"{topic} has no generator case"
