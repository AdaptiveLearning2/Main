"""Raising the difficulty must raise the difficulty.

Each topic mapped a difficulty to a fixed list of scenario numbers. That is
right while every scenario is available and wrong once a grade filter removes
some: geometry's hard tier is the volumes, and the hard ones -- cylinder and
sphere, with pi -- are 8.G.9, so grades 6-7 were left with the two simplest.
Measured before the fix:

    grade 6  medium -> rect_area_missing_side, triangle_area_missing_side, ...
    grade 6  hard   -> cube_volume, rect_volume

Invert a formula, versus multiply three numbers. **Difficulty is what the
biosignals move**: `signal_fusion` labels a student `focused`, the decider
shifts medium -> hard, and at those grades that handed them an easier question.
The fusion fired correctly and was undone one layer down.

Ranking and slicing the *available* scenarios fixes it by construction rather
than by keeping two lists consistent by hand.
"""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import scenario_tiers  # noqa: E402
import LLM_geometry_generation as geo  # noqa: E402
import LLM_angle_relationship_generation as angles  # noqa: E402

TOPICS = [("geometry", geo, [str(g) for g in range(2, 13)]),
          ("angle_relationships", angles, [str(g) for g in range(7, 13)])]


@pytest.mark.parametrize("name,module,grades", TOPICS, ids=[t[0] for t in TOPICS])
def test_every_scenario_has_a_difficulty(name, module, grades):
    """Ranked by how hard it is to *do*, deliberately not by the grade that
    teaches it -- `algebra_complementary` is 7.G.5 and harder than
    `triangle_sum` at 8.G.5. A scenario without a rank raises rather than
    silently sorting first."""
    assert set(module.SCENARIO_DIFFICULTY) == set(module._SCENARIO_NAMES.values())


@pytest.mark.parametrize("name,module,grades", TOPICS, ids=[t[0] for t in TOPICS])
def test_hard_is_never_easier_than_medium_at_any_grade(name, module, grades):
    """The property that was broken. Sampled per tier because a tier holds
    several scenarios; the hardest each can offer is what must not invert."""
    for grade in grades:
        top = {}
        for tier in ("easy", "medium", "hard"):
            offered = {module._SCENARIO_NAMES[module._pick_scenario(tier, grade)]
                       for _ in range(300)}
            top[tier] = max(module.SCENARIO_DIFFICULTY[s] for s in offered)
        assert top["easy"] <= top["medium"] <= top["hard"], (
            f"{name} grade {grade}: easy={top['easy']} medium={top['medium']} "
            f"hard={top['hard']}")


@pytest.mark.parametrize("name,module,grades", TOPICS, ids=[t[0] for t in TOPICS])
def test_every_tier_can_still_answer(name, module, grades):
    """Slicing must not empty a tier -- `random.choice` raises on an empty
    list, and a grade with one available scenario is a real case."""
    for grade in grades:
        for tier in ("easy", "medium", "hard", None):
            assert module._pick_scenario(tier, grade) in module._SCENARIO_NAMES


@pytest.mark.parametrize("count", range(1, 20))
def test_tiers_are_ordered_and_non_empty_for_any_number_of_scenarios(count):
    """Including the small cases: a grade with one or two scenarios must not
    produce an empty tier or an out-of-order one."""
    band = scenario_tiers.tiers(list(range(1, count + 1)))
    assert band["easy"] and band["medium"] and band["hard"]
    assert max(band["easy"]) <= max(band["medium"]) <= max(band["hard"])
