"""The question shown must describe the data scored; both checks fail open when unsure."""

import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import question_consistency as qc  # noqa: E402


# --- the dataset shown must be the dataset scored --------------------------

MODE_FAILURE = ("A school counselor recorded the number of hours students spent "
                "on homework each day. The numbers of hours were: 8, 4, 12, 16, "
                "4, 14, 8, 10, 20, 4. What is the mode(s) of this dataset?")


def test_the_measured_mode_failure_is_caught():
    """Real output, answered [8, 4]; the shown data's only mode is 4."""
    scored = ["8", "4", "12", "16", "4", "14", "8", "10", "20", "4", "8"]
    assert qc.dataset_mismatch(MODE_FAILURE, scored) is not None


def test_agreement_is_not_a_violation():
    shown = ["8", "4", "12", "16", "4", "14", "8", "10", "20", "4"]
    assert qc.dataset_mismatch(MODE_FAILURE, shown) is None


def test_order_is_not_part_of_the_comparison():
    """The solver sorts anyway, so a different order is the same dataset."""
    resorted = ["4", "4", "4", "8", "8", "10", "12", "14", "16", "20"]
    assert qc.dataset_mismatch(MODE_FAILURE, resorted) is None


def test_a_single_missing_value_is_caught():
    dropped = ["8", "4", "12", "16", "4", "14", "8", "10", "20"]
    assert qc.dataset_mismatch(MODE_FAILURE, dropped) is not None


def test_a_changed_value_is_caught():
    changed = ["8", "4", "12", "16", "4", "14", "8", "10", "21", "4"]
    assert qc.dataset_mismatch(MODE_FAILURE, changed) is not None


@pytest.mark.parametrize("text,values", [
    # No colon-delimited list: the dataset cannot be located.
    ("A bag contains 8 red and 12 blue marbles.", ["8", "12"]),
    # Non-numeric values (expressions, operators) are not comparable.
    (MODE_FAILURE, ["2x", "+", "3"]),
    ("", ["1", "2"]),
    (MODE_FAILURE, []),
    (None, ["1", "2"]),
])
def test_it_fails_open_when_it_cannot_read_confidently(text, values):
    assert qc.dataset_mismatch(text, values) is None


def test_numbers_outside_the_dataset_do_not_trip_it():
    """Only the list after the colon is compared, not other numbers in the sentence."""
    text = ("A librarian recorded books borrowed over 12 months by 30 students. "
            "The numbers of books were: 5, 7, 5, 9.")
    assert qc.dataset_mismatch(text, ["5", "7", "5", "9"]) is None


# --- a probability question must be scored on the side it asks about -------

PROBABILITY_FAILURE = ("A music festival features bands from diverse genres. If "
                       "17 rock bands, 23 pop bands, 14 hip-hop bands, and 15 "
                       "electronic dance music (EDM) bands are participating in "
                       "the festival, what is the probability of selecting an "
                       "EDM band at random?")


def test_the_measured_probability_failure_is_caught():
    """Real output, answered 18/23 (the complement of 15/69) under not_probability_of."""
    assert qc.negation_mismatch(PROBABILITY_FAILURE, "not_probability_of") is not None


def test_the_reverse_direction_is_caught_too():
    text = "what is the probability of NOT drawing a red marble?"
    assert qc.negation_mismatch(text, "probability_of") is not None


@pytest.mark.parametrize("text,scenario", [
    ("what is the probability of NOT drawing a pink pencil?", "not_probability_of"),
    ("what is the probability of drawing a red marble?", "probability_of"),
])
def test_matching_wording_and_scenario_pass(text, scenario):
    assert qc.negation_mismatch(text, scenario) is None


@pytest.mark.parametrize("scenario", ["dice", None, "", "unknown"])
def test_only_the_two_complementary_scenarios_are_judged(scenario):
    """`dice` asks a condition over faces, neither positive nor complementary."""
    assert qc.negation_mismatch("a die showing greater than 4", scenario) is None


def test_a_category_containing_no_is_not_read_as_negation():
    """The negation pattern is word-bounded."""
    text = "A shelf has 4 novels and 6 notebooks. What is the probability of drawing a notebook?"
    assert qc.negation_mismatch(text, "probability_of") is None


# -- fractions ------------------------------------------------------------
# Compared by value, as `solve_ordering` sorts. Without fraction parsing the check failed
# open on half of a 32-question live ordering sample; the texts below are verbatim from it.

