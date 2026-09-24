"""The chart-explaining summary endpoint.

Structured so almost nothing here needs a database. `_chart_summary_basis` is
the only function that reads one, and it is tested for *which* reads it makes
rather than for what they return -- an absent heart figure cannot tell "asked
and found nothing" from "never asked", which is the distinction the consent
rule is about, so the assertion is on the call, not the payload.

Everything else -- the sentences, the numeric check, the channel-state
precedence, the four bounds -- is pure and is driven with hand-built basis
dicts.
"""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402


def _basis(**over):
    """A complete, ordinary basis. Every test changes one field of it.

    Built as a whole rather than assembled per test for the reason the
    frontend fixtures are builders: every interesting case here is one field
    off the happy path, and a test that restates the whole dict to move one
    field tends to move two.
    """
    base = {
        "days": 7, "weeks": 8, "face_included": True,
        "signals_retrieved": True, "trend_retrieved": True,
        "stats_retrieved": True, "topics_retrieved": True,
        "consent_retrieved": True,
        "channels": {
            "eeg":   {"enabled": True, "revoked_at": None, "samples": 400},
            "heart": {"enabled": True, "revoked_at": None, "samples": 120},
        },
        "averages": {"focus": 0.63, "stress": 0.41, "heart_rate_bpm": 72.4},
        "trend": {
            "focus": {"direction": "up", "first": 0.55, "last": 0.63,
                      "weeks_with_data": 4},
            "stress": {"direction": "steady", "first": 0.40, "last": 0.41,
                       "weeks_with_data": 4},
        },
        "academic": {"sessions": 12, "total_questions": 240,
                     "total_correct": 163, "accuracy": 68},
        "topics": {
            "weakest": {"topic_name": "angle_relationships", "accuracy": 42,
                        "attempted_questions": 20},
            "strongest": {"topic_name": "ordering", "accuracy": 91,
                          "attempted_questions": 30},
            "attempted_count": 5,
        },
    }
    base.update(over)
    return base


# ── the flag ─────────────────────────────────────────────────────────────

def test_the_flag_is_declared_so_an_unreadable_table_cannot_change_behaviour():
    """A key absent from `feature_flags` still has a value, and this is it.

    The map is also the whitelist, so a flag that is only ever read and never
    declared is a switch that controls nothing.
    """
    assert main._FEATURE_FLAG_DEFAULTS["chart_summary_llm_enabled"] is True


# ── _trend_direction ─────────────────────────────────────────────────────

def _weeks(*values):
    return [{"week_start": f"2026-0{i + 1}-01", "focus": v}
            for i, v in enumerate(values)]


def test_one_week_of_readings_is_a_value_not_a_direction():
    assert main._trend_direction(_weeks(0.5), "focus")["direction"] is None
    assert main._trend_direction([], "focus")["direction"] is None


def test_no_weeks_and_one_week_are_told_apart():
    """Returning None for both was the bug.

    A student part way through their very first session has raw rows -- so a
    focus average -- and no rollup row yet, so no week at all, and was told
    that one week has readings.
    """
    assert main._trend_direction([], "focus")["weeks_with_data"] == 0
    assert main._trend_direction(_weeks(0.5), "focus")["weeks_with_data"] == 1


def test_a_move_smaller_than_the_threshold_is_steady():
    """Below `_CHART_SUMMARY_TREND_MIN_DELTA` the difference is inside what a
    change of strap fit moves, so calling it a trend asserts more than the
    data carries."""
    move = main._trend_direction(_weeks(0.50, 0.52), "focus")
    assert move["direction"] == "steady"


def test_direction_is_reported_either_way_past_the_threshold():
    assert main._trend_direction(_weeks(0.50, 0.70), "focus")["direction"] == "up"
    assert main._trend_direction(_weeks(0.70, 0.50), "focus")["direction"] == "down"


def test_the_anchors_are_the_weeks_with_readings_not_the_ends_of_the_range():
    """A term with a fortnight off school ends in null weeks.

    Reading the last bucket rather than the last bucket *with a reading* would
    answer "no trend" for a series that moved.
    """
    weeks = [{"week_start": "a", "focus": 0.40},
             {"week_start": "b", "focus": None},
             {"week_start": "c", "focus": 0.80},
             {"week_start": "d", "focus": None}]
    move = main._trend_direction(weeks, "focus")
    assert (move["first"], move["last"], move["weeks_with_data"]) == (0.4, 0.8, 2)
    assert move["direction"] == "up"


