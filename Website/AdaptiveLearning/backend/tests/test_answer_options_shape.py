"""Exactly one option must match `correct_answer` the way the client compares.

`Adaptive.jsx` decides correctness with

    JSON.stringify(answer_options[selected]) === JSON.stringify(correct_answer)

which is type-sensitive: `24` and `"24"` do not match. So the invariant every
topic has to hold is not "the correct answer is in the list" but "exactly one
option is JSON-identical to it".

It held before this test existed, and by accident: `correct_answer` is the same
object that goes into the list, so it matched itself whatever its type.
`geometry` was shipping `['13.93', 27.86, 5.0, 41.79]` -- the answer a string
among floats -- and React renders 13.93 and "13.93" identically, so an edit
that re-serialised the options would have marked every geometry answer wrong
with nothing on screen to show why.

Two matches is the other failure, and the worse one: two options that a student
cannot tell apart, one scored right and one wrong.
"""
import json
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import llm_client  # noqa: E402

from test_one_solve_per_attempt import CASES  # noqa: E402


@pytest.mark.parametrize("name,module,entry,payload",
                         CASES, ids=[c[0] for c in CASES])
def test_exactly_one_option_is_json_identical_to_the_correct_answer(
        name, module, entry, payload, monkeypatch):
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

    question = getattr(module, entry)([], [], "medium", "8th Grade")

    # Round-tripped, because the client compares what came over the wire.
    wire = json.loads(json.dumps(question))
    options, correct = wire["answer_options"], wire["correct_answer"]
    matches = [o for o in options if json.dumps(o) == json.dumps(correct)]

    assert len(matches) == 1, (
        f"{name}: {len(matches)} of {options} match {correct!r} under the "
        f"comparison Adaptive.jsx uses")


@pytest.mark.parametrize("name,module,entry,payload",
                         CASES, ids=[c[0] for c in CASES])
def test_no_two_options_render_the_same(name, module, entry, payload, monkeypatch):
    """Distinct in JSON is not enough: `24` and `"24"` are different options
    and the same thing on screen. React renders both as `24`."""
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

    question = getattr(module, entry)([], [], "medium", "8th Grade")
    options = json.loads(json.dumps(question))["answer_options"]

    def rendered(value):
        # What React puts on screen for a JSON scalar or list of them.
        if isinstance(value, list):
            return ", ".join(rendered(v) for v in value)
        if isinstance(value, bool) or value is None:
            return str(value)
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    shown = [rendered(o) for o in options]
    assert len(set(shown)) == len(shown), \
        f"{name}: two options look identical on screen: {shown}"


@pytest.mark.parametrize("name,module,entry,payload",
                         CASES, ids=[c[0] for c in CASES])
def test_all_options_share_one_type(name, module, entry, payload, monkeypatch):
    """The consistency the two tests above do not pin.

    They assert what the client needs -- one JSON-identical match, no two
    options rendering alike -- and both held against geometry's mixed
    `['13.93', 27.86, 5.0, 41.79]`, correctly, because that list was not
    broken. So a revert to `round(float(ans), 2)` would keep them green, which
    makes them the wrong guard for the change they shipped with.

    This is the right one. It fails on a mixed list directly, and it states the
    property that made the mixed list worth changing: the correct option
    matched only because it was the same object that went into the list, and
    one type throughout is what turns that from an accident into a
    construction.
    """
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

    question = getattr(module, entry)([], [], "medium", "8th Grade")
    options = json.loads(json.dumps(question))["answer_options"]

    types = {type(o).__name__ for o in options}
    assert len(types) == 1, f"{name}: options mix {sorted(types)}: {options}"
