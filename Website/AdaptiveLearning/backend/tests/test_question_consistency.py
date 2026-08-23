"""The question a student sees must describe the data that gets scored.

Both cases below are real generator output, not invented: measured on
llama3.1:8b (2026-08-19), 2 wrong answers in 12 generated questions. They
are the worst shape of failure available here -- the question is
well-formed and answerable, the student answers it correctly, and the
solver marks them wrong against data or a question they never saw.

As with `grade_appropriateness`, the load-bearing half of this file is the
"must not fire" half: these checks run inside the retry loops, so a false
positive burns retries and looks exactly like a model that cannot follow
instructions. Both checks fail OPEN when the text cannot be read
confidently.
"""

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
    """Answered [8, 4]. In the dataset shown, 4 occurs three times and 8
    twice, so 4 is the only mode -- the scored values were not the numbers
    on screen."""
    scored = ["8", "4", "12", "16", "4", "14", "8", "10", "20", "4", "8"]
    assert qc.dataset_mismatch(MODE_FAILURE, scored) is not None


def test_agreement_is_not_a_violation():
    shown = ["8", "4", "12", "16", "4", "14", "8", "10", "20", "4"]
    assert qc.dataset_mismatch(MODE_FAILURE, shown) is None


def test_order_is_not_part_of_the_comparison():
    """The solver sorts anyway, so a different order is the same dataset --
    flagging it would reject correct questions."""
    resorted = ["4", "4", "4", "8", "8", "10", "12", "14", "16", "20"]
    assert qc.dataset_mismatch(MODE_FAILURE, resorted) is None


def test_a_single_missing_value_is_caught():
    dropped = ["8", "4", "12", "16", "4", "14", "8", "10", "20"]
    assert qc.dataset_mismatch(MODE_FAILURE, dropped) is not None


def test_a_changed_value_is_caught():
    changed = ["8", "4", "12", "16", "4", "14", "8", "10", "21", "4"]
    assert qc.dataset_mismatch(MODE_FAILURE, changed) is not None


@pytest.mark.parametrize("text,values", [
    # No colon-delimited list -- the dataset cannot be located, so the check
    # declines rather than comparing against stray numbers in the sentence.
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
    """A question sentence routinely carries numbers that are not data --
    "during a school year", "for a week". Only the list after the colon is
    compared, which is what makes this safe to run on every question."""
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
    """Answered 18/23. The totals are 17+23+14+15 = 69 and EDM is 15, so the
    answer should be 15/69 = 5/23; 18/23 is 54/69, the COMPLEMENT. The text
    asks a positive question and the scenario said not_probability_of."""
    assert qc.negation_mismatch(PROBABILITY_FAILURE, "not_probability_of") is not None


def test_the_reverse_direction_is_caught_too():
    """A negated question scored as `probability_of` is wrong by exactly the
    same amount, so the check has to bind in both directions."""
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
    """`dice` asks about a condition over faces ("greater than 4"), which is
    neither positive nor complementary in this sense -- judging it would
    reject every dice question."""
    assert qc.negation_mismatch("a die showing greater than 4", scenario) is None


def test_a_category_containing_no_is_not_read_as_negation():
    """The negation pattern is word-bounded so an ordinary word cannot make
    a positive question look complementary."""
    text = "A shelf has 4 novels and 6 notebooks. What is the probability of drawing a notebook?"
    assert qc.negation_mismatch(text, "probability_of") is None


# -- fractions ------------------------------------------------------------
#
# `ordering` scores fractions alongside decimals, and `solve_ordering` sorts
# on float(sympify(v)), so they are comparable. Reading "3/4" as a bare 3 and
# a bare 4 made this check fail open on HALF the ordering questions in a
# 32-question live sample (2026-08-21) -- inert on exactly the topic the PR
# claimed to cover. Every text below is verbatim from that sample.

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
    """The point of reading fractions at all: a mismatch among them is now
    visible, where before the whole question was skipped."""
    text = "Order from least to greatest: 3/4, 0.27, 0.85, 2/3"
    assert qc.dataset_mismatch(text, ["3/4", "0.27", "0.85", "1/3"]) is not None


def test_a_fraction_and_its_decimal_agree():
    """Compared by value, not by token -- the solver sorts on the value, so
    4/5 shown against 0.8 scored is agreement, not a mismatch."""
    assert qc.dataset_mismatch("Order these: 4/5, 0.27", ["0.8", "0.27"]) is None


def test_equal_fractions_written_differently_agree():
    assert qc.dataset_mismatch("Order these: 4/5, 32/40", ["0.8", "0.8"]) is None


def test_a_mixed_number_inside_the_list_fails_open():
    """"1 1/2" is one value to a reader and two tokens to the regex, which
    truncates the list at it: without the guard the text above yields a shown
    list of just [3/4, 1] and reports a false mismatch against three scored
    values. Failing open costs a check; firing would blame the model for the
    tokenisation.

    The mixed number has to sit INSIDE the list to test the guard -- with it
    leading ("Order these: 1 1/2, 3/4") no comma-list matches at all, so the
    function returns None for an unrelated reason and the guard is never
    reached. That version of this test passed with the guard deleted.
    """
    text = "Order from least to greatest: 3/4, 1 1/2, 0.5"
    assert qc._LIST_AFTER_COLON.findall(text) == ["3/4, 1"]   # the truncation
    assert qc.dataset_mismatch(text, ["3/4", "3/2", "0.5"]) is None


def test_a_zero_denominator_fails_open_rather_than_raising():
    assert qc.dataset_mismatch("Order these: 1/0, 3/4", ["1/0", "3/4"]) is None


def test_an_oversized_fraction_fails_open_rather_than_raising():
    """float() of a Fraction with an oversized numerator raises OverflowError,
    where the equivalent plain decimal degrades to inf -- so this is specific
    to the fraction path. Nothing here may raise: these run inside the
    generation retry loops, and an escaping exception kills the generation
    instead of retrying it."""
    huge = "1" + "0" * 400
    assert qc._as_floats([huge + "/1", "2"]) is None
    assert qc.dataset_mismatch("Order these: 3/4, 0.5", [huge + "/1", "2"]) is None
