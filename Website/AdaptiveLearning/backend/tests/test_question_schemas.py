"""The schemas the Claude branch constrains its replies with.

`extract_json` exists because `llama3.1:8b` wraps JSON in fences and preamble.
It was carried to Claude untouched, so the retry loop still absorbed malformed
JSON from a provider that can be told not to produce any.

The tests that matter here are the two that cannot be checked by reading:
whether a schema is accepted by the API at all, and whether every generator
sends one. The first is a real constraint with two teeth -- both were found by
sending requests, not by reasoning -- and a violation is a 400 on a student's
first question, which is why it is pinned locally.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import question_schemas as qs  # noqa: E402
import angle_solvers  # noqa: E402
import geometry_solvers  # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# One builder call per topic, covering every distinct shape the module emits.
ALL_SCHEMAS = [
    ("algebra", qs.token_list("algebra")),
    ("rationals", qs.token_list("rationals")),
    ("mean", qs.dataset("mean")),
    ("median", qs.dataset("median")),
    ("mode", qs.dataset("mode")),
    ("ordering", qs.ordering()),
    ("expressions", qs.expressions("simplify")),
    ("geometry", qs.geometry("rectangle_area")),
    ("geometry-missing-side", qs.geometry("rect_area_missing_side")),
    ("angles", qs.angles("triangle_sum")),
    ("probability-dice", qs.probability("dice")),
]


def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


@pytest.mark.parametrize("name,schema", ALL_SCHEMAS, ids=[n for n, _ in ALL_SCHEMAS])
def test_no_schema_uses_a_keyword_the_api_refuses(name, schema):
    """Two restrictions, both found by sending a request and reading the 400.

        For 'array' type, 'minItems' values other than 0 or 1 are not supported
        For 'object' type, 'additionalProperties: object' is not supported.
            Please set 'additionalProperties' to false

    Neither is guessable from the JSON Schema spec -- both are valid schema and
    refused by this endpoint. A violation is not a degraded question, it is a
    400 on the first question of whatever topic carries it, so it is worth
    catching here rather than in production. This is the local half of a
    property whose real check is a live call; it cannot confirm a schema is
    *accepted*, only that it avoids the two things known to be rejected.
    """
    for node in _walk(schema):
        if node.get("type") == "array" and "minItems" in node:
            assert node["minItems"] in (0, 1), (
                f"{name}: minItems={node['minItems']}; the API allows only 0 or 1")
        if node.get("type") == "object":
            extra = node.get("additionalProperties")
            assert extra is False, (
                f"{name}: additionalProperties={extra!r}; the API requires False")


def test_every_generator_sends_a_schema():
    """Exhaustiveness, like `_MODE_AWARE` and the close-site tests.

    A generator added without one keeps the `extract_json` path silently -- it
    would work, and nothing would say that one topic in eleven is still
    absorbing malformed JSON on a provider that need not produce any.
    """
    missing = []
    for filename in sorted(os.listdir(BACKEND)):
        if not (filename.startswith("LLM_") and filename.endswith("_generation.py")):
            continue
        source = open(os.path.join(BACKEND, filename), encoding="utf-8").read()
        if "schema=question_schemas." not in source:
            missing.append(filename)
    assert not missing, (
        f"these generators call generate_text with no schema: {missing}. "
        "A topic without one keeps the extract_json path on the Claude branch.")


def test_geometrys_variable_keys_come_from_the_solvers_table():
    """Derived from `geometry_solvers.SCENARIO_VARS`, not restated.

    The schema tells the model which keys to produce and the solver indexes
    them; a second copy is how those two drift and the model is told to emit
    something the solver cannot read. Haiku has already returned a
    `rect_perimeter_missing_side` carrying `rect_area_missing_side`'s keys.
    """
    for scenario in geometry_solvers.SOLVABLE_SCENARIOS:
        variables = qs.geometry(scenario)["properties"]["variables"]
        assert set(variables["properties"]) == set(
            geometry_solvers.SCENARIO_VARS[scenario]), scenario
        assert set(variables["required"]) == set(
            geometry_solvers.SCENARIO_VARS[scenario]), scenario


def test_the_angle_arity_is_deliberately_not_in_the_schema():
    """`SCENARIO_ARITY` cannot be expressed -- `minItems` above 1 is refused.

    Pinned because the gap is invisible: every other shape here is constrained,
    so a reader would reasonably assume this one is, and stop checking the
    arity at runtime. It is still checked there, and that check is the only
    one.
    """
    for scenario in angle_solvers.SOLVABLE_SCENARIOS:
        variables = qs.angles(scenario)["properties"]["variables"]
        assert "minItems" not in variables and "maxItems" not in variables
    assert angle_solvers.SCENARIO_ARITY["triangle_sum"] == 2, (
        "the arity this schema cannot express still has to exist somewhere")


def test_the_two_bag_scenarios_get_no_schema_rather_than_a_permissive_one():
    """`items` maps invented category names to counts, so the object must stay
    open, and the API refuses an open object.

    `None` rather than a schema with `items` dropped: a schema listing every
    key *except* the one carrying the data would be accepted, would constrain
    nothing that matters, and would read as covered.
    """
    assert qs.probability("probability_of") is None
    assert qs.probability("not_probability_of") is None
    assert qs.probability("dice") is not None


@pytest.mark.parametrize("module", ["LLM_expressions_generation.py",
                                   "LLM_probability_generation.py"])
def test_the_new_name_maps_match_the_blocks_they_send(module):
    """Both prompts name the wanted scenario by *number* and the reply must
    carry the matching *name*, which the solver then dispatches on. The map is
    the only thing tying those together, so a wrong entry pins the schema's
    enum to one scenario while the prompt asks for another -- every reply
    refused, on a topic that reads as the model simply failing.

    Compared against the module's own `_SCENARIO_NAMES`, never against a copy
    written here. The first version of this test did the latter -- blocks
    against a literal in the test file -- so it compared two things the map
    does not appear in, and passed unchanged with `3: "dice"` mutated to
    `3: "probability_of"`. It asserted the prompt was self-consistent, which
    nothing doubted.
    """
    import importlib
    live = importlib.import_module(module[:-3])._SCENARIO_NAMES

    source = open(os.path.join(BACKEND, module), encoding="utf-8").read()
    from_blocks = {int(n): name for n, name in
                   re.findall(r'^\s*(\d+): """Scenario \d+: ([a-z_]+)',
                              source, re.M)}
    if not from_blocks:  # probability keeps its blocks in one prompt string
        from_blocks = {i: name for i, name in
                       enumerate(re.findall(r'"scenario": "([a-z_]+)"', source), 1)}
    assert from_blocks, "the block regex matched nothing -- this test is inert"
    assert from_blocks == live


