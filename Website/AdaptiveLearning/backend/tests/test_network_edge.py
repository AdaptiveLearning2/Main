"""The network edge: who may call, what comes back on it, and how much may be sent.

Three things shipped together because they are one surface, and none of them
had any test at all -- no backend test used `TestClient` before this file, so
every middleware was unreachable from the suite by construction.

What each one is actually claiming is worth keeping straight, because two of
them are weaker than they look:

- **CORS** decides which origin may *read* a response. It was
  `allow_origins=["*"]` with `allow_credentials=True`, which Starlette serves
  by reflecting whatever Origin asked -- an allowlist spelled as a wildcard.
- **The security headers** are instructions to a browser, not enforcement. The
  CSP in particular is scoped to what this server is: a JSON API that serves no
  document. It says nothing about the frontend, which is a separate origin with
  its own hosting config.
- **The body cap** bounds what a worker will accept. Its whole subtlety is that
  the three ingest endpoints legitimately need far more than everything else,
  so a single number would have refused real sensor data.
"""

import ast
import json
import os
import subprocess
import sys

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

client = TestClient(main.app)

# A public read with no auth and no body -- the cheapest route that proves a
# response-wide behaviour without needing a signed-in user.
OPEN_PATH = "/api/topics"

ALLOWED = "http://localhost:5173"
FOREIGN = "https://evil.example"


# ─── CORS ────────────────────────────────────────────────────────────────

def test_a_listed_origin_is_allowed_to_read_the_response():
    r = client.get(OPEN_PATH, headers={"Origin": ALLOWED})
    assert r.headers.get("access-control-allow-origin") == ALLOWED


def test_an_unlisted_origin_gets_no_permission_to_read_it():
    """The response still returns 200 -- CORS is enforced in the browser.

    So the assertion is on the *absence of the header*, not on a status code.
    A test expecting a 4xx here would fail against a correct implementation.
    """
    r = client.get(OPEN_PATH, headers={"Origin": FOREIGN})
    assert r.headers.get("access-control-allow-origin") is None


def test_an_unlisted_origin_is_refused_at_the_preflight():
    r = client.options(OPEN_PATH, headers={
        "Origin": FOREIGN, "Access-Control-Request-Method": "GET"})
    assert r.status_code == 400
    assert r.headers.get("access-control-allow-origin") is None


