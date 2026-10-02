"""get_user's GoTrue check is bounded, pooled, and an outage is a 503, never a 401 or an unhandled 500."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import main  # noqa: E402


class _Request:
    headers = {"authorization": "Bearer tok"}


def _ok(*_a, **_k):
    return type("R", (), {"status_code": 200, "json": lambda self: {"id": "u1"}})()


def test_the_auth_check_goes_through_the_pooled_client(monkeypatch):
    """A per-call client or `requests.get` re-handshakes TLS on every authenticated request."""
    seen = []
    monkeypatch.setattr(main._AUTH_HTTP, "get", lambda url, **k: seen.append(url) or _ok())
    assert main.get_user(_Request())["id"] == "u1"
    assert main.get_user(_Request())["id"] == "u1"
    assert seen == [f"{main.SUPABASE_URL}/auth/v1/user"] * 2


def test_the_auth_check_carries_its_own_timeout():
    """The Supabase client's request hook rewrites /auth/v1/ timeouts; this client has none."""
    timeout = main._AUTH_HTTP.timeout
    assert timeout.connect == timeout.read == main.AUTH_CHECK_TIMEOUT > 0
    assert not main._AUTH_HTTP.event_hooks["request"]


def test_the_auth_pool_keeps_connections_alive():
    pool = main._AUTH_HTTP._transport._pool
    assert pool._max_keepalive_connections == pool._max_connections >= 1
    assert pool._keepalive_expiry >= 30


@pytest.mark.parametrize("error", [httpx.ConnectError("refused"), httpx.ReadTimeout("stalled"),
                                   httpx.PoolTimeout("pool full")])
def test_an_unreachable_auth_server_is_a_503(monkeypatch, error):
    def get(*_a, **_k):
        raise error

    monkeypatch.setattr(main._AUTH_HTTP, "get", get)
    with pytest.raises(HTTPException) as caught:
        main.get_user(_Request())
    assert caught.value.status_code == 503


def test_a_refused_token_is_still_a_401(monkeypatch):
    monkeypatch.setattr(main._AUTH_HTTP, "get",
                        lambda *a, **k: type("R", (), {"status_code": 401})())
    with pytest.raises(HTTPException) as caught:
        main.get_user(_Request())
    assert caught.value.status_code == 401
