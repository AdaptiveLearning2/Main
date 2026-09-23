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

import ast
import os
import re
import pathlib

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402
from conftest import tighten  # noqa: E402

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
    main._STRATEGY_LIMITER.reset()
    main._CHART_SUMMARY_LIMITER.reset()
    main._INGEST_LIMITER.reset()
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


@pytest.mark.parametrize("limiter,fn,budget", [
    ("strategies", "_rate_limit_strategies", "_STRATEGY_LIMITER"),
    ("chart_summary", "_rate_limit_chart_summary", "_CHART_SUMMARY_LIMITER"),
    ("ingest", "_rate_limit_ingest", "_INGEST_LIMITER"),
])
def test_each_limiter_records_which_one_fired(recorder, monkeypatch, limiter, fn, budget):
    tighten(monkeypatch, getattr(main, budget), limit=1, window=60)
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
    tighten(monkeypatch, main._STRATEGY_LIMITER, limit=1, window=60)
    monkeypatch.setattr(main, "_SECURITY_EVENT_COOLDOWN_SEC", 300)

    main._rate_limit_strategies("caller-1")
    for _ in range(20):
        with pytest.raises(main.HTTPException):
            main._rate_limit_strategies("caller-1")

    assert len(recorder.rows) == 1, "one row per cooldown, not one per refusal"


def test_the_cooldown_is_per_caller(recorder, monkeypatch):
    """Or one noisy client silences the log for everyone else.

    A limit of 1 and two calls each, not a limit of 0: `STRATEGY_RATE_LIMIT`
    is read through `_env_number` with a floor of 1 precisely because 0 would
    refuse every request, and at 0 the limiter reaches `min(hits)` on an empty
    list. Setting it here anyway would be testing a state the setting cannot
    hold.
    """
    tighten(monkeypatch, main._STRATEGY_LIMITER, limit=1, window=60)

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


# ─── the page names an account, or says it cannot ────────────────────────

class _ReadClient:
    """`security_events` rows, and a `profiles` read that can be made to fail."""

    def __init__(self, rows, profiles=(), names_fail=False):
        self.rows, self.profiles, self.names_fail = rows, list(profiles), names_fail

    def table(self, name):
        is_profiles = name == "profiles"
        fail = is_profiles and self.names_fail
        data = self.profiles if is_profiles else self.rows

        class _Q:
            def select(self, *_a, **_k): return self
            def order(self, *_a, **_k):  return self
            def limit(self, *_a):        return self
            def eq(self, *_a):           return self
            def in_(self, *_a):          return self

            def execute(self):
                if fail:
                    raise RuntimeError("profiles is down")
                return type("R", (), {"data": data})()

        return _Q()


def _one_event():
    return [{"id": 1, "kind": "authz_denied", "actor_user_id": "teacher-1",
             "subject_user_id": "student-1", "detail": {}, "created_at": "2026-09-19T10:00:00Z"}]


def test_an_account_with_no_profile_row_is_not_handed_a_name(monkeypatch):
    """`_profiles_many` answers a missing row with `_placeholder_profile`,
    whose `display_name` is the literal "Student" -- right where a blank name
    would otherwise read as a withdrawn preference, and wrong on an audit page,
    where it puts a plausible name on a row and on a teacher's row the wrong
    one. The name is absent here, and the id carries the row."""
    monkeypatch.setattr(main, "_require_admin", lambda _r: {"id": "admin-1"})
    monkeypatch.setattr(main, "supabase", _ReadClient(_one_event()))

    out = main.admin_security_events(None)

    assert out["names_retrieved"] is True
    assert out["events"][0]["actor"] == {"id": "teacher-1", "name": None}


