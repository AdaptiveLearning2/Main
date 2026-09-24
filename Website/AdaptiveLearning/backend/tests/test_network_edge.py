"""The network edge: CORS, security headers, body caps and public rate limits, via TestClient."""

import ast
import asyncio
import json
import pathlib
import re
import os
import subprocess
import sys
import threading

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

# Rebound per test by an autouse fixture: another test reloads `main`, building a new app.
client = None


@pytest.fixture(autouse=True)
def _client_for_the_live_module():
    global client
    client = TestClient(main.app)
    # One shared peer address; conftest's `_limiters_start_empty` empties its budgets.
    yield
    client = None


# A public read with no auth and no body.
OPEN_PATH = "/api/topics"

ALLOWED = "http://localhost:5173"
FOREIGN = "https://evil.example"


def test_the_client_serves_the_module_the_assertions_read():
    """Only has teeth in a full-suite run, after another test reloads `main`."""
    assert client.app is main.app


# ─── CORS ────────────────────────────────────────────────────────────────

def test_a_listed_origin_is_allowed_to_read_the_response():
    r = client.get(OPEN_PATH, headers={"Origin": ALLOWED})
    assert r.headers.get("access-control-allow-origin") == ALLOWED


def test_an_unlisted_origin_gets_no_permission_to_read_it():
    """Still a 200: CORS is enforced in the browser, so assert the header's absence."""
    r = client.get(OPEN_PATH, headers={"Origin": FOREIGN})
    assert r.headers.get("access-control-allow-origin") is None


def test_an_unlisted_origin_is_refused_at_the_preflight():
    r = client.options(OPEN_PATH, headers={
        "Origin": FOREIGN, "Access-Control-Request-Method": "GET"})
    assert r.status_code == 400
    assert r.headers.get("access-control-allow-origin") is None


