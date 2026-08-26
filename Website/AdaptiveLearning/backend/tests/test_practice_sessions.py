"""The self-study practice mode: topic(s)/difficulty/grade picked explicitly,
questions generated through the same LLM pipeline the live Adaptive session
uses, no EEG/camera involvement, and tracking kept out of the tables the live
adaptive engine and its `sessions`-table close machinery read.

Three things this file exists to pin, beyond the ordinary ownership/shape
checks every endpoint here needs:

1. A topic outside `_allowed_topics(grade)` is refused at `/start`, not just
   greyed out client-side.
2. `/answer` and `/view` never touch `user_math_performance` /
   `record_topic_attempt` -- that table drives the *live* adaptive engine's
   own topic/difficulty choice, and letting an untimed, explicitly-picked
   practice answer feed it would defeat the point of a separate mode.
3. `end_practice_session` must never be discoverable by
   `conftest.close_sites()`. PR #152 added a background sweep that scans every
   function in `main` for the literal substring `_close_session(` or
   `"ended_at":` and, for anything found that isn't `end_session`, demands
   `CLOSED_BY_SWEEP` appear in its source. Practice sessions are a different
   table with none of that machinery, so tripping that scan would fail a test
   this file has no way to satisfy on purpose.
"""
import collections
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import llm_client  # noqa: E402
import main  # noqa: E402

USER = "student-1"
OTHER_USER = "student-2"
SESSION = "practice-1"


class _Client:
    """A hand-rolled fake covering exactly the tables/RPC the practice
    endpoints touch, in the same spirit as test_answer_recording.py's
    `_Client` -- small and endpoint-specific rather than the large shared
    fake in test_access_control.py.
    """

    def __init__(self, sessions=None, answers=None, questions=None):
        self.sessions = {s["id"]: dict(s) for s in (sessions or [])}
        self.answers = list(answers or [])
        self.questions = {q["id"]: dict(q) for q in (questions or [])}
        self.inserted_sessions = []
        self.inserted_answers = []
        self.updates = []          # (table, row, id)
        self.rpcs = []
        self.tables_touched = set()
        self._next_id = 1

    def _new_id(self, prefix):
        self._next_id += 1
        return f"{prefix}-{self._next_id}"

    def rpc(self, name, params):
        client = self

        class _R:
            def execute(self):
                client.rpcs.append((name, params))
                return type("R", (), {"data": None})()

        return _R()

    def table(self, name):
        self.tables_touched.add(name)
        client = self

        class _Q:
            def __init__(self):
                self._filters = {}
                self._single = False
                self._insert = None
                self._update = None
                self._order = None
                self._limit = None

            def select(self, *_a, **_k):
                return self

            def eq(self, col, val):
                self._filters[col] = val
                return self

            def order(self, col, desc=False, **_k):
                self._order = (col, desc)
                return self

            def limit(self, n, *_a, **_k):
                self._limit = n
                return self

            def single(self):
                self._single = True
                return self

            def insert(self, row):
                self._insert = row
                return self

            def update(self, row):
                self._update = row
                return self

            def _matching(self, rows):
                out = rows
                for col, val in self._filters.items():
                    out = [r for r in out if r.get(col) == val]
                return out

            def execute(self):
                if name == "practice_sessions":
                    if self._insert is not None:
                        row = dict(self._insert)
                        row.setdefault("id", client._new_id("practice"))
                        row.setdefault("ended_at", None)
                        row.setdefault("questions_answered", 0)
                        row.setdefault("correct_answers", 0)
                        row.setdefault("topic_summary", {})
                        client.sessions[row["id"]] = row
                        client.inserted_sessions.append(row)
                        return type("R", (), {"data": [row]})()
                    if self._update is not None:
                        sid = self._filters.get("id")
                        row = client.sessions.get(sid)
                        if row is not None:
                            row.update(self._update)
                        client.updates.append(("practice_sessions", dict(self._update), sid))
                        return type("R", (), {"data": [row] if row else []})()
                    rows = self._matching(list(client.sessions.values()))
                    if self._order:
                        col, desc = self._order
                        rows = sorted(rows, key=lambda r: r.get(col) or "", reverse=desc)
                    if self._limit is not None:
                        rows = rows[:self._limit]
                    if self._single:
                        return type("R", (), {"data": rows[0] if rows else None})()
                    return type("R", (), {"data": rows})()

                if name == "practice_session_answers":
                    if self._insert is not None:
                        row = dict(self._insert)
                        row.setdefault("id", client._new_id("answer"))
                        client.answers.append(row)
                        client.inserted_answers.append(row)
                        return type("R", (), {"data": [row]})()
                    rows = self._matching(client.answers)
                    return type("R", (), {"data": rows})()

                if name == "questions":
                    qid = self._filters.get("id")
                    row = client.questions.get(qid)
                    return type("R", (), {"data": row})()

                raise AssertionError(f"unexpected table in this fake: {name}")

        return _Q()


