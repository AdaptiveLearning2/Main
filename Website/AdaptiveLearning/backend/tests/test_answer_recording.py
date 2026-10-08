"""An answer reaches the database, attributed to the question's own topic."""

import json
import os
from datetime import datetime, timedelta

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from postgrest.exceptions import APIError, APIErrorFromJSON  # noqa: E402

import main  # noqa: E402

USER = "student-1"
QUESTION = "0b1c2d3e-4f50-4a6b-8c7d-9e0f1a2b3c4d"
SESSION = "5a6b7c8d-9e0f-4a1b-8c2d-3e4f5a6b7c8d"


def _postgrest_error(body: str) -> APIError:
    """An error as postgrest-py raises it from a non-2xx PostgREST body."""
    return APIError(dict(APIErrorFromJSON.model_validate_json(body)))


class _Client:
    def __init__(self, subject="algebra", prior=None, topics=("algebra",), raises=(),
                 rpc_error=None, session_user=USER, session_ended=None, answer=None):
        # What `record_answer` returns: the function's own outcome vocabulary.
        self.answer = answer if answer is not None else {
            "status": "ok", "topic": "algebra", "topic_error": None, "counters_error": None}
        self.session_ended = session_ended
        self.subject = subject
        self.prior = prior
        self.topics = topics
        self.raises = set(raises)
        self.rpc_error = rpc_error
        self.session_user = session_user
        self.upserts = []
        self.rpcs = []
        self.inserts = []
        # (table, column, value) per filter: canned data can't show a wrong lookup.
        self.filters = []

    def rpc(self, name, params):
        client = self

        class _R:
            def execute(self):
                client.rpcs.append((name, params))
                if client.rpc_error:
                    raise client.rpc_error if isinstance(client.rpc_error, Exception) \
                        else RuntimeError(client.rpc_error)
                data = client.answer if name == "record_answer" else None
                return type("R", (), {"data": data})()

        return _R()

    def table(self, name):
        client = self
        table = name

        class _Q:
            def select(self, *_a):
                return self

            def eq(self, col, value):
                client.filters.append((table, col, value))
                return self

            def limit(self, *_a):
                return self

            def upsert(self, row, **kw):
                self._upsert = (row, kw)
                return self

            def insert(self, row):
                self._insert = row
                return self

            def update(self, _row):
                return self

            def single(self):
                self._single = True
                return self

            def execute(self):
                if table in client.raises:
                    raise RuntimeError(f"{table} unavailable")
                if getattr(self, "_upsert", None) is not None:
                    client.upserts.append(self._upsert)
                    return type("R", (), {"data": []})()
                if getattr(self, "_insert", None) is not None:
                    client.inserts.append((table, self._insert))
                    return type("R", (), {"data": []})()
                if table == "sessions":
                    row = {"id": "s-1", "user_id": client.session_user,
                           "questions_answered": 0, "correct_answers": 0,
                           "ended_at": client.session_ended}
                    return type("R", (), {"data": row if getattr(self, "_single", False) else [row]})()
                if table == "questions":
                    return type("R", (), {"data": [{"subject": client.subject}]})()
                if table == "math_topics":
                    rows = [{"id": 7}] if client.subject in client.topics else []
                    return type("R", (), {"data": rows})()
                if table == "user_math_performance":
                    return type("R", (), {"data": [client.prior] if client.prior else []})()
                return type("R", (), {"data": []})()

        return _Q()


def _answer(monkeypatch, client, correct=True):
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": USER})
    monkeypatch.setattr(main, "supabase", client)
    return main.record_answer(
        session_id="s-1", request=None,
        payload=main.AnswerPayload(question_id=QUESTION, selected_index=2, correct=correct))


def test_an_answer_is_one_call_carrying_everything_it_writes(monkeypatch):
    """Ownership, the open-session check and the arithmetic are asserted in assert_signal_rls.sql."""
    client = _Client()

    out = _answer(monkeypatch, client, correct=False)

    assert [name for name, _ in client.rpcs] == ["record_answer"]
    params = client.rpcs[0][1]
    # No topic parameter: the function takes the topic from the question row, never the caller.
    assert set(params) == {"p_session_id", "p_user_id", "p_question_id", "p_selected_index",
                           "p_correct", "p_answered_at"}
    assert (params["p_session_id"], params["p_user_id"], params["p_question_id"],
            params["p_selected_index"], params["p_correct"]) == ("s-1", USER, QUESTION, 2, False)
    assert datetime.fromisoformat(params["p_answered_at"]).utcoffset() == timedelta(0)
    assert client.inserts == [], "the answer was written outside the function"
    assert out == {"ok": True, "topic": "algebra"}