# ── the numeric check ────────────────────────────────────────────────────

def test_the_allowed_figures_are_read_out_of_the_sentences_we_send():
    """Not enumerated from the basis fields.

    Enumerating was the first shape and rejected correct replies twice: the
    sentence prints a rounded heart rate where the basis holds a fractional
    one, and a revocation date puts a day number on screen that no basis field
    carries. Reading the text the model is actually handed closes both.
    """
    lines = main._rule_based_chart_summary(_basis())
    figures = main._chart_summary_figures(lines)
    assert 72.0 in figures, "the rounded heart rate the sentence prints"
    assert 68.0 in figures and 240.0 in figures


def test_a_revocation_date_is_a_figure_the_reply_may_repeat():
    basis = _basis()
    basis["channels"]["heart"] = {"enabled": False, "samples": 0,
                                  "revoked_at": "2026-08-03T16:00:00+00:00"}
    lines = main._rule_based_chart_summary(basis)
    assert any("3 August" in line for line in lines)
    assert 3.0 in main._chart_summary_figures(lines)


def test_a_number_we_did_not_supply_rejects_the_whole_reply():
    """The one check this endpoint has that the strategies pass does not."""
    lines = main._rule_based_chart_summary(_basis())
    allowed = main._chart_summary_figures(lines)
    reply = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines))
    assert main._validated_chart_summary(reply, allowed, len(lines)) is not None

    invented = reply.replace("68%", "77%")
    assert invented != reply
    assert main._validated_chart_summary(invented, allowed, len(lines)) is None


def test_a_thousands_separator_is_not_read_as_two_numbers():
    """"1,240" against a supplied 1240 is a correct reply formatted differently.

    Without absorbing the separator it reads as the numbers 1 and 240, neither
    of which is allowed, and every reply about a busy student is rejected for
    punctuation.
    """
    lines = ["A student has answered 1240 questions in total, which is a lot of practice."]
    allowed = main._chart_summary_figures(lines)
    reply = "1. A student has answered 1,240 questions in total, which is a lot of practice."
    assert main._validated_chart_summary(reply, allowed, 1) == [
        "A student has answered 1,240 questions in total, which is a lot of practice."]


def test_a_reply_that_drops_a_point_is_rejected():
    """Exactly the baseline's length, not a range.

    A reply with fewer points has dropped one silently, and most likely the
    channel-absence sentence -- the one point whose whole job is to say that
    something is missing.
    """
    lines = main._rule_based_chart_summary(_basis())
    allowed = main._chart_summary_figures(lines)
    short = "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines[:-1]))
    assert main._validated_chart_summary(short, allowed, len(lines)) is None


def test_a_clinical_term_anywhere_in_the_reply_rejects_it():
    lines = main._rule_based_chart_summary(_basis())
    allowed = main._chart_summary_figures(lines)
    reply = ("Here is what this suggests about their anxiety disorder:\n"
             + "\n".join(f"{i + 1}. {line}" for i, line in enumerate(lines)))
    assert main._validated_chart_summary(reply, allowed, len(lines)) is None


def test_a_degenerate_reply_of_fragments_is_rejected():
    """Well-formed and not a summary -- the floor, not just the ceiling."""
    assert main._validated_chart_summary("1. a\n2. b\n3. c", set(), 3) is None


# ── channel states ───────────────────────────────────────────────────────

def test_unreadable_consent_outranks_everything_else():
    """No claim about the family's decision has been earned.

    Ordered ahead of a revocation for the reason CLAUDE.md fixes for
    `cellLabel`: this is the one state where we know nothing about what they
    chose.
    """
    basis = _basis(consent_retrieved=False)
    basis["channels"]["heart"] = {"enabled": False, "samples": 0,
                                  "revoked_at": "2026-08-03T16:00:00+00:00"}
    note = main._channel_absence("heart", basis)
    assert "could not be read" in note
    assert "3 August" not in note


