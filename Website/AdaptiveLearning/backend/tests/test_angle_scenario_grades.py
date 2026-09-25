"""An angle scenario reaches a student no earlier than the grade that teaches it."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import LLM_angle_relationship_generation as angles  # noqa: E402
import angle_solvers  # noqa: E402

_TIERS = ("easy", "medium", "hard", None)


def test_every_scenario_has_a_grade():
    """A scenario without one would otherwise be available at every grade."""
    assert set(angles.SCENARIO_MIN_GRADE) == set(angles._SCENARIO_NAMES.values())


def test_the_names_match_the_solver():
    """`angle_solvers.SCENARIO_ARITY` is a second hand-maintained list of these names."""
    assert set(angles.SCENARIO_MIN_GRADE) == set(angle_solvers.SCENARIO_ARITY)


@pytest.mark.parametrize("grade", ["7", "8", "9", "12"])
@pytest.mark.parametrize("difficulty", _TIERS)
def test_no_tier_offers_a_grade_a_relationship_it_has_not_reached(grade, difficulty):
    """Sampled: `_pick_scenario` falls back to the whole allowed set when tier and grade share none."""
    for _ in range(200):
        name = angles._SCENARIO_NAMES[angles._pick_scenario(difficulty, grade)]
        assert angles.SCENARIO_MIN_GRADE[name] <= int(grade), (
            f"{grade}/{difficulty} offered {name}, introduced at grade "
            f"{angles.SCENARIO_MIN_GRADE[name]}")


def test_grade_seven_never_gets_the_triangle_sum():
    """The medium tier is only this scenario, so grade 7 falls back to the rest."""
    offered = {angles._SCENARIO_NAMES[angles._pick_scenario(t, "7")]
               for t in _TIERS for _ in range(200)}
    assert "triangle_sum" not in offered
    assert offered, "grade 7 must still have angle questions"


def test_grade_eight_gains_it():
    offered = {angles._SCENARIO_NAMES[angles._pick_scenario(t, "8")]
               for t in _TIERS for _ in range(200)}
    assert "triangle_sum" in offered


def test_a_narrower_grade_is_a_subset_of_a_wider_one():
    sets = [{angles._SCENARIO_NAMES[n] for n in angles._grade_scenarios(str(g))}
            for g in range(7, 13)]
    for narrower, wider in zip(sets, sets[1:]):
        assert narrower <= wider


def test_the_names_match_the_blocks_they_send():
    """`_SCENARIO_NAMES` is a literal, not derived from `SCENARIO_BLOCKS`.

    Every assertion above keys off it, so a wrong entry would be invisible to all of them.
    """
    import re
    source = open(angles.__file__, encoding="utf-8").read()
    from_blocks = dict(re.findall(
        r'^    (\d+): """Scenario \d+: ([a-z_]+)', source, re.M))
    assert from_blocks, "the block regex matched nothing -- see the comment above"
    assert {int(n): name for n, name in from_blocks.items()} == angles._SCENARIO_NAMES


def test_a_reply_naming_a_scenario_other_than_the_one_asked_is_refused(monkeypatch):
    """The grade allows both, but another scenario is another tier; the pick is always in-grade."""
    import json
    import llm_client

    payload = {"question_text": "In a triangle two angles measure 75 and 60 "
                                "degrees. What is the third?",
               "scenario": "triangle_sum", "variables": ["75", "60"]}
    number = {name: n for n, name in angles._SCENARIO_NAMES.items()}
    monkeypatch.setattr(llm_client, "generate_text", lambda *a, **k: json.dumps(payload))
    monkeypatch.setattr(angles.lesson_plan_context, "append_lesson_context", lambda p, t, b: p)

    monkeypatch.setattr(angles, "_pick_scenario", lambda *a: number["complementary"])
    with pytest.raises(ValueError):
        angles.generate_angle_relationship_question([], [], "medium", "8th Grade")
    # The same reply to the scenario it answers is served, so the refusal is the mismatch.
    monkeypatch.setattr(angles, "_pick_scenario", lambda *a: number["triangle_sum"])
    question = angles.generate_angle_relationship_question([], [], "medium", "8th Grade")
    assert question["question_text"] == payload["question_text"]
