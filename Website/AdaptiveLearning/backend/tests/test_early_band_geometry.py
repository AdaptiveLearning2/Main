"""The early band is grades 1-3, so its geometry has to be grade-3 geometry.

`EARLY_BAND_SCENARIOS` included scenario 3, `triangle_area` -- which is
1/2 x base x height, CCSS 6.G.1, three years above the top of this band. A
grade-1 session was generated asking "A triangle has a base of 8 units and a
height of 5 units. What is its area?".

Nothing caught it and nothing could: `grade_appropriateness` looks for variable
notation, and the seeded lesson plans steer what a scenario *asks* rather than
which scenarios are offered. It was found by reading generated output, which is
the check CLAUDE.md names as the one that actually settles this.
"""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import LLM_geometry_generation as geo  # noqa: E402

# What grade 3 covers: area of a rectangle (3.MD.7) and perimeter of a polygon
# (3.MD.8). Named by standard rather than by scenario number, so a renumbering
# has to face the reason rather than silently re-admit something.
_GRADE_3_SCENARIOS = {"rectangle_area", "rectangle_perimeter",
                      "triangle_perimeter"}


def test_the_early_band_offers_only_grade_three_geometry():
    offered = {geo._SCENARIO_NAMES[n] for n in geo.EARLY_BAND_SCENARIOS}
    assert offered == _GRADE_3_SCENARIOS


def test_triangle_area_is_not_offered_to_the_early_band():
    """Pinned by name, because it is the one that was wrong and the reason is
    not visible from a scenario number."""
    assert "triangle_area" not in {
        geo._SCENARIO_NAMES[n] for n in geo.EARLY_BAND_SCENARIOS}


@pytest.mark.parametrize("difficulty", ["easy", "medium", "hard", None])
def test_no_difficulty_tier_smuggles_another_scenario_into_the_early_band(difficulty):
    """`_pick_scenario` intersects the difficulty tier with this set and falls
    back to the whole set when the intersection is empty -- so a tier holding
    only advanced scenarios must land on the fallback, not on its own list."""
    for _ in range(60):
        picked = geo._pick_scenario(difficulty, "early")
        assert geo._SCENARIO_NAMES[picked] in _GRADE_3_SCENARIOS, difficulty


def test_older_bands_still_get_the_full_range():
    """The fix must not narrow anything above the early band -- triangle area
    is ordinary content from grade 6."""
    seen = {geo._SCENARIO_NAMES[geo._pick_scenario(d, "upper")]
            for d in ("easy", "medium", "hard") for _ in range(80)}
    assert "triangle_area" in seen
    assert len(seen) > len(_GRADE_3_SCENARIOS)