def test_a_known_revocation_outranks_a_failed_signal_read():
    """The revocation and its date come from a *different* query.

    When the signal read fails, what the family chose is still fully known;
    reporting the outage instead discards a fact we hold for one we do not.
    """
    basis = _basis(signals_retrieved=False)
    basis["channels"]["heart"] = {"enabled": False, "samples": 0,
                                  "revoked_at": "2026-08-03T16:00:00+00:00"}
    assert "turned off on 3 August" in main._channel_absence("heart", basis)


def test_a_failed_read_is_not_reported_as_nothing_recorded():
    basis = _basis(signals_retrieved=False)
    basis["channels"]["heart"] = {"enabled": True, "revoked_at": None, "samples": 0}
    assert "could not be read" in main._channel_absence("heart", basis)


def test_a_permitted_channel_with_no_samples_says_so_plainly():
    basis = _basis()
    basis["channels"]["heart"] = {"enabled": True, "revoked_at": None, "samples": 0}
    assert "nothing was recorded" in main._channel_absence("heart", basis)


def test_a_channel_with_a_reading_has_no_absence_note():
    assert main._channel_absence("heart", _basis()) is None


# ── the deterministic sentences ──────────────────────────────────────────

def test_a_failed_signal_read_never_reports_a_quiet_week():
    """`sessions` comes from the same aggregate as the averages, so a failed
    read leaves it at 0. Printing that reports a quiet week for a query that
    never ran."""
    # `sessions: 0` is part of the fixture, not decoration: a failed read
    # leaves the field at its default, and a test that kept the happy path's
    # 12 asserts that "0 sessions" is absent from a summary that was never
    # going to say it -- vacuous against the exact bug. Found by mutation:
    # removing the guard left the earlier version of this test green.
    basis = _basis(signals_retrieved=False)
    basis["academic"] = {**basis["academic"], "sessions": 0}
    lines = main._rule_based_chart_summary(basis)
    assert not any("0 sessions" in line for line in lines)
    assert any("could not be read" in line for line in lines)


def test_a_failed_trend_read_is_not_reported_as_a_first_week():
    """Empty and "only one week so far" are indistinguishable without this,
    and the second is a claim about how long the student has been practising
    made by a query that never ran."""
    lines = main._rule_based_chart_summary(
        _basis(trend_retrieved=False, trend={"focus": None, "stress": None}))
    assert not any("Only one week" in line for line in lines)
    assert any("term trend could not be read" in line for line in lines)


def test_the_eeg_channel_being_off_is_said_once_not_twice():
    """Focus and stress go off together, so two sentences read as two faults."""
    basis = _basis()
    basis["channels"]["eeg"] = {"enabled": False, "samples": 0, "revoked_at": None}
    lines = main._rule_based_chart_summary(basis)
    off = [line for line in lines if "turned off" in line and "Focus and stress" in line]
    assert len(off) == 1


def test_no_sentence_names_engagement_beside_focus():
    """They are one number (signal_mapping.py), so naming both reads as two
    measurements agreeing. The rule holds for a sentence exactly as it holds
    for a second chart line."""
    lines = main._rule_based_chart_summary(_basis())
    prompt = main._chart_summary_prompt(_basis(), lines)
    assert "engagement" not in " ".join(lines).lower()
    assert "engagement" not in prompt.lower()


def test_the_lifetime_totals_and_the_weekly_sessions_are_separate_sentences():
    """Joined, a lifetime accuracy reads as one earned over the last week."""
    lines = main._rule_based_chart_summary(_basis())
    totals = next(line for line in lines if "240" in line)
    assert "12 sessions" not in totals


def test_the_prompt_carries_no_student_identifier():
    """The shape of the week, not a record that identifies a child."""
    basis = _basis()
    prompt = main._chart_summary_prompt(basis, main._rule_based_chart_summary(basis))
    assert "student-1" not in prompt


# ── the endpoint ─────────────────────────────────────────────────────────

@pytest.fixture
def endpoint(monkeypatch):
    """The endpoint with its reads stubbed, so only its own logic is under test."""
    monkeypatch.setattr(main, "get_user", lambda r: {"id": "parent-1"})
    monkeypatch.setattr(main, "_verify_can_view_student", lambda v, s: None)
    monkeypatch.setattr(main, "_chart_summary_basis",
                        lambda *a, **k: _basis())
    return lambda: main.student_chart_summary(
        "student-1", None, main.ChartSummaryRequest())