@pytest.mark.parametrize("answer,status", [
    ({"status": "not_found"}, 404),
    ({"status": "ended"}, 409),
    # An answer this code does not know is not a success.
    ({"status": "surprise"}, 503),
    ({}, 503),
])
def test_each_refusal_is_its_own_status(monkeypatch, answer, status):
    with pytest.raises(main.HTTPException) as caught:
        _answer(monkeypatch, _Client(answer=answer))
    assert caught.value.status_code == status


def test_another_students_session_is_refused_through_the_shared_check(monkeypatch):
    """The 403 and its authz_denied row come from _session_or_403, as for every session."""
    events = []
    monkeypatch.setattr(main, "_record_security_event",
                        lambda kind, actor, subject=None, **detail: events.append((kind, actor, subject, detail)))
    with pytest.raises(main.HTTPException) as caught:
        _answer(monkeypatch, _Client(answer={"status": "forbidden", "owner": "student-2"}))
    assert caught.value.status_code == 403
    assert events == [("authz_denied", USER, "student-2",
                       {"check": "session_owner", "session_id": "s-1"})]


@pytest.mark.parametrize("error,status", [
    (type("E", (Exception,), {"code": "22P02"})("not a uuid"), 404),
    (RuntimeError("connection reset"), 503),
])
def test_a_failed_call_is_not_an_answer(monkeypatch, error, status):
    """A session id that cannot be a uuid names no session; anything else is a read that failed."""
    with pytest.raises(main.HTTPException) as caught:
        _answer(monkeypatch, _Client(rpc_error=error))
    assert caught.value.status_code == status


def test_a_missing_function_is_reported_as_a_missing_migration(monkeypatch, capsys):
    """Deployed ahead of 20261003000000, every answer fails: a 503 that says why in the log."""
    with pytest.raises(main.HTTPException) as caught:
        _answer(monkeypatch, _Client(rpc_error="{'code': 'PGRST202', 'message': 'no function'}"))
    assert caught.value.status_code == 503
    line = capsys.readouterr().out
    assert "20261003000000" in line and "none is saved" in line


def _fk_violation(constraint: str, column: str, table: str) -> APIError:
    """PostgREST's body for SQLSTATE 23503 raised inside the function: Postgres's own text."""
    return _postgrest_error(json.dumps({
        "code": "23503",
        "message": f'insert or update on table "session_answers" violates foreign key '
                   f'constraint "{constraint}"',
        "details": f'Key ({column})=({QUESTION}) is not present in table "{table}".',
        "hint": None,
    }))


@pytest.mark.parametrize("error,status", [
    # Expired or deleted since it was served: no retry can save it.
    (_fk_violation("session_answers_question_id_fkey", "question_id", "questions"), 410),
    # Another foreign key failing is not the question's absence.
    (_fk_violation("session_answers_user_id_fkey", "user_id", "profiles"), 503),
    (RuntimeError("connection reset"), 503),
])
def test_an_answer_to_a_deleted_question_is_gone_not_retryable(monkeypatch, error, status):
    with pytest.raises(main.HTTPException) as caught:
        _answer(monkeypatch, _Client(rpc_error=error))
    assert caught.value.status_code == status
    if status == 410:
        assert "no longer available" in caught.value.detail


def _ok(**errors):
    return {"status": "ok", "topic": None, "topic_error": None, "counters_error": None, **errors}


