"""`missing_number` (1.OA.8) and `patterns` (1.NBT.1): total solvers that refuse rather than guess."""
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
    """Rearranged, never searched: a search's termination would depend on the numbers."""
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
    """`None`, not a raise: the caller's retry loop spends an attempt on it."""
    assert missing.solve_missing(tokens) is None, why


def test_the_equation_on_screen_must_be_the_one_being_scored():
    """Otherwise a correct answer is marked wrong; the question *is* the equation."""
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
    """No other check catches `3 * ? = 12`; 3.OA.4 makes it grade-3 content."""
    tokens = ["3", "*", "?", "=", "12"]
    assert missing._forbidden_operator(tokens, grade) == expected


def test_an_unreadable_grade_is_treated_as_the_youngest_for_multiplication():
    """`grade_number` answers None for free text, which means "treat as youngest"."""
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
    """The first-pair step alone would confidently answer 8; the step is checked against every term."""
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
    """A retry, not a raise out of the generator."""
    reply(payload)
    with pytest.raises(ValueError):
        getattr(module, entry)([], [], "easy", "1st Grade")


def test_algebraic_notation_is_refused_at_these_grades(reply):
    """`2x` is `algebra` (6.EE.7); `grade_appropriateness` enforces what the prompt only asks."""
    reply({"question_text": "What number goes in the blank? 2x + ? = 11",
           "question_topic": "missing_number",
           "variables": ["8", "+", "?", "=", "11"]})
    with pytest.raises(ValueError):
        missing.generate_missing_number_question([], [], "easy", "1st Grade")


# --- the wiring a new topic needs -----------------------------------------

def test_a_new_topic_carries_a_math_topics_row():
    """`record_topic_attempt` joins on `math_topics.topic_name` and silently credits nothing without a row.

    The original ten predate migrations tracking this table.
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
    """`question_generation` dispatches on a `match`; every topic needs a case.

    Read from the AST, so a case that serves several topics (`case "a" | "b":`) counts.
    """
    import ast
    tree = ast.parse(open(os.path.join(BACKEND, "LLM_topic_decider.py"),
                          encoding="utf-8").read())
    dispatch = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "question_generation")
    cased = {node.value.value for node in ast.walk(dispatch)
             if isinstance(node, ast.MatchValue) and isinstance(node.value, ast.Constant)}
    for topic in decider.ALL_TOPICS:
        assert topic in cased, f"{topic} has no generator case"
