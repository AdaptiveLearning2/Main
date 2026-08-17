"""An answer on the adaptive page reaches the database.

It did not. `Adaptive.jsx` had no `/api/sessions/{id}/answer` call at all -- the
only page that did was Practice.jsx -- so every question answered through the
adaptive path was counted in that browser's localStorage and nowhere else.
`session_answers`, `sessions.questions_answered`, `user_stats` and every report
built on them read zero however long a student practised, while the page's own
Topic Accuracy panel showed figures. Two records of the same afternoon, one of
them private to a browser.

The half worth pinning is the per-topic attribution, because it decides what the
adaptive engine serves next and because it is the half a client could get wrong.
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
        # canned data, so without recording this a test can assert on the result
        # while the code under test looks up something else entirely -- which is
        # exactly what the first version of the attribution test below did.
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
    """The arithmetic moved into `record_topic_attempt` (20260825000000).

    It used to be four sequential round trips on the hottest path in the
    product, and the last two of them were a read-modify-write with no lock: two
    answers landing together both read the same counts and the second write
    overwrote the first, so attempts disappeared from the table the adaptive
    engine reads to choose what to serve next.

    Whether the counters actually increment is Postgres's job now, and is
    asserted against a real stack in `scripts/assert_signal_rls.sql`. What is
    checkable here is that this makes one call and passes what the function
    needs.
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
    the topic as well would let a page credit one subject for work done in
    another -- and this table is what the adaptive engine reads to choose what
    to serve next, so a wrong attribution steers a student's whole session."""
    c = _client(subject="geometry", topics=("geometry",))

    main._record_topic_attempt(USER, QUESTION, correct=True)

    # Structural now that the join is in SQL: the only identifiers crossing the
    # boundary are the student, the question and whether it was right. A topic
    # cannot be smuggled in because there is no parameter to put one in, which
    # is a stronger guarantee than the query-shape check this replaces.
    _, params = c.rpcs[0]
    assert set(params) == {"p_user_id", "p_question_id", "p_correct"}
    assert params["p_question_id"] == QUESTION
    assert not any("topic" in k for k in params if k != "p_question_id")


def test_a_failed_attempt_never_raises(_client):
    """This runs *after* the answer is written. `session_answers` is the record
    that matters and it is already safe by this point, so an attribution that
    fails must not turn a recorded answer into a 500 the page reports as
    'that answer could not be saved'."""
    _client(rpc_error="boom")

    main._record_topic_attempt(USER, QUESTION, correct=True)  # must not raise


def test_a_missing_function_is_reported_as_a_missing_migration(_client, capsys):
    """PGRST202 is the deploy-ordering trap, and it is silent by design.

    This helper swallows its exceptions -- it must, because a failed lookup may
    not cost a student the answer already recorded -- so code deployed ahead of
    20260825000000 stops attributing anything with no symptom other than the
    numbers not moving. Every other failure here is transient and the next
    answer retries it; this one is every answer until somebody applies the
    migration, so it gets its own sentence in the log.
    """
    _client(rpc_error="{'code': 'PGRST202', 'message': 'no function'}")

    main._record_topic_attempt(USER, QUESTION, correct=True)

    out = capsys.readouterr().out
    assert "20260825000000" in out, f"the log does not name the migration: {out}"


def test_the_answer_endpoint_updates_the_topic_record(monkeypatch):
    """Wiring, not arithmetic: the helper is only useful if `record_answer`
    calls it, and that call is one line away from being dropped in a merge."""
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
    """`/end` credits the session's *cumulative* counts, not a delta.

    So a session already closed by the stale sweep or by a teacher opening the
    Live view -- both of which credit -- was credited a second time in full when
    the student's page finally called `/end`, doubling every answer in the
    lifetime totals and inflating the accuracy a parent reads. The student's
    page has no way to know a teacher's view closed the session underneath it,
    so this is an ordinary race, not a misuse.
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
    monkeypatch.setattr(main, "_discard_if_nothing_recorded", lambda *_a: False)
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

    def __init__(self, stored, answers=(), claim_wins=True, confirmed_ended_at=None):
        self.stored = stored
        self.answers = list(answers)
        self.claim_wins = claim_wins
        self.confirmed_ended_at = confirmed_ended_at
        self.updates = []
        self.deleted = []

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
                    row = dict(client.stored)
                    if client.confirmed_ended_at is not None:
                        row["ended_at"] = client.confirmed_ended_at
                    return type("R", (), {"data": [row]})()
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

    That read and the write below it are two statements, so a `/end` arriving
    while the stale sweep or `class_live` is closing the same session passes the
    check and runs the whole sequence anyway. Both closes credit the session's
    *cumulative* counts, so every answer in it landed twice in the lifetime
    totals -- inflating exactly the accuracy figure a parent reads.
    """
    credited = []
    client = _ClaimClient(
        {"id": "s-1", "user_id": USER, "questions_answered": 6, "correct_answers": 4,
         "started_at": "2026-08-15T10:00:00Z"},
        answers=[{"correct": True}] * 6,
        claim_wins=False,
        confirmed_ended_at="2026-08-15T11:00:00Z",   # somebody else's stamp
    )
    _close_with(monkeypatch, client, credited)

    out = main._close_session(USER, {"id": "s-1", "questions_answered": 6,
                                     "correct_answers": 4,
                                     "started_at": "2026-08-15T10:00:00Z"},
                              "2026-08-15T11:30:00Z")

    assert out.get("already_closed") is True
    assert credited == [], "a close that lost the stamp credited the totals anyway"


