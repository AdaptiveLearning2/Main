"""get_user's GoTrue check is bounded, and an outage is a 503, never a 401 or an unhandled 500."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402
import requests  # noqa: E402
from fastapi import HTTPException  # noqa: E402

import main  # noqa: E402


class _Request:
    headers = {"authorization": "Bearer tok"}


def test_the_auth_check_carries_a_timeout(monkeypatch):
    seen = {}

    def get(url, headers=None, timeout=None):
        seen["timeout"] = timeout
        return type("R", (), {"status_code": 200, "json": lambda self: {"id": "u1"}})()

    monkeypatch.setattr(main.requests, "get", get)
    assert main.get_user(_Request())["id"] == "u1"
    assert seen["timeout"] == main.AUTH_CHECK_TIMEOUT and seen["timeout"] > 0


@pytest.mark.parametrize("error", [requests.ConnectionError("refused"), requests.Timeout("stalled")])
def test_an_unreachable_auth_server_is_a_503(monkeypatch, error):
    def get(*_a, **_k):
        raise error

    monkeypatch.setattr(main.requests, "get", get)
    with pytest.raises(HTTPException) as caught:
        main.get_user(_Request())
    assert caught.value.status_code == 503


def test_a_refused_token_is_still_a_401(monkeypatch):
    monkeypatch.setattr(main.requests, "get",
                        lambda *a, **k: type("R", (), {"status_code": 401})())
    with pytest.raises(HTTPException) as caught:
        main.get_user(_Request())
    assert caught.value.status_code == 401