def test_the_access_check_runs_before_the_rate_limit(monkeypatch):
    """Or a caller with no relationship gets a 429 masking the 403."""
    monkeypatch.setattr(main, "get_user", lambda r: {"id": "stranger"})

    def refuse(viewer, student_id):
        raise main.HTTPException(403, "You do not have access to this student")
    monkeypatch.setattr(main, "_verify_can_view_student", refuse)

    for _ in range(main._CHART_SUMMARY_RATE_LIMIT + 2):
        with pytest.raises(main.HTTPException) as e:
            main.student_chart_summary("student-1", None, main.ChartSummaryRequest())
        assert e.value.status_code == 403


def test_the_per_caller_rate_limit_answers_429_with_a_retry_after(endpoint, set_flag):
    set_flag("chart_summary_llm_enabled", False)
    for _ in range(main._CHART_SUMMARY_RATE_LIMIT):
        endpoint()
    with pytest.raises(main.HTTPException) as e:
        endpoint()
    assert e.value.status_code == 429
    assert int(e.value.headers["Retry-After"]) >= 1


def test_with_the_flag_off_no_socket_is_opened(endpoint, set_flag, monkeypatch):
    """Pinned off explicitly rather than left to the suite's default.

    The autouse flag fixture reads live from `_FEATURE_FLAG_DEFAULTS`, so a
    test about the deterministic path that relied on the default would start
    calling a model the day the default flipped -- which is exactly what
    happened to the strategies tests.
    """
    called = []
    monkeypatch.setattr(main, "_llm_chart_summary_bounded",
                        lambda *a: called.append(a))
    set_flag("chart_summary_llm_enabled", False)
    out = endpoint()
    assert called == []
    assert out["source"] == "rule-based"
    assert len(out["summary"]) >= 3


def test_a_rejected_model_reply_is_distinguishable_from_never_asking(
        endpoint, set_flag, monkeypatch):
    set_flag("chart_summary_llm_enabled", True)
    monkeypatch.setattr(main, "_llm_chart_summary_bounded", lambda *a: None)
    assert endpoint()["source"] == "rule-based (model output rejected)"


def test_an_accepted_model_reply_replaces_the_sentences_and_says_so(
        endpoint, set_flag, monkeypatch):
    set_flag("chart_summary_llm_enabled", True)
    monkeypatch.setattr(main, "_llm_chart_summary_bounded",
                        lambda *a: ["one", "two", "three"])
    out = endpoint()
    assert out["source"] == "model-phrased"
    assert out["summary"] == ["one", "two", "three"]


def test_the_three_reads_behind_one_response_report_separately(endpoint, set_flag):
    """Collapsed into one flag, a summary missing only its trend sentence is
    presented either as entirely fine or as entirely broken."""
    set_flag("chart_summary_llm_enabled", False)
    basis = endpoint()["basis"]
    assert {"signals_retrieved", "trend_retrieved",
            "stats_retrieved", "topics_retrieved"} <= set(basis)


# ── the four bounds ──────────────────────────────────────────────────────

def test_consent_is_read_once_and_passed_into_both_reads(monkeypatch):
    """Assert on what was asked for, not on what came back.

    An absent heart figure cannot tell "asked and discarded" from "never
    asked", and two consent reads could disagree about the same student inside
    one response.
    """
    reads = []
    channels = main.ReportChannels(heart=False, emotion=False, consent_retrieved=True)
    monkeypatch.setattr(main, "_reportable_channels",
                        lambda sid, inc=True: reads.append(sid) or channels)
    summary_args, trend_args = {}, {}
    monkeypatch.setattr(main, "_signal_summary",
                        lambda sid, days, **kw: summary_args.update(kw) or main._EMPTY_SUMMARY)
    monkeypatch.setattr(main, "_signal_trend",
                        lambda sid, weeks, **kw: trend_args.update(kw) or {"weeks": [], "retrieved": True})
    monkeypatch.setattr(main, "_stats_including_open_session",
                        lambda sid: {"total_questions": 0, "total_correct": 0, "retrieved": True})
    monkeypatch.setattr(main, "_topic_breakdown_with_state", lambda sid: ([], True))

    main._chart_summary_basis("student-1", 7, 8, True)
    assert reads == ["student-1"], "one consent read, shared by both queries"
    assert summary_args["include_heart"] is False
    assert trend_args["include_heart"] is False


