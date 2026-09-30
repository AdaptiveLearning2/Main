"""Every Supabase client here speaks HTTP/1.1 through one pool. See CLAUDE.md, Database."""
import ast
import os
import pathlib
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from supabase_client import make_client  # noqa: E402

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
_URL, _KEY = "http://localhost:54321", "test-key"


def test_every_sub_client_uses_the_one_http1_pool():
    client = make_client(_URL, _KEY)
    http = client.options.httpx_client
    assert isinstance(http, httpx.Client)
    # Private, but the only place httpx records it; HTTP/2 shared across threads is the bug.
    assert http._transport._pool._http2 is False
    assert client.postgrest.session is http
    assert client.storage.session is http
    assert client.auth._http_client is http


def test_a_query_and_a_storage_call_go_out_through_it_with_their_url_and_key():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[])

    client = make_client(_URL, _KEY)
    client.options.httpx_client._transport = httpx.MockTransport(handler)

    client.table("sessions").select("id").eq("id", "s1").execute()
    client.storage.list_buckets()

    assert [str(r.url).split("?")[0] for r in seen] == [
        f"{_URL}/rest/v1/sessions", f"{_URL}/storage/v1/bucket"]
    for r in seen:
        assert r.headers["apikey"] == _KEY
        assert r.headers["authorization"] == f"Bearer {_KEY}"


def _calls(tree, name):
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and (
        getattr(n.func, "id", None) == name or getattr(n.func, "attr", None) == name)]


def test_no_backend_module_builds_a_client_another_way():
    stray, factory_users = [], set()
    for path in _BACKEND.rglob("*.py"):
        rel = path.relative_to(_BACKEND)
        if rel.parts[0] in ("tests", "__pycache__") or rel.parts[0].startswith("."):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))  # some files carry a BOM
        if rel.name != "supabase_client.py" and _calls(tree, "create_client"):
            stray.append(str(rel))
        if _calls(tree, "make_client"):
            factory_users.add(rel.name)
    assert stray == [], f"build Supabase clients with supabase_client.make_client: {stray}"
    # A floor, so a scan that finds nothing cannot pass.
    assert {"main.py", "LLM_topic_decider.py", "lesson_plan_context.py"} <= factory_users
