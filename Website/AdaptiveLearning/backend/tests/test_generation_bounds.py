"""The bounds around question generation.

CLAUDE.md states the rule for the one model-backed endpoint that existed before
this: a timeout enforced by waiting, a bounded pool, a waiter cap, and a
per-user rate limit -- and that *any* further model-backed endpoint needs the
same four. Question generation reaches a model from thirteen places on the
hottest path in the product and had none of them, because a local Ollama was
free and nobody could run up a bill with it.

The per-call timeout and the process-wide concurrency ceiling live in
`llm_client` (see `test_llm_client.py`); the two halves that need a user id --
the per-student rate limit and what a refusal looks like to a caller -- are
here.
"""
import os
import threading

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import llm_client  # noqa: E402
import main  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_counters(monkeypatch):
    monkeypatch.setattr(main, "_generation_hits", {})
    monkeypatch.setattr(main, "_prefetch_cache", {})
    monkeypatch.setattr(main, "_prefetch_active", {})


# ─── the per-student rate limit ──────────────────────────────────────────

def test_a_student_may_generate_up_to_the_limit_and_no_further(monkeypatch):
    monkeypatch.setattr(main, "_GENERATION_RATE_LIMIT", 3)
    assert [main._claim_generation_slot("kid") for _ in range(4)] == \
        [True, True, True, False]


def test_the_limit_is_per_student(monkeypatch):
    """One child working quickly must not stop the rest of the class."""
    monkeypatch.setattr(main, "_GENERATION_RATE_LIMIT", 1)
    assert main._claim_generation_slot("kid-a") is True
    assert main._claim_generation_slot("kid-a") is False
    assert main._claim_generation_slot("kid-b") is True


def test_hits_older_than_the_window_stop_counting(monkeypatch):
    """Timed on monotonic(), so a clock adjustment can neither wipe the window
    nor extend it -- same reasoning as the strategy limiter."""
    monkeypatch.setattr(main, "_GENERATION_RATE_LIMIT", 1)
    monkeypatch.setattr(main, "_GENERATION_RATE_WINDOW", 0.05)
    assert main._claim_generation_slot("kid") is True
    assert main._claim_generation_slot("kid") is False
    import time
    time.sleep(0.06)
    assert main._claim_generation_slot("kid") is True


def test_the_limit_bounds_volume_where_the_queue_bounds_concurrency(monkeypatch):
    """These are different quantities, and only one of them existed.

    `_prefetch_active` caps a student at QUEUE_SIZE calls *in flight*. A student
    answering steadily for an hour never exceeds that and still generates
    hundreds of questions, which was free against a local model and is not
    against a metered one.
    """
    monkeypatch.setattr(main, "_GENERATION_RATE_LIMIT", 2)
    # Nothing is in flight -- the concurrency counter is untouched -- and the
    # third attempt is still refused.
    assert main._prefetch_active == {}
    assert [main._claim_generation_slot("kid") for _ in range(3)][-1] is False


# ─── what a refusal looks like from outside ──────────────────────────────

def _generate(monkeypatch, *, decider):
    monkeypatch.setattr(main.LLM_topic_decider,
                        "LLM_single_prompt_topic_and_difficulty_decider", decider)
    # Every parameter explicitly: called directly rather than through the app,
    # the unfilled Query defaults arrive as Query objects, and `class_id` in
    # particular is truthy enough to send the handler at a real database.
    return main.generate_question(user_id="kid", grade="5th Grade",
                                  class_id=None, bias=0, session_id=None)


def test_a_reached_ceiling_is_a_503_not_a_500(monkeypatch):
    """A ceiling is a decision this deployment made, not something that broke.

    The alternative -- quietly serving a question from the bank, or from a
    cheaper model -- would change what a child is asked with nothing on any
    surface saying so.
    """
    def _refuse(*_a, **_k):
        raise llm_client.GenerationUnavailable("daily model-call ceiling reached")

    with pytest.raises(HTTPException) as exc:
        _generate(monkeypatch, decider=_refuse)
    assert exc.value.status_code == 503


def test_a_generation_that_genuinely_failed_is_still_a_500(monkeypatch):
    """The two must not collapse into one code: a 503 says come back later and
    a 500 says something is wrong, and only one of them is worth paging over."""
    with pytest.raises(HTTPException) as exc:
        _generate(monkeypatch, decider=lambda *_a, **_k: None)
    assert exc.value.status_code == 500


def test_the_rate_limited_student_gets_a_429_with_a_retry_after(monkeypatch):
    monkeypatch.setattr(main, "_GENERATION_RATE_LIMIT", 1)
    called = []
    decider = lambda *_a, **_k: called.append(1) or {"question_text": "2+2"}  # noqa: E731

    _generate(monkeypatch, decider=decider)
    with pytest.raises(HTTPException) as exc:
        _generate(monkeypatch, decider=decider)
    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"]
    # The refusal happens before the model is reached, or the limit would bound
    # nothing that costs anything.
    assert len(called) == 1


# ─── the prefetch pool ───────────────────────────────────────────────────

def test_prefetch_runs_in_a_bounded_pool_not_an_unbounded_thread(monkeypatch):
    """It used to be a bare daemon thread per queued question, so the peak was
    a function of how many children pressed start at once."""
    pool = main._prefetch_pool()
    assert pool._max_workers == llm_client.GENERATION_MAX_CONCURRENCY

    submitted = []
    monkeypatch.setattr(main, "_prefetch_pool",
                        lambda: type("P", (), {"submit": lambda _s, *a: submitted.append(a)})())
    before = threading.active_count()
    main._ensure_queue("kid", "5th Grade", 0, None)
    assert len(submitted) == main.QUEUE_SIZE
    assert threading.active_count() == before


def test_a_rate_limited_prefetch_skips_generating_rather_than_raising(monkeypatch):
    """There is no request to fail here, and a short queue is invisible -- the
    next question is simply generated inline."""
    monkeypatch.setattr(main, "_GENERATION_RATE_LIMIT", 1)
    main._claim_generation_slot("kid")
    called = []
    monkeypatch.setattr(main.LLM_topic_decider,
                        "LLM_single_prompt_topic_and_difficulty_decider",
                        lambda *_a, **_k: called.append(1))
    main._prefetch_active["kid"] = 1
    main._prefetch_worker("kid", "5th Grade", 0, None)
    assert called == []
    # The in-flight counter is still released, or the queue would never refill
    # again for this student.
    assert main._prefetch_active["kid"] == 0
