"""The security log: what gets recorded, what must never be, and who may read it."""

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
    # Module-level cooldown state outlives a test. (The limiters are conftest's.)
    main._security_event_seen.clear()
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
    # "Who tried to read this child's record" needs both ids.
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
    """Not folded into `authz_denied`, where it would hide among ordinary refusals."""
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
    """A limiter fires per request, so uncooled it adds a write to every refusal."""
    tighten(monkeypatch, main._STRATEGY_LIMITER, limit=1, window=60)
    monkeypatch.setattr(main, "_SECURITY_EVENT_COOLDOWN_SEC", 300)

    main._rate_limit_strategies("caller-1")
    for _ in range(20):
        with pytest.raises(main.HTTPException):
            main._rate_limit_strategies("caller-1")

    assert len(recorder.rows) == 1, "one row per cooldown, not one per refusal"


def test_the_cooldown_is_per_caller(recorder, monkeypatch):
    """Limit 1, not 0: the setting's floor is 1, so 0 is a state it cannot hold."""
    tighten(monkeypatch, main._STRATEGY_LIMITER, limit=1, window=60)

    for caller in ("a", "b", "c"):
        main._rate_limit_strategies(caller)
        with pytest.raises(main.HTTPException):
            main._rate_limit_strategies(caller)

    assert len(recorder.rows) == 3


def test_a_denial_is_recorded_every_time(recorder, monkeypatch):
    """Deduplicating denials would hide a caller probing a series of students."""
    monkeypatch.setattr(main, "_can_view_student", lambda *_a: False)

    for sid in ("s1", "s2", "s1"):
        with pytest.raises(main.HTTPException):
            main._verify_can_view_student(TEACHER, sid)

    assert len(recorder.rows) == 3


# ─── what must never be recorded ─────────────────────────────────────────

def test_the_row_carries_no_reading_no_body_and_no_address():
    """Over the call sites, since `detail` is whatever each hook passes."""
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
    """The write is swallowed; the log line, naming the kind, is the only trace."""
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
    """Otherwise the constraint violation is swallowed and the row silently missing."""
    sql = MIGRATION.read_text(encoding="utf-8")
    block = re.search(r"CHECK \(\"kind\" IN \((.*?)\)\)", sql, re.S)
    assert block, "the kind CHECK is gone from the migration"
    in_sql = set(re.findall(r"'([a-z_]+)'", block.group(1)))

    assert set(main._SECURITY_EVENT_KINDS) == in_sql


def test_every_kind_the_table_accepts_is_actually_written_somewhere():
    """A kind nothing produces is a filter that can only return nothing."""
    source = pathlib.Path(main.__file__).read_text(encoding="utf-8")
    for kind in main._SECURITY_EVENT_KINDS:
        # Whitespace-tolerant: one call site wraps its arguments.
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
    """`_profiles_many`'s placeholder is named "Student", which is wrong on an audit page."""
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
    # The subject has no row, so it stays unnamed beside a named actor.
    assert out["events"][0]["subject"]["name"] is None


def test_a_failed_name_lookup_says_so_rather_than_losing_the_events(monkeypatch):
    """Its own flag, not `retrieved`: the events are in hand, only the names are not."""
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
    """The cooldown key carries the limiter, or ingest (~1 Hz) masks the others."""
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
    """The functions in `main.py` that raise a 403, by AST (a grep can't tell a raise from a mention)."""
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


# Refuses a reach for another person's data or a control over it; value = what is refused.
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
    # Per function: the refused code records; its role-gate 403 does not.
    "link_child":                  "a link code that was wrong or expired",
}

# Refuses something that is not access to anyone's data; a row here would mean nothing.
NOT_AN_ACCESS_DENIAL = {
    "create_class":        "a role gate on the caller's own action",
    "create_parent_link_code":
                           "a role gate on the caller's own account -- a "
                           "teacher asking for a code meant for a student is "
                           "a wrong page, not an attempt on anyone's data",
    "_reserve_and_call":   "a headband is in use by someone else -- contention, not authorization",
    "eeg_muse_disconnect": "the same device contention",
    "eeg_start":           "consent or the school year, which is a configuration state; "
                           "filing it as an incident is what `signals_missing` must not do either",
}


def test_every_403_is_either_recorded_or_classified():
    """Without this the next refusal is silent by default."""
    unclassified = _functions_raising_403() - set(RECORDS_THE_DENIAL) - set(NOT_AN_ACCESS_DENIAL)
    assert unclassified == set()


def test_the_list_has_not_outlived_its_subjects():
    """A stale entry's exemption would be inherited by whatever takes the name."""
    raising = _functions_raising_403()
    declared = set(RECORDS_THE_DENIAL) | set(NOT_AN_ACCESS_DENIAL)
    assert declared - raising == set()


def test_the_list_is_read_against_a_module_that_was_actually_parsed():
    """Or every check above passes against an empty set."""
    assert len(_functions_raising_403()) > 10


@pytest.mark.parametrize("name", sorted(RECORDS_THE_DENIAL))
def test_a_function_classified_as_recording_actually_records(name):
    assert _denial_kinds_recorded_in(name) & {"authz_denied", "admin_denied"}, name


@pytest.mark.parametrize("name", sorted(NOT_AN_ACCESS_DENIAL))
def test_a_function_classified_as_not_a_denial_records_none(name):
    assert not _denial_kinds_recorded_in(name) & {"authz_denied", "admin_denied"}, name