def test_a_name_that_was_read_is_used(monkeypatch):
    monkeypatch.setattr(main, "_require_admin", lambda _r: {"id": "admin-1"})
    monkeypatch.setattr(main, "supabase", _ReadClient(
        _one_event(), profiles=[{"id": "teacher-1", "display_name": "Mr Vance"}]))

    out = main.admin_security_events(None)
    assert out["events"][0]["actor"]["name"] == "Mr Vance"
    # The subject was in the same query and has no row, so it stays unnamed
    # while its neighbour is named -- the two states side by side.
    assert out["events"][0]["subject"]["name"] is None


def test_a_failed_name_lookup_says_so_rather_than_losing_the_events(monkeypatch):
    """Its own flag, not `retrieved`. The events are in hand and are the point
    of the page; what could not be read is the names, and "we could not look
    this account up" must not render as "this account has no profile"."""
    monkeypatch.setattr(main, "_require_admin", lambda _r: {"id": "admin-1"})
    monkeypatch.setattr(main, "supabase", _ReadClient(_one_event(), names_fail=True))

    out = main.admin_security_events(None)

    assert out["retrieved"] is True and len(out["events"]) == 1
    assert out["names_retrieved"] is False
    assert out["events"][0]["actor"]["name"] is None


def test_both_branches_carry_the_flag(monkeypatch):
    """Or a consumer has to treat an absent field as a third state."""
    monkeypatch.setattr(main, "_require_admin", lambda _r: {"id": "admin-1"})

    class _Broken:
        def table(self, _n):
            raise RuntimeError("down")

    monkeypatch.setattr(main, "supabase", _Broken())
    assert main.admin_security_events(None)["names_retrieved"] is False


# ─── the cooldown separates the limiters ─────────────────────────────────

def test_one_limiter_firing_does_not_silence_the_others(recorder, monkeypatch):
    """The cooldown key has to carry what makes two events different.

    All three limiters write the same kind, so keying on `(kind, actor)` alone
    let the first one to fire hide the other two for the whole window -- and
    ingest, posting at ~1 Hz per student, is always the one that gets there
    first. A student refused by ingest would then hit the strategies limiter
    with nothing recorded at all.
    """
    for budget in (main._INGEST_LIMITER, main._STRATEGY_LIMITER,
                   main._CHART_SUMMARY_LIMITER):
        tighten(monkeypatch, budget, limit=1, window=60)
    monkeypatch.setattr(main, "_SECURITY_EVENT_COOLDOWN_SEC", 300)

    for limit in (main._rate_limit_ingest, main._rate_limit_strategies,
                  main._rate_limit_chart_summary):
        limit("caller-1")
        with pytest.raises(main.HTTPException):
            limit("caller-1")
        with pytest.raises(main.HTTPException):
            limit("caller-1")          # still inside the cooldown for this one

    assert [r["detail"]["limiter"] for r in recorder.rows] == [
        "ingest", "strategies", "chart_summary"]


# ─── every refusal is either recorded or classified ──────────────────────

def _functions_raising_403() -> set[str]:
    """The functions in `main.py` that answer 403, by name.

    The AST rather than a grep: a 403 inside a nested helper belongs to the
    function a reader would name, and a string search cannot tell a raise from
    the same digits in a comment or a message.
    """
    tree = ast.parse(pathlib.Path(main.__file__).read_text(encoding="utf-8"))
    names = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                    and getattr(node.exc.func, "id", None) == "HTTPException"
                    and node.exc.args
                    and isinstance(node.exc.args[0], ast.Constant)
                    and node.exc.args[0].value == 403):
                names.add(fn.name)
    return names


def _denial_kinds_recorded_in(name: str) -> set[str]:
    tree = ast.parse(pathlib.Path(main.__file__).read_text(encoding="utf-8"))
    kinds = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name != name:
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) == "_record_security_event"
                    and node.args and isinstance(node.args[0], ast.Constant)):
                kinds.add(node.args[0].value)
    return kinds


