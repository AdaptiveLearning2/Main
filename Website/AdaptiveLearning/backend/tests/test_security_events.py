"""The security log: what gets recorded, what must never be, and who may read it.

There was no audit surface of any kind before this -- a refused read, a
rate-limited caller and a consent change left no queryable trace. The tests
here are in three groups, and the middle one is the point:

- that each hook records the right kind, with the right actor *and subject*;
- that the row can never carry a reading, a request body or an IP, which is
  the one property that cannot be walked back once rows exist;
- that recording never costs the caller their answer -- every hook is on a path
  that has already decided to return a 403, a 429, or a saved consent change.
"""

import os
import re
import pathlib

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402

MIGRATION = (pathlib.Path(__file__).resolve().parents[4]
             / "supabase" / "migrations" / "20260919000000_security_events.sql")

STUDENT = {"id": "student-1", "user_metadata": {}}
TEACHER = {"id": "teacher-1", "user_metadata": {}}


class _Recorder:
    """Captures inserts into `security_events`, passes everything else."""

    def __init__(self, fail=False):
        self.rows = []
        self.fail = fail

    def table(self, name):
        outer, table = self, name

        class _Q:
            def insert(self, obj):
                if table == "security_events":
                    if outer.fail:
                        raise RuntimeError("database is down")
                    outer.rows.append(obj)
                return self

            def select(self, *_a, **_k):  return self
            def eq(self, *_a):            return self
            def order(self, *_a, **_k):   return self
            def limit(self, *_a):         return self
            def single(self):             return self
            def execute(self):            return type("R", (), {"data": []})()

        return _Q()


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(main, "supabase", rec)
    # All four of these are module-level dicts that outlive a test, so a
    # caller id used twice in this file carries its hits across -- which is how
    # the hammering test first failed, on a 429 from the call that was supposed
    # to be *inside* the allowance. Same shape as the persisted view state the
    # frontend suite has to clear in `beforeEach`.
    main._security_event_seen.clear()
    main._strategy_hits.clear()
    main._chart_summary_hits.clear()
    main._ingest_hits.clear()
    return rec


# ─── what gets recorded ──────────────────────────────────────────────────

def test_a_refused_student_read_records_who_and_whose(recorder, monkeypatch):
    monkeypatch.setattr(main, "_can_view_student", lambda *_a: False)

    with pytest.raises(main.HTTPException):
        main._verify_can_view_student(TEACHER, STUDENT["id"])

    assert len(recorder.rows) == 1
    row = recorder.rows[0]
    assert row["kind"] == "authz_denied"
    assert row["actor_user_id"] == TEACHER["id"]
    # The subject is the half that makes the log answerable: "who tried to read
    # this child's record" needs both ids, and an actor-only row cannot say.
    assert row["subject_user_id"] == STUDENT["id"]
    assert row["detail"]["check"] == "can_view_student"


def test_a_refused_session_records_the_owner_as_the_subject(recorder, monkeypatch):
    monkeypatch.setattr(main, "_row_or_404", lambda *_a: {"user_id": STUDENT["id"]})

    with pytest.raises(main.HTTPException):
        main._session_or_403("session-1", TEACHER["id"])

    row = recorder.rows[0]
    assert row["kind"] == "authz_denied"
    assert (row["actor_user_id"], row["subject_user_id"]) == (TEACHER["id"], STUDENT["id"])


def test_reaching_for_the_admin_console_is_its_own_kind(recorder, monkeypatch):
    """Not folded into `authz_denied`.

    Every other denial is a relationship that legitimately does not exist.
    This one is someone trying the console, and collapsed together it would sit
    four rows deep in a list of ordinary refusals.
    """
    monkeypatch.setattr(main, "get_user", lambda _r: STUDENT)
    monkeypatch.setattr(main, "_is_admin", lambda _uid: False)

    class _Req:
        url = type("U", (), {"path": "/api/admin/flags/strategy_llm_enabled"})()

    with pytest.raises(main.HTTPException):
        main._require_admin(_Req())

    assert recorder.rows[0]["kind"] == "admin_denied"


