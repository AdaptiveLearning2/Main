import httpx
from fastapi.testclient import TestClient

from src.app.config import get_settings
from src.app.main import app


def _send_with_raw_authorization(client: TestClient, auth_value: bytes):
    """Send a request with a raw (possibly non-ASCII) Authorization header.

    httpx's normal header API rejects non-ASCII str values before sending, so
    a plain `client.get(headers=...)` can't reproduce what a non-Python client
    puts on the wire. Building the request and replacing its raw header bytes
    bypasses that client-side validation.
    """
    request = client.build_request("GET", "/api/v1/state")
    raw = list(request.headers.raw)
    raw.append((b"authorization", auth_value))
    request.headers = httpx.Headers(raw)
    return client.send(request)


def test_state_rejects_non_ascii_bearer_token_with_401():
    # A byte >= 0x80 arrives as a non-ASCII str (Starlette decodes headers as
    # latin-1), which crashes secrets.compare_digest -- must be a 401, not a
    # 500.
    client = TestClient(app)
    response = _send_with_raw_authorization(client, b"Bearer caf\xe9")
    assert response.status_code == 401


def test_state_rejects_high_byte_bearer_token_with_401():
    client = TestClient(app)
    response = _send_with_raw_authorization(client, b"Bearer \xff\xfe\xfd")
    assert response.status_code == 401


def test_state_still_accepts_correct_token_via_raw_path():
    # Confirms the raw-header send path itself isn't what causes the 401s
    # above -- a valid token through the same path must succeed.
    client = TestClient(app)
    settings = get_settings()
    response = _send_with_raw_authorization(
        client, f"Bearer {settings.api_token}".encode()
    )
    assert response.status_code == 200


# ── require_local_controller: the mode decides who may drive the hardware ──
#
# Under `pull`, the backend polls this sidecar and drives hardware for the
# student, so pairing stays admin-only. Under `push` the backend is remote and
# refuses those operations, so the student's own page must be allowed to
# reach their headband instead. These tests pin that widening to push only.

import pytest  # noqa: E402

from src.app.security import require_local_controller  # noqa: E402


def _auth(token):
    return f"Bearer {token}"


@pytest.fixture
def _fresh_settings(monkeypatch):
    """`get_settings` is cached; clear it around tests that toggle PUSH_ENABLED."""
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
    """Under pull, the backend is the legitimate controller and holds the
    admin token -- the browser gains nothing from this dependency."""
    monkeypatch.setenv("PUSH_ENABLED", "false")
    _fresh_settings()
    s = get_settings()

    with pytest.raises(Exception) as e:
        require_local_controller(_auth(s.api_token))
    assert getattr(e.value, "status_code", None) == 401


def test_under_push_the_learner_token_may(monkeypatch, _fresh_settings):
    """Without this, push has no pairing path: the browser holds only the
    learner token, and every start/scan/connect endpoint would answer 401."""
    monkeypatch.setenv("PUSH_ENABLED", "true")
    _fresh_settings()
    s = get_settings()

    assert require_local_controller(_auth(s.api_token)) == "learner"


def test_push_does_not_admit_an_unrelated_token(monkeypatch, _fresh_settings):
    """Push widens which token is accepted, not whether a valid one is
    required."""
    monkeypatch.setenv("PUSH_ENABLED", "true")
    _fresh_settings()

    for bad in ("", "not-the-token", "Bearer"):
        with pytest.raises(Exception) as e:
            require_local_controller(_auth(bad))
        assert getattr(e.value, "status_code", None) == 401, bad
    with pytest.raises(Exception):
        require_local_controller(None)


def test_the_learner_only_routes_did_not_become_admin_routes(monkeypatch, _fresh_settings):
    """`/api/v1/state` and the push endpoints must stay learner-readable in
    both modes -- widening the pairing routes must not narrow anything else."""
    monkeypatch.setenv("PUSH_ENABLED", "false")
    _fresh_settings()
    s = get_settings()
    client = TestClient(app)

    r = client.get("/api/v1/state", headers={"Authorization": _auth(s.api_token)})

    assert r.status_code != 401