# Refuses someone reach for another person's data, or for a control over it.
# The value is what the refusal is about, so the classification can be
# re-checked against the code rather than taken on trust.
RECORDS_THE_DENIAL = {
    "_verify_class_owner":         "another teacher's class",
    "_verify_can_view_student":    "another student's report",
    "_session_or_403":             "another student's session",
    "_practice_session_or_403":    "another student's practice session",
    "student_learning_strategies": "a practice session belonging to a second student",
    "_consent_actor":              "a child's consent, by someone who is neither",
    "update_consent":              "undoing a parent's decision, as the student",
    "erase_consent_channel":       "an irreversible erasure, by a non-parent",
    "_require_admin":              "the admin console",
}

# Refuses something that is not access to anyone's data. Each of these would
# be a row that means nothing, and a log whose rows mean nothing is one nobody
# reads by the time a real one lands -- the argument `npm audit`'s threshold
# makes, on a surface where it matters more.
NOT_AN_ACCESS_DENIAL = {
    "create_class":        "a role gate on the caller's own action",
    "link_child":          "a role gate on the caller's own action",
    "_reserve_and_call":   "a headband is in use by someone else -- contention, not authorization",
    "eeg_muse_disconnect": "the same device contention",
    "eeg_start":           "consent or the school year, which is a configuration state; "
                           "filing it as an incident is what `signals_missing` must not do either",
}


def test_every_403_is_either_recorded_or_classified():
    """Without this the *next* refusal joins the silent ones by default.

    A classification list rather than "every 403 must record": several of these
    are genuinely not access decisions, and forcing a row for them would fill
    the log with events nobody can act on. What the list removes is the option
    of not deciding.
    """
    unclassified = _functions_raising_403() - set(RECORDS_THE_DENIAL) - set(NOT_AN_ACCESS_DENIAL)
    assert unclassified == set()


def test_the_list_has_not_outlived_its_subjects():
    """A stale entry is how an exemption granted for one reason is inherited by
    whatever takes the function's place."""
    raising = _functions_raising_403()
    declared = set(RECORDS_THE_DENIAL) | set(NOT_AN_ACCESS_DENIAL)
    assert declared - raising == set()


def test_the_list_is_read_against_a_module_that_was_actually_parsed():
    """Or every check above passes against an empty set."""
    assert len(_functions_raising_403()) > 10


@pytest.mark.parametrize("name", sorted(RECORDS_THE_DENIAL))
def test_a_function_classified_as_recording_actually_records(name):
    """Classification is not the fix; the call is. Asserted per function so a
    failure names the one that stopped."""
    assert _denial_kinds_recorded_in(name) & {"authz_denied", "admin_denied"}, name


@pytest.mark.parametrize("name", sorted(NOT_AN_ACCESS_DENIAL))
def test_a_function_classified_as_not_a_denial_records_none(name):
    """The other direction: a function that started recording after being
    exempted has had its decision changed without the list moving."""
    assert not _denial_kinds_recorded_in(name) & {"authz_denied", "admin_denied"}, name


# ── which limiters record, and which deliberately do not ─────────────────
#
# `test_each_limiter_records_which_one_fired` lists three limiters by hand, so a
# fourth or a sixth is *silent by default* rather than classified -- the failure
# mode `close_sites()` and `test_every_recording_site_gates_on_the_window` exist
# to remove. These two tests take the decision away from whoever adds the next
# limiter: it either records, or it appears below with a reason.

# Limiter name -> why a refusal from it writes no row.
SILENT_LIMITERS = {}

# `_claim_generation_slot` call site (enclosing function) -> why it records
# nothing. The limiter itself *does* record, at the one site with a real actor,
# so this partition is per site rather than per limiter.
GENERATION_SILENT_SITES = {
    "generate_question":
        "`user_id` is a query parameter the caller writes, so an actor from it "
        "is an invented id in an append-only log. This route's recorded "
        "refusals come from the address budget instead, with no actor.",
    "_prefetch_worker":
        "No refusal reaches anybody -- a skipped refill leaves the queue short "
        "and the next question is generated inline, so there is no denial to "
        "audit.",
}


