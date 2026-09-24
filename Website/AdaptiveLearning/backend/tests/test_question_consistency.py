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


# ── counts_mismatch: a bag's counts, located by the label they precede ──────

BAG_TEXT = "A bag contains 6 red marbles, 4 blue marbles, and 2 green marbles."


def test_the_counts_the_text_gives_must_be_the_counts_scored():
    assert qc.counts_mismatch(BAG_TEXT, {"red": 6, "blue": 4, "green": 2}) is None
    assert "red" in qc.counts_mismatch(BAG_TEXT, {"red": 5, "blue": 4, "green": 2})


@pytest.mark.parametrize("text,counts", [
    ("A jar holds 3 apples and 2 pears.", {"apple": 3, "pear": 2}),
    ("A spinner has 8 equal parts: 3 are RED and 5 are blue.", {"red": 3, "blue": 5}),
    ("There are 5 large red counters and 2 blue ones.", {"red": 5, "blue": 2}),
])
def test_a_label_is_found_across_case_plurals_and_adjectives(text, counts):
    assert qc.counts_mismatch(text, counts) is None
    wrong = {k: v + 1 for k, v in counts.items()}
    assert qc.counts_mismatch(text, wrong) is not None


@pytest.mark.parametrize("text", [
    "A bag holds some red, blue and green marbles.",
    "Red: six. Blue: four.",
    "",
])
def test_it_fails_open_where_no_count_precedes_a_label(text):
    assert qc.counts_mismatch(text, {"red": 6, "blue": 4}) is None


@pytest.mark.parametrize("text,counts", [
    (BAG_TEXT, {"red": 6, "blue": 4}),                                  # green left out of items
    ("A bag of 13 marbles has 6 red, 4 blue and 2 green.", {"red": 6, "blue": 4, "green": 2}),
    ("A bag has 6 red and 4 blue. After 1 red is removed, what is the probability of red?",
     {"red": 6, "blue": 4}),                                            # a second number for red
    ("A basket has 3 cherries and 2 plums.", {"cherry": 4, "plum": 2}),  # -ies plural
])
def test_every_number_in_the_text_must_be_a_scored_count_or_their_total(text, counts):
    assert qc.counts_mismatch(text, counts) is not None


def test_a_swapped_count_is_caught_through_an_ies_plural():
    """The same numbers either way, so only reading "cherries" as "cherry" can see the swap."""
    text = "A basket has 3 cherries and 4 berries."
    assert qc.counts_mismatch(text, {"cherry": 4, "berry": 3}) is not None
    assert qc.counts_mismatch(text, {"cherry": 3, "berry": 4}) is None


@pytest.mark.parametrize("text", [
    "A bag of 12 marbles has 6 red, 4 blue and 2 green.",
    "A bag contains 12 marbles: 6 red, 4 blue and 2 green.",
    "There are 12 marbles in total, 6 red, 4 blue and 2 green.",
])
def test_a_stated_total_that_adds_up_agrees(text):
    assert qc.counts_mismatch(text, {"red": 6, "blue": 4, "green": 2}) is None
    assert qc.counts_mismatch("A basket has 3 cherries and 2 plums.", {"cherry": 3, "plum": 2}) is None


def test_a_left_out_item_equal_to_the_total_is_not_read_as_the_total():
    """"and 10 green" is a count, not the bag: only a stated total may be the extra number."""
    assert qc.counts_mismatch("A bag has 6 red, 4 blue and 10 green marbles.", {"red": 6, "blue": 4}) is not None


def test_a_count_before_a_full_stop_is_read():
    assert qc.counts_mismatch("In the bag. Blue: 4. Red: 6. Green: 2.", {"red": 6, "blue": 4}) is not None


def test_the_single_draw_is_not_a_count_but_a_removal_is():
    tail = " what is the probability of red?"
    assert qc.counts_mismatch("A bag has 6 red and 4 blue. If 1 marble is drawn," + tail,
                              {"red": 6, "blue": 4}) is None
    assert qc.counts_mismatch("A bag has 6 red and 4 blue. After 1 red is removed," + tail,
                              {"red": 6, "blue": 4}) is not None


