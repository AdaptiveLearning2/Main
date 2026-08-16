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
    def __init__(self, subject="algebra", prior=None, topics=("algebra",), raises=()):
        self.subject = subject
        self.prior = prior
        self.topics = topics
        self.raises = set(raises)
        self.upserts = []
        # (table, column, value) for every filter applied. The fake answers from
        # canned data, so without recording this a test can assert on the result
        # while the code under test looks up something else entirely -- which is
        # exactly what the first version of the attribution test below did.
        self.filters = []

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

            def insert(self, _row):
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
                if table == "sessions":
                    row = {"id": "s-1", "questions_answered": 0, "correct_answers": 0}
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


def test_a_first_attempt_creates_the_topic_row(_client):
    c = _client()

    main._record_topic_attempt(USER, QUESTION, correct=True)

    row, kw = c.upserts[0]
    assert row["user_id"] == USER and row["topic_id"] == 7
    assert row["attempted_questions"] == 1 and row["correct_questions"] == 1
    assert kw["on_conflict"] == "user_id,topic_id"


def test_a_wrong_answer_counts_as_an_attempt_and_not_a_correct(_client):
    c = _client(prior={"attempted_questions": 4, "correct_questions": 3})

    main._record_topic_attempt(USER, QUESTION, correct=False)

    row, _ = c.upserts[0]
    assert row["attempted_questions"] == 5
    assert row["correct_questions"] == 3


def test_the_topic_comes_from_the_question_not_the_caller(_client):
    """The client already has to be trusted about correctness. Letting it name
    the topic as well would let a page credit one subject for work done in
    another -- and this table is what the adaptive engine reads to choose what
    to serve next, so a wrong attribution steers a student's whole session."""
    c = _client(subject="geometry", topics=("geometry",))

    # No topic is passed in; the only inputs are the user, the question and
    # whether it was right.
    main._record_topic_attempt(USER, QUESTION, correct=True)

    # Asserting on the *query*, not on the canned answer. The fake returns its
    # subject whatever is asked for, so checking the resulting topic_id passes
    # just as happily when the question id is never used -- verified by breaking
    # the lookup and watching this test stay green, which is why it now reads
    # the filter that was actually applied.
    assert ("questions", "id", QUESTION) in c.filters
    assert ("math_topics", "topic_name", "geometry") in c.filters
    assert c.upserts and c.upserts[0][0]["topic_id"] == 7


def test_a_subject_with_no_topic_row_records_nothing_rather_than_inventing_one(_client):
    c = _client(subject="astrophysics", topics=("algebra",))

    main._record_topic_attempt(USER, QUESTION, correct=True)

    assert c.upserts == []


@pytest.mark.parametrize("table", ["questions", "math_topics", "user_math_performance"])
def test_a_failed_lookup_never_raises(_client, table):
    """This runs *after* the answer is written. `session_answers` is the record
    that matters and it is already safe by this point, so a topic lookup that
    fails must not turn a recorded answer into a 500 the page reports as
    'that answer could not be saved'."""
    _client(raises=[table])

    main._record_topic_attempt(USER, QUESTION, correct=True)  # must not raise


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
    # on a board that already has an N+1 problem, to add an in-flight session to
    # a *ranking* -- where being one session stale is both harmless and uniform
    # across everyone on the board. Listed with its reason rather than left to
    # slip through a regex, so the next reader still has to justify itself.
    ALLOWED = {"_stats_including_open_session", "_credit_session_to_user_stats"}
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

    closers = []
    for name, obj in vars(main).items():
        if not inspect.isfunction(obj) or obj.__module__ != "main":
            continue
        try:
            src = inspect.getsource(obj)
        except OSError:                      # pragma: no cover -- defensive
            continue
        if '"ended_at":' in src and name != "_close_session":
            closers.append((name, src))

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

    Derived from what `_close_session` reads off the row, so a column added to
    the close has to be added to the selects too.
    """
    import inspect
    import re

    close_src = inspect.getsource(main._close_session)
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
