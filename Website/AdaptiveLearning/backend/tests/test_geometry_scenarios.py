"""The geometry scenario whitelist has to match the dispatch it guards.

`generate_geometry_question` validates the model's `scenario` against
`SOLVABLE_SCENARIOS` inside the retry loop, because the `match` that dispatches
on it has no `case _`: an unrecognised name leaves `solution` unbound and
raises `UnboundLocalError` from a line that reads like arithmetic -- a 500 to a
student where a retry costs nothing.

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

import LLM_geometry_generation as geo  # noqa: E402

_SOURCE = open(geo.__file__, encoding="utf-8").read()
# `\s*` before the colon is load-bearing -- see the docstring.
_DISPATCHED = re.findall(r'^        case "([a-z_]+)"\s*:', _SOURCE, re.M)


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
_CASE_BODIES = dict(re.findall(
    r'^        case "([a-z_]+)"\s*:[ \t]*\n((?:^            .*\n)+)',
    _SOURCE, re.M))


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
