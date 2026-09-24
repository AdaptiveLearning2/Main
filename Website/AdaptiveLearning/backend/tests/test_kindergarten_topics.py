"""Kindergarten has its own topics and generator; code picks every number, the model words it."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import json  # noqa: E402
import random  # noqa: E402

import pytest  # noqa: E402

import ccss_standards  # noqa: E402
import grade_appropriateness  # noqa: E402
import LLM_kindergarten_generation as kg  # noqa: E402
import LLM_topic_decider as td  # noqa: E402
import question_figures  # noqa: E402

CELLS = [(topic, difficulty, scenario)
         for topic, tiers in kg.SCENARIOS.items()
         for difficulty, scenarios in tiers.items()
         for scenario in scenarios]


@pytest.fixture
def model(monkeypatch):
    """A model that words each question with the plan's own example; `said` overrides it."""
    plans, prompts = [], []
    said = {}
    real_plan = kg._plan

    def plan(*args):
        plans.append(real_plan(*args))
        return plans[-1]

    def generate_text(prompt, schema=None):
        prompts.append(prompt)
        text = said.get("text", plans[-1]["example"])
        return json.dumps({"question_text": text, "question_topic": "x"})

    monkeypatch.setattr(kg, "_plan", plan)
    monkeypatch.setattr(kg.llm_client, "generate_text", generate_text)
    return plans, prompts, said


def _generate(topic, difficulty, scenario, seed, monkeypatch):
    rng = random.Random(seed)
    monkeypatch.setitem(kg.SCENARIOS[topic], difficulty, [scenario])
    return kg.generate_kindergarten_question([], [], difficulty, "Kindergarten", topic=topic, rng=rng)


# ── which grades see these topics ────────────────────────────────────────────

def test_the_decider_lists_exactly_the_generators_topics():
    kindergarten = {t for t in td.ALL_TOPICS if td.TOPIC_MIN_GRADE[t] == 0}
    assert kindergarten == set(kg.TOPICS) == set(kg.SCENARIOS)


@pytest.mark.parametrize("grade", ["Kindergarten", "kindergarten", "Pre-K", "Grade 0"])
def test_kindergarten_sees_only_its_own_topics(grade):
    assert set(td._allowed_topics(grade)) == set(kg.TOPICS)


@pytest.mark.parametrize("grade", ["1", "2nd Grade", "5", "9th Grade", "not a grade"])
def test_no_other_grade_sees_a_kindergarten_topic(grade):
    assert not set(td._allowed_topics(grade)) & set(kg.TOPICS)


def test_every_scenario_has_a_kindergarten_code_and_a_grade_check():
    for topic, _, scenario in CELLS:
        assert ccss_standards.ccss_for(topic, "Kindergarten", scenario).startswith("K."), scenario
        assert scenario in ccss_standards.SCENARIO_LADDER[topic], scenario
    for topic in kg.TOPICS:
        assert "early" in grade_appropriateness.FORBIDDEN_BANDS[topic]


def test_the_decider_routes_a_kindergarten_topic_to_the_new_generator(monkeypatch):
    called = []
    monkeypatch.setattr(kg, "generate_kindergarten_question",
                        lambda *_a, topic, **_k: called.append(topic) or {"question_text": "q"})
    td.question_generation("shapes", "easy", "k-routing", "Kindergarten")
    assert called == ["shapes"]


# ── every scenario, generated end to end ─────────────────────────────────────

@pytest.mark.parametrize("topic,difficulty,scenario", CELLS)
def test_every_scenario_generates_a_consistent_question(model, monkeypatch, topic, difficulty, scenario):
    plans, _, _ = model
    for seed in range(25):
        q = _generate(topic, difficulty, scenario, seed, monkeypatch)
        plan = plans[-1]
        assert q["question_topic"] == topic
        assert q["correct_answer"] in q["answer_options"]
        assert len(set(q["answer_options"])) == len(q["answer_options"]) >= 2
        assert q["ccss_standard"].startswith("K.")
        assert (q["figure"] is None) == (plan["figure"] is None)
        _assert_answer_is_right(scenario, plan, q)


