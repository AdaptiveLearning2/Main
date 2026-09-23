"""What a response carries, and what a caller may ask for.

Two questions, taken from the one endpoint that already asked them of itself.
`leaderboard`'s docstring says `limit` "is the only thing bounding how much of
the user base comes back with names attached", and pops `user_id` because "the
page only needs to know which row is the viewer's own". So:

1. **Is every caller-supplied number bounded?** One was not: `/api/questions`
   passed `limit` straight into `.limit()`. The rows are not the exposure --
   `questions` is public-read by policy and reachable through PostgREST with
   the anon key -- the *cache* was: an unclamped limit put a copy of the whole
   bank behind every distinct key, so the entry bound that exists to stop a key
   sweep was holding 256 of them. A bound on the number of entries is not a
   bound on their size.
2. **Does a payload carry an identifier nothing renders?** `sessions` was read
   with `select("*")` on the four paths whose rows reach a browser, and
   `chart_paths` -- the storage object path of each archived SVG -- appears
   nowhere in `src/`.

The email fields came out of this audit unchanged, and that is a result rather
than an omission: `class_students`, `class_live`, `my_children` and
`admin_student_search` all send `email`, all four are behind the relationship
check that entitles the viewer to it, and all four are rendered (a roster line,
a live card, the parent dashboard, and the `<option>` fallback label in
`Questions.jsx`). `_profile()`'s full dict is returned only for the caller's own
profile; `link_child` sends the child's `display_name` and nothing else. The raw
`signal_consent` row never leaves the backend -- `_shape_consent` builds the
payload field by field, which is why `updated_by` and the per-channel revoker
uuids are absent rather than needing removing.

**The partition below is the durable half.** Fixing one unclamped limit does not
stop the next one; classifying every caller-supplied number does. It is a named
list for the reason `STUDENT_DRIVEN_CLOSERS` is: whether an int is a bound on a
query or a value to be stored is not a property of its type, and nothing in the
source separates them.
"""
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


# (handler, param) -> (how it is bounded, why).
#
#   "handler"  the handler clamps it as `max(floor, min(it, ceiling))`, or with
#              `_clamp_days(...)` -- one bound wrapping the other, checked
#              mechanically below.
#   "field"    the request model carries `le=` and `ge=`, read off `model_fields`.
#   "raises"   the handler answers 422 outside the range; cited to the test
#              that drives it, since a raise has no one shape to match.
#   "value"    not a bound at all -- a number that is stored or applied, whose
#              own range is somebody else's business.
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

# The floor, named rather than counted, for the reason the limiter partition
# carries one: a scan that stops seeing parameters reports nothing unclassified
# and passes having examined nothing.
#
# **It covers the scan going quiet, not the scan being blind.** An annotation
# spelled a way the scan does not recognise produces no entry to be missing, so
# these four would still be found and the suite would still be green. The
# spelling tests at the end of this file are what cover that.
EXPECTED_NUMBERS = {
    ("get_questions", "limit"),
    ("generate_question", "bias"),
    ("student_chart_summary", "weeks"),
    ("leaderboard", "limit"),
}


# The wrappers a type is written inside, and the only ones the scan looks
# through: `Union` covers `int | None` and `Optional[int]` (one origin since
# 3.14), and `Annotated` covers `Annotated[int, Query(le=500)]` -- the only way
# to bound a *query* parameter rather than a model field, and the form current
# FastAPI documentation recommends -- and `Annotated[Model, Body()]`.
#
# **Containers are deliberately not unwrapped**, for numbers or for models.
# `typing.get_args` hands back `int` from `dict[str, int]`, and a mapping of
# counts is not a caller-supplied bound. The only models inside a container
# today are the ingest batches' `list[FaceSample]` / `list[HeartSample]`, whose
# numbers are sensor readings rather than bounds, and whose volume is bounded by
# `INGEST_MAX_BATCH` and the body cap instead.
_WRAPPERS = (typing.Union, types.UnionType, typing.Annotated)


def _mentions_a_number(ann) -> bool:
    """Whether a caller can put an int or a float here, however it is spelled.

    One predicate for parameters *and* model fields. There were two: parameters
    matched `ann in (int, float)` exactly while fields unwrapped unions, so
    `limit: int | None = None` -- how `get_questions` already writes
    `subject: str | None` two parameters along -- was invisible on a route and
    visible in a body. `bool` is not an int here: this compares identity, and
    `include_face` bounds nothing.
    """
    if ann is int or ann is float:
        return True
    if typing.get_origin(ann) in _WRAPPERS:
        return any(_mentions_a_number(arg) for arg in typing.get_args(ann))
    return False


def _models_in(ann) -> list:
    """The request models an annotation carries, through the same wrappers.

    A body written `payload: Model | None = None` or
    `Annotated[Model, Body()]` is still a body; testing `isinstance(ann, type)`
    alone never looked inside either.
    """
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return [ann]
    if typing.get_origin(ann) in _WRAPPERS:
        return [m for arg in typing.get_args(ann) for m in _models_in(arg)]
    return []