def test_every_generator_stores_its_own_topic_name_not_the_models():
    """`question_topic` becomes `questions.subject`, which
    `record_topic_attempt` joins against `math_topics.topic_name`.

    So a wrong value is not a cosmetic label: it credits the student's answer
    to a different topic in `user_math_performance`, which is what the adaptive
    engine reads to choose what to serve next. CLAUDE.md already states the
    rule one layer down -- the topic is derived from the question row, never
    from the caller, or a page could credit one subject for work done in
    another. Letting the model name it is the same hazard one layer up.

    `rationals` returned `question_data["question_topic"]` and its prompt named
    "algebra" in prose and "rations" in the JSON example. Measured 3 of 3
    against Haiku: every fractions question was stored as algebra.

    Two halves, and the literal is the load-bearing one -- a generator reading
    the model's value could still pass a membership check on any given run.
    """
    import ast

    import LLM_topic_decider

    checked = []
    for filename in sorted(os.listdir(BACKEND)):
        if not (filename.startswith("LLM_") and filename.endswith("_generation.py")):
            continue
        # utf-8-sig: these files carry a BOM, and `ast.parse` rejects it as an
        # invalid non-printable character rather than skipping it.
        tree = ast.parse(
            open(os.path.join(BACKEND, filename), encoding="utf-8-sig").read())
        found = [
            value
            for node in ast.walk(tree)
            if isinstance(node, ast.Dict)
            for key, value in zip(node.keys, node.values)
            if isinstance(key, ast.Constant) and key.value == "question_topic"
            and not isinstance(value, ast.Constant)  # a literal is fine; this is not
        ]
        assert not found, (
            f"{filename} builds question_topic from an expression rather than a "
            "literal. It reaches questions.subject, which record_topic_attempt "
            "joins on -- the model naming it credits the answer elsewhere.")

        returned = [
            value.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)
            for key, value in zip(node.value.keys, node.value.values)
            if isinstance(key, ast.Constant) and key.value == "question_topic"
            and isinstance(value, ast.Constant)
        ]
        assert returned, f"{filename} returns no question_topic at all"
        for topic in returned:
            assert topic in LLM_topic_decider.ALL_TOPICS, (
                f"{filename} returns subject {topic!r}, which is not one of "
                f"ALL_TOPICS -- record_topic_attempt's join finds no row and "
                "the attempt is attributed to nothing, silently.")
        checked.append(filename)

    assert len(checked) == len(LLM_topic_decider.ALL_TOPICS), (
        f"a generator file per topic; found {checked}")
