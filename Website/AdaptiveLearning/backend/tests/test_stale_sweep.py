"""When the abandoned-session sweep runs.

`_sweep_abandoned_sessions` itself is exercised in `test_session_alerts.py`;
this file is about the loop around it, which is where the defect was. The
sweep worked correctly every time it was called by hand -- it just was not
being called.
"""
import os
import re
import time

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import main  # noqa: E402


class _FakeDB:
    """Just enough of the supabase client for `student_sessions`: the sessions
    read, plus the four activity sources, any one of which can be made to fail.

    The three signal tables are modelled rather than left to fall through to
    the sessions rows. They used to, harmlessly and by accident -- a session
    row has no `ts`, so every one was skipped -- which meant a test could not
    tell a page that reads them from one that does not.
    """

    SIGNALS = ("cognitive_signals", "face_signals", "heart_signals")

    def __init__(self, sessions, answers, boom=False, signals=None,
                 boom_table="session_answers"):
        self._sessions, self._answers, self._boom = sessions, answers, boom
        self._signals = signals or {}
        self._boom_table = boom_table
        self.filters = []

    def table(self, name):
        self._t = name
        return self

    def select(self, *a, **k): return self
    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    # Both forms are recorded, not swallowed. The measured-row filter is what
    # keeps a headband on a desk from advancing the activity clock, and it is
    # applied server-side -- so a test that only looked at the returned rows
    # could not tell it was there at all.
    #
    # Recording alone is not enough, and it is worth being clear about the
    # limit: nothing here *executes* these. A renamed column or a mis-spelled
    # operator would satisfy every assertion below and throw against a real
    # database. `test_the_filters_are_valid_postgrest` builds them with the
    # real client, and `test_every_filtered_column_exists` checks the names
    # against the migrations.

    def or_(self, expression):
        self.filters.append((self._t, expression))
        return self

    def filter(self, column, operator, value):
        self.filters.append((self._t, f"{column}.{operator}.{value}"))
        return self

    def measured_columns(self, table):
        """Which columns this table's rows were required to have, whichever
        filter form said so -- the semantic the endpoint is asserting, not the
        spelling it happened to use."""
        return {term.split(".")[0]
                for recorded, expression in self.filters if recorded == table
                for term in expression.split(",")}

    def execute(self):
        if self._boom and self._t == self._boom_table:
            raise RuntimeError(f"{self._t} unavailable")
        if self._t == "session_answers":
            return type("R", (), {"data": self._answers})
        if self._t in self.SIGNALS:
            return type("R", (), {"data": self._signals.get(self._t, [])})
        return type("R", (), {"data": self._sessions})


def test_the_first_sweep_does_not_wait_a_whole_interval(monkeypatch):
    """A process that does not outlive one interval never swept at all.

    `uvicorn --reload` restarts on every file save, so on a development machine
    that is most of them: measured on a local stack after a stretch of editing,
    **123 sessions open and every one past the 6h threshold**, on a sweep that
    works correctly when called by hand. In production the same gap is smaller
    and still real -- nothing is collected until an interval after each deploy.
    """
    swept = []
    monkeypatch.setattr(main, "_sweep_abandoned_sessions",
                        lambda *a, **k: swept.append(1) or {})
    # A long interval, so anything that runs must have run *before* the wait.
    monkeypatch.setattr(main, "_STALE_SWEEP_INTERVAL_SEC", 3600.0)
    main._stale_sweep_stop.set()          # stop after the first pass
    try:
        main._stale_sweep_loop()
    finally:
        main._stale_sweep_stop.clear()
    assert swept == [1], "the sweep must run once before waiting on the interval"


def test_a_stop_during_the_first_sweep_is_not_made_to_wait(monkeypatch):
    """The stop event is checked between passes as well as at the top. Without
    that, a shutdown arriving during the first sweep would sit through a whole
    interval before the loop condition noticed -- and this thread is joined on
    shutdown, so that is a hang, not a delay."""
    monkeypatch.setattr(main, "_STALE_SWEEP_INTERVAL_SEC", 3600.0)

    def _sweep_then_stop(*a, **k):
        main._stale_sweep_stop.set()
        return {}

    monkeypatch.setattr(main, "_sweep_abandoned_sessions", _sweep_then_stop)
    started = time.monotonic()
    try:
        main._stale_sweep_loop()
    finally:
        main._stale_sweep_stop.clear()
    assert time.monotonic() - started < 30, "it waited on the interval anyway"