@pytest.fixture
def _client(monkeypatch):
    def _install(**kw):
        c = _Client(**kw)
        monkeypatch.setattr(main, "supabase", c)
        return c
    return _install


def _as(monkeypatch, user_id):
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": user_id})


# ─── GET /api/topics ─────────────────────────────────────────────────────

def test_topics_marks_which_are_allowed_at_a_young_grade():
    topics = main.list_topics(grade="2nd Grade")
    by_name = {t["name"]: t["allowed"] for t in topics}
    assert by_name["ordering"] is True
    assert by_name["algebra"] is False
    assert by_name["probability"] is False


def test_topics_allows_everything_from_sixth_grade_up():
    topics = main.list_topics(grade="8th Grade")
    assert all(t["allowed"] for t in topics)


# ─── POST /api/practice-sessions/start ──────────────────────────────────

def test_start_rejects_a_topic_the_grade_may_not_see(_client, monkeypatch):
    _as(monkeypatch, USER)
    _client()
    payload = main.StartPracticeSessionRequest(
        mode="test", topics=["algebra"], difficulty="easy", grade="2nd Grade")
    with pytest.raises(main.HTTPException) as exc:
        main.start_practice_session(payload, None)
    assert exc.value.status_code == 400


def test_start_accepts_grade_appropriate_topics(_client, monkeypatch):
    _as(monkeypatch, USER)
    c = _client()
    payload = main.StartPracticeSessionRequest(
        mode="test", topics=["ordering", "geometry"], difficulty="medium", grade="3rd Grade")
    row = main.start_practice_session(payload, None)
    assert row["user_id"] == USER
    assert row["mode"] == "test"
    assert row["topics"] == ["ordering", "geometry"]
    assert row["difficulty"] == "medium"
    assert c.inserted_sessions and c.inserted_sessions[0]["grade_level"] == "3rd Grade"


def test_start_rejects_an_empty_topic_list(_client, monkeypatch):
    _as(monkeypatch, USER)
    _client()
    payload = main.StartPracticeSessionRequest(
        mode="test", topics=[], difficulty="easy", grade="5th Grade")
    with pytest.raises(main.HTTPException) as exc:
        main.start_practice_session(payload, None)
    assert exc.value.status_code == 400


# ─── ownership across every route ───────────────────────────────────────

_OWNED_SESSION = {"id": SESSION, "user_id": USER, "topics": ["ordering"],
                   "difficulty": "easy", "grade_level": "5th Grade", "ended_at": None}


def test_a_stranger_cannot_request_a_question(_client, monkeypatch):
    _as(monkeypatch, OTHER_USER)
    _client(sessions=[_OWNED_SESSION])
    with pytest.raises(main.HTTPException) as exc:
        main.practice_question(SESSION, None)
    assert exc.value.status_code == 403


def test_a_stranger_cannot_answer(_client, monkeypatch):
    _as(monkeypatch, OTHER_USER)
    _client(sessions=[_OWNED_SESSION])
    payload = main.PracticeAnswerPayload(question_id="q-1", selected_index=0, correct=True)
    with pytest.raises(main.HTTPException) as exc:
        main.record_practice_answer(SESSION, payload, None)
    assert exc.value.status_code == 403


def test_a_stranger_cannot_mark_a_view(_client, monkeypatch):
    _as(monkeypatch, OTHER_USER)
    _client(sessions=[_OWNED_SESSION])
    payload = main.PracticeViewPayload(question_id="q-1")
    with pytest.raises(main.HTTPException) as exc:
        main.record_practice_view(SESSION, payload, None)
    assert exc.value.status_code == 403


def test_a_stranger_cannot_end_the_session(_client, monkeypatch):
    _as(monkeypatch, OTHER_USER)
    _client(sessions=[_OWNED_SESSION])
    with pytest.raises(main.HTTPException) as exc:
        main.end_practice_session(SESSION, None)
    assert exc.value.status_code == 403


def test_an_unknown_session_is_404_not_403(_client, monkeypatch):
    _as(monkeypatch, USER)
    _client()
    with pytest.raises(main.HTTPException) as exc:
        main.practice_question("does-not-exist", None)
    assert exc.value.status_code == 404


# ─── GET /api/practice-sessions/{id}/question ───────────────────────────

