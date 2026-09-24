"""An evaluation must come out as a number; only `simplify` may answer in x."""
import json
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import llm_client  # noqa: E402
import lesson_plan_context  # noqa: E402
import LLM_expressions_generation as expr_gen  # noqa: E402

EVALUATE = {"question_text": "Evaluate (4+6)*3-5.", "question_topic": "expressions",
            "scenario": "evaluate", "variables": ["(", "4", "+", "6", ")", "*", "3", "-", "5"]}
ORDER = {**EVALUATE, "scenario": "order_of_operations"}
WITH_X = ["3x", "+", "2"]


@pytest.fixture
def replies(monkeypatch):
    """Answers each attempt with the next payload; returns how many were asked for."""
    monkeypatch.setattr(lesson_plan_context, "append_lesson_context", lambda p, t, b: p)
    asked = []

    def _use(scenario, *payloads):
        monkeypatch.setattr(expr_gen, "_pick_scenario", lambda band: scenario)

        def generate(*_a, **_k):
            asked.append(1)
            return json.dumps(payloads[min(len(asked), len(payloads)) - 1])
        monkeypatch.setattr(llm_client, "generate_text", generate)
        return asked
    return _use


def _generate():
    return expr_gen.generate_expression_question([], [], "medium", "7th Grade")


@pytest.mark.parametrize("scenario,good", [(1, EVALUATE), (2, ORDER)])
def test_an_evaluation_that_leaves_a_variable_is_retried(replies, scenario, good):
    """"3*x + 2" was served as the answer, the only option offered."""
    left_x = {**good, "question_text": "Evaluate 3x + 2 when x = 4.", "variables": WITH_X}
    asked = replies(scenario, left_x, good)
    question = _generate()
    assert len(asked) == 2
    assert question["correct_answer"] == "25"
    assert len(question["answer_options"]) == 4


def test_simplify_still_answers_in_x(replies):
    simplify = {"question_text": "Simplify 2x+3x.", "question_topic": "expressions",
                "scenario": "simplify", "variables": ["2x", "+", "3x"]}
    asked = replies(3, simplify)
    question = _generate()
    assert len(asked) == 1
    assert "x" in question["correct_answer"]
