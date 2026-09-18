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

# Rebound before every test rather than built once at import.
#
# `test_consent_gates_polling` calls `importlib.reload(main)`, which builds a
# *new* `FastAPI` object. A client captured at collection time would go on
# serving the old app from that point while every assertion beside it read the
# reloaded module -- two instances, agreeing only because nothing yet
# configures them differently, so the first monkeypatched setting would make
# this file pass or fail on test ordering. Same hazard as the request models in
# `test_grade_prompt_injection`, and the same rule: resolve `main`'s attributes
# when the test runs.
#
# Autouse and rebinding the global, rather than a `client` argument on each of
# the seventeen tests below, so a test added later cannot opt out of the fresh
# instance by forgetting to ask for it.
client = None


@pytest.fixture(autouse=True)
def _client_for_the_live_module():
    global client
    client = TestClient(main.app)
    yield
    client = None


# A public read with no auth and no body -- the cheapest route that proves a
# response-wide behaviour without needing a signed-in user.
OPEN_PATH = "/api/topics"

ALLOWED = "http://localhost:5173"
FOREIGN = "https://evil.example"


def test_the_client_serves_the_module_the_assertions_read():
    """One instance, not two.

    Trivially true in isolation and the whole point in a full-suite run: after
    `test_consent_gates_polling` reloads `main`, a client captured at
    collection time fails this while everything around it still passes, which
    is exactly how a two-instance bug hides. Its teeth come from running with
    the rest of the suite, so check it there.
    """
    assert client.app is main.app


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


def test_retry_after_is_readable_by_the_page_it_was_sent_to():
    """It is not a CORS-safelisted response header, so it needs naming.

    Seven refusals here set `Retry-After`, and `apiFetch` reads it to size its
    wait and then jitters it. Unexposed, the browser hides the header, every
    refusal falls back to the fixed delay, and the arrival-rate measurement
    behind `GENERATION_MAX_WAITERS` describes behaviour nothing performs. The
    frontend is a different origin from this API in every deployment, local dev
    included, so this is not an edge case.
    """
    r = client.get(OPEN_PATH, headers={"Origin": ALLOWED})
    exposed = [h.strip() for h in
               (r.headers.get("access-control-expose-headers") or "").split(",")]
    assert "Retry-After" in exposed


def test_the_exposure_list_is_that_one_header():
    """Exposure is a read permission, granted per header for a reason."""
    r = client.get(OPEN_PATH, headers={"Origin": ALLOWED})
    exposed = [h.strip() for h in
               r.headers["access-control-expose-headers"].split(",") if h.strip()]
    assert exposed == ["Retry-After"]


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


@pytest.mark.parametrize("raw", ["production", "PRODUCTION", " Production "])
def test_the_production_spellings_are_recognised(raw):
    assert main._is_production(raw)[1] is True


@pytest.mark.parametrize("raw", [None, "", "   ", "development", "dev", "local", "ci"])
def test_the_development_spellings_and_the_unset_case_publish_the_docs(raw):
    """Unset has to stay development: it is the ordinary local state, and
    needing a variable set for the tooling to work is its own trap."""
    assert main._is_production(raw)[1] is False


@pytest.mark.parametrize("raw", ["prod", "PROD", "prod ", "production "])
def test_a_shortened_production_name_is_not_a_near_miss(raw):
    """`ENV=prod` under a plain `== "production"` publishes the API's whole
    shape in production, silently. It is the likeliest typo of the lot."""
    assert main._is_production(raw)[1] is True


@pytest.mark.parametrize("raw", ["staging", "produciton", "Production!", "1", "yes"])
def test_a_name_this_app_does_not_know_hides_the_docs_and_says_so(raw, capsys):
    """The opposite fallback direction from `_env_number`, on purpose.

    There the safe side is the feature's own default; here it is publishing
    less. Guessing the other way turns one typo in a deploy config into a
    published map of the API, and the log line is what separates that from a
    value someone meant.
    """
    assert main._is_production(raw)[1] is True
    assert "not a name this app knows" in capsys.readouterr().out


def _env_example():
    path = os.path.join(os.path.dirname(os.path.abspath(main.__file__)),
                        ".env.example")
    return open(path, encoding="utf-8").read()


def test_the_example_file_ships_a_value_this_app_recognises(capsys):
    """`.env.example` is the file a deployment is copied from.

    A value there that the app warns about would put the `[config]` line in
    front of every reader of a fresh checkout, which is how a warning stops
    being read.
    """
    shipped = [line.split("=", 1)[1].strip()
               for line in _env_example().splitlines()
               if line.startswith("ENV=")]
    assert shipped, "the example no longer documents ENV at all"
    for value in shipped:
        assert main._is_production(value)[1] is False
        assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "name", sorted(main._PRODUCTION_ENVS | main._DEVELOPMENT_ENVS))
def test_every_name_the_app_recognises_is_written_down_where_a_deploy_looks(name):
    """Coverage, not wording -- the comment is prose and a test cannot read it.

    What it can catch is the drift that already happened here once: the block
    described the fallback as keeping the docs *on* when the shipped rule turns
    them off, in the file someone copies to configure a deployment. Adding a
    recognised spelling without naming it here fails this.
    """
    assert name in _env_example()


@pytest.mark.parametrize("raw", [None, "", "development", "production", "prod"])
def test_a_name_this_app_knows_is_silent(raw, capsys):
    """A warning on every ordinary boot is a warning nobody reads, and the
    line above only means something while it is rare."""
    main._is_production(raw)
    assert capsys.readouterr().out == ""
