"""Every caller-supplied number is classified and bounded; no payload carries an unrendered id."""
import ast
import inspect
import pathlib
import types
import typing
from typing import Annotated, Optional

from annotated_types import Interval
from pydantic import BaseModel, Field, conint

import pytest

import main
from tests.test_access_control import _FakeSupabase, _ts


# (handler, param) -> (how it is bounded, why). Named, since bound vs stored value isn't in the type.
#   "handler" `max(floor, min(it, ceiling))` or `_clamp_days`; "field" `ge=`/`le=` on the model;
#   "raises" 422 outside the range, cited to its test; "value" stored or applied, not a bound.
CALLER_NUMBERS = {
    ("get_questions", "limit"):
        ("handler", "Clamped to _QUESTIONS_MAX, which is the largest any "
                    "surface asks for. The only row-returning route with no "
                    "caller to resolve, and its answer is cached."),
    ("student_questions", "limit"):
        ("handler", "_STUDENT_QUESTIONS_MAX, and the payload says `truncated`."),
    ("leaderboard", "limit"):
        ("handler", "_LEADERBOARD_MAX. The precedent this file extends."),
    ("generate_question", "bias"):
        ("handler", "max(-1, min(1, ...)) -- a difficulty shift, not a row "
                    "count, but unbounded it would reach _shift_difficulty."),
    ("student_signal_trend", "weeks"):
        ("handler", "_TREND_MAX_WEEKS, with a floor of 2."),
    ("student_weekly_report", "days"):
        ("handler", "30. Seven days at 1 Hz is already half a million rows."),
    ("student_signal_summary", "days"):
        ("handler", "30, as the weekly report."),
    ("student_focus_accuracy", "days"):
        ("handler", "_clamp_days."),
    ("class_cohort_signals", "days"):
        ("handler", "_clamp_days."),
    ("class_accuracy_trend", "days"):
        ("handler", "_clamp_days."),
    ("class_time_of_day", "days"):
        ("handler", "_clamp_days."),
    ("class_alerts", "days"):
        ("handler", "_clamp_days."),
    ("admin_flag_history", "limit"):
        ("handler", "100."),
    ("admin_security_events", "limit"):
        ("handler", "_SECURITY_EVENTS_MAX."),
    ("admin_student_search", "limit"):
        ("handler", "25, over a term already refused below two characters."),
    ("list_sessions", "limit"):
        ("handler", "_SESSION_LIST_MAX. Rows for display only; every count the "
                    "pages show comes from `total` or /api/stats/me."),

    ("student_learning_strategies", "days"):
        ("handler", "max(1, min(payload.days, 30)) -- clamped in the handler "
                    "rather than with ge/le on the field, which would turn the "
                    "same input into a 422. See the note in CLAUDE.md."),
    ("student_chart_summary", "days"):
        ("handler", "As the strategies endpoint."),
    ("student_chart_summary", "weeks"):
        ("handler", "_TREND_MAX_WEEKS, floor 2."),

    ("update_my_profile", "difficulty_bias"):
        ("field", "Field(ge=-1, le=1). A stored preference, not a query bound, "
                  "so a 422 is the right answer and the CHECK constraint "
                  "behind it is the second line."),
    ("update_my_profile", "session_duration_minutes"):
        ("field", "Field(ge=5, le=180)."),

    ("admin_set_flag", "bypass_minutes"):
        ("raises", "def test_the_bypass_duration_is_bounded("),

    ("record_answer", "selected_index"):
        ("value", "Which option was chosen. It indexes the question's own "
                  "options and bounds no read; out of range it stores a "
                  "nonsense answer for one row, which is the client lying "
                  "about its own student."),
    ("record_practice_answer", "selected_index"):
        ("value", "As record_answer."),
}

# Floor for the scan going quiet; the spelling tests at the end cover it going blind.
EXPECTED_NUMBERS = {
    ("get_questions", "limit"),
    ("generate_question", "bias"),
    ("student_chart_summary", "weeks"),
    ("leaderboard", "limit"),
}


# The only wrappers the scan looks through: Union (`int | None`, `Optional`) and Annotated.
# Containers are deliberately not unwrapped: ingest samples are readings, bounded by INGEST_MAX_BATCH.
_WRAPPERS = (typing.Union, types.UnionType, typing.Annotated)


def _mentions_a_number(ann) -> bool:
    """Whether a caller can put an int or float here; one predicate for params and fields.

    Compares identity, so `bool` is not an int.
    """
    if ann is int or ann is float:
        return True
    if typing.get_origin(ann) in _WRAPPERS:
        return any(_mentions_a_number(arg) for arg in typing.get_args(ann))
    return False


