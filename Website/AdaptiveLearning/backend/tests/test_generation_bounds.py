"""Generation bounds needing a user id; timeout and concurrency are in test_llm_client.py."""
import os
import threading

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import llm_client  # noqa: E402
import main  # noqa: E402
from conftest import tighten  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_counters(monkeypatch):
    monkeypatch.setattr(main._GENERATION_LIMITER, "hits", {})
    monkeypatch.setattr(main, "_prefetch_cache", {})
    monkeypatch.setattr(main, "_prefetch_active", {})


# ─── the per-student rate limit ──────────────────────────────────────────

def test_a_student_may_generate_up_to_the_limit_and_no_further(monkeypatch):
    tighten(monkeypatch, main._GENERATION_LIMITER, limit=3)
    assert [main._claim_generation_slot("kid") for _ in range(4)] == \
        [True, True, True, False]


def test_the_limit_is_per_student(monkeypatch):
    tighten(monkeypatch, main._GENERATION_LIMITER, limit=1)
    assert main._claim_generation_slot("kid-a") is True
    assert main._claim_generation_slot("kid-a") is False
    assert main._claim_generation_slot("kid-b") is True


def test_hits_older_than_the_window_stop_counting(monkeypatch):
    tighten(monkeypatch, main._GENERATION_LIMITER, limit=1, window=0.05)
    assert main._claim_generation_slot("kid") is True
    assert main._claim_generation_slot("kid") is False
    import time
    time.sleep(0.06)
    assert main._claim_generation_slot("kid") is True


def test_the_limit_bounds_volume_where_the_queue_bounds_concurrency(monkeypatch):
    """`_prefetch_active` caps calls in flight; this caps volume over time."""
    tighten(monkeypatch, main._GENERATION_LIMITER, limit=2)
    # Nothing in flight, and the third attempt is still refused.
    assert main._prefetch_active == {}
    assert [main._claim_generation_slot("kid") for _ in range(3)][-1] is False


# ─── what a refusal looks like from outside ──────────────────────────────

def _generate(monkeypatch, *, decider, session_id=None):
    monkeypatch.setattr(main.LLM_topic_decider,
                        "LLM_single_prompt_topic_and_difficulty_decider", decider)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "kid"})
    # Called directly, unfilled Query defaults arrive as truthy Query objects.
    return main.generate_question(request=None, grade="5th Grade",
                                  class_id=None, bias=0, session_id=session_id)


# ─── whose question it is ────────────────────────────────────────────────

def test_the_question_is_generated_for_the_caller_and_their_session(monkeypatch):
    """The decider reads consent by student id and signals by session id."""
    from test_access_control import _FakeSupabase
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        {"sessions": [{"id": "mine", "user_id": "kid"}]}))
    seen = []
    _generate(monkeypatch, session_id="mine",
              decider=lambda *a, **_k: seen.append(a) or {"question_text": "2+2"})
    assert seen[0][0] == "kid"
    assert seen[0][2] == "mine"


def _grade_generated(monkeypatch, *, sent=None, saved=None, class_id=None, class_grade=None):
    """The grade the decider was handed, for a student whose profile holds `saved`."""
    from test_access_control import _FakeSupabase
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        {"classes": [{"id": "c1", "grade_level": class_grade}],
         "profiles": [{"id": "kid", "grade_level": saved, "display_name": "Kid", "role": "student"}]}))
    seen = []
    monkeypatch.setattr(main.LLM_topic_decider, "LLM_single_prompt_topic_and_difficulty_decider",
                        lambda *a, **_k: seen.append(a) or {"question_text": "2+2"})
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "kid"})
    main.generate_question(request=None, grade=sent, class_id=class_id, bias=0, session_id=None)
    return seen[0][1]


def test_no_grade_is_generated_at_the_grade_the_topic_list_shows(monkeypatch):
    """No grade sent, saved or on a class: the grade `/api/topics` answers for, with no grade."""
    grade = _grade_generated(monkeypatch)
    assert main.list_topics(grade=grade) == main.list_topics(grade=None)


def test_no_grade_sent_is_generated_at_the_students_saved_grade(monkeypatch):
    """As the session prewarm and practice resolve it: a failed read in the page is not grade 1."""
    assert _grade_generated(monkeypatch, saved="7th Grade") == "7th Grade"
    assert _grade_generated(monkeypatch, sent="3rd Grade", saved="7th Grade") == "3rd Grade"


def test_the_saved_grade_is_read_alone_and_only_when_none_is_sent(monkeypatch):
    """Generation asks per question, so the fallback reads one column, not the profile row."""
    from test_access_control import _FakeSupabase
    fake = _FakeSupabase({"profiles": [{"id": "kid", "grade_level": "7th Grade", "display_name": "Kid"}]})
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main.LLM_topic_decider, "LLM_single_prompt_topic_and_difficulty_decider",
                        lambda *a, **_k: {"question_text": "2+2"})
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "kid"})
    main.generate_question(request=None, grade=None, class_id=None, bias=0, session_id=None)
    reads = [q for name, q in zip(fake.table_calls, fake.queries) if name == "profiles"]
    assert len(reads) == 1 and reads[0]._cols == ["grade_level"]
    main.generate_question(request=None, grade="3rd Grade", class_id=None, bias=0, session_id=None)
    assert fake.table_calls.count("profiles") == 1


