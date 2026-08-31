"""Every band is offered only geometry it has actually reached.

`EARLY_BAND_SCENARIOS` filtered one band and left the other three open, so
fixing `early` did nothing for `middle` -- grades 4-6, which was being offered
circle area (7.G.4), the Pythagorean theorem (8.G.7), and on the hard tier
*only* volumes, two of them 8.G.9. A 4th grader on that tier was always asked a
grade-8 question. The band this file was named for was the smaller half of the
same defect.

`SCENARIO_MIN_GRADE` replaces the allowlist with the grade each formula is
introduced at, and `_pick_scenario` filters every band against its ceiling. The
point is structural: a per-band allowlist can omit a band, a per-scenario grade
cannot.

Nothing else catches this. `grade_appropriateness` looks for variable notation,
and a seeded lesson plan steers what a scenario asks rather than which
scenarios are offered.
"""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import LLM_geometry_generation as geo  # noqa: E402
import grade_levels  # noqa: E402

_TIERS = ("easy", "medium", "hard", None)
_BANDS = ("early", "middle", "upper", "advanced")


def test_every_scenario_has_a_grade():
    """A scenario added without one would otherwise be available to every band
    -- the old failure, in a new place."""
    assert set(geo.SCENARIO_MIN_GRADE) == set(geo._SCENARIO_NAMES.values())


def test_the_band_ceilings_match_the_bands_that_exist():
    """`_BAND_CEILING` has to name the same four buckets `grade_levels` sorts
    students into, or a band silently takes the `advanced` default."""
    assert set(geo._BAND_CEILING) == {
        grade_levels.grade_band(f"{g}") for g in range(1, 14)}


@pytest.mark.parametrize("band", _BANDS)
@pytest.mark.parametrize("difficulty", _TIERS)
def test_no_tier_offers_a_band_a_formula_it_has_not_reached(band, difficulty):
    """The assertion the previous version of this file made for `early` alone.

    Sampled rather than reasoned about, because `_pick_scenario` intersects the
    tier with the band and falls back to the whole allowed set when that is
    empty -- middle/hard is entirely volumes, of which only the two rectangular
    ones are grade 5.
    """
    ceiling = geo._BAND_CEILING[band]
    for _ in range(200):
        name = geo._SCENARIO_NAMES[geo._pick_scenario(difficulty, band)]
        assert geo.SCENARIO_MIN_GRADE[name] <= ceiling, (
            f"{band}/{difficulty} offered {name}, introduced at grade "
            f"{geo.SCENARIO_MIN_GRADE[name]} against a ceiling of {ceiling}")


def test_triangle_area_is_not_offered_to_the_early_band():
    """The question that was actually generated for a 1st grader: 1/2 x base x
    height, CCSS 6.G.1. Pinned by name, since the reason is not visible from a
    scenario number."""
    assert "triangle_area" not in {
        geo._SCENARIO_NAMES[n] for n in geo._band_scenarios("early")}


def test_the_pythagorean_theorem_is_not_offered_to_the_middle_band():
    """8.G.7, and it was reachable by every grade-4 student on the medium
    tier."""
    assert "pythagorean" not in {
        geo._SCENARIO_NAMES[n] for n in geo._band_scenarios("middle")}


def test_a_narrower_band_is_a_subset_of_a_wider_one():
    """Ceilings only ever add scenarios, so a younger band cannot be offered
    something an older one is not -- which would mean a grade is gated on
    something other than what it has been taught."""
    sets = [geo._band_scenarios(b) for b in _BANDS]
    for narrower, wider in zip(sets, sets[1:]):
        assert narrower <= wider


def test_the_older_bands_keep_what_they_have_reached():
    """The fix must not narrow anything it should not. Circles are 7.G.4 and
    the Pythagorean theorem is 8.G.7, so both belong to `upper`."""
    upper = {geo._SCENARIO_NAMES[n] for n in geo._band_scenarios("upper")}
    assert {"circle_area", "pythagorean", "triangle_area"} <= upper
    # Pyramid volume is HS (G-GMD.3), not 8.G.9, so it waits for `advanced`.
    assert "pyramid_volume" not in upper
    assert "pyramid_volume" in {
        geo._SCENARIO_NAMES[n] for n in geo._band_scenarios("advanced")}


@pytest.mark.parametrize("grade,hardest", [
    ("4", 4), ("5", 5), ("6", 6), ("7", 7), ("8", 8), ("9", 9),
])
def test_scenarios_are_gated_on_the_grade_not_the_band_ceiling(grade, hardest):
    """The band spans several grades and its ceiling is the top of them, so
    gating there rounded a 4th grader up: rectangular-prism volume (5.MD.5)
    reached grade 4 on 3 of 10 measured questions. The student's own grade is
    known and `SCENARIO_MIN_GRADE` is per scenario, so there is nothing to
    round.
    """
    offered = {geo._SCENARIO_NAMES[n] for n in geo._band_scenarios(grade)}
    assert offered, grade
    assert max(geo.SCENARIO_MIN_GRADE[n] for n in offered) <= int(grade)
    assert offered <= {geo._SCENARIO_NAMES[n]
                       for n in geo._band_scenarios(str(int(grade) + 1))}


@pytest.mark.parametrize("grade", ["1", "2", "", None, "no idea"])
def test_grades_below_the_easiest_scenario_still_get_geometry(grade):
    """The easiest scenario here is area of a rectangle, 3.MD.7, so grades 1-2
    reach nothing on a strict reading. They keep the grade-3 set rather than
    losing the topic: removing it would leave those grades two topics, which is
    a decision about what a 1st grader is offered rather than a defect. It is
    why grades 1-2 measured 10 of 10 above grade, and it is the same open
    question as mean/median/mode at grade 4.
    """
    offered = {geo._SCENARIO_NAMES[n] for n in geo._band_scenarios(grade)}
    assert offered == {"rectangle_area", "rectangle_perimeter",
                       "triangle_perimeter"}
