"""SOLVABLE_SCENARIOS, SCENARIO_VARS and the prompt blocks must match the dispatch `match`."""
import os
import re

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import LLM_geometry_generation as geo  # noqa: E402

# The dispatch lives here so the bounded worker can import it without supabase.
import geometry_solvers  # noqa: E402

_SOURCE = open(geometry_solvers.__file__, encoding="utf-8").read()
# Indent-agnostic, and `\s*` before the colon: some cases read `case "name" :`.
_DISPATCHED = re.findall(r'^\s+case "([a-z_]+)"\s*:', _SOURCE, re.M)


def test_the_match_actually_has_branches_to_find():
    """A regex matching nothing would make the tests below vacuous."""
    assert len(_DISPATCHED) >= 18


def test_every_dispatched_scenario_is_accepted():
    """A rejected valid scenario reads as three retries and a ValueError."""
    assert not set(_DISPATCHED) - geo.SOLVABLE_SCENARIOS


def test_every_accepted_scenario_can_be_dispatched():
    """The direction that produces the UnboundLocalError."""
    assert not geo.SOLVABLE_SCENARIOS - set(_DISPATCHED)


# `[ \t]*` before the newline: `case "rect_volume": ` has a trailing space.
def _case_bodies(source):
    """scenario name -> the lines of its `case` block, at whatever indent."""
    bodies = {}
    for match in re.finditer(r'^([ \t]+)case "([a-z_]+)"\s*:[ \t]*\n',
                             source, re.M):
        indent, name = match.group(1), match.group(2)
        lines = []
        for line in source[match.end():].splitlines(keepends=True):
            # A blank line or a shallower indent ends the block.
            if not line.strip() or not line.startswith(indent + " "):
                break
            lines.append(line)
        bodies[name] = "".join(lines)
    return bodies


_CASE_BODIES = _case_bodies(_SOURCE)


def test_every_scenario_body_was_found():
    """Guards the regex above, not the code -- see the comment on it."""
    assert set(_CASE_BODIES) == set(_DISPATCHED)


def test_the_required_variables_match_what_each_solver_indexes():
    """A valid scenario with the wrong variables is a KeyError, not a retry."""
    for name, body in _CASE_BODIES.items():
        indexed = set(re.findall(r'vars\["([a-z0-9_]+)"\]', body))
        assert set(geo.SCENARIO_VARS[name]) == indexed, name


# The prompt blocks are in the generator module, not `_SOURCE`.
_PROMPT_SOURCE = open(geo.__file__, encoding="utf-8").read()
_BLOCK_NAMES = dict(re.findall(
    r'^    (\d+): """.*?"scenario": "([a-z_]+)"', _PROMPT_SOURCE, re.M | re.S))


def test_every_block_name_was_found():
    """Guards the regex, like the one above it."""
    assert len(_BLOCK_NAMES) == len(geo.SCENARIO_BLOCKS)


def test_each_blocks_name_is_a_scenario_the_dispatch_handles():
    for number, name in _BLOCK_NAMES.items():
        assert name in geo.SCENARIO_VARS, f"block {number} asks for {name}"
        assert geo._SCENARIO_NAMES[int(number)] == name


def test_the_prompt_states_the_scenario_and_its_keys():
    """The block alone let the model blend two scenarios' keys."""
    for number, name in geo._SCENARIO_NAMES.items():
        prompt = geo._geometry_prompt(number)
        assert f'"scenario" MUST be exactly "{name}"' in prompt
        for key in geo.SCENARIO_VARS[name]:
            assert f'"{key}"' in prompt


@pytest.mark.parametrize("scenario,variables,why", [
    ("cube_volume", {"side": "1e200"}, "1e600 -> float() -> inf"),
    ("cube_volume", {"side": "!!"}, "sympify refuses the value"),
    ("pythagorean", {"a": "3"}, "a key the solver indexes is absent"),
    ("circle_area", {"radius": "x"}, "solves to a symbol, not a number"),
])
def test_an_unsolvable_scenario_is_a_retry_not_a_raise(scenario, variables, why):
    assert geo._solve_scenario(scenario, variables, 1) is None, why


def test_an_ordinary_scenario_still_solves():
    assert geo._solve_scenario("cube_volume", {"side": "3"}, 1) == 27.0


def _block_example(block):
    """The JSON example a scenario block shows the model."""
    import json
    return json.loads(block[block.index("{"):block.rindex("}") + 1])


@pytest.mark.parametrize("number", sorted(geo.SCENARIO_BLOCKS))
def test_every_blocks_own_example_solves_to_a_positive_measure(number):
    """`circle_area_missing_side` is quadratic; the root taken must be the positive one."""
    example = _block_example(geo.SCENARIO_BLOCKS[number])
    value, reason = geometry_solvers.solve_scenario(example["scenario"],
                                                    example["variables"])
    assert reason is None, reason
    assert value > 0, f"{example['scenario']} {example['variables']} -> {value}"


@pytest.mark.parametrize("scenario,variables,why", [
    ("rect_perimeter_missing_side", {"perimeter": "10", "known_side": "8"},
     "positive inputs that leave -3 for the other side"),
    ("rectangle_area", {"length": "-5", "width": "3"}, "a negative side"),
    ("rectangle_area", {"length": "-5", "width": "-3"},
     "two negative sides whose product is positive"),
    ("cube_volume", {"side": "0"}, "a zero side"),
    ("triangle_perimeter_missing_side", {"perimeter": "20", "s1": "2", "s2": "3"},
     "a third side of 15, longer than the other two together"),
    ("triangle_perimeter", {"s1": "2", "s2": "3", "s3": "5"},
     "sides that lie flat: 2 + 3 is exactly 5"),
])
def test_a_measure_that_is_no_figure_is_refused(scenario, variables, why):
    """Every input and answer is a length, area or volume of a real figure."""
    value, reason = geometry_solvers.solve_scenario(scenario, variables)
    assert value is None and reason, why


def test_a_missing_radius_is_the_positive_root():
    assert geometry_solvers.solve_scenario(
        "circle_area_missing_side", {"area": "78.5"}) == (5.0, None)