@pytest.mark.parametrize("answer,expected", [
    (_ok(topic_error="XX000: deadlock detected"),
     ["topic_error", "XX000: deadlock detected"]),
    (_ok(topic_error="42883: function public.record_topic_attempt(uuid, uuid, boolean) does not exist"),
     ["record_topic_attempt is missing", "20260825000000"]),
    # Named but present, and missing but another function: neither is this migration's absence.
    (_ok(topic_error="42501: permission denied for function record_topic_attempt"),
     ["topic_error", "42501: permission denied"]),
    (_ok(topic_error="42883: function public.score_scale_of(text) does not exist"),
     ["topic_error", "score_scale_of"]),
    (_ok(counters_error="57014: canceling statement due to statement timeout"),
     ["counters_error", "57014: canceling statement due to statement timeout"]),
    (_ok(counters_error="42883: function public.bump_session_counters(uuid, boolean) does not exist"),
     ["bump_session_counters is missing", "20260826000000"]),
])
def test_a_step_the_answer_survived_is_logged_by_name(monkeypatch, capsys, answer, expected):
    """The outcome is a 200 either way, so only the log can tell; the answer itself is saved."""
    out = _answer(monkeypatch, _Client(answer=answer))
    assert out == {"ok": True, "topic": None}
    log = capsys.readouterr().out
    for text in expected:
        assert text in log, f"{text!r} not logged: {log!r}"


def test_an_answer_no_topic_matched_is_logged(monkeypatch, capsys):
    """`record_topic_attempt` returns null without raising when the subject has no math_topics row."""
    out = _answer(monkeypatch, _Client(answer=_ok()))
    assert out == {"ok": True, "topic": None}
    log = capsys.readouterr().out
    assert "no math_topics row" in log and QUESTION in log, log


def test_a_clean_answer_logs_nothing(monkeypatch, capsys):
    """Mirror of the test above, so it cannot be satisfied by logging every answer."""
    out = _answer(monkeypatch, _Client())
    assert out == {"ok": True, "topic": "algebra"}
    assert "[answer]" not in capsys.readouterr().out


def _post_answer(monkeypatch, client, question_id):
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": USER})
    monkeypatch.setattr(main, "supabase", client)
    return TestClient(main.app).post(
        f"/api/sessions/{SESSION}/answer",
        json={"question_id": question_id, "selected_index": 1, "correct": True})


def test_a_question_id_that_cannot_be_a_uuid_is_refused_before_the_call(monkeypatch):
    """Sent on, 22P02 came back as "Session not found" for a session that exists."""
    client = _Client()
    res = _post_answer(monkeypatch, client, "q-1")
    assert res.status_code == 422
    assert [e["loc"] for e in res.json()["detail"]] == [["body", "question_id"]]
    assert client.rpcs == [], "a malformed id reached the database"


def test_a_uuid_question_id_reaches_the_call_as_text(monkeypatch):
    """A `UUID` object is not JSON; the RPC body must carry the string."""
    client = _Client()
    res = _post_answer(monkeypatch, client, QUESTION)
    assert res.status_code == 200
    assert res.json() == {"ok": True, "topic": "algebra"}
    sent = client.rpcs[0][1]["p_question_id"]
    assert type(sent) is str and sent == QUESTION


class _SessionClient:
    """A `sessions` row with whatever state the test needs, and a credit counter."""

    def __init__(self, row):
        self.row = row
        self.credited = []
        self.updates = []

    def table(self, name):
        client, table = self, name

        class _Q:
            def select(self, *_a):
                return self

            def eq(self, *_a):
                return self

            def is_(self, *_a):
                return self

            def order(self, *_a, **_k):
                return self

            def limit(self, *_a):
                return self

            def single(self):
                return self

            def update(self, row, **_k):
                if table == "sessions":
                    client.updates.append(row)
                return self

            def insert(self, _row):
                return self

            def upsert(self, _row, **_k):
                return self

            def execute(self):
                if table == "sessions":
                    return type("R", (), {"data": client.row})()
                return type("R", (), {"data": []})()

        return _Q()


def test_closing_a_session_twice_credits_it_once(monkeypatch):
    """`/end` credits cumulative counts, so a second close would double them."""
    closed = {"id": "s-1", "user_id": USER, "questions_answered": 6,
              "correct_answers": 4, "started_at": "2026-08-15T10:00:00Z",
              "ended_at": "2026-08-15T11:00:00Z"}
    client = _SessionClient(closed)
    credited = []
    monkeypatch.setattr(main, "supabase", client)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": USER})
    monkeypatch.setattr(main.eeg_poller, "stop", lambda *_a, **_k: None)
    monkeypatch.setattr(main, "_credit_session_to_user_stats",
                        lambda uid, q, c, *_started: credited.append((uid, q, c)))

    out = main.end_session(session_id="s-1", request=None)

    assert out.get("already_closed") is True
    assert credited == [], "an already-closed session was credited a second time"
    assert client.updates == [], "an already-closed session had its ended_at rewritten"