def _models_in(ann) -> list:
    """The request models an annotation carries, through the same wrappers."""
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return [ann]
    if typing.get_origin(ann) in _WRAPPERS:
        return [m for arg in typing.get_args(ann) for m in _models_in(arg)]
    return []


def _model_numbers(model, prefix="", seen=None):
    """Dotted path of every number in a model, through nested models; `seen` stops self-reference."""
    seen = set() if seen is None else seen
    if model in seen:
        return []
    seen = seen | {model}
    out = []
    for name, field in model.model_fields.items():
        if _mentions_a_number(field.annotation):
            out.append(prefix + name)
        for inner in _models_in(field.annotation):
            out.extend(_model_numbers(inner, f"{prefix}{name}.", seen))
    return out


def _caller_numbers():
    """Every int/float a caller can send outside a container, from `app.routes`, not the AST."""
    found = {}
    for route in main.app.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        try:
            sig = inspect.signature(endpoint)
        except (TypeError, ValueError):       # pragma: no cover - builtins
            continue
        for param in sig.parameters.values():
            ann = param.annotation
            if _mentions_a_number(ann):
                found[(endpoint.__name__, param.name)] = getattr(route, "path", "?")
            for model in _models_in(ann):
                for path in _model_numbers(model):
                    found[(endpoint.__name__, path)] = getattr(route, "path", "?")
    return found


def test_every_caller_supplied_number_is_bounded_or_says_why_not():
    found = _caller_numbers()

    missing = EXPECTED_NUMBERS - set(found)
    assert not missing, (
        f"the scan no longer sees {sorted(missing)} -- it found "
        f"{len(found)} parameters. Fix the scan before trusting the partition.")

    unclassified = sorted(pair for pair in found if pair not in CALLER_NUMBERS)
    assert not unclassified, (
        "A caller can put these numbers in a request and nothing here says "
        "whether they bound anything. Clamp in the handler and add them with "
        "\"handler\", or classify them: " + str(unclassified))

    # The other direction, or stale entries accumulate.
    stale = sorted(pair for pair in CALLER_NUMBERS if pair not in found)
    assert not stale, (
        f"these parameters are gone; drop them from CALLER_NUMBERS: {stale}")


def _handler(name):
    """One handler's AST, from its own source (re-parsing main.py per case is slow)."""
    fn = getattr(main, name)
    return ast.parse(inspect.getsource(fn)).body[0]


def _names_param(node, param) -> bool:
    """Whether an expression reads `param` or `payload.param`; nodes, not text (`school_days`)."""
    return any(
        (isinstance(n, ast.Name) and n.id == param)
        or (isinstance(n, ast.Attribute) and n.attr == param)
        for n in ast.walk(node))


def _is_call(node, name) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == name)


def _clamps(tree, param) -> bool:
    """`max(floor, min(param, ceiling))`, either way round, or `_clamp_days`.

    The nesting is the check; the inner call need only read the param (`int(bias or 0)`).
    """
    return bool(_clamp_nodes(tree, param))


def _clamp_nodes(tree, param) -> list:
    """The outermost call of every clamp of `param` in `tree`."""
    found = []
    for node in ast.walk(tree):
        if _is_call(node, "_clamp_days") and any(_names_param(a, param) for a in node.args):
            found.append(node)
            continue
        for outer, inner in (("max", "min"), ("min", "max")):
            if _is_call(node, outer) and any(
                    _is_call(arg, inner) and any(_names_param(a, param) for a in arg.args)
                    for arg in node.args):
                found.append(node)
                break
    return found


