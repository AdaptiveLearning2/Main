"""A missing or unreadable grade is `grade_levels.DEFAULT_GRADE` at every site that reads one."""
import pytest

import ccss_standards
import grade_levels
import LLM_angle_relationship_generation as angles
import LLM_geometry_generation as geometry
import LLM_topic_decider

# Each reads a grade and falls back when it cannot; a hard-coded 1 ignores a moved default.
SITES = {
    "topic gate": LLM_topic_decider._allowed_topics,
    "ccss code": lambda g: ccss_standards.ccss_for("ordering", g),
    "grade band": grade_levels.grade_band,
    "angle scenarios": angles._grade_scenarios,
    "whole-degree answers": angles._requires_whole_number_solution,
    "geometry scenarios": geometry._band_scenarios,
}
# Far enough from grade 1 that every site answers differently for it.
MOVED = "8th Grade"


@pytest.mark.parametrize("site", SITES)
def test_the_site_can_tell_the_moved_default_from_grade_1(site):
    """Otherwise the test below passes whatever the site falls back to."""
    assert SITES[site](MOVED) != SITES[site]("1st Grade")


@pytest.mark.parametrize("site", SITES)
@pytest.mark.parametrize("grade", [None, "", "not a grade"])
def test_a_missing_or_unreadable_grade_follows_the_default(site, grade, monkeypatch):
    monkeypatch.setattr(grade_levels, "DEFAULT_GRADE", MOVED)
    assert SITES[site](grade) == SITES[site](MOVED)
