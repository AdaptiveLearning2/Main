"""No query string in this backend is built from a value it did not choose.

There is no raw SQL here -- every read and write goes through the Supabase
client or an RPC, both parameterized -- so the classic injection is already
absent and this file is not about it. What PostgREST adds is a second
grammar: `or_`, `filter`, `select`, `order` and `on_conflict` take *strings
that are parsed as query syntax*, and `rpc` takes a function name. A value
interpolated into one of those is not a bound parameter, it is structure.

`/api/admin/students/search` is the live example: a teacher's search term
reaches `.or_(f"display_name.ilike.%{safe}%,...")`, where a bare comma would
end the first condition and start a second one of the caller's choosing. It
strips `, ( )` and escapes `\\ %` first, which is why it is allowed below --
the point of this test is that the stripping cannot quietly disappear, and
that the next such site has to argue for itself the same way.

**Every dynamic argument is listed, including a bare name.** Flagging only
f-strings would leave the evasion of assigning to a variable first, and the
whole set is ten -- small enough that a recorded reason per site costs little
and buys a re-read whenever one changes. `check_function_grants.py`'s
ALLOWLIST is the same shape and exists for the same reason.

**The honest limit**: this reads one call at a time and cannot see where a
variable came from. The eight bare names below are safe because every caller
passes a literal, and that is a claim this test checks by *listing* them, not
by proving it -- change what a listed helper is called with and the entry
still matches. It catches a new site appearing, not an existing one being fed
something new. Whole-program dataflow is the thing that would, and it is not
a lint.
"""
import ast
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Methods whose string argument PostgREST parses as query syntax, plus `rpc`
# (its *first* argument names the function to run) and `execute` (which takes
# no arguments through supabase-py, and would mean a raw driver if it ever
# did).
PARSED_ARG_METHODS = {
    "or_", "not_", "filter", "select", "order", "on_conflict", "rpc", "execute",
}

# (module, enclosing function, method, argument source) -> why it is allowed.
# The key holds no line number on purpose: these move, and a key that churns
# is one people re-baseline without reading.
ALLOWLIST = {
    ("main.py", "admin_student_search", "or_",
     "f'display_name.ilike.%{safe}%,email.ilike.%{safe}%'"):
        "PostgREST's or-filter has no parameterized form. `safe` strips , ( ) "
        "-- the filter's own structural characters -- and escapes \\ and %, "
        "which are ilike's. The stripping is the control; see the comment there.",

    ("main.py", "student_sessions", "select", "f'session_id, {column}'"):
        "`column` comes from the module-level _ACTIVITY_SOURCES, never a request.",
    ("main.py", "student_sessions", "order", "column"):
        "_ACTIVITY_SOURCES again, in the same loop as the select above.",

    ("main.py", "_measured_only", "filter", "columns[0]"):
        "Caller passes _ACTIVITY_SOURCES entries; no request value reaches it.",
    ("main.py", "_measured_only", "or_",
     "','.join((f'{c}.not.is.null' for c in columns))"):
        "Same `columns`. The join builds an or-tree from those fixed names.",

    # One constant, four reads, because the alternative to naming it is four
    # copies of the column list -- and the copy that drifts is the one that
    # quietly starts returning the column the others stopped returning.
    ("main.py", "list_sessions", "select", "_SESSION_CLIENT_COLUMNS"):
        "A module-level literal: every `sessions` column except `chart_paths`. "
        "No interpolation and nothing from a request reaches it.",
    ("main.py", "_open_sessions_many", "select", "_SESSION_CLIENT_COLUMNS"):
        "The same constant.",
    ("main.py", "student_sessions", "select", "_SESSION_CLIENT_COLUMNS"):
        "The same constant.",
    ("main.py", "my_children", "select", "_SESSION_CLIENT_COLUMNS"):
        "The same constant.",

    ("main.py", "_summary_rpc", "rpc", "name"):
        "The RPC name, passed by internal callers as a literal.",
    ("main.py", "_session_or_403", "select", "columns"):
        "Column list chosen by the calling endpoint, defaulting to 'user_id'.",
    ("main.py", "_practice_session_or_403", "select", "columns"):
        "As _session_or_403.",
    ("main.py", "_fetch", "order", "ts_col"):
        "Timestamp column name, picked by _weekly_signal_report's caller from "
        "a fixed set.",
    ("LLM_topic_decider.py", "_latest", "select", "columns"):
        "Column list passed by internal callers as a literal.",
}