def _limiter_instances():
    """Module-level names bound to a `_SlidingWindowLimiter(...)` call."""
    tree = ast.parse(pathlib.Path(main.__file__).read_text(encoding="utf-8"))
    found = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        call = node.value
        # `_PUBLIC_BUDGETS` is a comprehension over `_PUBLIC_RATE_LIMITS`; its
        # members are covered by the middleware, asserted separately below.
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == "_SlidingWindowLimiter"
                and isinstance(node.targets[0], ast.Name)):
            found[node.targets[0].id] = getattr(main, node.targets[0].id).name
    return found


def _recorded_limiter_attrs():
    """Every `X` in a `_record_security_event(..., limiter=X.name)` call."""
    tree = ast.parse(pathlib.Path(main.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_record_security_event"):
            continue
        for kw in node.keywords:
            if kw.arg == "limiter" and isinstance(kw.value, ast.Attribute) \
                    and kw.value.attr == "name" \
                    and isinstance(kw.value.value, ast.Name):
                names.add(kw.value.value.id)
    return names


def test_every_limiter_either_records_or_is_classified_as_silent():
    """A new limiter has to say which it is, rather than defaulting to silent.

    This is what the generation limiter failed: four of the five recorded, it
    did not, and nothing in the suite said whether that was a decision.
    """
    instances = _limiter_instances()
    recording = _recorded_limiter_attrs()
    unclassified = {
        attr: label for attr, label in instances.items()
        if attr not in recording and label not in SILENT_LIMITERS
    }
    assert not unclassified, (
        "These limiters refuse callers and write no security_events row. Record "
        "with `limiter=<instance>.name`, or add the limiter's name to "
        "SILENT_LIMITERS with the reason:\n"
        + "\n".join(f"  {a} (name={n!r})" for a, n in sorted(unclassified.items())))


def test_the_public_budgets_record_through_the_middleware():
    """`_PUBLIC_BUDGETS` is excluded from the scan above, so its coverage is
    asserted rather than assumed: the middleware records every refusal it
    returns, keyed on the name that *is* the dict key."""
    src = pathlib.Path(main.__file__).read_text(encoding="utf-8")
    middleware = src.split("async def public_rate_limit")[1].split("\ndef ")[0]
    assert "_record_security_event" in middleware
    assert "limiter=limiter" in middleware
    # And with no actor, since an address is not one.
    assert '"rate_limited", None' in middleware


def test_every_generation_slot_site_records_or_says_why_not():
    """Per site, because this limiter's three callers differ in whether there
    is an actor to name and whether a refusal reaches anyone at all."""
    tree = ast.parse(pathlib.Path(main.__file__).read_text(encoding="utf-8"))

    def calls(fn, name):
        """Call *nodes*, not a substring: matched as text, `_claim_generation_
        slot`'s own `def` line contains its own name and it reads as its own
        caller. Same use-versus-mention trap the CSP test documents."""
        return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == name for n in ast.walk(fn))

    sites = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name == "_claim_generation_slot":
            continue          # the helper itself, not one of its callers
        if calls(fn, "_claim_generation_slot"):
            sites[fn.name] = calls(fn, "_record_security_event")

    assert sites, "no _claim_generation_slot call sites found -- has it moved?"
    unclassified = [name for name, records in sites.items()
                    if not records and name not in GENERATION_SILENT_SITES]
    assert not unclassified, (
        "These sites refuse a generation and record nothing. Record with "
        "`limiter=_GENERATION_LIMITER.name`, or add the function to "
        f"GENERATION_SILENT_SITES with the reason: {sorted(unclassified)}")
    # The other direction: a site that started recording after being exempted
    # has had its decision changed without the list moving.
    for name in GENERATION_SILENT_SITES:
        assert name in sites, f"{name} no longer claims a generation slot"
