"""The schemas the Claude branch constrains its replies with. See docs/question-generation.md."""
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
    ("angles-algebra", qs.angles("algebra_complementary")),
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
    """The API 400s on minItems > 1 and non-false additionalProperties (valid JSON Schema, refused)."""
    for node in _walk(schema):
        if node.get("type") == "array" and "minItems" in node:
            assert node["minItems"] in (0, 1), (
                f"{name}: minItems={node['minItems']}; the API allows only 0 or 1")
        if node.get("type") == "object":
            extra = node.get("additionalProperties")
            assert extra is False, (
                f"{name}: additionalProperties={extra!r}; the API requires False")


def test_every_generator_sends_a_schema():
    """Exhaustive: a generator without one silently keeps the `extract_json` path."""
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
    """Derived from `geometry_solvers.SCENARIO_VARS`, not restated, so schema and solver cannot drift."""
    for scenario in geometry_solvers.SOLVABLE_SCENARIOS:
        variables = qs.geometry(scenario)["properties"]["variables"]
        assert set(variables["properties"]) == set(
            geometry_solvers.SCENARIO_VARS[scenario]), scenario
        assert set(variables["required"]) == set(
            geometry_solvers.SCENARIO_VARS[scenario]), scenario


def test_the_angle_arity_is_deliberately_not_in_the_schema():
    """`minItems` above 1 is refused, so the runtime arity check is the only one."""
    for scenario in angle_solvers.SOLVABLE_SCENARIOS:
        variables = qs.angles(scenario)["properties"]["variables"]
        assert "minItems" not in variables and "maxItems" not in variables
    assert angle_solvers.SCENARIO_ARITY["triangle_sum"] == 2, (
        "the arity this schema cannot express still has to exist somewhere")


def test_every_angle_blocks_own_example_is_allowed_by_its_schema_and_solves():
    """The schema must admit the reply the prompt asks for; read from the blocks, so new ones are covered."""
    import json
    os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
    os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")
    import LLM_angle_relationship_generation as angles

    for number, block in angles.SCENARIO_BLOCKS.items():
        example = json.loads(block[block.index("{"):block.rindex("}") + 1])
        name = example["scenario"]
        items = qs.angles(name)["properties"]["variables"]["items"]
        for value in example["variables"]:
            pattern = items.get("pattern")
            assert pattern is None or re.fullmatch(pattern, value), (
                f"block {number} ({name}): the schema refuses its own example "
                f"{value!r}")
        value, reason = angle_solvers.solve_scenario(name, example["variables"])
        assert reason is None, f"block {number} ({name}): {reason}"


def test_the_two_bag_scenarios_get_no_schema_rather_than_a_permissive_one():
    """`items` needs an open object, which the API refuses; a schema without it would read as covered."""
    assert qs.probability("probability_of") is None
    assert qs.probability("not_probability_of") is None
    assert qs.probability("dice") is not None


@pytest.mark.parametrize("module", ["LLM_expressions_generation.py",
                                   "LLM_probability_generation.py"])
def test_the_new_name_maps_match_the_blocks_they_send(module):
    """The prompt names a scenario by number; the map ties it to the name. Compared to the live map."""
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
    """`question_topic` is what `record_topic_attempt` joins on; the model must not name it.

    The literal half is load-bearing: a model-read value can pass the membership check on any run.
    """
    import ast

    import LLM_topic_decider

    checked = []
    for filename in sorted(os.listdir(BACKEND)):
        if not (filename.startswith("LLM_") and filename.endswith("_generation.py")):
            continue
        # utf-8-sig: these files carry a BOM, which `ast.parse` rejects.
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