def test_question_refuses_over_the_rate_limit(_client, monkeypatch):
    _as(monkeypatch, USER)
    _client(sessions=[_OWNED_SESSION])
    monkeypatch.setattr(main, "_claim_generation_slot", lambda _uid: False)
    with pytest.raises(main.HTTPException) as exc:
        main.practice_question(SESSION, None)
    assert exc.value.status_code == 429


def test_question_surfaces_a_reached_ceiling_as_503_not_500(_client, monkeypatch):
    """Same contract as /api/generate-question: a ceiling is a decision this
    deployment made, not something that broke."""
    _as(monkeypatch, USER)
    _client(sessions=[_OWNED_SESSION])

    def _refuse(*_a, **_k):
        raise llm_client.GenerationUnavailable("daily model-call ceiling reached")

    monkeypatch.setattr(main.LLM_topic_decider, "question_generation", _refuse)
    with pytest.raises(main.HTTPException) as exc:
        main.practice_question(SESSION, None)
    assert exc.value.status_code == 503


def test_question_that_genuinely_failed_is_a_500(_client, monkeypatch):
    _as(monkeypatch, USER)
    _client(sessions=[_OWNED_SESSION])
    monkeypatch.setattr(main.LLM_topic_decider, "question_generation",
                         lambda *_a, **_k: None)
    with pytest.raises(main.HTTPException) as exc:
        main.practice_question(SESSION, None)
    assert exc.value.status_code == 500


def test_question_generates_from_the_sessions_own_settings_and_stores_it(_client, monkeypatch):
    """The endpoint calls the topic-agnostic generator directly with the
    session's own topic/difficulty/grade, then attaches a stored id the same
    way the live decider does -- confirmed by asserting the exact arguments
    reach `question_generation`, not just that a question came back.
    """
    _as(monkeypatch, USER)
    _client(sessions=[_OWNED_SESSION])
    calls = []

    def _generate(topic, difficulty, user_id, grade):
        calls.append((topic, difficulty, user_id, grade))
        return {"question_text": "2 + 2?", "question_topic": topic,
                "answer_options": ["3", "4"], "correct_answer": "4"}

    monkeypatch.setattr(main.LLM_topic_decider, "question_generation", _generate)
    monkeypatch.setattr(main.LLM_topic_decider, "_attach_stored_id",
                         lambda q, d: q.update({"id": "generated-1"}) or q)

    question = main.practice_question(SESSION, None)

    assert calls == [("ordering", "easy", USER, "5th Grade")]
    assert question["id"] == "generated-1"
    assert question["difficulty"] == "easy"
    # No EEG/bias fields -- question_generation never sets them, unlike the
    # live decider, and nothing here should invent them.
    assert "eeg_label" not in question
    assert "bias" not in question


def test_question_ends_a_finished_session_with_409(_client, monkeypatch):
    _as(monkeypatch, USER)
    ended = dict(_OWNED_SESSION, ended_at="2026-08-25T00:00:00Z")
    _client(sessions=[ended])
    with pytest.raises(main.HTTPException) as exc:
        main.practice_question(SESSION, None)
    assert exc.value.status_code == 409


# ─── POST /api/practice-sessions/{id}/answer, /view ─────────────────────

def test_answer_resolves_topic_from_the_question_row_never_the_caller(_client, monkeypatch):
    """Same principle as `_record_topic_attempt` on the live path: the client
    already has to be trusted about correctness, so letting it also name the
    topic would let a page credit the wrong subject."""
    _as(monkeypatch, USER)
    c = _client(sessions=[_OWNED_SESSION],
                questions=[{"id": "q-1", "subject": "ordering"}])
    payload = main.PracticeAnswerPayload(question_id="q-1", selected_index=1, correct=True)

    result = main.record_practice_answer(SESSION, payload, None)

    assert result == {"ok": True, "topic": "ordering"}
    assert c.inserted_answers[0]["topic"] == "ordering"
    assert c.inserted_answers[0]["correct"] is True
    assert c.rpcs == [("bump_practice_session_counters",
                       {"p_session_id": SESSION, "p_graded": True, "p_correct": True})]


def test_answer_never_touches_the_live_topic_tables(_client, monkeypatch):
    _as(monkeypatch, USER)
    c = _client(sessions=[_OWNED_SESSION],
                questions=[{"id": "q-1", "subject": "ordering"}])
    payload = main.PracticeAnswerPayload(question_id="q-1", selected_index=0, correct=False)

    main.record_practice_answer(SESSION, payload, None)

    assert "user_math_performance" not in c.tables_touched
    assert not any(name == "record_topic_attempt" for name, _ in c.rpcs)