@pytest.mark.parametrize("limiter,fn,setting,window", [
    ("strategies", "_rate_limit_strategies", "_STRATEGY_RATE_LIMIT", "_STRATEGY_RATE_WINDOW"),
    ("chart_summary", "_rate_limit_chart_summary", "_CHART_SUMMARY_RATE_LIMIT", "_CHART_SUMMARY_RATE_WINDOW"),
    ("ingest", "_rate_limit_ingest", "_INGEST_RATE_LIMIT", "_INGEST_RATE_WINDOW"),
])
def test_each_limiter_records_which_one_fired(recorder, monkeypatch, limiter, fn, setting, window):
    monkeypatch.setattr(main, setting, 1)
    monkeypatch.setattr(main, window, 60)
    limit = getattr(main, fn)

    limit("caller-1")                       # inside the allowance
    with pytest.raises(main.HTTPException):
        limit("caller-1")                   # over it

    assert [r["detail"]["limiter"] for r in recorder.rows] == [limiter]
    assert recorder.rows[0]["kind"] == "rate_limited"
    # No subject: a rate limit is about the caller alone.
    assert recorder.rows[0]["subject_user_id"] is None


def test_a_hammering_caller_does_not_write_a_row_per_request(recorder, monkeypatch):
    """The cooldown, and why only this kind has one.

    A limiter fires once per *request* past the allowance, so without it a
    client sending too much would add a database write to every refusal --
    load added at exactly the moment the caller is already sending too much.
    """
    monkeypatch.setattr(main, "_STRATEGY_RATE_LIMIT", 1)
    monkeypatch.setattr(main, "_STRATEGY_RATE_WINDOW", 60)
    monkeypatch.setattr(main, "_SECURITY_EVENT_COOLDOWN_SEC", 300)

    main._rate_limit_strategies("caller-1")
    for _ in range(20):
        with pytest.raises(main.HTTPException):
            main._rate_limit_strategies("caller-1")

    assert len(recorder.rows) == 1, "one row per cooldown, not one per refusal"


def test_the_cooldown_is_per_caller(recorder, monkeypatch):
    """Or one noisy client silences the log for everyone else.

    A limit of 1 and two calls each, not a limit of 0: `_STRATEGY_RATE_LIMIT`
    is read through `_env_number` with a floor of 1 precisely because 0 would
    refuse every request, and at 0 the limiter reaches `min(hits)` on an empty
    list. Setting it here anyway would be testing a state the setting cannot
    hold.
    """
    monkeypatch.setattr(main, "_STRATEGY_RATE_LIMIT", 1)
    monkeypatch.setattr(main, "_STRATEGY_RATE_WINDOW", 60)

    for caller in ("a", "b", "c"):
        main._rate_limit_strategies(caller)
        with pytest.raises(main.HTTPException):
            main._rate_limit_strategies(caller)

    assert len(recorder.rows) == 3


def test_a_denial_is_recorded_every_time(recorder, monkeypatch):
    """Only the limiters are cooled. An authorization denial is rare and each
    one is its own event -- deduplicating them would hide a caller probing a
    series of different students."""
    monkeypatch.setattr(main, "_can_view_student", lambda *_a: False)

    for sid in ("s1", "s2", "s1"):
        with pytest.raises(main.HTTPException):
            main._verify_can_view_student(TEACHER, sid)

    assert len(recorder.rows) == 3


# ─── what must never be recorded ─────────────────────────────────────────

def test_the_row_carries_no_reading_no_body_and_no_address():
    """The property that cannot be walked back once rows exist.

    Asserted over the call sites rather than one row: `detail` is built by
    whatever each hook passes, so the check has to be that no hook passes one
    of these at all.
    """
    source = pathlib.Path(main.__file__).read_text(encoding="utf-8")
    calls = re.findall(r"_record_security_event\((.*?)\)\n", source, re.S)
    assert len(calls) >= 6, f"expected every hook to be found, saw {len(calls)}"

    forbidden = ("focus", "stress", "bpm", "heart", "emotion", "payload",
                 "body", "samples", "ip", "address", "token", "raw")
    for call in calls:
        for word in forbidden:
            assert f"{word}=" not in call, f"{word!r} passed to the security log: {call!r}"


def test_a_long_value_is_truncated_rather_than_stored_whole(recorder):
    main._record_security_event("authz_denied", "a", "b", check="x" * 5000)
    assert len(recorder.rows[0]["detail"]["check"]) == 200


def test_absent_context_is_dropped_rather_than_stored_as_null(recorder):
    main._record_security_event("authz_denied", "a", None, check="c", extra=None)
    assert recorder.rows[0]["detail"] == {"check": "c"}


