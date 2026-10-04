"""Every non-literal argument to a PostgREST-parsed query method carries a recorded reason."""
import ast
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Parsed-as-syntax methods, `rpc` (first arg names the function) and `execute` (an arg means a raw driver).
PARSED_ARG_METHODS = {
    "or_", "not_", "filter", "select", "order", "on_conflict", "rpc", "execute",
}

# (module, enclosing function, method, argument source) -> why it is allowed.
# No line number in the key: a churning key gets re-baselined without reading.
ALLOWLIST = {
    ("main.py", "admin_student_search", "or_",
     "f'display_name.ilike.%{safe}%,email.ilike.%{safe}%'"):
        "PostgREST's or-filter has no parameterized form. `safe` strips , ( ) "
        "-- the filter's own structural characters -- and escapes \\ and %, "
        "which are ilike's. The stripping is the control; see the comment there.",

    # One constant, three reads, so no copy of the column list can drift.
    ("main.py", "list_sessions", "select", "_SESSION_CLIENT_COLUMNS"):
        "A module-level literal: every `sessions` column except `chart_paths`. "
        "No interpolation and nothing from a request reaches it.",
    ("main.py", "_open_sessions_many", "select", "_SESSION_CLIENT_COLUMNS"):
        "The same constant.",
    ("main.py", "student_sessions", "select", "_SESSION_CLIENT_COLUMNS"):
        "The same constant.",

    ("main.py", "_summary_rpc", "rpc", "name"):
        "The RPC name, passed by internal callers as a literal.",
    ("main.py", "_own_profile", "select", "_PROFILE_SELF_COLUMNS"):
        "A module-level literal naming the caller's own profile columns.",
    ("main.py", "_session_or_403", "select", "columns"):
        "Column list chosen by the calling endpoint, defaulting to 'user_id'.",
    ("main.py", "_practice_session_or_403", "select", "columns"):
        "As _session_or_403.",
    ("LLM_topic_decider.py", "_latest", "select", "columns"):
        "Column list passed by internal callers as a literal.",
    ("chart_archive.py", "rows", "select", "SIGNAL_COLUMNS[table]"):
        "A module-level literal keyed by the three table names it iterates itself.",
    ("repair_common.py", "rows", "select", "columns"):
        "Column list passed as a literal by the two repair scripts, run by hand.",
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

    Descends rather than `ast.walk`, so a nested helper's call is attributed to it alone.
    """
    found = []

    def visit(node, module, fname):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fname = node.name
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in PARSED_ARG_METHODS):
            # `rpc`'s second argument is a bound params dict.
            args = node.args[:1] if node.func.attr == "rpc" else node.args
            for arg in args:
                kind = _dynamic_kind(arg)
                if kind:
                    found.append((module, fname, node.func.attr,
                                  ast.unparse(arg), kind, node.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child, module, fname)

    for path in sorted(BACKEND.glob("*.py")):
        # utf-8-sig: several generators carry a BOM.
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
    """An entry outliving its call site is a reason nobody will re-read."""
    live = {(s[0], s[1], s[2], s[3]) for s in _sites()}
    stale = sorted(set(ALLOWLIST) - live)
    assert not stale, (
        "ALLOWLIST entries with no matching call site -- delete them:\n"
        + "\n".join(f"  {e}" for e in stale))


def test_a_new_site_in_a_scanned_file_fails_this_check(tmp_path, monkeypatch):
    """The end of the chain: glob, parse, detect, miss the allowlist, fail."""
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
    """The check is only worth its allowlist if it catches these."""
    tree = ast.parse(snippet)
    call = next(n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in PARSED_ARG_METHODS)
    args = call.args[:1] if call.func.attr == "rpc" else call.args
    assert any(_dynamic_kind(a) for a in args), why


# ── a write whose rows decide something asks for them ──────────────────────

# Every postgrest write that defaults to returning=representation.
WRITE_METHODS = {"insert", "upsert", "update", "delete"}


def _base_name(node):
    """The name a call chain starts from (`q` in `q.eq(...).execute()`), or None."""
    while isinstance(node, (ast.Call, ast.Attribute)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else None


def _row_reading_writes(tree, module):
    """A table write whose returned rows are read: (module, function, line, named)."""
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(func):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr in WRITE_METHODS
                    and "table(" in ast.unparse(call.func.value)):
                continue
            top, reads = call, False
            while isinstance(parents.get(top), (ast.Call, ast.Attribute)):
                top = parents[top]
                reads |= isinstance(top, ast.Attribute) and top.attr == "data"
            # A builder kept in a name (`q = ...`) is read wherever `q`'s chain takes `.data`.
            holder = parents.get(top)
            if not reads and isinstance(holder, ast.Assign) and len(holder.targets) == 1:
                name = getattr(holder.targets[0], "id", None)
                reads = any(isinstance(n, ast.Attribute) and n.attr == "data" and _base_name(n.value) == name
                            for n in ast.walk(func))
            if reads:
                named = any(k.arg == "returning" for k in call.keywords)
                found.append((module, func.name, call.lineno, named))
    return found


def _all_row_reading_writes():
    found = []
    for path in sorted(BACKEND.glob("*.py")):
        found += _row_reading_writes(ast.parse(path.read_text(encoding="utf-8-sig")), path.name)
    return found


def test_a_write_whose_rows_decide_something_asks_for_them():
    """Empty rows read as "no match" (a 404, a 409, a lost close); under `return=minimal` every write is empty."""
    sites = _all_row_reading_writes()
    # A scan that finds nothing passes while checking nothing.
    assert len(sites) >= 14, sites
    unnamed = [s for s in sites if not s[3]]
    assert not unnamed, ("Pass returning=ReturnMethod.representation; the client's default is not a contract:\n"
                         + "\n".join(f"  {m}:{ln} {fn}()" for m, fn, ln, _ in unnamed))


@pytest.mark.parametrize("body, reads", [
    ('    rows = supabase.table("t").update({}).eq("id", 1).execute().data', True),
    ('    q = supabase.table("t").delete().eq("id", 1)\n    out = q.execute().data', True),
    ('    res = supabase.table("t").delete().eq("id", 1).execute()\n    return bool(res.data)', True),
    ('    supabase.table("t").update({}).eq("id", 1).execute()', False),
    ('    q = supabase.table("t").delete().eq("id", 1)\n    q.execute()', False),
    ('    res = supabase.table("t").insert({}).execute()\n    return res.data[0]', True),
    ('    self.n += len(supabase.table("t").upsert({}, on_conflict="id").execute().data)', True),
], ids=["chained", "builder-in-a-name", "response-in-a-name", "rows-unread", "builder-rows-unread",
        "insert", "upsert"])
def test_the_write_detector_sees_each_shape(body, reads):
    found = _row_reading_writes(ast.parse(f"def handler():\n{body}\n"), "m.py")
    assert bool(found) is reads, (body, found)
