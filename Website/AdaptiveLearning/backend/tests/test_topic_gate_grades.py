"""A topic reaches a student no earlier than the grade that teaches it.

`_allowed_topics` was three grade brackets, and a bracket has to be
*remembered* for every topic it should exclude. Two were missed, and an audit
of 640 generated questions across grades 1-9 measured the cost:

- `angle_relationships` was allowed from grade 4 against 7.G.5. All 30
  questions at grades 4, 5 and 6 were above grade -- "Two angles form a linear
  pair. If one measures 65 degrees, find the other" is a grade-7 question, and
  a 4th grader got one every time.
- `probability` was allowed from grade 6 against 7.SP.5. 10 of 10 at grade 6.

Neither is reachable by prompt tuning: the topic arrives before the concept,
so no version of the question is grade-appropriate. `TOPIC_MIN_GRADE` is the
per-topic form, for the reason `SCENARIO_MIN_GRADE` is per scenario.
"""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import LLM_topic_decider as decider  # noqa: E402


def test_every_topic_has_a_minimum_grade():
    """A topic added without one raises rather than defaulting to available
    everywhere -- which is the old failure in a new place."""
    assert set(decider.TOPIC_MIN_GRADE) == set(decider.ALL_TOPICS)


@pytest.mark.parametrize("topic,first,code", [
    ("angle_relationships", 7, "7.G.5"),
    ("probability", 7, "7.SP.5"),
    ("algebra", 6, "6.EE.7"),
    ("rationals", 4, "4.NF.3"),
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


def test_mean_median_mode_are_knowingly_early():
    """Not an oversight, and pinned so it cannot become one.

    6.SP.5c introduces all three, and the audit flagged 10 of 10 at grades 4
    and 5 in all six cells. They stay at 4 because raising them is a decision
    about what grades 4-5 are offered at all -- they would drop to five topics
    -- rather than a defect. If that decision is ever made, this test is the
    thing to change, and its failure is the record of the decision.
    """
    for topic in ("mean", "median", "mode"):
        assert decider.TOPIC_MIN_GRADE[topic] == 4
        assert topic in decider._allowed_topics("4")


@pytest.mark.parametrize("grade", ["", None, "no idea", "2026 cohort"])
def test_an_unreadable_grade_gets_the_youngest_topics(grade):
    """`profiles.grade_level` is free text. An unreadable one must not fall
    through to a permissive branch -- the failure `_allowed_topics` was
    rewritten for once already."""
    assert set(decider._allowed_topics(grade)) == {
        "ordering", "geometry", "expressions"}


def test_a_narrower_grade_is_a_subset_of_a_wider_one():
    """Topics only ever accumulate with grade. A younger student being offered
    something an older one is not would mean the gate keys on something other
    than what has been taught."""
    sets = [set(decider._allowed_topics(str(g))) for g in range(1, 13)]
    for younger, older in zip(sets, sets[1:]):
        assert younger <= older


def test_every_grade_has_something_to_ask():
    """The gate narrows; it must not empty. `_safe_topic` calls
    `random.choice` on this list."""
    for g in range(1, 13):
        assert decider._allowed_topics(str(g))


# --- per-grade overrides inside a band --------------------------------------

import LLM_expressions_generation as expressions  # noqa: E402
import LLM_rationals_generation as rationals  # noqa: E402


@pytest.mark.parametrize("module,concept", [
    (expressions, "parentheses"),
    (rationals, "denominator"),
])
def test_grade_four_is_held_back_from_a_grade_five_concept(module, concept):
    """`COMPLEXITY_BY_GRADE` is keyed by band, and the middle band spans 4-6,
    so its tiers are written for grade 6. Measured at grade 4: parentheses on
    6 of 10 questions (5.OA.1), unlike denominators on 7 of 10 (5.NF.1).

    Keyed by grade rather than folded into the table, because giving that table
    a thirteenth column to express one rule would make every other topic's
    table wrong by omission. Prompt-level, so it can leak; a code-level check
    would go in `grade_appropriateness`.
    """
    assert 4 in module.GRADE_OVERRIDES
    assert concept in module.GRADE_OVERRIDES[4].lower()
    assert "GRADE 4" in module.GRADE_OVERRIDES[4]


@pytest.mark.parametrize("module", [expressions, rationals])
@pytest.mark.parametrize("grade", [5, 6, 7, 9])
def test_the_override_does_not_reach_grades_that_have_met_the_concept(module, grade):
    """5.OA.1 and 5.NF.1 are grade-5 standards, so grade 5 keeps them."""
    assert grade not in module.GRADE_OVERRIDES