def test_past_the_waiter_cap_the_model_pass_is_skipped(monkeypatch):
    """Bounding the workers does not bound how many callers are waiting.

    This endpoint is sync, so a caller blocked on the model holds one of
    anyio's shared threadpool slots; enough of them takes down every other
    sync endpoint in the app.
    """
    original = main._chart_summary_waiters
    main._chart_summary_waiters = threading.BoundedSemaphore(1)
    held = threading.Event()
    release = threading.Event()

    def _slow(*_a, **_k):
        held.set()
        release.wait(timeout=10)
        return ["a" * 40, "b" * 40, "c" * 40]

    monkeypatch.setattr(main, "_llm_chart_summary_admitted", _slow)
    try:
        blocker = threading.Thread(
            target=lambda: main._llm_chart_summary_bounded("p", ["x"]))
        blocker.start()
        assert held.wait(timeout=10)
        assert main._llm_chart_summary_bounded("p", ["x"]) is None, \
            "the second caller queued instead of being turned away"
        release.set()
        blocker.join(timeout=10)
    finally:
        release.set()
        main._chart_summary_waiters = original


def test_time_spent_queueing_comes_out_of_the_budget_it_was_promised():
    """The wait and the work share one deadline.

    Two of the same length measured from different moments would let a
    submission that queued behind a busy worker spend most of the caller's
    budget waiting, then start a fresh full timeout of its own -- keeping the
    pool saturated for close to twice the setting, against a deadline nobody
    is waiting on.

    Asserts `<=` the budget, never `==`: the claim is that the remainder is
    charged, and an exact comparison against a clock is the elapsed-time
    assertion CLAUDE.md rules out.
    """
    charged = []
    blocking, release = threading.Event(), threading.Event()

    def _work(prompt, _baseline, timeout=None):
        if prompt == "occupying":
            blocking.set()
            release.wait(timeout=10)
            return None
        charged.append(timeout)
        return None

    pool = ThreadPoolExecutor(max_workers=1)
    original_pool, original_llm = main._CHART_SUMMARY_LLM_POOL, main._llm_chart_summary
    original_timeout = main.CHART_SUMMARY_LLM_TIMEOUT
    main._CHART_SUMMARY_LLM_POOL = pool
    main._llm_chart_summary = _work
    main.CHART_SUMMARY_LLM_TIMEOUT = 5.0
    try:
        pool.submit(_work, "occupying", [])
        assert blocking.wait(timeout=10)
        # Let the queued call spend some of its budget waiting.
        waiter = threading.Thread(
            target=lambda: main._llm_chart_summary_admitted("queued", ["x"]))
        waiter.start()
        time.sleep(0.4)
        release.set()
        waiter.join(timeout=10)
    finally:
        release.set()
        pool.shutdown(wait=True)
        main._CHART_SUMMARY_LLM_POOL = original_pool
        main._llm_chart_summary = original_llm
        main.CHART_SUMMARY_LLM_TIMEOUT = original_timeout

    assert charged and charged[0] <= 5.0 - 0.3, \
        "the queued call was given a fresh budget instead of the remainder"


