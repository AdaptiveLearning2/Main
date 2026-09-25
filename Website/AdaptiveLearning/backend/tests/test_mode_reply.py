"""A mode question is served only for a dataset that has the mode, or modes, its tier asks for."""
import json
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import llm_client  # noqa: E402
import lesson_plan_context  # noqa: E402
import LLM_mode_generation as mode_gen  # noqa: E402


def _reply(values):
    return {"question_text": f"The values were: {', '.join(values)}. What is the mode?",
            "question_topic": "mode", "variables": values}


GOOD = _reply(["4", "8", "4", "2", "10"])


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
    return mode_gen.generate_mode_question([], [], difficulty, "7th Grade")


@pytest.mark.parametrize("difficulty,values", [
    ("easy", ["3", "5", "2", "8"]),                     # nothing repeats
    ("hard", ["2", "2", "5", "5", "7", "7"]),           # every value tied
    ("hard", ["2", "2", "5", "5"]),                     # tied, and within the tier's two
    ("medium", ["2", "2", "5", "5", "7", "9"]),         # two modes on a single-mode tier
    ("hard", ["2", "2", "5", "5", "7", "7", "9"]),      # three modes
])
def test_a_dataset_without_the_tiers_mode_is_retried_not_served(replies, difficulty, values):
    """It was served with the whole dataset, or every tied value, as 'the mode'."""
    asked = replies(_reply(values), GOOD)
    question = _generate(difficulty)
    assert len(asked) == 2
    assert question["correct_answer"] == ["4"]


def test_one_repeated_value_is_its_own_mode(replies):
    """[5, 5, 5] was refused as "no mode"."""
    asked = replies(_reply(["5", "5", "5"]))
    question = _generate("easy")
    assert len(asked) == 1 and question["correct_answer"] == ["5"]
    assert len(set(map(str, question["answer_options"]))) == 4


def test_a_single_value_is_retried():
    assert mode_gen._mode_problem([5.0], "easy") is not None


def test_two_modes_are_served_on_a_hard_tier(replies):
    replies(_reply(["2", "2", "5", "5", "7", "9"]))
    assert sorted(_generate("hard")["correct_answer"]) == ["2", "5"]


def test_a_single_mode_is_served_on_every_tier(replies):
    for difficulty in ("easy", "medium", "hard"):
        replies(GOOD)
        assert _generate(difficulty)["correct_answer"] == ["4"]


def test_the_prompts_own_example_is_valid_json_matching_its_text():
    """The example the model copies was malformed, and its values differed from its text."""
    example = json.loads(mode_gen.extract_json(mode_gen.mode_prompt))
    shown = example["question_text"].split(":", 1)[1].split(".")[0].replace(" ", "").split(",")
    assert shown == example["variables"]
    assert mode_gen._mode_problem([float(v) for v in shown], "easy") is None