def test_view_records_an_ungraded_attempt(_client, monkeypatch):
    _as(monkeypatch, USER)
    c = _client(sessions=[_OWNED_SESSION],
                questions=[{"id": "q-1", "subject": "ordering"}])
    payload = main.PracticeViewPayload(question_id="q-1")

    main.record_practice_view(SESSION, payload, None)

    assert c.inserted_answers[0]["correct"] is None
    assert c.inserted_answers[0]["selected_index"] is None
    assert c.rpcs == [("bump_practice_session_counters",
                       {"p_session_id": SESSION, "p_graded": False, "p_correct": False})]


def test_answer_refuses_once_the_session_has_ended(_client, monkeypatch):
    """A timeout's `postAnswer` fires without the page awaiting it, so a
    student clicking through to results can otherwise land an answer after
    `topic_summary` was already computed at close -- permanently stale
    against a counter bump nothing ever re-summarizes. Same contract as
    `practice_question`'s 409.
    """
    _as(monkeypatch, USER)
    ended = dict(_OWNED_SESSION, ended_at="2026-08-25T00:00:00Z")
    c = _client(sessions=[ended], questions=[{"id": "q-1", "subject": "ordering"}])
    payload = main.PracticeAnswerPayload(question_id="q-1", selected_index=0, correct=True)

    with pytest.raises(main.HTTPException) as exc:
        main.record_practice_answer(SESSION, payload, None)

    assert exc.value.status_code == 409
    assert c.inserted_answers == []
    assert c.rpcs == []


def test_view_refuses_once_the_session_has_ended(_client, monkeypatch):
    _as(monkeypatch, USER)
    ended = dict(_OWNED_SESSION, ended_at="2026-08-25T00:00:00Z")
    c = _client(sessions=[ended], questions=[{"id": "q-1", "subject": "ordering"}])
    payload = main.PracticeViewPayload(question_id="q-1")

    with pytest.raises(main.HTTPException) as exc:
        main.record_practice_view(SESSION, payload, None)

    assert exc.value.status_code == 409
    assert c.inserted_answers == []


# ─── POST /api/practice-sessions/{id}/end ───────────────────────────────

def test_end_summarizes_correct_and_ungraded_topics_separately(_client, monkeypatch):
    _as(monkeypatch, USER)
    c = _client(sessions=[_OWNED_SESSION], answers=[
        {"practice_session_id": SESSION, "topic": "ordering", "correct": True},
        {"practice_session_id": SESSION, "topic": "ordering", "correct": False},
        {"practice_session_id": SESSION, "topic": "geometry", "correct": None},
    ])

    result = main.end_practice_session(SESSION, None)

    assert result["topic_summary"]["ordering"] == {"attempted": 2, "correct": 50}
    # A topic that was only ever viewed has no graded answers -- `correct`
    # stays null rather than reading as a 0% score nobody actually earned.
    assert result["topic_summary"]["geometry"] == {"attempted": 1, "correct": None}
    assert c.sessions[SESSION]["ended_at"] is not None
    assert c.sessions[SESSION]["topic_summary"] == result["topic_summary"]


def test_end_is_idempotent_and_does_not_recompute(_client, monkeypatch):
    _as(monkeypatch, USER)
    already = dict(_OWNED_SESSION, ended_at="2026-08-25T00:00:00Z",
                   topic_summary={"ordering": {"attempted": 5, "correct": 80}})
    c = _client(sessions=[already])

    result = main.end_practice_session(SESSION, None)

    assert result == {"ok": True, "already_closed": True}
    assert c.updates == []


def test_end_never_touches_sessions_or_its_close_machinery(_client, monkeypatch):
    """The whole point of a separate table pair: closing a practice session
    must not run the rollup/chart-archive/alert machinery built for a
    signal-bearing `sessions` row."""
    _as(monkeypatch, USER)
    c = _client(sessions=[_OWNED_SESSION])

    main.end_practice_session(SESSION, None)

    assert "sessions" not in c.tables_touched
    assert "session_alerts" not in c.tables_touched
    assert not any(name in ("bump_session_counters", "record_topic_attempt")
                   for name, _ in c.rpcs)


def test_end_practice_session_is_not_a_close_site():
    """Pins the PR #152 gotcha directly: `conftest.close_sites()` scans every
    function in `main` for `_close_session(` or a literal `"ended_at":`, and
    demands `CLOSED_BY_SWEEP` from anything it finds that isn't `end_session`.
    `end_practice_session` deliberately writes its close stamp through a
    variable so it is never swept in.
    """
    from conftest import close_sites
    names = [name for name, _ in close_sites()]
    assert "end_practice_session" not in names, (
        "end_practice_session was swept into the sessions-table close-site "
        "scan; it needs CLOSED_BY_SWEEP or the update payload needs to stop "
        "spelling \"ended_at\" as an inline dict literal")


