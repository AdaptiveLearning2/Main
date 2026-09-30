"""Every Supabase client here speaks HTTP/1.1 through one pool per process. See CLAUDE.md, Database."""
import ast
import os
import pathlib
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import supabase_client  # noqa: E402
from supabase_client import get_client, make_client  # noqa: E402

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
_URL, _KEY = "http://localhost:54321", "test-key"
# One-shot CLIs build a fresh client: cached, a test's patched fake would outlive the test.
_SCRIPTS = {"rearchive_session_charts.py", "repair_graph_comparisons.py",
            "repair_shape_fraction_texts.py", "sweep_orphan_charts.py"}


def test_every_sub_client_uses_the_one_http1_pool():
    client = make_client(_URL, _KEY)
    http = client.options.httpx_client
    assert isinstance(http, httpx.Client)
    # Private, but the only place httpx records these. HTTP/2 shared across threads is the bug;
    # a keep-alive cap below the connection cap re-handshakes the overflow of every burst.
    pool = http._transport._pool
    assert pool._http2 is False
    assert pool._max_keepalive_connections == pool._max_connections >= 100
    assert client.postgrest.session is http
    assert client.storage.session is http
    assert client.auth._http_client is http


def test_one_client_per_credentials_per_process(monkeypatch):
    monkeypatch.setattr(supabase_client, "_clients", {})
    first = get_client(_URL, _KEY)
    assert get_client(_URL, _KEY) is first
    assert get_client(_URL, "another-key") is not first


def _send_one_query_and_one_storage_call():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[])

    client = make_client(_URL, _KEY)
    client.options.httpx_client._transport = httpx.MockTransport(handler)
    client.table("sessions").select("id").eq("id", "s1").execute()
    client.storage.list_buckets()
    return seen


def test_a_query_and_a_storage_call_go_out_with_their_url_and_key():
    seen = _send_one_query_and_one_storage_call()
    assert [str(r.url).split("?")[0] for r in seen] == [
        f"{_URL}/rest/v1/sessions", f"{_URL}/storage/v1/bucket"]
    for r in seen:
        assert r.headers["apikey"] == _KEY
        assert r.headers["authorization"] == f"Bearer {_KEY}"


def test_storage_keeps_its_short_timeout_on_the_shared_pool():
    rest, storage = (r.extensions["timeout"] for r in _send_one_query_and_one_storage_call())
    assert rest["read"] == 120.0
    assert storage["read"] == 20.0
    # A starved pool fails fast for both, rather than queueing for the read budget.
    assert rest["pool"] == storage["pool"] == 10.0


def _calls(tree, name):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and (
        getattr(n.func, "id", None) == name or getattr(n.func, "attr", None) == name)]


def test_long_lived_modules_share_the_cached_client():
    stray, shared, fresh = [], set(), set()
    for path in _BACKEND.rglob("*.py"):
        rel = path.relative_to(_BACKEND)
        if rel.parts[0] in ("tests", "__pycache__") or rel.parts[0].startswith("."):
            continue
        if rel.name == "supabase_client.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))  # some files carry a BOM
        if _calls(tree, "create_client"):
            stray.append(f"{rel}: create_client")
        if _calls(tree, "make_client"):
            fresh.add(rel.name)
            if rel.name not in _SCRIPTS:
                stray.append(f"{rel}: make_client")
        if _calls(tree, "get_client"):
            shared.add(rel.name)
    assert stray == [], f"use supabase_client.get_client (make_client in one-shot scripts): {stray}"
    # Floors, so a scan that finds nothing cannot pass and the script list cannot go stale.
    assert {"main.py", "LLM_topic_decider.py", "lesson_plan_context.py"} <= shared
    assert fresh == _SCRIPTS