def test_an_abandoned_call_is_cancelled_rather_than_left_queued():
    """Bounding the workers does not bound the queue behind them.

    Left uncancelled, a sustained outage accumulates prompts nobody is waiting
    for, and every one of them is still holding a slot in the pool's work
    queue when the provider recovers.

    Asserted on the future's own state, not on whether the work ran. The
    obvious version of this test -- occupy the worker, let the wait time out,
    then check the prompt never reached the model -- passes with
    `future.cancel()` deleted, and that is not a weak assertion but a true
    one about a second mechanism: the wait and the work share one deadline, so
    an item that starts after the wait timed out finds `remaining <= 0` and
    returns without opening a socket. The call is prevented either way. What
    cancelling adds is that the item leaves the queue, which is the thing the
    outage was piling up. Found by mutation.
    """
    submitted = []
    release = threading.Event()

    def _work(*_a, **_k):
        release.wait(timeout=10)
        return None

    class _Recording:
        """The real pool, with every future it hands out kept."""
        def __init__(self, pool):
            self._pool = pool

        def submit(self, fn, *a, **kw):
            future = self._pool.submit(fn, *a, **kw)
            submitted.append(future)
            return future

    pool = ThreadPoolExecutor(max_workers=1)
    original_pool, original_llm = main._CHART_SUMMARY_LLM_POOL, main._llm_chart_summary
    original_timeout = main.CHART_SUMMARY_LLM_TIMEOUT
    main._CHART_SUMMARY_LLM_POOL = _Recording(pool)
    main._llm_chart_summary = _work
    main.CHART_SUMMARY_LLM_TIMEOUT = 0.05
    try:
        blocker = pool.submit(_work)
        assert main._llm_chart_summary_bounded("queued", ["x"]) is None
        assert submitted, "nothing was submitted, so nothing was under test"
        assert submitted[0].cancelled(), \
            "the abandoned work is still sitting in the pool's queue"
        release.set()
        blocker.result(timeout=10)
    finally:
        release.set()
        pool.shutdown(wait=True)
        main._CHART_SUMMARY_LLM_POOL = original_pool
        main._llm_chart_summary = original_llm
        main.CHART_SUMMARY_LLM_TIMEOUT = original_timeout


def test_the_pool_is_shut_down_on_the_way_out():
    """A pool left running holds a worker stuck in a socket read against a
    stalled provider, and the global must reset so a reload builds a fresh
    one rather than reusing a shut-down pool."""
    original = main._CHART_SUMMARY_LLM_POOL
    try:
        pool = main._chart_summary_pool()
        assert pool is main._chart_summary_pool(), "built twice"
        main._shutdown_chart_summary_pool()
        assert main._CHART_SUMMARY_LLM_POOL is None
        assert main._chart_summary_pool() is not pool
    finally:
        main._shutdown_chart_summary_pool()
        main._CHART_SUMMARY_LLM_POOL = original


def test_a_named_topic_carries_three_fields_and_not_the_whole_row(monkeypatch):
    """`_topic_breakdown` rows carry a `stress` reading, and this response
    never asked for one -- a signal reaching a surface that did not gate on
    consent for it. The same rule the strategies basis already follows for the
    weakest topic, applied to the strongest one beside it."""
    row = {"topic_id": "t1", "topic_name": "ordering", "accuracy": 91,
           "attempted_questions": 30, "correct_questions": 27,
           "stress": 0.8, "updated_at": "2026-09-01"}
    monkeypatch.setattr(main, "_reportable_channels",
                        lambda sid, inc=True: main.ReportChannels(
                            heart=False, emotion=False, consent_retrieved=True))
    monkeypatch.setattr(main, "_signal_summary", lambda *a, **k: main._EMPTY_SUMMARY)
    monkeypatch.setattr(main, "_signal_trend",
                        lambda *a, **k: {"weeks": [], "retrieved": True})
    monkeypatch.setattr(main, "_stats_including_open_session",
                        lambda sid: {"total_questions": 0, "total_correct": 0, "retrieved": True})
    monkeypatch.setattr(main, "_topic_breakdown_with_state", lambda sid: ([row], True))

    topics = main._chart_summary_basis("student-1", 7, 8, True)["topics"]
    for named in (topics["weakest"], topics["strongest"]):
        assert set(named) == {"topic_name", "accuracy", "attempted_questions"}


def test_a_failed_topics_read_is_not_reported_as_an_untouched_subject():
    """`_topic_breakdown` swallows its exception and answers `[]`.

    Most callers cannot act on the difference -- the strategies endpoint
    degrades to generic advice either way -- but this is the first surface
    where the empty list becomes an assertion, so the outage would arrive as
    "no topic has been attempted yet": a claim about the child made by a query
    that never ran.
    """
    basis = _basis(topics_retrieved=False)
    basis["topics"] = {"weakest": None, "strongest": None, "attempted_count": 0}
    lines = main._rule_based_chart_summary(basis)
    assert not any("No topic has been attempted yet" in line for line in lines)
    assert any("topic figures could not be read" in line for line in lines)


