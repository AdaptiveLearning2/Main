"""A probability reply is read after the retry loop, so a bad shape or scenario is refused inside it."""
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
    """`_prefetch_worker` catches ValueError; a KeyError reaches the student as a 500."""
    reply(payload)
    with pytest.raises(ValueError):
        prob.generate_probability_question([], [], "medium", "7th Grade")


def test_a_reply_answering_the_scenario_that_was_asked_for_is_served(reply):
    """Without this, refusing everything would pass the three above."""
    reply(DICE)
    question = prob.generate_probability_question([], [], "medium", "7th Grade")
    assert question["question_text"] == DICE["question_text"]
    assert question["correct_answer"] in question["answer_options"]


# ── what is scored is what the question shows ───────────────────────────────

BAG = {"question_text": "A bag contains 6 red marbles, 4 blue marbles, and 2 green marbles. "
                        "If one marble is drawn at random, what is the probability of drawing a red marble?",
       "question_topic": "probability", "scenario": "probability_of",
       "items": {"red": "6", "blue": "4", "green": "2"}, "target": "red"}
NOT_BAG = {**BAG, "scenario": "not_probability_of",
           "question_text": BAG["question_text"].replace("of drawing", "of NOT drawing")}


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


def _generate(difficulty):
    return prob.generate_probability_question([], [], difficulty, "7th Grade")


@pytest.mark.parametrize("target", ["Red", " red ", ["RED"]])
def test_a_target_written_differently_still_names_its_item(replies, target):
    replies({**BAG, "target": target})
    assert _generate("easy")["correct_answer"] == "1/2"


@pytest.mark.parametrize("difficulty,payload", [
    ("easy", {**BAG, "target": "red marbles"}),
    ("easy", {**BAG, "target": "purple"}),
    ("easy", {**BAG, "target": ["red", "Red"]}),
    ("hard", {**NOT_BAG, "target": "purple"}),
    ("easy", {**BAG, "items": {"red": "6", "blue": "4.5", "green": "2"},
              "question_text": BAG["question_text"].replace("4 blue", "4.5 blue")}),
    ("easy", {**BAG, "items": {"red": "0", "blue": "0"}}),
])
def test_a_target_or_count_that_cannot_be_scored_is_retried_not_served(replies, difficulty, payload):
    """Unknown targets scored 0 favourable: 0 served as correct, or 1 for NOT."""
    asked = replies(payload, BAG if difficulty == "easy" else NOT_BAG)
    question = _generate(difficulty)
    assert len(asked) == 2
    # Red is half the bag, so "red" and "not red" both serve 1/2 from the good reply.
    assert question["correct_answer"] == "1/2"


@pytest.mark.parametrize("target,sides", [
    (["5", "6", "7"], "6"), (["5", "5"], "6"), (["0", "1"], "6"), (["2.5"], "6"),
    (["5", "6"], "6.5"), (["1"], "1"), ("56", "6"),
])
def test_dice_faces_must_be_distinct_faces_of_the_die(replies, target, sides):
    asked = replies({**DICE, "target": target, "sides": sides}, DICE)
    question = _generate("medium")
    assert len(asked) == 2
    assert question["correct_answer"] == "1/3"


def test_counts_in_the_text_must_be_the_counts_scored(replies):
    """Shown 6 red, scored 5: 5/11 would mark a correct 1/2 wrong."""
    asked = replies({**BAG, "items": {"red": "5", "blue": "4", "green": "2"}}, BAG)
    assert _generate("easy")["correct_answer"] == "1/2"
    assert len(asked) == 2


def test_a_reply_that_never_agrees_is_refused_not_served(replies):
    replies({**BAG, "target": "purple"})
    with pytest.raises(ValueError):
        _generate("easy")


@pytest.mark.parametrize("items", [
    {"red": "6", "blue": "4.5"}, {"red": "0", "blue": "0"}, {"red": "6", "blue": "-2"},
])
def test_counts_must_be_whole_and_add_up_to_something(items):
    """Checked on the parse itself: the text comparison would also catch these fixtures."""
    assert isinstance(prob._scored_data({**BAG, "items": items}), str)
    assert prob._scored_data(BAG) == ({"red": 6, "blue": 4, "green": 2}, "red")
