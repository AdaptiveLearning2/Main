"""A probability reply is read after the retry loop, so a bad one must be
refused inside it.

`sides` and `items` belong to one scenario each, so neither can go in
`required_keys` unconditionally -- and both are read *below* the `for/else`.
A missing one was a KeyError escaping the generator on attempt 1 and reaching
the student as a 500, where every other malformed reply costs a retry.

The same read takes `scenario` from the reply. This prompt sends all three
blocks and names the wanted one by number, so a reply is free to answer a
different scenario -- the hole geometry and angles had, still open here.

The schema does not cover this: it closes the dice half only, and only on
Claude. The two bag scenarios get no schema at all, and `LLM_PROVIDER` defaults
to ollama.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import llm_client  # noqa: E402
import lesson_plan_context  # noqa: E402
import LLM_probability_generation as prob  # noqa: E402

# "medium" maps to scenario 3, `dice`.
DICE = {"question_text": "A standard six-sided die is rolled. What is the "
                         "probability of rolling a number greater than 4?",
        "question_topic": "probability", "scenario": "dice",
        "sides": "6", "target": ["5", "6"]}


@pytest.fixture
def reply(monkeypatch):
    monkeypatch.setattr(lesson_plan_context, "append_lesson_context",
                        lambda p, t, b: p)

    def _use(payload):
        monkeypatch.setattr(llm_client, "generate_text",
                            lambda *a, **k: json.dumps(payload))
    return _use


@pytest.mark.parametrize("label,payload", [
    ("dice reply with no 'sides'", {**{k: v for k, v in DICE.items()
                                       if k != "sides"}}),
    ("bag reply with no 'items'", {**DICE, "scenario": "probability_of",
                                   "target": "red"}),
    ("bag question mislabelled dice", {**DICE, "target": "red",
                                       "items": {"red": "6", "blue": "4"}}),
])
def test_an_unusable_reply_retries_instead_of_raising_keyerror(label, payload,
                                                               reply):
    """All three reached the student as a 500 before this. The assertion is on
    the *type*: a ValueError after the retries is the path `_prefetch_worker`
    already catches, and a KeyError is not."""
    reply(payload)
    with pytest.raises(ValueError):
        prob.generate_probability_question([], [], "medium", "7th Grade")


def test_a_reply_answering_the_scenario_that_was_asked_for_is_served(reply):
    """The teeth. Without this, refusing everything would pass the three
    above."""
    reply(DICE)
    question = prob.generate_probability_question([], [], "medium", "7th Grade")
    assert question["question_text"] == DICE["question_text"]
    assert question["correct_answer"] in question["answer_options"]