def test_the_topics_flag_outranks_having_no_attempted_topic():
    """Ordered ahead of every topic shape below it.

    A failed read and a genuinely untouched subject both arrive as an empty
    list, so the flag has to be consulted before the list's contents are.
    """
    basis = _basis(topics_retrieved=False)
    lines = main._rule_based_chart_summary(basis)
    assert any("topic figures could not be read" in line for line in lines)
    assert not any("strongest attempted" in line for line in lines)


def test_the_read_state_is_reported_by_the_two_value_form(monkeypatch):
    """The plain name returns rows, the two-value name returns the state.

    Split rather than parameterised so a caller that did not know to ask for
    the flag cannot silently drop it.
    """
    class _Boom:
        def table(self, _name):
            raise RuntimeError("database unreachable")

    monkeypatch.setattr(main, "supabase", _Boom())
    rows, retrieved = main._topic_breakdown_with_state("student-1")
    assert rows == [] and retrieved is False
    # The plain form still answers with the rows alone, for its other callers.
    assert main._topic_breakdown("student-1") == []


def test_zero_weeks_is_not_reported_as_one_week():
    """The average comes from raw rows, the trend from the rollup, and the
    rollup row is not written until the session closes -- so zero weeks is an
    ordinary state, not an error."""
    basis = _basis(trend={"focus": main._trend_direction([], "focus"),
                          "stress": main._trend_direction([], "stress")})
    lines = main._rule_based_chart_summary(basis)
    assert not any("Only one week" in line for line in lines)
    assert any("No week has a reading for it yet" in line for line in lines)


def test_the_zero_week_sentence_names_no_cause():
    """A first session is one way to reach it. A rollup writer that failed on
    every day in range, and a set of rolled days all carrying null for this
    series, are two others -- and the read succeeded in all three, so nothing
    here can tell them apart.

    Naming the first contradicted the session count two sentences above
    whenever one of the others was the real one, which is what this asserts
    against: the summary already says twelve sessions were recorded.
    """
    basis = _basis(trend={"focus": main._trend_direction([], "focus"),
                          "stress": main._trend_direction([], "stress")})
    assert basis["academic"]["sessions"] > 1, "the fixture has to contradict it"
    lines = main._rule_based_chart_summary(basis)

    joined = " ".join(lines)
    assert "this session's own readings" not in joined
    for claim in ("first session", "hasn't finished", "has not finished",
                  "not been written", "just started"):
        assert claim not in joined, f"the sentence explains itself with {claim!r}"


def test_one_week_still_says_one_week():
    """The fix must not collapse the two cases the other way."""
    one = main._trend_direction(_weeks(0.5), "focus")
    lines = main._rule_based_chart_summary(_basis(trend={"focus": one, "stress": one}))
    assert any("Only one week has readings" in line for line in lines)


def test_a_single_session_is_not_described_in_the_plural():
    basis = _basis()
    basis["academic"] = {**basis["academic"], "sessions": 1}
    lines = main._rule_based_chart_summary(basis)
    assert any("1 session was recorded" in line for line in lines)
    assert not any("1 sessions" in line for line in lines)


def test_the_basis_carries_the_read_state_it_was_given(monkeypatch):
    """The wiring, not just the field's presence.

    Every other test here builds a basis by hand, so none of them exercises
    the path from `_topic_breakdown_with_state` into the payload -- and
    hardcoding the field to True survived the whole suite. Found by mutation.
    """
    monkeypatch.setattr(main, "_reportable_channels",
                        lambda sid, inc=True: main.ReportChannels(
                            heart=False, emotion=False, consent_retrieved=True))
    monkeypatch.setattr(main, "_signal_summary", lambda *a, **k: main._EMPTY_SUMMARY)
    monkeypatch.setattr(main, "_signal_trend",
                        lambda *a, **k: {"weeks": [], "retrieved": True})
    monkeypatch.setattr(main, "_stats_including_open_session",
                        lambda sid: {"total_questions": 0, "total_correct": 0, "retrieved": True})

    monkeypatch.setattr(main, "_topic_breakdown_with_state", lambda sid: ([], False))
    assert main._chart_summary_basis("s", 7, 8, True)["topics_retrieved"] is False

    monkeypatch.setattr(main, "_topic_breakdown_with_state", lambda sid: ([], True))
    assert main._chart_summary_basis("s", 7, 8, True)["topics_retrieved"] is True