def test_a_class_grade_wins_and_a_class_with_none_falls_back_to_the_students(monkeypatch):
    assert _grade_generated(monkeypatch, saved="7th Grade", class_id="c1", class_grade="2nd Grade") \
        == "2nd Grade"
    assert _grade_generated(monkeypatch, saved="7th Grade", class_id="c1") == "7th Grade"


def test_another_students_session_is_refused_before_anything_is_read(monkeypatch):
    from test_access_control import _FakeSupabase
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        {"sessions": [{"id": "theirs", "user_id": "another-child"}]}))
    reached = []
    with pytest.raises(HTTPException) as exc:
        _generate(monkeypatch, session_id="theirs",
                  decider=lambda *a, **_k: reached.append(a) or {"question_text": "2+2"})
    assert exc.value.status_code == 403
    assert reached == []


def test_a_generation_refusal_names_the_caller_in_the_security_log(monkeypatch):
    tighten(monkeypatch, main._GENERATION_LIMITER, limit=1)
    rows = []
    monkeypatch.setattr(main, "_record_security_event",
                        lambda kind, actor, subject=None, **d: rows.append((kind, actor, d)))
    decider = lambda *_a, **_k: {"question_text": "2+2"}  # noqa: E731
    _generate(monkeypatch, decider=decider)
    with pytest.raises(HTTPException):
        _generate(monkeypatch, decider=decider)
    assert rows == [("rate_limited", "kid", {"limiter": "generation"})]


def test_a_reached_ceiling_is_a_503_not_a_500(monkeypatch):
    """A ceiling is a deployment decision, never a silent fallback to another source."""
    def _refuse(*_a, **_k):
        raise llm_client.GenerationUnavailable("daily model-call ceiling reached")

    with pytest.raises(HTTPException) as exc:
        _generate(monkeypatch, decider=_refuse)
    assert exc.value.status_code == 503


def test_a_generation_that_genuinely_failed_is_still_a_500(monkeypatch):
    with pytest.raises(HTTPException) as exc:
        _generate(monkeypatch, decider=lambda *_a, **_k: None)
    assert exc.value.status_code == 500


def test_the_rate_limited_student_gets_a_429_with_a_retry_after(monkeypatch):
    tighten(monkeypatch, main._GENERATION_LIMITER, limit=1)
    called = []
    decider = lambda *_a, **_k: called.append(1) or {"question_text": "2+2"}  # noqa: E731

    _generate(monkeypatch, decider=decider)
    with pytest.raises(HTTPException) as exc:
        _generate(monkeypatch, decider=decider)
    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"]
    # Refused before the model is reached.
    assert len(called) == 1


# ─── the prefetch pool ───────────────────────────────────────────────────

def test_prefetch_runs_in_a_bounded_pool_not_an_unbounded_thread(monkeypatch):
    """QUEUE_SIZE is pinned: at its default of 0 nothing would be submitted."""
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
    """A shut-down pool; `submit` raises like the real one."""

    def submit(self, *_a, **_k):
        raise RuntimeError("cannot schedule new futures after shutdown")


def test_a_pool_that_refuses_the_work_does_not_leak_the_in_flight_count(monkeypatch):
    """A worker that never starts never decrements, so the queue would never refill."""
    monkeypatch.setattr(main, "_prefetch_pool", _DeadPool)
    main._ensure_queue("kid", "5th Grade", 0, None)
    assert main._prefetch_active == {}


def test_a_failed_refill_does_not_discard_the_question_already_built(monkeypatch):
    """`_ensure_queue` runs after the response is built; `_lifespan` can shut the pool first."""
    monkeypatch.setattr(main, "_prefetch_pool", _DeadPool)
    out = _generate(monkeypatch, decider=lambda *_a, **_k: {"question_text": "2+2"})
    assert out["question_text"] == "2+2"


def _queued_then_inline(monkeypatch, saved, prepared):
    """`ask(grade=, bias=)` for a student saved at `saved`, after preparing one question per (grade, bias)."""
    from test_access_control import _FakeSupabase
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        {"profiles": [{"id": "kid", "grade_level": saved, "display_name": "Kid"}]}))
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "kid"})
    monkeypatch.setattr(main.LLM_topic_decider, "LLM_single_prompt_topic_and_difficulty_decider",
                        lambda _uid, grade, _sid, bias, **_k: {"question_text": f"made for {grade} {bias}"})
    for grade, bias in prepared:
        main._prefetch_worker("kid", grade, bias, None)
    monkeypatch.setattr(main.LLM_topic_decider, "LLM_single_prompt_topic_and_difficulty_decider",
                        lambda _uid, grade, _sid, bias, **_k: {"question_text": f"inline for {grade} {bias}"})
    return lambda grade=None, bias=0: main.generate_question(
        request=None, grade=grade, class_id=None, bias=bias, session_id=None)["question_text"]


