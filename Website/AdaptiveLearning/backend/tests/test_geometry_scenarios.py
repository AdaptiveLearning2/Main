"""The geometry scenario whitelist has to match the dispatch it guards.

`generate_geometry_question` validates the model's `scenario` against
`SOLVABLE_SCENARIOS` inside the retry loop. The `match` that dispatches on it
now has a `case _` as well -- it did not when this was written, and an
unrecognised name left `solution` unbound and raised `UnboundLocalError` from a
line that reads like arithmetic: a 500 to a student where a retry costs
nothing. Both guards stay. The whitelist keeps the rejection near the model's
reply where it can be logged and retried, and the default keeps
`geometry_solvers.solve_scenario` correct as a standalone unit, since the
extraction moved that guarantee into a different file from the check.

Measured against Haiku 4.5 on 2026-08-26, 2 of 3 geometry generations failed
exactly that way. The model answered `circle_missing_radius_circumference`,
a reasonable rearrangement of this file's `circle_circumference_missing_side`.

Two hand-maintained lists is how that crash comes back, so this derives one
from the other. It caught a real error on the first run: the whitelist was
first built with a regex that missed the four `case "name" :` branches written
with a space before the colon, which would have rejected four *valid*
scenarios and retried them into a raised ValueError.
"""
import os
import re

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import LLM_geometry_generation as geo  # noqa: E402

# The dispatch moved to `geometry_solvers` so the bounded worker can import it
# without dragging in supabase, flask and dotenv. These checks follow it: they
# are about the `match`, not about which file it lives in.
import geometry_solvers  # noqa: E402

_SOURCE = open(geometry_solvers.__file__, encoding="utf-8").read()
# Indentation-agnostic on purpose. These patterns were anchored at eight
# spaces, and moving the `match` into `_solve_scenario` -- so the solve could
# run inside the retry loop -- reindented every case and failed both source
# checks at once. That is the guard working rather than a flaw in it: a silent
# zero-match would have made every assertion here vacuous. Matching any indent
# keeps the check alive across a refactor that does not change what is
# dispatched.
_DISPATCHED = re.findall(r'^\s+case "([a-z_]+)"\s*:', _SOURCE, re.M)


def test_the_match_actually_has_branches_to_find():
    """A regex that silently matched nothing would make both tests below
    vacuous -- the failure mode this whole file exists to catch, one level up."""
    assert len(_DISPATCHED) >= 18


def test_every_dispatched_scenario_is_accepted():
    """A valid scenario rejected here is not a crash, it is worse to diagnose:
    three retries and a ValueError, indistinguishable from a model that will
    not follow instructions."""
    assert not set(_DISPATCHED) - geo.SOLVABLE_SCENARIOS


def test_every_accepted_scenario_can_be_dispatched():
    """The direction that produces the UnboundLocalError."""
    assert not geo.SOLVABLE_SCENARIOS - set(_DISPATCHED)


# `[ \t]*` before the newline is the same lesson as `\s*` before the colon a
# few lines up: `case "rect_volume": ` carries a trailing space, and a regex
# without it silently produced 17 scenarios where there are 18 -- a map missing
# exactly one entry, which surfaces as three retries and a ValueError on
# whichever scenario got dropped rather than as anything naming the cause.
def _case_bodies(source):
    """scenario name -> the lines of its `case` block, at whatever indent."""
    bodies = {}
    for match in re.finditer(r'^([ \t]+)case "([a-z_]+)"\s*:[ \t]*\n',
                             source, re.M):
        indent, name = match.group(1), match.group(2)
        lines = []
        for line in source[match.end():].splitlines(keepends=True):
            # A body line is indented further than its own `case`. A blank
            # line belongs to whatever comes after it, so it ends the block.
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
    """A valid scenario carrying the wrong variables is a KeyError out of the
    dispatch -- a 500, not a retry. Haiku answered `pythagorean` with no `b`
    on 2 of 3 upper-band generations, so this is the ordinary case."""
    for name, body in _CASE_BODIES.items():
        indexed = set(re.findall(r'vars\["([a-z0-9_]+)"\]', body))
        assert set(geo.SCENARIO_VARS[name]) == indexed, name


# The prompt blocks stayed behind when the dispatch moved, so this reads the
# generator module rather than `_SOURCE`. Scanning the wrong file would find
# nothing and make both tests below vacuous -- which is why one of them counts.
_PROMPT_SOURCE = open(geo.__file__, encoding="utf-8").read()
_BLOCK_NAMES = dict(re.findall(
    r'^    (\d+): """.*?"scenario": "([a-z_]+)"', _PROMPT_SOURCE, re.M | re.S))


def test_every_block_name_was_found():
    """Guards the regex, like the one above it."""
    assert len(_BLOCK_NAMES) == len(geo.SCENARIO_BLOCKS)


def test_each_blocks_name_is_a_scenario_the_dispatch_handles():
    """`_geometry_prompt` restates the name and required keys as a rule, so a
    block whose example names something the dispatch cannot solve would ask the
    model for a reply the validation then rejects on every attempt -- three
    retries and a ValueError, for a question that was never askable."""
    for number, name in _BLOCK_NAMES.items():
        assert name in geo.SCENARIO_VARS, f"block {number} asks for {name}"
        assert geo._SCENARIO_NAMES[int(number)] == name


def test_the_prompt_states_the_scenario_and_its_keys():
    """Sending the block alone was not enough: Haiku answered
    `rect_perimeter_missing_side` carrying `{"area", "known_side"}` -- two
    scenarios blended, which SCENARIO_VARS refuses and which, before that check
    existed, was a KeyError or a question scored against the wrong formula."""
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
    """`_solve_scenario` was below the `for/else`, so each of these was a 500.

    The non-finite one is the reachable case and the one that was documented
    wrongly: `incorrect_solution_generation` said its ValueError "reaches the
    generator's retry loop", and it could not -- it escaped
    `generate_geometry_question` on attempt 1.
    """
    assert geo._solve_scenario(scenario, variables, 1) is None, why


def test_an_ordinary_scenario_still_solves():
    """The bar every rejection above has to clear."""
    assert geo._solve_scenario("cube_volume", {"side": "3"}, 1) == 27.0
