"""An ordering question is written from the values scored, in a direction the code chooses."""
import json
import os
from fractions import Fraction

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import llm_client  # noqa: E402
import lesson_plan_context  # noqa: E402
import LLM_ordering_generation as ordering  # noqa: E402

GOOD = {"question_text": "Order from least to greatest: 3/4, 0.6, 2/3, 0.2",
        "question_topic": "ordering", "direction": "least_to_greatest",
        "values": ["3/4", "0.6", "2/3", "0.2"]}
ASCENDING = ["0.2", "0.6", "2/3", "3/4"]


@pytest.fixture
def replies(monkeypatch):
    """Answers each attempt with the next payload; returns how many were asked for."""
    monkeypatch.setattr(lesson_plan_context, "append_lesson_context", lambda p, t, b: p)
    asked = []

    def _use(*payloads, direction="least_to_greatest"):
        monkeypatch.setattr(ordering, "_pick_direction", lambda: direction)

        def generate(*_a, **_k):
            asked.append(1)
            return json.dumps(payloads[min(len(asked), len(payloads)) - 1])
        monkeypatch.setattr(llm_client, "generate_text", generate)
        return asked
    return _use


def _generate():
    return ordering.generate_ordering_question([], [], "medium", "7th Grade")


@pytest.mark.parametrize("direction,words,expected", [
    ("least_to_greatest", "least to greatest", ASCENDING),
    ("greatest_to_least", "greatest to least", ASCENDING[::-1]),
])
def test_the_text_states_the_direction_that_is_scored(replies, direction, words, expected):
    replies(GOOD, direction=direction)
    question = _generate()
    assert question["question_text"] == f"Order from {words}: 3/4, 0.6, 2/3, 0.2"
    assert question["correct_answer"] == expected


@pytest.mark.parametrize("text,field", [
    ("Order from greatest to least: 3/4, 0.6, 2/3, 0.2", "greatest_to_least"),
    ("Order from least_to_greatest: 1, 2, 3", "sideways"),
    ("Put these from the largest to the smallest.", "descending"),
])
def test_the_models_text_and_direction_are_not_what_is_served(replies, text, field):
    """Whatever it wrote, the student reads the rendered text and is scored in its direction."""
    replies({**GOOD, "question_text": text, "direction": field})
    question = _generate()
    assert question["question_text"] == "Order from least to greatest: 3/4, 0.6, 2/3, 0.2"
    assert question["correct_answer"] == ASCENDING


def test_a_reply_without_text_or_direction_is_served(replies):
    replies({"values": GOOD["values"]})
    assert _generate()["correct_answer"] == ASCENDING


@pytest.mark.parametrize("values", [["1", "2"], "3/4, 0.6, 2/3", [["1"], "2", "3"], None])
def test_values_that_cannot_be_ordered_and_shown_are_retried(replies, values):
    asked = replies({**GOOD, "values": values}, GOOD)
    assert _generate()["correct_answer"] == ASCENDING
    assert len(asked) == 2


def test_equal_values_are_retried_because_they_have_two_right_orders(replies):
    """1/2 and 0.5 parse to one number, so a correct order could be a 'wrong' option."""
    asked = replies({**GOOD, "values": ["1/2", "0.5", "3"]}, GOOD)
    question = _generate()
    assert len(asked) == 2
    assert question["correct_answer"] == ASCENDING


def test_both_directions_are_chosen():
    assert {ordering._pick_direction() for _ in range(200)} == set(ordering.DIRECTIONS)


def test_the_prompts_own_example_has_distinct_values_matching_its_text():
    """The example the model copies may not show equal values, or values its text does not."""
    example = json.loads(ordering.extract_json(ordering.ordering_prompt))
    shown = example["question_text"].split(":", 1)[1].replace(" ", "").split(",")
    assert shown == example["values"]
    numbers = [Fraction(v) for v in example["values"]]
    assert len(set(numbers)) == len(numbers)