def _session(sid, *, ended=None, started_min_ago=1):
    from datetime import timedelta
    return {"id": sid, "user_id": "u1", "ended_at": ended,
            "started_at": (main._utc_now()
                           - timedelta(minutes=started_min_ago)).isoformat()}


def test_a_quiet_open_session_is_reported_idle_not_live(monkeypatch):
    """`abandoned` is an age (6h) and only stops the list claiming a session
    from June is in progress. A student who answered three questions and shut
    the laptop stayed `LIVE` on the teacher's screen, duration ticking up,
    until that mark -- telling a teacher a child was working who had gone.

    `idle` is real quiet, against the same window `class_live` uses.
    """
    from datetime import timedelta
    old = (main._utc_now() - timedelta(seconds=main._STALE_AFTER_SEC + 120)).isoformat()

    rows = [_session("s-quiet", started_min_ago=30)]
    answers = [{"session_id": "s-quiet", "answered_at": old}]
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *a, **k: None)
    monkeypatch.setattr(main, "get_user", lambda r: {"id": "t1"})
    monkeypatch.setattr(main, "supabase", _FakeDB(rows, answers))

    out = main.student_sessions("u1", request=None)
    assert out[0]["idle"] is True
    assert out[0]["abandoned"] is False, "30 minutes is nowhere near the 6h age"
    assert out[0]["activity_known"] is True


def test_a_session_answered_just_now_is_still_live(monkeypatch):
    """The teeth. Marking everything idle would be as wrong as marking
    everything live."""
    rows = [_session("s-busy", started_min_ago=30)]
    answers = [{"session_id": "s-busy", "answered_at": main._utc_now().isoformat()}]
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *a, **k: None)
    monkeypatch.setattr(main, "get_user", lambda r: {"id": "t1"})
    monkeypatch.setattr(main, "supabase", _FakeDB(rows, answers))

    assert main.student_sessions("u1", request=None)[0]["idle"] is False


def test_a_failed_activity_read_never_claims_idle(monkeypatch):
    """Three states, not two. A database blip must not relabel a live session
    as quiet -- the same error as reporting a failed count as a quiet week. The
    client gates on `activity_known` for exactly this."""
    rows = [_session("s-unknown", started_min_ago=30)]
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *a, **k: None)
    monkeypatch.setattr(main, "get_user", lambda r: {"id": "t1"})
    monkeypatch.setattr(main, "supabase", _FakeDB(rows, answers=None, boom=True))

    out = main.student_sessions("u1", request=None)
    assert out[0]["activity_known"] is False
    assert out[0]["idle"] is False, "unknown is not idle"


def _as_teacher(monkeypatch, db):
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *a, **k: None)
    monkeypatch.setattr(main, "get_user", lambda r: {"id": "t1"})
    monkeypatch.setattr(main, "supabase", db)


def test_a_session_streaming_signals_is_not_idle(monkeypatch):
    """`class_live` derives staleness from the three signal tables AND
    answers; this endpoint read answers alone, against the same window. So a
    student streaming EEG through a long question -- or spending ten minutes
    pairing a headband before answering anything -- was active on Live
    Monitoring and `idle` here, about the same session at the same moment.

    Sharing the window without the inputs is what made that disagreement look
    like a bug in one of the two pages.
    """
    from datetime import timedelta
    old = (main._utc_now() - timedelta(seconds=main._STALE_AFTER_SEC + 120)).isoformat()
    rows = [_session("s-eeg", started_min_ago=30)]
    answers = [{"session_id": "s-eeg", "answered_at": old}]
    signals = {"cognitive_signals": [
        {"session_id": "s-eeg", "ts": main._utc_now().isoformat()}]}
    _as_teacher(monkeypatch, _FakeDB(rows, answers, signals=signals))

    out = main.student_sessions("u1", request=None)
    assert out[0]["idle"] is False, "the headband has been streaming throughout"
    assert out[0]["last_activity_at"] == signals["cognitive_signals"][0]["ts"]


