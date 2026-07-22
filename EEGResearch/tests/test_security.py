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
