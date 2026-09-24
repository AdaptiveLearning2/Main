"""The Common Core code a question carries: every topic resolves, every generator attaches one."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

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


def test_grade_four_is_kept_off_the_grade_five_standard():
    # Matches `GRADE_OVERRIDES[4]` in the expressions generator: 5.OA.1 is grade 5.
    for scenario in ("evaluate", "order_of_operations"):
        assert ccss_standards.ccss_for("expressions", "4th Grade", scenario) != "5.OA.1"
        assert ccss_standards.ccss_for("expressions", "5th Grade", scenario) == "5.OA.1"


def test_every_scenario_selecting_generator_checks_the_reply_scenario():
    # An unchecked reply scenario misses SCENARIO_LADDER and takes the lowest rung's code.
    missing = []
    for filename in sorted(os.listdir(BACKEND)):
        if not (filename.startswith("LLM_") and filename.endswith("_generation.py")):
            continue
        source = open(os.path.join(BACKEND, filename), encoding="utf-8").read()
        # Equality with the asked-for name, or membership in the grade's allowed set.
        checks = ("!= _SCENARIO_NAMES[scenario]", 'question_data["scenario"] not in')
        if "_SCENARIO_NAMES[scenario]" in source \
                and not any(c in source for c in checks):
            missing.append(filename)
    assert not missing, f"these generators never check the reply's scenario: {missing}"


class _Query:
    def __init__(self, store, table):
        self.store, self.table, self.filters, self.inserted = store, table, [], None

    def select(self, *_):
        return self

    def eq(self, col, val):
        self.filters.append(("eq", col, val))
        return self

    def is_(self, col, val):
        self.filters.append(("is", col, val))
        return self

    def limit(self, n):
        self.n = n
        return self

    def insert(self, row):
        self.inserted = row
        return self

    def execute(self):
        if self.inserted is not None:
            self.store["rows"].append({"id": f"id-{len(self.store['rows'])}", **self.inserted})
            return type("R", (), {"data": [self.store["rows"][-1]]})()
        hits = [r for r in self.store["rows"]
                if all((r.get(c) is None) if kind == "is" else (r.get(c) == v)
                       for kind, c, v in self.filters)]
        return type("R", (), {"data": hits[:self.n]})()


class _FakeSupabase:
    def __init__(self):
        self.store = {"rows": []}
        self.queries = []

    def table(self, name):
        q = _Query(self.store, name)
        self.queries.append(q)
        return q


def test_the_same_text_at_a_different_standard_is_a_different_row(monkeypatch):
    fake = _FakeSupabase()
    monkeypatch.setattr(LLM_topic_decider, "supabase", fake)
    q = {"question_text": "Solve 2x + 3 = 11", "question_topic": "algebra",
         "answer_options": ["4", "5"], "correct_answer": "4"}
    first = LLM_topic_decider.add_question_to_supabase({**q, "ccss_standard": "6.EE.7"}, "easy")
    again = LLM_topic_decider.add_question_to_supabase({**q, "ccss_standard": "6.EE.7"}, "easy")
    other = LLM_topic_decider.add_question_to_supabase({**q, "ccss_standard": "8.EE.7b"}, "easy")
    assert first == again
    assert other != first
    assert [r["ccss_standard"] for r in fake.store["rows"]] == ["6.EE.7", "8.EE.7b"]
    # Assert on the query: a passing result alone could come from a text mismatch.
    assert ("eq", "ccss_standard", "8.EE.7b") in fake.queries[-2].filters


_SHADED = {"question_text": "What fraction of the shape is shaded?",
           "question_topic": "shape_fractions", "ccss_standard": "3.NF.1",
           "figure": {"kind": "shape", "parts": 4, "shaded": 3},
           "answer_options": ["3/4", "1/4", "1/2"], "correct_answer": "3/4"}


@pytest.mark.parametrize("change", [
    {"figure": {"kind": "shape", "parts": 4, "shaded": 1}},
    # Same option set, another answer: only the answer check tells them apart.
    {"correct_answer": "1/4", "answer_options": ["1/4", "3/4", "1/2"]},
], ids=["another figure", "another answer"])
def test_the_same_text_with_different_content_is_a_different_row(change, monkeypatch):
    """`shape_fractions` and `graphs` keep digits out of the text, so text alone can't dedupe."""
    fake = _FakeSupabase()
    monkeypatch.setattr(LLM_topic_decider, "supabase", fake)
    first = LLM_topic_decider.add_question_to_supabase(dict(_SHADED), "easy")
    second = LLM_topic_decider.add_question_to_supabase({**_SHADED, **change}, "easy")
    assert second != first
    assert fake.store["rows"][-1]["id"] == second