def test_the_newest_source_wins_whichever_table_it_came_from(monkeypatch):
    """The negative control for the test above: with every signal stale as
    well, the session really is quiet. Without this, that test would pass
    against an endpoint that had simply stopped reporting anything idle."""
    from datetime import timedelta
    old = (main._utc_now() - timedelta(seconds=main._STALE_AFTER_SEC + 120)).isoformat()
    rows = [_session("s-quiet-all", started_min_ago=30)]
    answers = [{"session_id": "s-quiet-all", "answered_at": old}]
    signals = {"heart_signals": [{"session_id": "s-quiet-all", "ts": old}]}
    _as_teacher(monkeypatch, _FakeDB(rows, answers, signals=signals))

    assert main.student_sessions("u1", request=None)[0]["idle"] is True


def test_a_failed_signal_read_is_unknown_too(monkeypatch):
    """Any one of the four sources failing can only *under*-report activity,
    and under-reported activity is exactly what calls a working session quiet.
    So the flag goes unknown rather than the read being quietly skipped."""
    rows = [_session("s-partial", started_min_ago=30)]
    _as_teacher(monkeypatch, _FakeDB(rows, answers=[], boom=True,
                                     boom_table="heart_signals"))

    out = main.student_sessions("u1", request=None)
    assert out[0]["activity_known"] is False
    assert out[0]["idle"] is False


def test_a_headband_on_a_desk_does_not_keep_a_session_alive(monkeypatch):
    """The regression that folding in the signal tables introduced.

    A `contact_poor` row is a real row with a real `ts` and its measurement
    columns nulled -- "recording but unable to measure", which the mapper
    keeps on purpose. A headband left on a desk writes one every poller tick,
    for ever, so counting them advanced this clock indefinitely: `idle` never
    fired and the session showed a pulsing LIVE with a ticking duration for
    the full six hours. That is precisely the bug `idle` exists to remove,
    back for the sessions most likely to be left open.

    Asserted on the *filter*, not on the returned rows: it is applied
    server-side, so a fake that simply returned nothing would pass against an
    endpoint with no filter at all.
    """
    from datetime import timedelta
    old = (main._utc_now() - timedelta(seconds=main._STALE_AFTER_SEC + 120)).isoformat()
    rows = [_session("s-desk", started_min_ago=30)]
    answers = [{"session_id": "s-desk", "answered_at": old}]
    db = _FakeDB(rows, answers)
    _as_teacher(monkeypatch, db)

    out = main.student_sessions("u1", request=None)
    assert out[0]["idle"] is True, "nothing measured anything; the student left"

    assert db.measured_columns("cognitive_signals") == {"focus"}
    assert db.measured_columns("heart_signals") == {"heart_rate_bpm"}
    assert db.measured_columns("session_answers") == set(), (
        "an answer is activity whatever the sensors were doing")


def test_a_gaze_only_face_row_counts_as_a_measurement(monkeypatch):
    """`face_signals` has two producers and either may succeed alone --
    `20260819000000` says a row is enqueued when *either* does.

    `-Gaze -NoEmotion` is a supported and deliberately cheaper camera
    deployment, and in it every face row has `emotion` NULL. Filtering on
    emotion alone would make the camera contribute no activity at all there,
    so a student it was measuring through a long question would still read
    idle -- the `contact_poor` failure from the other direction. Head pose
    refuses independently of gaze, so a pose-only row counts too.
    """
    rows = [_session("s-cam", started_min_ago=30)]
    db = _FakeDB(rows, answers=[])
    _as_teacher(monkeypatch, db)
    main.student_sessions("u1", request=None)

    face = db.measured_columns("face_signals")
    assert "emotion" in face
    assert "gaze_x" in face, "a gaze-only deployment measures too"
    assert "head_yaw" in face, "pose refuses independently of gaze"


def test_a_measured_signal_row_still_counts(monkeypatch):
    """The teeth for the test above. Filtering every signal row out would
    also make a headband on a desk look idle, and would undo the fix that
    reads signals at all."""
    from datetime import timedelta
    old = (main._utc_now() - timedelta(seconds=main._STALE_AFTER_SEC + 120)).isoformat()
    rows = [_session("s-worn", started_min_ago=30)]
    answers = [{"session_id": "s-worn", "answered_at": old}]
    # The fake applies no filter, so this stands for a row that passed it.
    signals = {"cognitive_signals": [
        {"session_id": "s-worn", "ts": main._utc_now().isoformat()}]}
    _as_teacher(monkeypatch, _FakeDB(rows, answers, signals=signals))

    assert main.student_sessions("u1", request=None)[0]["idle"] is False


