"""The Common Core code a question carries.

Two things are checked by reading rather than by generating, because both fail
silently otherwise: every topic resolves to a code (a topic missing from the
tables would store NULL for ever and read as "not resolved" on every one of
its questions), and every generator attaches one (a generator that does not is
the `figure` omission again -- the column exists and the row never fills it).
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ccss_standards  # noqa: E402
import LLM_topic_decider  # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CODE = re.compile(r"^([1-8]\.[A-Z]{1,3}\.[0-9]+[a-d]?|[A-Z]-[A-Z]{2,3}\.[0-9]+[a-d]?)$")


def test_every_topic_resolves_at_every_grade_it_is_offered():
    for topic in LLM_topic_decider.ALL_TOPICS:
        floor = LLM_topic_decider.TOPIC_MIN_GRADE[topic]
        ceiling = LLM_topic_decider.TOPIC_MAX_GRADE.get(topic, 12)
        for grade in range(floor, ceiling + 1):
            code = ccss_standards.ccss_for(topic, f"{grade}th Grade")
            assert code and _CODE.match(code), (topic, grade, code)


def test_every_scenario_in_a_gate_table_has_a_code():
    import LLM_angle_relationship_generation as angles
    import LLM_geometry_generation as geometry
    for topic, table in (("geometry", geometry.SCENARIO_MIN_GRADE),
                         ("angle_relationships", angles.SCENARIO_MIN_GRADE)):
        assert set(table) == set(ccss_standards.SCENARIO_LADDER[topic]), topic


def test_an_unknown_topic_is_none_and_an_unknown_scenario_falls_back():
    assert ccss_standards.ccss_for("no_such_topic", "6th Grade") is None
    assert ccss_standards.ccss_for("geometry", "6th Grade", "no_such_scenario") == "2.G.2"


def test_the_scenario_names_the_standard_inside_a_topic():
    # 8.G.5 inside a grade-7 topic: the scenario decides, not the floor.
    assert ccss_standards.ccss_for("angle_relationships", "7th Grade", "triangle_sum") == "8.G.5"
    assert ccss_standards.ccss_for("angle_relationships", "7th Grade", "linear_pair") == "7.G.5"
    assert ccss_standards.ccss_for("functions", "9th Grade", "evaluate") == "F-IF.2"
    assert ccss_standards.ccss_for("functions", "9th Grade", "compose") == "F-BF.1c"


def test_the_grade_moves_a_topic_along_its_ladder():
    assert ccss_standards.ccss_for("algebra", "6th Grade") == "6.EE.7"
    assert ccss_standards.ccss_for("algebra", "7th Grade") == "7.EE.4"
    assert ccss_standards.ccss_for("algebra", "8th Grade") == "8.EE.7b"
    assert ccss_standards.ccss_for("algebra", "Highschool") == "8.EE.7b"
    assert ccss_standards.ccss_for("graphs", "1st Grade") == "1.MD.4"
    assert ccss_standards.ccss_for("graphs", "2nd Grade") == "2.MD.10"


def test_an_unreadable_grade_is_the_youngest():
    assert ccss_standards.ccss_for("algebra", "Grade ?") == "6.EE.7"
    assert ccss_standards.ccss_for("ordering", None) == "1.NBT.3"


def test_every_generator_attaches_a_standard():
    missing = []
    for filename in sorted(os.listdir(BACKEND)):
        if not (filename.startswith("LLM_") and filename.endswith("_generation.py")):
            continue
        source = open(os.path.join(BACKEND, filename), encoding="utf-8").read()
        if '"ccss_standard": ccss_standards.ccss_for(' not in source:
            missing.append(filename)
    assert not missing, f"these generators return no ccss_standard: {missing}"