def _dynamic_kind(node):
    """What kind of non-literal this argument is, or None if it is a literal."""
    if isinstance(node, ast.JoinedStr):
        return "f-string"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return "concatenation"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return "%-formatting"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == "format":
        return ".format()"
    if isinstance(node, ast.Constant):
        return None
    return "a name, so this test cannot see what it holds"


def _sites():
    """Every non-literal argument reaching a parsed-string query method.

    Descends rather than using `ast.walk` per function, so a call inside a
    nested helper is attributed to that helper and not to whichever enclosing
    scope happens to also contain it. `ast.walk` sees it under both, and the
    outer name is the one that goes stale first.
    """
    found = []

    def visit(node, module, fname):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fname = node.name
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in PARSED_ARG_METHODS):
            # Only `rpc`'s first argument names anything; its second is a
            # params dict, which is bound rather than interpolated.
            args = node.args[:1] if node.func.attr == "rpc" else node.args
            for arg in args:
                kind = _dynamic_kind(arg)
                if kind:
                    found.append((module, fname, node.func.attr,
                                  ast.unparse(arg), kind, node.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child, module, fname)

    for path in sorted(BACKEND.glob("*.py")):
        # utf-8-sig: several generators carry a BOM, which plain utf-8 reads
        # as a non-printable character and refuses to parse.
        visit(ast.parse(path.read_text(encoding="utf-8-sig")),
              path.name, "<module>")
    return sorted(found)


def test_no_query_string_is_built_from_an_unreviewed_value():
    unlisted = [s for s in _sites()
                if (s[0], s[1], s[2], s[3]) not in ALLOWLIST]
    assert not unlisted, (
        "A query method that parses its argument as syntax is being handed a "
        "value this test has not seen. If the value cannot come from a "
        "request, add it to ALLOWLIST with that reason; if it can, bind it or "
        "strip the grammar's own characters first, as _admin_search_students "
        "does.\n" + "\n".join(
            f"  {m}:{ln} {fn}() .{meth}({src})  [{kind}]"
            for m, fn, meth, src, kind, ln in unlisted))


def test_the_allowlist_has_no_entry_for_a_site_that_is_gone():
    """An entry outliving its call site is a reason nobody will re-read.

    Without this the list only grows, and a future reader takes a stale
    justification as evidence the current code was reviewed.
    """
    live = {(s[0], s[1], s[2], s[3]) for s in _sites()}
    stale = sorted(set(ALLOWLIST) - live)
    assert not stale, (
        "ALLOWLIST entries with no matching call site -- delete them:\n"
        + "\n".join(f"  {e}" for e in stale))


def test_a_new_site_in_a_scanned_file_fails_this_check(tmp_path, monkeypatch):
    """The end of the chain: glob, parse, detect, miss the allowlist, fail.

    The shape tests below drive `_dynamic_kind` directly, which says nothing
    about whether a file on disk is read or whether an unlisted site actually
    reaches the assertion. Pointing the scan at a directory of one file is how
    that gets exercised without editing `main.py` to prove it.
    """
    (tmp_path / "newmodule.py").write_text(
        'def handler(term):\n'
        '    return supabase.table("profiles").or_(f"email.ilike.%{term}%")\n',
        encoding="utf-8")
    monkeypatch.setattr("test_query_construction.BACKEND", tmp_path)

    sites = _sites()
    assert [(s[0], s[1], s[2]) for s in sites] == [
        ("newmodule.py", "handler", "or_")]
    with pytest.raises(AssertionError, match="has not seen"):
        test_no_query_string_is_built_from_an_unreviewed_value()


@pytest.mark.parametrize("snippet, why", [
    ('supabase.table("t").select(f"id, {col}")', "f-string into select"),
    ('supabase.table("t").or_("a.eq." + val)', "concatenation into or_"),
    ('supabase.table("t").filter("c" % v, "eq", 1)', "%-formatting into filter"),
    ('supabase.table("t").order("{}".format(c))', ".format() into order"),
    ('supabase.rpc(fn_name, {})', "a name as the RPC to run"),
])
def test_the_detector_sees_each_shape(snippet, why):
    """The check is only worth its allowlist if it catches these.

    Asserted against parsed snippets rather than by editing `main.py`: the
    thing under test is `_dynamic_kind` plus the method set, and driving it
    directly is what makes each shape a separate named failure.
    """
    tree = ast.parse(snippet)
    call = next(n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in PARSED_ARG_METHODS)
    args = call.args[:1] if call.func.attr == "rpc" else call.args
    assert any(_dynamic_kind(a) for a in args), why