def test_closing_an_open_session_still_credits_it(monkeypatch):
    """The mirror, so the guard above cannot be satisfied by never crediting."""
    open_row = {"id": "s-1", "user_id": USER, "questions_answered": 6,
                "correct_answers": 4, "started_at": "2026-08-15T10:00:00Z",
                "ended_at": None}
    client = _SessionClient(open_row)
    credited = []
    monkeypatch.setattr(main, "supabase", client)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": USER})
    monkeypatch.setattr(main.eeg_poller, "stop", lambda *_a, **_k: None)
    monkeypatch.setattr(main, "_credit_session_to_user_stats",
                        lambda uid, q, c, *_started: credited.append((uid, q, c)))
    monkeypatch.setattr(main, "_discard_if_nothing_recorded", lambda *_a, **_k: False)
    monkeypatch.setattr(main, "_rollup_session_days", lambda *_a: None)
    monkeypatch.setattr(main.chart_archive, "schedule", lambda *_a, **_k: None)

    main.end_session(session_id="s-1", request=None)

    assert credited == [(USER, 6, 4)]


class _ClaimClient:
    """A session whose conditional stamp can be made to win or lose.

    `answers`: rows the close recounts; `stored`: the row's own counters;
    `claim_wins`: whether the `is_("ended_at", "null")` update matches.
    """

    def __init__(self, stored, answers=(), claim_wins=True):
        self.stored = stored
        self.answers = list(answers)
        self.claim_wins = claim_wins
        self.updates = []
        self.returning = []
        self.deleted = []

    def rpc(self, name, params):
        client = self

        class _R:
            def execute(self):
                assert name == "session_answer_counts", name
                return type("R", (), {"data": [{
                    "total": len(client.answers),
                    "correct": sum(1 for a in client.answers if a.get("correct")),
                }]})()

        return _R()

    def table(self, name):
        client, table = self, name

        class _Q:
            _update = None
            _conditional = False

            def select(self, *_a):
                return self

            def eq(self, *_a):
                return self

            def in_(self, *_a):
                return self

            def is_(self, *_a):
                self._conditional = True
                return self

            def order(self, *_a, **_k):
                return self

            def limit(self, *_a):
                return self

            def single(self):
                return self

            def update(self, row, returning=None, **_k):
                # Recorded as passed, None when omitted; `minimal` answers no rows, as PostgREST does.
                self._update, self._returning = row, returning
                return self

            def insert(self, _row):
                return self

            def delete(self):
                self._delete = True
                return self

            def execute(self):
                if getattr(self, "_delete", False):
                    client.deleted.append(table)
                    return type("R", (), {"data": []})()
                if self._update is not None:
                    client.updates.append((table, self._update, self._conditional))
                    client.returning.append((table, self._update, self._returning))
                    won = client.claim_wins or not self._conditional
                    minimal = self._returning == main.ReturnMethod.minimal
                    return type("R", (), {"data": [self._update] if won and not minimal else []})()
                if table == "session_answers":
                    return type("R", (), {"data": client.answers})()
                if table == "sessions":
                    return type("R", (), {"data": [dict(client.stored)]})()
                return type("R", (), {"data": []})()

        return _Q()


def _close_with(monkeypatch, client, credited):
    monkeypatch.setattr(main, "supabase", client)
    monkeypatch.setattr(main, "_credit_session_to_user_stats",
                        lambda uid, q, c, *_started: credited.append((uid, q, c)))
    monkeypatch.setattr(main, "_rollup_session_days", lambda *_a: None)
    monkeypatch.setattr(main.chart_archive, "schedule", lambda *_a, **_k: None)