def _raw_uses(tree, param) -> list:
    """Reads of `param` that are not the clamped value, as line numbers.

    Every read must be inside a clamp or follow an unconditional top-level reassignment to one;
    `payload.days` counts as a read, `query.limit(...)` does not.
    """
    inside = {id(n) for clamp in _clamp_nodes(tree, param) for n in ast.walk(clamp)}
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))), tree)
    reassigned = min(
        (node.lineno for node in fn.body
         if isinstance(node, ast.Assign) and len(node.targets) == 1
         and isinstance(node.targets[0], ast.Name) and node.targets[0].id == param
         and _clamp_nodes(node.value, param)),
        default=None)
    method_names = {id(n.func) for n in ast.walk(tree)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    raw = []
    for node in ast.walk(tree):
        if id(node) in inside:
            continue
        if isinstance(node, ast.Name) and node.id == param and isinstance(node.ctx, ast.Load):
            if reassigned is None or node.lineno <= reassigned:
                raw.append(node.lineno)
        elif (isinstance(node, ast.Attribute) and node.attr == param
              and isinstance(node.ctx, ast.Load) and isinstance(node.value, ast.Name)
              and id(node) not in method_names):
            raw.append(node.lineno)
    return sorted(raw)


@pytest.mark.parametrize("source,param,raw", [
    ("def f(limit):\n    limit = max(1, min(limit, 30))\n    q.limit(limit)",         "limit", False),
    ("def f(weeks):\n    g(max(2, min(weeks, 8)))",                                "weeks", False),
    ("def f(days):\n    w = _clamp_days(days)\n    g(w)",                             "days",  False),
    ("def f(payload):\n    days = max(1, min(payload.days, 30))\n    g(days)",        "days",  False),
    # The clamp is there and is not what is used.
    ("def f(limit):\n    kept = max(1, min(limit, 30))\n    q.limit(limit)",          "limit", True),
    ("def f(payload):\n    days = max(1, min(payload.days, 30))\n    g(payload.days)", "days", True),
    # Used before it is clamped.
    ("def f(limit):\n    q.limit(limit)\n    limit = max(1, min(limit, 30))",         "limit", True),
    # Clamped on one path only: the other reaches the read raw.
    ("def f(limit, x):\n    if x:\n        limit = max(1, min(limit, 30))\n    q.limit(limit)",
     "limit", True),
    ("def f(limit):\n    try:\n        limit = max(1, min(limit, 30))\n    except E:\n        pass\n"
     "    q.limit(limit)", "limit", True),
])
def test_a_clamp_is_the_value_that_gets_used(source, param, raw):
    assert bool(_raw_uses(ast.parse(source), param)) is raw, source


@pytest.mark.parametrize("source,param,clamped", [
    ("max(1, min(days, 30))",                      "days", True),
    ("min(max(days, 1), 30)",                      "days", True),
    ("max(1, min(payload.days, 30))",              "days", True),
    ("max(-1, min(1, int(bias or 0)))",            "bias", True),
    ("_clamp_days(days)",                          "days", True),
    ("max(0, len(school_days))",                   "days", False),  # a longer name
    ("_clamp_days(school_days)",                   "days", False),
    ("max(1, days)",                               "days", False),  # no ceiling
    ("min(days, 30)",                              "days", False),  # no floor
    ("max(0, page * size - days)\nmin(days, 30)",  "days", False),  # two calls, no clamp
])
def test_a_clamp_is_one_bound_wrapping_the_other(source, param, clamped):
    assert _clamps(ast.parse(source), param) is clamped, source


@pytest.mark.parametrize("pair", sorted(
    p for p, (how, _why) in CALLER_NUMBERS.items() if how == "handler"))
def test_a_bound_claimed_clamped_in_the_handler_has_a_ceiling_and_a_floor(pair):
    """Both ends: no ceiling lets a caller choose the volume; no floor makes `LIMIT -5` a 500.

    Checks the spelling convention, not the arithmetic; values are tested at the query below.
    """
    handler_name, param = pair
    tree = _handler(handler_name)
    assert _clamps(tree, param), (
        f"CALLER_NUMBERS says {handler_name} clamps `{param}` in the handler, and "
        f"no `max(floor, min({param}, ceiling))` or `_clamp_days({param})` is in "
        "it. Clamp it the way the others do, or -- if it is bounded some other "
        "correct way -- move the entry to the mechanism that describes it. This "
        "checks the spelling, not the arithmetic.")
    # And the clamped value is the one read (line numbers relative to the handler's source).
    assert not _raw_uses(tree, param), (
        f"{handler_name} clamps `{param}` and then reads it unclamped at "
        f"line(s) {_raw_uses(tree, param)} of its source -- reassign the clamp to "
        "the name, or pass the clamp itself")


@pytest.mark.parametrize("pair", sorted(
    p for p, (how, _why) in CALLER_NUMBERS.items() if how == "field"))
def test_a_bound_claimed_on_the_field_carries_both_ends(pair):
    """Read off `model_fields`; out of range these hit a CHECK constraint as a 500, not a 422."""
    handler_name, field_name = pair
    endpoint = next(r.endpoint for r in main.app.routes
                    if getattr(getattr(r, "endpoint", None), "__name__", None)
                    == handler_name)
    fields = [model.model_fields[field_name]
              for p in inspect.signature(endpoint).parameters.values()
              for model in _models_in(p.annotation)
              if field_name in model.model_fields]
    assert fields, f"{handler_name} no longer has a `{field_name}` field"
    floor, ceiling = _field_bounds(fields[0])
    assert ceiling, f"{field_name} no longer carries an upper bound on its field"
    assert floor, f"{field_name} no longer carries a lower bound on its field"


def _field_bounds(field) -> tuple[bool, bool]:
    """(has a floor, has a ceiling), by value: `conint`/`Interval` carry `ge=None`, so not `hasattr`."""
    def present(*names):
        return any(getattr(m, n, None) is not None
                   for m in field.metadata for n in names)
    return present("ge", "gt"), present("le", "lt")


class _Bounds(BaseModel):
    both:        int | None = Field(None, ge=-1, le=1)
    ceiling:     int = Field(0, le=180)
    con_ceiling: conint(le=180) = 0
    interval:    Annotated[int, Interval(le=180)] = 0
    strict:      int = Field(0, gt=0, lt=10)
    con_both:    conint(ge=1, le=5) = 1
    neither:     int = 0


@pytest.mark.parametrize("name,floor,ceiling", [
    ("both",        True,  True),
    ("ceiling",     False, True),
    ("con_ceiling", False, True),    # carries ge=None
    ("interval",    False, True),    # carries ge=None
    ("strict",      True,  True),    # gt / lt
    ("con_both",    True,  True),
    ("neither",     False, False),
])
def test_a_field_bound_is_read_by_value_in_every_spelling(name, floor, ceiling):
    assert _field_bounds(_Bounds.model_fields[name]) == (floor, ceiling), name


def test_the_bound_enforced_by_raising_still_has_the_test_it_cites():
    """A raise has no single shape, so this checks the citation (anchored on the opening paren)."""
    _how, cited = CALLER_NUMBERS[("admin_set_flag", "bypass_minutes")]
    admin_tests = (pathlib.Path(__file__).parent / "test_admin.py").read_text(
        encoding="utf-8")
    assert cited in admin_tests, (
        f"{cited!r} is gone or renamed, so nothing checks the one bound that "
        "is enforced by raising")


# ── the payload half ─────────────────────────────────────────────────────
# Query first (rule 4), then payload with a positive assertion beside each negative one.

_SESSION_ROW = {
    "id": "sess-1", "user_id": "kid-1", "class_id": None,
    "title": "Practice Session", "started_at": _ts(5), "ended_at": _ts(4),
    "questions_answered": 3, "correct_answers": 2,
    "chart_paths": {"focus": "kid-1/sess-1/focus.svg"},
}


def _assert_sessions_read_names_its_columns(fake):
    """Every `sessions` read named columns (`_cols = None` is `select("*")`), none the path."""
    reads = [q for table, q in zip(fake.table_calls, fake.queries)
             if table == "sessions"]
    assert reads, "nothing read `sessions` at all"
    for query in reads:
        assert query._cols is not None, (
            "a `sessions` read asked for every column, so whatever the table "
            "gains next reaches the browser with nobody deciding")
        assert "chart_paths" not in query._cols, query._cols


def test_a_students_own_session_list_carries_no_object_paths(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "kid-1"})
    fake = _FakeSupabase({"sessions": [_SESSION_ROW]})
    monkeypatch.setattr(main, "supabase", fake)

    rows = main.list_sessions(None)["sessions"]

    _assert_sessions_read_names_its_columns(fake)
    assert rows and "chart_paths" not in rows[0], (
        "the archived charts' storage paths are being sent to the student's "
        "browser, which renders them nowhere")
    # And the read still returns what the page draws, or the fix is a blank list.
    assert rows[0]["questions_answered"] == 3
    assert rows[0]["title"] == "Practice Session"