def _assert_answer_is_right(scenario, plan, q):
    """Recomputed from what the student sees: the numbers shown, or the picture."""
    answer, shown, figure = q["correct_answer"], plan["shown"], q["figure"]
    counts = {"count_objects": lambda: figure["groups"][0]["count"],
              "count_ten_frames": lambda: figure["count"],
              "one_more": lambda: shown[0] + 1,
              "one_less": lambda: shown[0] - 1,
              "next_number": lambda: shown[2] + 1,
              "count_by_tens": lambda: shown[2] + 10,
              "make_ten": lambda: 10 - shown[0],
              "teen_make": lambda: 10 + shown[1],
              "teen_take_apart": lambda: figure["count"] - 10}
    if scenario in counts:
        assert answer == str(counts[scenario]())
    elif scenario in ("add", "add_story"):
        assert answer == str(shown[0] + shown[1]) and sum(shown) <= 10
    elif scenario in ("subtract", "subtract_story"):
        assert answer == str(shown[0] - shown[1]) and 0 < int(answer)
    elif scenario in ("larger_number", "largest_of_three"):
        assert answer == max(q["answer_options"], key=int)
    elif scenario == "smaller_number":
        assert answer == min(q["answer_options"], key=int)
    elif scenario == "compare_groups":
        a, b = figure["groups"]
        more = "more" in q["question_text"].lower()
        bigger = a if a["count"] > b["count"] else b
        smaller = b if bigger is a else a
        assert answer == kg.plural((bigger if more else smaller)["item"])
    elif scenario == "name_shape":
        assert answer == figure["shape"] and figure["named"] is False
    elif scenario in ("count_sides", "count_corners"):
        assert answer == str(question_figures.SHAPE_SIDES[figure["shape"]])
    else:
        raise AssertionError(f"no independent check for {scenario}")


@pytest.mark.parametrize("difficulty,top", [("easy", 5), ("medium", 10), ("hard", 20)])
def test_numbers_stay_inside_each_tier(model, monkeypatch, difficulty, top):
    """K.CC.5 counts to 20, K.OA works within 10, K.CC.7 compares to 10."""
    plans, _, _ = model
    for topic, scenarios in ((t, s[difficulty]) for t, s in kg.SCENARIOS.items()):
        for scenario in scenarios:
            for seed in range(25):
                _generate(topic, difficulty, scenario, seed, monkeypatch)
                plan = plans[-1]
                if scenario == "count_by_tens":
                    assert max(plan["shown"]) + 10 <= 100
                    continue
                if topic in ("counting",) and isinstance(plan["answer"], int):
                    assert plan["answer"] <= top
                if topic == "add_and_subtract":
                    assert max(plan["shown"] + [plan["answer"]]) <= (5 if difficulty == "easy" else 10)
                if scenario in ("larger_number", "smaller_number", "largest_of_three"):
                    assert max(plan["options"]) <= min(top, 10)


# ── the wording the model returns is checked, not trusted ───────────────────

def _plan_for(scenario, seed=3):
    topic, difficulty = next((t, d) for t, d, s in CELLS if s == scenario)
    return kg._plan(topic, scenario, difficulty, random.Random(seed))


@pytest.mark.parametrize("scenario,bad,why", [
    ("add", "Add. {a} + {b} = ? Hint: it is more than {extra}.", "shows numbers"),
    ("add", "Add the numbers.", "shows numbers"),
    ("one_more", "What number is one more than {a}? It is {answer}.", "shows numbers"),
    ("one_more", "What number is one more than {a}? It is {answer_word}.", "uses '{answer_word}'"),
    ("add_story", "There are {a} {items}. {b} more come, then they go away. How many now?", "uses 'away'"),
    ("subtract_story", "There are {a} {items}. Then {b} more come. How many are there now?", "uses none of"),
    ("name_shape", "What is the name of this {answer} shape?", "uses '{answer}'"),
    ("count_objects", "How many are there?", "uses none of"),
    ("larger_number", "Which number is smaller?", "uses none of"),
])
def test_a_reply_that_does_not_match_the_plan_is_refused(scenario, bad, why):
    plan = _plan_for(scenario)
    shown = plan["shown"] + [0, 0]
    answer = plan["answer"]
    # The plan's own item, so a story is refused for its verb and not for a missing noun.
    items = plan["require"][0][0] if plan["require"] else "apples"
    values = dict(a=shown[0], b=shown[1], extra=99, answer=answer, items=items,
                  answer_word=kg.NUMBER_WORDS[answer] if isinstance(answer, int) else answer)
    text = bad.format(**values)
    assert why.format(**values) in (kg.wording_problem(text, plan) or ""), text


def test_a_long_reply_is_refused():
    plan = _plan_for("count_objects")
    text = plan["example"] + " Look" + " very" * kg.MAX_WORDS + " closely."
    assert "too long" in kg.wording_problem(text, plan)


def test_a_dash_the_model_types_is_read_as_minus():
    plan = _plan_for("subtract")
    a, b = plan["shown"]
    assert kg.wording_problem(f"Subtract. {a} – {b} = ?", plan) is None


def test_three_refused_replies_raise_rather_than_serve(model, monkeypatch):
    _, prompts, said = model
    said["text"] = "What is 99 + 99?"
    with pytest.raises(ValueError):
        _generate("counting", "easy", "one_more", 1, monkeypatch)
    assert len(prompts) == 3


def test_the_prompt_names_the_numbers_and_the_grade(model, monkeypatch):
    plans, prompts, _ = model
    _generate("add_and_subtract", "medium", "add_story", 4, monkeypatch)
    a, b = plans[-1]["shown"]
    assert f"ONLY numbers in the question are: {a}, {b}" in prompts[-1]
    assert "Kindergarten" in prompts[-1]
