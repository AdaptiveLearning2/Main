"""One trip to the solve subprocess per generation attempt.

`LLM_mean_generation` had its guard block pasted twice, so every attempt
spawned two subprocesses and threw the first result away. Measured at 0.77s for
one call against 1.47s for two -- on the inline path, which is every question.

The arithmetic behind `SOLVE_TIMEOUT` is what makes that more than waste. That
value is sized so a hung reply costs about three attempts' worth of the budget;
a second call per attempt doubles the worst case, so mean's was 18s where every
other topic's was 9s. A duplicated call is not a slow path, it is a different
bound.

Counting at runtime rather than grepping: `probability` legitimately has two
call sites on mutually exclusive branches, so a source count reports it as the
same defect and reports nothing at all about a duplicate inside one branch.
"""
import json
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import llm_client  # noqa: E402
import safe_solve  # noqa: E402

import LLM_mean_generation as mean_gen  # noqa: E402
import LLM_median_generation as median_gen  # noqa: E402
import LLM_mode_generation as mode_gen  # noqa: E402
import LLM_ordering_generation as ordering_gen  # noqa: E402
import LLM_probability_generation as prob_gen  # noqa: E402

_VALUES = ["4", "8", "6", "2", "10"]

CASES = [
    ("mean", mean_gen, "generate_mean_question",
     {"question_text": "The values were: 4, 8, 6, 2, 10. What is the mean?",
      "question_topic": "mean", "variables": _VALUES}),
    ("median", median_gen, "generate_median_question",
     {"question_text": "The values were: 4, 8, 6, 2, 10. What is the median?",
      "question_topic": "median", "variables": _VALUES}),
    ("mode", mode_gen, "generate_mode_question",
     {"question_text": "The values were: 4, 8, 4, 2, 10. What is the mode?",
      "question_topic": "mode", "variables": ["4", "8", "4", "2", "10"]}),
    ("ordering", ordering_gen, "generate_ordering_question",
     {"question_text": "Order from least to greatest: 4, 8, 6, 2, 10.",
      "question_topic": "ordering", "values": _VALUES,
      "direction": "least_to_greatest"}),
    ("probability", prob_gen, "generate_probability_question",
     {"question_text": "A bag holds 6 red, 4 blue and 2 green marbles. "
                       "What is the probability of drawing a red marble?",
      "question_topic": "probability", "scenario": "probability_of",
      "items": {"red": "6", "blue": "4", "green": "2"}, "target": "red"}),
]


@pytest.mark.parametrize("name,module,entry,payload",
                         CASES, ids=[c[0] for c in CASES])
def test_a_successful_attempt_makes_exactly_one_worker_call(
        name, module, entry, payload, monkeypatch):
    calls = []
    real = safe_solve.safe_sympify_values

    def counting(values, timeout=None):
        calls.append(list(values))
        return real(values, timeout)

    monkeypatch.setattr(safe_solve, "safe_sympify_values", counting)
    monkeypatch.setattr(llm_client, "generate_text",
                        lambda *a, **k: json.dumps(payload))
    monkeypatch.setattr(module.lesson_plan_context, "append_lesson_context",
                        lambda prompt, topic, band: prompt)
    if hasattr(module, "grade_appropriateness"):
        monkeypatch.setattr(module.grade_appropriateness, "refuse",
                            lambda *a, **k: False)
    if hasattr(module, "question_consistency"):
        monkeypatch.setattr(module.question_consistency, "dataset_mismatch",
                            lambda *a, **k: None)
        monkeypatch.setattr(module.question_consistency, "negation_mismatch",
                            lambda *a, **k: None)

    getattr(module, entry)([], [], "medium", "8th Grade")

    assert len(calls) == 1, (
        f"{name} made {len(calls)} worker calls for one attempt: {calls}")