# ── target_mismatch: the item the question asks about ──────────────────────

LABELS = ["red", "blue", "green"]


@pytest.mark.parametrize("text,targets,agrees", [
    (BAG_TEXT + " What is the probability of drawing a red marble?", ["red"], True),
    (BAG_TEXT + " What is the probability of drawing a red marble?", ["blue"], False),
    (BAG_TEXT + " What is the probability of drawing a red or blue marble?", ["red", "blue"], True),
    (BAG_TEXT + " What is the probability of drawing a red or blue marble?", ["red"], False),
    (BAG_TEXT + " What is the probability of NOT drawing a green marble?", ["green"], True),
    (BAG_TEXT + " What are the chances of drawing a red marble?", ["blue"], False),
    (BAG_TEXT + " How likely is a red marble?", ["red"], True),
    (BAG_TEXT + " Which colour would you pick?", ["blue"], True),               # asks nothing
])
def test_the_items_asked_about_must_be_the_target(text, targets, agrees):
    assert (qc.target_mismatch(text, LABELS, targets) is None) is agrees


# ── dice_mismatch: the die and the event the question describes ────────────

DIE = "A standard six-sided die is rolled. What is the probability of rolling "


@pytest.mark.parametrize("text,sides,faces", [
    (DIE + "a number greater than 4?", 6, [6]),                         # the event
    (DIE + "a number greater than 4?", 8, [5, 6]),                      # the die
    (DIE + "a 3?", 8, [3]),                                             # the die, same event
    (DIE + "an even number?", 6, [2, 4]),
    (DIE + "a 2 or a 5?", 6, [2, 3]),
    (DIE + "a number less than 3?", 6, [1, 2, 3]),
    ("An 8-sided die is rolled. What is the probability of rolling at least 7?", 8, [8]),
])
def test_a_die_or_event_the_text_does_not_describe_is_caught(text, sides, faces):
    assert qc.dice_mismatch(text, sides, faces) is not None


@pytest.mark.parametrize("text,sides,faces", [
    (DIE + "a number greater than 4?", 6, [5, 6]),
    (DIE + "an even number?", 6, [2, 4, 6]),
    (DIE + "a prime number?", 6, [2, 3, 5]),
    (DIE + "a 2 or a 5?", 6, [2, 5]),
    (DIE + "a multiple of 3?", 6, [3, 6]),
    ("An 8-sided die is rolled. What is the probability of rolling a 3 on the 8-sided die?", 8, [3]),
    ("A die is rolled. What is the probability of rolling 5 or more?", 6, [5, 6]),
])
def test_the_die_and_event_described_agree(text, sides, faces):
    assert qc.dice_mismatch(text, sides, faces) is None


@pytest.mark.parametrize("text", [
    DIE + "an even number greater than 2?",                             # two events at once
    DIE + "a number that is not not 3?",                                # two negations
    DIE + "a multiple of 0?",                                           # no such event
    "A die is rolled. What are the chances of a four?",                 # the event in words
])
def test_it_fails_open_on_an_event_it_cannot_read(text):
    assert qc.dice_mismatch(text, 6, [1]) is None


@pytest.mark.parametrize("text,faces", [
    (DIE + "a number that isn't a 6?", [1, 2, 3, 4, 5]),
    (DIE + "a number that isn’t even?", [1, 3, 5]),
    (DIE + "a number that is not greater than 4?", [1, 2, 3, 4]),
])
def test_a_negated_event_is_its_complement(text, faces):
    """"Isn't a 6" scored as [6] served 1/6 for a 5/6 question."""
    assert qc.dice_mismatch(text, 6, faces) is None
    assert qc.dice_mismatch(text, 6, [f for f in range(1, 7) if f not in faces]) is not None


def test_prime_is_computed_for_any_die():
    text = "A 30-sided die is rolled. What is the probability of rolling a prime number?"
    assert qc.dice_mismatch(text, 30, [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]) is None
    assert qc.dice_mismatch(text, 30, [2, 3, 5, 7, 11, 13, 17, 19]) is not None


