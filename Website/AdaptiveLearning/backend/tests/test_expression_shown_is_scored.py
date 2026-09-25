"""Rationals and algebra score the expression the student reads, not a different one."""
import json
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import llm_client  # noqa: E402
import lesson_plan_context  # noqa: E402
import LLM_algebra_generation as algebra_gen  # noqa: E402
import LLM_rationals_generation as rationals_gen  # noqa: E402

RATIONAL = {"question_text": "Solve 3/4 + 1/8", "question_topic": "rationals",
            "variables": ["3/4", "+", "1/8"]}
EQUATION = {"question_text": "Solve for x: 3x + 5 = 20", "question_topic": "algebra",
            "variables": ["3x", "+", "5", "=", "20"]}

CASES = [
    # Shown 3/4 + 1/8, scored 3/4 - 1/8: 5/8 marked a correct 7/8 wrong.
    (rationals_gen.generate_rational_question, RATIONAL, {**RATIONAL, "variables": ["3/4", "-", "1/8"]}, "7/8"),
    # Shown = 20, scored = 26: served 7 for a question whose answer is 5.
    (algebra_gen.generate_algebra_question, EQUATION, {**EQUATION, "variables": ["3x", "+", "5", "=", "26"]}, "5"),
]


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


@pytest.mark.parametrize("generate,good,mismatched,answer", CASES,
                         ids=["rationals", "algebra"])
def test_a_reply_scoring_a_different_expression_is_retried(replies, generate, good, mismatched, answer):
    asked = replies(mismatched, good)
    question = generate([], [], "medium", "7th Grade")
    assert len(asked) == 2
    assert str(question["correct_answer"]) == answer
