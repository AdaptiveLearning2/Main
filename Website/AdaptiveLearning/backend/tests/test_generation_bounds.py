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
    a function of how many children pressed start at once.

    QUEUE_SIZE is pinned rather than inherited: it now defaults to 0, and at 0
    this test would assert `0 == 0` after submitting nothing -- passing while
    exercising none of the pooling it exists to check.
    """
    monkeypatch.setattr(main, "QUEUE_SIZE", 2)
    pool = main._prefetch_pool()
    assert pool._max_workers == llm_client.GENERATION_MAX_CONCURRENCY

    submitted = []
    monkeypatch.setattr(main, "_prefetch_pool",
                        lambda: type("P", (), {"submit": lambda _s, *a: submitted.append(a)})())
    before = threading.active_count()
    main._ensure_queue("kid", "5th Grade", 0, None)
    assert len(submitted) == main.QUEUE_SIZE
    assert threading.active_count() == before


class _DeadPool:
    """A pool that has been shut down. `submit` raises exactly like the real one."""

    def submit(self, *_a, **_k):
        raise RuntimeError("cannot schedule new futures after shutdown")


def test_a_pool_that_refuses_the_work_does_not_leak_the_in_flight_count(monkeypatch):
    """This is the durable half, and it is not only about shutdown.

    `_prefetch_worker` owns the decrement, in its `finally`. A worker that never
    starts never runs one -- so without the rollback this student's in-flight
    count stays raised for the life of the process, `needed` is <= 0 from then
    on, and their queue never refills again.
    """
    monkeypatch.setattr(main, "_prefetch_pool", _DeadPool)
    main._ensure_queue("kid", "5th Grade", 0, None)
    assert main._prefetch_active.get("kid", 0) == 0


def test_a_failed_refill_does_not_discard_the_question_already_built(monkeypatch):
    """`_ensure_queue` runs *after* the response is assembled, so letting a
    refill failure out turns a served question into a 500.

    The window is real rather than theoretical: `_ensure_queue` releases the
    lock before submitting, so `_lifespan` can shut the pool in between.
    """
    monkeypatch.setattr(main, "_prefetch_pool", _DeadPool)
    out = _generate(monkeypatch, decider=lambda *_a, **_k: {"question_text": "2+2"})
    assert out["question_text"] == "2+2"


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


def test_the_queue_is_off_by_default_so_nothing_is_generated_before_it_is_asked_for(monkeypatch):
    """QUEUE_SIZE defaults to 0, and that is a spend decision, not a tuning one.

    A queued question is billed when it is generated and only earns its cost
    when a student answers it, so any prefetch depth > 0 pays for the questions
    of every student who closes the tab. Asserted on the default value *and* on
    the behaviour, because a default nothing reads is not a default.
    """
    # Asserted on the *default resolution* with no env var set, not on
    # main.QUEUE_SIZE, which resolves QUESTION_QUEUE_SIZE at import: reading
    # the module value makes setting the very env var this exists to expose
    # turn the suite red with a message blaming a source change that did not
    # happen. Same trap CLAUDE.md documents for a locally-edited
    # EEGResearch/.env, and the same shape test_llm_client.py avoids for
    # LLM_PROVIDER.
    monkeypatch.delenv("QUESTION_QUEUE_SIZE", raising=False)
    assert main._env_number("QUESTION_QUEUE_SIZE", 0, int, minimum=0) == 0,         "the source default changed; this is a billing decision"

    # The behaviour is pinned explicitly rather than inherited, so this half
    # holds whatever the deployment has configured.
    monkeypatch.setattr(main, "QUEUE_SIZE", 0)
    submitted = []
    monkeypatch.setattr(main, "_prefetch_pool",
                        lambda: type("P", (), {"submit": lambda _s, *a: submitted.append(a)})())
    main._ensure_queue("kid", "5th Grade", 0, None)
    assert submitted == []
    # And nothing was recorded as in flight -- a count left raised here would
    # keep `needed` <= 0 for the life of the process if the queue were later
    # turned back on for this user.
    assert main._prefetch_active.get("kid", 0) == 0


def test_raising_the_queue_turns_prefetching_back_on(monkeypatch):
    """The setting is the only thing standing between the two behaviours, so a
    deployment can restore prefetching without a code change."""
    monkeypatch.setattr(main, "QUEUE_SIZE", 3)
    submitted = []
    monkeypatch.setattr(main, "_prefetch_pool",
                        lambda: type("P", (), {"submit": lambda _s, *a: submitted.append(a)})())
    main._ensure_queue("kid2", "5th Grade", 0, None)
    assert len(submitted) == 3


def test_a_waiting_caller_is_refused_rather_than_holding_a_threadpool_slot(monkeypatch):
    """The fourth bound CLAUDE.md requires, and the one generation lacked.

    GENERATION_MAX_CONCURRENCY bounds calls *in flight*; this bounds callers
    blocked waiting to become one. Only the second protects the threadpool:
    llm_client's semaphore is acquired in the caller's own thread, and FastAPI
    runs these sync endpoints on anyio's shared ~40-slot pool -- so a class
    starting together would sit in those slots for up to GENERATION_LLM_TIMEOUT
    and queue the signal-ingest endpoints behind them. The per-student rate
    limit does not help: that is one student over time, this is many students
    at one instant.
    """
    monkeypatch.setattr(main, "_generation_waiters", threading.BoundedSemaphore(1))

    with main._generation_waiter() as first:
        assert first is True
        with main._generation_waiter() as second:
            assert second is False, "the cap admitted a second waiter"

    # Released on the way out, both times -- including the refused one, which
    # never held a permit to give back.
    with main._generation_waiter() as again:
        assert again is True


def test_the_waiter_permit_survives_the_caller_raising(monkeypatch):
    """A leaked permit is permanent: the cap is a BoundedSemaphore acquired
    without a timeout, so once they all leak generation is off for the life of
    the process. The refusal path raises HTTPException *inside* the block, so
    this is the ordinary case rather than an edge one."""
    monkeypatch.setattr(main, "_generation_waiters", threading.BoundedSemaphore(1))

    with pytest.raises(RuntimeError):
        with main._generation_waiter() as admitted:
            assert admitted is True
            raise RuntimeError("the model blew up")

    with main._generation_waiter() as after:
        assert after is True, "the permit leaked when the body raised"
