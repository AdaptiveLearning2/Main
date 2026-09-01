"""Grades 9+ get a tier that asks for something, not one that asks for less.

`advanced` was `upper` with the magnitude clause deleted -- "No additional
restriction", "beyond what's typical". To a model that reads as no requirement
rather than a harder one, and it produces the easiest shape that fits. An audit
of 640 generated questions across grades 1-9 measured it: **83% of grade-9
questions were three or more grades below grade**, including `Simplify 5/9 +
7/11 - 2/9` (5.NF.1) and `Evaluate 72 / 8 + 5 * (9 - 4) - 3 * 2 + 10` (5.OA.1)
on the *hard* tier.

The ceiling is grade 8, and that is a solver limit rather than a prompt one --
verified against the solvers before these tiers were written: variables on both
sides, distribution, and fractional or negative coefficients all score
correctly, while a quadratic and a two-unknown equation are both correctly
refused. So `advanced` means the hardest grade-8 content, not high school.
Reaching grades 9-12 needs new solvers, not new prompt text.
"""
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

# The phrasings that made the old tier an absence rather than a requirement.
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
    """The specific shape the old tier had: the same sentence, shorter.

    A tier that is a prefix or subset of the band below it cannot be asking for
    more than that band.
    """
    upper = module.COMPLEXITY_BY_GRADE["upper"][tier]
    adv = module.COMPLEXITY_BY_GRADE["advanced"][tier]
    assert adv != upper, f"{name}/{tier} is identical to upper"
    assert not upper.startswith(adv.rstrip(". ")), \
        f"{name}/{tier} is upper with the end removed"


@pytest.mark.parametrize("name,module", TABLE_TOPICS, ids=[t[0] for t in TABLE_TOPICS])
def test_advanced_hard_is_the_hardest_tier_in_its_band(name, module):
    """Within advanced, hard must ask for more than easy -- length is a crude
    proxy, but the failure this catches is a tier left empty."""
    band = module.COMPLEXITY_BY_GRADE["advanced"]
    assert len(band["hard"]) > 40
    assert band["hard"] != band["easy"]
