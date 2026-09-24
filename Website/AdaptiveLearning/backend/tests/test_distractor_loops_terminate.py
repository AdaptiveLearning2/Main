"""Every distractor generator terminates on a dataset that cannot supply three."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import LLM_median_generation as median_gen  # noqa: E402
import LLM_mode_generation as mode_gen  # noqa: E402
import LLM_ordering_generation as ordering_gen  # noqa: E402


@pytest.mark.parametrize("solution,values,why", [
    (7.0, [5.0, 7.0, 9.0], "the measured hang: 3 values, 2 of them non-median"),
    (5.0, [5.0, 5.0, 5.0], "every value is the median"),
    (3.0, [1.0, 2.0, 3.0, 4.0, 5.0], "ample -- must not be broken by the fix"),
])
def test_median_distractors_terminate(solution, values, why):
    answers = median_gen.generate_incorrect_answers(solution, values)
    assert len(answers) == 3, why
    assert solution not in answers


@pytest.mark.parametrize("solution,values,why", [
    (4.0, [4.0, 4.0, 9.0], "one non-modal value"),
    (4.0, [4.0, 4.0], "no non-modal value at all"),
    (4.0, [4.0, 4.0, 9.0, 2.0, 7.0], "ample"),
])
def test_mode_single_distractors_terminate(solution, values, why):
    answers = mode_gen.generate_incorrect_answers(solution, values)
    assert len(answers) == 3, why
    assert str(solution) not in answers


@pytest.mark.parametrize("solution,values,why", [
    ([4.0, 9.0], [4.0, 9.0], "the nested loop's case: no spare distinct value"),
    ([4.0, 9.0], [4.0, 9.0, 2.0], "one spare, still short of three"),
    ([4.0, 9.0], [4.0, 9.0, 2.0, 7.0, 1.0], "ample"),
])
def test_mode_multiple_distractors_terminate(solution, values, why):
    answers = mode_gen.generate_incorrect_answers(solution, values)
    assert len(answers) == 3, why
    assert all(isinstance(a, list) for a in answers), \
        "a multi-mode answer is a list; a distractor has to be the same shape"


@pytest.mark.parametrize("solution,expected,why", [
    (["a", "b"], 1, "two values have exactly one wrong order"),
    (["a", "b", "c"], 3, "six permutations, five wrong"),
    (["a", "b", "c", "d"], 3, "ample"),
])
def test_ordering_distractors_terminate(solution, expected, why):
    answers = ordering_gen.shuffle_incorrect_answers(solution)
    assert len(answers) == expected, why
    assert solution not in answers


def test_a_two_value_ordering_question_is_rejected_upstream(monkeypatch):
    """One distractor is not a usable question, so the retry loop refuses the dataset first."""
    import json
    import llm_client
    monkeypatch.setattr(ordering_gen.lesson_plan_context, "append_lesson_context", lambda p, t, b: p)
    monkeypatch.setattr(llm_client, "generate_text", lambda *a, **k: json.dumps({"values": ["1", "2"]}))
    with pytest.raises(ValueError, match="after retries"):
        ordering_gen.generate_ordering_question([], [], "medium", "7th Grade")