def test_a_question_prepared_for_another_grade_is_never_served(monkeypatch):
    """A prewarm at the default (a failed read at session start) must not reach a 7th grader."""
    ask = _queued_then_inline(monkeypatch, "7th Grade", [("1st Grade", 0), ("7th Grade", 0)])
    assert ask() == "made for 7th Grade 0"
    assert ask() == "inline for 7th Grade 0"
    # Kept for a switch back, not thrown away.
    assert ask(grade="1st Grade") == "made for 1st Grade 0"


def test_a_grade_written_another_way_is_the_same_grade(monkeypatch):
    """An old saved "Grade 7" and a prepared "7th Grade" are one grade, so the queue still serves."""
    ask = _queued_then_inline(monkeypatch, "Grade 7", [("7th Grade", 0)])
    assert ask() == "made for 7th Grade 0"


def test_a_question_prepared_at_another_difficulty_is_never_served(monkeypatch):
    """After "Easier", a question made while "Harder" was on is not served and labelled easy."""
    ask = _queued_then_inline(monkeypatch, "7th Grade", [("7th Grade", 1)])
    assert ask(bias=-1) == "inline for 7th Grade -1"
    assert ask(bias=1) == "made for 7th Grade 1"


def test_only_a_few_queues_are_kept_per_student(monkeypatch):
    """One per grade and bias ever picked would grow for the whole process; the served one stays."""
    prepared = [(f"{n}th Grade", 0) for n in (4, 5, 6, 7, 8)]
    ask = _queued_then_inline(monkeypatch, "8th Grade", prepared)
    ask(grade="4th Grade")
    queues = main._prefetch_cache["kid"]
    assert len(queues) == main._PREFETCH_KEPT_QUEUES
    assert list(queues)[-1] == main._prefetch_key("4th Grade", 0)


def test_a_rate_limited_prefetch_skips_generating_rather_than_raising(monkeypatch):
    tighten(monkeypatch, main._GENERATION_LIMITER, limit=1)
    main._claim_generation_slot("kid")
    called = []
    monkeypatch.setattr(main.LLM_topic_decider,
                        "LLM_single_prompt_topic_and_difficulty_decider",
                        lambda *_a, **_k: called.append(1))
    main._prefetch_active[("kid", main._prefetch_key("5th Grade", 0))] = 1
    main._prefetch_worker("kid", "5th Grade", 0, None)
    assert called == []
    # The in-flight counter is still released.
    assert main._prefetch_active == {}


def test_the_queue_is_off_by_default_so_nothing_is_generated_before_it_is_asked_for(monkeypatch):
    """QUEUE_SIZE defaults to 0: a prefetched question is billed even if never answered."""
    # The named constant, not main.QUEUE_SIZE, which reads the environment.
    assert main.QUESTION_QUEUE_SIZE_DEFAULT == 0, \
        "the source default changed; this is a billing decision"

    monkeypatch.setattr(main, "QUEUE_SIZE", 0)
    submitted = []
    monkeypatch.setattr(main, "_prefetch_pool",
                        lambda: type("P", (), {"submit": lambda _s, *a: submitted.append(a)})())
    main._ensure_queue("kid", "5th Grade", 0, None)
    assert submitted == []
    # A count left raised here would block the queue if later turned back on.
    assert main._prefetch_active == {}


def test_raising_the_queue_turns_prefetching_back_on(monkeypatch):
    monkeypatch.setattr(main, "QUEUE_SIZE", 3)
    submitted = []
    monkeypatch.setattr(main, "_prefetch_pool",
                        lambda: type("P", (), {"submit": lambda _s, *a: submitted.append(a)})())
    main._ensure_queue("kid2", "5th Grade", 0, None)
    assert len(submitted) == 3


def test_a_waiting_caller_is_refused_rather_than_holding_a_threadpool_slot(monkeypatch):
    """Caps callers blocked on llm_client's semaphore, which hold anyio's shared ~40 threads."""
    monkeypatch.setattr(main, "_generation_waiters", threading.BoundedSemaphore(1))

    with main._generation_waiter() as first:
        assert first is True
        with main._generation_waiter() as second:
            assert second is False, "the cap admitted a second waiter"

    # Released both times, including the refused one that held no permit.
    with main._generation_waiter() as again:
        assert again is True


def test_the_waiter_permit_survives_the_caller_raising(monkeypatch):
    """A leaked permit is permanent, and the refusal path raises inside the block."""
    monkeypatch.setattr(main, "_generation_waiters", threading.BoundedSemaphore(1))

    with pytest.raises(RuntimeError):
        with main._generation_waiter() as admitted:
            assert admitted is True
            raise RuntimeError("the model blew up")

    with main._generation_waiter() as after:
        assert after is True, "the permit leaked when the body raised"