def test_the_filters_are_valid_postgrest():
    """Built with the real client, not the fake, and asserted on the wire.

    Everything else in this file records the filter and hands it back, which
    proves a string was passed and nothing more. A renamed column, a
    mis-spelled `not.is`, or a query shape the library refuses would satisfy
    all of it and throw inside the request against a real database -- and
    that throw is caught, so `activity_known` would go False for every
    session, `idle` with it, and the pulsing LIVE badge would come back with
    a green suite.

    Also pins the single-column form. `or=(focus.not.is.null)` is a
    one-branch or-tree, which is a shape nothing else here emits; the plain
    `focus=not.is.null` is what the single-column sources use.
    """
    from urllib.parse import unquote
    from postgrest import SyncPostgrestClient

    client = SyncPostgrestClient("http://localhost:54321/rest/v1")
    emitted = {}
    for table, column, measured in main._ACTIVITY_SOURCES:
        query = (client.table(table).select(f"session_id, {column}")
                 .in_("session_id", ["11111111-1111-1111-1111-111111111111"]))
        if measured:
            query = main._measured_only(query, measured)
        emitted[table] = unquote(str(query.order(column, desc=True)
                                     .limit(500).request.params))

    assert "focus=not.is.null" in emitted["cognitive_signals"]
    assert "heart_rate_bpm=not.is.null" in emitted["heart_signals"]
    assert ("or=(emotion.not.is.null,gaze_x.not.is.null,head_yaw.not.is.null)"
            in emitted["face_signals"])
    assert "not.is.null" not in emitted["session_answers"], (
        "an answer is activity whatever the sensors were doing")
    for table, params in emitted.items():
        assert "order=" in params and "limit=500" in params, (table, params)


