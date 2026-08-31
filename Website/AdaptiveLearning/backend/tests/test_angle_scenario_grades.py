"""An angle scenario reaches a student no earlier than the grade that teaches it.

`TOPIC_MIN_GRADE` puts `angle_relationships` at 7 for **7.G.5** --
complementary, supplementary and linear pairs. But the triangle angle sum is
**8.G.5**, a grade later, and a topic-level minimum cannot express that.

Measured over 539 generated questions: 4 of 10 at grade 7 were triangle-sum
questions. Not chance -- the medium difficulty tier is *only* that scenario, so
every grade-7 student on that tier got a grade-8 question.

Same shape and same fix as `SCENARIO_MIN_GRADE` in
`LLM_geometry_generation`, which is the third place this pattern has been
needed: a per-bucket minimum cannot describe an item that arrives after the
bucket it belongs to.
"""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import LLM_angle_relationship_generation as angles  # noqa: E402
import angle_solvers  # noqa: E402

_TIERS = ("easy", "medium", "hard", None)


def test_every_scenario_has_a_grade():
    """A scenario added without one raises rather than being available at every
    grade -- the failure this file exists to prevent."""
    assert set(angles.SCENARIO_MIN_GRADE) == set(angles._SCENARIO_NAMES.values())


def test_the_names_match_the_solver():
    """`angle_solvers.SCENARIO_ARITY` is the other list of these names. Two
    hand-maintained lists is how a scenario ends up gated here and unsolvable
    there, or the reverse."""
    assert set(angles.SCENARIO_MIN_GRADE) == set(angle_solvers.SCENARIO_ARITY)


@pytest.mark.parametrize("grade", ["7", "8", "9", "12"])
@pytest.mark.parametrize("difficulty", _TIERS)
def test_no_tier_offers_a_grade_a_relationship_it_has_not_reached(grade, difficulty):
    """Sampled, because `_pick_scenario` intersects the tier with the grade and
    falls back to the whole allowed set when that is empty -- which is exactly
    what grade 7 on the medium tier now does."""
    for _ in range(200):
        name = angles._SCENARIO_NAMES[angles._pick_scenario(difficulty, grade)]
        assert angles.SCENARIO_MIN_GRADE[name] <= int(grade), (
            f"{grade}/{difficulty} offered {name}, introduced at grade "
            f"{angles.SCENARIO_MIN_GRADE[name]}")


def test_grade_seven_never_gets_the_triangle_sum():
    """The measured case, pinned by name: the medium tier is only this
    scenario, so grade 7 falls back to the rest rather than to nothing."""
    offered = {angles._SCENARIO_NAMES[angles._pick_scenario(t, "7")]
               for t in _TIERS for _ in range(200)}
    assert "triangle_sum" not in offered
    assert offered, "grade 7 must still have angle questions"


def test_grade_eight_gains_it():
    """The fix must not withhold 8.G.5 from the grade that teaches it."""
    offered = {angles._SCENARIO_NAMES[angles._pick_scenario(t, "8")]
               for t in _TIERS for _ in range(200)}
    assert "triangle_sum" in offered


def test_a_narrower_grade_is_a_subset_of_a_wider_one():
    sets = [{angles._SCENARIO_NAMES[n] for n in angles._grade_scenarios(str(g))}
            for g in range(7, 13)]
    for narrower, wider in zip(sets, sets[1:]):
        assert narrower <= wider