def test_credentials_are_not_allowed_and_that_is_what_makes_the_list_mean_anything():
    """With credentials on and `["*"]`, Starlette reflects the asking Origin.

    That is the configuration this replaced, and it is why the wildcard was an
    allowlist of everything rather than an obvious blanket. This app puts its
    bearer token in a header, which no browser attaches on its own, so there
    are no credentials to allow.
    """
    r = client.options(OPEN_PATH, headers={
        "Origin": ALLOWED, "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-credentials") is None
    assert main.ALLOWED_ORIGINS and "*" not in main.ALLOWED_ORIGINS


def test_a_method_the_api_does_not_use_is_refused_at_the_preflight():
    r = client.options(OPEN_PATH, headers={
        "Origin": ALLOWED, "Access-Control-Request-Method": "PATCH"})
    assert r.status_code == 400


@pytest.mark.parametrize("header", ["Authorization", "Content-Type"])
def test_the_headers_the_clients_actually_send_are_allowed(header):
    """`lib/api.js` sends exactly these two, and so does the sidecar's push
    client. Narrowing the list is only safe while it still covers them."""
    r = client.options(OPEN_PATH, headers={
        "Origin": ALLOWED, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": header})
    assert r.status_code == 200


@pytest.mark.parametrize("raw,expected", [
    (None,             ["http://localhost:5173", "http://127.0.0.1:5173"]),
    ("",               ["http://localhost:5173", "http://127.0.0.1:5173"]),
    ("   ",            ["http://localhost:5173", "http://127.0.0.1:5173"]),
    ("https://a.test", ["https://a.test"]),
    (" https://a.test , https://b.test ", ["https://a.test", "https://b.test"]),
    ("https://a.test,,", ["https://a.test"]),
])
def test_the_origin_list_reads_the_unset_cases_as_unset(monkeypatch, raw, expected):
    """A blank value must mean "not configured", never a list of one empty
    string -- as an allowed origin that matches nothing, so the symptom is the
    whole frontend refused at the edge by a setting that looks present."""
    if raw is None:
        monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("ALLOWED_ORIGINS", raw)
    assert main._env_list(
        "ALLOWED_ORIGINS",
        ("http://localhost:5173", "http://127.0.0.1:5173")) == expected


# ─── security headers ────────────────────────────────────────────────────

EXPECTED_HEADERS = {
    "x-content-type-options":   "nosniff",
    "x-frame-options":          "DENY",
    "referrer-policy":          "strict-origin-when-cross-origin",
    "cache-control":            "no-store",
    "permissions-policy":       "camera=(), microphone=(), geolocation=(), payment=()",
}


@pytest.mark.parametrize("header,value", sorted(EXPECTED_HEADERS.items()))
def test_every_response_carries_the_header(header, value):
    assert client.get(OPEN_PATH).headers.get(header) == value


def test_the_camera_is_denied_here_however_much_the_product_uses_one():
    """The app does open a webcam -- on the *frontend* origin, via the sidecar.

    This origin serves no document, so a `camera=(self)` carve-out here would
    permit a capability with nothing to use it. Stated as a test because the
    plan this came from asked for the carve-out, and it reads as an oversight.
    """
    policy = client.get(OPEN_PATH).headers["permissions-policy"]
    assert "camera=()" in policy and "camera=(self)" not in policy


def test_the_policy_says_this_is_not_a_document():
    csp = client.get(OPEN_PATH).headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_the_backend_still_serves_no_html_which_is_why_that_policy_is_honest():
    """`default-src 'none'` is only the right answer while nothing here renders.

    Add a `StaticFiles` mount or an `HTMLResponse` and the policy above goes
    from strict-and-correct to a page that loads nothing, so this is the check
    that has to fail first.

    Over the AST rather than the file's text: the first version of this read
    `main.py` as a string and failed on the *comment* beside the CSP, which
    names all three of these to explain why none of them is there. A source
    scan cannot tell a use from a mention; identifiers can.
    """
    tree = ast.parse(open(os.path.abspath(main.__file__), encoding="utf-8").read())
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            used.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)

    for sink in ("HTMLResponse", "StaticFiles", "Jinja2Templates", "FileResponse"):
        assert sink not in used, sink


@pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
def test_the_docs_pages_are_exempt_from_the_policy(path):
    """They are real HTML pulling Swagger from a CDN.

    Under `default-src 'none'` they render blank, which reads as broken tooling
    rather than as a policy doing its job. They are off in production anyway.
    """
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers.get("content-security-policy") is None


def test_the_headers_reach_an_error_response_too():
    """A header that only rides on 200s protects the responses least worth it."""
    r = client.get("/api/definitely-not-a-route")
    assert r.status_code == 404
    for header, value in EXPECTED_HEADERS.items():
        assert r.headers.get(header) == value


def test_a_refusal_is_readable_by_the_page_that_caused_it():
    """The body cap sits inside CORS so its 413 still carries the header.

    Without it the browser reports a generic network failure, and a size limit
    becomes indistinguishable from the backend being down.
    """
    r = client.post("/api/classes", content=b"x" * (300 * 1024),
                    headers={"content-type": "application/json", "Origin": ALLOWED})
    assert r.status_code == 413
    assert r.headers.get("access-control-allow-origin") == ALLOWED


# ─── request body size ───────────────────────────────────────────────────

def _limit(path):
    return main.MaxBodySizeMiddleware(None)._limit(path)


def test_a_declared_oversized_body_is_refused():
    r = client.post("/api/classes", content=b"x" * (main._MAX_BODY_BYTES + 1),
                    headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_a_body_that_declares_no_size_is_refused_as_it_arrives():
    """The declaration check passes a chunked upload straight through.

    Which is exactly the caller this exists for: every client in this product
    sends `Content-Length`, so a client that does not is the one worth
    counting. Without the streaming half the cap is advisory.
    """
    def chunks():
        for _ in range(main._MAX_BODY_BYTES // 1024 + 64):
            yield b"x" * 1024

    r = client.post("/api/classes", content=chunks(),
                    headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_a_body_under_the_cap_is_not_touched():
    """The cap is worthless if it also refuses ordinary traffic, and a 413 on
    a real request is indistinguishable from the endpoint being broken."""
    r = client.post("/api/classes", json={"name": "4B", "grade_level": "5th Grade"})
    assert r.status_code != 413


def test_the_ingest_endpoints_get_their_own_larger_cap():
    assert _limit("/api/signals/cognitive") > _limit("/api/classes")
    assert _limit("/api/signals/heart") == _limit("/api/signals/face")


def test_a_body_between_the_two_caps_is_refused_on_one_path_and_not_the_other():
    """The distinction itself, rather than either number.

    A test of one cap passes against a build that applies it everywhere, which
    is the mistake worth catching: a single global limit would refuse real
    sensor data.
    """
    between = b"x" * ((main._MAX_BODY_BYTES + _limit("/api/signals/cognitive")) // 2)
    headers = {"content-type": "application/json"}
    assert client.post("/api/classes", content=between, headers=headers).status_code == 413
    assert client.post("/api/signals/cognitive", content=between,
                       headers=headers).status_code != 413


def test_the_ingest_cap_is_derived_from_the_batch_bound():
    """Not written as its own number, so the two cannot drift.

    Raising `INGEST_MAX_BATCH` must not start refusing batches at the edge for
    a reason nothing in the ingest code mentions.
    """
    assert _limit("/api/signals/cognitive") == \
        main._INGEST_MAX_BATCH * main._INGEST_MAX_SAMPLE_BYTES


def test_a_full_sized_real_cognitive_batch_fits_inside_it():
    """The per-sample allowance against a measured sample, not a guess.

    This is the check that fails if someone trims the allowance: a full batch
    of the fattest sample the sidecar sends is ~637 KiB, which is already two
    and a half times the ordinary cap.
    """
    sample = {
        "ts": "2026-09-18T12:34:56.789012+00:00",
        "features": {"focus": 0.6231, "stress": 0.4112, "engagement": 0.6231,
                     "confidence": 0.8123, "contact_ratio": 0.75,
                     "focus_log_ratio": -0.5312, "calm_log_ratio": -0.4417,
                     "calm_alpha_residual": 0.1234, "spectrum_slope": -1.2413,
                     "artifact_reason": None, "samples_artifact": 12,
                     "samples_no_delta": 3, "samples_no_spread": 1,
                     "focus_centred": True, "calm_centred": False,
                     "calm_measured": True, "calm_held_seconds": 0.0},
        "bands": {"delta": 0.913, "theta": 0.412, "alpha": 0.5523,
                  "beta": 0.3311, "gamma": 0.2214},
        "raw": {"device_id": "default", "channels": [0.1234] * 4,
                "state": {"label": "neutral", "since": "2026-09-18T12:34:00+00:00",
                          "calm_source": "sdk", "score_scale": 2},
                "ingestion": {"eeg_source": "muse", "bridge_mode": "native",
                              "muse_connected": True,
                              "active_muse_name": "MuseS-0FFC",
                              "muse_devices": ["MuseS-0FFC", "MuseS-1A2B",
                                               "MuseS-3C4D"],
                              "connection_state": "connected", "eeg_age_ms": 41,
                              "battery_percent": 78.0,
                              "requested_preset": "PRESET_1035",
                              "active_preset": "PRESET_1035",
                              "last_good_ts": 1.7e9, "last_good_age_s": 0.25,
                              "consecutive_errors": 0, "preset_mismatch": False,
                              "auto_reconnect": True, "reconnecting": False,
                              "reconnect_attempt": 0,
                              "reconnect_max_attempts": 5,
                              "reconnect_exhausted": False}}}
    full = json.dumps({"session_id": "b7e1c2d3-4f56-7890-abcd-ef0123456789",
                       "samples": [sample] * main._INGEST_MAX_BATCH})
    assert len(full) > main._MAX_BODY_BYTES, "sanity: this is why ingest is special"
    assert len(full) < _limit("/api/signals/cognitive")


# ─── the docs, in production ─────────────────────────────────────────────

def test_the_docs_are_off_in_production():
    """Run in a subprocess, because the setting is read at import.

    `importlib.reload(main)` would answer the same question and rebind every
    class in the module for whatever runs next in the session -- which has
    already cost this suite once.
    """
    script = (
        "import os, sys;"
        "sys.path.insert(0, %r);"
        "from fastapi.testclient import TestClient;"
        "import main;"
        "c = TestClient(main.app);"
        "print(main.IS_PRODUCTION,"
        " c.get('/docs').status_code,"
        " c.get('/redoc').status_code,"
        " c.get('/openapi.json').status_code)"
    ) % os.path.dirname(os.path.abspath(main.__file__))

    env = dict(os.environ, ENV="production",
               SUPABASE_URL="http://localhost:54321",
               SUPABASE_SERVICE_ROLE_KEY="test-key")
    out = subprocess.run([sys.executable, "-c", script], capture_output=True,
                         text=True, env=env)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.split()[-4:] == ["True", "404", "404", "404"], out.stdout


def test_the_docs_are_on_outside_production():
    """The other half: switching them off everywhere costs the tooling."""
    assert main.IS_PRODUCTION is False
    assert client.get("/docs").status_code == 200
