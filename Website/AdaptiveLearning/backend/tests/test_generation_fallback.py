"""A generator that gives up costs the student nothing: one more try, in another topic."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import collections  # noqa: E402

import pytest  # noqa: E402

import LLM_topic_decider as td  # noqa: E402
import llm_client  # noqa: E402
import ops_metrics  # noqa: E402


def _gave_up():
    return ValueError("Failed to generate valid JSON after retries")


def _script(monkeypatch, outcomes):
    """question_generation answering `outcomes` in turn (an Exception is raised); returns the calls."""
    calls = []

    def fake(topic, difficulty, _u, _g):
        calls.append((topic, difficulty))
        out = outcomes[len(calls) - 1]
        if isinstance(out, Exception):
            raise out
        return {"question_text": "q", "question_topic": topic}
    monkeypatch.setattr(td, "question_generation", fake)
    return calls


def test_a_generator_that_gives_up_is_followed_by_the_alternative_topic(monkeypatch):
    calls = _script(monkeypatch, [_gave_up(), None])

    q = td.generate_with_fallback("mode", "hard", "u", "6th Grade", ["median"])

    assert calls == [("mode", "hard"), ("median", "hard")]
    assert q["question_topic"] == "median" and q["fallback_from"] == "mode"
    assert ops_metrics.pending() == {("question", "generation_fallback"): 1}


def test_an_alternative_equal_to_the_topic_is_a_plain_retry(monkeypatch):
    calls = _script(monkeypatch, [_gave_up(), None])

    q = td.generate_with_fallback("mode", "easy", "u", "6th Grade", ["mode"])

    assert calls == [("mode", "easy"), ("mode", "easy")]
    assert "fallback_from" not in q


def test_a_question_that_arrives_first_time_makes_no_second_call(monkeypatch):
    calls = _script(monkeypatch, [None])

    td.generate_with_fallback("mode", "easy", "u", "6th Grade", ["median"])

    assert calls == [("mode", "easy")]
    assert ops_metrics.pending() == {}


def test_the_last_failure_is_raised_when_the_alternative_fails_too(monkeypatch):
    calls = _script(monkeypatch, [_gave_up(), ValueError("second")])

    with pytest.raises(ValueError, match="second"):
        td.generate_with_fallback("mode", "easy", "u", "6th Grade", ["median"])
    assert len(calls) == 2


@pytest.mark.parametrize("failure", [TimeoutError("30 s"), ConnectionError("down"),
                                     RuntimeError("401 bad key")])
def test_a_provider_failure_is_not_retried_in_another_topic(monkeypatch, failure):
    """A timeout would cost the student a second wait; a bad key fails again."""
    calls = _script(monkeypatch, [failure, None])

    with pytest.raises(type(failure)):
        td.generate_with_fallback("mode", "easy", "u", "6th Grade", ["median"])
    assert len(calls) == 1
    assert ops_metrics.pending() == {}


def test_a_ceiling_refusal_is_never_retried(monkeypatch):
    calls = _script(monkeypatch, [llm_client.GenerationUnavailable("ceiling"), None])

    with pytest.raises(llm_client.GenerationUnavailable):
        td.generate_with_fallback("mode", "easy", "u", "6th Grade", ["median"])
    assert len(calls) == 1


def test_the_adaptive_decider_falls_back_to_a_different_allowed_topic(monkeypatch):
    monkeypatch.setattr(td, "get_user_performance", lambda _u: type("R", (), {"data": []})())
    monkeypatch.setattr(td, "get_user_history",
                        lambda _u: collections.defaultdict(collections.deque))
    monkeypatch.setattr(td, "get_session_performance", lambda _s: None)
    monkeypatch.setattr(td, "get_session_signal_state", lambda _s, _u: None)
    monkeypatch.setattr(td, "_attach_stored_id", lambda _q, _d: None)
    monkeypatch.setattr(td.llm_client, "generate_text",
                        lambda _p: '{"topic": "mode", "difficulty": "hard"}')
    calls = _script(monkeypatch, [_gave_up(), None])

    q = td.LLM_single_prompt_topic_and_difficulty_decider("u1", "7th Grade")

    (first, _), (second, _) = calls
    assert first == "mode" and second != "mode" and second in td._allowed_topics("7th Grade")
    assert q["question_topic"] == second and q["fallback_from"] == "mode"