# ─── GET /api/practice-sessions ─────────────────────────────────────────

def test_list_practice_sessions_is_scoped_to_the_caller(_client, monkeypatch):
    _as(monkeypatch, USER)
    _client(sessions=[
        _OWNED_SESSION,
        {"id": "other", "user_id": OTHER_USER, "topics": ["ordering"],
         "difficulty": "easy", "grade_level": "5th Grade", "ended_at": None},
    ])
    rows = main.list_practice_sessions(None)
    assert [r["id"] for r in rows] == [SESSION]


def test_list_practice_sessions_is_capped(_client, monkeypatch):
    """Matches `student_sessions`' own cap on the analogous live-session
    read -- unbounded here would grow with every practice session a student
    has ever started."""
    _as(monkeypatch, USER)
    many = [dict(_OWNED_SESSION, id=f"s-{i}", started_at=f"2026-01-{i + 1:02d}T00:00:00Z")
            for i in range(25)]
    _client(sessions=many)
    rows = main.list_practice_sessions(None)
    assert len(rows) == 20


# ─── the per-session topic-rotation state is bounded ────────────────────

def test_topic_rotation_state_is_evicted_when_a_session_ends(_client, monkeypatch):
    _as(monkeypatch, USER)
    monkeypatch.setattr(main, "_practice_last_topic", collections.OrderedDict())
    _client(sessions=[dict(_OWNED_SESSION, topics=["ordering", "geometry"])])

    main._pick_practice_topic(SESSION, ["ordering", "geometry"])
    assert SESSION in main._practice_last_topic

    main.end_practice_session(SESSION, None)

    assert SESSION not in main._practice_last_topic


def test_topic_rotation_state_is_capped_regardless_of_close(monkeypatch):
    """Unlike `_prefetch_cache` (bounded by however many students are signed
    in, keyed per user), this dict is keyed per practice session, which is
    never reused -- a session a student abandons without ever calling `/end`
    would otherwise sit here forever. `end_practice_session` evicts its own
    key, but a hard cap is what protects the sessions that never reach it.
    """
    monkeypatch.setattr(main, "_PRACTICE_TOPIC_CAP", 3)
    monkeypatch.setattr(main, "_practice_last_topic", collections.OrderedDict())

    for i in range(5):
        main._pick_practice_topic(f"s-{i}", ["ordering", "geometry"])

    assert len(main._practice_last_topic) == 3
    # The earliest sessions are the ones evicted, not the most recent.
    assert "s-0" not in main._practice_last_topic
    assert "s-1" not in main._practice_last_topic
    assert "s-4" in main._practice_last_topic


# ─── learning-strategies extension ──────────────────────────────────────

def test_topics_from_practice_summary_matches_topic_breakdown_shape():
    topics = main._topics_from_practice_summary({
        "ordering": {"attempted": 4, "correct": 75},
        "geometry": {"attempted": 1, "correct": None},
    })
    by_name = {t["topic_name"]: t for t in topics}
    assert by_name["ordering"]["accuracy"] == 75
    assert by_name["ordering"]["attempted_questions"] == 4
    # A viewed-only topic reads as 0, matching `_weakest_topic`'s convention
    # for "no graded data" rather than crashing on a None comparison.
    assert by_name["geometry"]["accuracy"] == 0


def test_learning_strategies_rejects_a_practice_session_owned_by_someone_else(_client, monkeypatch):
    _as(monkeypatch, USER)
    _client(sessions=[{"id": SESSION, "user_id": OTHER_USER, "topics": ["ordering"],
                        "difficulty": "easy", "grade_level": "5th Grade",
                        "ended_at": "2026-08-25T00:00:00Z",
                        "topic_summary": {"ordering": {"attempted": 3, "correct": 66}}}])
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a, **_k: None)
    monkeypatch.setattr(main, "_rate_limit_strategies", lambda *_a, **_k: None)
    monkeypatch.setattr(main, "_strategy_basis",
                         lambda *_a, **_k: {"averages": {}, "signals_retrieved": True, "days": 7})
    monkeypatch.setattr(main, "_feature_flags",
                         lambda: {"strategy_llm_enabled": {"enabled": False}})
    payload = main.LearningStrategyRequest(practice_session_id=SESSION)
    with pytest.raises(main.HTTPException) as exc:
        main.student_learning_strategies(USER, None, payload)
    assert exc.value.status_code == 403
