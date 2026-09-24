"""`shape_fractions`: reading a fraction off a partitioned shape (1.G.3, 2.G.3, 3.NF.1)."""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import llm_client  # noqa: E402
import lesson_plan_context  # noqa: E402
import question_figures  # noqa: E402
import LLM_shape_fractions_generation as shapes  # noqa: E402


@pytest.mark.parametrize("parts,shaded,expected", [
    (2, 1, "1/2"), (4, 1, "1/4"), (4, 3, "3/4"),
    (3, 1, "1/3"), (3, 2, "2/3"), (8, 3, "3/8"), (8, 5, "5/8"),
])
def test_the_fraction_is_what_the_picture_shows(parts, shaded, expected):
    assert shapes.solve_shape_fraction(parts, shaded) == expected


@pytest.mark.parametrize("parts,shaded", [(4, 2), (6, 2), (6, 3), (6, 4), (8, 2), (8, 4), (8, 6)])
def test_a_reducible_fraction_is_refused_because_it_has_two_right_answers(parts, shaded):
    """`2/4` and `1/2` are both right, so either key marks a right answer wrong."""
    assert math.gcd(shaded, parts) != 1, "this case is meant to be reducible"
    assert shapes.solve_shape_fraction(parts, shaded) is None


@pytest.mark.parametrize("parts,shaded,why", [
    (4, 4, "the whole shape is not a fraction of itself to read"),
    (4, 0, "nothing shaded has nothing to point at"),
    (1, 1, "one part is not a partition"),
    (4, 5, "more shaded than there are parts"),
    ("four", 1, "not a number"),
])
def test_a_picture_that_asks_nothing_is_refused(parts, shaded, why):
    assert shapes.solve_shape_fraction(parts, shaded) is None, why


def test_the_distractors_lead_with_the_mistake_a_child_makes():
    """Counting the unshaded parts is the misreading, so it is the first distractor."""
    wrong = shapes.generate_incorrect_answers(4, 3)
    assert wrong[0] == "1/4"                     # the complement
    assert "3/4" not in wrong
    assert len(wrong) == 3 and len(set(wrong)) == 3


@pytest.fixture
def reply(monkeypatch):
    monkeypatch.setattr(lesson_plan_context, "append_lesson_context",
                        lambda p, t, b: p)

    def _use(payload):
        monkeypatch.setattr(llm_client, "generate_text",
                            lambda *a, **k: json.dumps(payload))
    return _use


VALID = {"question_text": "What fraction of the shape is shaded?",
         "question_topic": "shape_fractions", "scenario": "part_whole",
         "parts": "4", "shaded": "3"}


def test_a_valid_reply_is_served_with_its_shape(reply):
    reply(VALID)
    question = shapes.generate_shape_fractions_question([], [], "easy", "1st Grade")
    assert question["correct_answer"] == "3/4"
    assert question["correct_answer"] in question["answer_options"]
    assert len(set(question["answer_options"])) == 4
    assert question["figure"] == {"type": "part_whole", "parts": 4, "shaded": 3}


@pytest.mark.parametrize("override,why", [
    ({"shaded": "2"}, "2/4 has two right answers"),
    ({"shaded": "4"}, "the whole shape"),
    ({"parts": "12"}, "too many parts to count"),
    ({"question_text": "What fraction of the 4 parts is shaded?"}, "a digit gives the reading away"),
    ({"scenario": "rectangle_area"}, "a scenario that was not asked for"),
])
def test_an_unusable_reply_retries(override, why, reply):
    reply({**VALID, **override})
    with pytest.raises(ValueError, match="after retries"):
        shapes.generate_shape_fractions_question([], [], "easy", "1st Grade")


def test_the_figure_and_the_answer_come_from_the_same_two_numbers():
    """No text states the fraction, so a mismatched picture would be unfalsifiable."""
    figure = question_figures.figure_for("part_whole", {"parts": "8", "shaded": "3"})
    assert figure == {"type": "part_whole", "parts": 8, "shaded": 3}
    assert shapes.solve_shape_fraction(figure["parts"], figure["shaded"]) == "3/8"


def test_every_reachable_question_gets_three_usable_distractors():
    """Exhaustive over 21 fractions: distractors are proper (< 1) and of distinct value."""
    from fractions import Fraction

    checked = 0
    for parts in range(2, question_figures.MAX_PARTS + 1):
        for shaded in range(1, parts):
            if math.gcd(shaded, parts) != 1:
                continue                 # refused as ambiguous, see above
            wrong = shapes.generate_incorrect_answers(parts, shaded)
            values = [Fraction(w) for w in wrong]
            assert len(wrong) == 3, (parts, shaded, wrong)
            assert all(v < 1 for v in values), (parts, shaded, wrong)
            assert len(set(values)) == 3, (parts, shaded, wrong)
            assert Fraction(shaded, parts) not in values, (parts, shaded, wrong)
            assert len(set(wrong)) == 3, (parts, shaded, wrong)
            checked += 1
    assert checked == 21, f"the reachable space is 21 fractions, checked {checked}"
