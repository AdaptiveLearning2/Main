"""Tests that an answer on the adaptive page reaches the database.

`Adaptive.jsx` used to have no `/api/sessions/{id}/answer` call at all, so every
question answered through the adaptive path was only counted in that browser's
localStorage. `session_answers`, `sessions.questions_answered`, `user_stats`,
and every report built on them read zero for that student.

The half worth pinning most is per-topic attribution, since it decides what the
adaptive engine serves next and is the half a client could get wrong.
"""

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
        # (table, column, value) for every filter applied. The fake answers from
        # canned data, so without recording this a test could assert on the
        # right result while the code under test looked up the wrong thing.
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
    """The topic-attempt arithmetic now runs as one call to
    `record_topic_attempt` in the database.

    It used to be four sequential round trips, and the last two were an
    unlocked read-modify-write: two answers landing together could both read
    the same counts, and the second write would overwrite the first, silently
    losing attempts from the table the adaptive engine reads from.

    Whether the counters actually increment is Postgres's job now, checked
    against a real stack in `scripts/assert_signal_rls.sql`. What's checkable
    here is that this makes one call and passes what the function needs.
    """
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
    """The client already has to be trusted about correctness. Letting it name
    the topic too would let a page credit one subject for work done in another,
    and this table is what the adaptive engine reads to pick what to serve
    next -- a wrong attribution steers a student's whole session."""
    c = _client(subject="geometry", topics=("geometry",))

    main._record_topic_attempt(USER, QUESTION, correct=True)

    # The join is in SQL now, so the only identifiers crossing the boundary are
    # the student, the question, and whether it was right. There's no parameter
    # to smuggle a topic into.
    _, params = c.rpcs[0]
    assert set(params) == {"p_user_id", "p_question_id", "p_correct"}
    assert params["p_question_id"] == QUESTION
    assert not any("topic" in k for k in params if k != "p_question_id")


def test_a_failed_attempt_never_raises(_client):
    """This runs after the answer is written, so `session_answers` is already
    safe by this point. An attribution failure must not turn a recorded answer
    into a 500 the page reports as "that answer could not be saved"."""
    _client(rpc_error="boom")

    main._record_topic_attempt(USER, QUESTION, correct=True)  # must not raise


def test_a_missing_function_is_reported_as_a_missing_migration(_client, capsys):
    """PGRST202 means the migration hasn't been applied, and it's silent by
    design: this helper swallows its exceptions because a failed lookup must not
    cost a student the answer already recorded. So code deployed ahead of the
    migration stops attributing anything, with the numbers just not moving as
    the only symptom. Every other failure here is transient and retries on the
    next answer; this one repeats until someone applies the migration, so it
    gets its own line in the log.
    """
    _client(rpc_error="{'code': 'PGRST202', 'message': 'no function'}")

    main._record_topic_attempt(USER, QUESTION, correct=True)

    out = capsys.readouterr().out
    assert "20260825000000" in out, f"the log does not name the migration: {out}"


def test_the_answer_endpoint_updates_the_topic_record(monkeypatch):
    """Wiring, not arithmetic: the helper only matters if `record_answer`
    actually calls it."""
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
    """`/end` credits the session's cumulative counts, not a delta.

    A session already closed by the stale sweep or by a teacher's Live view
    would otherwise get credited a second time when the student's page finally
    calls `/end`, doubling every answer in the lifetime totals. The student's
    page has no way to know a teacher's view already closed the session, so
    this is an ordinary race, not misuse.
    """
    closed = {"id": "s-1", "user_id": USER, "questions_answered": 6,
              "correct_answers": 4, "started_at": "2026-08-15T10:00:00Z",
              "ended_at": "2026-08-15T11:00:00Z"}
    client = _SessionClient(closed)
    credited = []
    monkeypatch.setattr(main, "supabase", client)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": USER})
    monkeypatch.setattr(main.eeg_poller, "stop", lambda *_a, **_k: None)
    monkeypatch.setattr(main, "_credit_session_to_user_stats",
                        lambda uid, q, c: credited.append((uid, q, c)))

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
                        lambda uid, q, c: credited.append((uid, q, c)))
    monkeypatch.setattr(main, "_discard_if_nothing_recorded", lambda *_a, **_k: False)
    monkeypatch.setattr(main, "_rollup_session_days", lambda *_a: None)
    monkeypatch.setattr(main.chart_archive, "schedule", lambda *_a, **_k: None)

    main.end_session(session_id="s-1", request=None)

    assert credited == [(USER, 6, 4)]