@pytest.mark.parametrize("options", [
    ["1/2", "3/4", "1/4"],
    ["2/3", "3/4", "1/5"],
], ids=["reshuffled", "other wrong answers"])
def test_a_repeat_reuses_its_row_and_is_served_the_stored_options(options, monkeypatch):
    """A repeat is same text, answer and figure; it gets the stored options, since answers index them."""
    fake = _FakeSupabase()
    monkeypatch.setattr(LLM_topic_decider, "supabase", fake)
    first = LLM_topic_decider.add_question_to_supabase(dict(_SHADED), "easy")

    again = {**_SHADED, "answer_options": options}
    assert LLM_topic_decider.add_question_to_supabase(again, "easy") == first
    assert again["answer_options"] == ["3/4", "1/4", "1/2"]
    assert len(fake.store["rows"]) == 1


def test_a_generic_text_finds_its_match_past_the_candidate_cap(monkeypatch):
    """The answer is a filter, so rows for other answers don't use up `_DEDUPE_CANDIDATES`."""
    fake = _FakeSupabase()
    for i in range(LLM_topic_decider._DEDUPE_CANDIDATES + 10):
        fake.store["rows"].append({**_SHADED, "id": f"other-{i}", "correct_answer": f"{i}/99",
                                   "options": [f"{i}/99", "1/4", "1/2"]})
    fake.store["rows"].append({**_SHADED, "id": "the-match",
                               "options": _SHADED["answer_options"]})
    monkeypatch.setattr(LLM_topic_decider, "supabase", fake)

    assert LLM_topic_decider.add_question_to_supabase(dict(_SHADED), "easy") == "the-match"
    assert ("eq", "correct_answer", "3/4") in fake.queries[0].filters


def test_a_stored_row_missing_its_answer_is_not_served(monkeypatch):
    """Options lacking the answer would serve a question nobody can get right."""
    fake = _FakeSupabase()
    fake.store["rows"].append({**_SHADED, "id": "broken", "options": ["1/4", "1/2", "2/3"]})
    monkeypatch.setattr(LLM_topic_decider, "supabase", fake)

    assert LLM_topic_decider.add_question_to_supabase(dict(_SHADED), "easy") != "broken"


@pytest.mark.parametrize("stored", ['["4", "8"]', '["4","8"]'],
                         ids=["jsonb spacing", "compact"])
def test_a_list_answer_matches_the_text_it_is_stored_as(stored, monkeypatch):
    """`correct_answer` is text, so a `mode`/`ordering` list answer comes back as JSON text."""
    fake = _FakeSupabase()
    fake.store["rows"].append({
        "id": "stored", "question_text": "Find the mode: 4, 8, 4, 8, 2",
        "ccss_standard": "6.SP.5c", "figure": None,
        "options": [["4", "8"], ["2"], ["4"], ["8"]], "correct_answer": stored})
    monkeypatch.setattr(LLM_topic_decider, "supabase", fake)
    q = {"question_text": "Find the mode: 4, 8, 4, 8, 2", "question_topic": "mode",
         "ccss_standard": "6.SP.5c",
         "answer_options": [["4"], ["4", "8"], ["8"], ["2"]], "correct_answer": ["4", "8"]}
    assert LLM_topic_decider.add_question_to_supabase(q, "easy") == "stored"


def test_a_question_with_no_standard_dedupes_against_null_not_the_string_none(monkeypatch):
    fake = _FakeSupabase()
    monkeypatch.setattr(LLM_topic_decider, "supabase", fake)
    q = {"question_text": "t", "question_topic": "algebra", "answer_options": ["1"],
         "correct_answer": "1"}
    LLM_topic_decider.add_question_to_supabase(q, "easy")
    assert ("is", "ccss_standard", "null") in fake.queries[0].filters


def test_every_generator_attaches_a_standard():
    missing = []
    for filename in sorted(os.listdir(BACKEND)):
        if not (filename.startswith("LLM_") and filename.endswith("_generation.py")):
            continue
        source = open(os.path.join(BACKEND, filename), encoding="utf-8").read()
        if '"ccss_standard": ccss_standards.ccss_for(' not in source:
            missing.append(filename)
    assert not missing, f"these generators return no ccss_standard: {missing}"
