"""An answer reaches the database, attributed to the question's own topic."""

import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402

USER = "student-1"
QUESTION = "q-1"


class _Client:
    def __init__(self, subject="algebra", prior=None, topics=("algebra",), raises=(),
                 rpc_error=None, session_user=USER):
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
                    raise RuntimeError(client.rpc_error)
                return type("R", (), {"data": None})()

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
                           "questions_answered": 0, "correct_answers": 0}
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


@pytest.fixture
def _client(monkeypatch):
    def _install(**kw):
        c = _Client(**kw)
        monkeypatch.setattr(main, "supabase", c)
        return c
    return _install


def test_an_attempt_is_one_statement_in_the_database(_client):
    """One `record_topic_attempt` RPC; the arithmetic is checked in `assert_signal_rls.sql`."""
    c = _client()

    main._record_topic_attempt(USER, QUESTION, correct=True)

    assert len(c.rpcs) == 1, f"expected one call, got {c.rpcs}"
    name, params = c.rpcs[0]
    assert name == "record_topic_attempt"
    assert params == {"p_user_id": USER, "p_question_id": QUESTION,
                      "p_correct": True}
    assert c.upserts == [], "the read-modify-write is back"


def test_a_wrong_answer_is_passed_through_as_an_attempt_that_was_not_correct(_client):
    c = _client()

    main._record_topic_attempt(USER, QUESTION, correct=False)

    _, params = c.rpcs[0]
    assert params["p_correct"] is False


def test_the_topic_comes_from_the_question_not_the_caller(_client):
    """A caller-named topic would let a page credit one subject for work in another."""
    c = _client(subject="geometry", topics=("geometry",))

    main._record_topic_attempt(USER, QUESTION, correct=True)

    # No parameter to smuggle a topic into.
    _, params = c.rpcs[0]
    assert set(params) == {"p_user_id", "p_question_id", "p_correct"}
    assert params["p_question_id"] == QUESTION
    assert not any("topic" in k for k in params if k != "p_question_id")


def test_a_failed_attempt_never_raises(_client):
    """Runs after the answer is written; a failure here must not become a 500."""
    _client(rpc_error="boom")

    main._record_topic_attempt(USER, QUESTION, correct=True)  # must not raise


def test_a_missing_function_is_reported_as_a_missing_migration(_client, capsys):
    """PGRST202 is swallowed like any failure but never self-heals, so it is logged by name."""
    _client(rpc_error="{'code': 'PGRST202', 'message': 'no function'}")

    main._record_topic_attempt(USER, QUESTION, correct=True)

    out = capsys.readouterr().out
    assert "20260825000000" in out, f"the log does not name the migration: {out}"


def test_the_answer_endpoint_updates_the_topic_record(monkeypatch):
    """Wiring, not arithmetic."""
    seen = []
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": USER})
    monkeypatch.setattr(main, "supabase", _Client())
    monkeypatch.setattr(main, "_record_topic_attempt",
                        lambda uid, qid, correct: seen.append((uid, qid, correct)))

    main.record_answer(
        session_id="s-1",
        payload=main.AnswerPayload(question_id=QUESTION, selected_index=2, correct=True),
        request=None,
    )

    assert seen == [(USER, QUESTION, True)]


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

            def update(self, row):
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

            def update(self, row):
                self._update = row
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
                    won = client.claim_wins or not self._conditional
                    return type("R", (), {"data": [self._update] if won else []})()
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


def test_postgrest_update_returns_the_updated_row():
    """`_claim_session_close` relies on postgrest-py's `returning=representation` default."""
    import inspect

    from postgrest._sync import request_builder

    sig = inspect.signature(request_builder.SyncRequestBuilder.update)
    default = sig.parameters["returning"].default

    assert getattr(default, "value", default) == "representation", (
        "postgrest's update() no longer returns the updated row by default, so "
        "_claim_session_close reads every successful stamp as a lost race -- "
        "every session close silently skips its credit, rollup and archive")


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
    # `[\s\\]*`, not `\s*`: `leaderboard` splits its call with a backslash continuation.
    readers = [m.start() for m in
               re.finditer(r'table\("user_stats"\)[\s\\]*\.?[\s\\]*select', source)]
    assert len(readers) >= 3, (
        "the call shape changed, or the line-continuation blind spot is back; "
        f"found {len(readers)} readers and there are at least three"
    )

    ALLOWED = {"_stats_including_open_session", "_stats_including_open_session_many",
               "_credit_session_to_user_stats"}
    ALLOWLIST = {"leaderboard": "ranks all users; one query, staleness is uniform"}

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


def test_the_answer_endpoint_tells_the_sidecar_after_the_writes(monkeypatch):
    """Best effort and last: a sidecar that raises must not cost the answer its 200."""
    order = []
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": USER})
    monkeypatch.setattr(main, "supabase", _Client())
    monkeypatch.setattr(main, "_record_topic_attempt",
                        lambda uid, qid, correct: order.append("topic") or "algebra")
    monkeypatch.setattr(main.eeg_poller, "notify_answer",
                        lambda sid, correct, difficulty=None: order.append(("notify", sid, correct)))
    out = main.record_answer(
        session_id="s-1",
        payload=main.AnswerPayload(question_id=QUESTION, selected_index=2, correct=False),
        request=None,
    )
    assert out == {"ok": True, "topic": "algebra"}
    assert order == ["topic", ("notify", "s-1", False)]

    def boom(*_a, **_k):
        raise RuntimeError("sidecar down")
    monkeypatch.setattr(main.eeg_poller, "notify_answer", boom)
    out = main.record_answer(
        session_id="s-1",
        payload=main.AnswerPayload(question_id=QUESTION, selected_index=2, correct=True),
        request=None,
    )
    assert out["ok"] is True