def test_a_close_that_loses_the_stamp_credits_nothing(monkeypatch):
    """The guard is the conditional update; `/end`'s read of `ended_at` is a separate statement."""
    credited = []
    client = _ClaimClient(
        {"id": "s-1", "user_id": USER, "questions_answered": 6, "correct_answers": 4,
         "started_at": "2026-08-15T10:00:00Z"},
        answers=[{"correct": True}] * 6,
        claim_wins=False,
    )
    _close_with(monkeypatch, client, credited)

    out = main._close_session(USER, {"id": "s-1", "questions_answered": 6,
                                     "correct_answers": 4,
                                     "started_at": "2026-08-15T10:00:00Z"},
                              "2026-08-15T11:30:00Z")

    assert out.get("already_closed") is True
    assert credited == [], "a close that lost the stamp credited the totals anyway"


def test_the_stamp_asks_for_the_row_it_stamped(monkeypatch):
    """By name, not by the client's default: under `minimal` every close would read as lost."""
    client = _ClaimClient({"id": "s-1", "user_id": USER, "questions_answered": 1,
                           "correct_answers": 1, "started_at": "2026-08-15T10:00:00Z"},
                          answers=[{"correct": True}])
    _close_with(monkeypatch, client, [])

    main._close_session(USER, {"id": "s-1", "questions_answered": 1, "correct_answers": 1,
                               "started_at": "2026-08-15T10:00:00Z"}, "2026-08-15T11:30:00Z")

    stamps = [returning for tbl, row, returning in client.returning if tbl == "sessions" and "ended_at" in row]
    assert stamps == [main.ReturnMethod.representation]


def test_the_stamp_is_conditional(monkeypatch):
    """An unconditional update would pass the tests above by never losing."""
    credited = []
    client = _ClaimClient({"id": "s-1", "user_id": USER, "questions_answered": 1,
                           "correct_answers": 1, "started_at": "2026-08-15T10:00:00Z"},
                          answers=[{"correct": True}])
    _close_with(monkeypatch, client, credited)

    main._close_session(USER, {"id": "s-1", "questions_answered": 1,
                               "correct_answers": 1,
                               "started_at": "2026-08-15T10:00:00Z"},
                        "2026-08-15T11:30:00Z")

    stamps = [(row, conditional) for tbl, row, conditional in client.updates
              if tbl == "sessions" and "ended_at" in row]
    assert stamps, "the close never stamped ended_at"
    assert all(conditional for _, conditional in stamps), (
        "the stamp is unconditional, so two closes both win it")


def test_a_close_credits_the_answers_that_exist_not_the_counter(monkeypatch):
    """`questions_answered` is a cache written separately from the answer row."""
    credited = []
    stale = {"id": "s-1", "user_id": USER, "questions_answered": 0,
             "correct_answers": 0, "started_at": "2026-08-15T10:00:00Z"}
    client = _ClaimClient(stale, answers=[{"correct": True}, {"correct": False},
                                          {"correct": True}])
    _close_with(monkeypatch, client, credited)

    main._close_session(USER, dict(stale), "2026-08-15T11:30:00Z")

    assert credited == [(USER, 3, 2)]
    assert "sessions" not in client.deleted, (
        "a session with three answers in it was discarded as empty")


def test_a_counter_ahead_of_the_rows_is_credited_rather_than_reduced(monkeypatch):
    """The recount only revises upward: a short read looks exactly like a correct one."""
    credited = []
    stale = {"id": "s-1", "user_id": USER, "questions_answered": 9,
             "correct_answers": 7, "started_at": "2026-08-15T10:00:00Z"}
    client = _ClaimClient(stale, answers=[{"correct": True}])
    _close_with(monkeypatch, client, credited)

    main._close_session(USER, dict(stale), "2026-08-15T11:30:00Z")

    assert credited == [(USER, 9, 7)]


def test_every_close_site_stops_the_poller_first():
    """A tick between close and stop would record after the emptiness check and rollup."""
    from conftest import close_sites

    checked = 0
    for name, source in close_sites():
        if "eeg_poller.stop" not in source or "_close_session(" not in source:
            continue
        checked += 1
        assert source.index("eeg_poller.stop") < source.index("_close_session("), (
            f"{name} closes the session before stopping its poller: a tick in "
            "between records a row for a session that has already been "
            "checked for emptiness and rolled up")
    assert checked >= 3, f"only {checked} close sites stop a poller; expected three"