# ── which limiters record, and which deliberately do not ─────────────────
# A new limiter either records or appears below with a reason.

# Limiter name -> why a refusal from it writes no row.
SILENT_LIMITERS = {}

# `_claim_generation_slot` call site -> why it records nothing (per site, not per limiter).
GENERATION_SILENT_SITES = {
    "_prefetch_worker":
        "No refusal reaches anybody -- a skipped refill leaves the queue short "
        "and the next question is generated inline, so there is no denial to "
        "audit.",
}


# Named floor, not a count, so a rename fails too.
EXPECTED_LIMITERS = {
    "strategies", "chart_summary", "ingest", "generation",
    "public_read", "public_probe",
    "parent_link_code",
}


def _limiter_instances():
    """Every `_SlidingWindowLimiter` the module holds, found at runtime (an AST scan misses registries).

    Recurses one level into dicts, as `_PUBLIC_BUDGETS` is.
    """
    cls = type(main._STRATEGY_LIMITER)
    found = {}
    for name, value in vars(main).items():
        if isinstance(value, cls):
            found[name] = value
        elif isinstance(value, dict):
            for key, member in value.items():
                if isinstance(member, cls):
                    found[f"{name}[{key!r}]"] = member
    return found


def _recorded_limiter_labels():
    """The limiter name each `_record_security_event(..., limiter=…)` records, evaluated against the module.

    An unresolvable local (the middleware's `limiter=limiter`) yields nothing.
    """
    tree = ast.parse(pathlib.Path(main.__file__).read_text(encoding="utf-8"))
    labels = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_record_security_event"):
            continue
        for kw in node.keywords:
            if kw.arg != "limiter":
                continue
            try:
                labels.add(eval(compile(ast.Expression(kw.value), "<label>", "eval"),
                                vars(main)))
            except Exception:
                pass          # a local, like the middleware's `limiter`
    return labels


def test_every_limiter_either_records_or_is_classified_as_silent():
    instances = _limiter_instances()
    found = {limiter.name for limiter in instances.values()}

    # The floor: a scan that sees nothing would otherwise pass.
    missing = EXPECTED_LIMITERS - found
    assert not missing, (
        f"the scan no longer sees {sorted(missing)} -- it found {sorted(found)}. "
        "Fix the scan before trusting the partition below it.")

    # Names must be distinct: sets collapse duplicates, and the name is the cooldown key.
    # Keyed on `id(limiter)`, since the scan finds one object by every path to it.
    by_name = {}
    for attr, limiter in sorted(instances.items()):
        by_name.setdefault(limiter.name, {}).setdefault(id(limiter), attr)
    collisions = {name: sorted(paths.values())
                  for name, paths in by_name.items() if len(paths) > 1}
    assert not collisions, (
        "two limiters answer to one name, so they share a cooldown bucket and "
        f"this partition cannot tell them apart: {collisions}")

    recording = _recorded_limiter_labels()
    # The public budgets record via the middleware's local `limiter`; covered behaviourally
    # by test_network_edge.py, whose test name is checked here so the exclusion can't go stale.
    cited = (pathlib.Path(__file__).parent / "test_network_edge.py").read_text(
        encoding="utf-8")
    # Anchored on the paren, so a rename that appends still fails.
    assert "def test_the_refusal_is_recorded_without_saying_who(" in cited, (
        "the test this exclusion rests on is gone or renamed -- the public "
        "budgets are now unchecked by anything")
    recording |= set(main._PUBLIC_BUDGETS)

    unclassified = sorted(
        name for name in found
        if name not in recording and name not in SILENT_LIMITERS)
    assert not unclassified, (
        "These limiters refuse callers and write no security_events row. Record "
        "with `limiter=<instance>.name`, or add the limiter's name to "
        f"SILENT_LIMITERS with the reason: {unclassified}")


def test_every_generation_slot_site_records_or_says_why_not():
    """Per site: the callers differ in whether there is an actor and whether a refusal reaches anyone."""
    tree = ast.parse(pathlib.Path(main.__file__).read_text(encoding="utf-8"))

    def calls(fn, name):
        """Call nodes, not a substring, which would match the helper's own `def`."""
        return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == name for n in ast.walk(fn))

    def records_a_generation_refusal(fn):
        """A record call carrying this limiter's label, not just any record call."""
        for n in ast.walk(fn):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "_record_security_event"):
                continue
            for kw in n.keywords:
                if kw.arg == "limiter" and \
                        ast.unparse(kw.value) == "_GENERATION_LIMITER.name":
                    return True
        return False

    sites = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fn.name == "_claim_generation_slot":
            continue          # the helper itself, not one of its callers
        if calls(fn, "_claim_generation_slot"):
            sites[fn.name] = records_a_generation_refusal(fn)

    assert sites, "no _claim_generation_slot call sites found -- has it moved?"
    unclassified = [name for name, records in sites.items()
                    if not records and name not in GENERATION_SILENT_SITES]
    assert not unclassified, (
        "These sites refuse a generation and record nothing. Record with "
        "`limiter=_GENERATION_LIMITER.name`, or add the function to "
        f"GENERATION_SILENT_SITES with the reason: {sorted(unclassified)}")
    # No stale exemptions.
    for name in GENERATION_SILENT_SITES:
        assert name in sites, f"{name} no longer claims a generation slot"
