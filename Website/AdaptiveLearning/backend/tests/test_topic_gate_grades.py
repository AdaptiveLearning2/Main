"""A topic reaches a student no earlier than the grade that teaches it; see docs/question-generation.md."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import LLM_topic_decider as decider  # noqa: E402


def test_every_topic_has_a_minimum_grade():
    assert set(decider.TOPIC_MIN_GRADE) == set(decider.ALL_TOPICS)


@pytest.mark.parametrize("topic,first,code", [
    ("angle_relationships", 7, "7.G.5"),
    ("probability", 7, "7.SP.5"),
    ("algebra", 6, "6.EE.7"),
    ("rationals", 4, "4.NF.3"),
    ("geometry", 2, "2.G.2"),
])
def test_a_topic_first_appears_at_the_grade_that_teaches_it(topic, first, code):
    assert topic not in decider._allowed_topics(str(first - 1)), \
        f"{topic} reaches grade {first - 1}; {code} introduces it at {first}"
    assert topic in decider._allowed_topics(str(first))


@pytest.mark.parametrize("grade", [1, 2, 3, 4, 5, 6])
def test_the_two_measured_topics_are_gone_from_the_grades_that_had_them(grade):
    allowed = decider._allowed_topics(str(grade))
    assert "angle_relationships" not in allowed
    assert "probability" not in allowed


def test_mean_median_mode_wait_for_the_grade_that_teaches_them():
    """6.SP.5c; at grades 4-5 they were 10 of 10 above grade (30 of grade 4's 46 above-grade questions)."""
    for topic in ("mean", "median", "mode"):
        assert decider.TOPIC_MIN_GRADE[topic] == 6
        assert topic not in decider._allowed_topics("5")
        assert topic in decider._allowed_topics("6")


def test_grade_one_has_no_geometry():
    """1.G produces nothing a solver can score; 2.G.2 is the earliest numeric geometry.

    Pins grade 1's set; the tests below compare against grade 1 rather than restating it.
    """
    assert set(decider._allowed_topics("1")) == {
        "ordering", "expressions", "missing_number", "patterns", "graphs",
        "shape_fractions"}
    assert "geometry" in decider._allowed_topics("2")


def test_an_unreadable_grade_gets_grade_ones_topics():
    assert set(decider._allowed_topics("no idea")) == set(
        decider._allowed_topics("1"))


def test_the_cost_of_that_decision_is_four_topics_for_grades_four_and_five():
    """Pinned so a widening is a choice; `patterns` (4.OA.5, 5.OA.3) makes it five."""
    for grade in ("4", "5"):
        assert set(decider._allowed_topics(grade)) == {
            "ordering", "geometry", "expressions", "rationals", "patterns"}


@pytest.mark.parametrize("grade", ["", None, "no idea", "2026 cohort"])
def test_an_unreadable_grade_gets_the_youngest_topics(grade):
    """`profiles.grade_level` is free text; unreadable must not fall through to a permissive branch."""
    assert set(decider._allowed_topics(grade)) == set(
        decider._allowed_topics("1"))


def test_each_topic_is_offered_over_exactly_the_grades_it_declares():
    """Availability follows the tables alone: each topic over exactly `[min, max]`, contiguous."""
    for topic in decider.ALL_TOPICS:
        low = decider.TOPIC_MIN_GRADE[topic]
        high = decider.TOPIC_MAX_GRADE.get(topic, 12)
        offered = [g for g in range(1, 13)
                   if topic in decider._allowed_topics(str(g))]
        assert offered == list(range(low, min(high, 12) + 1)), topic


def test_every_grade_has_something_to_ask():
    """`_safe_topic` calls `random.choice` on this list; through 13, which is College."""
    for g in range(1, 14):
        assert decider._allowed_topics(str(g))


def test_the_grade_eight_topics_are_knowingly_uncapped():
    """Capping empties grades 10-12 (and 500s), so they stay; see docs/question-generation.md."""
    ceiling_at_eight = ["ordering", "expressions", "geometry", "algebra",
                        "rationals", "mean", "median", "mode", "probability",
                        "angle_relationships"]
    for grade in ("9th Grade", "10th Grade", "11th Grade", "12th Grade"):
        allowed = decider._allowed_topics(grade)
        for topic in ceiling_at_eight:
            assert topic in allowed, (
                f"{topic} was capped below {grade}. Capping these empties the "
                "upper grades rather than improving them -- see this test.")
    # Its own loop: trailing the one above, it would read the leaked loop variable.
    for topic in ceiling_at_eight:
        assert topic not in decider.TOPIC_MAX_GRADE, topic


# --- per-grade overrides inside a band --------------------------------------

import LLM_expressions_generation as expressions  # noqa: E402
import LLM_rationals_generation as rationals  # noqa: E402


@pytest.mark.parametrize("module,concept", [
    (expressions, "parentheses"),
    (rationals, "denominator"),
])
def test_grade_four_is_held_back_from_a_grade_five_concept(module, concept):
    """The 4-6 band's tiers are written for grade 6; prompt-level, so it can leak.

    Measured at grade 4: parentheses on 6 of 10 (5.OA.1), unlike denominators on 7 of 10 (5.NF.1).
    """
    assert 4 in module.GRADE_OVERRIDES
    assert concept in module.GRADE_OVERRIDES[4].lower()
    assert "GRADE 4" in module.GRADE_OVERRIDES[4]


@pytest.mark.parametrize("module", [expressions, rationals])
@pytest.mark.parametrize("grade", [5, 6, 7, 9])
def test_the_override_does_not_reach_grades_that_have_met_the_concept(module, grade):
    assert grade not in module.GRADE_OVERRIDES


def test_a_topic_without_a_minimum_grade_fails_loudly():
    """KeyError, not a `.get(t, 1)` that would make the topic available at every grade."""
    import pytest as _pytest
    original = list(decider.ALL_TOPICS)
    try:
        decider.ALL_TOPICS.append("brand_new_topic")
        with _pytest.raises(KeyError):
            decider._allowed_topics("7")
    finally:
        decider.ALL_TOPICS[:] = original