@pytest.mark.parametrize("event,faces", [
    ("a number other than 6?", [1, 2, 3, 4, 5]),
    ("anything except a 6?", [1, 2, 3, 4, 5]),
    ("anything but a 6?", [1, 2, 3, 4, 5]),
    ("a number that cannot be 6?", [1, 2, 3, 4, 5]),
    ("neither a 1 nor a 6?", [2, 3, 4, 5]),
])
def test_every_negation_wording_takes_the_complement(event, faces):
    """Each was served as the event itself: 1/6 for "other than 6", 1/3 for "neither a 1 nor a 6"."""
    text = DIE + event
    assert qc.dice_mismatch(text, 6, faces) is None
    assert qc.dice_mismatch(text, 6, [f for f in range(1, 7) if f not in faces]) is not None


@pytest.mark.parametrize("text,refused", [
    ("A bag has 6 red and 4 blue. What are the odds of drawing a red marble?", True),
    ("A bag has 6 red and 4 blue. What is the probability of drawing a red marble?", False),
])
def test_an_odds_question_is_refused_because_a_probability_is_not_odds(text, refused):
    assert (qc.odds_mismatch(text) is not None) is refused


def test_a_total_before_a_listing_colon_is_stated():
    assert qc.counts_mismatch("There are 12 marbles in a bag: 6 red, 4 blue and 2 green.",
                              {"red": 6, "blue": 4, "green": 2}) is None


@pytest.mark.parametrize("text", [
    "There are 12 marbles in a bag: red 6, blue 4, green 2.",
    "There are 12 marbles in a bag: red 6, blue 4 and green 2.",
    "There are 12 marbles in a bag: red: 6, blue: 4, green: 2.",
    "A bag has red 6, blue 4 and green 2.",
])
def test_a_list_that_leads_with_the_colour_agrees(text):
    assert qc.counts_mismatch(text, {"red": 6, "blue": 4, "green": 2}) is None


@pytest.mark.parametrize("text", [
    "There are 12 marbles in a bag: red 4, blue 6, green 2.",
    "A bag has red 4, blue 6 and green 2.",
    "There are 12 marbles in a bag: red: 4, blue: 6, green: 2.",
])
def test_a_colour_led_list_that_swaps_two_counts_is_refused(text):
    """Every number is still shown, so only reading "red 4" as red's count catches it."""
    reason = qc.counts_mismatch(text, {"red": 6, "blue": 4, "green": 2})
    # Named for red: "6 and green" misread as green's count would refuse for another reason.
    assert reason and "'red' as [4]" in reason


def test_a_colon_that_starts_no_list_does_not_make_a_count_the_total():
    """The left-out green's 10 is the others' sum; only a list after the colon makes it the bag."""
    text = "A bag has 6 red, 4 blue and 10 green marbles in the bag: what is the probability of red?"
    assert qc.counts_mismatch(text, {"red": 6, "blue": 4}) is not None


def test_a_count_of_one_is_not_taken_for_the_draw():
    """Only "1 … is drawn", or "if 1 … drawn", is the draw; "1 red marble picked" is scored."""
    assert qc.counts_mismatch("1 red marble picked from a tray of 6 red and 4 blue.",
                              {"red": 6, "blue": 4}) is not None
    assert qc.counts_mismatch("A bag has 6 red and 4 blue. If 1 marble drawn at random, what is "
                              "the probability of red?", {"red": 6, "blue": 4}) is None


def test_only_anything_but_negates():
    """A bare "but" is a clause, not a negation: "a 3, but only on the first roll" is faces [3]."""
    text = DIE + "a 3, but only on the first roll?"
    assert qc.dice_mismatch(text, 6, [3]) is None
    assert qc.dice_mismatch(text, 6, [1, 2, 4, 5, 6]) is not None


def test_chances_and_likely_ask_the_question_too():
    text = "A standard six-sided die is rolled. What are the chances of rolling a number greater than 4?"
    assert qc.dice_mismatch(text, 6, [6]) is not None
