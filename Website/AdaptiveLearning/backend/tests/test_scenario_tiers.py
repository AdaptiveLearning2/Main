"""Raising the difficulty must raise the difficulty at every grade; see docs/question-generation.md."""
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
    """An unranked scenario would raise rather than silently sort first."""
    assert set(module.SCENARIO_DIFFICULTY) == set(module._SCENARIO_NAMES.values())


@pytest.mark.parametrize("name,module,grades", TOPICS, ids=[t[0] for t in TOPICS])
def test_hard_is_never_easier_than_medium_at_any_grade(name, module, grades):
    """Sampled per tier; the hardest each can offer is what must not invert."""
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
    """`random.choice` raises on an empty tier."""
    for grade in grades:
        for tier in ("easy", "medium", "hard", None):
            assert module._pick_scenario(tier, grade) in module._SCENARIO_NAMES


@pytest.mark.parametrize("count", range(1, 20))
def test_tiers_are_ordered_and_non_empty_for_any_number_of_scenarios(count):
    band = scenario_tiers.tiers(list(range(1, count + 1)))
    assert band["easy"] and band["medium"] and band["hard"]
    assert max(band["easy"]) <= max(band["medium"]) <= max(band["hard"])


def test_the_fixed_tier_tables_are_gone_from_the_two_topics_that_rank():
    """A dead table reads as the live rule; source check, since there is no behaviour to assert.

    `LLM_probability_generation` does not rank, so it keeps its table.
    """
    for module in (geo, angles):
        source = open(module.__file__, encoding="utf-8").read()
        assert "DIFFICULTY_SCENARIOS" not in source, (
            f"{os.path.basename(module.__file__)} names DIFFICULTY_SCENARIOS. "
            "Tiers here are a slice of what the grade allows, ranked by "
            "SCENARIO_DIFFICULTY -- a fixed per-tier list cannot express that "
            "and inverts once a grade filter removes part of one.")