class _RosterClient:
    """`user_stats` and open `sessions` for many students, counting queries."""

    def __init__(self, stats_rows=(), open_rows=(), raises=()):
        self.stats_rows = list(stats_rows)
        self.open_rows = list(open_rows)
        self.raises = set(raises)
        self.queries = []

    def table(self, name):
        client, table = self, name

        class _Q:
            def select(self, *_a):
                return self

            def in_(self, *_a):
                return self

            def eq(self, *_a):
                return self

            def is_(self, *_a):
                return self

            def execute(self):
                client.queries.append(table)
                if table in client.raises:
                    raise RuntimeError(f"{table} unavailable")
                rows = client.stats_rows if table == "user_stats" else client.open_rows
                return type("R", (), {"data": rows})()

        return _Q()


def test_a_roster_costs_two_queries_not_two_per_student(monkeypatch):
    """A per-student version would make a class of thirty sixty round trips."""
    ids = [f"s-{i}" for i in range(30)]
    c = _RosterClient(stats_rows=[{"user_id": "s-0", "total_questions": 4,
                                   "total_correct": 3}])
    monkeypatch.setattr(main, "supabase", c)

    out = main._stats_including_open_session_many(ids)

    assert len(c.queries) == 2, f"one read per table, got {c.queries}"
    assert len(out) == 30
    assert out["s-0"]["total_questions"] == 4
    # A student with no row is zero, not missing -- they have answered nothing.
    assert out["s-7"]["total_questions"] == 0 and out["s-7"]["retrieved"] is True


def test_the_roster_adds_each_students_own_open_session(monkeypatch):
    """`user_stats` is a session behind; the live delta lands on its own student."""
    c = _RosterClient(
        stats_rows=[{"user_id": "a", "total_questions": 10, "total_correct": 6}],
        open_rows=[{"user_id": "a", "questions_answered": 3, "correct_answers": 2},
                   {"user_id": "b", "questions_answered": 5, "correct_answers": 5}])
    monkeypatch.setattr(main, "supabase", c)

    out = main._stats_including_open_session_many(["a", "b"])

    assert out["a"]["total_questions"] == 13 and out["a"]["total_correct"] == 8
    assert out["b"]["total_questions"] == 5 and out["b"]["total_correct"] == 5


def test_a_failed_roster_read_marks_every_student_unretrieved(monkeypatch):
    """A roster of zeros would assert an absence from data that never loaded."""
    c = _RosterClient(raises=["user_stats"])
    monkeypatch.setattr(main, "supabase", c)

    out = main._stats_including_open_session_many(["a", "b"])

    assert all(v["retrieved"] is False for v in out.values())


def test_a_failed_open_session_read_still_reports_the_stored_totals(monkeypatch):
    """The delta only adds, so the stored totals stay true without it."""
    c = _RosterClient(stats_rows=[{"user_id": "a", "total_questions": 10,
                                   "total_correct": 6}],
                      raises=["sessions"])
    monkeypatch.setattr(main, "supabase", c)

    out = main._stats_including_open_session_many(["a"])

    assert out["a"]["retrieved"] is True
    assert out["a"]["total_questions"] == 10


def test_only_one_place_reads_user_stats():
    """`user_stats` only gains a row at close, so a direct read misses a mid-lesson student."""
    import inspect
    import re

    source = inspect.getsource(main)
    # `[\s\\]*`, not `\s*`: `_leaderboard_rows` splits its call with a backslash continuation.
    readers = [m.start() for m in
               re.finditer(r'table\("user_stats"\)[\s\\]*\.?[\s\\]*select', source)]
    assert len(readers) >= 3, (
        "the call shape changed, or the line-continuation blind spot is back; "
        f"found {len(readers)} readers and there are at least three"
    )

    ALLOWED = {"_stats_including_open_session", "_stats_including_open_session_many",
               "_credit_session_to_user_stats"}
    ALLOWLIST = {"_leaderboard_rows": "ranks a caller's classmates; staleness is uniform across them"}

    for pos in readers:
        head = source[:pos]
        enclosing = re.findall(r"^def (\w+)", head, re.MULTILINE)[-1]
        assert enclosing in ALLOWED or enclosing in ALLOWLIST, (
            f"{enclosing} reads user_stats directly -- it will report a student "
            "who is mid-session as having answered nothing. Use "
            "_stats_including_open_session(), or add it to ALLOWLIST with a reason."
        )


