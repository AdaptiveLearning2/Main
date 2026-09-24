"""An ordering is scored in the direction the text asks for, over values with one right order."""
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

    def _use(*payloads):
        def generate(*_a, **_k):
            asked.append(1)
            return json.dumps(payloads[min(len(asked), len(payloads)) - 1])
        monkeypatch.setattr(llm_client, "generate_text", generate)
        return asked
    return _use


def _generate():
    return ordering.generate_ordering_question([], [], "medium", "7th Grade")


@pytest.mark.parametrize("text,field", [
    ("Order from greatest to least: 3/4, 0.6, 2/3, 0.2", "greatest to least"),
    ("Order from greatest to least: 3/4, 0.6, 2/3, 0.2", "descending"),
    ("Order from greatest to least: 3/4, 0.6, 2/3, 0.2", "Greatest_To_Least"),
    ("Put these in descending order: 3/4, 0.6, 2/3, 0.2", "greatest_to_least"),
    ("Order from largest to smallest: 3/4, 0.6, 2/3, 0.2", "sideways"),
])
def test_a_descending_question_is_scored_descending_however_the_field_is_written(replies, text, field):
    """Anything but exactly 'greatest_to_least' used to sort ascending."""
    replies({**GOOD, "question_text": text, "direction": field})
    assert _generate()["correct_answer"] == ASCENDING[::-1]


def test_an_ascending_question_is_scored_ascending(replies):
    replies(GOOD)
    assert _generate()["correct_answer"] == ASCENDING


@pytest.mark.parametrize("payload", [
    {**GOOD, "direction": "greatest_to_least"},
    {**GOOD, "question_text": "Order these numbers: 3/4, 0.6, 2/3, 0.2"},
    {**GOOD, "question_text": "Order from least to greatest, then greatest to least: 3/4, 0.6, 2/3, 0.2"},
])
def test_a_text_and_field_that_disagree_or_say_nothing_are_retried(replies, payload):
    asked = replies(payload, GOOD)
    assert _generate()["correct_answer"] == ASCENDING
    assert len(asked) == 2


def test_equal_values_are_retried_because_they_have_two_right_orders(replies):
    """1/2 and 0.5 parse to one number, so a correct order could be a 'wrong' option."""
    equal = {**GOOD, "question_text": "Order from least to greatest: 1/2, 0.5, 3",
             "values": ["1/2", "0.5", "3"]}
    asked = replies(equal, GOOD)
    question = _generate()
    assert len(asked) == 2
    assert question["correct_answer"] == ASCENDING


def test_the_prompts_own_example_has_distinct_values_matching_its_text():
    """The example the model copies may not show the two mistakes this file refuses."""
    example = json.loads(ordering.extract_json(ordering.ordering_prompt))
    shown = example["question_text"].split(":", 1)[1].replace(" ", "").split(",")
    assert shown == example["values"]
    numbers = [Fraction(v) for v in example["values"]]
    assert len(set(numbers)) == len(numbers)
