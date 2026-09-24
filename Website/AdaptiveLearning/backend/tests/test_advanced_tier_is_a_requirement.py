"""Grades 9+ get an `advanced` tier that states a requirement, not an absence of one."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import LLM_algebra_generation as algebra  # noqa: E402
import LLM_expressions_generation as expressions  # noqa: E402
import LLM_rationals_generation as rationals  # noqa: E402
import LLM_mean_generation as mean  # noqa: E402
import LLM_median_generation as median  # noqa: E402
import LLM_mode_generation as mode  # noqa: E402
import LLM_ordering_generation as ordering  # noqa: E402
import LLM_geometry_generation as geometry  # noqa: E402
import LLM_angle_relationship_generation as angles  # noqa: E402
import LLM_probability_generation as probability  # noqa: E402

TABLE_TOPICS = [("algebra", algebra), ("expressions", expressions),
                ("rationals", rationals), ("mean", mean), ("median", median),
                ("mode", mode), ("ordering", ordering)]
SCENARIO_TOPICS = [("geometry", geometry), ("angle_relationships", angles),
                   ("probability", probability)]

# Phrasings a model reads as no requirement at all.
_EMPTY = ("no additional restriction", "beyond what's typical",
          "no restriction")


@pytest.mark.parametrize("name,module", TABLE_TOPICS, ids=[t[0] for t in TABLE_TOPICS])
@pytest.mark.parametrize("tier", ["easy", "medium", "hard"])
def test_the_advanced_tier_states_a_requirement(name, module, tier):
    text = module.COMPLEXITY_BY_GRADE["advanced"][tier].lower()
    for phrase in _EMPTY:
        assert phrase not in text, f"{name}/{tier} says {phrase!r}"


@pytest.mark.parametrize("name,module", SCENARIO_TOPICS,
                         ids=[t[0] for t in SCENARIO_TOPICS])
def test_the_advanced_magnitude_rule_states_a_requirement(name, module):
    text = module.GRADE_COMPLEXITY["advanced"].lower()
    for phrase in _EMPTY:
        assert phrase not in text, f"{name} says {phrase!r}"


@pytest.mark.parametrize("name,module", TABLE_TOPICS, ids=[t[0] for t in TABLE_TOPICS])
@pytest.mark.parametrize("tier", ["easy", "medium", "hard"])
def test_advanced_is_not_upper_with_a_clause_removed(name, module, tier):
    """A tier that is a prefix of the band below cannot ask for more than it."""
    upper = module.COMPLEXITY_BY_GRADE["upper"][tier]
    adv = module.COMPLEXITY_BY_GRADE["advanced"][tier]
    assert adv != upper, f"{name}/{tier} is identical to upper"
    assert not upper.startswith(adv.rstrip(". ")), \
        f"{name}/{tier} is upper with the end removed"


@pytest.mark.parametrize("name,module", TABLE_TOPICS, ids=[t[0] for t in TABLE_TOPICS])
def test_advanced_hard_is_the_hardest_tier_in_its_band(name, module):
    """Length is a crude proxy; the failure this catches is a tier left empty."""
    band = module.COMPLEXITY_BY_GRADE["advanced"]
    assert len(band["hard"]) > 40
    assert band["hard"] != band["easy"]