def test_every_session_close_credits_the_lifetime_totals():
    """Every closer reaches `_close_session`, and the helper still credits."""
    import inspect

    from conftest import close_sites

    closers = close_sites()

    assert len(closers) >= 3, f"expected at least three close sites, found {closers}"
    for name, src in closers:
        assert "_close_session(" in src or "_credit_session_to_user_stats(" in src, (
            f"{name} closes a session without crediting the lifetime totals -- "
            "its answers vanish from Questions/Correct/Accuracy while "
            "session_answers keeps them"
        )

    assert "_credit_session_to_user_stats(" in inspect.getsource(main._close_session), (
        "the shared close no longer credits, so no close site does")


def _safe_source(fn):
    """A function's source, or "" if it has none (C-level, or defined in exec)."""
    import inspect
    try:
        return inspect.getsource(fn)
    except (OSError, TypeError):
        return ""


def test_the_close_reads_every_column_it_credits():
    """An unselected column arrives absent, and `or 0` turns it into a measured-looking zero."""
    import inspect
    import re

    close_src = (inspect.getsource(main._close_session)
                 + inspect.getsource(main._answer_counts))
    needed = set(re.findall(r'session\.get\("(\w+)"\)', close_src))
    needed.discard("id")
    assert "correct_answers" in needed and "questions_answered" in needed, (
        "the close stopped reading the counts; this test is looking at nothing")

    # Only closers are scanned; plain reads of `id, started_at` credit nothing.
    closers = [fn for fn in vars(main).values()
               if inspect.isfunction(fn)
               and getattr(fn, "__module__", None) == "main"
               and "_close_session(" in _safe_source(fn)]
    assert closers, "no function calls _close_session; this test is looking at nothing"

    scanned = 0
    for fn in closers:
        for cols in re.findall(r'table\("sessions"\)\s*\\?\s*\.select\("([^"*]+)"\)',
                               _safe_source(fn)):
            listed = {c.strip() for c in cols.split(",")}
            if "id" not in listed:
                continue        # not the row that gets closed
            scanned += 1
            missing = needed - listed
            assert not missing, (
                f"{fn.__name__} selects {sorted(listed)} but the close reads "
                f"{sorted(missing)} -- absent columns arrive as None and `or 0` "
                "turns that into a zero that looks measured"
            )

    # The stale sweep's select spans a line continuation; the regex must keep matching it.
    assert scanned, (
        "no explicit `sessions` select was scanned inside a function that closes "
        "a session -- either the selects moved, or the pattern above stopped "
        "matching how they are written")


def test_an_answer_to_a_closed_session_tells_nobody(monkeypatch):
    """The close already credited the totals; the function wrote nothing, and the sidecar hears nothing."""
    notified = []
    monkeypatch.setattr(main.eeg_poller, "notify_answer", lambda *a, **_k: notified.append(a))
    with pytest.raises(main.HTTPException) as caught:
        _answer(monkeypatch, _Client(answer={"status": "ended"}))
    assert caught.value.status_code == 409
    assert notified == []


def test_the_answer_endpoint_tells_the_sidecar_after_the_writes(monkeypatch):
    """Best effort and last: a sidecar that raises must not cost the answer its 200."""
    order = []
    client = _Client()
    client_rpc = client.rpc
    monkeypatch.setattr(client, "rpc", lambda name, params: order.append(name) or client_rpc(name, params))
    monkeypatch.setattr(main.eeg_poller, "notify_answer",
                        lambda sid, correct, difficulty=None: order.append(("notify", sid, correct)))
    out = _answer(monkeypatch, client, correct=False)
    assert out == {"ok": True, "topic": "algebra"}
    assert order == ["record_answer", ("notify", "s-1", False)]

    def boom(*_a, **_k):
        raise RuntimeError("sidecar down")
    monkeypatch.setattr(main.eeg_poller, "notify_answer", boom)
    assert _answer(monkeypatch, _Client())["ok"] is True
