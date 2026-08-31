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


def test_the_names_match_the_blocks_they_send():
    """`_SCENARIO_NAMES` is a literal here, not derived from `SCENARIO_BLOCKS`
    the way geometry's is, so nothing but this test ties the two together.

    It matters more than the duplication suggests: every grade and tier
    assertion in this file keys off `_SCENARIO_NAMES`, so a wrong entry would
    send one block, validate the reply against another, and be invisible to all
    of them at once. The comment on that dict used to claim it was derived --
    it is not, and this is what makes the claim true in effect.
    """
    import re
    source = open(angles.__file__, encoding="utf-8").read()
    from_blocks = dict(re.findall(
        r'^    (\d+): """Scenario \d+: ([a-z_]+)', source, re.M))
    assert from_blocks, "the block regex matched nothing -- see the comment above"
    assert {int(n): name for n, name in from_blocks.items()} == angles._SCENARIO_NAMES


def test_a_reply_naming_a_scenario_above_the_grade_is_refused():
    """`SCENARIO_MIN_GRADE` gates which block is *sent*. Nothing checked the
    reply, so a `triangle_sum` answer to a grade-7 request was solved and
    served: "In a triangle two angles measure 75 and 60 degrees. What is the
    third?" -- 8.G.5, which the gate exists to withhold.

    Not a defensive test. Haiku returned a scenario other than the one asked
    for twice in this work -- `circle_missing_radius_circumference` for
    geometry, and a blended `rect_perimeter_missing_side`.
    """
    import json
    import llm_client

    payload = {"question_text": "In a triangle two angles measure 75 and 60 "
                                "degrees. What is the third?",
               "scenario": "triangle_sum", "variables": ["75", "60"]}
    original = llm_client.generate_text
    lesson = angles.lesson_plan_context.append_lesson_context
    try:
        llm_client.generate_text = lambda *a, **k: json.dumps(payload)
        angles.lesson_plan_context.append_lesson_context = lambda p, t, b: p
        with pytest.raises(ValueError):
            angles.generate_angle_relationship_question([], [], "medium", "7th Grade")
        # ...and grade 8 accepts the same reply, so the refusal is the grade
        # rather than the scenario being unusable.
        question = angles.generate_angle_relationship_question(
            [], [], "medium", "8th Grade")
        assert question["question_text"] == payload["question_text"]
    finally:
        llm_client.generate_text = original
        angles.lesson_plan_context.append_lesson_context = lesson