def test_a_stamp_that_reports_nothing_is_confirmed_rather_than_assumed_lost(monkeypatch):
    """An empty update result is ambiguous, and guessing costs every close.

    PostgREST returns the updated row only when asked to; a client that is not
    asking returns an empty list from a stamp that landed perfectly well.
    Reading "empty" as "somebody beat me to it" would silently skip the credit,
    the rollup and the archive for *every* session -- a far worse failure than
    the double-credit the claim exists to prevent.
    """
    credited = []
    client = _ClaimClient(
        {"id": "s-1", "user_id": USER, "questions_answered": 6, "correct_answers": 4,
         "started_at": "2026-08-15T10:00:00Z"},
        answers=[{"correct": True}] * 4 + [{"correct": False}] * 2,
        claim_wins=False,
        confirmed_ended_at=None,          # still open: nobody else stamped it
    )
    _close_with(monkeypatch, client, credited)

    main._close_session(USER, {"id": "s-1", "questions_answered": 6,
                               "correct_answers": 4,
                               "started_at": "2026-08-15T10:00:00Z"},
                        "2026-08-15T11:30:00Z")

    assert credited == [(USER, 6, 4)]


def test_the_stamp_is_conditional(monkeypatch):
    """Belt and braces on the mechanism itself: an unconditional update would
    satisfy both tests above by never losing, and would race exactly as before.
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
    """`questions_answered` is a cache, and the close was the last thing trusting it.

    `record_answer` writes the answer row and bumps the counter in two separate
    statements with nothing around them, so the counter can sit at zero while
    real answers exist. `_discard_if_nothing_recorded` already knows that and
    re-checks `session_answers` before deleting a session that looks empty -- so
    the session was correctly saved, and then credited zero questions and zero
    correct answers, because the credit was reading the same stale field. The
    student's work survived in `session_answers` and never reached the lifetime
    totals. Permanently: no later close revisits a session that is stamped.
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
    """The recount may only ever find *more*.

    Rows are the record, but crediting fewer answers than a previous reading of
    this session already showed is the direction that loses a student's work --
    and a `session_answers` read that returns short looks exactly like one that
    is right.
    """
    credited = []
    stale = {"id": "s-1", "user_id": USER, "questions_answered": 9,
             "correct_answers": 7, "started_at": "2026-08-15T10:00:00Z"}
    client = _ClaimClient(stale, answers=[{"correct": True}])
    _close_with(monkeypatch, client, credited)

    main._close_session(USER, dict(stale), "2026-08-15T11:30:00Z")

    assert credited == [(USER, 9, 7)]


