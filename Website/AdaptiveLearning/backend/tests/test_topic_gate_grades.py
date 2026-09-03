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
    """Raised from 4 to 6, which is the decision this test used to record.

    Its previous form asserted `TOPIC_MIN_GRADE == 4` and said in as many
    words that changing it meant changing a test that explains why. That is
    what happened: 6.SP.5c introduces all three, and the audit measured 10 of
    10 above grade at grades 4 and 5 in all six cells -- 30 of the 46
    above-grade questions grade 4 received.

    The cost was the reason for waiting and is worth keeping visible: grades
    4-5 drop from seven topics to four, which is what grades 1-3 get plus
    `rationals`. Breadth traded for accuracy, deliberately.
    """
    for topic in ("mean", "median", "mode"):
        assert decider.TOPIC_MIN_GRADE[topic] == 6
        assert topic not in decider._allowed_topics("5")
        assert topic in decider._allowed_topics("6")


def test_grade_one_has_no_geometry():
    """1.G is defining attributes of shapes and partitioning into halves and
    fourths -- nothing that produces a number a solver can score. 2.G.2,
    counting the squares that fill a rectangle, is the earliest that does.

    The alternative was dressing addition as geometry ("3 triangles and 4
    squares -- how many shapes?"), which keeps a topic count up while teaching
    1.OA. That is still refused: grade 1 has no geometry.

    What changed is the rest of the list. Grade 1 had exactly two topics, which
    meant a 6-year-old saw `ordering` and `expressions` on rotation;
    `missing_number` (1.OA.8), `patterns` (1.NBT.1), `graphs` (1.MD.4) and
    `shape_fractions` (1.G.3) are real grade-1 standards with exact solvers, so
    the honest size of the list is six.

    This one pins the set, because its whole point is what a 6-year-old can be
    asked. The tests below deliberately do not: "an unreadable grade is treated
    as the youngest" is a property of the *gate*, and restating grade 1's
    membership to express it made four literals that all had to be edited every
    time the youngest grade gained a topic -- twice now.
    """
    assert set(decider._allowed_topics("1")) == {
        "ordering", "expressions", "missing_number", "patterns", "graphs",
        "shape_fractions"}
    assert "geometry" in decider._allowed_topics("2")


def test_an_unreadable_grade_gets_grade_ones_topics():
    """It is treated as the youngest, so it narrows with grade 1 rather than
    keeping a list that used to include geometry."""
    assert set(decider._allowed_topics("no idea")) == set(
        decider._allowed_topics("1"))


def test_the_cost_of_that_decision_is_four_topics_for_grades_four_and_five():
    """Pinned so the narrowing is visible rather than incidental. If a future
    change widens these grades again, it should be because someone chose to.

    It has been widened once, deliberately: `patterns` runs to grade 5 (4.OA.5,
    5.OA.3), so these grades have five topics rather than four. `mean`,
    `median` and `mode` are still out, which is what the narrowing was about --
    they are 6.SP.5c and were the 30 above-grade questions that prompted it."""
    for grade in ("4", "5"):
        assert set(decider._allowed_topics(grade)) == {
            "ordering", "geometry", "expressions", "rationals", "patterns"}


@pytest.mark.parametrize("grade", ["", None, "no idea", "2026 cohort"])
def test_an_unreadable_grade_gets_the_youngest_topics(grade):
    """`profiles.grade_level` is free text. An unreadable one must not fall
    through to a permissive branch -- the failure `_allowed_topics` was
    rewritten for once already."""
    assert set(decider._allowed_topics(grade)) == set(
        decider._allowed_topics("1"))


def test_each_topic_is_offered_over_exactly_the_grades_it_declares():
    """This replaces a subset test, and the reason is the point.

    That test asserted topics only ever accumulate with grade -- true while
    `TOPIC_MIN_GRADE` was a floor with no ceiling, and deliberately repealed by
    `TOPIC_MAX_GRADE`. "8 + ? = 11" is 1.OA.8 and does not become a grade-9
    question by using bigger numbers, so `missing_number` leaves the list at
    grade 4 and a 15-year-old is not offered it.

    The property that survives is the one the subset test was really
    protecting: availability follows the declared tables and nothing else, so
    each topic appears over exactly `[min, max]` and is contiguous -- no gap in
    the middle, which is what a gate keying on something other than the tables
    would produce."""
    for topic in decider.ALL_TOPICS:
        low = decider.TOPIC_MIN_GRADE[topic]
        high = decider.TOPIC_MAX_GRADE.get(topic, 12)
        offered = [g for g in range(1, 13)
                   if topic in decider._allowed_topics(str(g))]
        assert offered == list(range(low, min(high, 12) + 1)), topic


def test_every_grade_has_something_to_ask():
    """The gate narrows; it must not empty. `_safe_topic` calls
    `random.choice` on this list, and on `[]` that is an IndexError -- a 500
    on every question for that grade, not a graceful degradation.

    Through 13, which is what `grade_levels` reads for College.
    """
    for g in range(1, 14):
        assert decider._allowed_topics(str(g))


def test_the_grade_eight_topics_are_knowingly_uncapped():
    """A decision, recorded here so reversing it means editing a test that
    says why.

    The 2026-09-02 audit measured grades 9-12 at 56/69/81/100% of questions
    three or more grades below grade, which reads as an argument for giving
    the topics that top out at grade 8 a `TOPIC_MAX_GRADE`. It is not one, and
    the reason is arithmetic rather than judgement: the highest concept
    anything here can *score* is grade 9, so a cap keyed to the student's
    grade removes everything.

        strict cap (concept >= grade)   grade  9: 2 topics    grades 10-12: 0
        within two grades               grade  9: 8 topics    grade 12:     0

    Capping cannot fix a ceiling. It converts "serves below-grade content"
    into "serves no content", and by the line above, into a 500.

    The metric also over-reads for practice topics, which is worth knowing
    before trusting it again: it counts the grade a concept is *introduced*,
    and S-ID.2 has high schoolers using mean and median to compare
    distributions. A 9th grader finding a median is doing grade-appropriate
    work that this measure scores three grades below.

    So the only real lever for grades 9-12 is more solvers -- `spread`
    (S-ID.2) is the next one -- and this asserts the topics stay available
    until there is something to replace them with.
    """
    ceiling_at_eight = ["ordering", "expressions", "geometry", "algebra",
                        "rationals", "mean", "median", "mode", "probability",
                        "angle_relationships"]
    for grade in ("9th Grade", "10th Grade", "11th Grade", "12th Grade"):
        allowed = decider._allowed_topics(grade)
        for topic in ceiling_at_eight:
            assert topic in allowed, (
                f"{topic} was capped below {grade}. Capping these empties the "
                "upper grades rather than improving them -- see this test.")
    # Inside its own loop, not trailing the one above: written there it read
    # the leaked loop variable and checked one topic of ten.
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


def test_a_topic_without_a_minimum_grade_fails_loudly():
    """`_allowed_topics` indexes `TOPIC_MIN_GRADE[t]` directly, so a topic
    added to `ALL_TOPICS` without one raises KeyError on the next question
    rather than defaulting to available at every grade.

    That is the intended behaviour and worth pinning: the alternative -- a
    `.get(t, 1)` -- is how `angle_relationships` would have reached grade 4
    again, silently, the next time someone added a topic.
    """
    import pytest as _pytest
    original = list(decider.ALL_TOPICS)
    try:
        decider.ALL_TOPICS.append("brand_new_topic")
        with _pytest.raises(KeyError):
            decider._allowed_topics("7")
    finally:
        decider.ALL_TOPICS[:] = original
