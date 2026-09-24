"""The decider offers the model the grade's topics and serves only a known difficulty."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import collections  # noqa: E402

import pytest  # noqa: E402

import LLM_topic_decider as td  # noqa: E402


def _rows(*rows):
    return type("R", (), {"data": list(rows)})()


def _row(topic, correct, attempted):
    return {"correct_questions": correct, "attempted_questions": attempted,
            "math_topics": {"topic_name": topic}}


@pytest.fixture
def decider(monkeypatch):
    """Runs the decider with the model's reply given; returns (prompts, served)."""
    prompts, served = [], []

    def run(reply, grade="9th Grade", performance=()):
        monkeypatch.setattr(td, "get_user_performance", lambda _u: _rows(*performance))
        monkeypatch.setattr(td, "get_user_history",
                            lambda _u: collections.defaultdict(collections.deque))
        monkeypatch.setattr(td, "get_session_performance", lambda _s: None)
        monkeypatch.setattr(td, "get_session_signal_state", lambda _s, _u: None)
        monkeypatch.setattr(td, "_attach_stored_id", lambda _q, _d: None)

        def generate_text(prompt):
            prompts.append(prompt)
            return reply
        monkeypatch.setattr(td.llm_client, "generate_text", generate_text)

        def question_generation(topic, difficulty, _u, _g):
            served.append((topic, difficulty))
            return {"question_text": "q"}
        monkeypatch.setattr(td, "question_generation", question_generation)

        return td.LLM_single_prompt_topic_and_difficulty_decider("u1", grade)
    return run, prompts, served


def _topics_line(prompt):
    lines = [line.strip() for line in prompt.splitlines()]
    return lines[lines.index("TOPICS:") + 1]


@pytest.mark.parametrize("grade", ["1st Grade", "3rd Grade", "7th Grade", "9th Grade", "12th Grade"])
def test_the_prompt_offers_exactly_the_grades_topics(decider, grade):
    run, prompts, _ = decider
    run('{"topic": "ordering", "difficulty": "easy"}', grade=grade)
    offered = [t.strip() for t in _topics_line(prompts[0]).split(",")]
    assert set(offered) == set(td._allowed_topics(grade))


def test_a_newer_topic_the_model_picks_is_served(decider):
    run, _, served = decider
    run('{"topic": "quadratics", "difficulty": "hard"}', grade="9th Grade")
    assert served == [("quadratics", "hard")]


@pytest.mark.parametrize("said,served_as", [("Medium", "medium"), (" HARD ", "hard")])
def test_a_differently_written_difficulty_is_read(decider, said, served_as):
    run, _, served = decider
    run(f'{{"topic": "mean", "difficulty": "{said}"}}', grade="7th Grade")
    assert served == [("mean", served_as)]


@pytest.mark.parametrize("said", ["moderate", "easy_or_medium_or_hard", "", 3])
def test_an_unknown_difficulty_falls_back_to_the_accuracy_rule(decider, said):
    run, _, served = decider
    value = f'"{said}"' if isinstance(said, str) else said
    question = run(f'{{"topic": "mean", "difficulty": {value}}}', grade="7th Grade",
                   performance=[_row("mean", 5, 10)])
    assert served == [("mean", "medium")]
    assert question["difficulty"] == "medium"


def test_the_fallback_serves_a_topic_the_student_never_attempted(decider):
    run, _, served = decider
    question = run("not json at all", grade="9th Grade")
    (topic, difficulty), = served
    assert topic in td._allowed_topics("9th Grade")
    assert difficulty == "easy"
    assert question["difficulty"] == "easy"


@pytest.mark.parametrize("grade", ["Kindergarten", "Pre-K", "Grade 0"])
def test_kindergarten_is_served_grade_ones_topics(grade):
    assert set(td._allowed_topics(grade)) == set(td._allowed_topics("1"))


def test_kindergarten_gets_a_question_either_way(decider):
    run, _, served = decider
    run('{"topic": "algebra", "difficulty": "easy"}', grade="Kindergarten")
    run("not json at all", grade="Kindergarten")
    assert [topic in td._allowed_topics("1") for topic, _ in served] == [True, True]