class _ClaimClient:
    """A session whose conditional stamp can be made to win or lose.

    `answers` are the `session_answers` rows the close recounts from, `stored`
    is what the row itself claims, and `claim_wins` decides whether the
    `is_("ended_at", "null")` update matches anything.
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
                # `session_answer_counts` returns two integers from SQL now,
                # instead of the answer rows the close used to sum itself.
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
                        lambda uid, q, c: credited.append((uid, q, c)))
    monkeypatch.setattr(main, "_rollup_session_days", lambda *_a: None)
    monkeypatch.setattr(main.chart_archive, "schedule", lambda *_a, **_k: None)


def test_a_close_that_loses_the_stamp_credits_nothing(monkeypatch):
    """The guard is the conditional update, not `/end`'s read of `ended_at`.
    That read and the write are two separate statements, so a `/end` arriving
    while the stale sweep or `class_live` is closing the same session can pass
    the check and run the whole sequence anyway -- crediting the session's
    cumulative counts twice.
    """
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
    """Pins the assumption `_claim_session_close` rests on against the library.

    It reads an empty `.data` from the conditional stamp as "zero rows matched",
    which is only true because postgrest-py defaults `update()` to
    `returning=representation`. If a dependency bump flips that default, every
    close would read as lost, silently losing its credit, rollup, and archive
    with no error -- just numbers that stop moving.

    The alternative was a confirming SELECT on every lost race, paying a round
    trip forever to guard against a change this catches at install time.
    """
    import inspect

    from postgrest._sync import request_builder

    sig = inspect.signature(request_builder.SyncRequestBuilder.update)
    default = sig.parameters["returning"].default

    assert getattr(default, "value", default) == "representation", (
        "postgrest's update() no longer returns the updated row by default, so "
        "_claim_session_close reads every successful stamp as a lost race -- "
        "every session close silently skips its credit, rollup and archive")


def test_the_stamp_is_conditional(monkeypatch):
    """Checks the mechanism directly: an unconditional update would satisfy
    both tests above by never losing, but would race exactly as before.
    """
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
    """`questions_answered` is a cache, and the close used to be the last thing
    trusting it.

    `record_answer` writes the answer row and bumps the counter in two separate
    unguarded statements, so the counter can sit at zero while real answers
    exist. `_discard_if_nothing_recorded` already re-checks `session_answers`
    before deleting a session that looks empty, so a correctly-saved session
    could still get credited zero questions and zero correct answers by reading
    that same stale counter. The student's work stayed in `session_answers` but
    never reached the lifetime totals, permanently, since no later close
    revisits a stamped session.
    """
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
    """The recount may only ever find more, never fewer. Rows are the record,
    but crediting fewer answers than a previous reading already showed is the
    direction that loses a student's work -- and a short `session_answers` read
    looks exactly like a correct one.
    """
    credited = []
    stale = {"id": "s-1", "user_id": USER, "questions_answered": 9,
             "correct_answers": 7, "started_at": "2026-08-15T10:00:00Z"}
    client = _ClaimClient(stale, answers=[{"correct": True}])
    _close_with(monkeypatch, client, credited)

    main._close_session(USER, dict(stale), "2026-08-15T11:30:00Z")

    assert credited == [(USER, 9, 7)]


def test_every_close_site_stops_the_poller_first():
    """This ordering was wrong at one close site: `class_live` used to stamp
    `ended_at` and run the whole close before stopping the poller. A tick
    landing in that gap could insert a `cognitive_signals` row after
    `_discard_if_nothing_recorded` had already checked, so an abandoned pairing
    got deleted with a row still pointing at it, and the rollup computed from a
    day that gained a sample a moment later.

    Now that the stamp lives inside `_close_session`, "stop the poller before
    calling it" is the whole rule, and this test checks that directly.
    """
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
    """`class_students` and `my_children` call this inside a loop that already
    makes several reads per student, so a per-student version would turn a
    class of thirty into sixty extra round trips."""
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
    """The whole point of the helper: `user_stats` is a session behind, and the
    live delta must land on the student it belongs to, not the whole roster."""
    c = _RosterClient(
        stats_rows=[{"user_id": "a", "total_questions": 10, "total_correct": 6}],
        open_rows=[{"user_id": "a", "questions_answered": 3, "correct_answers": 2},
                   {"user_id": "b", "questions_answered": 5, "correct_answers": 5}])
    monkeypatch.setattr(main, "supabase", c)

    out = main._stats_including_open_session_many(["a", "b"])

    assert out["a"]["total_questions"] == 13 and out["a"]["total_correct"] == 8
    assert out["b"]["total_questions"] == 5 and out["b"]["total_correct"] == 5


def test_a_failed_roster_read_marks_every_student_unretrieved(monkeypatch):
    """Same three-state rule as the single-student version. The batch cannot
    know which rows it would have got, so reporting a roster of zeros would be
    an absence asserted from data that never loaded -- for a whole class."""
    c = _RosterClient(raises=["user_stats"])
    monkeypatch.setattr(main, "supabase", c)

    out = main._stats_including_open_session_many(["a", "b"])

    assert all(v["retrieved"] is False for v in out.values())


def test_a_failed_open_session_read_still_reports_the_stored_totals(monkeypatch):
    """This call only ever *adds* to the stored totals, so losing the delta
    leaves them true as far as they go -- unlike losing the totals themselves."""
    c = _RosterClient(stats_rows=[{"user_id": "a", "total_questions": 10,
                                   "total_correct": 6}],
                      raises=["sessions"])
    monkeypatch.setattr(main, "supabase", c)

    out = main._stats_including_open_session_many(["a"])

    assert out["a"]["retrieved"] is True
    assert out["a"]["total_questions"] == 10


def test_only_one_place_reads_user_stats():
    """`user_stats` only gains a row when a session closes, so reading it
    directly reports a student mid-lesson as having answered nothing.

    Four surfaces used to do this: `/api/stats/me`, `/api/stats/student`, the
    class roster, and the parent's children list -- causing mismatches between
    surfaces reading different sources for the same student.

    Derived from the source rather than a hand-kept list, so a new reader can't
    be added quietly. `_credit_session_to_user_stats` is the writer and is
    allowed; `_stats_including_open_session` is the reader.
    """
    import inspect
    import re

    source = inspect.getsource(main)
    # `[\s\\]*`, not `\s*`: a backslash line continuation isn't whitespace, and
    # `leaderboard` splits its call across two lines that way. A plain `\s*`
    # would miss it silently.
    readers = [m.start() for m in
               re.finditer(r'table\("user_stats"\)[\s\\]*\.?[\s\\]*select', source)]
    assert len(readers) >= 3, (
        "the call shape changed, or the line-continuation blind spot is back; "
        f"found {len(readers)} readers and there are at least three"
    )

    # Two writers/readers own this table, plus one deliberate exception.
    #
    # `leaderboard` reads every user's row in one query to rank them. Routing it
    # through `_stats_including_open_session` would mean a second query per
    # user -- reintroducing the N+1 the profile lookup beside it was just
    # batched out of -- just to add an in-flight session to a ranking, where
    # being one session stale is harmless and uniform for everyone on the
    # board. Listed here with its reason so a new reader still has to justify
    # itself.
    ALLOWED = {"_stats_including_open_session", "_stats_including_open_session_many",
               "_credit_session_to_user_stats"}
    ALLOWLIST = {"leaderboard": "ranks all users; one query, staleness is uniform"}

    for pos in readers:
        # Find which function the read sits in by walking back to the nearest def.
        head = source[:pos]
        enclosing = re.findall(r"^def (\w+)", head, re.MULTILINE)[-1]
        assert enclosing in ALLOWED or enclosing in ALLOWLIST, (
            f"{enclosing} reads user_stats directly -- it will report a student "
            "who is mid-session as having answered nothing. Use "
            "_stats_including_open_session(), or add it to ALLOWLIST with a reason."
        )


def test_every_session_close_credits_the_lifetime_totals():
    """Derived from the closes themselves, not a hand-kept list.

    There are three close sites: `/end`, the stale sweep in `start_session`,
    and the one in `class_live`. The stats update used to live only in `/end`,
    so a student who shut the tab -- or whose teacher opened the Live view --
    had every answer in that session dropped from Questions/Correct/Accuracy
    while `session_answers` kept it.

    The three copies are now folded into `_close_session`, since each one had
    drifted separately (the sweep credited a `correct_answers` it never
    selected). So the check is that every closer reaches the helper, and the
    helper still credits.
    """
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
    """The stale sweep used to credit a column it hadn't selected.

    `select("id, started_at, questions_answered")` with no `correct_answers`
    means that key is simply absent; `or 0` reads that as an honest zero, so
    every session the sweep closed -- every student who shuts the tab --
    credited its questions and none of its correct answers. Nothing raises; a
    student's accuracy just falls.

    Derived from what the close actually reads off the row, so a new column
    has to be added to the selects too. Both `_close_session` and
    `_answer_counts` are scanned, since the counters are read from the latter
    now.
    """
    import inspect
    import re

    close_src = (inspect.getsource(main._close_session)
                 + inspect.getsource(main._answer_counts))
    needed = set(re.findall(r'session\.get\("(\w+)"\)', close_src))
    needed.discard("id")
    assert "correct_answers" in needed and "questions_answered" in needed, (
        "the close stopped reading the counts; this test is looking at nothing")

    # Scanned per function that actually closes a session, not over the whole
    # module. `id` + `started_at` alone is only a proxy for that -- a read-only
    # query wanting a session's start time matches the proxy without crediting
    # anything (e.g. `/api/admin/live-signals`). Narrowing to actual closers
    # keeps this from firing on plain reads.
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

    # The regex must keep matching how selects are actually written -- the
    # stale sweep's is split across a line continuation, and a pattern that
    # stopped handling that would leave this test green while checking nothing.
    assert scanned, (
        "no explicit `sessions` select was scanned inside a function that closes "
        "a session -- either the selects moved, or the pattern above stopped "
        "matching how they are written")