def test_a_viewers_session_list_carries_no_object_paths(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "teacher-1"})
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a: None)
    fake = _FakeSupabase({"sessions": [_SESSION_ROW]})
    monkeypatch.setattr(main, "supabase", fake)

    rows = main.student_sessions("kid-1", None)

    _assert_sessions_read_names_its_columns(fake)
    assert rows and "chart_paths" not in rows[0]
    assert rows[0]["id"] == "sess-1"
    # The derived flag is still there -- this read feeds it.
    assert rows[0]["abandoned"] is False


def test_the_open_session_a_teacher_watches_carries_no_object_paths(monkeypatch):
    """`class_live` returns what this helper finds as `active_session`."""
    fake = _FakeSupabase({"sessions": [{**_SESSION_ROW, "ended_at": None}]})
    monkeypatch.setattr(main, "supabase", fake)

    by_student = main._open_sessions_many(["kid-1"])

    _assert_sessions_read_names_its_columns(fake)
    assert by_student["kid-1"], "the open session was not found at all"
    session = by_student["kid-1"][0]
    assert "chart_paths" not in session
    # `class_live` and the stats merge read these; dropping them would pass the line above.
    assert session["id"] == "sess-1"
    assert session["questions_answered"] == 3


def test_a_parents_child_sessions_carry_no_object_paths(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "parent-1"})
    monkeypatch.setattr(main, "_profile", lambda _c: {})
    monkeypatch.setattr(main, "_signal_summaries",
                        lambda ids, **_kw: {str(i): {} for i in ids})
    fake = _FakeSupabase({
        "parent_child_links": [{"parent_id": "parent-1", "child_id": "kid-1",
                                "created_at": _ts(9)}],
        "sessions": [_SESSION_ROW],
        "user_stats": [], "user_math_performance": [],
    })
    monkeypatch.setattr(main, "supabase", fake)

    children = main.my_children(None)

    _assert_sessions_read_names_its_columns(fake)
    assert children and children[0]["sessions"], "no sessions reached the parent"
    assert "chart_paths" not in children[0]["sessions"][0]
    assert children[0]["sessions"][0]["id"] == "sess-1"