def test_credentials_are_not_allowed_and_that_is_what_makes_the_list_mean_anything():
    """With credentials on, Starlette reflects any Origin; the bearer token is a header anyway."""
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
    """`lib/api.js` and the sidecar's push client send exactly these two."""
    r = client.options(OPEN_PATH, headers={
        "Origin": ALLOWED, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": header})
    assert r.status_code == 200


def test_retry_after_is_readable_by_the_page_it_was_sent_to():
    """Not CORS-safelisted, and `apiFetch` reads it cross-origin to size its wait."""
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
    """A blank value means unset, never a list of one empty string."""
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
    """The webcam opens on the frontend origin; this one serves no document."""
    policy = client.get(OPEN_PATH).headers["permissions-policy"]
    assert "camera=()" in policy and "camera=(self)" not in policy


def test_the_policy_says_this_is_not_a_document():
    csp = client.get(OPEN_PATH).headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_the_backend_still_serves_no_html_which_is_why_that_policy_is_honest():
    """Over the AST, since a text scan cannot tell a use from a mention in a comment."""
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
    """Real HTML pulling Swagger from a CDN; off in production anyway."""
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers.get("content-security-policy") is None


def test_the_headers_reach_an_error_response_too():
    r = client.get("/api/definitely-not-a-route")
    assert r.status_code == 404
    for header, value in EXPECTED_HEADERS.items():
        assert r.headers.get(header) == value


def test_a_refusal_is_readable_by_the_page_that_caused_it():
    """The body cap sits inside CORS, or its 413 reads as a network failure."""
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
    """A chunked upload has no Content-Length, so the cap must count what arrives."""
    def chunks():
        for _ in range(main._MAX_BODY_BYTES // 1024 + 64):
            yield b"x" * 1024

    r = client.post("/api/classes", content=chunks(),
                    headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_a_body_under_the_cap_is_not_touched():
    r = client.post("/api/classes", json={"name": "4B", "grade_level": "5th Grade"})
    assert r.status_code != 413


def test_the_ingest_endpoints_get_their_own_larger_cap():
    assert _limit("/api/signals/cognitive") > _limit("/api/classes")
    assert _limit("/api/signals/heart") == _limit("/api/signals/face")


def test_a_body_between_the_two_caps_is_refused_on_one_path_and_not_the_other():
    """A single global limit would refuse real sensor data."""
    between = b"x" * ((main._MAX_BODY_BYTES + _limit("/api/signals/cognitive")) // 2)
    headers = {"content-type": "application/json"}
    assert client.post("/api/classes", content=between, headers=headers).status_code == 413
    assert client.post("/api/signals/cognitive", content=between,
                       headers=headers).status_code != 413


def test_the_ingest_cap_is_derived_from_the_batch_bound():
    """Derived, so raising `INGEST_MAX_BATCH` cannot start refusing batches at the edge."""
    assert _limit("/api/signals/cognitive") == \
        main._INGEST_MAX_BATCH * main._INGEST_MAX_SAMPLE_BYTES


def test_a_full_sized_real_cognitive_batch_fits_inside_it():
    """The fattest sample the sidecar sends, at a full batch (~637 KiB)."""
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
    """A subprocess, since the setting is read at import and a reload rebinds every class."""
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
    assert main.IS_PRODUCTION is False
    assert client.get("/docs").status_code == 200


@pytest.mark.parametrize("raw", ["production", "PRODUCTION", " Production "])
def test_the_production_spellings_are_recognised(raw):
    assert main._is_production(raw)[1] is True


@pytest.mark.parametrize("raw", [None, "", "   ", "development", "dev", "local", "ci"])
def test_the_development_spellings_and_the_unset_case_publish_the_docs(raw):
    """Unset stays development: it is the ordinary local state."""
    assert main._is_production(raw)[1] is False


@pytest.mark.parametrize("raw", ["prod", "PROD", "prod ", "production "])
def test_a_shortened_production_name_is_not_a_near_miss(raw):
    """`ENV=prod` is the likeliest typo; under `== "production"` it publishes the docs."""
    assert main._is_production(raw)[1] is True


@pytest.mark.parametrize("raw", ["staging", "produciton", "Production!", "1", "yes"])
def test_a_name_this_app_does_not_know_hides_the_docs_and_says_so(raw, capsys):
    """Unlike `_env_number`, the safe fallback here is publishing less, with a log line."""
    assert main._is_production(raw)[1] is True
    assert "not a name this app knows" in capsys.readouterr().out


def _env_example():
    path = os.path.join(os.path.dirname(os.path.abspath(main.__file__)),
                        ".env.example")
    return open(path, encoding="utf-8").read()


def _env_doc_block():
    """The comment block documenting ENV, and the `ENV=` line; the whole file would match `ci` in "decision"."""
    lines = _env_example().splitlines()
    i = next(n for n, line in enumerate(lines) if line.startswith("ENV="))
    start = i
    while start > 0 and lines[start - 1].startswith("#"):
        start -= 1
    return "\n".join(lines[start:i + 1])


def test_the_example_file_ships_a_value_this_app_recognises(capsys):
    """A warning on a fresh checkout is how a warning stops being read."""
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
    """Coverage, not wording; whole words, so "production" does not satisfy `prod`."""
    assert re.search(rf"\b{re.escape(name)}\b", _env_doc_block()), \
        f"{name!r} is recognised by _is_production but not named where ENV is documented"


@pytest.mark.parametrize("raw", [None, "", "development", "production", "prod"])
def test_a_name_this_app_knows_is_silent(raw, capsys):
    main._is_production(raw)
    assert capsys.readouterr().out == ""


# ─── the routes with no caller ───────────────────────────────────────────

def _public_route_paths() -> set[str]:
    """Every route handler that never resolves a caller, derived so a new one needs a budget."""
    tree = ast.parse(pathlib.Path(main.__file__).read_text(encoding="utf-8"))
    paths = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        routes = [d for d in fn.decorator_list
                  if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
                  and getattr(d.func.value, "id", None) == "app"
                  # Not `app.middleware("http")`, which is how the limiter itself is registered.
                  and d.func.attr in ("get", "post", "put", "delete")]
        if not routes:
            continue
        body = ast.unparse(fn)
        if "get_user(" in body or "_require_admin(" in body:
            continue
        paths.update(r.args[0].value for r in routes
                     if r.args and isinstance(r.args[0], ast.Constant))
    return paths


def test_every_route_with_no_caller_has_an_address_budget():
    """The other limiters key on the id `get_user` returns."""
    assert _public_route_paths() == set(main._PUBLIC_LIMITER)


def test_the_derivation_found_routes_at_all():
    """Or the comparison above passes against two empty sets."""
    assert len(_public_route_paths()) >= 5


def test_every_budgeted_path_names_a_budget_that_exists():
    """A typo'd name is a KeyError, so a 500 on a public route."""
    for path, limiter in main._PUBLIC_LIMITER.items():
        assert limiter in main._PUBLIC_RATE_LIMITS, path


def _tighten(monkeypatch, limiter="public_read", limit=2, window=60.0):
    """Shrink one budget by patching the limiter object; `_PUBLIC_RATE_LIMITS` is read at import."""
    budget = main._PUBLIC_BUDGETS[limiter]
    monkeypatch.setattr(budget, "limit", limit)
    monkeypatch.setattr(budget, "window", window)
    budget.reset()


def test_a_public_read_is_refused_once_the_address_is_over_its_allowance(monkeypatch):
    _tighten(monkeypatch)

    assert client.get(OPEN_PATH).status_code == 200
    assert client.get(OPEN_PATH).status_code == 200
    refused = client.get(OPEN_PATH)

    assert refused.status_code == 429
    # Sized from the window, not a constant.
    assert int(refused.headers["retry-after"]) >= 1


def test_the_refusal_is_readable_by_the_page_that_caused_it(monkeypatch):
    """The limiter sits inside CORS and `security_headers`, or a 429 reads as a network error."""
    _tighten(monkeypatch, limit=1)

    client.get(OPEN_PATH, headers={"Origin": ALLOWED})
    refused = client.get(OPEN_PATH, headers={"Origin": ALLOWED})

    assert refused.status_code == 429
    assert refused.headers["access-control-allow-origin"] == ALLOWED
    # `Retry-After` is not CORS-safelisted, so it must be exposed.
    assert "retry-after" in refused.headers["access-control-expose-headers"].lower()
    # The inner middleware still ran.
    assert refused.headers["X-Content-Type-Options"] == "nosniff"


def test_a_caller_cannot_mint_a_fresh_allowance_out_of_the_query_string(monkeypatch):
    """Its `user_id` limiter keys on a string the caller writes; the address is not chosen."""
    _tighten(monkeypatch, "public_generate", limit=2)
    # The handler body raises here; the allowance is spent before it runs.
    unguarded = TestClient(main.app, raise_server_exceptions=False)

    seen = [unguarded.get(f"/api/generate-question?user_id=fresh-{i}&grade=5th+Grade")
            for i in range(3)]

    assert [r.status_code for r in seen[:2]] != [429, 429], "the first two were inside it"
    assert seen[2].status_code == 429


def test_two_addresses_do_not_share_one_allowance(monkeypatch):
    _tighten(monkeypatch, limit=1)
    a = TestClient(main.app, client=("10.0.0.1", 1))
    b = TestClient(main.app, client=("10.0.0.2", 1))

    assert a.get(OPEN_PATH).status_code == 200
    assert a.get(OPEN_PATH).status_code == 429
    assert b.get(OPEN_PATH).status_code == 200


def test_the_forwarded_header_is_not_read_unless_a_proxy_is_declared(monkeypatch):
    """With no proxy declared, `X-Forwarded-For` is a header anyone can write."""
    _tighten(monkeypatch, limit=1)

    first = client.get(OPEN_PATH, headers={"X-Forwarded-For": "9.9.9.1"})
    second = client.get(OPEN_PATH, headers={"X-Forwarded-For": "9.9.9.2"})

    assert (first.status_code, second.status_code) == (200, 429)


def test_with_a_proxy_declared_the_client_is_taken_from_the_right(monkeypatch):
    """Entries are appended left to right, so only the rightmost are trusted."""
    _tighten(monkeypatch, limit=1)
    monkeypatch.setattr(main, "_TRUSTED_PROXY_HOPS", 1)

    mine = client.get(OPEN_PATH, headers={"X-Forwarded-For": "spoofed, 9.9.9.1"})
    same = client.get(OPEN_PATH, headers={"X-Forwarded-For": "other, 9.9.9.1"})
    other = client.get(OPEN_PATH, headers={"X-Forwarded-For": "9.9.9.1, 9.9.9.2"})

    assert mine.status_code == 200
    # One trusted entry, two invented prefixes: one caller, one allowance.
    assert same.status_code == 429
    assert other.status_code == 200


def test_a_short_forwarded_chain_falls_back_to_the_peer(monkeypatch):
    """Trusting the one entry present would let a caller name itself."""
    _tighten(monkeypatch, limit=1)
    monkeypatch.setattr(main, "_TRUSTED_PROXY_HOPS", 2)

    first = client.get(OPEN_PATH, headers={"X-Forwarded-For": "9.9.9.1"})
    second = client.get(OPEN_PATH, headers={"X-Forwarded-For": "9.9.9.2"})

    assert (first.status_code, second.status_code) == (200, 429)


def test_a_route_that_is_not_public_is_not_touched_by_this(monkeypatch):
    _tighten(monkeypatch, limit=1)
    for _ in range(3):
        client.get(OPEN_PATH)

    assert client.get("/api/profile/me").status_code == 401


def test_the_refusal_is_recorded_without_saying_who(monkeypatch):
    """An address is personal data no consent covers; the row names only the limiter."""
    _tighten(monkeypatch, limit=1)
    rows = []
    monkeypatch.setattr(main, "_record_security_event",
                        lambda kind, actor, subject=None, **d: rows.append((kind, actor, d)))

    client.get(OPEN_PATH)
    assert client.get(OPEN_PATH).status_code == 429

    assert rows == [("rate_limited", None, {"limiter": "public_read"})]


def test_the_probe_has_a_budget_of_its_own():
    """The probe is polled per open lesson and must not lose to an unrelated burst."""
    assert main._PUBLIC_LIMITER["/api/eeg/health"] != \
        main._PUBLIC_LIMITER["/api/questions"]


def test_exhausting_the_question_bank_does_not_refuse_the_health_probe(monkeypatch):
    _tighten(monkeypatch, "public_read", limit=1)

    assert client.get(OPEN_PATH).status_code == 200
    assert client.get(OPEN_PATH).status_code == 429
    # Its body may fail without a sidecar; only an edge refusal is asserted against.
    assert client.get("/api/eeg/health").status_code != 429


def test_the_audit_write_does_not_stop_the_event_loop(monkeypatch):
    """Middleware runs on the loop; the insert sees the flag only if a coroutine could set it."""
    released = threading.Event()
    observed = []

    class _Blocking:
        def table(self, _name):
            class _Q:
                def insert(self, _obj):
                    observed.append(released.wait(2.0))
                    return self

                def execute(self):
                    return type("R", (), {"data": []})()
            return _Q()

    monkeypatch.setattr(main, "supabase", _Blocking())
    _tighten(monkeypatch, "public_read", limit=1)
    main._security_event_seen.clear()

    class _Req:
        url = type("U", (), {"path": OPEN_PATH})()
        headers: dict = {}
        client = type("C", (), {"host": "10.1.1.1"})()

    async def _call_next(_request):
        return "not reached"

    async def _drive():
        await main.public_rate_limit(_Req(), _call_next)      # inside the allowance
        asyncio.ensure_future(_release())
        return await main.public_rate_limit(_Req(), _call_next)

    async def _release():
        released.set()

    refused = asyncio.run(_drive())

    assert refused.status_code == 429
    assert observed == [True], \
        "the audit insert ran on the event loop, so nothing else could run"
