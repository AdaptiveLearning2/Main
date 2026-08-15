import httpx
from fastapi.testclient import TestClient

from src.app.config import get_settings
from src.app.main import app


def _send_with_raw_authorization(client: TestClient, auth_value: bytes):
    """Send a request with a raw (possibly non-ASCII) Authorization header.

    httpx's normal header API refuses non-ASCII str values before a request is
    ever sent, so client.get(..., headers={...}) cannot reproduce what a
    non-Python client (curl, a raw socket) can put on the wire. Building the
    request and replacing its header list with raw bytes bypasses that
    client-side validation, matching what Starlette actually receives.
    """
    request = client.build_request("GET", "/api/v1/state")
    raw = list(request.headers.raw)
    raw.append((b"authorization", auth_value))
    request.headers = httpx.Headers(raw)
    return client.send(request)


def test_state_rejects_non_ascii_bearer_token_with_401():
    # Regression test: a byte >= 0x80 in the Authorization header arrives here
    # as a non-ASCII str (Starlette decodes header bytes as latin-1).
    # secrets.compare_digest raises TypeError on non-ASCII str input, which
    # previously surfaced as an unauthenticated 500 instead of a 401.
    client = TestClient(app)
    response = _send_with_raw_authorization(client, b"Bearer caf\xe9")
    assert response.status_code == 401


def test_state_rejects_high_byte_bearer_token_with_401():
    client = TestClient(app)
    response = _send_with_raw_authorization(client, b"Bearer \xff\xfe\xfd")
    assert response.status_code == 401


def test_state_still_accepts_correct_token_via_raw_path():
    # Sanity check that the raw-header send path itself isn't what's making
    # the requests above 401 -- a valid token through the same path succeeds.
    client = TestClient(app)
    settings = get_settings()
    response = _send_with_raw_authorization(
        client, f"Bearer {settings.api_token}".encode()
    )
    assert response.status_code == 200


# ── require_local_controller: the mode decides who may drive the hardware ────
#
# Device lifecycle and headband pairing were admin-only because the backend was
# the only caller -- under `pull` it polls this sidecar and drives the hardware
# for the student. Push inverts that: the backend is remote by definition, so it
# refuses those operations, and the page in front of the student is the only
# thing that can reach their headband. These pin that the widening is scoped to
# push and nowhere else.

import pytest  # noqa: E402

from src.app.security import require_local_controller  # noqa: E402


def _auth(token):
    return f"Bearer {token}"


@pytest.fixture
def _fresh_settings(monkeypatch):
    """`get_settings` is cached, and these tests turn PUSH_ENABLED on and off."""
    def _clear():
        get_settings.cache_clear()
    _clear()
    yield _clear
    _clear()


def test_the_admin_token_drives_the_hardware_in_either_mode(monkeypatch, _fresh_settings):
    for push in ("false", "true"):
        monkeypatch.setenv("PUSH_ENABLED", push)
        _fresh_settings()
        s = get_settings()
        assert require_local_controller(_auth(s.admin_token)) == "admin", push


def test_under_pull_the_learner_token_may_not_drive_the_hardware(monkeypatch, _fresh_settings):
    """The pre-existing rule, unchanged. A pull deployment's browser gains
    nothing from this dependency -- the backend is the legitimate controller
    there, and it holds the admin token."""
    monkeypatch.setenv("PUSH_ENABLED", "false")
    _fresh_settings()
    s = get_settings()

    with pytest.raises(Exception) as e:
        require_local_controller(_auth(s.api_token))
    assert getattr(e.value, "status_code", None) == 401


def test_under_push_the_learner_token_may(monkeypatch, _fresh_settings):
    """Without this, push has no pairing path at all: the browser holds the
    learner token and every start/scan/connect endpoint answered 401, which is
    what made push unusable for the channel it exists for."""
    monkeypatch.setenv("PUSH_ENABLED", "true")
    _fresh_settings()
    s = get_settings()

    assert require_local_controller(_auth(s.api_token)) == "learner"


def test_push_does_not_admit_an_unrelated_token(monkeypatch, _fresh_settings):
    """Turning push on widens *which* token is accepted, not whether one is."""
    monkeypatch.setenv("PUSH_ENABLED", "true")
    _fresh_settings()

    for bad in ("", "not-the-token", "Bearer"):
        with pytest.raises(Exception) as e:
            require_local_controller(_auth(bad))
        assert getattr(e.value, "status_code", None) == 401, bad
    with pytest.raises(Exception):
        require_local_controller(None)


def test_the_learner_only_routes_did_not_become_admin_routes(monkeypatch, _fresh_settings):
    """The change is one-directional. `/api/v1/state` and the push endpoints
    stay learner-readable in both modes -- widening the pairing routes must not
    have narrowed anything."""
    monkeypatch.setenv("PUSH_ENABLED", "false")
    _fresh_settings()
    s = get_settings()
    client = TestClient(app)

    r = client.get("/api/v1/state", headers={"Authorization": _auth(s.api_token)})

    assert r.status_code != 401