# ── both ends of a limit, at the query ───────────────────────────────────
# Asserted on every limit the table was sent (rule 4), so a later `.limit(1)` can't stand in.

def _limits_sent_to(fake, table):
    return [q._limit for t, q in zip(fake.table_calls, fake.queries) if t == table]


_LIMITED_READS = {
    "student_questions": (
        lambda limit: main.student_questions("kid-1", None, limit=limit),
        "session_answers", lambda: main._STUDENT_QUESTIONS_MAX),
    "admin_flag_history": (
        lambda limit: main.admin_flag_history("strategy_llm_enabled", None,
                                              limit=limit),
        "feature_flag_changes", lambda: 100),
    "admin_student_search": (
        lambda limit: main.admin_student_search(None, q="ada", limit=limit),
        "profiles", lambda: 25),
    "list_sessions": (
        lambda limit: main.list_sessions(None, limit=limit),
        "sessions", lambda: main._SESSION_LIST_MAX),
}


@pytest.fixture
def _no_gates(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "viewer-1"})
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a: None)
    monkeypatch.setattr(main, "_require_admin", lambda *_a: None)


@pytest.mark.parametrize("route", sorted(_LIMITED_READS))
@pytest.mark.parametrize("asked", [-5, 0])
def test_a_negative_limit_reaches_the_query_as_the_floor(monkeypatch, _no_gates,
                                                         route, asked):
    """Without a floor, `LIMIT -5` is a 500 from the caller's own input."""
    call, table, _ceiling = _LIMITED_READS[route]
    fake = _FakeSupabase({})
    monkeypatch.setattr(main, "supabase", fake)

    call(asked)

    assert _limits_sent_to(fake, table) == [1], (
        f"{route} asked for {asked}; the {table} reads received "
        f"{_limits_sent_to(fake, table)!r}")


@pytest.mark.parametrize("route", sorted(_LIMITED_READS))
def test_an_enormous_limit_reaches_the_query_as_the_ceiling(monkeypatch, _no_gates,
                                                           route):
    call, table, ceiling = _LIMITED_READS[route]
    fake = _FakeSupabase({})
    monkeypatch.setattr(main, "supabase", fake)

    call(10_000_000)

    assert _limits_sent_to(fake, table) == [ceiling()], (
        f"{route}: the {table} reads received {_limits_sent_to(fake, table)!r}")


# ── what the scan can see ────────────────────────────────────────────────
# Spellings the real app doesn't use, so only a predicate table and a probe app can cover them.

@pytest.mark.parametrize("annotation,seen", [
    (int,                            True),
    (float,                          True),
    (Optional[int],                  True),
    (int | None,                     True),
    (Annotated[int, "query"],        True),   # Annotated[int, Query(le=500)]
    (Annotated[int | None, "query"], True),   # both wrappers at once
    (Annotated[Optional[float], "q"], True),
    (str,                            False),
    (str | None,                     False),
    (bool,                           False),  # include_face, not a bound
    (Optional[bool],                 False),
    (dict[str, int],                 False),  # containers are not unwrapped
    (list[int],                      False),
])
def test_the_predicate_recognises_a_number_however_it_is_written(annotation, seen):
    assert _mentions_a_number(annotation) is seen, annotation