def test_every_close_site_stops_the_poller_first():
    """Derived, because this ordering was wrong at one of the three sites.

    `class_live` stamped `ended_at` and ran the whole close before stopping the
    poller. A tick landing in that gap inserts a `cognitive_signals` row for the
    session *after* `_discard_if_nothing_recorded` has looked -- so an abandoned
    pairing is deleted with a row pointing at it -- and the rollup is computed
    from a day that gains a sample a moment later.

    Now that the stamp lives inside `_close_session`, "stop the poller before
    you call it" is the whole of the ordering rule, which is what makes it
    checkable rather than a sentence in a docstring.
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
    makes several reads per student, so the per-student version turned a class
    of thirty into sixty extra round trips on a page a teacher lives in."""
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
    live delta must land on the student it belongs to rather than the roster."""
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
    """`user_stats` gains a row when a session *closes*, so reading it directly
    reports a student mid-lesson as having answered nothing.

    Four surfaces did: `/api/stats/me`, `/api/stats/student`, the class roster
    and the parent's children list. The teacher's dashboard showed a class
    average of "—" for a class whose only student was sitting at 67% on the
    Students page two clicks away, because the average came from one of the
    readers and the tile from another.

    Derived from the source rather than listed, so a fifth reader cannot be
    added quietly -- the same exhaustiveness pattern as `_MODE_AWARE` and
    `test_every_recording_site_gates_on_the_window`. `_credit_session_to_user_stats`
    is the writer and is allowed; `_stats_including_open_session` is the reader.
    """
    import inspect
    import re

    source = inspect.getsource(main)
    # `[\s\\]*`, not `\s*`. A backslash line continuation is not whitespace, so
    # the original pattern silently skipped `leaderboard`, which splits the call
    # across two lines exactly that way -- the one reader this test existed to
    # catch was the one it could not see, and it passed while being blind.
    readers = [m.start() for m in
               re.finditer(r'table\("user_stats"\)[\s\\]*\.?[\s\\]*select', source)]
    assert len(readers) >= 3, (
        "the call shape changed, or the line-continuation blind spot is back; "
        f"found {len(readers)} readers and there are at least three"
    )

    # Two writers/readers own this table, plus one deliberate exception.
    #
    # `leaderboard` reads every user's row in one query to rank them. Routing it
    # through `_stats_including_open_session` would mean a second query per user
    # -- reintroducing the N+1 the profile lookup beside it was just batched out
    # of -- to add an in-flight session to a *ranking*, where being one session
    # stale is both harmless and uniform across everyone on the board. Listed
    # with its reason rather than left to slip through a regex, so the next
    # reader still has to justify itself.
    ALLOWED = {"_stats_including_open_session", "_stats_including_open_session_many",
               "_credit_session_to_user_stats"}
    ALLOWLIST = {"leaderboard": "ranks all users; one query, staleness is uniform"}

    for pos in readers:
        # Which function the read sits in, by walking back to the nearest def.
        head = source[:pos]
        enclosing = re.findall(r"^def (\w+)", head, re.MULTILINE)[-1]
        assert enclosing in ALLOWED or enclosing in ALLOWLIST, (
            f"{enclosing} reads user_stats directly -- it will report a student "
            "who is mid-session as having answered nothing. Use "
            "_stats_including_open_session(), or add it to ALLOWLIST with a reason."
        )


def test_every_session_close_credits_the_lifetime_totals():
    """Derived from the closes themselves, not a hand-kept list.

    There are three: `/end`, the stale sweep in `start_session`, and the one in
    `class_live`. The stats update lived only in `/end`, so a student who shut
    the tab -- or whose teacher opened the Live view -- had every answer in that
    session dropped from Questions/Correct/Accuracy while `session_answers` kept
    it. Fixing two of the three still left the class average reading "—" for a
    class whose only student was at 67%, because the third was the one that had
    actually closed the session.

    The three copies have since been folded into `_close_session`, because each
    one drifted separately: the sweep credited a `correct_answers` it had never
    selected, so every tab-close added questions and zero correct answers to a
    student's record. So the check is now that every closer reaches the helper,
    and that the helper still credits.
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


def test_the_close_reads_every_column_it_credits():
    """The stale sweep credited a column it had not selected.

    `select("id, started_at, questions_answered")` with no `correct_answers`
    means the key is simply absent, `or 0` reads that as an honest zero, and
    every session closed by the sweep -- which is every session of a student who
    shuts the tab -- credited its questions and none of its correct answers.
    Nothing raises; a student's accuracy just falls.

    Derived from what the close reads off the row, so a column added to it has
    to be added to the selects too. Both halves are read: `_answer_counts` is
    where the counters are now read from, and deriving `needed` from
    `_close_session` alone would have quietly stopped checking anything the
    moment they moved.
    """
    import inspect
    import re

    close_src = (inspect.getsource(main._close_session)
                 + inspect.getsource(main._answer_counts))
    needed = set(re.findall(r'session\.get\("(\w+)"\)', close_src))
    needed.discard("id")
    assert "correct_answers" in needed and "questions_answered" in needed, (
        "the close stopped reading the counts; this test is looking at nothing")

    source = inspect.getsource(main)
    # Every explicit column list selected from `sessions`. `select("*")` is fine
    # by construction and is skipped.
    for cols in re.findall(r'table\("sessions"\)\s*\\?\s*\.select\("([^"*]+)"\)', source):
        listed = {c.strip() for c in cols.split(",")}
        if "id" not in listed or "started_at" not in listed:
            continue        # not a close-site select
        missing = needed - listed
        assert not missing, (
            f"a session close selects {sorted(listed)} but the close reads "
            f"{sorted(missing)} -- absent columns arrive as None and `or 0` "
            "turns that into a zero that looks measured"
        )