@pytest.mark.parametrize("text,values", [
    ("Order from least to greatest: 3/4, 0.27, 0.85, 2/3",
     ["3/4", "0.27", "0.85", "2/3"]),
    ("Order from greatest to least:  -1/2, 2/3, 0.55, 1/4, 1.25, -3/4",
     ["-1/2", "2/3", "0.55", "1/4", "1.25", "-3/4"]),
    ("Order from least to greatest: 4/5, -12.25, 75, 32/40, 0.85, -9/10",
     ["4/5", "-12.25", "75", "32/40", "0.85", "-9/10"]),
    ("Order from least to greatest: 2/3, 0.82, 0.5, 7/8, 0.91",
     ["2/3", "0.82", "0.5", "7/8", "0.91"]),
])
def test_real_ordering_questions_with_fractions_are_not_refused(text, values):
    assert qc.dataset_mismatch(text, values) is None


def test_a_fraction_dataset_that_disagrees_is_caught():
    text = "Order from least to greatest: 3/4, 0.27, 0.85, 2/3"
    assert qc.dataset_mismatch(text, ["3/4", "0.27", "0.85", "1/3"]) is not None


def test_a_fraction_and_its_decimal_agree():
    """Compared by value, not by token."""
    assert qc.dataset_mismatch("Order these: 4/5, 0.27", ["0.8", "0.27"]) is None


def test_equal_fractions_written_differently_agree():
    assert qc.dataset_mismatch("Order these: 4/5, 32/40", ["0.8", "0.8"]) is None


def test_a_mixed_number_inside_the_list_fails_open():
    """"1 1/2" truncates the regex's list; it must sit INSIDE the list, or the guard is never reached."""
    text = "Order from least to greatest: 3/4, 1 1/2, 0.5"
    assert qc._LIST_AFTER_COLON.findall(text) == ["3/4, 1"]   # the truncation
    assert qc.dataset_mismatch(text, ["3/4", "3/2", "0.5"]) is None


def test_a_zero_denominator_fails_open_rather_than_raising():
    assert qc.dataset_mismatch("Order these: 1/0, 3/4", ["1/0", "3/4"]) is None


def test_an_oversized_fraction_fails_open_rather_than_raising():
    """float(Fraction) overflows where a decimal gives inf; nothing may raise inside the retry loops."""
    huge = "1" + "0" * 400
    assert qc._as_floats([huge + "/1", "2"]) is None
    assert qc.dataset_mismatch("Order these: 3/4, 0.5", [huge + "/1", "2"]) is None


# ── expression_mismatch: the expression shown against the tokens scored ────

@pytest.mark.parametrize("text,tokens", [
    ("Solve for x: 3x + 5 = 20", ["3x", "+", "5", "=", "26"]),        # a changed value
    ("Solve 3/4 + 1/8", ["3/4", "-", "1/8"]),                          # a changed operator
    ("Maria has 3/4 of a pizza and eats 1/8 of it. How much is left?", ["3/4", "-", "1/6"]),
])
def test_an_expression_shown_differently_from_the_one_scored_is_caught(text, tokens):
    assert qc.expression_mismatch(text, tokens) is not None


@pytest.mark.parametrize("text,tokens", [
    ("Solve for x: 2x + 3 = 7.", ["2x", "+", "3", "=", "7"]),
    ("Solve 4/5 - 1/10", ["4/5", "-", "1/10"]),
    ("What is (3/4) × (1/2)?", ["3/4", "*", "1/2"]),
    ("Solve 3(x + 2) = 12 for x", ["3", "*", "(", "x", "+", "2", ")", "=", "12"]),
    ("Evaluate −3/4 + 1/2", ["-3/4", "+", "1/2"]),
    ("A number minus 7 is 12. Solve x - 7 = 12.", ["x", "-", "7", "=", "12"]),
    ("Maria has 3/4 of a pizza and eats 1/8 of it. How much is left?", ["3/4", "-", "1/8"]),
    # A lone fraction is a number in a sentence, not a displayed expression.
    ("A recipe needs 3/4 cup of flour. How much flour for 2 recipes?", ["3/4", "*", "2"]),
])
def test_the_same_expression_however_it_is_spaced_or_bracketed_agrees(text, tokens):
    assert qc.expression_mismatch(text, tokens) is None


@pytest.mark.parametrize("text,tokens", [
    ("Add 1 1/2 and 3/4.", ["3/2", "+", "3/4"]),
    ("What is three quarters plus one eighth?", ["3/4", "+", "1/8"]),
    ("", ["3/4"]),
    ("Solve 3/4 + 1/8", "3/4+1/8"),
])
def test_it_fails_open_on_mixed_numbers_words_and_unusable_input(text, tokens):
    assert qc.expression_mismatch(text, tokens) is None