def test_the_scan_finds_every_spelling_of_a_caller_number(monkeypatch):
    """`_caller_numbers` itself against a probe app, not a copy of its loop.

    A model inside a list is deliberately not found; FastAPI's own routes add no numbers.
    """
    from fastapi import Body, FastAPI, Query

    class Window(BaseModel):
        days: int

    class Direct(BaseModel):
        weeks: int

    class Nested(BaseModel):
        window: Window | None = None

    class Batch(BaseModel):
        samples: list[Window] = []

    probe = FastAPI()

    @probe.get("/optional")
    def optional_int(limit: int | None = None):                   # noqa: ARG001
        return {}

    @probe.get("/annotated")
    def annotated_int(limit: Annotated[int, Query(le=500)] = 10):  # noqa: ARG001
        return {}

    @probe.post("/optional-body")
    def optional_body(payload: Direct | None = None):             # noqa: ARG001
        return {}

    @probe.post("/annotated-body")
    def annotated_body(payload: Annotated[Direct, Body()]):       # noqa: ARG001
        return {}

    @probe.post("/nested")
    def nested_body(payload: Nested):                              # noqa: ARG001
        return {}

    @probe.post("/listed")
    def listed_body(payload: Batch):                               # noqa: ARG001
        return {}

    monkeypatch.setattr(main, "app", probe)

    assert set(_caller_numbers()) == {
        ("optional_int", "limit"),
        ("annotated_int", "limit"),
        ("optional_body", "weeks"),
        ("annotated_body", "weeks"),
        ("nested_body", "window.days"),
    }


# ── the cap on a student's own session list ──────────────────────────────
# A silent cap would render as a student who did less work, so the payload carries `total`.


def _sessions(n, *, ended=True):
    return [{**_SESSION_ROW, "id": f"sess-{i}", "started_at": _ts(n - i),
             "ended_at": _ts(n - i) if ended else None} for i in range(n)]


def test_the_session_list_is_capped_and_asks_for_the_real_total(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "kid-1"})
    fake = _FakeSupabase({"sessions": _sessions(3)})
    monkeypatch.setattr(main, "supabase", fake)

    main.list_sessions(None)

    query = fake.queries[0]
    assert query._limit == main._SESSION_LIST_MAX, (
        f"the session read is bounded at {query._limit}, not the cap")
    assert query._count == "exact", (
        "without the count there is no way to tell a whole list from a cut one")


def test_a_cut_list_reports_how_many_there_really_are(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "kid-1"})
    monkeypatch.setattr(main, "_SESSION_LIST_MAX", 2)
    monkeypatch.setattr(main, "supabase", _FakeSupabase({"sessions": _sessions(5)}))

    out = main.list_sessions(None)

    assert len(out["sessions"]) == 2
    assert out["total"] == 5, "the page cannot say `of 5` without this"
    assert out["truncated"] is True


def test_a_list_exactly_at_the_cap_is_not_reported_as_cut(monkeypatch):
    """The case `len(rows) == cap` gets wrong, which is why the count decides."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "kid-1"})
    monkeypatch.setattr(main, "_SESSION_LIST_MAX", 3)
    monkeypatch.setattr(main, "supabase", _FakeSupabase({"sessions": _sessions(3)}))

    out = main.list_sessions(None)

    assert out["total"] == 3
    assert out["truncated"] is False, (
        "a whole list of exactly the cap was reported as cut, which puts a "
        "`showing your most recent 3` notice on a complete history")


def test_a_count_that_did_not_come_back_is_unknown_rather_than_whole(monkeypatch):
    """Third state: `False` would claim completeness from a number never received."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "kid-1"})
    fake = _FakeSupabase(
        {"sessions": [_SESSION_ROW, {**_SESSION_ROW, "id": "other", "user_id": "kid-2"}]},
        count_missing=True)
    monkeypatch.setattr(main, "supabase", fake)

    out = main.list_sessions(None)

    assert out["total"] is None
    assert out["truncated"] is None
    # And the read it made is still the right one.
    query = fake.queries[0]
    assert ("user_id", "kid-1") in query.filters
    assert query._limit == main._SESSION_LIST_MAX
    assert [s["id"] for s in out["sessions"]] == ["sess-1"]
