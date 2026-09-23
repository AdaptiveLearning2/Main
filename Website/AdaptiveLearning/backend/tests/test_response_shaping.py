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
import typing

from pydantic import BaseModel

import pytest

import main
from tests.test_access_control import _FakeSupabase, _ts


# (handler, param) -> (how it is bounded, why).
#
#   "handler"  the handler passes it through `min(...)` or `_clamp_days(...)`,
#              which this file checks mechanically below.
#   "field"    the request model carries `le=`, checked from `model_fields`.
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
        ("handler", "min(1, ...) -- a difficulty shift, not a row count, but "
                    "unbounded it would reach _shift_difficulty."),
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
# and passes having examined nothing. These four are the shapes -- a query int,
# a `Query(...)` default, a body field, and the route the audit was about.
EXPECTED_NUMBERS = {
    ("get_questions", "limit"),
    ("generate_question", "bias"),
    ("student_chart_summary", "weeks"),
    ("leaderboard", "limit"),
}


def _caller_numbers():
    """Every int/float a caller can put in a request, found at runtime.

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
            if ann in (int, float):
                found[(endpoint.__name__, param.name)] = getattr(route, "path", "?")
            elif isinstance(ann, type) and issubclass(ann, BaseModel):
                for fname, field in ann.model_fields.items():
                    fann = field.annotation
                    args = typing.get_args(fann)
                    if fann in (int, float) or int in args or float in args:
                        found[(endpoint.__name__, fname)] = getattr(route, "path", "?")
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
    tree = ast.parse(pathlib.Path(main.__file__).read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    raise AssertionError(f"no handler named {name}")


@pytest.mark.parametrize("pair", sorted(
    p for p, (how, _why) in CALLER_NUMBERS.items() if how == "handler"))
def test_a_bound_claimed_clamped_in_the_handler_reaches_a_clamp(pair):
    """The name is passed to `min` or `_clamp_days` somewhere in the handler.

    **This is a check on the convention, not on the arithmetic**, and the
    difference matters because it decides what a failure means. All eighteen
    clamps here are spelled `max(floor, min(name, ceiling))` or `_clamp_days`,
    so a handler that bounds its parameter some other correct way -- `name = N
    if name > N else name` -- fails this and is not a bug. The message says
    what was looked for rather than asserting a defect, and conforming or
    reclassifying are both fine answers.

    **What it cannot see:** whether the ceiling is a sensible number, and
    whether the clamped value is the one that reaches the query. Both are
    per-endpoint behaviour, tested where the endpoint is --
    `test_questions.py::test_the_list_clamps_the_limit_it_was_handed` asserts on
    the limit the *query* received, which is the form to copy for a new one.
    What this catches is a clamp deleted outright, which is silent today.
    """
    handler_name, param = pair
    clamps = [
        node for node in ast.walk(_handler(handler_name))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in ("min", "_clamp_days")
        and any(param in ast.unparse(a) for a in node.args)
    ]
    assert clamps, (
        f"CALLER_NUMBERS says {handler_name} clamps `{param}` in the handler, "
        "and no min()/_clamp_days() call in it mentions that name. Either "
        "clamp it the way the other seventeen do, or -- if it is bounded some "
        "other correct way -- move the entry to the mechanism that describes "
        "it. This checks the spelling, not the arithmetic.")


@pytest.mark.parametrize("pair", sorted(
    p for p, (how, _why) in CALLER_NUMBERS.items() if how == "field"))
def test_a_bound_claimed_on_the_field_carries_an_upper_limit(pair):
    """Read off `model_fields`, so a `le=` removed from the model fails here
    even though the handler is untouched."""
    handler_name, field_name = pair
    endpoint = next(r.endpoint for r in main.app.routes
                    if getattr(getattr(r, "endpoint", None), "__name__", None)
                    == handler_name)
    models = [p.annotation for p in inspect.signature(endpoint).parameters.values()
              if isinstance(p.annotation, type) and issubclass(p.annotation, BaseModel)]
    limits = [m for model in models
              for m in getattr(model.model_fields[field_name], "metadata", ())
              if hasattr(m, "le")]
    assert limits, f"{field_name} no longer carries an upper bound on its field"


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
# `_FakeSupabase` projects to the columns a read names, so a session row given
# a `chart_paths` comes back carrying it if and only if the read asked for `*`.
# That makes these assertions about the request, not about a filter applied on
# the way out (rule 4) -- and the row is built with the column present, or
# there would be nothing for a `select("*")` to leak.

_SESSION_ROW = {
    "id": "sess-1", "user_id": "kid-1", "class_id": None,
    "title": "Practice Session", "started_at": _ts(5), "ended_at": _ts(4),
    "questions_answered": 3, "correct_answers": 2,
    "chart_paths": {"focus": "kid-1/sess-1/focus.svg"},
}


def test_a_students_own_session_list_carries_no_object_paths(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "kid-1"})
    monkeypatch.setattr(main, "supabase", _FakeSupabase({"sessions": [_SESSION_ROW]}))

    rows = main.list_sessions(None)

    assert rows and "chart_paths" not in rows[0], (
        "the archived charts' storage paths are being sent to the student's "
        "browser, which renders them nowhere")
    # And the read still returns what the page draws, or the fix is a blank list.
    assert rows[0]["questions_answered"] == 3
    assert rows[0]["title"] == "Practice Session"


def test_a_viewers_session_list_carries_no_object_paths(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "teacher-1"})
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a: None)
    monkeypatch.setattr(main, "supabase", _FakeSupabase({"sessions": [_SESSION_ROW]}))

    payload = main.student_sessions("kid-1", None)
    rows = payload["sessions"] if isinstance(payload, dict) else payload

    assert rows and "chart_paths" not in rows[0]
    # The derived flag is still there -- this read feeds it.
    assert rows[0]["abandoned"] is False


def test_the_open_session_a_teacher_watches_carries_no_object_paths(monkeypatch):
    """`class_live` returns what this helper finds as `active_session`."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase({
        "sessions": [{**_SESSION_ROW, "ended_at": None}]}))

    by_student = main._open_sessions_many(["kid-1"])

    assert by_student["kid-1"], "the open session was not found at all"
    assert "chart_paths" not in by_student["kid-1"][0]


def test_a_parents_child_sessions_carry_no_object_paths(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "parent-1"})
    monkeypatch.setattr(main, "_profile", lambda _c: {})
    monkeypatch.setattr(main, "_signal_summaries",
                        lambda ids, **_kw: {str(i): {} for i in ids})
    monkeypatch.setattr(main, "supabase", _FakeSupabase({
        "parent_child_links": [{"parent_id": "parent-1", "child_id": "kid-1",
                                "created_at": _ts(9)}],
        "sessions": [_SESSION_ROW],
        "user_stats": [], "user_math_performance": [],
    }))

    children = main.my_children(None)

    assert children and children[0]["sessions"], "no sessions reached the parent"
    assert "chart_paths" not in children[0]["sessions"][0]