def _columns_after_every_migration():
    """Per-table column sets, replayed over the migrations in order.

    Deliberately not a substring search over the concatenated files, which is
    what this started as and is two kinds of wrong: it is not table-scoped, and
    a name that appears only in a `DROP COLUMN` still matches. The proof is in
    the tree -- `identity_confidence` occurs in 20260812000000 solely as
    `DROP COLUMN IF EXISTS`, so putting it in `_ACTIVITY_SOURCES` passed. A
    check that exists to catch a retirement could not catch a retirement.

    Still only a reading of SQL text. `scripts/assert_signal_rls.sql` asks the
    live schema in CI and is the authoritative half; this one runs in every
    local pytest with no stack, and its job is fast feedback.
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    migrations = os.path.join(root, "..", "..", "supabase", "migrations")
    return _replay_columns(
        open(os.path.join(migrations, name), encoding="utf-8").read()
        for name in sorted(os.listdir(migrations)) if name.endswith(".sql"))


def _replay_columns(statements):
    """The parser itself, over SQL texts in order.

    Split from the reader so a shape with no example in the tree can still be
    pinned -- a multi-clause `DROP COLUMN` is the one this file's history says
    to worry about, and there is none to point at.
    """
    columns: dict[str, set] = {}
    create = re.compile(
        r'CREATE TABLE (?:IF NOT EXISTS )?"public"\."(\w+)"\s*\((.*?)\n\);',
        re.S | re.I)
    # Two stages, because one ALTER TABLE carries any number of clauses and a
    # single pattern matches each statement once. `20260820000000` is exactly
    # that shape -- three `ADD COLUMN`s under one ALTER -- and a
    # statement-at-a-time regex saw `head_yaw` and neither of the others.
    #
    # It passed only because `_ACTIVITY_SOURCES` happens to name the first of
    # the three. Adding `head_pitch` would have failed this guard with a
    # message insisting the column does not exist, and a future multi-clause
    # `DROP COLUMN` would have restored exactly the drop-blindness the replay
    # was written to remove.
    alter = re.compile(
        r'ALTER TABLE (?:ONLY )?"?public"?\."?(\w+)"?(.*?);', re.S | re.I)
    clause = re.compile(
        r'\b(ADD|DROP) COLUMN (?:IF (?:NOT )?EXISTS )?"?(\w+)"?', re.I)

    for sql in statements:
        for table, body in create.findall(sql):
            found = columns.setdefault(table, set())
            for line in body.splitlines():
                match = re.match(r'\s*"(\w+)"\s+\S', line)
                if match:
                    found.add(match.group(1))
        for table, body in alter.findall(sql):
            found = columns.setdefault(table, set())
            # In clause order: one statement may drop and add, and the last
            # word about a column has to win.
            for action, column in clause.findall(body):
                if action.upper() == "ADD":
                    found.add(column)
                else:
                    found.discard(column)
    return columns


def test_every_filtered_column_exists():
    """The half a recorded filter string cannot check: that the columns are
    real, on the table being filtered, and still there.

    `gaze_x` and `head_yaw` arrived in migrations later than their table, so
    "it is on face_signals" was never something to take on trust.
    """
    columns = _columns_after_every_migration()
    for table, column, measured in main._ACTIVITY_SOURCES:
        assert columns.get(table), (
            f"no CREATE TABLE parsed for {table} -- this guard has gone "
            "inert, which is worse than it failing")
        for name in (column, *measured):
            assert name in columns[table], (
                f"{table}.{name} is not a column after the migrations replay. "
                "student_sessions filters an activity read on it, so PostgREST "
                "would reject the request, the endpoint would swallow it, and "
                "every session would report activity unknown -- a quiet "
                "session reads LIVE again.")


def test_the_column_replay_reads_every_clause_of_a_multi_clause_alter():
    """One `ALTER TABLE` can carry any number of clauses, and a regex over
    whole statements matches each one once.

    `20260820000000` adds `head_yaw`, `head_pitch` and `head_roll` under a
    single ALTER, and the first version of the replay saw only `head_yaw`.
    Nothing failed, because `_ACTIVITY_SOURCES` names exactly that one -- so
    the guard was wrong and green, and would have started accusing a real
    column of not existing the moment anyone filtered on `head_pitch`.

    Pinned on all three rather than on the parser's shape: what matters is
    that a clause is not skipped, not how the skipping was avoided.
    """
    face = _columns_after_every_migration()["face_signals"]
    for column in ("head_yaw", "head_pitch", "head_roll"):
        assert column in face, (
            f"{column} was added by a multi-clause ALTER and the replay "
            "missed it -- every clause after the first is being dropped")


def test_the_replay_handles_a_multi_clause_drop():
    """The shape with no example in the tree, and the one the multi-clause
    ADD bug says to expect: a statement dropping several columns at once must
    drop all of them, not just the first.

    Synthetic because there is nothing to point at yet -- which is exactly
    why it is worth pinning now rather than after a migration relies on it.
    """
    columns = _replay_columns([
        'CREATE TABLE IF NOT EXISTS "public"."t" (\n'
        '    "a" integer,\n    "b" integer,\n    "c" integer\n);',
        'ALTER TABLE "public"."t"\n'
        '    DROP COLUMN IF EXISTS "a",\n    DROP COLUMN IF EXISTS "b";',
    ])
    assert columns["t"] == {"c"}


def test_the_replay_takes_the_last_word_about_a_column():
    """One statement may drop and re-add, so clause order decides."""
    columns = _replay_columns([
        'CREATE TABLE IF NOT EXISTS "public"."t" (\n    "a" integer\n);',
        'ALTER TABLE "public"."t"\n'
        '    DROP COLUMN IF EXISTS "a",\n    ADD COLUMN IF NOT EXISTS "a" text;',
    ])
    assert columns["t"] == {"a"}


def test_the_column_replay_notices_a_drop():
    """The teeth for the parser, against a column this repo really retired.

    `identity_confidence` was created on `face_signals` in 20260625000000 and
    dropped in 20260812000000. The substring version of the test above matched
    it in the DROP statement and passed, which is exactly the failure it was
    written to prevent.
    """
    columns = _columns_after_every_migration()
    assert "emotion" in columns["face_signals"], "the parser found the table"
    assert "identity_confidence" not in columns["face_signals"], (
        "a dropped column is still being counted as present")


def test_nothing_to_look_up_is_not_a_failed_read(monkeypatch):
    """`activity_known` separates "quiet" from "we could not tell", so it has
    to mean the second and only the second. It was initialised `False` and
    raised only inside `if ids:`, so a student whose sessions are all closed
    -- the ordinary case for anyone not mid-lesson -- published the flag that
    means the database read failed. Nothing renders it today, which is the
    only reason this was invisible rather than wrong on screen."""
    rows = [_session("s-done", ended=main._utc_now().isoformat())]
    _as_teacher(monkeypatch, _FakeDB(rows, answers=[]))

    out = main.student_sessions("u1", request=None)
    assert out[0]["activity_known"] is True
    assert out[0]["idle"] is False
