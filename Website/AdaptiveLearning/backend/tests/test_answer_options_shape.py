"""Exactly one option is JSON-identical to `correct_answer`, as `Adaptive.jsx` compares."""
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
    # The stub answers `evaluate`; pin the otherwise random scenario pick.
    if name == "expressions":
        monkeypatch.setattr(module, "_pick_scenario", lambda band: 1)
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
    """`24` and `"24"` are distinct in JSON and identical on screen."""
    monkeypatch.setattr(llm_client, "generate_text",
                        lambda *a, **k: json.dumps(payload))
    # The stub answers `evaluate`; pin the otherwise random scenario pick.
    if name == "expressions":
        monkeypatch.setattr(module, "_pick_scenario", lambda band: 1)
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
    """A mixed-type list passes the two tests above; one type makes the match a construction."""
    monkeypatch.setattr(llm_client, "generate_text",
                        lambda *a, **k: json.dumps(payload))
    # The stub answers `evaluate`; pin the otherwise random scenario pick.
    if name == "expressions":
        monkeypatch.setattr(module, "_pick_scenario", lambda band: 1)
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


@pytest.mark.parametrize("name,module,entry,payload",
                         CASES, ids=[c[0] for c in CASES])
def test_no_option_is_identifiable_by_its_formatting(name, module, entry,
                                                     payload, monkeypatch):
    """The correct answer must not be the odd one out to look at (e.g. the only `.0`).

    `format_value` is idempotent, so an option it changes came from a different rule.
    """
    import answer_format

    monkeypatch.setattr(llm_client, "generate_text",
                        lambda *a, **k: json.dumps(payload))
    # The stub answers `evaluate`; pin the otherwise random scenario pick.
    if name == "expressions":
        monkeypatch.setattr(module, "_pick_scenario", lambda band: 1)
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

    def canonical(option):
        if isinstance(option, list):
            return [answer_format.format_value(v) for v in option]
        return answer_format.format_value(option)

    odd = [o for o in options if canonical(o) != o]
    assert not odd, (
        f"{name}: {odd} rendered by a different rule from {options}")


@pytest.mark.parametrize("tokens", [
    ["36", "/", "8", "+", "1"],      # a fraction that terminates: 11/2
    ["1", "/", "3", "+", "1"],       # one that does not: 4/3
    ["0.5", "+", "1"],               # a Float rather than a Rational
])
def test_a_fractional_expression_answer_is_one_of_its_options(tokens, monkeypatch):
    """The shared CASES are whole numbers, which every formatting agrees on."""
    import LLM_expressions_generation as expr_gen

    payload = {"question_text": "Evaluate " + " ".join(tokens) + ".",
               "question_topic": "expressions", "scenario": "evaluate",
               "variables": tokens}
    monkeypatch.setattr(llm_client, "generate_text",
                        lambda *a, **k: json.dumps(payload))
    monkeypatch.setattr(expr_gen, "_pick_scenario", lambda band: 1)
    monkeypatch.setattr(expr_gen.lesson_plan_context, "append_lesson_context",
                        lambda prompt, topic, band: prompt)
    monkeypatch.setattr(expr_gen.grade_appropriateness, "refuse",
                        lambda *a, **k: False)

    question = expr_gen.generate_expression_question([], [], "medium", "8th Grade")

    wire = json.loads(json.dumps(question))
    options, correct = wire["answer_options"], wire["correct_answer"]
    assert [o for o in options if json.dumps(o) == json.dumps(correct)] == [correct], (
        f"{correct!r} is not exactly one of {options}")


@pytest.mark.parametrize("solution,values,why", [
    (5.0, [5.0, 5.0, 5.0, 7.0], "one non-modal value, formatted pool"),
    ([4.0, 9.0], [4.0, 9.0], "bimodal with no spare value"),
])
def test_mode_never_offers_the_answer_as_a_distractor(solution, values, why):
    """The solution must be formatted like the pool, or `"5.0"` fails to exclude `"5"`."""
    import answer_format
    import LLM_mode_generation as mode_gen

    answers = mode_gen.generate_incorrect_answers(solution, values)
    canonical = (answer_format.format_value(solution)
                 if not isinstance(solution, list)
                 else [answer_format.format_value(v) for v in solution])
    assert canonical not in answers, why