def _model_numbers(model, prefix="", seen=None):
    """`(dotted field path)` for every number in a model, including through a
    field that is itself a model -- `window: Window | None` carries
    `window.days` as surely as a top-level field does. `seen` stops a model
    that refers to itself."""
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
    """Every int/float a caller can put in a request outside a container,
    found at runtime. Inside one -- the ingest samples -- is out of scope on
    purpose; see `_WRAPPERS`.

    From `app.routes` and `model_fields`, not the AST: the limiter partition's
    scan was rebuilt this way after an AST match on one assignment shape turned
    out to be one refactor from seeing nothing, and a route decorated through a
    helper or a parameter given a `Query(...)` default is the same trap here.
    """
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

    # The other direction, or the list only grows and a stale entry reads as
    # evidence the current signature was reviewed.
    stale = sorted(pair for pair in CALLER_NUMBERS if pair not in found)
    assert not stale, (
        f"these parameters are gone; drop them from CALLER_NUMBERS: {stale}")


def _handler(name):
    """One handler's AST, from its own source.

    `inspect.getsource`, the way `close_sites()` reads a function, rather than
    re-parsing all of `main.py` per parametrized case -- that was eighteen
    parses of a nine-thousand-line module, most of the file's runtime.
    """
    fn = getattr(main, name)
    return ast.parse(inspect.getsource(fn)).body[0]


def _names_param(node, param) -> bool:
    """Whether an expression reads the parameter itself: the bare name, or an
    attribute of that name on a body (`payload.days`). Matched on nodes, not
    text -- as a substring, `days` is also in `len(school_days)`, and
    `max(0, len(school_days))` would have counted as a floor for it."""
    return any(
        (isinstance(n, ast.Name) and n.id == param)
        or (isinstance(n, ast.Attribute) and n.attr == param)
        for n in ast.walk(node))


def _is_call(node, name) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == name)


def _clamps(tree, param) -> bool:
    """`max(floor, min(param, ceiling))`, either way round, or `_clamp_days`.

    **The nesting is the check.** Asking only whether a `max` and a `min` each
    mention the name counts `max(0, page * size - limit)` as a floor for
    `limit`, beside any `min` elsewhere -- two calls that bound nothing between
    them. Its stated limit: the inner call has to *read* the parameter, not be
    handed it bare, since `generate_question` clamps `int(bias or 0)`.
    """
    for node in ast.walk(tree):
        if _is_call(node, "_clamp_days") and any(_names_param(a, param) for a in node.args):
            return True
        for outer, inner in (("max", "min"), ("min", "max")):
            if _is_call(node, outer) and any(
                    _is_call(arg, inner) and any(_names_param(a, param) for a in arg.args)
                    for arg in node.args):
                return True
    return False


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
    """One bound wraps the other around the name, or `_clamp_days` does both.

    **Both ends, because each fails differently.** Without the ceiling a caller
    chooses how much comes back; without the floor `?limit=-5` goes through
    PostgREST as `LIMIT -5`, which Postgres refuses -- a 500 from the caller's
    own input. Checking only `min` left the second one unguarded on every route
    this list names.

    **This is a check on the convention, not on the arithmetic**, and that
    decides what a failure means. All eighteen are spelled
    `max(floor, min(name, ceiling))` or `_clamp_days`, so a handler bounding its
    parameter some other correct way -- `name = N if name > N else name` --
    fails this and is not a bug; conforming or reclassifying are both fine
    answers. What a ceiling or a floor should *be* is per-endpoint behaviour,
    asserted on the limit the query received: `test_questions.py`, and
    `test_a_negative_limit_reaches_the_query_as_the_floor` below.
    """
    handler_name, param = pair
    assert _clamps(_handler(handler_name), param), (
        f"CALLER_NUMBERS says {handler_name} clamps `{param}` in the handler, and "
        f"no `max(floor, min({param}, ceiling))` or `_clamp_days({param})` is in "
        "it. Clamp it the way the others do, or -- if it is bounded some other "
        "correct way -- move the entry to the mechanism that describes it. This "
        "checks the spelling, not the arithmetic.")


@pytest.mark.parametrize("pair", sorted(
    p for p, (how, _why) in CALLER_NUMBERS.items() if how == "field"))