# ─── recording never costs the caller their answer ───────────────────────

def test_a_failed_write_does_not_turn_a_403_into_a_500(monkeypatch, capsys):
    """Every hook sits on a path that has already decided its answer.

    An audit row that could not be filed must not turn a refusal into a crash,
    so this swallows -- and the log line is the only trace, which is why it
    names the kind.
    """
    monkeypatch.setattr(main, "supabase", _Recorder(fail=True))
    monkeypatch.setattr(main, "_can_view_student", lambda *_a: False)
    main._security_event_seen.clear()

    with pytest.raises(main.HTTPException) as caught:
        main._verify_can_view_student(TEACHER, STUDENT["id"])

    assert caught.value.status_code == 403
    assert "authz_denied" in capsys.readouterr().out


# ─── the read surface ────────────────────────────────────────────────────

def test_the_endpoint_is_admin_only(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: STUDENT)
    monkeypatch.setattr(main, "_is_admin", lambda _uid: False)

    class _Req:
        url = type("U", (), {"path": "/api/admin/security-events"})()

    with pytest.raises(main.HTTPException) as caught:
        main.admin_security_events(_Req())
    assert caught.value.status_code == 403


def test_a_failed_read_says_so_rather_than_reporting_a_quiet_week(monkeypatch):
    """The three-state rule, on the surface where it matters most: an empty
    list from a failed read reads as "nothing has happened"."""
    monkeypatch.setattr(main, "_require_admin", lambda _r: {"id": "admin-1"})

    class _Broken:
        def table(self, _n):
            raise RuntimeError("down")

    monkeypatch.setattr(main, "supabase", _Broken())

    out = main.admin_security_events(None)
    assert out["retrieved"] is False and out["events"] == []


def test_an_unknown_kind_is_refused_rather_than_matching_nothing(monkeypatch):
    monkeypatch.setattr(main, "_require_admin", lambda _r: {"id": "admin-1"})
    with pytest.raises(main.HTTPException) as caught:
        main.admin_security_events(None, kind="not_a_kind")
    assert caught.value.status_code == 422


def test_the_limit_is_clamped(monkeypatch):
    seen = {}
    monkeypatch.setattr(main, "_require_admin", lambda _r: {"id": "admin-1"})
    monkeypatch.setattr(main, "_profiles_many", lambda _ids: {})

    class _Client:
        def table(self, _n):
            class _Q:
                def select(self, *_a, **_k): return self
                def order(self, *_a, **_k):  return self
                def eq(self, *_a):           return self

                def limit(self, n):
                    seen["limit"] = n
                    return self

                def execute(self):           return type("R", (), {"data": []})()
            return _Q()

    monkeypatch.setattr(main, "supabase", _Client())

    main.admin_security_events(None, limit=10_000)
    assert seen["limit"] == main._SECURITY_EVENTS_MAX
    main.admin_security_events(None, limit=0)
    assert seen["limit"] == 1


# ─── the code and the schema agree ───────────────────────────────────────

def test_every_kind_the_code_writes_is_one_the_table_accepts():
    """Derived from the migration's CHECK, so adding a kind in Python without
    the migration fails here rather than as a constraint violation on the
    first event of that kind -- which, since recording never raises, would be
    a swallowed exception and a silently missing row."""
    sql = MIGRATION.read_text(encoding="utf-8")
    block = re.search(r"CHECK \(\"kind\" IN \((.*?)\)\)", sql, re.S)
    assert block, "the kind CHECK is gone from the migration"
    in_sql = set(re.findall(r"'([a-z_]+)'", block.group(1)))

    assert set(main._SECURITY_EVENT_KINDS) == in_sql


def test_every_kind_the_table_accepts_is_actually_written_somewhere():
    """The other direction: a kind nothing produces is a filter that can only
    ever return nothing, which reads as "this never happens"."""
    source = pathlib.Path(main.__file__).read_text(encoding="utf-8")
    for kind in main._SECURITY_EVENT_KINDS:
        # Whitespace-tolerant: one call site wraps its arguments onto the next
        # line, and a plain substring match would report that kind as unwritten
        # purely because of how it is formatted.
        assert re.search(rf'_record_security_event\(\s*"{kind}"', source), kind
