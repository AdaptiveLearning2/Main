"""Every band is offered only geometry it has reached, via per-scenario `SCENARIO_MIN_GRADE`."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import LLM_geometry_generation as geo  # noqa: E402
import grade_levels  # noqa: E402

_TIERS = ("easy", "medium", "hard", None)
_BANDS = ("early", "middle", "upper", "advanced")


def test_every_scenario_has_a_grade():
    assert set(geo.SCENARIO_MIN_GRADE) == set(geo._SCENARIO_NAMES.values())


def test_the_band_ceilings_match_the_bands_that_exist():
    """Otherwise a band silently takes the `advanced` default."""
    assert set(geo._BAND_CEILING) == {
        grade_levels.grade_band(f"{g}") for g in range(1, 14)}


@pytest.mark.parametrize("band", _BANDS)
@pytest.mark.parametrize("difficulty", _TIERS)
def test_no_tier_offers_a_band_a_formula_it_has_not_reached(band, difficulty):
    """Sampled, since `_pick_scenario` falls back to the whole allowed set on an empty intersection."""
    ceiling = geo._BAND_CEILING[band]
    for _ in range(200):
        name = geo._SCENARIO_NAMES[geo._pick_scenario(difficulty, band)]
        assert geo.SCENARIO_MIN_GRADE[name] <= ceiling, (
            f"{band}/{difficulty} offered {name}, introduced at grade "
            f"{geo.SCENARIO_MIN_GRADE[name]} against a ceiling of {ceiling}")


def test_triangle_area_is_not_offered_to_the_early_band():
    """CCSS 6.G.1; pinned by name."""
    assert "triangle_area" not in {
        geo._SCENARIO_NAMES[n] for n in geo._band_scenarios("early")}


def test_the_pythagorean_theorem_is_not_offered_to_the_middle_band():
    """8.G.7."""
    assert "pythagorean" not in {
        geo._SCENARIO_NAMES[n] for n in geo._band_scenarios("middle")}


def test_a_narrower_band_is_a_subset_of_a_wider_one():
    sets = [geo._band_scenarios(b) for b in _BANDS]
    for narrower, wider in zip(sets, sets[1:]):
        assert narrower <= wider


def test_the_older_bands_keep_what_they_have_reached():
    """Circles (7.G.4) and Pythagoras (8.G.7) belong to `upper`."""
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
    """Gating on the band ceiling rounds a student up to the band's top grade."""
    offered = {geo._SCENARIO_NAMES[n] for n in geo._band_scenarios(grade)}
    assert offered, grade
    assert max(geo.SCENARIO_MIN_GRADE[n] for n in offered) <= int(grade)
    assert offered <= {geo._SCENARIO_NAMES[n]
                       for n in geo._band_scenarios(str(int(grade) + 1))}


@pytest.mark.parametrize("grade", ["1", "2", "", None, "no idea"])
def test_the_youngest_grades_get_the_one_scenario_they_have_reached(grade):
    """2.G.2 is the only numeric geometry standard below grade 3.

    An unreadable grade lands here too, matching `_allowed_topics` and `_grade_band`.
    """
    offered = {geo._SCENARIO_NAMES[n] for n in geo._band_scenarios(grade)}
    assert offered == {"rectangle_area_by_counting"}


def test_the_counting_scenario_reuses_the_area_solver():
    """rows x columns is length x width; only the prompt wording differs."""
    import geometry_solvers
    value, reason = geometry_solvers.solve_scenario(
        "rectangle_area_by_counting", {"rows": "3", "columns": "4"})
    assert value == 12.0 and reason is None


def test_grade_three_gains_the_formula_scenarios():
    offered = {geo._SCENARIO_NAMES[n] for n in geo._band_scenarios("3")}
    assert offered == {"rectangle_area_by_counting", "rectangle_area",
                       "rectangle_perimeter", "triangle_perimeter"}


def test_a_reply_naming_a_scenario_above_the_grade_is_refused():
    """`_band_scenarios` gates what is sent; the model can reply with another scenario."""
    import json
    import llm_client

    payload = {"question_text": "A sphere has a radius of 3 units. "
                                "What is its volume?",
               "scenario": "sphere_volume", "variables": {"radius": "3"}}
    original = llm_client.generate_text
    lesson = geo.lesson_plan_context.append_lesson_context
    try:
        llm_client.generate_text = lambda *a, **k: json.dumps(payload)
        geo.lesson_plan_context.append_lesson_context = lambda p, t, b: p
        with pytest.raises(ValueError):
            geo.generate_geometry_question([], [], "medium", "4th Grade")
        # Grade 8 takes the same reply: the grade is refused, not the scenario.
        question = geo.generate_geometry_question([], [], "medium", "8th Grade")
        assert question["question_text"] == payload["question_text"]
    finally:
        llm_client.generate_text = original
        geo.lesson_plan_context.append_lesson_context = lesson