def test_a_bound_claimed_on_the_field_carries_both_ends(pair):
    """Read off `model_fields`, so an `le=` or `ge=` removed from the model
    fails here even though the handler is untouched. Both ends for the reason
    the handler clamps need both: these land in columns with CHECK constraints,
    and a value outside one is a 500 rather than a 422 that names the field."""
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
    """(has a floor, has a ceiling), read off a field's metadata by *value*.

    Not `hasattr`: `conint(le=180)` and `Interval(le=180)` both carry
    `ge=None`, so an attribute check reads a floor that is not there. And
    `gt`/`lt` are bounds as much as `ge`/`le` are.
    """
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
    """`bypass_minutes` answers 422 rather than clamping, because a bypass of
    consent enforcement silently shortened to four hours is a different promise
    from one refused. A raise has no single shape to match, so this checks the
    citation rather than the code -- anchored on the opening paren, or a rename
    that appends still contains the name.
    """
    _how, cited = CALLER_NUMBERS[("admin_set_flag", "bypass_minutes")]
    admin_tests = (pathlib.Path(__file__).parent / "test_admin.py").read_text(
        encoding="utf-8")
    assert cited in admin_tests, (
        f"{cited!r} is gone or renamed, so nothing checks the one bound that "
        "is enforced by raising")


# ── the payload half ─────────────────────────────────────────────────────
#
# Each test asserts on the **query first** (rule 4): the `sessions` read named
# its columns and `chart_paths` was not among them. The payload assertions
# follow as the consequence, and each carries a positive one beside the
# negative, so a read that came back empty -- or a projection that dropped
# every column -- fails rather than passing for having no `chart_paths` in it.
# The row is built with the column present, or there would be nothing for a
# `select("*")` to leak.

_SESSION_ROW = {
    "id": "sess-1", "user_id": "kid-1", "class_id": None,
    "title": "Practice Session", "started_at": _ts(5), "ended_at": _ts(4),
    "questions_answered": 3, "correct_answers": 2,
    "chart_paths": {"focus": "kid-1/sess-1/focus.svg"},
}


def _assert_sessions_read_names_its_columns(fake):
    """Every read of `sessions` named columns, and none of them was the path.

    `_FakeSupabase` records `_cols = None` for a `select("*")`, which is the
    shape this exists to refuse; a list without `chart_paths` is the fix.
    """
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
    # `class_live` reads these off the row, and the stats merge reads the
    # counts -- a projection that dropped them would pass the line above.
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
#
# The spelling check above covers every handler clamp; these are the three
# `limit` routes that had no test driving either end. Asserted on the number the
# query was handed, not on what came back (rule 4): a fake answers the same
# rows whatever limit it is asked for.
#
# **Every limit that table was sent, not the last one.** A later `.limit(1)`
# read of the same table -- an existence check, say -- would otherwise stand in
# for the main read and pass the floor test with the floor removed.

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
    """`?limit=-5` without a floor reaches Postgres as `LIMIT -5`, which it
    refuses: a 500 from the caller's own input, on routes whose ceiling was
    already tested and whose floor was not."""
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
#
# The floor on EXPECTED_NUMBERS stops the scan seeing nothing. It cannot stop
# the scan being blind to a *spelling*, because an annotation it does not
# recognise produces no entry to be missing. The real app writes none of the
# spellings below, so the partition cannot fail on them either -- hence a
# predicate table, and a probe app run through `_caller_numbers()` itself.

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

    A copy of the loop carries whatever predicate it was written with, so it
    passes against a scan that has stopped calling the predicate at all -- the
    first version of this test did exactly that, and reverting the scan's
    parameter branch to the bug left it green.

    Parameters and bodies in every wrapper, a model nested in a model, and the
    one thing deliberately *not* found: a model inside a list, whose numbers are
    readings rather than bounds. FastAPI's own routes on the probe
    (`/openapi.json`, `/docs`, …) carry no numeric parameter, so exact equality
    holds with them present.
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
#
# Not a caller-supplied bound -- no page offers a longer list and no parameter
# lifts it -- so it is not in the partition above. It came out of the same
# audit: `/api/sessions` read every session a student had ever had, and the
# page it feeds is the only record of itself, counting the rows, summing their
# questions and dividing for an accuracy. A cap with no way to say it applied
# would not render as a shorter list; it would render as a student who did
# less work.


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
    """The case `len(rows) == cap` gets wrong, which is why the count decides.

    It is also the case PostgREST's own `db-max-rows` makes unreliable in the
    other direction: a short read is not evidence the list was whole.
    """
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "kid-1"})
    monkeypatch.setattr(main, "_SESSION_LIST_MAX", 3)
    monkeypatch.setattr(main, "supabase", _FakeSupabase({"sessions": _sessions(3)}))

    out = main.list_sessions(None)

    assert out["total"] == 3
    assert out["truncated"] is False, (
        "a whole list of exactly the cap was reported as cut, which puts a "
        "`showing your most recent 3` notice on a complete history")


def test_a_count_that_did_not_come_back_is_unknown_rather_than_whole(monkeypatch):
    """Third state. `False` would assert the list is complete on the strength
    of a number we did not receive, and a page would then present a cut
    history as a whole one.

    The shared fake with its count withheld, not a hand-written one: that kept
    the rows whatever the query asked, so dropping the student filter or the
    cap would have passed it.
    """
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
