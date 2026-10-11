from fastapi import FastAPI, Request, HTTPException, Path, Body, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.routing import Match
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, field_validator
import os, math, re, random, secrets, threading, time, collections, contextlib, statistics
import anyio.to_thread
import httpx
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import asynccontextmanager
from datetime import datetime, date, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from supabase_client import get_client, pooled_http
from postgrest.types import ReturnMethod  # supabase pins this sibling
from typing import Any, Literal, NamedTuple
from uuid import UUID

import LLM_topic_decider
import chart_archive
import eeg_client
import signal_mapping
import eeg_poller
import llm_client
import ops_metrics
import grade_levels
import kit_gate
import safe_solve
from env_config import env_number

load_dotenv()

SUPABASE_URL     = os.getenv("SUPABASE_URL")
SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
BACKEND_PORT     = env_number("BACKEND_PORT", 8000, int, minimum=1)

if not SUPABASE_URL or not SERVICE_ROLE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")

supabase = get_client(SUPABASE_URL, SERVICE_ROLE_KEY)

# anyio's default of 40 threads runs every `def` route, and LLM waiters alone can hold 38. Not
# bounded by the Supabase pool's 128: with the worker pools it can exceed it (supabase_client.py).
_WORKER_THREADS = 96


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Process-lifetime hooks. Everything before the yield is startup."""
    anyio.to_thread.current_default_thread_limiter().total_tokens = _WORKER_THREADS
    start_stale_sweeper()
    safe_solve.start_startup_probe()
    # A lambda, so the flusher uses whatever client `supabase` names at flush time.
    ops_metrics.start(lambda: supabase)
    yield
    # Join printing daemon threads first: a print during teardown is a fatal stdout-lock abort.
    try:
        eeg_poller.stop_all()
    finally:
        try:
            stop_stale_sweeper()
        finally:
            # Every step runs even if one raises; the last failure is re-raised.
            failure = None
            for shutdown in (ops_metrics.stop,
                             safe_solve.stop_startup_probe,
                             _shutdown_strategy_pool,
                             _shutdown_chart_summary_pool,
                             _shutdown_prefetch_pool,
                             chart_archive.shutdown_pool):
                try:
                    shutdown()
                except Exception as e:  # noqa: BLE001 - every step must still run
                    print(f"[lifespan:shutdown] {shutdown.__name__}: {e}")
                    failure = e
            if failure is not None:
                raise failure


# ─── the network edge ─────────────────────────────────────────────────────
# Mirrors `EEGResearch/src/app/main.py`. See CLAUDE.md, "The network edge".

def _env_list(name: str, default):
    """A comma-separated setting; blank or whitespace-only reads as unset, not [""]."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return list(default)
    return [part.strip() for part in raw.split(",") if part.strip()]


# `ENV` decides whether the API docs are published. Both sides are named: an
# unrecognised value is treated as production (publish less); unset is development.
_PRODUCTION_ENVS  = {"production", "prod"}
_DEVELOPMENT_ENVS = {"development", "dev", "local", "test", "ci"}


def _is_production(raw):
    name = (raw or "").strip().lower()
    if not name:
        return "development", False
    if name in _PRODUCTION_ENVS:
        return name, True
    if name in _DEVELOPMENT_ENVS:
        return name, False
    print(f"[config] ENV={raw!r} is not a name this app knows; "
          f"treating it as production and leaving the API docs unpublished")
    return name, True


ENV, IS_PRODUCTION = _is_production(os.getenv("ENV"))
ALLOWED_ORIGINS = _env_list(
    "ALLOWED_ORIGINS", ("http://localhost:5173", "http://127.0.0.1:5173"))

# Named once, so the paths the CSP exempts are the paths switched off in production.
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect")

app = FastAPI(
    title="AdaptiveLearning API",
    lifespan=_lifespan,
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
)


# Body cap for ordinary endpoints (bytes); nothing here accepts an upload.
_MAX_BODY_BYTES = int(env_number("MAX_BODY_BYTES", 256 * 1024, int, minimum=4096))

# Ingest cap is derived: INGEST_MAX_BATCH x this per-sample allowance (~3x a measured sample).
_INGEST_MAX_SAMPLE_BYTES = int(
    env_number("INGEST_MAX_SAMPLE_BYTES", 4096, int, minimum=512))
_INGEST_PATH_PREFIX = "/api/signals/"


class MaxBodySizeMiddleware:
    """Refuse an oversized request body, by `Content-Length` and by bytes arrived.

    Pure ASGI so the body is never read. Counting arrival catches chunked
    uploads, which send no `Content-Length`.
    """

    def __init__(self, app):
        self.app = app

    def _limit(self, path: str) -> int:
        # Read at request time so both bounds come from one value of `_INGEST_MAX_BATCH`.
        if path.startswith(_INGEST_PATH_PREFIX):
            return _INGEST_MAX_BATCH * _INGEST_MAX_SAMPLE_BYTES
        return _MAX_BODY_BYTES

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        limit = self._limit(scope.get("path", ""))

        declared = None
        for key, value in scope.get("headers", ()):
            if key == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = None
                break
        if declared is not None and declared > limit:
            return await self._refuse(send, limit)

        received = 0
        too_large = False

        async def counting_receive():
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    too_large = True
                    # End the body here; the app's parse-failure response is replaced below.
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        started = False

        async def guarded_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                if too_large:
                    # The app answered a truncated body; replace its 422 with the 413.
                    return await self._refuse(send, limit)
                started = True
            elif too_large and not started:
                return
            await send(message)

        await self.app(scope, counting_receive, guarded_send)

    @staticmethod
    async def _refuse(send, limit: int):
        body = (b'{"detail":"Request body is too large (limit '
                + str(limit).encode() + b' bytes)."}')
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


# Innermost, so CORS still wraps its 413 and the page can read the refusal.
app.add_middleware(MaxBodySizeMiddleware)


# ─── the four routes with no caller ──────────────────────────────────────
# The per-user limiters never run on these. `test_network_edge.py` derives the
# set from the module, so a new public route fails until budgeted.
_PUBLIC_LIMITER = {
    "/api/questions":         "public_read",
    "/api/questions/count":   "public_read",
    "/api/topics":            "public_read",
    # Own bucket: polled every 5 s per open lesson, so a shared burst would starve it.
    "/api/eeg/health":        "public_probe",
}

# Routes that resolve their caller *and* keep an address budget: sign-up is
# self-service, so a per-student limit alone is a new allowance per account.
# Separate so `_PUBLIC_LIMITER` means exactly "no caller" (tested both ways).
_AUTHENTICATED_ADDRESS_LIMITER = {
    "/api/generate-question": "public_generate",
}

# An address is a school behind one NAT, not a student: 60 students polling
# health is 720/min at rest. These refuse runaway clients, not a class.
_PUBLIC_RATE_LIMITS = {
    "public_generate": (
        env_number("PUBLIC_GENERATE_RATE_LIMIT", 600, int, minimum=1),
        env_number("PUBLIC_GENERATE_RATE_WINDOW", 60.0, float, minimum=1.0)),
    "public_read": (
        env_number("PUBLIC_READ_RATE_LIMIT", 1800, int, minimum=1),
        env_number("PUBLIC_READ_RATE_WINDOW", 60.0, float, minimum=1.0)),
    # 12/min per open lesson, so ~150 lessons behind one address.
    "public_probe": (
        env_number("PUBLIC_PROBE_RATE_LIMIT", 1800, int, minimum=1),
        env_number("PUBLIC_PROBE_RATE_WINDOW", 60.0, float, minimum=1.0)),
}

# Proxies in front of this process; X-Forwarded-For is read only this far from the
# right. 0 (default) ignores the header, so a caller cannot mint identities.
_TRUSTED_PROXY_HOPS = int(env_number("TRUSTED_PROXY_HOPS", 0, int, minimum=0))

class _SlidingWindowLimiter:
    """`limit` hits per `window` seconds per key, on a monotonic clock.

    Answers rather than raising, so middleware can use it. `hits[key]` is a deque of
    `[time, count]`, one per admitted call, oldest first. `sweep_at` is seeded from
    `monotonic()`, not 0.0, whose reference point is undefined.
    """

    def __init__(self, name: str, limit: int, window: float,
                 sweep_above: int = 1024, sweep_every: float = 60.0):
        self.name = name
        self.limit = limit
        self.window = window
        self._sweep_above = sweep_above
        self._sweep_every = sweep_every
        self.hits: dict[str, collections.deque[list]] = {}
        self.sweep_at = time.monotonic()
        self._lock = threading.Lock()

    def check(self, key: str, cost: int = 1) -> int | None:
        """Seconds to wait, or `None` while the caller is inside its allowance; `cost` hits at once, all or none."""
        with self._lock:
            # Read under the lock, so entries are appended in time order.
            now = time.monotonic()
            if (len(self.hits) > self._sweep_above
                    and now - self.sweep_at >= self._sweep_every):
                self.sweep_at = now
                for stale in [k for k, q in self.hits.items()
                              if not q or now - q[-1][0] >= self.window]:
                    del self.hits[stale]

            q = self.hits.setdefault(key, collections.deque())
            while q and now - q[0][0] >= self.window:
                q.popleft()
            used = sum(n for _t, n in q)
            if used + cost > self.limit:
                # The entry whose expiry frees enough room decides the wait.
                need, freeing = used + cost - self.limit, now
                for t, n in q:
                    need, freeing = need - n, t
                    if need <= 0:
                        break
                return max(1, int(self.window - (now - freeing)) + 1)
            q.append([now, cost])
            return None

    def release(self, key: str, count: int = 1) -> None:
        """Give back the caller's `count` newest hits, for work admitted but never done."""
        with self._lock:
            q = self.hits.get(key)
            while q and count > 0:
                take = min(count, q[-1][1])
                q[-1][1] -= take
                count -= take
                if q[-1][1] == 0:
                    q.pop()

    def reset(self) -> None:
        """Forget every caller and re-arm the sweep. For tests."""
        with self._lock:
            self.hits.clear()
            self.sweep_at = time.monotonic()


_PUBLIC_SWEEP_ABOVE = 4096
# One limiter (and lock) per budget, so the health probe never contends with generation.
_PUBLIC_BUDGETS = {
    name: _SlidingWindowLimiter(name, limit, window, sweep_above=_PUBLIC_SWEEP_ABOVE)
    for name, (limit, window) in _PUBLIC_RATE_LIMITS.items()
}


_proxy_hops_warned = False
_proxy_chain_short_warned = False


def _client_address(request: Request) -> str:
    """The caller, for a route where there is no account to name them by."""
    global _proxy_hops_warned, _proxy_chain_short_warned
    if _TRUSTED_PROXY_HOPS:
        chain = [p.strip() for p in
                 request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(chain) >= _TRUSTED_PROXY_HOPS:
            return chain[-_TRUSTED_PROXY_HOPS]
        # Too few entries: fall back to the peer (a shared bucket, never a bypass).
        # No header at all is a direct hit (a health check), not evidence about the setting.
        if chain and not _proxy_chain_short_warned:
            _proxy_chain_short_warned = True
            print(f"[config] a request's X-Forwarded-For had {len(chain)} of the "
                  f"{_TRUSTED_PROXY_HOPS} entries TRUSTED_PROXY_HOPS expects: set too high, or a "
                  "request reached this server around a proxy; either way it shares one address budget")
    elif not _proxy_hops_warned and request.headers.get("x-forwarded-for"):
        # Once, and not at boot: a proxy is only visible in the requests it forwards.
        _proxy_hops_warned = True
        print("[config] a request carried X-Forwarded-For but TRUSTED_PROXY_HOPS is 0: behind a "
              "proxy, every school shares one address budget, class joins included")
    client = request.client
    # No peer shares one bucket rather than being unlimited.
    return client.host if client and client.host else "unknown"


def _public_rate_limited(limiter: str, address: str) -> int | None:
    """Seconds to wait, or `None` while the caller is inside its allowance."""
    return _PUBLIC_BUDGETS[limiter].check(address)


# Inside `security_headers` and CORS, so the page can read its 429.
@app.middleware("http")
async def public_rate_limit(request: Request, call_next):
    path = request.url.path
    limiter = _PUBLIC_LIMITER.get(path) or _AUTHENTICATED_ADDRESS_LIMITER.get(path)
    if limiter is None:
        return await call_next(request)

    refused_after = _public_rate_limited(limiter, _client_address(request))
    if refused_after is None:
        return await call_next(request)

    # Never record the address (personal data). Threadpool: a sync insert on the
    # event loop would stall every other request.
    await run_in_threadpool(_record_security_event, "rate_limited", None,
                            limiter=limiter)
    return JSONResponse(
        {"detail": "Too many requests. Slow down."},
        status_code=429, headers={"Retry-After": str(refused_after)})


def _route_template(scope) -> str:
    """The matched route's path template (`/api/sessions/{session_id}/end`), never the raw path."""
    route = scope.get("route")
    if route is not None:
        return route.path
    # Refused before routing (a 429 here, a declared-length 413): match it ourselves.
    partial = None
    for r in app.router.routes:
        match, _ = r.matches(scope)
        if match is Match.FULL:
            return r.path
        if match is Match.PARTIAL and partial is None:
            partial = r.path
    return partial or "<unmatched>"


def _count_refusal(request: Request, status: int) -> None:
    # Status, method and route template: never an id, a caller or an address. 404 is a typo.
    if status >= 400 and status != 404:
        ops_metrics.bump("refusal", f"{status} {request.method} {_route_template(request.scope)}")


# Also counts refusals: outside `public_rate_limit` and the size cap, it sees their 429s and 413s.
@app.middleware("http")
async def security_headers(request: Request, call_next):
    try:
        response = await call_next(request)
    except Exception:
        _count_refusal(request, 500)
        raise
    _count_refusal(request, response.status_code)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Cache-Control"] = "no-store"
    # The webcam opens on the frontend origin; this one has no document to use it.
    response.headers["Permissions-Policy"] = \
        "camera=(), microphone=(), geolocation=(), payment=()"
    # This server serves no HTML, so its responses are not a document. FastAPI's
    # docs are exempt (off in production).
    if not request.url.path.startswith(_DOCS_PATHS):
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'none'")
    return response


# Added last, so outermost. No credentials: auth is a bearer header, not cookies.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    # Not CORS-safelisted; `apiFetch` reads it to size its retry.
    expose_headers=["Retry-After"],
    # Preflights are cached per URL; 7200 s is Chrome's cap (Starlette's default is 600).
    max_age=7200,
)


# ─── helpers ──────────────────────────────────────────────────────────────

# Seconds for the GoTrue check behind every authenticated request; unbounded, a stall holds a worker.
AUTH_CHECK_TIMEOUT = env_number("AUTH_CHECK_TIMEOUT", 5.0, float, minimum=0.5)

# Pooled, so each request skips a TCP+TLS handshake. Not the Supabase client: its hook
# rewrites every /auth/v1/ timeout, which would silently override AUTH_CHECK_TIMEOUT.
_AUTH_HTTP = pooled_http(AUTH_CHECK_TIMEOUT)

# RFC 6750's b64token. Anything else cannot be a token, and httpx raises on a non-ASCII header.
_BEARER_TOKEN = re.compile(r"[A-Za-z0-9\-._~+/]+=*")


def get_user(request: Request):
    token = request.headers.get("authorization", "").replace("Bearer ", "").strip()
    if not token:
        raise HTTPException(401, "Missing token")
    if not _BEARER_TOKEN.fullmatch(token):
        raise HTTPException(401, "Invalid token")
    try:
        resp = _AUTH_HTTP.get(f"{SUPABASE_URL}/auth/v1/user", headers={
            "Authorization": f"Bearer {token}",
            "apikey": SERVICE_ROLE_KEY
        })
    except httpx.RequestError as e:
        # 503, not 401: the token was never judged, and a 401 reads as an expired session.
        print(f"[auth] could not reach the auth server: {type(e).__name__}")
        raise HTTPException(503, "Sign-in could not be checked right now")
    if resp.status_code != 200:
        raise HTTPException(401, "Invalid token")
    return resp.json()

# ─── the code a child gives a parent ─────────────────────────────────────
# A credential, so `secrets` (CSPRNG), never `random`. No O/0/I/1:
# a child reads it aloud. The TTL, single use and limiter are the real controls.
_LINK_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_LINK_CODE_LEN = 8
_LINK_CODE_TTL_SEC = 30 * 60


def _new_link_code() -> str:
    return "".join(secrets.choice(_LINK_CODE_ALPHABET)
                   for _ in range(_LINK_CODE_LEN))


# A class join code is guessable by volume, so the same alphabet and length. Older 6-character
# codes still join until their teacher replaces them. The limits are the real control.
_JOIN_CODE_LEN = 8
# Attempts per account per hour, successful or not.
_JOIN_CODE_LIMITER = _SlidingWindowLimiter("class_join", 10, 3600.0)
# Wrong codes per address per hour, from signed-in students only: accounts are self-service, and a
# school's first morning of correct codes must not lock it out. 1000 misses an hour against 32^8 codes.
_JOIN_MISS_LIMITER = _SlidingWindowLimiter(
    "class_join_address", env_number("CLASS_JOIN_MISSES_PER_ADDRESS", 1000, int, minimum=1), 3600.0)


def _new_join_code() -> str:
    return "".join(secrets.choice(_LINK_CODE_ALPHABET) for _ in range(_JOIN_CODE_LEN))


def _unique_ids(values) -> list:
    """The ids worth querying for: in order, without blanks or repeats."""
    return [v for v in dict.fromkeys(values) if v]


# ─── the security log ────────────────────────────────────────────────────

# Seconds before the same cooled event is worth another row (a limiter fires per request).
_SECURITY_EVENT_COOLDOWN_SEC = env_number(
    "SECURITY_EVENT_COOLDOWN_SECONDS", 300, float, minimum=0)
# Cooled kind -> `detail` fields in the cooldown key, so one limiter cannot mask another.
_COOLED_KINDS = {"rate_limited": ("limiter",)}

_security_event_seen: dict[tuple, float] = {}
_security_event_lock = threading.Lock()


def _record_security_event(kind: str, actor_user_id: str | None,
                           subject_user_id: str | None = None,
                           **detail) -> None:
    """Append one row to `security_events`. Never raises.

    `detail` is context, never content: no readings, request bodies or IPs.
    Values are stringified and truncated, since some come from a client.
    """
    cooled_on = _COOLED_KINDS.get(kind)
    if cooled_on is not None and _SECURITY_EVENT_COOLDOWN_SEC > 0:
        key = (kind, actor_user_id, *(str(detail.get(f)) for f in cooled_on))
        now = time.monotonic()
        with _security_event_lock:
            last = _security_event_seen.get(key)
            if last is not None and now - last < _SECURITY_EVENT_COOLDOWN_SEC:
                return
            _security_event_seen[key] = now
            # Bounded; dropping the oldest costs at most one extra row.
            if len(_security_event_seen) > 4096:
                for stale in sorted(_security_event_seen,
                                    key=_security_event_seen.get)[:1024]:
                    del _security_event_seen[stale]

    try:
        supabase.table("security_events").insert({
            "kind": kind,
            "actor_user_id": actor_user_id,
            "subject_user_id": subject_user_id,
            "detail": {k: str(v)[:200] for k, v in detail.items() if v is not None},
        }).execute()
    except Exception as e:                                     # noqa: BLE001
        # This log line is the only trace of a lost event.
        print(f"[security] could not record {kind}: {e}")


def _group_by_user(rows) -> dict[str, list]:
    """Rows bucketed by `user_id`, order preserved (`_open_sessions_many` takes `[0]`)."""
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r.get("user_id"), []).append(r)
    return grouped


# PostgREST codes meaning "no such row": `.single()` matched none, or the id cannot be a uuid.
_NO_ROW_CODES = frozenset({"PGRST116", "22P02"})

# What a 503 for a failed read asks the client to wait, in seconds; `apiFetch` then retries a GET itself.
_READ_RETRY_AFTER_SEC = 2


def _names_no_row(exc: Exception) -> bool:
    """Whether a failed read means the row does not exist, rather than that the read failed."""
    return getattr(exc, "code", None) in _NO_ROW_CODES


def _read_failed(detail: str) -> HTTPException:
    """503 for a read that failed: a 403 or 404 would claim an answer nobody read."""
    return HTTPException(503, detail, headers={"Retry-After": str(_READ_RETRY_AFTER_SEC)})


def _missing_rpc(e: Exception, function: str, migration: str, consequence: str) -> bool:
    """Whether `e` is PostgREST's PGRST202 for `function`; if so, logs the migration and the site's `consequence`.

    One naming no function is the call the site made; one naming another is not this one.
    The caller keeps its own raise or return: a missing function is one more failed read.
    """
    if "PGRST202" not in str(e):
        return False
    message = getattr(e, "message", None)     # APIError's own field; the hint can name others
    named = re.findall(r"\bpublic\.(\w+)", message if isinstance(message, str) else str(e))
    if named and function not in named:
        return False
    print(f"[rpc] {function} is missing from the database -- apply {migration}; {consequence}: {e}")
    return True


def _row_or_404(query, what: str) -> dict:
    """Run a `.single()` lookup: 404 when no row matches, 503 when the read fails.

    Not for a lookup where absence is a legitimate answer.
    """
    try:
        res = query.single().execute()
    except HTTPException:
        raise
    except Exception as e:                                     # noqa: BLE001
        if _names_no_row(e):
            raise HTTPException(404, f"{what} not found")
        print(f"[row_or_404] {what}: {e}")
        raise _read_failed(f"{what} could not be loaded; try again")
    if not res.data:
        raise HTTPException(404, f"{what} not found")
    return res.data

# `admin` cannot be chosen at sign-up; `handle_new_user` whitelists the other three.
ADMIN_ROLE = "admin"
SELF_SERVICE_ROLES = ("student", "teacher", "parent")


def _role(uid: str) -> str:
    """A caller's role from `profiles`, never the client-writable `user_metadata`.

    Fails closed to 'student' on a failed read.
    """
    return _role_of(_profile(uid))


def _role_of(profile: dict | None) -> str:
    """The one rule for a role: the stored column, else 'student'."""
    return (profile or {}).get("role") or "student"


def _role_or_raise(uid: str) -> str | None:
    """The stored role, `None` with no profile row; a failed read raises.

    For a gate that must admit only a role it actually read: `_role` answers "student" to both.
    """
    rows = supabase.table("profiles").select("role").eq("id", uid).execute().data
    return _role_of(rows[0]) if rows else None


def _placeholder_profile(uid: str) -> dict:
    """Stand-in for an unreadable or missing profile, with new-account preference values.

    Omitted preferences would render as "the student turned this off".
    """
    return {"id": uid, "display_name": "Student", "email": "", "role": "student",
            "grade_level": None, "difficulty_bias": 0,
            "session_duration_minutes": 15, "practice_reminders": True}


def _profile(uid: str) -> dict:
    try:
        p = supabase.table("profiles").select("*").eq("id", uid).single().execute()
        if p.data:
            return p.data
    except Exception:
        pass
    return _placeholder_profile(uid)


def _saved_grade(uid: str) -> str | None:
    """The student's saved grade, read alone: generation asks per question. None if unreadable."""
    try:
        row = supabase.table("profiles").select("grade_level").eq("id", uid).single().execute()
        return (row.data or {}).get("grade_level")
    except Exception as e:                                     # noqa: BLE001
        print(f"[grade] could not read {uid[:8]}'s saved grade: {e}")
        return None


def _served_grade(uid: str, sent: str | None = None, profile: dict | None = None) -> str:
    """The grade a student is served: the one sent, else their saved one, else `DEFAULT_GRADE`.

    `profile` saves a read when the caller already holds it; an unreadable one reads as no grade.
    """
    if sent:
        return sent
    saved = profile.get("grade_level") if profile is not None else _saved_grade(uid)
    return saved or grade_levels.DEFAULT_GRADE


def _profiles_many(uids) -> dict[str, dict]:
    """`_profile` for a roster in one query; absent or unreadable rows get the placeholder."""
    ids = _unique_ids(uids)
    if not ids:
        return {}
    rows = []
    try:
        rows = supabase.table("profiles").select("*").in_("id", ids).execute().data or []
    except Exception as e:                                     # noqa: BLE001
        print(f"[profiles] could not batch-read {len(ids)} profiles: {e}")
    found = {r["id"]: r for r in rows if r.get("id")}
    return {uid: found.get(uid) or _placeholder_profile(uid) for uid in ids}


# ─── biosignal reporting ─────────────────────────────────────────────────

# Session rows per weekly report; ordered newest first, so the cap trims the OLDEST.
_SESSION_ROW_CAP = 100


def _avg(values):
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 2)


def _utc_now() -> datetime:
    """Timezone-aware UTC; `datetime.utcnow()` is naive and compares wrongly with timestamptz."""
    return datetime.now(timezone.utc)


# ── the school-year retention window ────────────────────────────────────────
# Only `open` and `not_enforced` record; the rest are distinct reasons a parent
# is told. See CLAUDE.md, "Recording needs consent and an open school year".
WINDOW_OPEN = "open"
WINDOW_NOT_ENFORCED = "not_enforced"
WINDOW_BEFORE = "before_year"
WINDOW_AFTER = "after_year"
WINDOW_UNCONFIGURED = "unconfigured"
WINDOW_UNREADABLE = "unreadable"

class _WindowMeaning(NamedTuple):
    """What a window state means, in the three places that need to know."""
    records: bool
    # Lowercase: ingest embeds it beside "eeg not consented".
    reason: str | None
    # A key; the frontend picks its own copy.
    stopped_reason: str | None


# `test_every_window_state_has_a_meaning` enforces one row per state.
_WINDOW_STATES = {
    WINDOW_OPEN: _WindowMeaning(True, None, None),
    WINDOW_NOT_ENFORCED: _WindowMeaning(True, None, None),
    WINDOW_BEFORE: _WindowMeaning(
        False, "recording has not started for this school year",
        "school_year_not_started"),
    WINDOW_AFTER: _WindowMeaning(
        False, "the school year has ended", "school_year_ended"),
    WINDOW_UNCONFIGURED: _WindowMeaning(
        False, "no school year is configured, so nothing is recorded",
        "school_year_unconfigured"),
    WINDOW_UNREADABLE: _WindowMeaning(
        False, "could not check the school year, so nothing was recorded",
        "school_year_unknown"),
}

# Derived; a state missing from the table denies (fail closed).
_WINDOW_DENIED = {k for k, v in _WINDOW_STATES.items() if not v.records}


# Window row cache (s). Consent is never cached: a withdrawal must apply mid-lesson.
_RETENTION_TTL_SECONDS = 30.0
_retention_cached: tuple[float, dict] | None = None
_retention_lock = threading.Lock()


def _retention_cache_clear() -> None:
    """Drop the cached window. For tests, and for anything that edits the row."""
    global _retention_cached
    with _retention_lock:
        _retention_cached = None


def _retention_window() -> dict:
    """The configured school year and today's `state` in it, plus the raw dates.

    Fails closed: unreadable or unconfigured records nothing. Compared in the
    school's timezone, not UTC.
    """
    global _retention_cached
    now = time.monotonic()
    with _retention_lock:
        cached = _retention_cached
    if cached and now < cached[0]:
        # Only the row is cached; the state depends on today's date.
        return _resolve_window(cached[1])

    try:
        rows = supabase.table("retention_window").select("*").limit(1).execute().data or []
    except Exception as e:
        print(f"[retention:read] {e}")
        # Failures are not cached.
        return {"state": WINDOW_UNREADABLE, "starts_on": None, "ends_on": None,
                "timezone": None}
    if not rows:
        # Not cached: the first row is often inserted from the SQL editor, which clears no cache.
        return {"state": WINDOW_UNCONFIGURED, "starts_on": None, "ends_on": None,
                "timezone": None}

    with _retention_lock:
        _retention_cached = (now + _RETENTION_TTL_SECONDS, rows[0])
    return _resolve_window(rows[0])


def _expiry_cutoff(starts_on, ends_on, today: date, enforced=True) -> date | None:
    """`expired_signal_cutoff`'s rule in Python, its only copy here: None without usable dates.

    None for an unenforced year, as in the SQL; `ends_on` expires only once it has passed.
    """
    if enforced is False:
        return None
    try:
        starts, ends = date.fromisoformat(str(starts_on)), date.fromisoformat(str(ends_on))
    except ValueError:
        return None                 # no dates: the SQL cutoff is NULL and deletes nothing
    return ends if today > ends else starts - timedelta(days=1)


def _expired_through(today: date) -> date | None:
    """The last day whose raw signal rows the expiry job may have deleted, or None.

    Unreadable is `date.max`: unknown, so every day may have expired.
    """
    window = _retention_window()
    if window.get("state") == WINDOW_UNREADABLE:
        return date.max
    return _expiry_cutoff(window.get("starts_on"), window.get("ends_on"), today,
                          enforced=window.get("state") != WINDOW_NOT_ENFORCED)


def _resolve_window(row: dict) -> dict:
    """Today's position in a window row (the cache stores the row, not this verdict)."""
    name = row.get("timezone") or "UTC"
    try:
        tz = ZoneInfo(name)
    except Exception:
        # Deny rather than fall back to UTC, which would shift every boundary silently.
        print(f"[retention:tz] unknown timezone {name!r} -- recording denied")
        return {"state": WINDOW_UNREADABLE, "starts_on": row.get("starts_on"),
                "ends_on": row.get("ends_on"), "timezone": name}

    today = _utc_now().astimezone(tz).date()
    starts, ends = row.get("starts_on"), row.get("ends_on")

    # Before the dates, which are null on an unenforced row. `is False`, not
    # falsiness: a missing column keeps the gate on.
    if row.get("enforced") is False:
        return {"state": WINDOW_NOT_ENFORCED, "starts_on": starts,
                "ends_on": ends, "timezone": name}

    try:
        starts_d = date.fromisoformat(str(starts))
        ends_d = date.fromisoformat(str(ends))
    except (TypeError, ValueError):
        if starts is None and ends is None:
            # Enforced with no dates is a half-finished edit, not unbounded.
            print("[retention:dates] enforced with no dates set -- recording denied")
            return {"state": WINDOW_UNCONFIGURED, "starts_on": None,
                    "ends_on": None, "timezone": name}
        print(f"[retention:dates] unparseable window {starts!r}..{ends!r}")
        return {"state": WINDOW_UNREADABLE, "starts_on": starts, "ends_on": ends,
                "timezone": name}

    if today < starts_d:
        state = WINDOW_BEFORE
    elif today > ends_d:
        # Inclusive of ends_on: the last day of school is still a school day.
        state = WINDOW_AFTER
    else:
        state = WINDOW_OPEN
    return {"state": state, "starts_on": starts, "ends_on": ends, "timezone": name}


# ─── feature flags ───────────────────────────────────────────────────────
# Defaults are the pre-table behaviour, used for absent keys and failed reads.
# Also the whitelist: unknown keys are ignored on read and refused on write.
_FEATURE_FLAG_DEFAULTS = {
    "strategy_llm_enabled": True,
    "chart_summary_llm_enabled": True,
    "recording_eeg_enabled": True,
    "recording_heart_enabled": True,
    "recording_camera_enabled": True,
    "consent_enforcement_enabled": True,
}

CONSENT_ENFORCEMENT_FLAG = "consent_enforcement_enabled"

# Rows only; bypass expiry is checked against the clock on every read.
_FEATURE_FLAGS_TTL_SECONDS = 30.0
_feature_flags_cached: tuple[float, dict] | None = None
_feature_flags_lock = threading.Lock()


def _feature_flags_cache_clear() -> None:
    """Drop the cached flags. For tests, and for anything that writes a flag."""
    global _feature_flags_cached
    with _feature_flags_lock:
        _feature_flags_cached = None


def _feature_flags() -> dict:
    """Every known flag, as `{key: {"enabled": bool, "bypass_until": str|None}}`.

    A read error answers the declared defaults (so consent stays enforced);
    unknown keys are dropped.
    """
    global _feature_flags_cached
    now = time.monotonic()
    with _feature_flags_lock:
        cached = _feature_flags_cached
    if cached and now < cached[0]:
        return cached[1]

    flags = {k: {"enabled": v, "bypass_until": None}
             for k, v in _FEATURE_FLAG_DEFAULTS.items()}
    try:
        rows = supabase.table("feature_flags").select("*").execute().data or []
    except Exception as e:
        print(f"[flags:read] {e}")
        # Failures are not cached.
        return flags

    for row in rows:
        key = row.get("key")
        if key in flags:
            flags[key] = {"enabled": bool(row.get("enabled")),
                          "bypass_until": row.get("bypass_until")}

    with _feature_flags_lock:
        _feature_flags_cached = (now + _FEATURE_FLAGS_TTL_SECONDS, flags)
    return flags


def _consent_enforcement_active(flags: dict | None = None) -> bool:
    """Whether per-student consent gates recording right now.

    True unless a bypass is live; expiry is checked on every read, and a bypass
    with no `bypass_until` has already expired.
    """
    flags = flags if flags is not None else _feature_flags()
    flag = flags.get(CONSENT_ENFORCEMENT_FLAG) or {}
    if flag.get("enabled", True):
        return True
    until = _parse_ts(flag.get("bypass_until"))
    if until is None:
        return True
    return _utc_now() >= until


def _school_timezone() -> tzinfo:
    """The school's zone, for bucketing report days; degrades to UTC (reporting fails open).

    Last resort is `timezone.utc`: `ZoneInfo("UTC")` raises on Windows without `tzdata`.
    """
    try:
        return ZoneInfo(_retention_window().get("timezone") or "UTC")
    except Exception:
        try:
            return ZoneInfo("UTC")
        except Exception:
            return timezone.utc


def _tz_name(tz: tzinfo) -> str:
    """The zone's IANA name for an RPC; `timezone.utc` (the last-resort fallback) has no `.key`."""
    return getattr(tz, "key", None) or "UTC"


def _school_timezone_name() -> str:
    """`_school_timezone()`'s name: a mistyped zone reaches Postgres as UTC, never raw."""
    return _tz_name(_school_timezone())


def _school_date(ts, tz: tzinfo) -> date | None:
    """The calendar day `ts` falls on *at the school*. None if unparseable.

    Never `str(ts)[:10]`: PostgREST returns UTC. Feeds both the rollup and
    `_school_day`, so they agree on a reading's day.
    """
    parsed = _parse_ts(ts)
    return None if parsed is None else parsed.astimezone(tz).date()


def _school_day(ts, tz: tzinfo) -> str:
    """`_school_date` as YYYY-MM-DD; "" for unparseable, so it joins no bucket."""
    resolved = _school_date(ts, tz)
    return "" if resolved is None else resolved.isoformat()


def _missed_a_school_day(last: date, day: date) -> bool:
    """Whether a weekday falls strictly between `last` and `day`. Weekends never break a streak.

    Holidays are not modelled: `retention_window` holds a term, not a calendar.
    """
    gap = (day - last).days
    if gap > 7:
        return True
    return any((last + timedelta(days=i)).weekday() < 5 for i in range(1, gap))


def _streak_update(stats: dict, started_at, tz: tzinfo) -> dict:
    """The streak fields after crediting a session that began at `started_at`.

    A streak counts school days with an answered session and no weekday missed between them,
    keyed on the day the work began (the sweep closes sessions hours later). A weekend session
    counts; an older session changes nothing.
    """
    day, last = _school_date(started_at, tz), _school_date(stats.get("last_session_at"), tz)
    current, best = stats.get("current_streak") or 0, stats.get("best_streak") or 0
    if day is None or (last is not None and day < last):
        return {}
    if last is not None and day == last:
        current = max(current, 1)
    elif last is not None and not _missed_a_school_day(last, day):
        current += 1
    else:
        current = 1
    return {"current_streak": current, "best_streak": max(best, current),
            "last_session_at": _parse_ts(started_at).isoformat()}


def _live_streak(stats: dict, tz: tzinfo) -> dict:
    """`stats` with `current_streak` 0 once a whole weekday has passed without a session.

    Today is not missed yet: there is still time to practise.
    """
    last = _school_date(stats.get("last_session_at"), tz)
    today = _utc_now().astimezone(tz).date()
    if stats.get("current_streak") and (last is None or _missed_a_school_day(last, today)):
        return {**stats, "current_streak": 0}
    return stats


def _credit_session_to_user_stats(user_id: str, total_q: int, correct: int,
                                  started_at=None) -> None:
    """Add one closed session's answers to the lifetime totals and the streak. Never raises.

    `last_session_at` is when the latest credited session began; the streak is read from it.
    """
    if not total_q:
        # No zero row: an absent `user_stats` row already reads as "no data yet".
        return
    try:
        existing = supabase.table("user_stats").select("*").eq("user_id", user_id).execute()
        now = _utc_now().isoformat()
        streak = _streak_update(existing.data[0] if existing.data else {},
                                started_at or now, _school_timezone())
        if existing.data:
            s = existing.data[0]
            supabase.table("user_stats").update({
                "total_questions": (s.get("total_questions") or 0) + total_q,
                "total_correct":   (s.get("total_correct")   or 0) + correct,
                **streak,
                "updated_at":      now,
            }).eq("user_id", user_id).execute()
        else:
            supabase.table("user_stats").insert({
                "user_id":          user_id,
                "total_questions":  total_q,
                "total_correct":    correct,
                "current_streak":   0,
                "best_streak":      0,
                "last_session_at":  now,
                **streak,
            }).execute()
    except Exception as e:                                     # noqa: BLE001
        print(f"[stats] could not credit {total_q} answers for {user_id[:8]}: {e}")


def _discard_if_nothing_recorded(session_id: str, questions,
                                 answers_counted: bool = False) -> bool:
    """Delete a session that answered nothing and recorded nothing. True if gone.

    Deletes on absence, so a failed read keeps the session. Re-checks
    `session_answers` because the `questions_answered` counter can lag.
    """
    if questions:
        return False
    # Skip that re-check only when the caller just counted zero from the database.
    tables = ("cognitive_signals", "face_signals", "heart_signals") if answers_counted \
        else ("session_answers", "cognitive_signals", "face_signals", "heart_signals")
    for table in tables:
        try:
            rows = supabase.table(table).select("session_id") \
                .eq("session_id", session_id).limit(1).execute().data or []
        except Exception as e:                                 # noqa: BLE001
            print(f"[session:discard] could not check {table} for {session_id}: {e}")
            return False
        if rows:
            return False
    try:
        supabase.table("sessions").delete().eq("id", session_id).execute()
    except Exception as e:                                     # noqa: BLE001
        print(f"[session:discard] could not delete {session_id}: {e}")
        return False
    return True


# Days rolled up from a session's start when it spans more; bounds a corrupt `started_at`.
_ROLLUP_MAX_SPAN_DAYS = 7


def _rollup_session_days(user_id: str, started_at, ended_at) -> None:
    """Recompute the daily rollup for the school days this session touched. Never raises.

    Idempotent, so the next close repairs a failure; the expiry job refuses
    days with no rollup.
    """
    tz = _school_timezone()
    try:
        now = _utc_now()
        day = _school_date(started_at, tz) or _school_date(now, tz)
        end_day = _school_date(ended_at, tz) or _school_date(now, tz)
        span = (end_day - day).days
        if span < 0:
            print(f"[rollup] {user_id[:8]}: reversed span {day}..{end_day}, "
                  f"rolling up the closing day only")
            days = [end_day]
        elif span > _ROLLUP_MAX_SPAN_DAYS:
            # An abandoned session's rows are near its start; the day the sweep closes it has none.
            days = [day + timedelta(days=i) for i in range(_ROLLUP_MAX_SPAN_DAYS + 1)] + [end_day]
        else:
            days = [day + timedelta(days=i) for i in range(span + 1)]
        failures = 0
        for day in days:
            # Per day, so a failure on day one cannot skip the closing day.
            try:
                supabase.rpc("rollup_signal_day", {
                    "p_user_id": user_id,
                    "p_day": day.isoformat(),
                    "p_timezone": _tz_name(tz),
                }).execute()
            except Exception as e:
                failures += 1
                print(f"[rollup] {user_id[:8]} {day}: {e}")
        if failures:
            print(f"[rollup] {user_id[:8]}: {failures} day(s) not rolled up")
    except Exception as e:
        print(f"[rollup] {user_id[:8]}: {e}")


def _claim_session_close(session_id: str, ended_at: str, reason: str) -> bool | None:
    """Stamp `ended_at` and `close_reason` on a session that has neither yet. Never raises.

    True: this caller won. False: another close already had (an empty result, asked for by name).
    None: the write failed and the session is still open, which is not "already closed".
    """
    try:
        claimed = supabase.table("sessions").update({"ended_at": ended_at, "close_reason": reason},
                                                    returning=ReturnMethod.representation) \
            .eq("id", session_id).is_("ended_at", "null").execute().data or []
    except Exception as e:                                     # noqa: BLE001
        print(f"[session:close] could not stamp {session_id}: {e}")
        return None
    return bool(claimed)


def _answer_counts(session_id: str, session: dict) -> tuple[int, int, bool]:
    """`(questions, correct, counted)` for a closing session, recounted from the rows.

    `counted` is False when it fell back to the stored counter: on a failed
    read, or when rows are fewer than the counter (only ever revise upward).
    """
    stored_q = session.get("questions_answered") or 0
    stored_c = session.get("correct_answers") or 0
    try:
        res = supabase.rpc("session_answer_counts",
                           {"p_session_id": session_id}).execute()
    except Exception as e:                                     # noqa: BLE001
        if not _missing_rpc(e, "session_answer_counts", "20260826000000",
                            "a closing session credits the stored counter until then"):
            print(f"[session:close] could not recount answers for {session_id}: {e}")
        return stored_q, stored_c, False
    rows = res.data or []
    if isinstance(rows, dict):
        rows = [rows]
    if not rows:
        return stored_q, stored_c, False
    counted_q = rows[0].get("total") or 0
    if counted_q < stored_q:
        return stored_q, stored_c, False
    return counted_q, rows[0].get("correct") or 0, True


# ─── session alerts ───────────────────────────────────────────────────────
# Operational facts about a session, never a claim about the student.
# See docs/reporting.md, "Session alerts are operations".

# Which close site is running; `_close_session` cannot tell on its own.
CLOSED_BY_STUDENT = "student"
CLOSED_BY_SWEEP = "stale_sweep"

# Why a session ended, stored as `sessions.close_reason`; must equal the migration's CHECK.
# The student's own: what the page says /end was for, or "student" when it names nothing.
END_REASONS = ("finish", "sign_out", "page_closed")
CLOSE_REASON_STUDENT = "student"
CLOSE_REASON_SUPERSEDED = "superseded"
CLOSE_REASON_LIVE_STALE = "live_stale"
CLOSE_REASON_SWEEP = "sweep"
CLOSE_REASONS = END_REASONS + (CLOSE_REASON_STUDENT, CLOSE_REASON_SUPERSEDED,
                               CLOSE_REASON_LIVE_STALE, CLOSE_REASON_SWEEP)

# Session age (not idleness) past which it is abandoned; errs long, since closing
# a live session discards the question in progress.
_SESSION_ABANDONED_AFTER_SEC = env_number(
    "SESSION_ABANDONED_AFTER_HOURS", 6.0, float, minimum=1.0) * 3600

# Background sweep interval (s); 0 disables it.
_STALE_SWEEP_INTERVAL_SEC = env_number(
    "STALE_SWEEP_INTERVAL_SECONDS", 900.0, float, minimum=0.0)
def _first_sweep_delay(production: bool) -> float:
    """Seconds before the first pass, so a cold start serves requests before the sweep competes.

    0 by default outside production, where `--reload` restarts within any delay.
    """
    return env_number("STALE_SWEEP_FIRST_DELAY_SECONDS", 60.0 if production else 0.0, float,
                      minimum=0.0)


_STALE_SWEEP_FIRST_DELAY_SEC = _first_sweep_delay(IS_PRODUCTION)

# Sessions closed per pass; each close renders charts and writes storage.
_STALE_SWEEP_BATCH = 50

ALERT_SESSION_AUTO_CLOSED = "session_auto_closed"
ALERT_SIGNALS_MISSING = "signals_missing"


def _session_had_signals(session_id: str) -> bool | None:
    """Whether any cognitive row reached this session. None if unreadable.

    None must not raise `signals_missing`. Only EEG is checked: it is the channel the alert is about.
    """
    try:
        res = supabase.table("cognitive_signals").select("id") \
            .eq("session_id", session_id).limit(1).execute()
        return bool(res.data)
    except Exception as e:                                     # noqa: BLE001
        print(f"[alerts] could not check signals for {session_id}: {e}")
        return None


def _eeg_was_started(session_id: str) -> bool | None:
    """Whether /api/eeg/start ran for this session. None if unreadable.

    Without it, a session answered with no headband paired read as a broken recording.
    Stamped by `/api/eeg/start` (pull) or `/api/sessions/{id}/eeg-started` (push).
    """
    try:
        rows = supabase.table("sessions").select("eeg_started_at") \
            .eq("id", session_id).limit(1).execute().data or []
        return bool(rows and rows[0].get("eeg_started_at"))
    except Exception as e:                                     # noqa: BLE001
        print(f"[alerts] could not tell whether EEG started for {session_id}: {e}")
        return None


def _mark_eeg_started(session_id: str, device_id: str | None = None) -> None:
    """Stamp the first EEG start on the session, and the station it is on now. Never raises."""
    try:
        supabase.table("sessions").update({"eeg_started_at": _utc_now().isoformat()}) \
            .eq("id", session_id).is_("eeg_started_at", "null").execute()
    except Exception as e:                                     # noqa: BLE001
        print(f"[eeg] could not stamp the EEG start on {session_id}: {e}")
    if device_id is None:
        return
    # The latest station, not the first: a tab can move a lesson to another headband.
    try:
        supabase.table("sessions").update({"eeg_device_id": device_id}).eq("id", session_id).execute()
    except Exception as e:                                     # noqa: BLE001
        print(f"[eeg] could not record the station for {session_id}: {type(e).__name__}")


def _recording_was_expected(user_id: str) -> bool | None:
    """Whether EEG *should* have been recording. None when that cannot be told.

    A failed consent or window read returns `record_eeg: False` without raising,
    so this reads `retrieved` and `window_state` to tell an outage from a refusal.
    """
    try:
        gate = _may_record(user_id)
    except Exception as e:                                     # noqa: BLE001
        print(f"[alerts] could not read recording state for {user_id[:8]}: {e}")
        return None
    # `is False`, not falsiness: an absent flag is not a failed read.
    if gate.get("retrieved") is False:
        return None
    if gate.get("window_state") == WINDOW_UNREADABLE:
        return None
    return bool(gate.get("record_eeg"))


def _raise_session_alerts(user_id: str, session: dict,
                          closed_by: str, answered: int) -> None:
    """Emit whatever operational alerts this close earned. Never raises.

    A unique index on `(session_id, kind)` backstops `_claim_session_close`.
    """
    sid = session.get("id")
    if not sid:
        return
    alerts = []

    if closed_by == CLOSED_BY_SWEEP:
        # No timestamps in `detail`: a literal end-stamp key would make
        # `conftest.close_sites()` read this as a close site.
        alerts.append({
            "kind": ALERT_SESSION_AUTO_CLOSED,
            "detail": {"questions_answered": answered},
        })

    expected = _recording_was_expected(user_id)
    if expected is None:
        # Unknown, not "no"; logged because this branch has no other trace.
        print(f"[alerts] cannot tell whether recording was expected for "
              f"{user_id[:8]}; withholding {ALERT_SIGNALS_MISSING}")
    elif expected:
        started = _eeg_was_started(sid)
        # A headband nobody started is not a fault; None (unreadable) withholds too.
        had = _session_had_signals(sid) if started else None
        # `is False`: None means the count failed.
        if had is False:
            alerts.append({
                "kind": ALERT_SIGNALS_MISSING,
                "detail": {"questions_answered": answered, "channel": "eeg"},
            })

    if not alerts:
        return
    try:
        supabase.table("session_alerts").upsert(
            [{"user_id": user_id, "session_id": sid, **a} for a in alerts],
            on_conflict="session_id,kind", ignore_duplicates=True,
        ).execute()
    except Exception as e:                                     # noqa: BLE001
        print(f"[alerts] could not record {len(alerts)} alert(s) for {sid}: {e}")


def _close_session(user_id: str, session: dict, ended_at: str,
                   closed_by: str = CLOSED_BY_STUDENT, reason: str = CLOSE_REASON_STUDENT) -> dict:
    """Everything a session close does, including stamping `ended_at` and `close_reason` (the claim).

    Every close site goes through here; callers stop the poller *before* the call.
    `closed_by` defaults to the student, so a new site must opt in to an alert; `reason` likewise.
    """
    sid = session["id"]
    claimed = _claim_session_close(sid, ended_at, reason)
    if claimed is None:
        # Still open: callers must not report this as closed.
        return {"discarded": False, "failed": True}
    if not claimed:
        # Already closed; running again would double-credit the answers.
        return {"discarded": False, "already_closed": True}

    # Questions prepared for it can never be served now.
    _drop_prefetched(user_id, sid)
    # This lesson is over, so a headband paired in it is free for the next student at the station.
    _forget_session_pairings(user_id, sid)
    total_q, correct, counted = _answer_counts(sid, session)

    if _discard_if_nothing_recorded(sid, total_q, answers_counted=counted):
        # The row is gone, and its reason with it: the count is the only record.
        ops_metrics.bump("session_discarded", reason)
        return {"discarded": True}

    _credit_session_to_user_stats(user_id, total_q, correct, session.get("started_at"))
    _rollup_session_days(user_id, session.get("started_at"), ended_at)
    # After the discard: an empty session is not a fault worth an alert.
    _raise_session_alerts(user_id, session, closed_by, total_q)
    # Off the request path.
    chart_archive.schedule(supabase, sid, user_id)
    return {"discarded": False}


# ─── the background sweep for abandoned sessions ──────────────────────────
# Catches students who never come back. A backend thread, not pg_cron: a close
# credits stats, rolls up, archives charts and raises alerts, which SQL cannot.

_stale_sweep_stop = threading.Event()
_stale_sweep_thread: threading.Thread | None = None


def _last_seen_many(sessions: list[dict]) -> dict[str, datetime] | None:
    """Each session's last activity, by id; None when the read failed. Never raises."""
    if not sessions:
        return {}
    try:
        got = supabase.rpc("last_activity_for_sessions",
                           {"p_session_ids": [s["id"] for s in sessions]}).execute().data or []
    except Exception as e:                                     # noqa: BLE001
        print(f"[session:close] could not read last activity: {e}")
        return None
    return {g["session_id"]: ts for g in got if (ts := _parse_ts(g.get("last_activity_at")))}


def _quiet_since(session: dict, last_seen: dict[str, datetime] | None, cutoff: datetime) -> bool:
    """Whether nothing has happened in a session since `cutoff`, its start included; an unread activity is not quiet."""
    if last_seen is None:
        return False
    seen = last_seen.get(session["id"]) or _parse_ts(session.get("started_at"))
    return seen is not None and seen < cutoff


def _ended_when_last_seen(session: dict, last_seen: dict[str, datetime] | None) -> str:
    """A swept session ends when the student was last seen, else at its start; now only if that was unread."""
    if last_seen is None:
        return _utc_now().isoformat()
    ended = last_seen.get(session["id"]) or _parse_ts(session.get("started_at")) or _utc_now()
    return ended.isoformat()


def _sweep_abandoned_sessions(limit: int = _STALE_SWEEP_BATCH) -> dict:
    """Close sessions left open past `_SESSION_ABANDONED_AFTER_SEC`; returns counts.

    Each close is guarded individually, so one bad session cannot stop the sweep.
    """
    cutoff = (_utc_now() - timedelta(seconds=_SESSION_ABANDONED_AFTER_SEC)).isoformat()
    try:
        rows = (supabase.table("sessions")
                .select("id, user_id, started_at, questions_answered, correct_answers")
                .is_("ended_at", "null").lt("started_at", cutoff)
                .order("started_at").limit(limit).execute().data or [])
    except Exception as e:                                     # noqa: BLE001
        print(f"[stale_sweep] could not list abandoned sessions: {e}")
        return {"found": 0, "closed": 0, "discarded": 0, "failed": 0,
                "retrieved": False}

    last_seen = _last_seen_many(rows)
    closed = discarded = failed = 0
    for s in rows:
        uid = s.get("user_id")
        if not uid:
            continue
        try:
            # Poller first, so no tick lands after the discard check.
            eeg_poller.stop(s["id"], uid)
            out = _close_session(uid, s, _ended_when_last_seen(s, last_seen),
                                 closed_by=CLOSED_BY_SWEEP, reason=CLOSE_REASON_SWEEP)
            if out.get("failed"):
                failed += 1
            elif out.get("discarded"):
                discarded += 1
            else:
                closed += 1
        except Exception as e:                                 # noqa: BLE001
            failed += 1
            print(f"[stale_sweep] could not close {s.get('id')}: {e}")
    if rows:
        print(f"[stale_sweep] {len(rows)} abandoned: {closed} closed, "
              f"{discarded} discarded, {failed} failed")
    return {"found": len(rows), "closed": closed, "discarded": discarded,
            "failed": failed, "retrieved": True}


def _stale_sweep_loop() -> None:
    """Sweep once shortly after startup, then every interval, until asked to stop.

    Waits on the stop event, not `sleep`, so it can be joined promptly. Sweeping
    soon after startup matters because `--reload` restarts often.
    """
    if _STALE_SWEEP_FIRST_DELAY_SEC > 0 and _stale_sweep_stop.wait(_STALE_SWEEP_FIRST_DELAY_SEC):
        return
    first = True
    while first or not _stale_sweep_stop.wait(_STALE_SWEEP_INTERVAL_SEC):
        first = False
        try:
            _sweep_abandoned_sessions()
        except Exception as e:                                 # noqa: BLE001
            print(f"[stale_sweep] pass failed: {e}")
        try:
            _sweep_abandoned_practice()
        except Exception as e:                                 # noqa: BLE001
            print(f"[practice_sweep] pass failed: {e}")
        # Its own guard: an archive cancelled at shutdown or failed is retried from here.
        try:
            chart_archive.archive_missing(supabase)
        except Exception as e:                                 # noqa: BLE001
            print(f"[stale_sweep] chart catch-up failed: {e}")
        # Also checked here, so a stop during the first pass is not an interval away.
        if _stale_sweep_stop.is_set():
            return


def start_stale_sweeper() -> bool:
    """Start the background sweep, unless switched off or already running.

    Safe in several workers at once via `_claim_session_close`.
    """
    global _stale_sweep_thread
    if _STALE_SWEEP_INTERVAL_SEC <= 0:
        print("[stale_sweep] disabled (STALE_SWEEP_INTERVAL_SECONDS=0)")
        return False
    if _stale_sweep_thread and _stale_sweep_thread.is_alive():
        return False
    _stale_sweep_stop.clear()
    _stale_sweep_thread = threading.Thread(
        target=_stale_sweep_loop, name="stale-sweep", daemon=True)
    _stale_sweep_thread.start()
    return True


def stop_stale_sweeper(timeout: float = 5.0) -> None:
    """Ask the sweep to finish and join it (a print during shutdown is a fatal abort)."""
    global _stale_sweep_thread
    _stale_sweep_stop.set()
    thread, _stale_sweep_thread = _stale_sweep_thread, None
    if thread and thread.is_alive():
        thread.join(timeout=timeout)


def _may_record(student_id: str, consent: "_StoredConsent | None" = None) -> dict:
    """Consent **and** the retention window, composed for recording sites only.

    Kept out of `_consent`, whose readers must not change answer when term ends.
    `consent` in hand comes from `_stored_consent`; a bare dict has no provenance and raises.
    """
    if consent is not None and not isinstance(consent, _StoredConsent):
        raise TypeError(f"_may_record takes consent from _stored_consent, not a {type(consent).__name__}")
    flags = _feature_flags()
    # A bypass substitutes full consent only; `consent_bypassed` says so. Other gates still apply.
    enforced = _consent_enforcement_active(flags)
    if not enforced:
        consent = {**_CONSENT_ENABLED_ALL, "retrieved": True, "exists": False}
    elif consent is None:
        consent = _consent(student_id)
    else:
        consent = consent.answer
    window = _retention_window()
    recording = window["state"] not in _WINDOW_DENIED
    return {**consent,
            "window_state": window["state"],
            "window_starts_on": window["starts_on"],
            "window_ends_on": window["ends_on"],
            "consent_bypassed": not enforced,
            # ANDed, never ORed: a flag can withhold recording, never grant it.
            "record_eeg": (recording and flags["recording_eeg_enabled"]["enabled"]
                           and bool(consent.get("eeg_enabled"))),
            "record_headband_optical": (
                recording and flags["recording_heart_enabled"]["enabled"]
                and bool(consent.get("headband_optical_enabled"))),
            "record_camera": (recording and flags["recording_camera_enabled"]["enabled"]
                              and bool(consent.get("camera_enabled"))),
            # So a reason can name the switch rather than blame consent.
            "switched_off": sorted(key for key, flag in _RECORDING_SWITCHES.items()
                                   if not flags[flag]["enabled"])}


def _as_sentence(text: str) -> str:
    """A lowercase `reason` fragment as a capitalised, punctuated sentence."""
    if not text:
        return text
    return text[0].upper() + text[1:] + ("" if text.endswith((".", "!", "?")) else ".")


# Unknown states report nothing; `_WINDOW_DENIED` already denies them.
_NO_MEANING = _WindowMeaning(False, None, None)


def _window_meaning(state: str) -> _WindowMeaning:
    """What a window state means; callers pick a field."""
    return _WINDOW_STATES.get(state) or _NO_MEANING


# The admin switch behind each `record_*` flag `_may_record` composes.
_RECORDING_SWITCHES = {"record_eeg": "recording_eeg_enabled",
                       "record_headband_optical": "recording_heart_enabled",
                       "record_camera": "recording_camera_enabled"}
_SWITCHED_OFF = "recording is switched off by an administrator"
# A zeroed tick, as a headband off the head sends: nothing refused it.
_NO_USABLE_EEG = "no usable reading; check the headband is on"


def _not_recording_reason(gate: dict, declined: str,
                          unavailable: str = "consent unavailable", *,
                          switches: tuple[str, ...], partly_switched: str | None = None) -> str:
    """Why this channel is not recording: the window, then an admin switch, then consent.

    `switches` names the `record_*` keys the channel records under; all off is the switch's
    doing. `partly_switched` is for a channel of several sensors with only some switched off.
    """
    window = _window_meaning(gate.get("window_state")).reason
    if window:
        return window
    off = [key for key in switches if key in gate.get("switched_off", ())]
    if switches and len(off) == len(switches):
        return _SWITCHED_OFF
    if not gate.get("retrieved"):
        return unavailable
    if off and partly_switched:
        return partly_switched
    return declined


def _recording_state(gate: dict, channel: str) -> str:
    """One channel's key for `GET /api/recording/me`, in `_not_recording_reason`'s order.

    `unknown` (an unreadable window or consent) is not a refusal, so any definite refusal outranks it."""
    if gate.get(f"record_{channel}"):
        return "permitted"
    if gate.get("window_state") != WINDOW_UNREADABLE:
        window = _window_meaning(gate.get("window_state")).stopped_reason
        if window:
            return window
    if f"record_{channel}" in gate.get("switched_off", ()):
        return "switched_off"
    if gate.get("retrieved") and not gate.get(f"{channel}_enabled"):
        return "declined"
    return "unknown"


def _definitely_refused(gate: dict, *channels: str) -> bool:
    """An ingest receipt's `refused`: a channel definitely may not record, not merely unreadable."""
    return any(_recording_state(gate, c) not in ("permitted", "unknown") for c in channels)


def _topic_breakdown(student_id: str):
    """The student's per-topic accuracy; [] on a failed read.

    If [] would become a claim ("nothing attempted"), use `_topic_breakdown_with_state`.
    """
    return _topic_breakdown_with_state(student_id)[0]


def _topic_breakdown_with_state(student_id: str) -> tuple[list[dict], bool]:
    """The rows, and whether the read actually happened."""
    retrieved = True
    try:
        rows = supabase.table("user_math_performance") \
            .select("*, math_topics(topic_name)") \
            .eq("user_id", student_id).execute().data or []
    except Exception as e:
        print(f"[topic_breakdown] {e}")
        rows = []
        retrieved = False
    out = []
    for r in rows:
        attempted = r.get("attempted_questions") or 0
        correct = r.get("correct_questions") or 0
        out.append({
            "topic_id": r.get("topic_id"),
            "topic_name": ((r.get("math_topics") or {}).get("topic_name") or "Unknown"),
            "attempted_questions": attempted,
            "correct_questions": correct,
            "accuracy": round((correct / attempted) * 100) if attempted else 0,
            "stress": r.get("stress"),
            "updated_at": r.get("updated_at"),
        })
    return out, retrieved


class ReportChannels(NamedTuple):
    """Which optional channels a report may read, and whether consent was readable.

    `consent_retrieved` separates "nobody consented" from "could not read consent".
    `*_revoked_at` lets a tile say "Off since <date>"; `*_erased_at`, that a parent erased its past.
    """
    heart: bool
    emotion: bool
    consent_retrieved: bool
    heart_revoked_at: str | None = None
    emotion_revoked_at: str | None = None
    # Consent only, not a read filter: the cognitive channel is always read.
    eeg: bool = True
    eeg_revoked_at: str | None = None
    # `_erasures()`'s {sensor: erased_at}, and the heart sensors consented now; `_erased_fields` windows them.
    erasures: dict | None = None
    heart_sensors: tuple = ()


def _reportable_channels(student_id: str, want_emotion: bool = True,
                         want_heart: bool = True) -> ReportChannels:
    """Which optional channels a report may read: consent AND what was asked for.

    Consent is resolved here and fails closed. `want_*` can only narrow; no
    client sends it and it is not a privacy boundary.
    """
    return _channels_from_consent(_consent(student_id), want_emotion, want_heart,
                                  erasures=_erasures(student_id))


def _latest(*stamps):
    """The latest of some timestamps, as instants not text; None when there are none."""
    stamps = [t for t in stamps if t]
    # Unparseable sorts last.
    return max(stamps, key=lambda t: _parse_ts(t) or datetime.min.replace(tzinfo=timezone.utc),
               default=None)


def _channels_from_consent(consent: dict, want_emotion: bool = True,
                           want_heart: bool = True, erasures: dict | None = None) -> ReportChannels:
    """The consent row -> channels mapping, shared by the single and batch forms.

    `erasures` is `_erasures()`'s {sensor: erased_at}; `_erased_fields` decides what a report says.
    """
    heart_sensors = tuple(s for s in ("headband_optical", "camera") if consent.get(f"{s}_enabled"))
    heart = bool(consent.get("headband_optical_enabled")) or bool(consent.get("camera_enabled"))
    emotion = bool(consent.get("camera_enabled"))
    # Heart is off only when both sensors are; it stopped at the later revocation.
    heart_revoked = None if heart else _latest(consent.get("headband_optical_revoked_at"),
                                               consent.get("camera_revoked_at"))
    eeg = bool(consent.get("eeg_enabled"))
    return ReportChannels(heart=want_heart and heart,
                          emotion=want_emotion and emotion,
                          consent_retrieved=bool(consent.get("retrieved")),
                          heart_revoked_at=heart_revoked,
                          emotion_revoked_at=None if emotion else consent.get("camera_revoked_at"),
                          eeg=eeg,
                          eeg_revoked_at=None if eeg else consent.get("eeg_revoked_at"),
                          erasures=dict(erasures or {}),
                          heart_sensors=heart_sensors)


def _window_start(days: int, tz=None) -> datetime:
    """Midnight of the earliest school day in the last `days`, in UTC: the reports' own start."""
    tz = tz or _school_timezone()
    school_today = _utc_now().astimezone(tz).date()
    # `datetime.min.time()`: `time` here is the stdlib module, which has no `min`.
    return datetime.combine(school_today - timedelta(days=days - 1), datetime.min.time(),
                            tzinfo=tz).astimezone(timezone.utc)


_NO_ERASURES = {"eeg_erased_at": None, "heart_erased_at": None, "emotion_erased_at": None}


def _erased_fields(ch: "ReportChannels", since: datetime) -> dict:
    """The three `*_erased_at` payload keys, beside `*_revoked_at`; `since` is `_window_start`'s.

    Only an erasure inside the window: an earlier one took nothing from it, so an empty tile is not
    its doing. Heart: every consented heart sensor erased inside it, at the latest of them.
    """
    def within(at):
        t = _parse_ts(at) if at else None
        return at if t and t >= since else None
    erasures = ch.erasures or {}
    heart = [within(erasures.get(s)) for s in ch.heart_sensors]
    return {"eeg_erased_at": within(erasures.get("eeg")),
            "heart_erased_at": _latest(*heart) if heart and all(heart) else None,
            "emotion_erased_at": within(erasures.get("camera"))}


def _summary_rpc(name: str, params: dict, include_heart: bool, include_emotion: bool):
    """Call a summary RPC with the channel opt-outs threaded in. Raises on failure, deliberately."""
    return supabase.rpc(name, {**params,
                               "p_include_heart": include_heart,
                               "p_include_emotion": include_emotion,
                               # School timezone, matching `_weekly_signal_report`.
                               "p_timezone": _school_timezone_name()}).execute()


def _signal_summary(student_id: str, days: int = 7, include_heart: bool = True,
                    include_emotion: bool = True,
                    consent_retrieved: bool = True,
                    emotion_revoked_at: str | None = None,
                    heart_revoked_at: str | None = None,
                    eeg_enabled: bool = True,
                    eeg_revoked_at: str | None = None,
                    erased: dict | None = None) -> dict:
    """Just the headline averages, aggregated in Postgres; a declined channel is never read.

    Carries `dominant_emotion`, which `_signal_summaries` does not.
    """
    row = None
    retrieved = True
    try:
        res = _summary_rpc("student_signal_summary",
                           {"p_student_id": student_id, "p_days": days},
                           include_heart, include_emotion)
    except Exception as e:
        print(f"[signal_summary] {e}")
        retrieved = False
    else:
        rows = res.data or []
        row = rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else None)
    summary = _shape_summary(row, include_heart, include_emotion, retrieved,
                             consent_retrieved,
                             emotion_revoked_at=emotion_revoked_at,
                             heart_revoked_at=heart_revoked_at,
                             eeg_enabled=eeg_enabled,
                             eeg_revoked_at=eeg_revoked_at,
                             erased=erased)
    summary["score_scale"] = _scale_ranges_many([student_id], days).get(str(student_id))
    # Not in `_shape_summary`: the batch RPC has no such field.
    summary["dominant_emotion"] = (row or {}).get("dominant_emotion") if include_emotion else None
    return summary


_EMPTY_SUMMARY = {"consent_retrieved": True, "score_scale": None,
                  "focus": None, "stress": None, "engagement": None,
                  "face_attention": None, "heart_rate_bpm": None,
                  "rmssd_ms": None, "sessions": 0,
                  "cognitive_samples": 0, "face_samples": 0, "heart_samples": 0,
                  # `face_included` is a deprecated alias of `emotion_included`.
                  "face_included": True, "emotion_included": True,
                  "heart_included": True, "retrieved": True,
                  "eeg_enabled": True, "eeg_revoked_at": None,
                  **_NO_ERASURES}


def _shape_summary(row, include_heart: bool = True, include_emotion: bool = True,
                   retrieved: bool = True, consent_retrieved: bool = True,
                   emotion_revoked_at: str | None = None,
                   heart_revoked_at: str | None = None,
                   eeg_enabled: bool = True,
                   eeg_revoked_at: str | None = None,
                   erased: dict | None = None) -> dict:
    """The summary payload.

    `retrieved: False` means the aggregate read failed, as distinct from nothing
    recorded or not requested. Any surface showing "no data" must check it.
    """
    erased = erased or _NO_ERASURES
    if not row:
        return {**_EMPTY_SUMMARY, "face_included": include_emotion,
                "emotion_included": include_emotion, "heart_included": include_heart,
                "retrieved": retrieved, "consent_retrieved": consent_retrieved,
                "emotion_revoked_at": emotion_revoked_at,
                "heart_revoked_at": heart_revoked_at,
                "eeg_enabled": eeg_enabled, "eeg_revoked_at": eeg_revoked_at, **erased}
    return {
        "focus": row.get("focus"),
        "stress": row.get("stress"),
        # Served from `focus`: older stored `engagement` rows hold strap-fit confidence.
        "engagement": row.get("focus"),
        # Stamped by the caller from the rollup (`_scale_ranges_many`).
        "score_scale": None,
        "face_attention": row.get("face_attention"),
        # Absolute units, not 0..1 ratios: never apply `toPct()`.
        "heart_rate_bpm": row.get("heart_rate_bpm"),
        "rmssd_ms": row.get("rmssd_ms"),
        "sessions": row.get("sessions") or 0,
        # None average + 0 count = nothing recorded; + nonzero count = recorded but unusable.
        "cognitive_samples": row.get("cognitive_samples") or 0,
        "face_samples": row.get("face_samples") or 0,
        "heart_samples": row.get("heart_samples") or 0,
        # Deprecated alias of emotion_included.
        "face_included": include_emotion,
        "emotion_included": include_emotion,
        "heart_included": include_heart,
        "retrieved": retrieved,
        # The consent read, as distinct from the aggregate read above.
        "consent_retrieved": consent_retrieved,
        "emotion_revoked_at": emotion_revoked_at,
        "heart_revoked_at": heart_revoked_at,
        # Consent, not an inclusion flag: the cognitive channel is always read.
        "eeg_enabled": eeg_enabled,
        "eeg_revoked_at": eeg_revoked_at,
        **erased,
    }


def _signal_summaries(student_ids: list[str], days: int = 7,
                      include_heart: bool = True,
                      include_emotion: bool = True,
                      channels_by_student: dict | None = None) -> dict[str, dict] | None:
    """Headline averages for many students in one round-trip.

    None means the read failed; {} means it succeeded with nothing to return.
    `channels_by_student` ({id: ReportChannels}) stamps the per-child consent
    fields the batch RPC cannot return.
    """
    if not student_ids:
        return {}
    try:
        res = _summary_rpc("student_signal_summary_many",
                           {"p_student_ids": student_ids, "p_days": days},
                           include_heart, include_emotion)
    except Exception as e:
        print(f"[signal_summaries] {e}")
        return None
    rows = res.data or []
    if isinstance(rows, dict):
        rows = [rows]
    out = {}
    scales = _scale_ranges_many([str(s) for s in student_ids], days)
    since = _window_start(days)
    for r in rows:
        sid = r.get("student_id")
        if not sid:
            continue
        ch = (channels_by_student or {}).get(sid) or (channels_by_student or {}).get(str(sid))
        out[str(sid)] = _shape_summary(
            r, include_heart, include_emotion,
            consent_retrieved=ch.consent_retrieved if ch else True,
            emotion_revoked_at=ch.emotion_revoked_at if ch else None,
            heart_revoked_at=ch.heart_revoked_at if ch else None,
            eeg_enabled=ch.eeg if ch else True,
            eeg_revoked_at=ch.eeg_revoked_at if ch else None,
            erased=_erased_fields(ch, since) if ch else _NO_ERASURES)
        out[str(sid)]["score_scale"] = scales.get(str(sid))
    return out


# Max weeks a trend may span; the payload is per week.
_TREND_MAX_WEEKS = 26


def _stress_weight(rollup_row: dict) -> int:
    """The rows a cognitive rollup row's `avg_stress` was averaged over.

    `stress_sample_count`, falling back per row to `trusted_sample_count` (the
    focus count) for rows rolled before that column existed."""
    n = rollup_row.get("stress_sample_count")
    if isinstance(n, (int, float)) and not isinstance(n, bool):
        return int(n)
    return int(rollup_row.get("trusted_sample_count") or 0)


_HEADBAND_HEART_SOURCES = frozenset({"muse_optics", "muse_ppg"})


def _arousal_counts(stress_counts) -> tuple[int, int, int, int]:
    """`(low, moderate, high, calibrating)` windows from a heart rollup row's `stress_counts`."""
    counts = stress_counts if isinstance(stress_counts, dict) else {}

    def n(key):
        v = counts.get(key)
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else 0
    return n("low"), n("moderate"), n("high"), n("calibrating")


def _add_counts(a: tuple, b: tuple) -> tuple:
    return tuple(x + y for x, y in zip(a, b))


def _arousal_share(counts: tuple) -> dict:
    """Body arousal from pooled counts: shares of classified windows, None with none classified."""
    low, moderate, high, calibrating = counts
    classified = low + moderate + high
    return {"high_share": round(high / classified, 4) if classified else None,
            "moderate_share": round(moderate / classified, 4) if classified else None,
            "classified_windows": classified,
            "calibrating_windows": calibrating}


def _sensor_class(sources) -> frozenset:
    """The heart sensors a source list names, as `headband` / `camera`; unknown names add nothing."""
    names = {s for s in (sources or ()) if isinstance(s, str)}
    return frozenset(({"headband"} if names & _HEADBAND_HEART_SOURCES else set())
                     | ({"camera"} if "rppg" in names else set()))


# Fewer classified windows than this and a share is a thin reading (a judgement call).
_AROUSAL_MIN_WINDOWS = 30


def _body_arousal(counts: tuple, sensors: frozenset, pending: bool,
                  include_heart: bool = True, read_ok: bool = True,
                  raw_ok: bool = True) -> dict:
    """Body arousal and the reason for its value, the same on every surface.

    `pending`: heart rows from a lesson not yet rolled up, so not counted here. `raw_ok`
    False: open lessons could not be checked, so no absence is claimed (`unknown`, `pending: None`).
    See docs/reporting.md for each `state`.
    """
    out = _arousal_share(counts)
    if not include_heart:
        state = "not_requested"
    elif not read_ok:
        state = "not_retrieved"
    elif out["classified_windows"]:
        state = "measured"
    elif out["calibrating_windows"]:
        state = "calibrating"
    elif not raw_ok:
        state = "unknown"
    elif pending:
        state = "pending"
    elif "headband" in sensors:
        state = "unusable"
    elif "camera" in sensors:
        state = "camera_only"
    else:
        state = "none"
    readable = include_heart and read_ok
    return {**out, "state": state,
            "pending": (None if not raw_ok else bool(pending)) if readable else False,
            "few_readings": 0 < out["classified_windows"] < _AROUSAL_MIN_WINDOWS}


def _unrolled_heart(agg: dict | None, rolled: bool) -> bool:
    """Heart rows on a day with no rollup row yet: an open lesson, unless camera alone.

    The raw aggregate names trusted sources only, so poor-contact rows name no sensor.
    """
    if rolled or not int((agg or {}).get("rows") or 0):
        return False
    sensors = _sensor_class((agg or {}).get("sources"))
    return "headband" in sensors or not sensors


def _scale_range(rollup_rows) -> dict | None:
    """`{"min", "max", "calm_sources"}` over cognitive rollup rows, or None if no row has a scale.

    Read from rows, never a date: both change per sidecar restart. Ends that differ, or
    two calm sources (stress only), mean values either side must not be averaged together.
    """
    cognitive = [r for r in rollup_rows if r.get("channel") == "cognitive"]
    lows = [r["score_scale_min"] for r in cognitive if r.get("score_scale_min") is not None]
    highs = [r["score_scale_max"] for r in cognitive if r.get("score_scale_max") is not None]
    if not lows or not highs:
        return None
    return {"min": int(min(lows)), "max": int(max(highs)),
            "calm_sources": _calm_sources_union(r.get("calm_sources") for r in cognitive)}


def _calm_sources_union(lists) -> list[str]:
    """Sorted union of calm-source lists; a null list (no scored stress) adds nothing."""
    return sorted({s for sources in lists if isinstance(sources, list)
                   for s in sources if isinstance(s, str)})


def _week_start(day: date) -> date:
    """The Monday of `day`'s school week (`day` is already in the school's timezone)."""
    return day - timedelta(days=day.weekday())


def _signal_trend(student_id: str, weeks: int = 8, include_heart: bool = True,
                  include_emotion: bool = True,
                  consent_retrieved: bool = True,
                  emotion_revoked_at: str | None = None,
                  heart_revoked_at: str | None = None,
                  rollup: tuple[list, bool] | None = None,
                  open_session: bool | None = None):
    """Week-over-week averages, read from the rollup and nothing else.

    Weighted by `trusted_sample_count` (stress by `_stress_weight`); see docs/reporting.md,
    "The term trend reads the rollup". `rollup` is `(rows, retrieved)` already read, to share
    one read. `open_session` is the caller's `_any_open_session` answer; None is unchecked.
    """
    tz = _school_timezone()
    school_today = _school_today(tz)
    # Whole Monday-anchored weeks, so no part-week reads as a full one.
    first_monday = _week_start(school_today) - timedelta(weeks=weeks - 1)

    # A declined channel is filtered out of the query, never read and discarded.
    channels = ["cognitive"]
    if include_heart:
        channels.append("heart")
    if include_emotion:
        channels.append("emotion")

    rows: list = []
    retrieved = True
    if rollup is not None:
        rows, retrieved = rollup
    else:
        try:
            rows = (supabase.table("signal_daily_rollup").select("*")
                    .eq("user_id", student_id)
                    .in_("channel", channels)
                    .gte("day", first_monday.isoformat())
                    .lte("day", school_today.isoformat())
                    .execute().data or [])
        except Exception as e:                                 # noqa: BLE001
            print(f"[signal_trend] {student_id}: {e}")
            retrieved = False

    # Every week in range: an empty week is a gap, not a dropped bar.
    buckets: dict[date, dict] = {}
    for i in range(weeks):
        monday = first_monday + timedelta(weeks=i)
        buckets[monday] = {
            "week_start": monday.isoformat(),
            "sums": {k: [0.0, 0] for k in ("focus", "stress", "engagement",
                                           "heart_rate_bpm", "rmssd_ms")},
            "cognitive_samples": 0,
            "heart_samples": 0,
            "emotion_samples": 0,
            "days_with_data": set(),
            "heart_sources": set(),
            "emotion_counts": {},
            "scale_rows": [],  # cognitive rollup rows, for `_scale_range`
            "arousal": (0, 0, 0, 0),
        }

    COLUMNS = {
        "cognitive": (("focus", "avg_focus"), ("stress", "avg_stress"),
                      # Never the stored `avg_engagement`; see `_shape_summary`.
                      ("engagement", "avg_focus")),
        "heart": (("heart_rate_bpm", "avg_heart_rate_bpm"),
                  ("rmssd_ms", "avg_rmssd_ms")),
    }

    for r in rows:
        channel = r.get("channel")
        # Backstop: a query string alone should not enforce consent.
        if channel not in channels:
            continue
        try:
            day = date.fromisoformat(str(r.get("day")))
        except (TypeError, ValueError):
            continue
        b = buckets.get(_week_start(day))
        if b is None:
            continue

        n = r.get("trusted_sample_count") or 0
        b["days_with_data"].add(day.isoformat())
        if channel == "cognitive":
            b["cognitive_samples"] += n
            b["scale_rows"].append(r)
        elif channel == "heart":
            b["heart_samples"] += n
            for s in (r.get("heart_sources") or []):
                b["heart_sources"].add(s)
            # Pooled counts, never a mean of daily shares.
            b["arousal"] = _add_counts(b["arousal"], _arousal_counts(r.get("stress_counts")))
        elif channel == "emotion":
            b["emotion_samples"] += n
            for label, count in (r.get("emotion_counts") or {}).items():
                b["emotion_counts"][label] = b["emotion_counts"].get(label, 0) + count

        for key, column in COLUMNS.get(channel, ()):
            value = r.get(column)
            # A null average contributes nothing, never a zero.
            weight = _stress_weight(r) if key == "stress" else n
            if isinstance(value, (int, float)) and weight > 0:
                b["sums"][key][0] += float(value) * weight
                b["sums"][key][1] += weight

    def _mean(pair):
        total, n = pair
        return round(total / n, 4) if n else None

    out = []
    for monday in sorted(buckets):
        b = buckets[monday]
        # Rollup only: this week may hold an uncounted lesson unless the caller saw none running.
        arousal = _body_arousal(b["arousal"], _sensor_class(b["heart_sources"]), False,
                                include_heart, retrieved,
                                raw_ok=monday != _week_start(school_today)
                                or open_session is False)
        out.append({
            "week_start": b["week_start"],
            "score_scale": _scale_range(b["scale_rows"]),
            **{k: _mean(v) for k, v in b["sums"].items()},
            # Share of classified heart windows `high` / `moderate`; separate from EEG stress.
            "body_arousal": arousal["high_share"],
            "body_arousal_moderate": arousal["moderate_share"],
            "body_arousal_windows": arousal["classified_windows"],
            "body_arousal_state": arousal["state"],
            "body_arousal_few_readings": arousal["few_readings"],
            "cognitive_samples": b["cognitive_samples"],
            "heart_samples": b["heart_samples"],
            "emotion_samples": b["emotion_samples"],
            "days_with_data": len(b["days_with_data"]),
            # Sorted, so a chart caption is stable between reads.
            "heart_sources": sorted(b["heart_sources"]),
            "emotion_distribution": b["emotion_counts"],
        })

    return {
        "weeks": out,
        "retrieved": retrieved,
        "heart_included": include_heart,
        "emotion_included": include_emotion,
        "consent_retrieved": consent_retrieved,
        "emotion_revoked_at": emotion_revoked_at,
        "heart_revoked_at": heart_revoked_at,
        "timezone": str(tz),
    }


def _weekly_body_arousal(include_heart: bool, rollup_ok: bool, rollup_by: dict,
                         heart_days: dict, heart_sources: list, heart_ok: bool = True,
                         lesson: tuple = (False, None)) -> dict:
    """The window's body arousal, pooled from the heart rollup rows, with a `state`.

    `heart_ok` is the raw heart read; `lesson` is `(pending, first school day)` of a
    running lesson with heart rows, as `_running_heart_lessons` gives it.
    """
    rolled = [r for (_day, channel), r in rollup_by.items() if channel == "heart"]
    counts = (0, 0, 0, 0)
    for r in rolled:
        counts = _add_counts(counts, _arousal_counts(r.get("stress_counts")))
    sensors = _sensor_class(heart_sources) | _sensor_class(
        s for r in rolled for s in (r.get("heart_sources") or ()))
    uncounted = any(_unaccounted_heart(agg, (day, "heart") in rollup_by, day, lesson)
                    for day, agg in heart_days.items())
    return _body_arousal(counts, sensors, bool(lesson[0]), include_heart, rollup_ok,
                         heart_ok and lesson[0] is not None and not uncounted)


def _unaccounted_heart(agg: dict | None, rolled: bool, day: str, lesson: tuple) -> bool:
    """Heart rows on `day` that neither the rollup nor the running lesson accounts for.

    Those were never summarised (a failed write): not a lesson in progress, not an absence.
    """
    pending, first_day = lesson
    in_lesson = bool(pending) and first_day is not None and day >= first_day
    return _unrolled_heart(agg, rolled) and not in_lesson


def _weekly_signal_report(student_id: str, days: int = 7, include_heart: bool = True,
                          include_emotion: bool = True,
                          consent_retrieved: bool = True,
                          emotion_revoked_at: str | None = None,
                          heart_revoked_at: str | None = None,
                          eeg_enabled: bool = True,
                          eeg_revoked_at: str | None = None):
    """Averages, highlights and per-day buckets of a student's recent signals.

    Callers must already have authorised the viewer. A false flag skips that
    channel's read outright; `*_included` then reads "not requested".
    """
    tz = _school_timezone()
    school_today = _school_today(tz)
    since = _window_start(days, tz).isoformat()

    def _signal_days(channel: str) -> tuple[dict, dict | None, bool]:
        """`(aggregate per school day, latest row, read_ok)`, over every row of the week.

        Aggregated in SQL: a raw read stops at PostgREST's 1000 rows, ~17 minutes of EEG.
        """
        try:
            out = supabase.rpc("weekly_signal_days", {
                "p_student_id": student_id, "p_channel": channel,
                "p_since": since, "p_timezone": _tz_name(tz)}).execute().data or {}
            return ({str(d.get("day")): d for d in out.get("days") or []},
                    out.get("latest"), True)
        except Exception as e:                                  # noqa: BLE001
            print(f"[weekly_report:{channel}] {e}")
            return {}, None, False

    cog_days, latest_cognitive, cog_ok = _signal_days("cognitive")
    # Whether a lesson is still open, so its uncounted heart rows read `pending`; None if unread.
    pending, started = _running_heart_lessons([student_id])[str(student_id)] \
        if include_heart else (False, None)
    lesson = (pending, _school_day(started, tz) if started else None)
    # An opted-out channel is ok=True: nothing failed, nothing was asked for.
    face_days, latest_face, face_ok = _signal_days("emotion") if include_emotion \
        else ({}, None, True)
    heart_days, latest_heart, heart_ok = _signal_days("heart") if include_heart \
        else ({}, None, True)

    # Sessions are counted from their rows, so this read keeps a cap and the only cut.
    try:
        res = supabase.table("sessions").select("started_at", count="exact") \
            .eq("user_id", student_id).gte("started_at", since) \
            .order("started_at", desc=True).limit(_SESSION_ROW_CAP).execute()
        sessions = res.data or []
        ses_total = getattr(res, "count", None)
        if not isinstance(ses_total, int):
            ses_total = None
        # Truncation comes from the exact count: PostgREST's ceiling can cut below `.limit()`.
        ses_cut = (ses_total > len(sessions)) if ses_total is not None \
            else len(sessions) >= _SESSION_ROW_CAP
        ses_ok = True
    except Exception as e:                                      # noqa: BLE001
        print(f"[weekly_report:sessions] {e}")
        sessions, ses_total, ses_cut, ses_ok = [], None, False, False
    ses_oldest_day = _school_day(
        min([str(r["started_at"]) for r in sessions if r.get("started_at")], default=""), tz)
    sessions_by_day: dict[str, list] = {}
    for r in sessions:
        sessions_by_day.setdefault(_school_day(r.get("started_at"), tz), []).append(r)

    # The rollup covers days whose raw rows are gone, keyed on (day, channel).
    # Its totals feed the week's averages as (sum, n), never a mean of daily means.
    rolled_totals: dict[str, list] = {k: [0.0, 0] for k in
                                      ("focus", "stress", "engagement",
                                       "heart_rate_bpm", "rmssd_ms")}
    rolled_emotions: dict[str, int] = {}
    rolled_sources: set[str] = set()

    rollup_by: dict[tuple[str, str], dict] = {}
    rollup_ok = True
    # A declined channel's rollup is not read, as its raw rows are not.
    rollup_channels = ["cognitive"] + (["heart"] if include_heart else []) \
        + (["emotion"] if include_emotion else [])
    try:
        for r in (supabase.table("signal_daily_rollup").select("*")
                  .eq("user_id", student_id).in_("channel", rollup_channels)
                  .gte("day", (school_today - timedelta(days=days - 1)).isoformat())
                  .lte("day", school_today.isoformat())
                  .execute().data or []):
            rollup_by[(str(r.get("day")), r.get("channel"))] = r
    except Exception as e:                                      # noqa: BLE001
        print(f"[weekly_report:rollup] {student_id}: {e}")
        rollup_ok = False
    # Days whose raw rows the expiry job may have deleted: with the rollup unread, an
    # empty day there is unknown, not quiet.
    expired_through = _expired_through(school_today)

    # The sessions read is the one that can be cut, so the one with partial days.
    def _coverage(ok: bool, cut: bool, oldest_day: str, day: str) -> tuple[bool, bool]:
        """(nothing was retrieved for this day, this day is complete)."""
        if not ok:
            return True, False          # the read failed; no day was covered
        if not cut:
            return False, True          # nothing was trimmed, so every day is whole
        if not oldest_day:
            # Trimmed with no usable oldest timestamp: missing, not whole.
            return True, False
        if day < oldest_day:
            return True, False          # the cap stopped before this day entirely
        return False, day > oldest_day  # == oldest_day is the day it cut into

    def _mean(agg, key):
        """A day's mean from its SQL sum and count, as `_avg` rounds; None with no readings."""
        n = (agg or {}).get(f"{key}_n") or 0
        return round(float(agg[f"{key}_sum"]) / n, 2) if n else None

    daily = []
    # Channels with a day that may have expired and could not be read from the rollup.
    lost_channels: set[str] = set()
    for i in range(days - 1, -1, -1):
        day = (school_today - timedelta(days=i)).isoformat()
        cog_raw, face_raw, heart_raw = cog_days.get(day), face_days.get(day), heart_days.get(day)
        ses_missing, ses_whole = _coverage(ses_ok, ses_cut, ses_oldest_day, day)
        # Every row is aggregated, so a read that worked covers each day whole.
        cog_whole, face_whole, heart_whole = cog_ok, face_ok, heart_ok
        if (not rollup_ok and expired_through is not None
                and date.fromisoformat(day) <= expired_through):
            # The raw rows may be gone and the rollup is unread: an empty day is unknown.
            for channel, raw, included in (("cognitive", cog_raw, True),
                                           ("emotion", face_raw, include_emotion),
                                           ("heart", heart_raw, include_heart)):
                if included and not raw:
                    lost_channels.add(channel)
            cog_whole = cog_whole and bool(cog_raw)
            face_whole = face_whole and bool(face_raw)
            heart_whole = heart_whole and bool(heart_raw)
        # Skip only when nothing asked for was retrieved; sessions have their own cap.
        if (not cog_ok and (not face_ok or not include_emotion)
                and (not heart_ok or not include_heart) and ses_missing):
            continue

        def _rolled(channel, raw, read_ok):
            """The rollup row for this day when it has no raw rows; never after a failed read."""
            if not read_ok:
                return None
            row = rollup_by.get((day, channel))
            return row if row is not None and not raw else None

        cog_roll = _rolled("cognitive", cog_raw, cog_ok)
        face_roll = _rolled("emotion", face_raw, face_ok) if include_emotion else None
        heart_roll = _rolled("heart", heart_raw, heart_ok) if include_heart else None
        # Only the rollup carries heart categories, so this reads it even beside raw rows.
        heart_rolled = (rollup_by.get((day, "heart")) if include_heart else None) or {}
        raw_sensors = _sensor_class((heart_raw or {}).get("sources"))
        # This day's rows belong to the running lesson only from the day it started.
        day_pending = bool(lesson[0]) and lesson[1] is not None and day >= lesson[1] \
            and bool(int((heart_raw or {}).get("rows") or 0))
        day_arousal = _body_arousal(
            _arousal_counts(heart_rolled.get("stress_counts")),
            _sensor_class(heart_rolled.get("heart_sources")) | raw_sensors,
            day_pending, include_heart, rollup_ok,
            heart_ok and lesson[0] is not None
            and not _unaccounted_heart(heart_raw, bool(heart_rolled), day, lesson))

        daily.append({
            "date": day,
            # Withheld unless the day is whole.
            "focus": (cog_roll.get("avg_focus") if cog_roll else
                      _mean(cog_raw, "focus") if cog_whole else None),
            "stress": (cog_roll.get("avg_stress") if cog_roll else
                       _mean(cog_raw, "stress") if cog_whole else None),
            # From focus, not the stored engagement -- see `_shape_summary`.
            "engagement": (cog_roll.get("avg_focus") if cog_roll else
                           _mean(cog_raw, "focus") if cog_whole else None),
            "attention": _mean(face_raw, "attention") if face_whole else None,
            # None, not 0, for a day the cap could not reach.
            "sessions": len(sessions_by_day.get(day, []))
                        if ses_whole else None,
            # Absolute units, not 0..1 ratios. Trusted only, matching the week's averages.
            "heart_rate_bpm": (heart_roll.get("avg_heart_rate_bpm") if heart_roll else
                               _mean(heart_raw, "bpm") if heart_whole else None),
            "rmssd_ms": (heart_roll.get("avg_rmssd_ms") if heart_roll else
                         _mean(heart_raw, "rmssd") if heart_whole else None),
            "body_arousal": day_arousal["high_share"],
            "body_arousal_windows": day_arousal["classified_windows"],
            "body_arousal_state": day_arousal["state"],
            "body_arousal_few_readings": day_arousal["few_readings"],
            # False = not fully fetched; None = not requested (check `=== false`).
            "cognitive_retrieved": True if cog_roll else cog_whole,
            "face_retrieved": (None if not include_emotion else
                               True if face_roll else face_whole),
            "heart_retrieved": (None if not include_heart else
                                True if heart_roll else heart_whole),
            "sessions_retrieved": ses_whole,

            # Named, so a chart can say it mixes rollup and raw precision.
            "cognitive_from_rollup": bool(cog_roll),
            "face_from_rollup": bool(face_roll),
            "heart_from_rollup": bool(heart_roll),

            # Sample counts keep a thin day visibly thin after raw rows are gone.
            "cognitive_samples": ((cog_roll.get("sample_count") or 0) if cog_roll
                                  else int((cog_raw or {}).get("rows") or 0)),
            # Emotion rows only (gaze-only face rows excluded), matching the rollup.
            "face_samples": ((face_roll.get("sample_count") or 0) if face_roll
                             else int((face_raw or {}).get("emotion_rows") or 0)),
            # Trusted rows, as the day's average is: one definition before and after expiry.
            "heart_samples": ((heart_roll.get("trusted_sample_count") or 0) if heart_roll
                              else int((heart_raw or {}).get("trusted_rows") or 0)),
        })

        # Weighted by `trusted_sample_count`; approximate for `rmssd_ms`.
        if cog_roll:
            n = cog_roll.get("trusted_sample_count") or 0
            for key, col in (("focus", "avg_focus"), ("stress", "avg_stress"),
                             ("engagement", "avg_focus")):  # see `_shape_summary`
                value = cog_roll.get(col)
                weight = _stress_weight(cog_roll) if key == "stress" else n
                if value is not None and weight:
                    rolled_totals[key][0] += float(value) * weight
                    rolled_totals[key][1] += weight
        if heart_roll:
            n = heart_roll.get("trusted_sample_count") or 0
            for key, col in (("heart_rate_bpm", "avg_heart_rate_bpm"),
                             ("rmssd_ms", "avg_rmssd_ms")):
                value = heart_roll.get(col)
                if value is not None and n:
                    rolled_totals[key][0] += float(value) * n
                    rolled_totals[key][1] += n
            rolled_sources.update(heart_roll.get("heart_sources") or ())
        if face_roll:
            for label, count in (face_roll.get("emotion_counts") or {}).items():
                rolled_emotions[label] = rolled_emotions.get(label, 0) + int(count)

    def _raw(days_by_date: dict, key: str) -> tuple[float, int]:
        """`(sum, n)` over the raw days; `_rolled` never takes a day that has raw rows."""
        total, n = 0.0, 0
        for agg in days_by_date.values():
            count = int(agg.get(f"{key}_n") or 0)
            if count:
                total += float(agg.get(f"{key}_sum") or 0)
                n += count
        return total, n

    def _week(key, raw):
        """The week's mean over raw rows and rolled-up days, by true sum and count."""
        total, n = rolled_totals[key]
        return ((total + raw[0]) / (n + raw[1])) if n + raw[1] else None

    focus_raw = _raw(cog_days, "focus")
    stress_raw = _raw(cog_days, "stress")
    # Only trusted heart samples are aggregated, as in the rollup.
    bpm_raw = _raw(heart_days, "bpm")
    rmssd_raw = _raw(heart_days, "rmssd")
    # Which sensor produced the readings (accuracy differs); trusted rows plus rollup days.
    heart_sources = sorted({s for agg in heart_days.values() for s in agg.get("sources") or ()
                            if isinstance(s, str)} | rolled_sources)
    body_arousal = _weekly_body_arousal(include_heart, rollup_ok, rollup_by, heart_days,
                                        heart_sources, heart_ok, lesson)

    # Seeded from the rollup's full distribution, then the raw days on top; trusted only.
    emotion_counts: dict[str, int] = dict(rolled_emotions)
    for agg in face_days.values():
        for label, count in (agg.get("emotion_counts") or {}).items():
            emotion_counts[label] = emotion_counts.get(label, 0) + int(count)

    def _round2(value):
        return None if value is None else round(value, 2)

    avg_focus = _round2(_week("focus", focus_raw))
    avg_stress = _round2(_week("stress", stress_raw))
    attention = _raw(face_days, "attention")
    avg_attention = round(attention[0] / attention[1], 2) if attention[1] else None
    highest_stress = max((float(a["stress_max"]) for a in cog_days.values()
                          if a.get("stress_max") is not None), default=None)
    lowest_focus = min((float(a["focus_min"]) for a in cog_days.values()
                        if a.get("focus_min") is not None), default=None)
    cog_rows = sum(int(a.get("rows") or 0) for a in cog_days.values())
    face_rows = sum(int(a.get("rows") or 0) for a in face_days.values())
    heart_rows = sum(int(a.get("rows") or 0) for a in heart_days.values())

    # Stored as 0..1 ratios.
    def _as_pct(ratio):
        return round(float(ratio) * 100)

    bits = []
    if avg_focus is not None:
        bits.append(f"average focus was {_as_pct(avg_focus)}%")
    if avg_stress is not None:
        # Calm: EEG stress is `1 - calm`, and stated as stress a parent reads it as anxiety.
        bits.append(f"average calm was {_as_pct(_calm_of(avg_stress))}%")
    # No attention sentence: `face_signals.attention` has no producer.
    if bits:
        summary = "This week, " + ", ".join(bits) + "."
    else:
        # Each channel is one of: has readings, recorded nothing, or could not be read.
        # "Nothing recorded" needs a successful read and no readings, rollup included.
        has_heart = bpm_raw[1] > 0 or rolled_totals["heart_rate_bpm"][1] > 0
        # Emotions arrived, trusted or not: the distribution is trusted-only, the record is not.
        has_face = any(d["face_samples"] for d in daily)
        # A rolled-up day's rows arrived too, though its raw rows are gone.
        has_eeg = cog_rows > 0 or any(d["cognitive_from_rollup"] and d["cognitive_samples"]
                                      for d in daily)
        channels = [("EEG", has_eeg, cog_ok and "cognitive" not in lost_channels,
                     eeg_enabled or has_eeg)]
        if include_emotion:
            channels.append(("facial recognition", has_face,
                             face_ok and "emotion" not in lost_channels, True))
        if include_heart:
            channels.append(("heart rate", has_heart,
                             heart_ok and "heart" not in lost_channels, True))
        recorded, measured, unread = [], [], []
        for name, has_data, read_ok, asked in channels:
            if has_data:
                recorded.append(name)
            elif not read_ok:
                unread.append(name)
            elif asked:
                # A declined EEG channel recorded nothing by decision, not by fault.
                measured.append(name)

        parts = []
        if "EEG" in recorded:
            # Rows with no average: poor contact nulls the measurement it would have given.
            parts.append("EEG readings were recorded this week, but none gave a usable "
                         "focus or calm score.")
        others = [name for name in recorded if name != "EEG"]
        if others:
            parts.append(_as_sentence(f"{_list_words(others)} readings were recorded this week"))
        if measured:
            parts.append(f"No {_list_words(measured, 'or')} samples were recorded this week.")
        if unread:
            parts.append(_as_sentence(
                f"{_list_words(unread)} data could not be loaded."))
        summary = " ".join(parts)

    return {
        "student_id": student_id,
        "days": days,
        "since": since,
        # Only the sessions read is capped; the signal reads are SQL aggregates.
        "truncated": ses_cut,
        # "Not requested", as distinct from "recorded nothing". `face_included`
        # is a deprecated alias of `emotion_included`.
        "face_included": include_emotion,
        "emotion_included": include_emotion,
        "heart_included": include_heart,
        # False: the flags above are unknown, not declined.
        "consent_retrieved": consent_retrieved,
        "emotion_revoked_at": emotion_revoked_at,
        "heart_revoked_at": heart_revoked_at,
        # EEG is always read, so no `eeg_included`; the tiles need these to say "Off since".
        "eeg_enabled": eeg_enabled,
        "eeg_revoked_at": eeg_revoked_at,
        "emotion_distribution": (dict(sorted(emotion_counts.items(),
                                             key=lambda kv: (-kv[1], kv[0])))
                                 if include_emotion else None),
        "heart_sources": heart_sources if include_heart else None,
        # Per read, since they fail independently; None = not requested.
        "retrieved": {
            "cognitive": cog_ok,
            "face": face_ok if include_emotion else None,
            "heart": heart_ok if include_heart else None,
            "sessions": ses_ok,
            "rollup": rollup_ok,
        },
        # Rows in the week, untrusted heart included ("measured, unusable").
        "sample_counts": {"cognitive": cog_rows, "face": face_rows,
                          "heart": heart_rows, "sessions": len(sessions)},
        # The exact total, not the capped row count; None when the read failed.
        "sessions_recorded": (ses_total if ses_total is not None else len(sessions)) if ses_ok else None,
        "averages": {
            "focus": avg_focus,
            "stress": avg_stress,
            # From focus, not the stored engagement -- see `_shape_summary`.
            "engagement": _round2(_week("engagement", focus_raw)),
            "face_attention": avg_attention,
            # The score scale(s) the averages above span; see `_scale_range`.
            "score_scale": _scale_range(rollup_by.values()) if rollup_ok else None,
        },
        "highlights": {
            "highest_stress": round(highest_stress, 2) if highest_stress is not None else None,
            "lowest_focus": round(lowest_focus, 2) if lowest_focus is not None else None,
            "dominant_emotion": max(emotion_counts, key=emotion_counts.get) if emotion_counts else None,
            "heart_rate_bpm": _week("heart_rate_bpm", bpm_raw),
            "rmssd_ms": _week("rmssd_ms", rmssd_raw),
        },
        "body_arousal": body_arousal,
        # The usable readings each average above was taken over (raw and rolled-up days).
        "average_samples": {k: rolled_totals[k][1] + raw[1] for k, raw in (
            ("focus", focus_raw), ("stress", stress_raw),
            ("heart_rate_bpm", bpm_raw), ("rmssd_ms", rmssd_raw))},
        # `heart` absent, not null, when the channel was not read.
        "latest": {"cognitive": latest_cognitive, "face": latest_face,
                   **({"heart": latest_heart} if include_heart else {})},
        "daily": daily,
        "summary": summary,
    }


# ─── a child compared with their own usual ────────────────────────────────
# See docs/reporting.md, "A child is compared with their own usual". The floors and
# widths are judgement calls, not measurements.
_USUAL_WEEKS = 8
_USUAL_MIN_DAYS = 5
_USUAL_MIN_WEEKS = 2
# Fewest samples behind a day (or the current period) before it counts.
_USUAL_DAY_FLOOR = {"focus": 60, "calm": 20, "heart_rate_bpm": 30, "rmssd_ms": 30,
                    "body_arousal": _AROUSAL_MIN_WINDOWS}
# The typical range is at least this wide either side of the median, in the measure's unit.
_USUAL_MIN_HALF_WIDTH = {"focus": 0.03, "calm": 0.03, "heart_rate_bpm": 2.0, "rmssd_ms": 3.0,
                         "body_arousal": 0.03}
_USUAL_RATIOS = frozenset({"focus", "calm", "body_arousal"})
_USUAL_HEART = ("heart_rate_bpm", "rmssd_ms", "body_arousal")
_USUAL_MEASURES = ("focus", "calm") + _USUAL_HEART


def _usual_day_value(row: dict, measure: str) -> tuple[float | None, int]:
    """`(value, samples)` one rollup row gives a measure; calm is `1 - avg_stress`."""
    def num(v):
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    trusted = int(row.get("trusted_sample_count") or 0)
    if measure == "focus":
        return num(row.get("avg_focus")), trusted
    if measure == "calm":
        stress = num(row.get("avg_stress"))
        return (None if stress is None else 1.0 - stress), _stress_weight(row)
    if measure == "heart_rate_bpm":
        return num(row.get("avg_heart_rate_bpm")), trusted
    if measure == "rmssd_ms":
        return num(row.get("avg_rmssd_ms")), trusted
    share = _arousal_share(_arousal_counts(row.get("stress_counts")))
    return share["high_share"], share["classified_windows"]


def _usual_points(rows, measure: str, period_start: date, scale: dict | None,
                  sensors: frozenset) -> list[tuple[date, float]]:
    """The prior days a measure's usual is built from, one value per day.

    Drops the current period, thin days, and days on another score scale, calm source
    or heart sensor than the current period's.
    """
    channel = "heart" if measure in _USUAL_HEART else "cognitive"
    points = []
    for r in rows:
        if r.get("channel") != channel:
            continue
        try:
            day = date.fromisoformat(str(r.get("day")))
        except (TypeError, ValueError):
            continue
        if day >= period_start:
            continue
        value, samples = _usual_day_value(r, measure)
        if value is None or samples < _USUAL_DAY_FLOOR[measure]:
            continue
        if channel == "cognitive":
            if r.get("score_scale_min") != scale["min"] or r.get("score_scale_max") != scale["max"]:
                continue
            if measure == "calm" and _calm_sources_union([r.get("calm_sources")]) \
                    != scale["calm_sources"]:
                continue
        elif _sensor_class(r.get("heart_sources")) != sensors:
            continue
        points.append((day, value))
    return points


def _usual_range(values: list[float], measure: str) -> dict:
    """Median and interquartile range of daily values, widened to `_USUAL_MIN_HALF_WIDTH`."""
    median = statistics.median(values)
    p25, _, p75 = statistics.quantiles(values, n=4, method="inclusive")
    width = _USUAL_MIN_HALF_WIDTH[measure]
    low, high = min(p25, median - width), max(p75, median + width)
    if measure in _USUAL_RATIOS:
        low, high = max(0.0, low), min(1.0, high)
    return {k: round(v, 4) for k, v in
            (("median", median), ("p25", p25), ("p75", p75), ("low", low), ("high", high))}


def _round_half_up(x: float) -> int:
    """JavaScript's `Math.round`: halves go up. Python's `round` sends them to even."""
    return math.floor(x + 0.5)


def _usual_display(value: float, measure: str) -> int:
    """A value as the tile prints it (`Math.round`): whole percent for ratios, whole units otherwise."""
    return _round_half_up(value * 100 if measure in _USUAL_RATIOS else value)


def _usual_verdict(current: float, band: dict, measure: str) -> str:
    """`lower` / `higher` / `about_usual`, compared as displayed so a reader can check it."""
    shown = _usual_display(current, measure)
    if shown < _usual_display(band["low"], measure):
        return "lower"
    if shown > _usual_display(band["high"], measure):
        return "higher"
    return "about_usual"


def _usual_blocker(measure: str, scale: dict | None, sensors: frozenset,
                   rolled_cognitive: bool) -> tuple[str, str] | None:
    """`(status, reason)` when the period cannot be set against earlier days, else None.

    The period's scale and sensor come from rolled-up days (raw rows carry neither), so a
    period with none rolled up yet is `pending`: unknown, never "changed".
    """
    if measure in _USUAL_HEART:
        if not sensors:
            return "pending", "period_not_summarised"
        return ("not_comparable", "sensor_changed") if len(sensors) > 1 else None
    if not rolled_cognitive:
        return "pending", "period_not_summarised"
    if scale is None:
        return "not_comparable", "scale_unknown"
    if scale["min"] != scale["max"]:
        return "not_comparable", "mixed_scale"
    if measure == "calm" and len(scale["calm_sources"]) > 1:
        return "not_comparable", "mixed_scale"
    return None


def _usual_rollup_query(period_start: date, period_end: date, include_heart: bool):
    """The baseline's rollup read, before its `user_id` filter; a declined heart channel is not asked for."""
    since = period_start - timedelta(weeks=_USUAL_WEEKS)
    channels = ["cognitive"] + (["heart"] if include_heart else [])
    return (supabase.table("signal_daily_rollup")
            .select("user_id, day, channel, avg_focus, avg_stress, avg_heart_rate_bpm, "
                    "avg_rmssd_ms, stress_counts, trusted_sample_count, stress_sample_count, "
                    "score_scale_min, score_scale_max, calm_sources, heart_sources")
            .in_("channel", channels)
            .gte("day", since.isoformat()).lte("day", period_end.isoformat()))


def _personal_baseline(student_id: str, period_start: date, period_end: date, current: dict,
                       include_heart: bool, consent_retrieved: bool = True,
                       rollup: tuple[list, bool] | None = None) -> dict:
    """Each measure this period against the student's own prior weeks, from the rollup.

    `current`: see `_usual_from_rows`. `rollup` is `(rows, retrieved)` a caller already read
    over a range covering this one; without it this reads. Never raises.
    """
    if rollup is not None:
        rows, retrieved = rollup
    else:
        rows, retrieved = [], True
        try:
            rows = (_usual_rollup_query(period_start, period_end, include_heart)
                    .eq("user_id", student_id).execute().data or [])
        except Exception as e:                                 # noqa: BLE001
            print(f"[personal_baseline] {student_id}: {e}")
            retrieved = False
    return _usual_from_rows(rows, retrieved, period_start, current, include_heart,
                            consent_retrieved)


_POSTGREST_MAX_ROWS = 1000


def _usual_batch_size(period_start: date, period_end: date, include_heart: bool) -> int:
    """Students per baseline read, so a full chunk stays under the row cap and is never cut."""
    days = (period_end - (period_start - timedelta(weeks=_USUAL_WEEKS))).days + 1
    rows_per_student = days * (2 if include_heart else 1)
    return max(1, (_POSTGREST_MAX_ROWS - 1) // rows_per_student)


def _personal_baselines_many(currents: dict[str, dict], include_heart: dict[str, bool],
                             consent_retrieved: dict[str, bool], period_start: date,
                             period_end: date) -> dict[str, dict]:
    """`_personal_baseline` for many students, one read per consent group and chunk.

    A chunk that fails, or comes back at the row cap (so possibly cut), is `not_retrieved`
    for its own students only. Never raises.
    """
    groups: dict[bool, list[str]] = {}
    for sid in currents:
        groups.setdefault(bool(include_heart.get(sid)), []).append(sid)
    out = {}
    for heart, ids in groups.items():
        size = _usual_batch_size(period_start, period_end, heart)
        for i in range(0, len(ids), size):
            chunk = ids[i:i + size]
            rows, retrieved = [], True
            try:
                rows = (_usual_rollup_query(period_start, period_end, heart)
                        .in_("user_id", chunk).execute().data or [])
                retrieved = len(rows) < _POSTGREST_MAX_ROWS
            except Exception as e:                             # noqa: BLE001
                print(f"[personal_baselines_many] {len(chunk)} students: {e}")
                retrieved = False
            for sid in chunk:
                mine = [r for r in rows if str(r.get("user_id")) == str(sid)] if retrieved else []
                out[sid] = _usual_from_rows(mine, retrieved, period_start, currents[sid], heart,
                                            consent_retrieved.get(sid, True))
    return out


def _usual_from_rows(rows: list, retrieved: bool, period_start: date, current: dict,
                     include_heart: bool, consent_retrieved: bool) -> dict:
    """The `usual` payload from one student's rollup rows (a read that failed has none).

    `current` maps a measure to `(value, samples)`, plus optional `heart_sources`, `unread`
    (channels whose current figures failed to load) and `arousal_pending`.
    """
    since = period_start - timedelta(weeks=_USUAL_WEEKS)
    channels = ["cognitive"] + (["heart"] if include_heart else [])
    rows = [r for r in rows if r.get("channel") in channels and str(r.get("day")) >= since.isoformat()]

    def in_period(r):
        return str(r.get("day")) >= period_start.isoformat()
    period_rows = [r for r in rows if in_period(r)]
    rolled_heart = [r for r in period_rows if r.get("channel") == "heart"]
    scale = _scale_range(period_rows)
    sensors = _sensor_class(current.get("heart_sources")) | _sensor_class(
        s for r in rolled_heart for s in (r.get("heart_sources") or ()))
    rolled_cognitive = any(r.get("channel") == "cognitive" for r in period_rows)
    # Body arousal has no other source than the rollup, so its current value is read here.
    counts = (0, 0, 0, 0)
    for r in rolled_heart:
        counts = _add_counts(counts, _arousal_counts(r.get("stress_counts")))
    unread = set(current.get("unread") or ())
    if "arousal_pending" in current:
        # The weekly report worked this out from its own raw rows; None is "could not check".
        pending = current["arousal_pending"]
        raw_ok = pending is not None
    else:
        # Heart rows of any quality, poor contact included; None if that could not be read.
        heart_rows = current.get("heart_rows", bool((current.get("heart_rate_bpm") or (None, 0))[1]))
        lesson = current.get("lesson_pending", False)
        pending = bool(lesson)
        # Rows nothing rolled up and no running lesson explains were never summarised.
        raw_ok = heart_rows is not None and lesson is not None \
            and not (heart_rows and not rolled_heart and not lesson)
    raw_ok = raw_ok and "heart" not in unread
    arousal = _body_arousal(counts, sensors, bool(pending), include_heart, retrieved, raw_ok)
    current = {**current, "body_arousal": (arousal["high_share"], arousal["classified_windows"])}

    measures = {}
    for m in _USUAL_MEASURES:
        value, samples = current.get(m) or (None, 0)
        entry = {"status": None, "verdict": None, "reason": None, "current": None,
                 "median": None, "p25": None, "p75": None, "low": None, "high": None,
                 "days_used": 0, "weeks_used": 0}
        channel = "heart" if m in _USUAL_HEART else "cognitive"
        blocker = _usual_blocker(m, scale, sensors, rolled_cognitive)
        if not retrieved:
            entry["status"] = "not_retrieved"
        elif m in _USUAL_HEART and not include_heart:
            entry["status"] = "not_requested"
        elif channel in unread and m != "body_arousal":
            # This period's figure failed to load: not a quiet period (CLAUDE.md rule 1).
            entry.update(status="not_retrieved", reason="current_unread")
        elif value is None:
            entry["status"] = "no_current"
        elif samples < _USUAL_DAY_FLOOR[m]:
            entry.update(status="too_little_this_period", current=round(value, 4))
        elif blocker:
            entry.update(status=blocker[0], reason=blocker[1], current=round(value, 4))
        else:
            points = _usual_points(rows, m, period_start, scale, sensors)
            weeks = {_week_start(d) for d, _ in points}
            entry.update(current=round(value, 4), days_used=len(points), weeks_used=len(weeks))
            if len(points) < _USUAL_MIN_DAYS or len(weeks) < _USUAL_MIN_WEEKS:
                entry["status"] = "not_enough_history"
            else:
                band = _usual_range([v for _, v in points], m)
                entry.update(status="compared", verdict=_usual_verdict(value, band, m), **band)
        measures[m] = entry
    return {"retrieved": retrieved, "consent_retrieved": consent_retrieved,
            "heart_included": include_heart,
            "from": since.isoformat(), "to": (period_start - timedelta(days=1)).isoformat(),
            "max_weeks": _USUAL_WEEKS, "min_days": _USUAL_MIN_DAYS, "min_weeks": _USUAL_MIN_WEEKS,
            # The period's body arousal with its reason, as `_body_arousal` gives it everywhere.
            "body_arousal": arousal,
            "measures": measures}


def _open_sessions(student_ids) -> dict[str, list[str]] | None:
    """`{student: [started_at, ...]}` of every session still open (`ended_at` null); None if unread."""
    ids = [str(s) for s in student_ids]
    if not ids:
        return {}
    try:
        rows = supabase.table("sessions").select("user_id, started_at").in_("user_id", ids) \
            .is_("ended_at", "null").execute().data or []
    except Exception as e:                                     # noqa: BLE001
        print(f"[open_sessions] {len(ids)} students: {e}")
        return None
    out: dict[str, list[str]] = {}
    for r in rows:
        if r.get("started_at"):
            out.setdefault(str(r.get("user_id")), []).append(str(r["started_at"]))
    return out


def _running_starts(open_sessions: dict[str, list[str]]) -> dict[str, str]:
    """`{student: started_at}` of the earliest open session that is a lesson still running.

    Running: started inside `_SESSION_ABANDONED_AFTER_SEC`. An older open session is one the
    sweep has not closed yet (a sleeping laptop): not in progress, though it may hold rows.
    """
    cutoff = _utc_now() - timedelta(seconds=_SESSION_ABANDONED_AFTER_SEC)
    out = {}
    for sid, starts in open_sessions.items():
        running = [s for s in starts if (_parse_ts(s) or cutoff) > cutoff]
        if running:
            out[sid] = min(running, key=lambda s: _parse_ts(s))
    return out


def _open_lessons(student_ids) -> dict[str, str] | None:
    """`{student: started_at}` of the earliest lesson still running; None if unread."""
    sessions = _open_sessions(student_ids)
    return None if sessions is None else _running_starts(sessions)


# `open_sessions` not supplied: the helper reads it. A caller that already has it passes it.
_READ = object()


def _any_open_session(student_id: str, include_heart: bool, open_sessions=_READ) -> bool | None:
    """Whether any session is still open, running or not yet swept; None if unread.

    Either may hold heart rows the rollup has not counted. Asked only when heart is consented.
    """
    if not include_heart:
        return False
    if open_sessions is _READ:
        open_sessions = _open_sessions([student_id])
    return None if open_sessions is None else bool(open_sessions.get(str(student_id)))


def _lesson_heart(open_starts: dict[str, str]) -> dict[str, bool | None]:
    """Whether each running lesson has any heart row yet (poor contact included); None if unread.

    One `limit(1)` read per running lesson, so it scales with lessons open, not students.
    """
    out: dict[str, bool | None] = {}
    for sid, start in open_starts.items():
        try:
            out[sid] = bool(supabase.table("heart_signals").select("ts").eq("user_id", sid)
                            .gte("ts", start).limit(1).execute().data)
        except Exception as e:                                 # noqa: BLE001
            print(f"[lesson_heart] {sid[:8]}: {e}")
            out[sid] = None
    return out


def _running_heart_lessons(student_ids, open_sessions=_READ
                           ) -> dict[str, tuple[bool | None, str | None]]:
    """Per student, `(pending, started_at)`: a running lesson with heart rows. None if unread."""
    if open_sessions is _READ:
        open_sessions = _open_sessions(student_ids)
    if open_sessions is None:
        return {str(s): (None, None) for s in student_ids}
    # Consent gates the read: only the students asked about (heart-consented) reach heart_signals,
    # whatever wider set `open_sessions` was read for.
    wanted = {str(s) for s in student_ids}
    starts = {s: t for s, t in _running_starts(open_sessions).items() if s in wanted}
    heart = _lesson_heart(starts)
    return {str(s): (heart.get(str(s)), starts[str(s)]) if str(s) in starts else (False, None)
            for s in student_ids}


def _heart_rows_many(student_ids, days: int) -> dict[str, bool | None]:
    """Whether any heart row (poor contact included) landed per student in the window, in one read.

    A result at the row cap may be cut, so a student it did not reach is None, not False.
    """
    ids = [str(s) for s in student_ids]
    if not ids:
        return {}
    try:
        rows = supabase.table("heart_signals").select("user_id").in_("user_id", ids) \
            .gte("ts", _window_start(days).isoformat()).limit(_POSTGREST_MAX_ROWS) \
            .execute().data or []
    except Exception as e:                                     # noqa: BLE001
        print(f"[heart_rows_many] {len(ids)} students: {e}")
        return {s: None for s in ids}
    seen = {str(r.get("user_id")) for r in rows}
    cut = len(rows) >= _POSTGREST_MAX_ROWS
    return {s: True if s in seen else (None if cut else False) for s in ids}


def _lesson_context(summaries: dict[str, dict], heart: dict[str, bool], days: int,
                    any_rows: dict[str, bool | None] | None = None,
                    open_sessions=_READ) -> dict[str, dict]:
    """Per student, `lesson_pending` and `heart_rows` for `_usual_from_rows`; None where unread.

    `lesson_pending`: a running lesson with heart rows. A summary counts usable samples only, so
    for one with none this asks, in one read, whether any row landed; `any_rows` passes answers had.
    """
    consented = [s for s in summaries if heart.get(s)]
    running = _running_heart_lessons(consented, open_sessions)
    known = dict(any_rows or {})
    ask = [s for s in consented if not summaries[s].get("heart_samples") and s not in known]
    known.update(_heart_rows_many(ask, days))
    out = {}
    for sid, summary in summaries.items():
        if not heart.get(sid):
            out[sid] = {"lesson_pending": False, "heart_rows": False}
            continue
        rows = True if summary.get("heart_samples") else known.get(sid, False)
        out[sid] = {"lesson_pending": running[sid][0], "heart_rows": rows}
    return out


def _school_today(tz=None) -> date:
    """Today in the school's timezone."""
    return _utc_now().astimezone(tz or _school_timezone()).date()


def _report_period(days: int) -> tuple[date, date]:
    """The first and last school day of a `days`-long report window."""
    today = _school_today()
    return today - timedelta(days=days - 1), today


def _usual_current(summary: dict) -> dict:
    """A signal summary's figures as `_personal_baseline`'s `current`; a failed read is `unread`."""
    eeg_n, heart_n = summary.get("cognitive_samples") or 0, summary.get("heart_samples") or 0
    return {"focus": (summary.get("focus"), eeg_n),
            "calm": (_calm_of(summary.get("stress")), eeg_n),
            "heart_rate_bpm": (summary.get("heart_rate_bpm"), heart_n),
            "rmssd_ms": (summary.get("rmssd_ms"), heart_n),
            "unread": ["cognitive", "heart"] if summary.get("retrieved") is False else []}


def _calm_of(stress) -> float | None:
    return None if not isinstance(stress, (int, float)) or isinstance(stress, bool) \
        else 1.0 - float(stress)


# ─── question prefetch cache ──────────────────────────────────────────────
# Up to QUEUE_SIZE pre-generated questions per user. 0 (default) disables it: with a
# billed model, questions an abandoned session never answers are wasted spend.
QUESTION_QUEUE_SIZE_DEFAULT = 0
QUEUE_SIZE = env_number("QUESTION_QUEUE_SIZE", QUESTION_QUEUE_SIZE_DEFAULT, int, minimum=0)
_prefetch_cache: dict[str, dict[tuple, list]] = {}   # user_id → {`_prefetch_key`: questions}
_prefetch_lock = threading.Lock()
_prefetch_active: dict[str, dict[tuple, int]] = {}   # user_id → {`_prefetch_key`: in-flight workers}
# Idle queues kept per student, most recently served last: a switch back reuses one. Busy ones stay
# until their work lands; the session's in-flight cap bounds how many that can be.
_PREFETCH_KEPT_QUEUES = 3


def _prefetch_key(grade, bias: int, session_id: str | None) -> tuple:
    """What a prepared question must match to be served: grade by number ("Grade 7" is "7th Grade"),
    bias, and session, since it was chosen from that session's accuracy and signals."""
    return (grade_levels.served_grade_number(grade), bias, session_id)


def _prefetch_done(user_id: str, key: tuple):
    """One in-flight worker for this queue has finished, or never started."""
    with _prefetch_lock:
        counts = _prefetch_active.get(user_id, {})
        left = counts.get(key, 0) - 1
        if left > 0:
            counts[key] = left
            return
        counts.pop(key, None)
        if not counts:
            _prefetch_active.pop(user_id, None)


def _drop_prefetched(user_id: str, session_id: str):
    """A closed session's queues, which nothing can serve; its in-flight results then have nowhere to land."""
    with _prefetch_lock:
        queues = _prefetch_cache.get(user_id, {})
        for key in [k for k in queues if k[2] == session_id]:
            del queues[key]

# Sized to `GENERATION_MAX_CONCURRENCY`; more workers would only block on its semaphore.
_PREFETCH_POOL: ThreadPoolExecutor | None = None
_prefetch_pool_lock = threading.Lock()


def _prefetch_pool() -> ThreadPoolExecutor:
    global _PREFETCH_POOL
    with _prefetch_pool_lock:
        if _PREFETCH_POOL is None:
            _PREFETCH_POOL = ThreadPoolExecutor(
                max_workers=llm_client.GENERATION_MAX_CONCURRENCY,
                thread_name_prefix="prefetch")
        return _PREFETCH_POOL


def _shutdown_prefetch_pool():
    """Cancel queued prefetch on shutdown; not joined, since a worker may be stuck on the model."""
    global _PREFETCH_POOL
    with _prefetch_pool_lock:
        pool, _PREFETCH_POOL = _PREFETCH_POOL, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


# Per-student generations per window (a rate, unlike `_prefetch_active`'s concurrency).
# 20/min is still above what answering can consume, prefetch refills included.
_GENERATION_RATE_LIMIT  = env_number("GENERATION_RATE_LIMIT", 20, int, minimum=1)
_GENERATION_RATE_WINDOW = env_number("GENERATION_RATE_WINDOW", 60.0, float, minimum=1.0)
_GENERATION_LIMITER = _SlidingWindowLimiter(
    "generation", _GENERATION_RATE_LIMIT, _GENERATION_RATE_WINDOW)

# Questions served per student per school day. GENERATION_DAILY_CALL_LIMIT is one process-wide ceiling,
# so without this one account could spend it for every student. Counted by `claim_daily_question`;
# the limiter below is only its fallback when that call fails.
_GENERATION_DAILY_LIMIT = env_number("GENERATION_DAILY_LIMIT_PER_STUDENT", 150, int, minimum=1)
_GENERATION_DAILY_LIMITER = _SlidingWindowLimiter(
    "generation_daily", _GENERATION_DAILY_LIMIT, 86400.0)
# Prefetch makes questions nobody may see, so it spends its own budget, never the student's.
_PREFETCH_DAILY_LIMITER = _SlidingWindowLimiter(
    "prefetch_daily", _GENERATION_DAILY_LIMIT, 86400.0)

# Requests in flight on generation, process-wide; past it, refuse rather than queue.
# Waiters block anyio's ~40-slot threadpool, so 30 leaves headroom for ingest.
_GENERATION_MAX_WAITERS = env_number("GENERATION_MAX_WAITERS", 30, int, minimum=1)
_generation_waiters = threading.BoundedSemaphore(_GENERATION_MAX_WAITERS)


@contextlib.contextmanager
def _generation_waiter():
    """Admit this caller to wait on the model, or yield False.

    A context manager so the permit is released on every exit path; a leaked
    one is permanent. Non-blocking, so a refusal holds no threadpool slot.
    """
    admitted = _generation_waiters.acquire(blocking=False)
    try:
        yield admitted
    finally:
        if admitted:
            _generation_waiters.release()


def _no_refund() -> None:
    return None


def _fallback_daily_claim(user_id: str):
    """`_claim_daily_question`'s answer from the per-process limiter."""
    wait = _GENERATION_DAILY_LIMITER.check(user_id)
    if wait is not None:
        return wait, _no_refund
    return None, lambda: _GENERATION_DAILY_LIMITER.release(user_id)


def _claim_daily_question(user_id: str):
    """Count one served question against the student's school day: (None, refund) to admit, else (seconds, no-op).

    Call the refund if no question reaches the student. A failed or unexpected answer falls back
    to the per-process limiter rather than refusing everyone.
    """
    tz = _school_timezone()
    now = datetime.now(tz)
    day = now.date().isoformat()
    try:
        admitted = supabase.rpc("claim_daily_question", {
            "p_user_id": user_id, "p_day": day,
            "p_limit": _GENERATION_DAILY_LIMIT}).execute().data
    except Exception as e:
        print(f"[generate] daily budget unreadable for {user_id[:8]}: {type(e).__name__}")
        admitted = None
    if admitted is True:
        def refund():
            # The claim's own day, not the refund's: a failure just past midnight gives back yesterday's.
            try:
                supabase.rpc("release_daily_question", {"p_user_id": user_id, "p_day": day}).execute()
            except Exception as e:
                print(f"[generate] could not refund a question for {user_id[:8]}: {type(e).__name__}")
        return None, refund
    if admitted is not False:
        return _fallback_daily_claim(user_id)
    midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    # Through UTC: aware datetimes sharing a tzinfo subtract as wall clocks, wrong across a DST change.
    return max(1, int((midnight.astimezone(timezone.utc)
                       - now.astimezone(timezone.utc)).total_seconds()) + 1), _no_refund


def _generation_refusal(user_id: str, generating: bool = True):
    """Count one served question against the rate (if generating now), then the day.

    (None, refund) to admit, else (limiter, seconds to wait).
    """
    if generating:
        wait = _GENERATION_LIMITER.check(user_id)
        if wait is not None:
            return _GENERATION_LIMITER, wait
    wait, refund = _claim_daily_question(user_id)
    if wait is not None:
        return _GENERATION_DAILY_LIMITER, wait
    return None, refund


def _claim_generation_slot(user_id: str) -> bool:
    """For the prefetch worker: the rate, then prefetch's own day budget. Nobody to tell on a refusal."""
    return all(limiter.check(user_id) is None
               for limiter in (_GENERATION_LIMITER, _PREFETCH_DAILY_LIMITER))


def _admit_generation(user_id: str, generating: bool = True):
    """Count one served question and return its refund, or record the refusal and raise 429.

    `generating=False` for a queue hit. Call the refund if no question reaches the student.
    """
    limiter, outcome = _generation_refusal(user_id, generating)
    if limiter is None:
        return outcome
    wait = outcome
    # `get_user` resolved this id, so the refusal has a real actor.
    if limiter is _GENERATION_DAILY_LIMITER:
        _record_security_event("rate_limited", user_id, limiter=_GENERATION_DAILY_LIMITER.name)
        raise HTTPException(429, "You've reached today's question limit. Try again tomorrow.",
                            headers={"Retry-After": str(max(1, int(wait)))})
    _record_security_event("rate_limited", user_id, limiter=_GENERATION_LIMITER.name)
    raise HTTPException(429, "Too many questions requested. Try again shortly.",
                        headers={"Retry-After": str(max(1, int(_GENERATION_RATE_WINDOW)))})


def _prefetch_worker(user_id: str, grade: str, bias: int, session_id: str | None):
    key = _prefetch_key(grade, bias, session_id)
    try:
        # No security event: a skipped refill refuses nobody.
        if not _claim_generation_slot(user_id):
            print(f"[prefetch] rate limit reached for {user_id[:8]}; not refilling")
            return
        question = LLM_topic_decider.LLM_single_prompt_topic_and_difficulty_decider(
            user_id, grade, session_id, bias
        )
        if isinstance(question, dict):
            with _prefetch_lock:
                # `_ensure_queue` made the queue; gone means its session closed or the cap pushed it out.
                queue = _prefetch_cache.get(user_id, {}).get(key)
                if queue is not None:
                    queue.append(question)
            ops_metrics.bump("question", "prefetched" if queue is not None else "prefetch_discarded")
        else:
            ops_metrics.bump("question", "prefetch_failed")
    except Exception as e:
        print(f"[prefetch] failed for {user_id[:8]}: {e}")
        ops_metrics.bump("question", "prefetch_failed")
    finally:
        # A count, not a flag: one worker finishing must not clear the others.
        _prefetch_done(user_id, key)

def _ensure_queue(user_id: str, grade: str, bias: int, session_id: str | None = None):
    """Spawn workers until this queue + its in-flight workers reach QUEUE_SIZE.

    The session's in-flight workers across all its queues are capped at QUEUE_SIZE too, so flipping
    Easier/Harder cannot hold more of the shared slots; an ended session's do not count against it.
    """
    key = _prefetch_key(grade, bias, session_id)
    with _prefetch_lock:
        queues   = _prefetch_cache.get(user_id, {})
        queued   = len(queues.get(key, []))
        counts   = _prefetch_active.get(user_id, {})
        inflight = counts.get(key, 0)
        session  = sum(n for k, n in counts.items() if k[2] == session_id)
        needed   = min(QUEUE_SIZE - queued - inflight, QUEUE_SIZE - session)
        if needed <= 0:
            return
        _prefetch_active.setdefault(user_id, {})[key] = inflight + needed
        _prefetch_cache.setdefault(user_id, {}).setdefault(key, [])
    for _ in range(needed):
        try:
            _prefetch_pool().submit(_prefetch_worker, user_id, grade, bias, session_id)
        except Exception as e:                                 # noqa: BLE001
            # E.g. the pool shut down. Roll back the count: a worker that never
            # starts never decrements, and the queue would never refill.
            _prefetch_done(user_id, key)
            print(f"[prefetch] could not queue for {user_id[:8]}: {e}")

# ─── models ──────────────────────────────────────────────────────────────


class StrictModel(BaseModel):
    """A request model that refuses a field it does not declare (defence in depth).

    The real barrier is handlers naming the columns they write. Sidecar ingest
    models are exempt (version skew would 422 a whole batch); `test_input_bounds.py` pins that.
    """

    model_config = ConfigDict(extra="forbid")


# Free-text caps: the guarded columns are unbounded `text`, so these are their only bound.
_NAME_MAX      = 100    # a display name, a class name
_TITLE_MAX     = 200    # a session title, which nothing but the student reads
_ID_MAX        = 64     # a uuid is 36; `device_id` is a short name like "default"
_SHORT_MAX     = 32     # a join code (generated as 6), a channel name
_TIMEZONE_MAX  = 64     # "America/Argentina/ComodRivadavia" is 32


class StartSessionRequest(StrictModel):
    title: str | None = Field(None, max_length=_TITLE_MAX)

class AnswerPayload(StrictModel):
    # A uuid here, or the RPC's 22P02 reads as a missing session.
    question_id:    UUID
    selected_index: int
    correct:        bool

# `grade_level` reaches model prompts; `grade_for_prompt` canonicalises it there,
# and this shared validator refuses unreadable values before storage.
def _grade_level_field(cls, v):
    return grade_levels.validated_grade(v)


class CreateClassRequest(StrictModel):
    name: str = Field(max_length=_NAME_MAX)
    grade_level: str | None = None

    _check_grade = field_validator("grade_level")(classmethod(_grade_level_field))

class UpdateClassRequest(StrictModel):
    name: str | None = Field(None, max_length=_NAME_MAX)
    grade_level: str | None = None

    _check_grade = field_validator("grade_level")(classmethod(_grade_level_field))

class JoinClassRequest(StrictModel):
    join_code: str = Field(max_length=_SHORT_MAX)

class LinkChildRequest(StrictModel):
    # The child's own code, never their user id (which is not a secret).
    link_code: str = Field(max_length=_LINK_CODE_LEN * 2)

class UpdateProfileRequest(StrictModel):
    display_name: str | None = Field(None, max_length=_NAME_MAX)
    grade_level:  str | None = None
    # Mirrors the DB CHECKs, so a bad value is a 422 naming the field, not a 500.
    difficulty_bias:          int | None = Field(None, ge=-1, le=1)
    session_duration_minutes: int | None = Field(None, ge=5, le=180)
    practice_reminders:       bool | None = None

    _check_grade = field_validator("grade_level")(classmethod(_grade_level_field))

class EegSessionRequest(StrictModel):
    session_id: str = Field(max_length=_ID_MAX)
    device_id: str | None = Field(None, max_length=_ID_MAX)
    # `/api/eeg/start` only. False pairs without writing rows; True arms the poller.
    record: bool = True


# ─── profiles ────────────────────────────────────────────────────────────

# What the account's own pages read; named, so a new profiles column does not reach them by existing.
_PROFILE_SELF_COLUMNS = ("id, display_name, email, role, grade_level, difficulty_bias, "
                         "session_duration_minutes, practice_reminders, created_at")


def _own_profile(uid: str) -> dict:
    """The caller's own row; a failed or missing read is an error, never `_profile`'s placeholder."""
    try:
        rows = supabase.table("profiles").select(_PROFILE_SELF_COLUMNS) \
            .eq("id", uid).limit(1).execute().data
    except Exception as e:                                     # noqa: BLE001
        print(f"[profile] could not read {uid[:8]}: {e}")
        raise HTTPException(503, "Your profile could not be loaded")
    if not rows:
        raise HTTPException(404, "No profile exists for this account")
    return rows[0]


@app.get("/api/profile/me")
def get_my_profile(request: Request):
    """The caller's own row.

    Never the placeholder, which is role "student": a teacher greeted by it was routed into the
    student app, where an error lets AuthContext fall back to the sign-up claim.
    """
    return _own_profile(get_user(request)["id"])

@app.put("/api/profile/me")
def update_my_profile(payload: UpdateProfileRequest, request: Request):
    user = get_user(request)
    # Named columns, never `payload.dict()`: the service-role client bypasses
    # column grants, so this is what keeps a posted `role` out of `profiles`.
    fields = {
        name: value for name, value in (
            ("display_name", payload.display_name),
            ("grade_level", payload.grade_level),
            ("difficulty_bias", payload.difficulty_bias),
            ("session_duration_minutes", payload.session_duration_minutes),
            ("practice_reminders", payload.practice_reminders),
        ) if value is not None
    }
    stored = None
    if fields:
        fields["updated_at"] = _utc_now().isoformat()
        # The row as stored, asked for by name: empty then means no row, and no second read can fail.
        stored = supabase.table("profiles").update(fields, returning=ReturnMethod.representation) \
            .eq("id", user["id"]).execute().data or []
    if payload.display_name is not None:
        try:
            supabase.auth.admin.update_user_by_id(
                user["id"],
                {"user_metadata": {**(user.get("user_metadata") or {}),
                                   "display_name": payload.display_name}},
            )
        except Exception as e:
            print("metadata sync failed:", e)
    if stored is None:
        return _own_profile(user["id"])
    if not stored:
        raise HTTPException(404, "No profile exists for this account")
    # Not `_profile`'s placeholder: the page adopts what a save returns and sends it on the next tap.
    columns = [c.strip() for c in _PROFILE_SELF_COLUMNS.split(",")]
    return {c: stored[0][c] for c in columns if c in stored[0]}


# ─── questions ───────────────────────────────────────────────────────────

class _TTLCache:
    """A tiny LRU+TTL cache for static, display-only reads; never signal/consent/session data.

    `max_size` bounds entry count, not entry size: a caller whose key decides
    how much an entry holds must clamp that part of the key too.
    """
    def __init__(self, ttl: float, max_size: int = 256):
        self._ttl = ttl
        self._max_size = max_size
        self._lock = threading.Lock()
        # key -> (expires_at, value), ordered least- to most-recently-used.
        self._store: "collections.OrderedDict" = collections.OrderedDict()

    def get(self, key):
        with self._lock:
            hit = self._store.get(key)
            if hit is None:
                return None, False
            expires_at, value = hit
            if expires_at < time.monotonic():
                del self._store[key]
                return None, False
            self._store.move_to_end(key)
            return value, True

    def set(self, key, value):
        with self._lock:
            self._store.pop(key, None)
            if len(self._store) >= self._max_size:
                self._store.popitem(last=False)
            self._store[key] = (time.monotonic() + self._ttl, value)


# Teacher question-bank reads only; the live adaptive path stays uncached.
QUESTIONS_CACHE_TTL = env_number("QUESTIONS_CACHE_TTL", 30.0, float, minimum=1.0)
# Entries up to `_QUESTIONS_MAX` rows each, keyed on caller strings; real traffic uses two keys.
_questions_cache = _TTLCache(QUESTIONS_CACHE_TTL, max_size=32)

# The largest any surface asks for (`questionsCache.js` defaults to 1000).
_QUESTIONS_MAX = 1000

@app.get("/api/questions")
def get_questions(limit: int = 100, subject: str | None = None, difficulty: str | None = None):
    """The question bank, newest first. `limit` is clamped to bound cache entry size."""
    limit = max(1, min(limit, _QUESTIONS_MAX))
    # Key on the clamped, normalised values that decide the query (`?subject=` == absent).
    subject = subject or None
    difficulty = difficulty or None
    key = (limit, subject, difficulty)
    cached, hit = _questions_cache.get(key)
    if hit:
        return cached
    # A retired row keeps a wrong key that past answers were marked against; the bank hides it.
    q = supabase.table("questions").select("*").is_("retired_at", "null") \
        .order("created_at", desc=True).limit(limit)
    if subject:    q = q.eq("subject", subject)
    if difficulty: q = q.eq("difficulty", difficulty)
    data = q.execute().data or []
    _questions_cache.set(key, data)
    return data


@app.get("/api/questions/count")
def count_questions(subject: str | None = None, difficulty: str | None = None):
    """How many questions exist, via `count="exact"`, without transferring them."""
    try:
        q = supabase.table("questions").select("id", count="exact").is_("retired_at", "null").limit(1)
        if subject:    q = q.eq("subject", subject)
        if difficulty: q = q.eq("difficulty", difficulty)
        res = q.execute()
        return {"total": res.count if res.count is not None else 0, "retrieved": True}
    except Exception as e:                                     # noqa: BLE001
        print(f"[questions] could not count: {e}")
        return {"total": None, "retrieved": False}


_STUDENT_QUESTIONS_MAX = 200


@app.get("/api/students/{student_id}/questions")
def student_questions(student_id: str, request: Request, limit: int = 100):
    """The questions one student has been asked, newest answer first, one row per question.

    Separate from /api/questions because which questions a named child was
    asked is student data and needs the relationship check. `session_id` is
    the most recent session it was asked in.
    """
    _verify_can_view_student(get_user(request), student_id)
    limit = max(1, min(limit, _STUDENT_QUESTIONS_MAX))

    # Embedded (left join): an expired question arrives as `questions: null`.
    rows = (supabase.table("session_answers")
            .select("question_id, session_id, correct, answered_at, "
                    "questions(question_text, subject, difficulty)")
            .eq("user_id", student_id)
            .order("answered_at", desc=True)
            .limit(limit).execute().data or [])

    collapsed: dict = {}
    for r in rows:
        q = r.get("questions")
        qid = r.get("question_id")
        # Skipped (counted below), or they would all merge under a `None` key.
        if not q or not qid:
            continue
        entry = collapsed.get(qid)
        if entry is None:
            entry = collapsed[qid] = {
                "question_id":   qid,
                "question_text": q.get("question_text"),
                "subject":       q.get("subject"),
                "difficulty":    q.get("difficulty"),
                # Rows are newest-first, so the first seen is the most recent.
                "last_answered_at": r.get("answered_at"),
                "session_id":       r.get("session_id"),
                "attempts": 0,
                "correct":  0,
            }
        entry["attempts"] += 1
        if r.get("correct"):
            entry["correct"] += 1

    return {
        "student_id": student_id,
        "questions": list(collapsed.values()),
        # Separates "answered nothing" from "questions aged out of the bank".
        "answers_read": len(rows),
        "expired_questions": sum(1 for r in rows if not r.get("questions")),
        "truncated": len(rows) == limit,
    }


# ─── llm generation ──────────────────────────────────────────────────────

def _record_adaptive_decision(user_id: str, session_id: str, question: dict, decision: dict) -> None:
    """Store why this served question's difficulty was eased, raised or held. Never raises.

    Signal-derived, so it expires with the per-sample rows and goes with any erasure
    (20261012000000). A failed write costs the record, never the question, and is counted for the admin panel.
    """
    try:
        supabase.table("adaptive_decisions").insert({
            "session_id": session_id, "user_id": user_id,
            "served_from": question.get("served_from"), "difficulty": question.get("difficulty"),
            "bias": decision.get("bias"), "why": decision.get("why"), "label": decision.get("label"),
            "opinions": decision.get("opinions") or [],
            "increase_withheld": bool(decision.get("increase_withheld")),
        }, returning=ReturnMethod.minimal).execute()
    except Exception as e:                                     # noqa: BLE001
        ops_metrics.bump("adaptive_decision", "write_failed")
        print(f"[adaptive] could not record the decision for {session_id}: {type(e).__name__}")


@app.get("/api/generate-question")
def generate_question(
    request:    Request,
    background_tasks: BackgroundTasks,
    grade:      str | None = Query(None),
    class_id:   str | None = Query(None),
    bias:       int        = Query(0),
    session_id: str | None = Query(None),
):
    # The student is the caller, never a query parameter: the decider reads
    # consent by this id and signals by the session. A sent `user_id` is ignored.
    user = get_user(request)
    user_id = user["id"]
    if session_id:
        _verify_session_owner(session_id, user_id)

    # A query param no model checked; an unreadable grade is a 422, not a silent default.
    try:
        grade = grade_levels.validated_grade(grade)
    except ValueError as e:
        raise HTTPException(422, str(e))

    class_grade = None
    if class_id:
        # Not `_row_or_404`: an unknown class falls back to the student's own grade.
        try:
            cls = supabase.table("classes").select("grade_level") \
                .eq("id", class_id).single().execute()
        except Exception as e:                                 # noqa: BLE001
            print(f"[question] could not read class {class_id}: {e}")
            cls = None
        if cls and cls.data:
            class_grade = cls.data.get("grade_level")
    effective_grade = class_grade or _served_grade(user_id, grade)

    manual_bias = max(-1, min(1, int(bias or 0)))

    # Serve from the prefetch queue if available, else generate now.
    # Only a question made for this grade, bias and session: another's (a failed read at session
    # start, a changed pick, "Easier") waits in its own queue; an old session's ages out of the cap.
    key = _prefetch_key(effective_grade, manual_bias, session_id)
    with _prefetch_lock:
        queues   = _prefetch_cache.setdefault(user_id, {})
        queue    = queues.pop(key, [])
        question = queue.pop(0) if queue else None
        queues[key] = queue
        # Only an idle queue goes: a busy one's work still counts against the session's cap,
        # so dropping it would free nothing, and its results would be thrown away on arrival.
        counts = _prefetch_active.get(user_id, {})
        idle = [k for k in queues if k != key and not counts.get(k)]
        while len(queues) > _PREFETCH_KEPT_QUEUES and idle:
            queues.pop(idle.pop(0))

    inline = not question
    if inline:
        print(f"[generate] generating inline for {user_id[:8]}")
        refund = _admit_generation(user_id)
        try:
            with _generation_waiter() as admitted:
                if not admitted:
                    raise HTTPException(
                        503, "Too many questions are being generated right now. Try again shortly.",
                        headers={"Retry-After": "5"},
                    )
                try:
                    question = LLM_topic_decider.LLM_single_prompt_topic_and_difficulty_decider(
                        user_id, effective_grade, session_id, manual_bias
                    )
                except llm_client.GenerationUnavailable as e:
                    # 503: a configured ceiling was reached; never silently serve another source.
                    print(f"[generate] refused for {user_id[:8]}: {e}")
                    raise HTTPException(503, "Question generation is temporarily unavailable.")
                except Exception as e:
                    # Re-raised as the 500 it always was; logged so the failure has a cause on record.
                    print(f"[generate] failed for {user_id[:8]} at {effective_grade!r}, bias {manual_bias}: "
                          f"{type(e).__name__}: {e}")
                    raise
            if not question:
                print(f"[generate] the decider returned no question for {user_id[:8]} at {effective_grade!r}")
                raise HTTPException(500, "Failed to generate question")
        except Exception:
            # No question reached the student, so it does not count against their day.
            refund()
            raise
    else:
        try:
            # A queued question costs the student's day when served, not when it was made.
            _admit_generation(user_id, generating=False)
        except HTTPException:
            with _prefetch_lock:
                _prefetch_cache.setdefault(user_id, {}).setdefault(key, []).insert(0, question)
            raise
        print(f"[generate] cache hit for {user_id[:8]} -- instant serve")

    question["effective_grade"] = effective_grade
    question["bias"]            = manual_bias
    # A queued question's `eeg_label` was read when it was made (`signal_read_at`), not now.
    question["served_from"]     = "inline" if inline else "queue"
    ops_metrics.bump("question", f"served:{question['served_from']}")
    # Stored, not sent: the page has no use for it. Written only for a question actually served,
    # after the response, so the student never waits on the insert.
    decision = question.pop("adaptive_decision", None)
    if session_id and decision:
        background_tasks.add_task(_record_adaptive_decision, user_id, session_id, dict(question), decision)

    _ensure_queue(user_id, effective_grade, manual_bias, session_id)

    return question


# ─── sessions ────────────────────────────────────────────────────────────

@app.post("/api/sessions/start")
def start_session(payload: StartSessionRequest, request: Request):
    user = get_user(request)

    # Close any session the student left open. Columns named: a missing one reads
    # as None, which the close would credit as zero.
    stale_open = supabase.table("sessions") \
        .select("id, started_at, questions_answered, correct_answers") \
        .eq("user_id", user["id"]).is_("ended_at", "null").execute().data or []
    last_seen = _last_seen_many(stale_open)
    quiet_before = _utc_now() - timedelta(seconds=_STALE_AFTER_SEC)
    for s in stale_open:
        if not _quiet_since(s, last_seen, quiet_before):
            # Possibly a lesson live in another tab: closed, its next answer would split it.
            continue
        # Also releases any pre-claim EEG reservation from a scan that never reached /start.
        eeg_poller.stop(s["id"], user["id"])
        _close_session(user["id"], s, _ended_when_last_seen(s, last_seen), closed_by=CLOSED_BY_SWEEP,
                       reason=CLOSE_REASON_SUPERSEDED)


    obj  = {
        "user_id":            user["id"],
        "title":              payload.title or "Practice Session",
        "started_at":         _utc_now().isoformat(),
        "questions_answered": 0,
        "correct_answers":    0,
    }
    res = supabase.table("sessions").insert(obj, returning=ReturnMethod.representation).execute()

    # Pre-warm the queue at the student's own difficulty bias.
    profile = _profile(user["id"])
    grade   = _served_grade(user["id"], profile=profile)
    bias    = max(-1, min(1, int(profile.get("difficulty_bias") or 0)))
    _ensure_queue(user["id"], grade, bias, res.data[0]["id"])

    return res.data[0]


# The answer's foreign key to its question (init migration): one expired or deleted since it was served.
_ANSWER_QUESTION_FK = "session_answers_question_id_fkey"

# Steps record_answer survives failing: (result field, function, its migration, what stops).
_ANSWER_SIDE_STEPS = (
    ("topic_error",    "record_topic_attempt",  "20260825000000", "no topic attribution until then"),
    ("counters_error", "bump_session_counters", "20260826000000", "live counters will not move"),
)


def _names_deleted_question(exc: Exception) -> bool:
    """Whether a failed answer write means its question no longer exists."""
    return (getattr(exc, "code", None) == "23503"
            and _ANSWER_QUESTION_FK in (getattr(exc, "message", None) or ""))


def _log_answer_side_errors(out: dict, session_id: str) -> None:
    """Log each step record_answer reports failing; the answer itself is saved."""
    for field, fn, migration, effect in _ANSWER_SIDE_STEPS:
        err = str(out.get(field) or "")
        if not err:
            continue
        # 'SQLSTATE: message'; 42883 is undefined_function, and must name this step's own function.
        if err.startswith("42883") and fn in err:
            print(f"[answer] {fn} is missing from the database -- apply {migration}; "
                  f"{effect}: {err}")
        else:
            print(f"[answer] saved, but {field} for {session_id}: {err}")


@app.post("/api/sessions/{session_id}/answer")
def record_answer(session_id: str = Path(...), payload: AnswerPayload = Body(...), request: Request = None):
    user = get_user(request)
    # One call: ownership and an open session checked under a row lock, then the answer,
    # the counters and the topic attempt (20261003000000). A refused session gets no write.
    try:
        out = supabase.rpc("record_answer", {
            "p_session_id":     session_id,
            "p_user_id":        user["id"],
            "p_question_id":    str(payload.question_id),
            "p_selected_index": payload.selected_index,
            "p_correct":        bool(payload.correct),
            "p_answered_at":    _utc_now().isoformat(),
        }).execute().data or {}
    except Exception as e:                                     # noqa: BLE001
        if _names_no_row(e):
            raise HTTPException(404, "Session not found")
        if _names_deleted_question(e):
            # 410, not 409: the page reads a 409 here as its session ended and starts another.
            raise HTTPException(410, "This question is no longer available")
        if not _missing_rpc(e, "record_answer", "20261003000000",
                            "every answer is a 503 and none is saved until then"):
            print(f"[answer] could not record an answer for {session_id}: {e}")
        raise _read_failed("This answer could not be saved; try again")
    status = out.get("status") if isinstance(out, dict) else None
    if status == "not_found":
        raise HTTPException(404, "Session not found")
    if status == "forbidden":
        # The shared refusal, so this 403 records its denial like every other.
        _session_or_403(session_id, user["id"], row={"user_id": out.get("owner")})
    if status == "ended":
        # Closed by the sweep, the live monitor or another tab: its totals are already
        # credited, so an answer here would count nowhere. The page starts a new session.
        raise HTTPException(409, "This session has ended")
    if status != "ok":
        print(f"[answer] record_answer answered {out!r} for {session_id}")
        raise _read_failed("This answer could not be saved; try again")
    _log_answer_side_errors(out, session_id)
    # Best effort, after the writes: the simulator reacts to answers; hardware ignores it.
    try:
        eeg_poller.notify_answer(session_id, bool(payload.correct))
    except Exception as e:                                     # noqa: BLE001
        print(f"[answer] could not notify the sidecar for {session_id}: {e}")
    # Returned so the page can update one topic figure; None = nothing attributed.
    topic = out.get("topic")
    if not isinstance(topic, str) and not out.get("topic_error"):
        # Ran cleanly and matched no topic: the question's subject has no math_topics row.
        print(f"[answer] saved, but no math_topics row for question {payload.question_id}'s "
              f"subject; its topic attempt counts nowhere")
    return {"ok": True, "topic": topic if isinstance(topic, str) else None}

class EndSessionRequest(StrictModel):
    # What the page ended it for; absent (an older client, a bare POST) is stored as "student".
    reason: Literal["finish", "sign_out", "page_closed"] | None = None


@app.post("/api/sessions/{session_id}/end")
def end_session(session_id: str = Path(...), request: Request = None,
                payload: EndSessionRequest | None = None):
    user = get_user(request)
    reason = (payload.reason if payload else None) or CLOSE_REASON_STUDENT
    # Ownership before stopping the poller.
    data = _session_or_403(session_id, user["id"], "*")
    # Also releases user["id"]'s pre-claim reservation, if any.
    eeg_poller.stop(session_id, user["id"])  # auto-stop EEG poller
    # Cheap early-out only; the conditional stamp in the close is the real guard.
    if data.get("ended_at"):
        return {"ok": True, "already_closed": True}
    # Timezone-aware, and read once: the rollup converts it to a school day.
    ended = _utc_now().isoformat()
    result = _close_session(user["id"], data, ended, reason=reason)
    if result.get("failed"):
        # 503, not "already closed": the session is still open, and the page should say the end did not land.
        raise _read_failed("Could not end the session just now")
    if result.get("already_closed"):
        return {"ok": True, "already_closed": True}
    return {"ok": True, **({"discarded": True} if result["discarded"] else {})}

# Session columns a browser may receive: named, and without `chart_paths`, which nothing renders.
_SESSION_CLIENT_COLUMNS = ("id, user_id, class_id, title, started_at, ended_at, "
                           "questions_answered, correct_answers")


# Rows per read (a page of history), not sessions per student; counts come from `total`.
_SESSION_LIST_MAX = 200


@app.get("/api/sessions")
def list_sessions(request: Request, limit: int = _SESSION_LIST_MAX):
    """A student's own sessions, newest first, capped, beside the exact `total`.

    Never derive a count from the rows (PostgREST's db-max-rows cuts silently).
    `truncated` comes from the count; None when the count did not come back.
    """
    user = get_user(request)
    limit = max(1, min(limit, _SESSION_LIST_MAX))
    res  = supabase.table("sessions").select(_SESSION_CLIENT_COLUMNS, count="exact") \
        .eq("user_id", user["id"]).order("started_at", desc=True) \
        .limit(limit).execute()
    rows = res.data or []
    total = res.count
    return {
        "sessions":  rows,
        "total":     total,
        "truncated": None if total is None else total > len(rows),
    }


# ─── practice sessions ──────────────────────────────────────────────────────
# Self-study with student-picked topics and no sensors, on the same generator
# and bounds. Never writes `user_math_performance`, which drives the live engine.

@app.get("/api/topics")
def list_topics(grade: str | None = Query(None)):
    """The topic catalog, and which of them a grade may see (from `LLM_topic_decider`)."""
    allowed = set(LLM_topic_decider._allowed_topics(grade))
    return [{"name": t, "allowed": t in allowed} for t in LLM_topic_decider.ALL_TOPICS]


def _practice_session_or_403(practice_session_id: str, user_id: str, columns: str = "user_id") -> dict:
    """`_session_or_403` for `practice_sessions`: ownership only."""
    row = _row_or_404(
        supabase.table("practice_sessions").select(columns).eq("id", practice_session_id),
        "Practice session")
    if row.get("user_id") != user_id:
        _record_security_event("authz_denied", user_id, row.get("user_id"),
                               check="practice_session_owner")
        raise HTTPException(403, "Not your practice session")
    return row


class StartPracticeSessionRequest(StrictModel):
    mode:       str
    # The handler checks each entry; the cap refuses a huge list before that loop.
    topics:     list[str] = Field(max_length=64)
    difficulty: str
    grade:      str | None = None

    _check_grade = field_validator("grade")(classmethod(_grade_level_field))

    @field_validator("mode")
    @classmethod
    def _mode_valid(cls, v):
        if v not in ("flashcard", "test"):
            raise ValueError("mode must be 'flashcard' or 'test'")
        return v

    @field_validator("difficulty")
    @classmethod
    def _difficulty_valid(cls, v):
        if v not in ("easy", "medium", "hard"):
            raise ValueError("difficulty must be 'easy', 'medium' or 'hard'")
        return v


@app.post("/api/practice-sessions/start")
def start_practice_session(payload: StartPracticeSessionRequest, request: Request):
    user = get_user(request)
    if not payload.topics:
        raise HTTPException(400, "Pick at least one topic")

    grade = _served_grade(user["id"], payload.grade)
    # Server-side grade gate, the same one auto-selection uses.
    allowed = set(LLM_topic_decider._allowed_topics(grade))
    bad = [t for t in payload.topics if t not in allowed]
    if bad:
        raise HTTPException(400, f"Not available at this grade: {', '.join(bad)}")

    res = supabase.table("practice_sessions").insert({
        "user_id":    user["id"],
        "mode":       payload.mode,
        "topics":     payload.topics,
        "difficulty": payload.difficulty,
        "grade_level": grade,
    }, returning=ReturnMethod.representation).execute()
    return res.data[0]


# Last topic served per practice session, to avoid repeats. In-process only;
# LRU-capped because abandoned sessions never reach /end to evict themselves.
_PRACTICE_TOPIC_CAP = 4096
_practice_last_topic: "collections.OrderedDict[str, str]" = collections.OrderedDict()
_practice_last_topic_lock = threading.Lock()


def _pick_practice_topic(practice_session_id: str, topics: list[str]) -> str:
    if len(topics) == 1:
        return topics[0]
    with _practice_last_topic_lock:
        last = _practice_last_topic.get(practice_session_id)
        choices = [t for t in topics if t != last] or topics
        topic = random.choice(choices)
        _practice_last_topic[practice_session_id] = topic
        _practice_last_topic.move_to_end(practice_session_id)
        if len(_practice_last_topic) > _PRACTICE_TOPIC_CAP:
            _practice_last_topic.popitem(last=False)
        return topic


@app.get("/api/practice-sessions/{practice_session_id}/question")
def practice_question(practice_session_id: str = Path(...), request: Request = None):
    user = get_user(request)
    session = _practice_session_or_403(
        practice_session_id, user["id"], "user_id, topics, difficulty, grade_level, ended_at")
    if session.get("ended_at"):
        raise HTTPException(409, "This practice session has already ended")

    topic = _pick_practice_topic(practice_session_id, session["topics"])

    # Same rate limit, daily budget, waiter cap and refusals as /api/generate-question.
    refund = _admit_generation(user["id"])
    try:
        with _generation_waiter() as admitted:
            if not admitted:
                raise HTTPException(
                    503, "Too many questions are being generated right now. Try again shortly.",
                    headers={"Retry-After": "5"},
                )
            try:
                question = LLM_topic_decider.question_generation(
                    topic, session["difficulty"], user["id"], session.get("grade_level"))
            except llm_client.GenerationUnavailable as e:
                print(f"[practice] generation refused for {user['id'][:8]}: {e}")
                raise HTTPException(503, "Question generation is temporarily unavailable.")
        if not question:
            raise HTTPException(500, "Failed to generate question")
    except Exception:
        # As /api/generate-question: a question that never arrived is not charged.
        refund()
        raise

    # Same deduplicating storage as the live path, so identical questions share one row.
    LLM_topic_decider._attach_stored_id(question, session["difficulty"])
    question["difficulty"] = session["difficulty"]
    return question


def _practice_topic_for_question(question_id: str) -> str | None:
    """The topic of a practice answer/view, from the question row, never the client. None if unknown."""
    try:
        res = supabase.table("questions").select("subject") \
            .eq("id", question_id).single().execute()
        return (res.data or {}).get("subject")
    except Exception as e:                                     # noqa: BLE001
        print(f"[practice] could not resolve topic for question {question_id}: {e}")
        return None


class PracticeAnswerPayload(StrictModel):
    question_id:    str = Field(max_length=_ID_MAX)
    selected_index: int
    correct:        bool


@app.post("/api/practice-sessions/{practice_session_id}/answer")
def record_practice_answer(practice_session_id: str = Path(...),
                            payload: PracticeAnswerPayload = Body(...),
                            request: Request = None):
    """Test-mode answer: graded, bumps the session's live counters.

    409 once ended: a late unawaited answer would leave `topic_summary` stale.
    """
    user = get_user(request)
    session = _practice_session_or_403(practice_session_id, user["id"], "user_id, ended_at")
    if session.get("ended_at"):
        raise HTTPException(409, "This practice session has already ended")
    topic = _practice_topic_for_question(payload.question_id)
    supabase.table("practice_session_answers").insert({
        "practice_session_id": practice_session_id,
        "user_id":             user["id"],
        "question_id":         payload.question_id,
        "topic":                topic,
        "selected_index":      payload.selected_index,
        "correct":             payload.correct,
    }).execute()
    try:
        supabase.rpc("bump_practice_session_counters", {
            "p_session_id": practice_session_id,
            "p_graded":     True,
            "p_correct":    bool(payload.correct),
        }).execute()
    except Exception as e:                                     # noqa: BLE001
        if not _missing_rpc(e, "bump_practice_session_counters", "20260904000000",
                            "live practice counters will not move until then"):
            print(f"[practice] could not bump counters for {practice_session_id}: {e}")
    return {"ok": True, "topic": topic}


class PracticeViewPayload(StrictModel):
    question_id: str = Field(max_length=_ID_MAX)


@app.post("/api/practice-sessions/{practice_session_id}/view")
def record_practice_view(practice_session_id: str = Path(...),
                          payload: PracticeViewPayload = Body(...),
                          request: Request = None):
    """Flashcard flip-to-reveal: ungraded, but counted as attempted. 409 once ended."""
    user = get_user(request)
    session = _practice_session_or_403(practice_session_id, user["id"], "user_id, ended_at")
    if session.get("ended_at"):
        raise HTTPException(409, "This practice session has already ended")
    topic = _practice_topic_for_question(payload.question_id)
    supabase.table("practice_session_answers").insert({
        "practice_session_id": practice_session_id,
        "user_id":             user["id"],
        "question_id":         payload.question_id,
        "topic":                topic,
        "selected_index":      None,
        "correct":             None,
    }).execute()
    try:
        supabase.rpc("bump_practice_session_counters", {
            "p_session_id": practice_session_id,
            "p_graded":     False,
            "p_correct":    False,
        }).execute()
    except Exception as e:                                     # noqa: BLE001
        if not _missing_rpc(e, "bump_practice_session_counters", "20260904000000",
                            "live practice counters will not move until then"):
            print(f"[practice] could not bump counters for {practice_session_id}: {e}")
    return {"ok": True, "topic": topic}


@app.post("/api/practice-sessions/{practice_session_id}/end")
def end_practice_session(practice_session_id: str = Path(...), request: Request = None):
    """Stamps a close and summarizes topic_summary. Nothing else.

    Not a `sessions` close site: `_close_practice_session` writes the stamp, so
    `conftest.close_sites()` does not match it. `test_end_practice_session_is_not_a_close_site` pins this.
    """
    user = get_user(request)
    session = _practice_session_or_403(practice_session_id, user["id"], "*")
    if session.get("ended_at"):
        return _already_closed_reply(session)
    summary = _close_practice_session(practice_session_id, _utc_now().isoformat())
    if summary is None:                                         # the sweep closed it first
        return _already_closed_reply(_practice_session_or_403(practice_session_id, user["id"], "*"))
    return {"ok": True, "topic_summary": summary}


def _already_closed_reply(row: dict) -> dict:
    """The stored close: its breakdown and the counts the answers that landed made, not the page's tally."""
    return {"ok": True, "already_closed": True, "topic_summary": row.get("topic_summary") or {},
            "questions_answered": row.get("questions_answered"), "correct_answers": row.get("correct_answers")}


def _close_practice_session(practice_session_id: str, ended_at: str) -> dict | None:
    """Stamp a practice session closed and summarize its topics; the end route's and the sweep's one copy.

    None if it was already closed. The stamp key is a local variable so `conftest.close_sites()` skips it.
    """
    answers = supabase.table("practice_session_answers").select("topic, correct") \
        .eq("practice_session_id", practice_session_id).execute().data or []
    buckets: dict[str, dict] = {}
    for a in answers:
        topic = a.get("topic")
        if not topic:
            continue
        bucket = buckets.setdefault(topic, {"attempted": 0, "correct": 0, "graded": 0})
        bucket["attempted"] += 1
        if a.get("correct") is not None:
            bucket["graded"] += 1
            if a["correct"]:
                bucket["correct"] += 1
    # `correct` is null, not 0%, for a topic only ever viewed.
    topic_summary = {
        topic: {
            "attempted": b["attempted"],
            "correct": round(b["correct"] / b["graded"] * 100) if b["graded"] else None,
        }
        for topic, b in buckets.items()
    }

    close_stamp_field = "ended_at"
    update = {close_stamp_field: ended_at, "topic_summary": topic_summary}
    res = supabase.table("practice_sessions").update(update, returning=ReturnMethod.representation) \
        .eq("id", practice_session_id).is_(close_stamp_field, "null").execute()
    with _practice_last_topic_lock:
        _practice_last_topic.pop(practice_session_id, None)
    return topic_summary if res.data else None


def _practice_last_activity(row: dict) -> str:
    """A practice session's last answer or view, else its start. Raises on a failed read."""
    last = (supabase.table("practice_session_answers").select("answered_at")
            .eq("practice_session_id", row["id"]).order("answered_at", desc=True)
            .limit(1).execute().data or [])
    return (last[0].get("answered_at") if last else None) or row["started_at"]


def _practice_idle_since(row: dict) -> str | None:
    """Its last activity if it is past the age cut (`_is_abandoned`) and quiet as long, else None.

    The sweep's and the list's one idle rule: a long session still in use is not idle. Raises on a failed read.
    """
    if not _is_abandoned(row):
        return None
    last = _practice_last_activity(row)
    t = _parse_ts(last)                                         # an unparseable stamp is not evidence
    return last if t is not None and t < _utc_now() - timedelta(seconds=_SESSION_ABANDONED_AFTER_SEC) else None


# The sweep's id cursor, so sessions it keeps skipping or failing cannot hold every batch.
_practice_sweep_after: str | None = None


def _sweep_abandoned_practice(limit: int = _STALE_SWEEP_BATCH) -> dict:
    """Close practice sessions idle past `_SESSION_ABANDONED_AFTER_SEC`, at their last activity; returns counts.

    Nothing else closes one left by closing the tab, and open it reads "in progress" for ever.
    """
    global _practice_sweep_after
    cutoff = _utc_now() - timedelta(seconds=_SESSION_ABANDONED_AFTER_SEC)
    try:
        query = (supabase.table("practice_sessions").select("id, started_at")
                 .is_("ended_at", "null").lt("started_at", cutoff.isoformat()))
        if _practice_sweep_after:
            query = query.gt("id", _practice_sweep_after)
        rows = query.order("id").limit(limit).execute().data or []
    except Exception as e:                                     # noqa: BLE001
        print(f"[practice_sweep] could not list abandoned practice sessions: {e}")
        return {"found": 0, "closed": 0, "active": 0, "failed": 0, "retrieved": False}
    # A short page is the end of the list: the next pass starts over.
    _practice_sweep_after = rows[-1]["id"] if len(rows) >= limit else None
    closed = active = failed = 0
    for s in rows:
        try:
            last = _practice_idle_since(s)
            if last is None:
                active += 1
                continue
            if _close_practice_session(s["id"], last) is not None:
                closed += 1
        except Exception as e:                                 # noqa: BLE001
            failed += 1
            print(f"[practice_sweep] could not close {s.get('id')}: {e}")
    if rows:
        print(f"[practice_sweep] {len(rows)} open past the cut: {closed} closed, {active} still active, "
              f"{failed} failed")
    return {"found": len(rows), "closed": closed, "active": active, "failed": failed, "retrieved": True}


@app.get("/api/practice-sessions")
def list_practice_sessions(request: Request):
    """The caller's own past practice sessions, most recent first, capped at 20.

    `abandoned` (`_practice_idle_since`) covers the gap until `_sweep_abandoned_practice` closes it;
    null when its activity read failed.
    """
    user = get_user(request)
    res = supabase.table("practice_sessions").select(_PRACTICE_CLIENT_COLUMNS) \
        .eq("user_id", user["id"]).order("started_at", desc=True).limit(20).execute()
    rows = res.data or []
    for r in rows:
        try:
            # Reads only for the caller's own sessions open past the age cut, which the sweep soon closes.
            r["abandoned"] = _practice_idle_since(r) is not None
        except Exception as e:                                 # noqa: BLE001
            print(f"[practice_sessions] last-activity read failed: {e}")
            r["abandoned"] = None
    return rows


# Practice-session columns a browser may receive (History, PracticeHistory).
_PRACTICE_CLIENT_COLUMNS = ("id, mode, topics, difficulty, started_at, ended_at, "
                            "questions_answered, correct_answers")


# ─── stats ───────────────────────────────────────────────────────────────

def _topic_performance_rows(student_ids) -> tuple[list, bool]:
    """The raw per-topic rows for several students, and whether the read worked.

    A heatmap needs the flag; an all-blank grid and an unfetched one look identical.
    """
    ids = _unique_ids(student_ids)
    if not ids:
        return [], True
    try:
        rows = supabase.table("user_math_performance") \
            .select("*, math_topics(topic_name)").in_("user_id", ids).execute().data or []
    except Exception as e:                                     # noqa: BLE001
        print(f"[perf] could not batch-read topic performance for {len(ids)}: {e}")
        return [], False
    return rows, True


def _topic_performance_many(student_ids) -> dict[str, list]:
    """Per-topic performance for several students in one query; {} on failure."""
    rows, _ = _topic_performance_rows(student_ids)
    return _group_by_user(rows)


def _open_sessions_many(student_ids) -> dict[str, list]:
    """Every open session per student, newest first, in one query.

    Raises on failure: an empty map would make every student look idle.
    """
    ids = _unique_ids(student_ids)
    if not ids:
        return {}
    # Named columns: these rows reach a teacher's browser via `class_live`.
    rows = supabase.table("sessions").select(_SESSION_CLIENT_COLUMNS) \
        .in_("user_id", ids).is_("ended_at", "null") \
        .order("started_at", desc=True).execute().data or []
    return _group_by_user(rows)


def _stats_including_open_session(student_id: str) -> dict:
    """Lifetime totals plus the open session's live counts.

    `retrieved` is False only when the lifetime read fails; a failed
    open-session read just omits the live delta.
    """
    return _stats_including_open_session_many([student_id])[student_id]


def _stats_including_open_session_many(student_ids: list[str]) -> dict[str, dict]:
    """`_stats_including_open_session` for a roster, in two queries.

    A failed lifetime read marks every student unretrieved. Always returns one
    entry per id passed in (the single form indexes it directly).
    """
    if not student_ids:
        return {}
    base: dict[str, dict] = {
        sid: {"total_questions": 0, "total_correct": 0, "current_streak": 0,
              "best_streak": 0, "retrieved": True}
        for sid in student_ids
    }
    lookup = _unique_ids(student_ids)
    if not lookup:
        return base
    try:
        rows = supabase.table("user_stats").select("*") \
            .in_("user_id", lookup).execute().data or []
        tz = _school_timezone() if rows else None
        for r in rows:
            if r.get("user_id") in base:
                base[r["user_id"]] = {**_live_streak(r, tz), "retrieved": True}
    except Exception as e:                                     # noqa: BLE001
        print(f"[stats] could not batch-read user_stats for {len(student_ids)}: {e}")
        return {sid: {**v, "retrieved": False} for sid, v in base.items()}
    try:
        open_rows = supabase.table("sessions") \
            .select("user_id, questions_answered, correct_answers") \
            .in_("user_id", lookup).is_("ended_at", "null").execute().data or []
    except Exception as e:                                     # noqa: BLE001
        # Stored totals stand on their own.
        print(f"[stats] could not batch-read open sessions: {e}")
        return base
    for r in open_rows:
        sid = r.get("user_id")
        if sid not in base:
            continue
        live_q = r.get("questions_answered") or 0
        if not live_q:
            continue
        base[sid] = {**base[sid],
                     "total_questions": (base[sid].get("total_questions") or 0) + live_q,
                     "total_correct": (base[sid].get("total_correct") or 0)
                     + (r.get("correct_answers") or 0)}
    return base


@app.get("/api/stats/me")
def my_stats(request: Request):
    user = get_user(request)
    return _stats_including_open_session(user["id"])

@app.get("/api/stats/student/{student_id}")
def student_stats(student_id: str, request: Request):
    _verify_can_view_student(get_user(request), student_id)
    return _stats_including_open_session(student_id)

# (table, ts column, measurement columns): a row counts only if a measurement is non-null
# (`contact_poor` rows are all null). The rule `last_activity_for_sessions` and
# `last_active_for_users` apply in SQL; tests hold both to it.
_ACTIVITY_SOURCES = (
    ("session_answers",   "answered_at", ()),
    ("cognitive_signals", "ts", ("focus",)),
    ("face_signals",      "ts", ("emotion", "gaze_x", "head_yaw")),
    ("heart_signals",     "ts", ("heart_rate_bpm",)),
)

# Sessions per student a history view shows.
_RECENT_SESSIONS = 20

_SESSION_CLIENT_KEYS = tuple(c.strip() for c in _SESSION_CLIENT_COLUMNS.split(","))


def _recent_sessions_many(user_ids, limit: int) -> dict[str, list[dict]]:
    """Each user's newest `limit` sessions, newest first, in one call. Raises on a failed read.

    The function returns `_SESSION_CLIENT_COLUMNS` alone; narrowing again here is a second
    guard, so a widened function still cannot reach a browser.
    """
    ids = _unique_ids(user_ids)
    if not ids:
        return {}
    rows = supabase.rpc("recent_sessions_for_users",
                        {"p_user_ids": ids, "p_limit": limit}).execute().data or []
    out: dict[str, list[dict]] = {uid: [] for uid in ids}
    # Parsed: offset spellings of one instant sort differently as text. Unreadable sorts last.
    for r in sorted(rows, reverse=True,
                    key=lambda r: _parse_ts(r.get("started_at"))
                    or datetime.min.replace(tzinfo=timezone.utc)):
        out.setdefault(r.get("user_id"), []).append({k: r.get(k) for k in _SESSION_CLIENT_KEYS})
    return out


def _is_abandoned(row: dict) -> bool:
    """Open past `_SESSION_ABANDONED_AFTER_SEC` (an age, not idleness); adaptive and practice rows alike.

    An unparseable start is not evidence.
    """
    started = _parse_ts(row.get("started_at"))
    cutoff = _utc_now() - timedelta(seconds=_SESSION_ABANDONED_AFTER_SEC)
    return bool(not row.get("ended_at") and started is not None and started < cutoff)


def _flag_sessions(rows: list[dict]) -> list[dict]:
    """Mark sessions `abandoned` (an age) and `idle` (real last activity), in place, over one read.

    Derived here so the thresholds have one definition. `activity_known` is False only
    when the activity read failed, so the client never calls an unread session quiet.
    """
    for r in rows:
        r["abandoned"] = _is_abandoned(r)

    ids = [r["id"] for r in rows if not r.get("ended_at")]
    last_seen: dict[str, str] = {}
    # False only when the read failed; nothing to look up is not "unknown".
    activity_known = True
    if ids:
        try:
            got = supabase.rpc("last_activity_for_sessions",
                               {"p_session_ids": ids}).execute().data or []
            last_seen = {g["session_id"]: g["last_activity_at"]
                         for g in got if g.get("last_activity_at")}
        except Exception as e:                                  # noqa: BLE001
            print(f"[sessions] could not read last activity: {e}")
            activity_known = False

    quiet_before = _utc_now() - timedelta(seconds=_STALE_AFTER_SEC)
    for r in rows:
        r["activity_known"] = activity_known
        r["last_activity_at"] = last_seen.get(r["id"])
        if r.get("ended_at") or not activity_known:
            r["idle"] = False
            continue
        # No activity yet falls back to the start time.
        seen = _parse_ts(r["last_activity_at"]) or _parse_ts(r.get("started_at"))
        r["idle"] = bool(seen is not None and seen < quiet_before
                         and not r["abandoned"])
    return rows


@app.get("/api/sessions/student/{student_id}")
def student_sessions(student_id: str, request: Request):
    """A student's recent sessions, flagged by `_flag_sessions`."""
    _verify_can_view_student(get_user(request), student_id)
    res = supabase.table("sessions").select(_SESSION_CLIENT_COLUMNS) \
        .eq("user_id", student_id).order("started_at", desc=True) \
        .limit(_RECENT_SESSIONS).execute()
    return _flag_sessions(res.data or [])


@app.get("/api/classes/{class_id}/sessions")
def class_sessions(class_id: str, request: Request):
    """Every student's recent sessions in a class the caller owns, flagged as above.

    Two reads for the class, where the Sessions page made one request per student.
    A failed roster read is a 503; a failed sessions read is every student's `None`.
    """
    user = get_user(request)
    _verify_class_owner(class_id, user["id"])
    try:
        # Names ride on the roster read, so a failed one fails the request rather than becoming "Student".
        members = supabase.table("class_memberships") \
            .select("student_id, profiles(display_name, email)") \
            .eq("class_id", class_id).execute().data or []
    except Exception as e:                                      # noqa: BLE001
        print(f"[sessions:class] {class_id}: {e}")
        raise _read_failed("Could not load this class's students; try again")
    names = {m["student_id"]: m.get("profiles") or {} for m in members}
    roster = _unique_ids(m["student_id"] for m in members)
    # None when unset: the page chooses what to draw.
    students = [{"user_id": sid,
                 "name": names[sid].get("display_name") or None,
                 "email": names[sid].get("email") or None}
                for sid in roster]
    try:
        by_student = _recent_sessions_many(roster, _RECENT_SESSIONS)
    except Exception as e:                                      # noqa: BLE001
        print(f"[sessions:class] {class_id}: {e}")
        return {"students": students, "sessions": {sid: None for sid in roster}}
    _flag_sessions([r for rows in by_student.values() for r in rows])
    return {"students": students,
            "sessions": {sid: by_student.get(sid, []) for sid in roster}}

@app.get("/api/performance/student/{student_id}")
def student_performance(student_id: str, request: Request):
    _verify_can_view_student(get_user(request), student_id)
    # What the three topic panels read (Students, StudentProgressReport, Adaptive), and no more.
    res = supabase.table("user_math_performance") \
        .select("topic_id, attempted_questions, correct_questions, math_topics(topic_name)") \
        .eq("user_id", student_id).execute()
    return res.data or []


@app.get("/api/students/{student_id}/signal-trend")
def student_signal_trend(student_id: str, request: Request, weeks: int = 8,
                         include_face: bool = True):
    """Week-over-week signal averages; same access and consent gating as the weekly report."""
    _verify_can_view_student(get_user(request), student_id)
    channels = _reportable_channels(student_id, include_face)
    return _signal_trend(student_id, max(2, min(weeks, _TREND_MAX_WEEKS)),
                         include_heart=channels.heart,
                         include_emotion=channels.emotion,
                         consent_retrieved=channels.consent_retrieved,
                         emotion_revoked_at=channels.emotion_revoked_at,
                         heart_revoked_at=channels.heart_revoked_at,
                         open_session=_any_open_session(student_id, channels.heart))


@app.get("/api/students/{student_id}/weekly-report")
def student_weekly_report(student_id: str, request: Request, days: int = 7, include_face: bool = True):
    """Aggregated signals for a student over the last `days`; access by relationship, not role.

    include_face=false can only narrow past stored consent, never widen it.
    """
    _verify_can_view_student(get_user(request), student_id)
    p = _profile(student_id)
    channels = _reportable_channels(student_id, include_face)
    days = max(1, min(days, 30))
    report = _weekly_signal_report(student_id, days,
                                   include_heart=channels.heart,
                                   include_emotion=channels.emotion,
                                   consent_retrieved=channels.consent_retrieved,
                                   emotion_revoked_at=channels.emotion_revoked_at,
                                   heart_revoked_at=channels.heart_revoked_at,
                                   eeg_enabled=channels.eeg,
                                   eeg_revoked_at=channels.eeg_revoked_at)
    avg, hl, n = report["averages"], report["highlights"], report["average_samples"]
    read = report["retrieved"]
    start, end = _report_period(days)
    usual = _personal_baseline(student_id, start, end, {
        # Each figure with the usable readings behind it, as `/signal-summary` counts them.
        "focus": (avg["focus"], n["focus"]), "calm": (_calm_of(avg["stress"]), n["stress"]),
        "heart_rate_bpm": (hl["heart_rate_bpm"], n["heart_rate_bpm"]),
        "rmssd_ms": (hl["rmssd_ms"], n["rmssd_ms"]),
        "heart_sources": report["heart_sources"],
        "unread": [c for c in ("cognitive", "heart") if read.get(c) is False],
        "arousal_pending": report["body_arousal"]["pending"]},
        channels.heart, channels.consent_retrieved)
    return {
        "student_name": p.get("display_name") or p.get("email") or "Student",
        **report,
        "usual": usual,
        **_erased_fields(channels, _window_start(days)),
    }


@app.get("/api/students/{student_id}/signal-summary")
def student_signal_summary(student_id: str, request: Request, days: int = 7, include_face: bool = True):
    """Headline signal averages for a student, aggregated in Postgres; gated on relationship.

    In SQL because a row cap would average only the newest minutes.
    """
    _verify_can_view_student(get_user(request), student_id)
    channels = _reportable_channels(student_id, include_face)
    days = max(1, min(days, 30))
    summary = _signal_summary(student_id, days,
                              include_heart=channels.heart,
                              include_emotion=channels.emotion,
                              consent_retrieved=channels.consent_retrieved,
                              emotion_revoked_at=channels.emotion_revoked_at,
                              heart_revoked_at=channels.heart_revoked_at,
                              eeg_enabled=channels.eeg,
                              eeg_revoked_at=channels.eeg_revoked_at,
                              erased=_erased_fields(channels, _window_start(days)))
    start, end = _report_period(days)
    context = _lesson_context({student_id: summary}, {student_id: channels.heart}, days)
    summary["usual"] = _personal_baseline(
        student_id, start, end, {**_usual_current(summary), **context[student_id]},
        channels.heart, channels.consent_retrieved)
    return summary


@app.get("/api/students/{student_id}/topic-breakdown")
def student_topic_breakdown(student_id: str, request: Request):
    _verify_can_view_student(get_user(request), student_id)
    return _topic_breakdown(student_id)


# ─── at-home learning strategies ─────────────────────────────────────────

# The model pass is gated per request by the `strategy_llm_enabled` flag; the
# rule-based answer is always the fallback.
STRATEGY_LLM_MODEL   = os.getenv("STRATEGY_LLM_MODEL", "llama3.1:8b")
# Wall-clock budget (s) for the whole model call.
STRATEGY_LLM_TIMEOUT = env_number("STRATEGY_LLM_TIMEOUT", 20.0, float, minimum=1.0)

# Own pool, so the wall-clock timeout is enforced (httpx's is per operation).
# Built on first use; tests substitute the global.
_STRATEGY_LLM_POOL: ThreadPoolExecutor | None = None
_strategy_pool_lock = threading.Lock()


def _strategy_pool() -> ThreadPoolExecutor:
    """The model-call pool, created on first use under a lock."""
    global _STRATEGY_LLM_POOL
    with _strategy_pool_lock:
        if _STRATEGY_LLM_POOL is None:
            _STRATEGY_LLM_POOL = ThreadPoolExecutor(max_workers=2,
                                                    thread_name_prefix="strategy-llm")
        return _STRATEGY_LLM_POOL


def _shutdown_strategy_pool():
    """Cancel queued work on shutdown without joining (a worker may be stuck on the model).

    Resets the global so a reload builds a fresh pool.
    """
    global _STRATEGY_LLM_POOL
    with _strategy_pool_lock:
        pool, _STRATEGY_LLM_POOL = _STRATEGY_LLM_POOL, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


# Callers blocked waiting on the model, process-wide (they hold anyio threadpool
# slots). Past it, the model pass is skipped for the rule-based list.
_STRATEGY_LLM_MAX_WAITERS = env_number("STRATEGY_LLM_MAX_WAITERS", 4, int, minimum=1)
_strategy_llm_waiters = threading.BoundedSemaphore(_STRATEGY_LLM_MAX_WAITERS)


# Per-caller strategy requests per window, per worker process.
_STRATEGY_RATE_LIMIT  = env_number("STRATEGY_RATE_LIMIT", 10, int, minimum=1)

# Ingest volume bounds: the sidecar posts with the student's token, a trust
# boundary. ~1000x headroom over a 1 Hz sensor, to allow a backlog flush.
_INGEST_MAX_BATCH   = env_number("INGEST_MAX_BATCH", 500, int, minimum=1)
_INGEST_RATE_LIMIT  = env_number("INGEST_RATE_LIMIT", 120, int, minimum=1)
_INGEST_RATE_WINDOW = env_number("INGEST_RATE_WINDOW", 60.0, float, minimum=1.0)

_INGEST_LIMITER = _SlidingWindowLimiter(
    "ingest", _INGEST_RATE_LIMIT, _INGEST_RATE_WINDOW)

# Rows per student (not session: a closed session's window still ingests) per channel per minute. The
# sidecar pushes one per tick, 240/min at EEG_SAMPLE_HZ=4; this is twice a 10 Hz sidecar's, and the
# sidecar warns at boot when it would pass this default, which it cannot see.
_INGEST_ROWS_PER_MINUTE = env_number("INGEST_MAX_ROWS_PER_MINUTE", 1200, int,
                                     minimum=_INGEST_MAX_BATCH)
_INGEST_ROW_LIMITER = _SlidingWindowLimiter("ingest_rows", _INGEST_ROWS_PER_MINUTE, 60.0)

# Heart sources each sensor permits, keyed on `_may_record`'s composed flags, never raw consent.
_HEART_SOURCES_BY_RECORD_FLAG = {
    "record_headband_optical": ("muse_optics", "muse_ppg"),
    "record_camera":           ("rppg",),
}
_HEART_SENSOR_DECLINED = {
    "record_headband_optical": "headband heart sensor not consented",
    "record_camera":           "camera not consented",
}

_STRATEGY_RATE_WINDOW = env_number("STRATEGY_RATE_WINDOW", 60.0, float, minimum=1.0)
_STRATEGY_LIMITER = _SlidingWindowLimiter(
    "strategies", _STRATEGY_RATE_LIMIT, _STRATEGY_RATE_WINDOW)


def _rate_limit_strategies(user_id: str):
    """Raise 429 if this caller has already had its allowance this window."""
    refused_after = _STRATEGY_LIMITER.check(user_id)

    # Recorded outside the limiter's lock, so callers never queue behind a DB write.
    if refused_after is not None:
        _record_security_event("rate_limited", user_id,
                               limiter=_STRATEGY_LIMITER.name)
        raise HTTPException(
            429,
            "Too many strategy requests. Try again shortly.",
            headers={"Retry-After": str(refused_after)},
        )

_STRATEGY_COUNT = 5
_STRATEGY_MAX_CHARS = 320
# A floor too, so a degenerate reply ("1. a\n2. b") is rejected.
_STRATEGY_MIN_CHARS = 25

# Clinical vocabulary that discards the whole reply. Stemmed narrowly: an
# over-broad stem silently disables the model pass. "patient" matches noun forms only.
_CLINICAL_TERMS = re.compile(
    r"\b(diagnos\w*|disorder\w*|disabilit\w*|adhd|autis\w*|dyslex\w*|dyscalcul\w*|"
    r"depress(?:ion|ive)|anxiet\w*|anxious|medicat\w*|meds|prescri\w*|psychiatr\w*|"
    r"psycholog\w*|counsel\w*|clinical\w*|symptom\w*|disease\w*|syndrome\w*|"
    r"patients|(?:a|an|the|any|your|their)\s+patient|"
    r"therap(?:y|ist|ies)|treatment\w*|neurolog\w*|"
    r"cognitive impairment|special (?:needs|education\w*)|iep)\b",
    re.IGNORECASE,
)

# A connect failure; its subject decides it, as `_FAILURE_PHRASE`'s does ("wasn't connected yet" included).
_CONNECT_FAILURE = re.compile(
    r"\b(?:(?P<neg>\w+n['’]t|not|never|no\s+longer)\s+(?:be(?:en)?\s+)?(?:able\s+to\s+)?|"
    r"fail(?:s|ed|ing)?\s+to\s+|(?:trouble|problems?|issues?|difficult(?:y|ies))\s+)(?:connect|pair|sync)\w*",
    re.IGNORECASE)

# After a person's connect failure, what makes it a device's: nothing, a manner or a date, or a device
# word within a few words ("the new headband", "the heart sensor"). "Connect fractions to decimals" is maths.
_DEVICE_NAMED = (
    # "It" only as the object itself: "connect the ideas to it" is maths.
    r"\s+(?:(?:to|with)\s+)?(?:it|them)\b|"
    # Up the clause ("couldn't connect in class with the headband"), but not where the material was shown
    # ("on the tablet") or past a "what"/"which" clause: those devices are not what was being connected.
    r"\s+(?:(?!(?:on|from|via|through|what|which|who)\b)[\w'’-]+\s+){0,6}?"
    r"(?:(?:the|a|an|her|his|their|its|my|your|that|this)\s+)?(?:(?!(?:on|from|via|through)\b)[\w'’-]+\s+)?"
    r"(?:headband|headset|headphone|earbud|earphone|camera|webcam|sensor|device|muse|"
    r"bluetooth|eeg|laptop|computer|tablet|phone|watch|tracker|monitor|microphone|mic|wi-?fi|internet|"
    r"network|app)(?:s|es)?\b")
_DEVICE_NAMED_AFTER = re.compile(_DEVICE_NAMED, re.IGNORECASE)
_DEVICE_AFTER = re.compile(
    r"\s*(?:[.,;:!?)]|$)|\s+(?:properly|again|reliably|at\s+all|yesterday|today(?!['’]))\b|"
    # A preposition before a whole time word up to four in, not "in her head"; a number only as a date.
    # The time ends its clause or another time follows: "in class yesterday" is when, "in class
    # discussions" is where the thinking was.
    r"\s+(?:on|at|for|during|after|before|until|in)\s+(?:[\w'’-]+\s+){0,4}?(?:\d{1,2}(?:st|nd|rd|th)?\b"
    r"(?!\s+of\b)|(?:morning|afternoon|evening|night|lesson|session|class|day|week|start|beginning|end|"
    r"update|restart|break|lunch|recess|(?:mon|tues|wednes|thurs|fri|satur|sun)day)s?\b"
    r"(?=\s*(?:[.,;:!?)]|$)|\s+(?:and|but|so|because|as|when|while|or|until|yesterday|today|tonight|"
    r"either|too|again|anymore|at\s+all|before|after|during|this|that|last|on|at|in)\b))|"
    + _DEVICE_NAMED,
    re.IGNORECASE)

# Subjects a connect failure can have that are neither people nor devices: "the idea didn't connect with her".
_IDEA_SUBJECTS = frozenset({"idea", "ideas", "lesson", "lessons", "concept", "concepts", "topic", "topics",
                            "explanation", "explanations", "example", "examples", "material", "content",
                            "method", "methods", "question", "questions", "story", "stories"})

# A cause for a missing reading, which the summary cannot know: a sensor off is turned off, never broken.
# Not "fail": "could not be read" is honestly rephrased as "failed to load".
_CAUSE_TERMS = re.compile(
    r"\b(stopped working|stops working|"
    r"broke|broken|faulty|fault|malfunction\w*|defect\w*|glitch\w*|disconnect\w*|lost (?:the )?connection|"
    r"ran out of (?:battery|power|charge)|out of battery|(?:low|dead|flat) battery|"
    r"battery (?:died|ran (?:out|low|flat)|was (?:dead|flat|low|empty))|"
    r"technical (?:problem|issue|difficult)\w*)\b",
    re.IGNORECASE,
)

# A failure phrase whose subject decides it. Not "record": "wasn't recording because it was turned
# off" is the absence the summary must state. "Didn't work out as planned" is about a plan.
_FAILURE_PHRASE = re.compile(
    r"\b(?:(?P<neg>\w+n['’]t|not|never|no\s+longer)(?:\s+been)?\s+(?:work|function|respond)(?:s|ed|ing)?\b"
    r"(?!\s+out\s+(?:as|well|for|the\s+way)\b)|"
    r"(?:stopped|stops)\s+responding\b|"
    r"fail(?:s|ed)?\s+to\s+(?:respond|work)\b|"
    r"ha(?:d|s|ve)\s+(?:(?:some|a\s+few|several|many|any)\s+)?(?:issues|problems|trouble)\b)",
    re.IGNORECASE)

# A person subject ("the student didn't work on fractions") is effort; any other ("the recording wasn't
# working", "they", a device's name) is a cause. Bare "not" has no auxiliary, so it needs a named thing.
_PERSON_SUBJECTS = frozenset({"student", "students", "child", "children", "kid", "kids", "learner",
                              "learners", "pupil", "pupils", "daughter", "daughters", "son", "sons",
                              "girl", "girls", "boy", "boys", "classmate", "classmates",
                              "class", "classes", "group", "groups", "everyone", "everybody",
                              "he", "she", "i", "we", "you", "who"})
_THING_SUBJECTS = frozenset({"sensor", "sensors", "headband", "headbands", "camera", "cameras", "webcam",
                             "eeg", "heart", "pulse", "device", "devices", "muse", "recording", "readings",
                             "connection", "equipment", "feed", "signal", "it"})
_AUXILIARIES = frozenset({"do", "does", "did", "is", "was", "are", "were", "am", "has", "have", "had",
                          "can", "could", "will", "would", "should", "may", "might", "must"})
_ADVERBS = frozenset({"sometimes", "often", "usually", "occasionally", "still", "also", "just", "really",
                      "ever", "always", "again", "then", "mostly", "rarely", "actually", "simply"})
_SENTENCE_END = re.compile(r"[.!?;:\n]")


def _phrase_subject(words: list[str]) -> str:
    """The subject before a failure phrase, past a relative "who/that <verb>"."""
    if len(words) >= 3 and words[-2].lower() in ("who", "that"):
        words = words[:-2]
    return re.sub(r"['’]s$", "", words[-1]).lower() if words else ""


def _subject_of(text: str, m: re.Match) -> tuple[str, bool]:
    """The phrase's subject, and whether its "not" is bare (no auxiliary before it)."""
    words = re.findall(r"[\w’'-]+", _SENTENCE_END.split(text[:m.start()])[-1])
    bare = (m.group("neg") or "").lower() == "not"
    # Adverbs and auxiliaries in any order ("also did not", "has had"); an auxiliary makes "not" a negation.
    while words and words[-1].lower() in _ADVERBS | _AUXILIARIES:
        bare = bare and words[-1].lower() not in _AUXILIARIES
        words = words[:-1]
    return _phrase_subject(words), bare


def _subject_names_a_cause(subject: str, bare: bool) -> bool:
    # An idea failing ("the method didn't work for her") is teaching, as a person's is effort.
    return subject in _THING_SUBJECTS if bare else subject not in _PERSON_SUBJECTS | _IDEA_SUBJECTS


def _names_a_cause(text: str) -> bool:
    """Whether a summary says why a reading is missing (broken, disconnected, a sensor not working)."""
    if _CAUSE_TERMS.search(text):
        return True
    if any(_subject_names_a_cause(*_subject_of(text, m)) for m in _FAILURE_PHRASE.finditer(text)):
        return True
    for m in _CONNECT_FAILURE.finditer(text):
        subject, bare = _subject_of(text, m)
        if subject in _PERSON_SUBJECTS:
            if _DEVICE_AFTER.match(text, m.end()):
                return True
        elif subject in _IDEA_SUBJECTS:
            if _DEVICE_NAMED_AFTER.match(text, m.end()):
                return True
        elif _subject_names_a_cause(subject, bare):
            return True
    return False

# Leading "1.", "2)", "-", "*", "•" from a numbered or bulleted model reply.
_LIST_MARKER = re.compile(r"^\s*(?:\d+\s*[\).:]|[-*•])\s*")

# Markdown emphasis, stripped since nothing renders it. Word-boundary guarded so
# snake_case names and "7*8" multiplication survive.
_MD_ASTERISK = re.compile(r"(?<![\w*])(\*{1,3})(?=\S)(.+?)(?<=\S)\1(?![\w*])")
_MD_UNDERSCORE = re.compile(r"(?<!\w)(_{1,3})(?=\S)(.+?)(?<=\S)\1(?!\w)")


def _strip_emphasis(line: str) -> str:
    return _MD_UNDERSCORE.sub(r"\2", _MD_ASTERISK.sub(r"\2", line))


def _weakest_topic(topics: list[dict]):
    """Lowest-accuracy topic the student has attempted and been scored on.

    A None accuracy (flashcards viewed, nothing answered) is skipped, never read as 0%.
    """
    scored = [t for t in topics if (t.get("attempted_questions") or 0) > 0
              and t.get("accuracy") is not None]
    if not scored:
        return None
    return min(scored, key=lambda t: t["accuracy"])


def _topic_summary(row: dict | None) -> dict | None:
    """Just the three fields a topic-naming response is about.

    Named, so the row's `stress` reading never reaches a surface that did not gate on consent.
    """
    if not row:
        return None
    return {
        "topic_name": row.get("topic_name"),
        "accuracy": row.get("accuracy"),
        "attempted_questions": row.get("attempted_questions"),
    }


def _weakest_topic_summary(topics: list[dict]) -> dict | None:
    """Just the fields the strategies response is about."""
    return _topic_summary(_weakest_topic(topics))


def _strategy_basis(student_id: str, days: int, include_face: bool) -> dict:
    """The slice of a weekly report this endpoint reads, from the aggregate RPC.

    Report-shaped for the shared consumers; `averages` lists its fields
    explicitly. Consent and include_face skip the read, not just the field.
    """
    channels = _reportable_channels(student_id, include_face)
    summary = _signal_summary(student_id, days, include_heart=channels.heart,
                              include_emotion=channels.emotion,
                              consent_retrieved=channels.consent_retrieved,
                              eeg_enabled=channels.eeg,
                              eeg_revoked_at=channels.eeg_revoked_at)
    return {
        "days": days,
        "face_included": summary["face_included"],
        # Not `retrieved`, which is a per-table dict on a real report.
        "signals_retrieved": summary["retrieved"],
        "averages": {
            "focus": summary["focus"],
            "stress": summary["stress"],
            "engagement": summary["engagement"],
            "face_attention": summary["face_attention"],
        },
        "sample_counts": {"sessions": summary["sessions"]},
    }


def _rule_based_strategies(report: dict, topics: list[dict]) -> list[str]:
    """Deterministic at-home strategies derived from the weekly report.

    Always computed, and always what the endpoint falls back to. Thresholds are
    on the 0..1 ratios the signal tables store.
    """
    averages = report.get("averages") or {}
    strategies = []

    weakest = _weakest_topic(topics)
    if weakest:
        label = str(weakest.get("topic_name") or "the weakest topic").replace("_", " ")
        strategies.append(
            f"Spend 10-15 minutes on {label} before new material -- it is currently "
            f"the lowest-scoring topic at {weakest.get('accuracy')}%."
        )
    else:
        strategies.append(
            "Start with a short review and ask your child to explain one solved "
            "problem out loud, which shows where their understanding actually stops."
        )

    # Stored stress is `1 - calm`, so stress >= 0.65 is calm <= 35%; named as the page shows it.
    stress = averages.get("stress")
    if stress is not None and float(stress) >= 0.65:
        strategies.append(
            "Break practice into shorter blocks with a two-minute pause between "
            "them -- calm readings ran low this week."
        )
    else:
        strategies.append(
            "Keep practice to 15-20 minute blocks, each followed by quick feedback "
            "on what went well."
        )

    focus = averages.get("focus")
    if focus is not None and float(focus) < 0.45:
        strategies.append(
            "Clear the workspace of phones and second screens, and set one small "
            "goal per block -- focus indicators were low this week."
        )
    else:
        strategies.append(
            "Keep the study setup and time of day consistent, since the current "
            "routine is holding up."
        )

    # No attention rule: `face_attention` has no producer.
    strategies.append(
        "Close each session by asking which problem felt hardest and what helped "
        "most -- it makes the next session easier to plan."
    )
    return strategies[:_STRATEGY_COUNT]


def _strategy_prompt(report: dict, topics: list[dict], baseline: list[str]) -> str:
    """Prompt text built from aggregates only; nothing that identifies the child."""
    averages = report.get("averages") or {}

    def _pct(v):
        return "unavailable" if v is None else f"{round(float(v) * 100)}%"

    weakest = _weakest_topic(topics)
    topic_line = (
        f"{str(weakest.get('topic_name')).replace('_', ' ')} at {weakest.get('accuracy')}%"
        if weakest else "no attempted topics yet"
    )
    # No attention line: `face_attention` has no producer yet.
    return (
        "You are helping a parent support their child's maths practice at home.\n"
        "Use only the weekly summary below. These are classroom learning "
        "indicators, not medical measurements -- do not diagnose, do not name any "
        "condition, and do not give medical advice. Never use the word stress or "
        "stressed: calm is a relaxation reading, not a stress measure.\n"
        f"Return exactly {_STRATEGY_COUNT} short, practical, at-home strategies as "
        "a numbered list. One sentence each, no preamble.\n\n"
        f"Weekly summary (last {report.get('days', 7)} days):\n"
        f"- average focus {_pct(averages.get('focus'))}\n"
        # Calm, as the report shows it: the stored `stress` is `1 - calm`, not a stress measure.
        f"- average calm (headband relaxation reading) {_pct(_calm_of(averages.get('stress')))}\n"
        # No engagement line: it is the focus index under another name
        # (signal_mapping.py), and restated it reads as a second fact.
        f"- weakest attempted topic: {topic_line}\n"
        f"- practice sessions recorded: {(report.get('sample_counts') or {}).get('sessions', 0)}\n\n"
        "For reference, here is a safe baseline answer:\n"
        + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(baseline))
    )


def _parse_strategy_lines(raw: str) -> list[str]:
    """The list items of a model reply, in order (also used by the chart summary).

    Only marked lines count, so a lead-in is not item 1. Emphasis is unwrapped
    first, since "**1. ...**" puts an asterisk before the number.
    """
    lines = []
    for line in (raw or "").splitlines():
        cleaned, marked = _LIST_MARKER.subn("", _strip_emphasis(line))
        cleaned = cleaned.strip()
        if marked and cleaned:
            lines.append(cleaned)
    return lines


def _validated_strategies(raw: str) -> list[str] | None:
    """Model output, or None if it fails any check (caller keeps the rules).

    No partial acceptance: one broken rule rejects the whole reply.
    """
    # The whole raw reply: a clinical term in a preamble rejects it too.
    if _CLINICAL_TERMS.search(raw or ""):
        return None
    # Calm is not a stress measure, and the rule-based list never says "stress" (CLAUDE.md).
    if _STRESS_WORD.search(raw or ""):
        return None
    lines = _parse_strategy_lines(raw)
    if len(lines) < 3:
        return None
    # The floor rejects well-formed scaffolding like "1. a".
    if any(not _STRATEGY_MIN_CHARS <= len(line) <= _STRATEGY_MAX_CHARS for line in lines):
        return None
    return lines[:_STRATEGY_COUNT]


def _llm_strategies(prompt: str, timeout: float | None = None) -> list[str] | None:
    """One model attempt via `llm_client`, or None on any failure.

    `timeout` is what remains of the caller's budget after queueing.
    Catches everything, `GenerationUnavailable` included: the rules are the fallback.
    """
    try:
        raw = llm_client.generate_text(
            prompt,
            ollama_model=STRATEGY_LLM_MODEL,
            temperature=0.4, top_p=None, top_k=None,
            claude_temperature=0.4,
            max_tokens=1024,
            timeout=STRATEGY_LLM_TIMEOUT if timeout is None else timeout,
        )
    except Exception as e:
        print(f"[learning_strategies:llm] {e}")
        return None
    return _validated_strategies(raw or "")


def _llm_strategies_bounded(prompt: str) -> list[str] | None:
    """_llm_strategies under a deadline, if admitted under _STRATEGY_LLM_MAX_WAITERS.

    A sync caller blocked on the model holds an anyio threadpool slot.
    """
    # Non-blocking, or the wait just moves one lock deeper.
    if not _strategy_llm_waiters.acquire(blocking=False):
        print(f"[learning_strategies:llm] at capacity "
              f"({_STRATEGY_LLM_MAX_WAITERS} in flight); using the rule-based answer")
        return None
    try:
        return _llm_strategies_admitted(prompt)
    finally:
        _strategy_llm_waiters.release()


def _llm_strategies_admitted(prompt: str) -> list[str] | None:
    """The wait itself, once admitted by _llm_strategies_bounded.

    One deadline shared by the wait and the work, so a queued call cannot hold
    a worker for nearly twice STRATEGY_LLM_TIMEOUT.
    """
    deadline = time.monotonic() + STRATEGY_LLM_TIMEOUT

    def _run():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # The caller already gave up; don't open a socket.
            return None
        return _llm_strategies(prompt, remaining)

    future = None
    try:
        # Inside the try: submit() raises if the pool is shutting down.
        future = _strategy_pool().submit(_run)
        return future.result(timeout=STRATEGY_LLM_TIMEOUT)
    except FutureTimeoutError:
        # Cancel a still-queued task, or an outage piles up abandoned prompts.
        future.cancel()
        print(f"[learning_strategies:llm] abandoned after {STRATEGY_LLM_TIMEOUT}s")
        return None
    except Exception as e:
        # The pool itself refused the work; _llm_strategies swallows its own.
        print(f"[learning_strategies:llm] {e}")
        return None


class LearningStrategyRequest(StrictModel):
    include_face: bool = True
    # No ge/le: the handler clamps it, and a field bound would 422 instead.
    days: int = 7
    # Topic input from this practice session; the signal averages stay live-session.
    practice_session_id: str | None = Field(None, max_length=_ID_MAX)


def _topics_from_practice_summary(topic_summary: dict) -> list[dict]:
    """A practice session's `topic_summary` in `_topic_breakdown`'s shape.

    `accuracy` is None for a flashcard-only topic: viewed, never scored, so it is not a 0%.
    """
    out = []
    for topic, stats in (topic_summary or {}).items():
        out.append({
            "topic_id": None,
            "topic_name": topic,
            "attempted_questions": stats.get("attempted") or 0,
            "correct_questions": None,
            "accuracy": stats.get("correct"),
            "stress": None,
            "updated_at": None,
        })
    return out


@app.post("/api/students/{student_id}/learning-strategies")
def student_learning_strategies(student_id: str, request: Request, payload: LearningStrategyRequest):
    """At-home strategies from the weekly report, or from one practice session.

    Always answers; `source` says whether the model refined the rules.
    Access check before the rate limit, so a 403 is never masked by a 429.
    """
    viewer = get_user(request)
    _verify_can_view_student(viewer, student_id)
    _rate_limit_strategies(viewer["id"])

    days = max(1, min(payload.days, 30))
    report = _strategy_basis(student_id, days, payload.include_face)

    if payload.practice_session_id:
        practice = _row_or_404(
            supabase.table("practice_sessions").select("user_id, topic_summary")
                .eq("id", payload.practice_session_id),
            "Practice session")
        if practice.get("user_id") != student_id:
            # Subject is the admitted student, not the session's owner.
            _record_security_event("authz_denied", viewer["id"], student_id,
                                   check="practice_session_student")
            raise HTTPException(403, "That practice session does not belong to this student")
        topics = _topics_from_practice_summary(practice.get("topic_summary") or {})
    else:
        topics = _topic_breakdown(student_id)

    strategies = _rule_based_strategies(report, topics)
    source = "rule-based"

    if _feature_flags()["strategy_llm_enabled"]["enabled"]:
        refined = _llm_strategies_bounded(_strategy_prompt(report, topics, strategies))
        if refined:
            strategies, source = refined, "model-refined"
        else:
            source = "rule-based (model output rejected)"

    return {
        "student_id": student_id,
        "generated_at": _utc_now().isoformat(),
        "strategies": strategies,
        "source": source,
        "basis": {
            "days": days,
            "face_included": payload.include_face,
            # False: the averages are defaults, not a quiet week.
            "signals_retrieved": report.get("signals_retrieved", True),
            "averages": report.get("averages") or {},
            "weakest_topic": _weakest_topic_summary(topics),
            "practice_session_id": payload.practice_session_id,
        },
    }


# ─── chart-explaining summary ────────────────────────────────────────────
# Same shape as strategies: a deterministic answer, a flag, and the four bounds.
# Pool and waiters stay per-endpoint so one cannot starve another.

CHART_SUMMARY_LLM_MODEL = os.getenv("CHART_SUMMARY_LLM_MODEL", "llama3.1:8b")
# Floored like STRATEGY_LLM_TIMEOUT: at zero the pass is silently off.
CHART_SUMMARY_LLM_TIMEOUT = env_number("CHART_SUMMARY_LLM_TIMEOUT", 20.0, float, minimum=1.0)

_CHART_SUMMARY_LLM_POOL: ThreadPoolExecutor | None = None
_chart_summary_pool_lock = threading.Lock()


def _chart_summary_pool() -> ThreadPoolExecutor:
    """The model-call pool, created on first use. See `_strategy_pool`."""
    global _CHART_SUMMARY_LLM_POOL
    with _chart_summary_pool_lock:
        if _CHART_SUMMARY_LLM_POOL is None:
            _CHART_SUMMARY_LLM_POOL = ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="chart-summary-llm")
        return _CHART_SUMMARY_LLM_POOL


def _shutdown_chart_summary_pool():
    """Drop the queue on the way out (from _lifespan). See `_shutdown_strategy_pool`.

    Resets the global so a reload builds a fresh pool.
    """
    global _CHART_SUMMARY_LLM_POOL
    with _chart_summary_pool_lock:
        pool, _CHART_SUMMARY_LLM_POOL = _CHART_SUMMARY_LLM_POOL, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


# Callers waiting on the model at once (anyio slots). Floor 1, or nobody is admitted.
_CHART_SUMMARY_MAX_WAITERS = env_number("CHART_SUMMARY_MAX_WAITERS", 4, int, minimum=1)
_chart_summary_waiters = threading.BoundedSemaphore(_CHART_SUMMARY_MAX_WAITERS)

_CHART_SUMMARY_RATE_LIMIT  = env_number("CHART_SUMMARY_RATE_LIMIT", 10, int, minimum=1)
_CHART_SUMMARY_RATE_WINDOW = env_number("CHART_SUMMARY_RATE_WINDOW", 60.0, float, minimum=1.0)
_CHART_SUMMARY_LIMITER = _SlidingWindowLimiter(
    "chart_summary", _CHART_SUMMARY_RATE_LIMIT, _CHART_SUMMARY_RATE_WINDOW)


def _rate_limit_chart_summary(user_id: str):
    """Raise 429 if this caller has already had its allowance this window."""
    refused_after = _CHART_SUMMARY_LIMITER.check(user_id)

    # Recorded outside the limiter's lock -- see `_rate_limit_strategies`.
    if refused_after is not None:
        _record_security_event("rate_limited", user_id,
                               limiter=_CHART_SUMMARY_LIMITER.name)
        raise HTTPException(
            429,
            "Too many summary requests. Try again shortly.",
            headers={"Retry-After": str(refused_after)},
        )


# Week-over-week move below this (0..1 ratio) is "steady": inside strap-fit noise.
_CHART_SUMMARY_TREND_MIN_DELTA = 0.05

# Floor as well as ceiling, as `_STRATEGY_MIN_CHARS`.
_CHART_SUMMARY_MAX_CHARS = 320
_CHART_SUMMARY_MIN_CHARS = 25

# Every numeral in a model reply must be one this endpoint supplied.
_NUMERAL = re.compile(r"\d+(?:\.\d+)?")
# Stripped first, so "1,240" is not read as 1 and 240.
_THOUSANDS_SEP = re.compile(r"(?<=\d),(?=\d)")


def _trend_direction(weeks: list[dict], key: str) -> dict:
    """Which way one series moved across the weeks that have a reading.

    Always a dict: `direction` is None below two weeks or across a score-scale change
    (`mixed_scale`; for stress or calm, two calm sources too), and `weeks_with_data` tells zero
    weeks from one. Anchored on the first and last weeks *with* a reading, so null weeks don't hide a trend.
    """
    readings = [w for w in (weeks or []) if isinstance(w.get(key), (int, float))]
    points = [w[key] for w in readings]
    scales = _combine_ranges(w.get("score_scale") for w in readings)
    mixed = bool(scales) and (scales["min"] != scales["max"]
                              or (key in ("stress", "calm") and len(scales["calm_sources"]) > 1))
    if len(points) < 2 or mixed:
        return {"direction": None, "first": None, "last": None,
                "weeks_with_data": len(points), "mixed_scale": mixed}
    first, last = float(points[0]), float(points[-1])
    delta = last - first
    if abs(delta) < _CHART_SUMMARY_TREND_MIN_DELTA:
        direction = "steady"
    else:
        direction = "up" if delta > 0 else "down"
    return {"direction": direction, "first": round(first, 4),
            "last": round(last, 4), "weeks_with_data": len(points), "mixed_scale": False}


def _summary_basis_rollup(student_id: str, weeks: int, start: date, end: date,
                          channels: "ReportChannels") -> tuple[list, bool]:
    """One rollup read covering both the term trend's weeks and the usual's 8 prior weeks."""
    since = min(_week_start(end) - timedelta(weeks=weeks - 1),
                start - timedelta(weeks=_USUAL_WEEKS))
    wanted = ["cognitive"] + (["heart"] if channels.heart else []) \
        + (["emotion"] if channels.emotion else [])
    try:
        return (supabase.table("signal_daily_rollup").select("*")
                .eq("user_id", student_id).in_("channel", wanted)
                .gte("day", since.isoformat()).lte("day", end.isoformat())
                .execute().data or []), True
    except Exception as e:                                     # noqa: BLE001
        print(f"[chart_summary] rollup for {student_id[:8]}: {e}")
        return [], False


def _chart_summary_basis(student_id: str, days: int, weeks: int,
                         include_face: bool) -> dict:
    """Every figure this endpoint may state, and why a missing one is missing.

    One consent read feeds both aggregate and trend, and the sources are the
    ones the report page draws, so the summary cannot disagree with the charts.
    """
    channels = _reportable_channels(student_id, include_face)
    summary = _signal_summary(student_id, days, include_heart=channels.heart,
                              include_emotion=channels.emotion,
                              consent_retrieved=channels.consent_retrieved,
                              eeg_enabled=channels.eeg,
                              eeg_revoked_at=channels.eeg_revoked_at)
    start, end = _report_period(days)
    rollup = _summary_basis_rollup(student_id, weeks, start, end, channels)
    # One sessions read for the trend and the usual, so they cannot disagree about a lesson.
    open_sessions = _open_sessions([student_id]) if channels.heart else {}
    trend = _signal_trend(student_id, weeks,
                          include_heart=channels.heart,
                          include_emotion=channels.emotion,
                          consent_retrieved=channels.consent_retrieved,
                          emotion_revoked_at=channels.emotion_revoked_at,
                          heart_revoked_at=channels.heart_revoked_at,
                          rollup=rollup,
                          open_session=_any_open_session(student_id, channels.heart,
                                                         open_sessions))
    stats = _stats_including_open_session(student_id)
    topics, topics_retrieved = _topic_breakdown_with_state(student_id)
    attempted = [t for t in topics if (t.get("attempted_questions") or 0) > 0]

    total = stats.get("total_questions") or 0
    correct = stats.get("total_correct") or 0

    def _channel(enabled: bool, revoked_at, samples, table: str) -> dict:
        info = {"enabled": enabled, "revoked_at": revoked_at, "samples": samples}
        # Only a permitted, read, empty channel needs to know whether rows arrived at all.
        if enabled and summary["retrieved"] and not samples:
            info["any_rows"] = _any_rows_since(table, student_id, days)
        return info

    scored = [t for t in attempted if t.get("accuracy") is not None]
    # Calm is EEG stress the right way up; the trend is computed on calm itself.
    calm_weeks = [{**w, "calm": _calm_of(w.get("stress"))} for w in trend.get("weeks") or []]
    eeg_info = _channel(channels.eeg, channels.eeg_revoked_at,
                        summary["cognitive_samples"], "cognitive_signals")
    heart_info = _channel(channels.heart, channels.heart_revoked_at,
                          summary["heart_samples"], "heart_signals")
    # `any_rows` was already asked for an empty heart channel; reuse it rather than ask twice.
    context = _lesson_context(
        {student_id: summary}, {student_id: channels.heart}, days,
        {student_id: heart_info["any_rows"]} if "any_rows" in heart_info else None,
        open_sessions)[student_id]
    usual = _personal_baseline(student_id, start, end, {**_usual_current(summary), **context},
                               channels.heart, channels.consent_retrieved, rollup=rollup)
    arousal = usual["measures"]["body_arousal"]
    return {
        "days": days,
        "weeks": weeks,
        "face_included": summary["face_included"],
        # Not `retrieved`: a report's `retrieved` is a per-table dict.
        "signals_retrieved": summary["retrieved"],
        "trend_retrieved": trend.get("retrieved", True),
        "stats_retrieved": stats.get("retrieved", True),
        # Here an empty topic list is an assertion, so an outage must be told apart.
        "topics_retrieved": topics_retrieved,
        "consent_retrieved": channels.consent_retrieved,
        "channels": {
            # No `emotion`: the report states no facial number.
            "eeg": eeg_info,
            "heart": heart_info,
        },
        "averages": {
            "focus": summary["focus"],
            "calm": _calm_of(summary["stress"]),
            # No `engagement`: it is the focus index under another name.
            "heart_rate_bpm": summary["heart_rate_bpm"],
            "rmssd_ms": summary["rmssd_ms"],
            "body_arousal": arousal["current"],
        },
        # Focus and calm only: the delta is in 0..1 ratio units, not bpm.
        "trend": {
            "focus": _trend_direction(trend.get("weeks") or [], "focus"),
            "calm": _trend_direction(calm_weeks, "calm"),
        },
        "usual": usual,
        "academic": {
            "sessions": summary["sessions"],
            "total_questions": total,
            "total_correct": correct,
            "accuracy": round(correct / total * 100) if total else None,
        },
        "topics": {
            "weakest": _weakest_topic_summary(topics),
            # Scored topics only: an untouched one reports 0%.
            "strongest": _topic_summary(
                max(scored, key=lambda t: t["accuracy"]) if scored else None),
            "attempted_count": len(attempted),
            # Ties make max() and min() return one topic; this tells that from a lone topic.
            "scored_count": len(scored),
        },
    }


def _pct_int(value) -> int | None:
    """A 0..1 ratio as whole percent, or None. The chart axes round the same way."""
    return None if value is None else round(float(value) * 100)


def _numerals(text: str) -> list[float]:
    """Every number in a piece of text, thousands separators absorbed."""
    return [float(t) for t in _NUMERAL.findall(_THOUSANDS_SEP.sub("", text or ""))]


def _chart_summary_figures(baseline: list[str]) -> set[float]:
    """Every number the model's reply is allowed to contain.

    Read out of the deterministic sentences the prompt sends, never enumerated
    from the basis, so it cannot drift from them. Does not check a number is
    attached to the right measurement.
    """
    return {n for line in baseline for n in _numerals(line)}


# EEG is named by its readings, not the sensor. Calm, never "stress": see CLAUDE.md.
_CHART_SUMMARY_CHANNEL_NAMES = {"eeg": "Focus and calm", "heart": "Heart rate"}


def _channel_absence(channel: str, basis: dict) -> str | None:
    """Why a channel has no figure to state, or None if it has one.

    Ordered as `cellLabel`: consent unreadable, revoked, unread, no samples. A revoked
    channel with samples (EEG is read regardless) has figures, from before it was turned off.
    `samples` counts usable rows, so `any_rows` is what tells "unusable" from "nothing".
    """
    info = (basis.get("channels") or {}).get(channel) or {}
    name = _CHART_SUMMARY_CHANNEL_NAMES.get(channel, channel)
    if not basis.get("consent_retrieved", True):
        return (f"{name} is not described here: whether this sensor was "
                "permitted could not be read, so nothing is claimed about it.")
    if not info.get("enabled") and not info.get("samples"):
        revoked = _local_date_text(info.get("revoked_at"))
        return (f"{name} was not recorded because the sensor was turned off"
                + (f" on {revoked}." if revoked else "."))
    if not basis.get("signals_retrieved", True):
        return f"{name} could not be read this time, so no figure is given for it."
    if not info.get("samples"):
        if info.get("any_rows"):
            return None             # recorded, none usable: the caller says so
        if "any_rows" in info and info["any_rows"] is None:
            return (f"{name} has no usable reading here, and whether anything was "
                    "recorded could not be read.")
        return (f"{name} was permitted but nothing was recorded, so there is "
                "no reading to describe.")
    return None


def _any_rows_since(table: str, student_id: str, days: int) -> bool | None:
    """Whether any row of `table` landed for the student in the last `days` school days.

    Usable or not: the summary RPC counts only usable ones. None if the read failed.
    """
    tz = _school_timezone()
    today = _utc_now().astimezone(tz).date()
    since = datetime.combine(today - timedelta(days=days - 1), datetime.min.time(),
                             tzinfo=tz).astimezone(timezone.utc).isoformat()
    try:
        rows = supabase.table(table).select("ts").eq("user_id", student_id) \
            .gte("ts", since).limit(1).execute().data or []
        return bool(rows)
    except Exception as e:                                     # noqa: BLE001
        print(f"[chart_summary] could not check {table} for {student_id[:8]}: {e}")
        return None


_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def _local_date_text(stamp: str | None) -> str | None:
    """A stored timestamp as a plain date in the school's timezone, or None.

    Formatted by hand: the unpadded-day `strftime` directive differs by platform.
    """
    if not stamp:
        return None
    parsed = _parse_ts(stamp)
    if not parsed:
        return None
    local = parsed.astimezone(_school_timezone())
    return f"{local.day} {_MONTHS[local.month - 1]}"


_TREND_WORDS = {"up": "risen", "down": "fallen", "steady": "held steady"}


def _plural(count, noun: str) -> str:
    """`noun` agreeing with `count`. Only the regular -s form is needed here."""
    return noun if count == 1 else noun + "s"


def _topic_prose(name, capitalise: bool = False) -> str:
    """A stored topic name as prose: `angle_relationships` -> `angle relationships`.

    `capitalise` raises only the first letter; `.capitalize()` lowercases the rest.
    """
    text = str(name or "an unnamed topic").replace("_", " ")
    return text[:1].upper() + text[1:] if capitalise else text


def _rule_based_chart_summary(basis: dict) -> list[str]:
    """The deterministic summary. Always computed, always the fallback.

    Every sentence states a computed number or says why there is none.
    """
    return _chart_summary_lines(basis)[0]


_USUAL_NAMES = {"focus": "focus", "calm": "calm", "heart_rate_bpm": "heart rate",
                "rmssd_ms": "heart-rate variability", "body_arousal": "body arousal"}
_USUAL_VERDICT_WORDS = {"about_usual": "about usual", "higher": "higher than usual",
                        "lower": "lower than usual"}


def _list_words(items: list[str], conjunction: str = "and") -> str:
    """"a", "a and b", "a, b and c"."""
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {conjunction} " + items[-1]


# Why a measure is `not_comparable`, as the sentence gives it.
_NOT_COMPARABLE_WORDS = {
    "mixed_scale": "the headband's scoring changed during this period",
    "scale_unknown": "this period's scoring was not recorded",
    "sensor_changed": "this period's heart readings came from more than one sensor",
}


def _usual_sentences(basis: dict) -> list[str]:
    """How each measure compares with the student's own earlier days, in words, no numbers."""
    usual = basis.get("usual")
    if not usual:
        return []
    if not usual.get("retrieved", True):
        return ["How these figures compare with this student's earlier days could not be read."]
    measures = usual.get("measures") or {}
    compared, short, pending = [], [], []
    changed: dict[str, list[str]] = {}
    for key, name in _USUAL_NAMES.items():
        m = measures.get(key) or {}
        if m.get("status") == "compared":
            compared.append(f"{name} was {_USUAL_VERDICT_WORDS[m['verdict']]}")
        elif m.get("status") == "not_enough_history":
            short.append(name)
        elif m.get("status") == "pending":
            pending.append(name)
        elif m.get("status") == "not_comparable":
            changed.setdefault(m.get("reason"), []).append(name)
    out = []
    if compared:
        out.append("Compared with this student's own earlier days, "
                   + _list_words(compared) + ".")
    if short:
        out.append(_as_sentence(f"{_list_words(short)} {'does' if len(short) == 1 else 'do'} "
                                "not have enough earlier days yet to say what is usual "
                                "for this student"))
    if pending:
        out.append(_as_sentence(f"{_list_words(pending)} will be compared with earlier days "
                                "once this period's lessons have finished"))
    for reason, names in changed.items():
        why = _NOT_COMPARABLE_WORDS.get(reason, "this period was recorded differently")
        out.append(_as_sentence(f"{_list_words(names)} cannot be compared with earlier days, "
                                f"because {why}"))
    return out


# By `_body_arousal` state; a measured share has its own sentence.
_BODY_AROUSAL_ABSENT = {
    "calibrating": ("Body arousal has no reading yet for this period: the headband was still "
                    "measuring this student's starting heart rate."),
    # Names no sensor: an open lesson's rows may be camera rows that never name one.
    "pending": ("Body arousal has no reading yet for this period: a lesson is still in "
                "progress, and it is worked out when the lesson finishes."),
    "unusable": ("Body arousal has no reading for this period: the headband's heart readings "
                 "were not steady enough to use."),
    "camera_only": ("Body arousal is measured only by the headband's heart sensor, not the "
                    "camera, so there is no reading for this period."),
    "none": ("Body arousal has no reading for this period, because the headband's heart "
             "sensor recorded nothing."),
    "unknown": ("Body arousal has no reading for this period, and whether a lesson is still "
                "in progress could not be checked."),
    "not_retrieved": "Body arousal could not be read this time, so no figure is given for it.",
}


def _body_arousal_sentence(basis: dict) -> str | None:
    """Body arousal, or why it has no reading; None when the heart channel is not described."""
    arousal = (basis.get("usual") or {}).get("body_arousal") or {}
    state = arousal.get("state")
    share = (basis.get("averages") or {}).get("body_arousal")
    if state == "measured" and share is not None:
        # `heart_stress`: `high` is 10+ bpm above the lesson's own opening baseline.
        out = (f"Body arousal -- the share of the headband's usable heart readings that were "
               f"at least 10 beats a minute above the rate it measured at the start of each "
               f"lesson -- was {_pct_int(share)}%. It rises with excitement, effort and "
               "movement as well as with stress.")
        if arousal.get("few_readings"):
            out += " It rests on only a few readings, so treat it as rough."
        if arousal.get("pending"):
            out += " Lessons still in progress are not counted yet."
        elif "pending" in arousal and arousal["pending"] is None:
            out += " Whether a lesson is still in progress could not be checked."
        return out
    return _BODY_AROUSAL_ABSENT.get(state)


def _chart_summary_lines(basis: dict) -> tuple[list[str], list[int]]:
    """The deterministic sentences, and the indexes of those about sensor readings."""
    averages = basis.get("averages") or {}
    academic = basis.get("academic") or {}
    topics = basis.get("topics") or {}
    trend = basis.get("trend") or {}
    out: list[str] = []

    days = basis.get("days")
    # Two sentences: totals are lifetime, the session count is the last `days`.
    if not basis.get("stats_retrieved", True):
        out.append("This student's practice totals could not be read, so the "
                   "activity figures beside the charts are not described here.")
    elif academic.get("accuracy") is None:
        out.append("No questions have been answered yet, so there is nothing "
                   "for the charts to compare against.")
    else:
        out.append(
            f"Across all of their practice so far, {academic.get('total_correct')} "
            f"of {academic.get('total_questions')} questions have been answered "
            f"correctly -- an accuracy of {academic.get('accuracy')}%.")
    if not basis.get("signals_retrieved", True):
        # `sessions` is 0 after a failed signal read; not a quiet week.
        out.append("How many sessions were recorded could not be read, so the "
                   f"last {days} days are not described here.")
    else:
        sessions = academic.get("sessions") or 0
        out.append(f"{sessions} {_plural(sessions, 'session')} "
                   f"{'was' if sessions == 1 else 'were'} recorded in the last "
                   f"{days} {_plural(days, 'day')}, which is the period the "
                   "weekly charts cover.")

    first_sensor_line = len(out)
    for channel, key, label in (("eeg", "focus", "Average focus"),
                                ("eeg", "calm", "Average calm")):
        absent = _channel_absence(channel, basis)
        value = _pct_int(averages.get(key))
        if absent:
            # One sentence per channel, not per reading.
            if absent not in out:
                out.append(absent)
            continue
        if value is None:
            out.append(f"{label} has no usable reading for this period, even though "
                       "the headband recorded -- the readings were rejected rather "
                       "than missing.")
            continue
        move = trend.get(key) or {}
        weeks_seen = move.get("weeks_with_data") or 0
        if move.get("direction"):
            out.append(
                f"{label} is {value}%, and across the weeks with readings it has "
                f"{_TREND_WORDS[move['direction']]} from "
                f"{_pct_int(move['first'])}% to {_pct_int(move['last'])}%.")
        elif move.get("mixed_scale"):
            # A sidecar restart can change the scale; the two ends are not comparable.
            out.append(f"{label} is {value}%. Its weekly readings were scored on different "
                       "scales, so no direction is given for it.")
        elif not basis.get("trend_retrieved", True):
            # A failed trend read must not read as a first week.
            out.append(f"{label} is {value}%. The term trend could not be read, "
                       "so no direction is given for it.")
        elif weeks_seen:
            out.append(f"{label} is {value}%. Only one week has readings for it "
                       "so far, so there is no direction to report yet.")
        else:
            # Zero weeks: several causes are indistinguishable here, so name none.
            out.append(f"{label} is {value}%. No week has a reading for it yet, "
                       "so the term chart cannot show a direction.")

    eeg = (basis.get("channels") or {}).get("eeg") or {}
    if not eeg.get("enabled") and eeg.get("samples") and not _channel_absence("eeg", basis):
        revoked = _local_date_text(eeg.get("revoked_at"))
        out.append("These focus and calm figures are from before the sensor was turned off"
                   + (f" on {revoked}." if revoked else "."))

    heart_absent = _channel_absence("heart", basis)
    bpm, rmssd = averages.get("heart_rate_bpm"), averages.get("rmssd_ms")
    if heart_absent:
        out.append(heart_absent)
    elif bpm is None:
        out.append("Heart rate windows were recorded but none passed the quality "
                   "checks, so no average is shown.")
    elif rmssd is None:
        out.append(f"Average heart rate is {round(float(bpm))} bpm.")
    else:
        out.append(f"Average heart rate is {round(float(bpm))} bpm, and heart-rate "
                   f"variability is {round(float(rmssd))} ms.")
    arousal = None if heart_absent else _body_arousal_sentence(basis)
    if arousal:
        out.append(arousal)
    out.extend(_usual_sentences(basis))
    sensor_lines = list(range(first_sensor_line, len(out)))

    weakest, strongest = topics.get("weakest"), topics.get("strongest")
    if not basis.get("topics_retrieved", True):
        # First: a failed read also yields `[]`, i.e. "no topic attempted".
        out.append("The topic figures could not be read, so how this student is "
                   "doing on each topic is not described here.")
    elif weakest and strongest and weakest.get("topic_name") != strongest.get("topic_name"):
        out.append(
            f"{_topic_prose(strongest.get('topic_name'), True)} is the strongest attempted "
            f"topic at {strongest.get('accuracy')}%, and "
            f"{_topic_prose(weakest.get('topic_name'))} the weakest at "
            f"{weakest.get('accuracy')}%.")
    elif weakest and (topics.get("scored_count") or 0) > 1:
        # One topic from both ends means every scored topic ties.
        out.append(f"All {topics['scored_count']} attempted topics are at "
                   f"{weakest.get('accuracy')}%, so none stands out as strongest or weakest.")
    elif weakest:
        out.append(f"Only {_topic_prose(weakest.get('topic_name'))} has been attempted "
                   f"so far, at {weakest.get('accuracy')}%.")
    else:
        out.append("No topic has been attempted yet, so the topic chart has "
                   "nothing to compare.")
    # Not truncated: bounded by construction, and a slice would drop the topic line.
    return out, sensor_lines


def _chart_summary_prompt(basis: dict, baseline: list[str]) -> str:
    """Prompt built from the deterministic sentences only, to rephrase, not interpret.

    That is what makes the numeric check possible. No student id or name.
    """
    return (
        "You are rewriting a summary of a maths practice report for the adult "
        "who is reading it -- a parent or a teacher.\n"
        "These are classroom learning indicators, not medical measurements. Do "
        "not diagnose, do not name any condition, and do not give medical "
        "advice.\n"
        "Rewrite the numbered points below in plainer, warmer English, one "
        "sentence each, keeping the same order and the same meaning.\n"
        "Do not add any number, percentage or figure that is not already in "
        "these points, do not move a number from one point to another, and do "
        "not draw a conclusion the points do not state. Never say or imply why a "
        "sensor was off or a reading is missing (for example that it broke or "
        "stopped working): keep the points' own words for it.\n"
        "Calm and body arousal are different measurements from different sensors: never "
        "combine them, and never call calm \"stress\".\n"
        "Body arousal is a share of heart readings, not of time: never describe it as how "
        "often something happened or as a share of the time or of the lesson.\n"
        f"Return exactly {len(baseline)} points as a numbered list, no preamble.\n\n"
        + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(baseline))
    )


_STRESS_WORD = re.compile(r"\bstress", re.IGNORECASE)
_SENSOR_WORDS = re.compile(r"\b(focus|calm|heart|arousal)", re.IGNORECASE)
# Body arousal counts readings; time words turn it back into the time-share it is not.
_TIME_SHARE = re.compile(r"\b(how often|often|of the (time|lesson)|time spent|spent)\b", re.IGNORECASE)


def _validated_chart_summary(raw: str, allowed: set[float], expected_lines: int,
                             baseline: list[str] | None = None) -> list[str] | None:
    """Model output, or None if it fails any check (caller keeps the rules).

    Every numeral must be one we supplied, and "stress" only where that point said it.
    Residual risk: a reply that swaps two allowed numbers between measurements still passes.
    """
    if _CLINICAL_TERMS.search(raw or ""):
        return None
    if _names_a_cause(raw or ""):
        print("[chart_summary:llm] rejected: it names a cause for a missing reading")
        return None
    lines = _parse_strategy_lines(raw)
    # Exactly the baseline's length: fewer means a point was silently dropped.
    if len(lines) != expected_lines:
        return None
    if any(not _CHART_SUMMARY_MIN_CHARS <= len(line) <= _CHART_SUMMARY_MAX_CHARS
           for line in lines):
        return None
    # A model relabelling calm as stress is the conflation CLAUDE.md forbids.
    if baseline is not None and any(_STRESS_WORD.search(line) and not _STRESS_WORD.search(base)
                                    for line, base in zip(lines, baseline)):
        print("[chart_summary:llm] rejected: it says stress where the point did not")
        return None
    if baseline is not None and any(
            "arousal" in line.lower() and _TIME_SHARE.search(line) and not _TIME_SHARE.search(base)
            for line, base in zip(lines, baseline)):
        print("[chart_summary:llm] rejected: it describes body arousal as a share of time")
        return None
    # Each point keeps its own numbers and stays a sensor point or not, so `sensor_lines` holds.
    if baseline is not None and any(
            not set(_numerals(line)) <= set(_numerals(base))
            or bool(_SENSOR_WORDS.search(line)) != bool(_SENSOR_WORDS.search(base))
            for line, base in zip(lines, baseline)):
        print("[chart_summary:llm] rejected: a point moved or took another point's figures")
        return None
    # Parsed lines, not raw: the list markers are numerals too.
    for line in lines:
        for number in _numerals(line):
            if number not in allowed:
                print(f"[chart_summary:llm] rejected: {number} is not a figure we supplied")
                return None
    return lines


def _llm_chart_summary(prompt: str, baseline: list[str],
                       timeout: float | None = None) -> list[str] | None:
    """One model attempt, or None on any failure. See `_llm_strategies`.

    Temperature 0.2: a rephrasing of fixed content wants no variety.
    """
    try:
        raw = llm_client.generate_text(
            prompt,
            ollama_model=CHART_SUMMARY_LLM_MODEL,
            temperature=0.2, top_p=None, top_k=None,
            claude_temperature=0.2,
            max_tokens=1024,
            timeout=CHART_SUMMARY_LLM_TIMEOUT if timeout is None else timeout,
        )
    except Exception as e:
        print(f"[chart_summary:llm] {e}")
        return None
    return _validated_chart_summary(raw or "", _chart_summary_figures(baseline),
                                    len(baseline), baseline)


def _llm_chart_summary_bounded(prompt: str, baseline: list[str]) -> list[str] | None:
    """`_llm_chart_summary` under a deadline, if admitted (non-blocking acquire)."""
    if not _chart_summary_waiters.acquire(blocking=False):
        print(f"[chart_summary:llm] at capacity "
              f"({_CHART_SUMMARY_MAX_WAITERS} in flight); using the rule-based summary")
        return None
    try:
        return _llm_chart_summary_admitted(prompt, baseline)
    finally:
        _chart_summary_waiters.release()


def _llm_chart_summary_admitted(prompt: str, baseline: list[str]) -> list[str] | None:
    """The wait itself; one deadline shared by queueing and work, as `_llm_strategies_admitted`."""
    deadline = time.monotonic() + CHART_SUMMARY_LLM_TIMEOUT

    def _run():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        return _llm_chart_summary(prompt, baseline, remaining)

    future = None
    try:
        future = _chart_summary_pool().submit(_run)
        return future.result(timeout=CHART_SUMMARY_LLM_TIMEOUT)
    except FutureTimeoutError:
        # Cancel a still-queued task, or an outage piles up abandoned prompts.
        future.cancel()
        print(f"[chart_summary:llm] abandoned after {CHART_SUMMARY_LLM_TIMEOUT}s")
        return None
    except Exception as e:
        print(f"[chart_summary:llm] {e}")
        return None


class ChartSummaryRequest(StrictModel):
    include_face: bool = True
    # Clamped in the handler, not bounded here.
    days:  int = 7
    weeks: int = 8


@app.post("/api/students/{student_id}/chart-summary")
def student_chart_summary(student_id: str, request: Request, payload: ChartSummaryRequest):
    """Plain sentences describing what this student's report charts show.

    Always answers; `source` says whether the model rephrased it.
    Access check before the rate limit, so a 403 is never masked by a 429.
    """
    viewer = get_user(request)
    _verify_can_view_student(viewer, student_id)
    _rate_limit_chart_summary(viewer["id"])

    days = max(1, min(payload.days, 30))
    weeks = max(2, min(payload.weeks, _TREND_MAX_WEEKS))
    basis = _chart_summary_basis(student_id, days, weeks, payload.include_face)

    summary, sensor_lines = _chart_summary_lines(basis)
    source = "rule-based"

    if _feature_flags()["chart_summary_llm_enabled"]["enabled"]:
        refined = _llm_chart_summary_bounded(_chart_summary_prompt(basis, summary), summary)
        if refined:
            summary, source = refined, "model-phrased"
        else:
            source = "rule-based (model output rejected)"

    return {
        "student_id": student_id,
        "generated_at": _utc_now().isoformat(),
        "summary": summary,
        "source": source,
        "basis": {
            "days": days,
            "weeks": weeks,
            "face_included": basis["face_included"],
            # One flag per read, so a partial failure is reported as partial.
            "signals_retrieved": basis["signals_retrieved"],
            "trend_retrieved": basis["trend_retrieved"],
            "stats_retrieved": basis["stats_retrieved"],
            "topics_retrieved": basis["topics_retrieved"],
            "consent_retrieved": basis["consent_retrieved"],
            "averages": basis["averages"],
            "trend": basis["trend"],
            "usual": basis.get("usual"),
            "academic": basis["academic"],
            "topics": basis["topics"],
            # Indexes into `summary` of the sensor sentences, for "Hide sensor data".
            "sensor_lines": sensor_lines,
        },
    }


# ─── leaderboard ─────────────────────────────────────────────────────────

_LEADERBOARD_MAX = 100
# Ids per `in_` read: each is ~37 bytes of URL, and a school's roster would pass URL limits.
_LEADERBOARD_ID_CHUNK = 200


def _chunks(ids: list[str], size: int = _LEADERBOARD_ID_CHUNK):
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


def _class_members(class_ids: list[str]) -> set[str]:
    """Every student in these classes, paged until a read comes back empty (db-max-rows cuts silently)."""
    members: set[str] = set()
    for chunk in _chunks(class_ids):
        last = None
        while True:
            query = supabase.table("class_memberships").select("student_id").in_("class_id", chunk)
            if last is not None:
                # By key, not offset: a row deleted mid-read cannot shift a student past the page.
                query = query.gt("student_id", last)
            page = query.order("student_id").limit(1000).execute().data or []
            if not page:
                break
            members |= {m["student_id"] for m in page}
            last = page[-1]["student_id"]
    return members


def _leaderboard_peers(user_id: str) -> set[str]:
    """The students the caller is ranked among: every student in a class they're in or teach.

    A student is always among them. Names never leave the caller's own classes, so no school
    sees another's children. Raises on a failed read: a partial class is not a smaller one.
    """
    # Not `_role`, which answers "student" on a failed read and so shows a teacher an empty board.
    if _role_or_raise(user_id) == "teacher":
        classes = supabase.table("classes").select("id").eq("teacher_id", user_id).execute()
        class_ids = [c["id"] for c in classes.data or []]
        peers: set[str] = set()
    else:
        mine = supabase.table("class_memberships").select("class_id") \
            .eq("student_id", user_id).execute()
        class_ids = [m["class_id"] for m in mine.data or []]
        peers = {user_id}
    return peers | _class_members(class_ids)


def _leaderboard_rows(peers: set[str], top: int) -> list[dict]:
    """The top `top` stats rows among `peers`; raises on a failed read."""
    rows = []
    # Each chunk's own top N, merged: the board's top N is among them.
    for chunk in _chunks(sorted(peers)):
        rows += supabase.table("user_stats") \
            .select("user_id, total_correct, total_questions, current_streak, best_streak, "
                    "last_session_at") \
            .in_("user_id", chunk) \
            .order("total_correct", desc=True).limit(top).execute().data or []
    return sorted(rows, key=lambda r: r.get("total_correct") or 0, reverse=True)[:top]


@app.get("/api/leaderboard")
def leaderboard(request: Request, limit: int = 20):
    """Top students by correct answers, among the caller's classes only.

    Service-role read, so `limit` must stay clamped to _LEADERBOARD_MAX.
    user_id is never returned; only `is_me`. Any failed read is a 503, never a smaller board.
    """
    user = get_user(request)
    try:
        peers = _leaderboard_peers(user["id"])
        rows = _leaderboard_rows(peers, max(1, min(limit, _LEADERBOARD_MAX))) if peers else []
    except Exception as e:
        print(f"[leaderboard] unreadable for {user['id'][:8]}: {type(e).__name__}")
        raise HTTPException(503, "The leaderboard could not be loaded right now")
    profiles = _profiles_many(r.get("user_id") for r in rows)
    tz = _school_timezone() if rows else None
    enriched = []
    for i, row in enumerate(rows):
        row = _live_streak(row, tz)
        row.pop("last_session_at", None)
        uid = row.pop("user_id", None)
        p = profiles.get(uid) or {}
        enriched.append({
            **row,
            "display_name": p.get("display_name") or "Student",
            "rank": i + 1,
            "is_me": uid == user["id"],
        })
    return enriched


# ─── classes ────────────────────────────────────────────────────────────

@app.post("/api/classes")
def create_class(payload: CreateClassRequest, request: Request):
    user = get_user(request)
    if _role(user["id"]) != "teacher":
        raise HTTPException(403, "Only teachers can create classes")
    res = supabase.table("classes").insert({
        "teacher_id":  user["id"],
        "name":        payload.name,
        "grade_level": payload.grade_level,
        "join_code":   _unused_join_code(),
    }, returning=ReturnMethod.representation).execute()
    return res.data[0]


def _unused_join_code() -> str:
    """A new code no class holds, retried a few times; a collision in 32^8 is vanishingly rare."""
    code = _new_join_code()
    for _ in range(5):
        if not supabase.table("classes").select("id").eq("join_code", code).execute().data:
            break
        code = _new_join_code()
    return code


@app.post("/api/classes/{class_id}/join-code")
def replace_join_code(class_id: str, request: Request):
    """A new 8-character code for the owning teacher; the old one stops joining, and members stay."""
    user = get_user(request)
    _verify_class_owner(class_id, user["id"])
    code = _unused_join_code()
    supabase.table("classes").update({"join_code": code}).eq("id", class_id).execute()
    return {"id": class_id, "join_code": code}

# Registered before `/api/classes/{class_id}`, or that route binds "summary".
@app.get("/api/classes/summary")
def class_summaries(request: Request):
    """Per-class headline averages for the teacher's dashboard, in three reads.

    Accuracy averages over students who attempted something (`None` if none);
    streak over the whole roster (`None` if empty). `retrieved` rides on each class, and
    an unretrieved class carries no averages: its zeros would be placeholders.
    """
    user = get_user(request)
    classes = supabase.table("classes").select("id") \
        .eq("teacher_id", user["id"]).execute().data or []
    ids = _unique_ids(c["id"] for c in classes)
    if not ids:
        return {}

    try:
        members = supabase.table("class_memberships").select("class_id, student_id") \
            .in_("class_id", ids).execute().data or []
    except Exception as e:                                     # noqa: BLE001
        print(f"[classes] could not read memberships for {len(ids)} classes: {e}")
        return {cid: {"avgAccuracy": None, "avgStreak": None, "retrieved": False}
                for cid in ids}

    by_class: dict[str, list] = {}
    for m in members:
        by_class.setdefault(m.get("class_id"), []).append(m.get("student_id"))

    stats = _stats_including_open_session_many(
        _unique_ids(m.get("student_id") for m in members))

    out = {}
    for cid in ids:
        roster = [stats.get(sid) or {} for sid in by_class.get(cid, [])]
        # One unretrieved student makes the class average unretrieved.
        retrieved = all(s.get("retrieved", False) for s in roster) if roster else True
        if not retrieved:
            out[cid] = {"avgAccuracy": None, "avgStreak": None, "retrieved": False}
            continue
        attempted = [s for s in roster if (s.get("total_questions") or 0) > 0]
        avg_accuracy = round(sum(
            (s.get("total_correct") or 0) / s["total_questions"] * 100
            for s in attempted) / len(attempted)) if attempted else None
        avg_streak = round(sum(s.get("current_streak") or 0
                               for s in roster) / len(roster)) if roster else None
        out[cid] = {"avgAccuracy": avg_accuracy, "avgStreak": avg_streak,
                    "retrieved": retrieved}
    return out


@app.get("/api/classes/{class_id}")
def get_class(class_id: str, request: Request):
    """One class, for the pages that need its name and join code. Owner-only."""
    user = get_user(request)
    _verify_class_owner(class_id, user["id"])
    # Named columns, so a new column doesn't reach the browser by existing.
    return _row_or_404(
        supabase.table("classes").select("id, name, join_code, grade_level")
                .eq("id", class_id),
        "Class")

@app.put("/api/classes/{class_id}")
def update_class(class_id: str, payload: UpdateClassRequest, request: Request):
    user = get_user(request)
    _verify_class_owner(class_id, user["id"])
    # Named columns, never the payload: `teacher_id`/`join_code` must stay unwritable.
    fields = {
        name: value for name, value in (
            ("name", payload.name),
            ("grade_level", payload.grade_level),
        ) if value is not None
    }
    if fields:
        supabase.table("classes").update(fields).eq("id", class_id).execute()
    # Re-read what was stored; 404 if deleted in between.
    return _row_or_404(
        supabase.table("classes").select("*").eq("id", class_id), "Class")

@app.get("/api/classes")
def my_classes(request: Request):
    user = get_user(request)
    # Not `_role`: a failed read would send a teacher down the student branch and show no classes.
    try:
        role = _role_or_raise(user["id"])
    except Exception as e:
        print(f"[my_classes] role unreadable for {user['id'][:8]}: {type(e).__name__}")
        raise HTTPException(503, "Your classes could not be loaded right now.")
    if role == "teacher":
        res = supabase.table("classes").select("*, class_memberships(count)").eq("teacher_id", user["id"]).execute()
    else:
        memberships = supabase.table("class_memberships").select("class_id").eq("student_id", user["id"]).execute()
        ids = [m["class_id"] for m in (memberships.data or [])]
        if not ids:
            return []
        # What `join_class` returns: the teacher's id and the class code are the teacher's.
        res = supabase.table("classes").select("id, name, grade_level").in_("id", ids).execute()
    return res.data or []

@app.post("/api/classes/join")
def join_class(payload: JoinClassRequest, request: Request):
    user = get_user(request)
    # Before the lookup: a refused guess must not learn whether the code exists.
    wait = _JOIN_CODE_LIMITER.check(user["id"])
    if wait is not None:
        _record_security_event("rate_limited", user["id"], limiter=_JOIN_CODE_LIMITER.name)
        raise HTTPException(429, "Too many attempts. Check the code with your teacher and "
                                 "try again later.", headers={"Retry-After": str(wait)})
    # Not `_role`: it answers "student" on a failed read or a missing row, admitting anyone here.
    try:
        role = _role_or_raise(user["id"])
    except Exception as e:
        print(f"[join_class] role unreadable for {user['id'][:8]}: {type(e).__name__}")
        raise HTTPException(503, "Joining a class is not available right now. Try again soon.")
    if role != "student":
        raise HTTPException(403, "Only students can join a class")
    address = _client_address(request)
    wait = _JOIN_MISS_LIMITER.check(address)
    if wait is not None:
        # The limiter's name, never the address: an address is personal data.
        _record_security_event("rate_limited", user["id"], limiter=_JOIN_MISS_LIMITER.name)
        raise HTTPException(429, "Too many wrong class codes from this network. Try again later.",
                            headers={"Retry-After": str(wait)})
    # Named columns, as `my_classes` sends a student: `teacher_id` and `join_code` are the teacher's.
    cls  = supabase.table("classes").select("id, name, grade_level") \
        .eq("join_code", payload.join_code.strip().upper()).execute()
    if not cls.data:
        raise HTTPException(404, "Class not found -- check the code")
    # A right code is not a guess, so only misses stay charged to the address.
    _JOIN_MISS_LIMITER.release(address)
    class_id = cls.data[0]["id"]
    already = supabase.table("class_memberships").select("id") \
        .eq("class_id", class_id).eq("student_id", user["id"]).execute()
    if already.data:
        raise HTTPException(409, "Already in this class")
    supabase.table("class_memberships").insert({
        "class_id":  class_id,
        "student_id": user["id"],
    }).execute()
    return cls.data[0]

def _verify_class_owner(class_id: str, user_id: str):
    """Only the owning teacher. Service-role reads bypass RLS: this is the whole check."""
    cls = _row_or_404(
        supabase.table("classes").select("teacher_id").eq("id", class_id), "Class")
    if cls["teacher_id"] != user_id:
        # Audited here, not per endpoint.
        _record_security_event("authz_denied", user_id,
                               check="class_owner", class_id=class_id)
        raise HTTPException(403, "Not your class")


def _can_view_student(viewer: dict, student_id: str) -> bool | None:
    """Self, a teacher of their class, a linked parent, or an admin; None if a check could not be read.

    Service-role reads bypass RLS: this check is the enforcement.
    """
    uid = viewer["id"]
    if uid == student_id:
        return True
    # Teacher of their class, linked parent or admin, in one statement (20261003000000).
    # A non-uuid on either side answers null, never an error, so it is a denial like any other.
    try:
        relationship = supabase.rpc("viewer_relationship", {
            "p_viewer": uid, "p_student": student_id}).execute().data
    except Exception as e:                                     # noqa: BLE001
        if not _missing_rpc(e, "viewer_relationship", "20261003000000",
                            "every teacher, parent and admin view of a student is a 503 until then"):
            print(f"[can_view_student] {e}")
        return None
    # Only a relationship it names admits; anything else, null included, is a denial.
    return relationship in ("teacher", "parent", "admin")


def _verify_can_view_student(viewer: dict, student_id: str):
    allowed = _can_view_student(viewer, student_id)
    if allowed is None:
        # Still refused, but a failed read is not a denial, so it stays out of the security log.
        raise _read_failed("Could not check access to this student; try again")
    if not allowed:
        # The subject id answers "who tried to read this child's record".
        _record_security_event("authz_denied", viewer.get("id"), student_id,
                               check="can_view_student")
        raise HTTPException(403, "You do not have access to this student")


@app.get("/api/classes/{class_id}/students")
def class_students(class_id: str, request: Request):
    user = get_user(request)
    _verify_class_owner(class_id, user["id"])
    try:
        # Names ride on the roster read, so a failed one fails the request rather than becoming "Student".
        memberships = supabase.table("class_memberships") \
            .select("student_id, joined_at, profiles(display_name, email)") \
            .eq("class_id", class_id).execute()
    except Exception as e:                                     # noqa: BLE001
        print(f"[class_students] {e}")
        raise _read_failed("Could not load this class's students; try again")
    students = []
    roster = [m["student_id"] for m in (memberships.data or [])]
    all_stats = _stats_including_open_session_many(roster)
    last_active = _last_active_many(roster)
    for m in (memberships.data or []):
        sid = m["student_id"]
        stats = all_stats.get(sid) or {}
        p = m.get("profiles") or {}
        students.append({
            "user_id":   sid,
            # None when unset: the page chooses what to draw, as on `/api/teacher/students`.
            "name":      p.get("display_name") or None,
            "email":     p.get("email") or None,
            "joined_at": m["joined_at"],
            # Timestamp, None (never active), or `last_active_retrieved: False`.
            **last_active.get(sid, _LAST_ACTIVE_UNKNOWN),
            **stats,
        })
    return students


_ROSTER_PAGE = 1000
# Pages a roster read may take with no count; with one, the pages it needs plus the spare.
_ROSTER_MAX_PAGES = 200
_ROSTER_SPARE_PAGES = 2


def _teacher_memberships(uid: str) -> list[dict]:
    """Every membership in a class `uid` teaches, with the student's profile; a failed read raises.

    Paged by `id` (db-max-rows cuts silently) until the first page's exact count is reached, or a
    page comes back empty; a cursor that stops advancing, or pages past the bound, raise.
    """
    rows, last, total, bound, pages = [], None, None, _ROSTER_MAX_PAGES, 0
    while True:
        # `!inner` drops other teachers' rows, from the count too; without it they arrive as `classes: null`.
        query = supabase.table("class_memberships") \
            .select("id, student_id, joined_at, classes!inner(teacher_id), profiles(display_name, email)",
                    count="exact" if last is None else None) \
            .eq("classes.teacher_id", uid)
        if last is not None:
            # A unique key, so a student's rows split across two pages are all read.
            query = query.gt("id", last)
        res = query.order("id").limit(_ROSTER_PAGE).execute()
        page, pages = res.data or [], pages + 1
        if not page:
            return rows
        if last is None and res.count is not None:
            # Only the first page's count is the whole roster; a later one counts only past the cursor.
            total, bound = res.count, -(-res.count // len(page)) + _ROSTER_SPARE_PAGES
        # uuid text sorts as Postgres sorts uuids.
        if last is not None and not page[-1]["id"] > last:
            raise RuntimeError("roster cursor did not advance")
        rows += page
        last = page[-1]["id"]
        if total is not None and len(rows) >= total:
            return rows
        if pages >= bound:
            raise RuntimeError(f"roster read passed {bound} pages")


@app.get("/api/teacher/students")
def teacher_students(request: Request):
    """Each student in any class the caller teaches, once, with their earliest join."""
    uid = get_user(request)["id"]
    try:
        memberships = _teacher_memberships(uid)
    except Exception as e:                                     # noqa: BLE001
        print(f"[teacher_students] {e}")
        # 503, so the Students page keeps the load error its own failed read showed, never "Student" rows.
        raise _read_failed("Could not load your students; try again")
    def _instant(m):
        at = _parse_ts(m.get("joined_at"))
        return (at is None, at or datetime.min.replace(tzinfo=timezone.utc))

    joined: dict[str, dict] = {}
    # Oldest first by instant, so the first row seen per student is their earliest join.
    for m in sorted(memberships, key=_instant):
        if m.get("student_id"):
            joined.setdefault(m["student_id"], m)
    rows = [{"user_id": sid,
             "name": (m.get("profiles") or {}).get("display_name") or None,
             "email": (m.get("profiles") or {}).get("email") or None,
             "joined_at": m.get("joined_at")} for sid, m in joined.items()]
    return sorted(rows, key=lambda r: (r["name"] is None, (r["name"] or "").casefold(), r["user_id"]))


# ─── teacher analytics ────────────────────────────────────────────────────
# All: `_verify_class_owner` before any read; `retrieved` on every payload;
# school-timezone buckets; aggregation in Postgres. See docs/reporting.md "teacher analytics".

# Below this, a topic's accuracy is returned but marked thin.
_MIN_TOPIC_ATTEMPTS = 4

# Default and ceiling for the class-level series window, in days.
_CLASS_TREND_DEFAULT_DAYS = 30
_CLASS_TREND_MAX_DAYS = 180

# Below this many pairs no correlation is shown: r over a handful is noise.
_FOCUS_MIN_PAIRS = 30
_FOCUS_BUCKETS = 5
# Max gap between an answer and its focus reading, in seconds (poller ~1 Hz).
_FOCUS_MATCH_SECONDS = 30

_LAST_ACTIVE_UNKNOWN = {"last_active": None, "last_active_retrieved": False}

# Alert feed window (days) and row cap; the payload says when the cap bites.
_ALERT_FEED_DAYS = 7
_ALERT_FEED_CAP = 200


def _last_active_many(student_ids) -> dict[str, dict]:
    """When each student was last doing anything, for a whole roster.

    An RPC, since "newest row per student" has no PostgREST form. Never active
    is `last_active: None` with `last_active_retrieved: True`.
    """
    ids = _unique_ids(student_ids)
    if not ids:
        return {}
    try:
        rows = supabase.rpc("last_active_for_users",
                            {"p_user_ids": ids}).execute().data or []
    except Exception as e:                                     # noqa: BLE001
        if not _missing_rpc(e, "last_active_for_users", "20260831000000",
                            "every roster shows last active as unknown until then"):
            print(f"[last_active] could not read for {len(ids)}: {e}")
        return {sid: dict(_LAST_ACTIVE_UNKNOWN) for sid in ids}
    found = {r.get("user_id"): r.get("last_active") for r in rows}
    return {sid: {"last_active": found.get(sid), "last_active_retrieved": True}
            for sid in ids}


def _class_roster(class_id: str) -> tuple[list[str], bool]:
    """The student ids in a class, in join order, and whether the read worked.

    Callers fold the flag into their `retrieved`, so a failed read is not an empty class.
    """
    try:
        rows = supabase.table("class_memberships").select("student_id") \
            .eq("class_id", class_id).execute().data or []
    except Exception as e:                                     # noqa: BLE001
        print(f"[class_analytics] could not read the roster for {class_id}: {e}")
        return [], False
    return _unique_ids(r.get("student_id") for r in rows), True


def _class_topic_heatmap(class_id: str) -> dict:
    """Per-student, per-topic accuracy for a class, from `user_math_performance`.

    Cells are a list aligned to `topics`, built in one pass so they cannot drift.
    An untouched topic is `null`, never 0.
    """
    roster, roster_retrieved = _class_roster(class_id)
    profiles = _profiles_many(roster)
    rows, perf_retrieved = _topic_performance_rows(roster)
    retrieved = roster_retrieved and perf_retrieved
    by_student = _group_by_user(rows)

    # Topics from the rows, not `math_topics`: only what this class was served.
    topic_names: dict[int, str] = {}
    for r in rows:
        tid = r.get("topic_id")
        if tid is None:
            continue
        joined = r.get("math_topics") or {}
        topic_names.setdefault(tid, joined.get("topic_name") or f"Topic {tid}")
    topic_ids = sorted(topic_names, key=lambda t: topic_names[t])

    def _cell(perf: dict | None) -> dict | None:
        if not perf:
            return None
        attempted = perf.get("attempted_questions") or 0
        if not attempted:
            return None
        correct = perf.get("correct_questions") or 0
        return {"attempted": attempted, "correct": correct,
                "accuracy": round(correct / attempted, 4)}

    students = []
    for sid in roster:
        perf_by_topic = {p.get("topic_id"): p for p in by_student.get(sid, [])}
        cells = [_cell(perf_by_topic.get(tid)) for tid in topic_ids]
        attempted = sum(c["attempted"] for c in cells if c)
        correct = sum(c["correct"] for c in cells if c)
        students.append({
            "user_id": sid,
            "name": (profiles.get(sid) or {}).get("display_name") or "Student",
            "cells": cells,
            "attempted": attempted,
            "accuracy": round(correct / attempted, 4) if attempted else None,
        })

    topics = []
    for i, tid in enumerate(topic_ids):
        attempted = sum(s["cells"][i]["attempted"] for s in students if s["cells"][i])
        correct = sum(s["cells"][i]["correct"] for s in students if s["cells"][i])
        topics.append({
            "topic_id": tid,
            "topic_name": topic_names[tid],
            "attempted": attempted,
            "correct": correct,
            "accuracy": round(correct / attempted, 4) if attempted else None,
        })

    return {"topics": topics, "students": students,
            "min_attempts": _MIN_TOPIC_ATTEMPTS, "retrieved": retrieved}


def _answer_buckets(roster: list[str], days: int,
                    tz: tzinfo) -> tuple[list, bool, date]:
    """Answers per school day and hour for a roster, from the RPC.

    Returns (rows, retrieved, first school day of the range). Callers bucket from
    that day rather than re-reading the clock, which could cross local midnight.
    """
    school_today = _utc_now().astimezone(tz).date()
    start = school_today - timedelta(days=days - 1)
    if not roster:
        return [], True, start
    # Half-open, ending at tomorrow's local midnight so today is in range.
    # `datetime.min.time()`: module-level `time` is the stdlib module, not datetime.time.
    midnight = datetime.min.time()
    try:
        rows = supabase.rpc("class_answer_buckets", {
            "p_user_ids": roster,
            "p_from": datetime.combine(start, midnight, tzinfo=tz).isoformat(),
            "p_to": datetime.combine(school_today + timedelta(days=1),
                                     midnight, tzinfo=tz).isoformat(),
            "p_timezone": str(tz),
        }).execute().data or []
    except Exception as e:                                     # noqa: BLE001
        print(f"[class_analytics] answer buckets failed for {len(roster)}: {e}")
        return [], False, start
    return rows, True, start


def _class_accuracy_trend(class_id: str, days: int) -> dict:
    """Class accuracy per school day.

    Every day in the range appears, so a gap renders as a gap; `accuracy` is
    null on a day nobody answered, never 0.
    """
    tz = _school_timezone()
    roster, roster_retrieved = _class_roster(class_id)
    rows, buckets_retrieved, start = _answer_buckets(roster, days, tz)
    retrieved = roster_retrieved and buckets_retrieved

    buckets = {start + timedelta(days=i): [0, 0] for i in range(days)}
    for r in rows:
        try:
            day = date.fromisoformat(str(r.get("day")))
        except (TypeError, ValueError):
            continue
        b = buckets.get(day)
        if b is None:
            continue
        b[0] += r.get("attempted") or 0
        b[1] += r.get("correct") or 0

    series = [{"day": day.isoformat(), "attempted": a, "correct": c,
               "accuracy": round(c / a, 4) if a else None}
              for day, (a, c) in sorted(buckets.items())]
    answered = sum(d["attempted"] for d in series)
    correct = sum(d["correct"] for d in series)
    return {
        "days": series,
        "attempted": answered,
        "correct": correct,
        "accuracy": round(correct / answered, 4) if answered else None,
        "days_with_data": sum(1 for d in series if d["attempted"]),
        "student_count": len(roster),
        "timezone": str(tz),
        "retrieved": retrieved,
    }


def _class_time_of_day(class_id: str, days: int) -> dict:
    """When in the week a class actually works, as weekday x hour.

    Weekday comes from the RPC's school-local date; no second tz conversion.
    Only hours the class has worked in are emitted.
    """
    tz = _school_timezone()
    roster, roster_retrieved = _class_roster(class_id)
    rows, buckets_retrieved, _start = _answer_buckets(roster, days, tz)
    retrieved = roster_retrieved and buckets_retrieved

    grid: dict[tuple[int, int], list[int]] = {}
    for r in rows:
        try:
            day = date.fromisoformat(str(r.get("day")))
            hour = int(r.get("hour"))
        except (TypeError, ValueError):
            continue
        if not 0 <= hour <= 23:
            continue
        cell = grid.setdefault((day.weekday(), hour), [0, 0])
        cell[0] += r.get("attempted") or 0
        cell[1] += r.get("correct") or 0

    cells = [{"weekday": wd, "hour": hour, "attempted": a, "correct": c,
              "accuracy": round(c / a, 4) if a else None}
             for (wd, hour), (a, c) in sorted(grid.items())]
    return {
        "cells": cells,
        "hours": sorted({c["hour"] for c in cells}),
        "attempted": sum(c["attempted"] for c in cells),
        "days": days,
        "student_count": len(roster),
        "timezone": str(tz),
        "retrieved": retrieved,
    }


def _focus_accuracy(student_id: str, days: int) -> dict:
    """Whether this student answers better when the headband reads focused.

    The caller gates on EEG consent by skipping this read. `correlation` is
    withheld below `_FOCUS_MIN_PAIRS`; the buckets are still returned.
    """
    since = (_utc_now() - timedelta(days=days)).isoformat()
    try:
        data = supabase.rpc("focus_accuracy_for_user", {
            "p_user_id": student_id,
            "p_from": since,
            "p_bucket_count": _FOCUS_BUCKETS,
            "p_match_seconds": _FOCUS_MATCH_SECONDS,
        }).execute().data or {}
    except Exception as e:                                     # noqa: BLE001
        print(f"[focus_accuracy] {student_id}: {e}")
        return _focus_accuracy_payload(None, days, retrieved=False)
    return _focus_accuracy_payload(data, days, retrieved=True)


def _focus_accuracy_payload(data: dict | None, days: int, retrieved: bool,
                            eeg_enabled: bool = True,
                            eeg_revoked_at: str | None = None,
                            consent_retrieved: bool = True) -> dict:
    """One shape for every outcome, so no caller sees a field only sometimes."""
    data = data or {}
    pairs = data.get("n") or 0
    r = data.get("r")
    buckets = [
        {"focus_low": b.get("focus_low"), "focus_high": b.get("focus_high"),
         "answered": b.get("answered") or 0, "correct": b.get("correct") or 0,
         "accuracy": round((b.get("correct") or 0) / b["answered"], 4)
         if b.get("answered") else None}
        for b in (data.get("buckets") or [])
    ]
    sufficient = pairs >= _FOCUS_MIN_PAIRS
    return {
        "correlation": round(float(r), 4)
        if sufficient and isinstance(r, (int, float)) else None,
        "pairs": pairs,
        "sufficient": sufficient,
        "min_pairs": _FOCUS_MIN_PAIRS,
        "buckets": buckets,
        "days": days,
        "retrieved": retrieved,
        "eeg_enabled": eeg_enabled,
        "eeg_revoked_at": eeg_revoked_at,
        "consent_retrieved": consent_retrieved,
    }


def _clamp_days(days: int) -> int:
    return max(1, min(days, _CLASS_TREND_MAX_DAYS))


# Roster floor for the per-student table; enforced server-side so the rows never leave.
_COHORT_MIN_STUDENTS = 5


def _class_signal_totals(student_ids: list[str], days: int,
                         include_heart: bool, include_emotion: bool) -> dict | None:
    """Per-student averages over the window, keyed by student, or None on failure.

    Reads the rollup, never the per-sample tables, to match the trend beside it
    after `expire_signal_rows` has run.
    """
    if not student_ids:
        return {}
    try:
        res = supabase.rpc("class_signal_student_totals", {
            "p_student_ids": student_ids,
            "p_days": days,
            "p_include_heart": include_heart,
            "p_include_emotion": include_emotion,
            "p_timezone": _school_timezone_name(),
        }).execute()
    except Exception as e:                                     # noqa: BLE001
        print(f"[cohort_signals] per-student read failed: {e}")
        return None
    rows = res.data or []
    if isinstance(rows, dict):
        rows = [rows]
    return {str(r["user_id"]): r for r in rows if r.get("user_id")}


def _cohort_student_row(sid: str, totals: dict | None, channels: ReportChannels,
                        retrieved: bool, since: datetime) -> dict:
    """One roster row: the student's averages, and why any of them is missing.

    Consent fields are stamped per student: the RPC cannot return revocation dates.
    """
    t = totals or {}
    return {
        "focus": t.get("avg_focus"),
        "stress": t.get("avg_stress"),
        "engagement": t.get("avg_focus"),  # see `_shape_summary`
        "heart_rate_bpm": t.get("avg_heart_rate_bpm"),
        "rmssd_ms": t.get("avg_rmssd_ms"),
        # Null average + zero count: nothing recorded; + nonzero: calibrating.
        "cognitive_samples": t.get("cognitive_samples") or 0,
        "heart_samples": t.get("heart_samples") or 0,
        "emotion_samples": t.get("emotion_samples") or 0,
        # Days, not sessions: `sessions` has a different lifetime from the rollup.
        "days_recorded": t.get("days_recorded") or 0,
        "heart_included": channels.heart,
        "emotion_included": channels.emotion,
        "eeg_enabled": channels.eeg,
        "eeg_revoked_at": channels.eeg_revoked_at,
        "heart_revoked_at": channels.heart_revoked_at,
        "emotion_revoked_at": channels.emotion_revoked_at,
        **_erased_fields(channels, since),
        "consent_retrieved": channels.consent_retrieved,
        "retrieved": retrieved,
    }


def _class_signal_trend(student_ids: list[str], days: int,
                        include_heart: bool, include_emotion: bool) -> list | None:
    """Per-day class averages for one consent bucket; None (not []) if the read failed."""
    if not student_ids:
        return []
    try:
        res = supabase.rpc("class_signal_daily_trend", {
            "p_student_ids": student_ids,
            "p_days": days,
            "p_include_heart": include_heart,
            "p_include_emotion": include_emotion,
            "p_timezone": _school_timezone_name(),
        }).execute()
    except Exception as e:                                     # noqa: BLE001
        print(f"[cohort_signals] trend read failed: {e}")
        return None
    rows = res.data or []
    return [rows] if isinstance(rows, dict) else rows


# Averages the merge re-weights. No `avg_engagement`: it is served from `avg_focus`.
_COHORT_TREND_METRICS = ("avg_focus", "avg_stress",
                         "avg_heart_rate_bpm", "avg_rmssd_ms")


def _merge_cohort_trend(parts: list[list]) -> list:
    """One class series from several consent buckets' series.

    Re-weighted on `trusted_sample_count`, never a mean of means. A metric
    absent from a bucket contributes no weight, not a zero.
    """
    merged: dict[tuple, dict] = {}
    for rows in parts:
        for r in rows:
            key = (r.get("day"), r.get("channel"))
            b = merged.setdefault(key, {
                "day": r.get("day"),
                "channel": r.get("channel"),
                "sums": {k: [0.0, 0] for k in _COHORT_TREND_METRICS},
                "sample_count": 0,
                "trusted_sample_count": 0,
                "student_count": 0,
            })
            n = r.get("trusted_sample_count") or 0
            b["sample_count"] += r.get("sample_count") or 0
            b["trusted_sample_count"] += n
            # Summed: the buckets partition the roster.
            b["student_count"] += r.get("student_count") or 0
            for metric in _COHORT_TREND_METRICS:
                value = r.get(metric)
                weight = _stress_weight(r) if metric == "avg_stress" else n
                if isinstance(value, (int, float)) and weight > 0:
                    b["sums"][metric][0] += float(value) * weight
                    b["sums"][metric][1] += weight

    def _mean(pair):
        total, count = pair
        return round(total / count, 4) if count else None

    return [{
        "day": b["day"],
        "channel": b["channel"],
        **{k: _mean(v) for k, v in b["sums"].items()},
        # From focus, not the stored column -- see `_shape_summary`.
        "avg_engagement": _mean(b["sums"]["avg_focus"]),
        "sample_count": b["sample_count"],
        "trusted_sample_count": b["trusted_sample_count"],
        "student_count": b["student_count"],
    } for _, b in sorted(merged.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))]


def _combine_ranges(ranges) -> dict | None:
    """The widest of several `_scale_range` results, calm sources unioned, or None if none."""
    present = [r for r in ranges if r]
    if not present:
        return None
    return {"min": min(r["min"] for r in present), "max": max(r["max"] for r in present),
            "calm_sources": _calm_sources_union(r.get("calm_sources") for r in present)}


def _scale_ranges_many(user_ids: list[str], days: int) -> dict[str, dict | None]:
    """`{user_id: {"min", "max"} | None}` over each student's cognitive rollup rows.

    Deliberately not consent-bucketed: it selects no reading, only the scale.
    Fails open to {}: it decides a caption, never what is shown.
    """
    if not user_ids:
        return {}
    try:
        today = date.fromisoformat(_school_day(_utc_now(), _school_timezone()))
        since = today - timedelta(days=days - 1)
        rows = (supabase.table("signal_daily_rollup")
                .select("user_id, channel, score_scale_min, score_scale_max, calm_sources")
                .in_("user_id", list(user_ids)).eq("channel", "cognitive")
                .gte("day", since.isoformat()).execute().data or [])
    except Exception as e:                                     # noqa: BLE001
        print(f"[score_scale] rollup read failed: {e}")
        return {}
    by_user: dict[str, list] = {}
    for r in rows:
        by_user.setdefault(str(r.get("user_id")), []).append(r)
    return {str(uid): _scale_range(by_user.get(str(uid), [])) for uid in user_ids}


def _cohort_scale_range(roster: list[str], days: int) -> dict | None:
    """The range across the whole roster; see `_scale_ranges_many`."""
    return _combine_ranges(_scale_ranges_many(roster, days).values())


def _cohort_signals(class_id: str, days: int) -> dict:
    """Class-wide signal averages per day, and the per-student rows behind them.

    The roster is bucketed by consent flag pair (at most four), so nobody is
    read under a classmate's permission.
    """
    roster, roster_retrieved = _class_roster(class_id)
    channels_by_student = _reportable_channels_many(roster)

    # Keyed on the flags alone: `consent_retrieved` doesn't change the query.
    by_channels: dict[tuple[bool, bool], list[str]] = {}
    for sid, ch in channels_by_student.items():
        by_channels.setdefault((ch.heart, ch.emotion), []).append(sid)

    parts: list[list] = []
    summaries: dict | None = {}
    trend_retrieved = roster_retrieved
    for (heart_flag, emotion_flag), group in by_channels.items():
        part = _class_signal_trend(group, days, heart_flag, emotion_flag)
        if part is None:
            # One failed bucket fails the whole trend: one flag can't describe a partial read.
            trend_retrieved = False
            # The roster too: breaking skips later buckets' totals, which would read as zeros.
            summaries = None
            break
        parts.append(part)

        # Fails independently of the trend, so the chart stays standing.
        if summaries is not None:
            got = _class_signal_totals(group, days, heart_flag, emotion_flag)
            if got is None:
                summaries = None
            else:
                summaries.update(got)

    series = _merge_cohort_trend(parts) if trend_retrieved else []
    summaries_retrieved = summaries is not None
    summaries = summaries or {}

    # The floor gates on the roster, not on who recorded something.
    per_student = None
    # One scale read labels both the chart and each roster row.
    scale_by_user = _scale_ranges_many(roster, days) if trend_retrieved else {}
    if len(roster) >= _COHORT_MIN_STUDENTS:
        profiles = _profiles_many(roster)
        since = _window_start(days)
        # Every roster student gets a row, including those with nothing recorded.
        per_student = [{
            "student_id": sid,
            "display_name": (profiles.get(sid) or {}).get("display_name") or "Student",
            "summary": {**_cohort_student_row(sid, summaries.get(sid),
                                              channels_by_student[sid],
                                              summaries_retrieved, since),
                        "score_scale": scale_by_user.get(sid)},
        } for sid in roster]

    return {
        "class_id": class_id,
        "class_size": len(roster),
        "days": days,
        "series": series,
        "retrieved": trend_retrieved,
        # Mixed means the series straddles a scale change -- see `_scale_range`.
        "score_scale": _combine_ranges(scale_by_user.values()) if trend_retrieved else None,
        "summaries_retrieved": summaries_retrieved,
        "per_student": per_student,
        "min_students": _COHORT_MIN_STUDENTS,
        # The zone the days were bucketed in, which a mistyped setting makes UTC.
        "timezone": _school_timezone_name(),
    }


@app.get("/api/classes/{class_id}/cohort-signals")
def class_cohort_signals(class_id: str, request: Request,
                         days: int = _CLASS_TREND_DEFAULT_DAYS):
    """Class-wide signal trend, and the per-student rows behind it. Class owner only."""
    user = get_user(request)
    _verify_class_owner(class_id, user["id"])
    return _cohort_signals(class_id, _clamp_days(days))


@app.get("/api/classes/{class_id}/topic-heatmap")
def class_topic_heatmap(class_id: str, request: Request):
    """Per-student, per-topic accuracy across a class."""
    user = get_user(request)
    _verify_class_owner(class_id, user["id"])
    return _class_topic_heatmap(class_id)


@app.get("/api/classes/{class_id}/accuracy-trend")
def class_accuracy_trend(class_id: str, request: Request,
                         days: int = _CLASS_TREND_DEFAULT_DAYS):
    """Class accuracy per school day, over the last `days`."""
    user = get_user(request)
    _verify_class_owner(class_id, user["id"])
    return _class_accuracy_trend(class_id, _clamp_days(days))


@app.get("/api/classes/{class_id}/time-of-day")
def class_time_of_day(class_id: str, request: Request,
                      days: int = _CLASS_TREND_DEFAULT_DAYS):
    """When in the week a class works, as weekday x hour of the school day."""
    user = get_user(request)
    _verify_class_owner(class_id, user["id"])
    return _class_time_of_day(class_id, _clamp_days(days))


def _class_alerts(class_id: str, days: int) -> dict:
    """Recent operational alerts for a class's roster.

    No acknowledge or dismiss, by decision. Stored per student, not per class,
    so the roster resolves class membership at read time.
    """
    tz = _school_timezone()
    roster, roster_retrieved = _class_roster(class_id)
    profiles = _profiles_many(roster)
    since = (_utc_now() - timedelta(days=days)).isoformat()

    rows, retrieved = [], roster_retrieved
    if roster:
        try:
            rows = (supabase.table("session_alerts")
                    .select("id, user_id, session_id, kind, detail, created_at")
                    .in_("user_id", roster)
                    .gte("created_at", since)
                    .order("created_at", desc=True)
                    .limit(_ALERT_FEED_CAP).execute().data or [])
        except Exception as e:                                 # noqa: BLE001
            print(f"[alerts] could not read the feed for {class_id}: {e}")
            retrieved = False

    alerts = [{
        **r,
        "student_name": (profiles.get(r.get("user_id")) or {}).get("display_name")
        or "Student",
        "school_day": _school_day(r.get("created_at"), tz),
    } for r in rows]

    return {
        "alerts": alerts,
        "days": days,
        "student_count": len(roster),
        "timezone": str(tz),
        "truncated": len(rows) >= _ALERT_FEED_CAP,
        "retrieved": retrieved,
    }


@app.get("/api/classes/{class_id}/alerts")
def class_alerts(class_id: str, request: Request, days: int = _ALERT_FEED_DAYS):
    """Operational alerts for a class, newest first. Class owner only, deliberately."""
    user = get_user(request)
    _verify_class_owner(class_id, user["id"])
    return _class_alerts(class_id, _clamp_days(days))


@app.get("/api/students/{student_id}/focus-accuracy")
def student_focus_accuracy(student_id: str, request: Request, days: int = 30):
    """Answer accuracy against the EEG focus reading at the time.

    Without EEG consent the read is skipped: `eeg_enabled: false`, no buckets.
    """
    _verify_can_view_student(get_user(request), student_id)
    window = _clamp_days(days)
    channels = _reportable_channels(student_id)
    if not channels.eeg:
        return _focus_accuracy_payload(
            None, window, retrieved=True, eeg_enabled=False,
            eeg_revoked_at=channels.eeg_revoked_at,
            consent_retrieved=channels.consent_retrieved)
    return {**_focus_accuracy(student_id, window),
            "eeg_enabled": True,
            "eeg_revoked_at": channels.eeg_revoked_at,
            "consent_retrieved": channels.consent_retrieved}


# ─── consent: what may be recorded, per student ───────────────────────────
# Channels are named for the sensor. The only write path: the checks below
# are the enforcement. See CLAUDE.md "Consent".

CONSENT_CHANNELS = ("eeg", "headband_optical", "camera")

# `_may_record`'s substitute during the admin bypass; never returned by `_consent`.
_CONSENT_ENABLED_ALL = {
    **{f"{c}_enabled": True for c in CONSENT_CHANNELS},
    **{f"{c}_revoked_at": None for c in CONSENT_CHANNELS},
    **{f"{c}_revoked_by": None for c in CONSENT_CHANNELS},
    "updated_by": None,
    "updated_at": None,
    "parent_enabled_at": None,
    "student_ack_at": None,
}

_CONSENT_DENIED = {
    **{f"{c}_enabled": False for c in CONSENT_CHANNELS},
    **{f"{c}_revoked_at": None for c in CONSENT_CHANNELS},
    **{f"{c}_revoked_by": None for c in CONSENT_CHANNELS},
    "updated_by": None,
    "updated_at": None,
    "parent_enabled_at": None,
    "student_ack_at": None,
}


def _parse_ts(value) -> datetime | None:
    """Parse a PostgREST timestamp, tolerating the trailing-Z spelling.

    Z and +00:00 sort differently as strings, so compare parsed values.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        # Logged: a silent failure would suppress needs_student_ack.
        print(f"[consent:parse_ts] unparseable timestamp {value!r}")
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _consent(student_id: str) -> dict:
    """Consent flags for a student. Absent row and failed read both deny (fails closed).

    `retrieved` tells "nobody consented" from "we couldn't find out".
    """
    try:
        rows = supabase.table("signal_consent").select("*") \
            .eq("user_id", student_id).limit(1).execute().data or []
    except Exception as e:
        print(f"[consent:read] {student_id}: {e}")
        return {**_CONSENT_DENIED, "retrieved": False, "exists": False}
    return _consent_from_row(rows[0] if rows else None)


def _consent_from_row(row: dict | None) -> dict:
    """`_consent`'s answer for a row read successfully; None is no row, which denies."""
    if not row:
        # `exists` False with `retrieved` True: a write should insert.
        return {**_CONSENT_DENIED, "retrieved": True, "exists": False}
    return {**_CONSENT_DENIED, **row, "retrieved": True, "exists": True}


class _StoredConsent(NamedTuple):
    """A `_consent` answer for a row read elsewhere: the only consent `_may_record` takes in hand."""
    answer: dict


def _stored_consent(row: dict | None) -> _StoredConsent:
    """The ingest gate's `signal_consent` row as `_consent` answers it; None is no row, which denies."""
    return _StoredConsent(_consent_from_row(row))


def _consent_many(student_ids) -> dict[str, dict]:
    """`_consent` for a roster, in one query.

    Fails closed per student: a failed read denies every id with `retrieved: False`;
    a missing row denies with `retrieved: True`.
    """
    ids = _unique_ids(student_ids)
    if not ids:
        return {}
    try:
        rows = supabase.table("signal_consent").select("*") \
            .in_("user_id", ids).execute().data or []
    except Exception as e:                                     # noqa: BLE001
        print(f"[consent:read_many] {len(ids)} students: {e}")
        return {sid: {**_CONSENT_DENIED, "retrieved": False, "exists": False}
                for sid in ids}
    by_id = {str(r["user_id"]): r for r in rows if r.get("user_id")}
    return {sid: _consent_from_row(by_id.get(sid)) for sid in ids}


def _reportable_channels_many(student_ids, want_emotion: bool = True,
                              want_heart: bool = True) -> dict[str, ReportChannels]:
    """`_reportable_channels` for a roster, over one consent read and one erasure read."""
    erasures = _erasures_many(student_ids)
    return {sid: _channels_from_consent(consent, want_emotion, want_heart,
                                        erasures=erasures.get(str(sid)))
            for sid, consent in _consent_many(student_ids).items()}


# The poller writes with the service-role client; this is its consent gate.
def _poller_may_record_eeg(student_id: str) -> bool:
    """The poller's recurring permission check; logs *why* on a refusal."""
    gate = _may_record(student_id)
    if gate["record_eeg"]:
        return True
    # "EEG": this is also a 403 sentence, and `_as_sentence` only capitalises letter one.
    reason = _as_sentence(_not_recording_reason(gate, "EEG not consented", switches=("record_eeg",)))
    print(f"<<< [eeg-poller] {student_id[:8]}: {reason}", flush=True)
    return False


def _poller_may_record_eeg_reason(student_id: str) -> str:
    """Why `eeg_poller.start()`'s own recheck refused, for its exception text.

    Re-reads rather than caching: runs only on a refusal, and a cache would race.
    """
    gate = _may_record(student_id)
    return _as_sentence(_not_recording_reason(gate, "EEG not consented", switches=("record_eeg",)))


def _paused_by_switch(gate: dict) -> bool:
    """Whether the EEG admin switch is the only refusal in this `_may_record` gate."""
    return ("record_eeg" in gate["switched_off"]
            and gate["window_state"] not in _WINDOW_DENIED
            and bool(gate.get("retrieved")) and bool(gate.get("eeg_enabled")))


def _poller_recheck(student_id: str) -> str:
    """A running poller's verdict over one read: 'record', 'pause' (the switch alone) or 'stop'."""
    gate = _may_record(student_id)
    if gate["record_eeg"]:
        return "record"
    if _paused_by_switch(gate):
        return "pause"
    reason = _as_sentence(_not_recording_reason(gate, "EEG not consented", switches=("record_eeg",)))
    print(f"<<< [eeg-poller] {student_id[:8]}: {reason}", flush=True)
    return "stop"


eeg_poller.set_consent_check(_poller_may_record_eeg)
eeg_poller.set_consent_reason_check(_poller_may_record_eeg_reason)
eeg_poller.set_recheck(_poller_recheck)


def _IS_DUPLICATE_KEY(exc: Exception) -> bool:
    """Whether a write failed because the row already existed (SQLSTATE 23505 or message)."""
    code = getattr(exc, "code", None) or (
        exc.args[0].get("code") if exc.args and isinstance(exc.args[0], dict) else None
    )
    if str(code) == "23505":
        return True
    return "duplicate key" in str(exc).lower()


def _is_linked_parent(viewer_id: str, student_id: str) -> bool:
    """Whether a parent link exists. A failed read raises a 503: it is not a refusal to log."""
    try:
        link = supabase.table("parent_child_links").select("id") \
            .eq("parent_id", viewer_id).eq("child_id", student_id).limit(1).execute().data or []
    except Exception as e:                                     # noqa: BLE001
        if _names_no_row(e):
            return False
        print(f"[consent:parent_link] {e}")
        raise _read_failed("Could not check the parent link; try again")
    return bool(link)


def _consent_actor(viewer: dict, student_id: str) -> str:
    """Who is writing: the student or a linked parent. Never a teacher; 403 otherwise."""
    if viewer["id"] == student_id:
        return "student"
    if _is_linked_parent(viewer["id"], student_id):
        return "parent"
    _record_security_event("authz_denied", viewer["id"], student_id,
                           check="consent_actor")
    raise HTTPException(403, "Only the student or a linked parent can change consent")


def _shape_consent(row: dict, student_id: str, erasures: dict | None = None) -> dict:
    """Per-channel payload: enabled, when it was revoked, and by which role.

    `revoked_by` is a role, never an identity, from that channel's own revoker
    rather than the row's single `updated_by`.
    """
    channels = {}
    for c in CONSENT_CHANNELS:
        enabled = bool(row.get(f"{c}_enabled"))
        revoker = row.get(f"{c}_revoked_by")
        channels[c] = {
            "enabled": enabled,
            "revoked_at": row.get(f"{c}_revoked_at"),
            # Independent of `enabled`; survives a re-enable (a fact about history).
            "erased_at": (erasures or {}).get(c),
            # Only meaningful while the channel is off.
            "revoked_by": (
                None if enabled or not revoker
                else ("student" if revoker == student_id else "parent")
            ),
        }

    enabled_at = _parse_ts(row.get("parent_enabled_at"))
    ack_at = _parse_ts(row.get("student_ack_at"))
    return {
        "student_id": student_id,
        "channels": channels,
        "retrieved": row.get("retrieved", True),
        "updated_at": row.get("updated_at"),
        # Raised only by a parent turning a channel ON, a first opt-in included.
        "needs_student_ack": bool(
            enabled_at and (ack_at is None or ack_at < enabled_at)
        ),
    }


class ConsentUpdate(StrictModel):
    eeg_enabled:              bool | None = None
    headband_optical_enabled: bool | None = None
    camera_enabled:           bool | None = None


class ErasureRequest(StrictModel):
    channel: str = Field(max_length=_SHORT_MAX)
    # Irreversible, so the request carries its own confirmation; omitted -> 422.
    confirm: bool = False


def _erasures(student_id: str) -> dict:
    """`{channel: erased_at}` for channels whose history has been erased.

    Fails **open** to {}, unlike `_consent()`: it only picks a tile's label.
    """
    try:
        rows = supabase.table("signal_erasure").select("channel, erased_at") \
            .eq("user_id", student_id).execute().data or []
    except Exception:
        return {}
    return {r["channel"]: r["erased_at"] for r in rows if r.get("channel")}


def _erasures_many(student_ids) -> dict[str, dict]:
    """`_erasures` for a roster, in one query: `{student_id: {channel: erased_at}}`. Fails open to {}."""
    ids = _unique_ids(student_ids)
    if not ids:
        return {}
    try:
        rows = supabase.table("signal_erasure").select("user_id, channel, erased_at") \
            .in_("user_id", ids).execute().data or []
    except Exception as e:                                     # noqa: BLE001
        print(f"[erasure:read_many] {len(ids)} students: {e}")
        return {}
    out: dict[str, dict] = {}
    for r in rows:
        if r.get("user_id") and r.get("channel"):
            out.setdefault(str(r["user_id"]), {})[r["channel"]] = r["erased_at"]
    return out


@app.get("/api/consent/{student_id}")
def get_consent(student_id: str, request: Request):
    user = get_user(request)
    _verify_can_view_student(user, student_id)
    return _shape_consent(_consent(student_id), student_id, _erasures(student_id))


@app.put("/api/consent/{student_id}")
def update_consent(student_id: str, payload: ConsentUpdate, request: Request):
    user = get_user(request)
    actor = _consent_actor(user, student_id)

    current = _consent(student_id)
    if not current["retrieved"]:
        # Never write blind: a 503 is recoverable, a wrongly-enabled channel is not.
        raise HTTPException(503, "Could not read current consent; not changing it")

    now = _utc_now().isoformat()
    fields: dict = {}
    guards: dict = {}
    re_enabled = False
    withdrawn: list[str] = []
    enabled: list[str] = []
    for c in CONSENT_CHANNELS:
        requested = getattr(payload, f"{c}_enabled")
        if requested is None:
            continue
        was = bool(current[f"{c}_enabled"])
        if requested == was:
            continue

        # A student may withdraw; only a parent may re-enable.
        if requested and actor == "student":
            # Recorded: the UI never offers this, so reaching it means going around the page.
            _record_security_event("authz_denied", user["id"], student_id,
                                   check="consent_direction", channel=c)
            raise HTTPException(
                403,
                f"You can turn {c} off, but only a parent can turn it back on",
            )

        fields[f"{c}_enabled"] = requested
        fields[f"{c}_revoked_at"] = None if requested else now
        fields[f"{c}_revoked_by"] = None if requested else user["id"]
        if not requested:
            # Logged separately: a re-enable nulls `*_revoked_at`.
            withdrawn.append(c)
        # State this decision was made against, asserted on the write below.
        guards[f"{c}_enabled"] = was
        # Any parent turn-on, a first opt-in included: the student is told either way, and
        # the banner's wording claims no earlier withdrawal (a re-enable nulls `revoked_at`).
        if requested and actor == "parent":
            re_enabled = True
            enabled.append(c)

    if not fields:
        # No-op: don't restamp, or an unchanged re-save raises a notice.
        return _shape_consent(current, student_id, _erasures(student_id))

    fields["updated_by"] = user["id"]
    fields["updated_at"] = now
    if re_enabled:
        fields["parent_enabled_at"] = now

    try:
        if not current["exists"]:
            # Not upsert: a concurrently created row collides instead of being overwritten.
            try:
                supabase.table("signal_consent") \
                    .insert({"user_id": student_id, **fields}).execute()
            except Exception as e:
                # Same race as the conditional update below, so the same 409.
                if _IS_DUPLICATE_KEY(e):
                    raise HTTPException(
                        409, "Consent changed while you were editing it; reload and try again"
                    )
                raise
            _record_enablements(student_id, enabled, user["id"], now)
            return _shape_consent(_consent(student_id), student_id, _erasures(student_id))

        # Conditional on every flag decided against; if it moved, no match -> 409.
        q = supabase.table("signal_consent").update(fields, returning=ReturnMethod.representation) \
            .eq("user_id", student_id)
        for col, was in guards.items():
            q = q.eq(col, was)
        written = q.execute().data or []
    except HTTPException:
        # The insert race's 409; not a 500.
        raise
    except Exception as e:
        print(f"[consent:write] {student_id}: {e}")
        raise HTTPException(500, "Could not save consent")

    if not written:
        raise HTTPException(409, "Consent changed while you were editing it; reload and try again")

    _record_withdrawals(student_id, withdrawn, user["id"], now)
    _record_enablements(student_id, enabled, user["id"], now)

    # After the confirmed update. Channel names and direction only, never the
    # resulting flags: `signal_consent` is the authority.
    _record_security_event(
        "consent_changed", user["id"], student_id,
        actor_role=actor,
        withdrew=",".join(withdrawn) or None,
        re_enabled=re_enabled or None)

    return _shape_consent(_consent(student_id), student_id, _erasures(student_id))


def _record_withdrawals(student_id: str, channels: list[str],
                        by: str, at: str) -> None:
    """Append one row per channel switched off. Never raises.

    Called only after the conditional update is confirmed. Swallows its failure:
    the withdrawal is already durable and must not become a 500.
    """
    if not channels:
        return
    try:
        supabase.table("consent_withdrawals").insert([
            {"user_id": student_id, "channel": c,
             "withdrawn_at": at, "withdrawn_by": by}
            for c in channels
        ]).execute()
    except Exception as e:
        print(f"[consent:withdrawal-log] {student_id} {channels}: {e}")


def _record_enablements(student_id: str, channels: list[str],
                        by: str, at: str) -> None:
    """Append one row per channel a parent switched on, for the other parents' notices. Never raises."""
    if not channels:
        return
    try:
        supabase.table("consent_enablements").insert([
            {"user_id": student_id, "channel": c,
             "enabled_at": at, "enabled_by": by}
            for c in channels
        ]).execute()
    except Exception as e:
        print(f"[consent:enable-log] {student_id} {channels}: {e}")


@app.post("/api/consent/{student_id}/erase")
def erase_consent_channel(student_id: str, payload: ErasureRequest,
                          request: Request):
    """Destroy one channel's stored signals for one student. Irreversible.

    Linked parent only (not `_consent_actor`), and never triggered by a consent
    change. The database half is one transaction; storage removal after it is
    counted in `charts_failed`, not awaited.
    """
    user = get_user(request)
    if not _is_linked_parent(user["id"], student_id):
        _record_security_event("authz_denied", user["id"], student_id,
                               check="erasure_parent")
        raise HTTPException(403, "Only a linked parent can erase stored signals")
    if payload.channel not in CONSENT_CHANNELS:
        raise HTTPException(422, f"Unknown channel {payload.channel!r}")
    if not payload.confirm:
        raise HTTPException(422, "Set confirm=true; erasing stored signals cannot be undone")

    try:
        result = supabase.rpc("erase_signals", {
            "p_user_id": student_id,
            "p_channel": payload.channel,
            "p_erased_by": user["id"],
            # A name: RPC params go through `json.dumps`, which can't serialise a ZoneInfo.
            "p_timezone": _school_timezone_name(),
        }).execute().data or {}
    except Exception as e:
        # One transaction, so nothing partial to describe.
        print(f"[erase] {student_id} {payload.channel}: {e}")
        raise HTTPException(500, "Could not erase stored signals")

    removed, failed = chart_archive.remove_objects(
        supabase, result.pop("object_paths", []))

    return {**result, "charts_removed": removed, "charts_failed": len(failed),
            "erased_at": _erasures(student_id).get(payload.channel)}


@app.post("/api/consent/ack")
def ack_consent(request: Request):
    """Student dismisses the notice that a parent turned a channel back on."""
    user = get_user(request)
    try:
        written = supabase.table("signal_consent") \
            .update({"student_ack_at": _utc_now().isoformat()}, returning=ReturnMethod.representation) \
            .eq("user_id", user["id"]).execute().data or []
    except Exception as e:
        print(f"[consent:ack] {user['id']}: {e}")
        raise HTTPException(500, "Could not acknowledge")
    # Matched nothing: no consent row, so nothing was dismissed.
    if not written:
        raise HTTPException(404, "No consent record to acknowledge")
    return {"ok": True}


# The consent channels each sensor serves: refused when all of them refuse, allowed when any permits.
_RECORDING_SENSORS = {"camera": ("camera",), "headband": ("eeg", "headband_optical")}
# Heart source to the consent channel it is recorded under, from the flags the ingest gate uses.
_HEART_SOURCE_CHANNEL = {source: flag.removeprefix("record_")
                         for flag, sources in _HEART_SOURCES_BY_RECORD_FLAG.items() for source in sources}
# The ingest endpoints' own decline wording, so a reading the sidecar withholds reads like one they declined.
_RECORDING_DECLINED = {"eeg": "eeg not consented",
                       **{flag.removeprefix("record_"): text for flag, text in _HEART_SENSOR_DECLINED.items()}}


def _recording_reason(channel: str, state: str) -> str | None:
    """Why `channel` may not record, in the ingest endpoints' words; None when it may, or nobody knows."""
    if state == "declined":
        return _RECORDING_DECLINED[channel]
    if state == "switched_off":
        return _SWITCHED_OFF
    return next((m.reason for m in _WINDOW_STATES.values() if m.stopped_reason == state), None)


@app.get("/api/recording/me")
def my_recording_permits(request: Request):
    """Whether each of the caller's sensors may record now; the lesson page and the sidecar ask.

    Always 200: a failed read is `unknown` per channel, which the page treats as "don't switch on".
    """
    user = get_user(request)
    try:
        gate = _may_record(user["id"])
    except Exception as e:
        print(f"[recording:me] {user['id']}: {e}")
        gate = None
    states = {c: _recording_state(gate, c) if gate else "unknown" for c in CONSENT_CHANNELS}
    refused = {c: s not in ("permitted", "unknown") for c, s in states.items()}
    return {
        **{c: {"state": s, "reason": _recording_reason(c, s)} for c, s in states.items()},
        # Derived here once, so the sidecar keeps no copy of the rule or the mapping.
        "sensors": {sensor: {"allowed": any(states[c] == "permitted" for c in channels),
                             "refused": all(refused[c] for c in channels)}
                    for sensor, channels in _RECORDING_SENSORS.items()},
        "heart_sources": dict(_HEART_SOURCE_CHANNEL),
    }


# ─── biosignals: cognitive (headband) + face recognition ──────────────────

# `features`/`bands` keys that land in numeric columns; the rest goes to `raw`.
_COGNITIVE_NUMERIC_KEYS = (
    "focus_score", "calm_score", "confidence",
    "alpha", "beta", "theta", "delta", "gamma",
)


class CognitiveSample(BaseModel):
    """One EEG reading, in either of two shapes.

    Flat fields: already-mapped 0..1 rows. `features`/`bands`: the sidecar's
    raw 0..100 payload, converted once in `signal_mapping`.
    """
    ts:         str | None = None
    focus:      float | None = None
    stress:     float | None = None
    engagement: float | None = None
    alpha:      float | None = None
    beta:       float | None = None
    theta:      float | None = None
    delta:      float | None = None
    gamma:      float | None = None
    raw:        dict  | None = None
    # Free-form keys; the values reaching numeric columns are still checked.
    features:   dict  | None = None
    bands:      dict  | None = None

    @field_validator("focus", "stress", "engagement",
                     "alpha", "beta", "theta", "delta", "gamma")
    @classmethod
    def _finite(cls, v: float | None) -> float | None:
        """NaN and inf reach a `float | None` field untouched -- see below."""
        if v is not None and not math.isfinite(v):
            raise ValueError("must be a finite number")
        return v

    @field_validator("features", "bands")
    @classmethod
    def _numeric_values_must_be_storable(cls, v: dict | None) -> dict | None:
        """Reject non-finite values bound for numeric columns, before the database.

        Pydantic v2 and `json.loads` both let NaN/Infinity through, and one would
        fail the whole batch's insert. Only column-bound keys are checked.
        """
        if v is None:
            return v
        for key in _COGNITIVE_NUMERIC_KEYS:
            if key not in v or v[key] is None:
                continue
            try:
                n = float(v[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key!r} must be a number, got {v[key]!r}")
            if not math.isfinite(n):
                raise ValueError(f"{key!r} must be finite, got {v[key]!r}")
        return v

class CognitiveBatch(BaseModel):
    session_id: str
    # Capped: this is a trust boundary. `Any`, validated per sample in the
    # endpoint, so one malformed sample is dropped rather than 422ing the batch.
    samples:    list[Any] = Field(max_length=_INGEST_MAX_BATCH)

class FaceSample(BaseModel):
    ts:                  str | None = None
    emotion:             str   | None = None
    attention:           float | None = None
    gaze_x:              float | None = None
    gaze_y:              float | None = None
    # Head pose, degrees; distinct from gaze (iris within the eye).
    # Every column the mapper writes needs a field here, or Pydantic drops it silently.
    head_yaw:            float | None = None
    head_pitch:          float | None = None
    head_roll:           float | None = None
    emotion_confidence:  float | None = None
    emotion_trusted:     bool  | None = None
    raw:                 dict  | None = None

    @field_validator("attention", "gaze_x", "gaze_y", "head_yaw", "head_pitch", "head_roll",
                     "emotion_confidence")
    @classmethod
    def _finite(cls, v: float | None) -> float | None:
        """As `CognitiveSample._finite`: a NaN would fail the whole batch's upsert, not drop one sample."""
        if v is not None and not math.isfinite(v):
            raise ValueError("must be a finite number")
        return v

class FaceBatch(BaseModel):
    session_id: str
    # `Any`, validated per sample in the endpoint, as `CognitiveBatch` is.
    samples:    list[Any] = Field(max_length=_INGEST_MAX_BATCH)


# Must equal `heart_signals_stress_category_check`: one value outside it fails the whole upsert.
_HEART_STRESS_CATEGORIES = frozenset({"calibrating", "low", "moderate", "high", "unknown"})


class HeartSample(BaseModel):
    """One derived heart reading, from whichever sensor produced it.

    `source` (muse_optics | muse_ppg | rppg) is required: consent is per sensor.
    Bounds are the table's CHECKs, so a bad value is one malformed sample, not a failed batch.
    """
    ts:                 str | None = None
    source:             str
    heart_rate_bpm:     float | None = Field(None, ge=20, le=250)
    rmssd_ms:           float | None = Field(None, ge=0, le=1000)
    beat_coverage:      float | None = None
    rmssd_rejected_by:  str   | None = None
    sqi:                float | None = Field(None, ge=0, le=1)
    stress_score:       float | None = Field(None, ge=0, le=100)
    stress_category:    str   | None = None
    # What `stress_score` is relative to; the mapper puts it in `raw`.
    stress_baseline_bpm: float | None = None
    trusted:            bool  | None = None
    # Simulator mark (EEG_SIM_OPTICS); top-level, since the mapper strips it from `raw`.
    synthetic:          bool  | None = None
    raw:                dict  | None = None

    @field_validator("heart_rate_bpm", "rmssd_ms", "beat_coverage",
                     "sqi", "stress_score", "stress_baseline_bpm")
    @classmethod
    def _finite(cls, v: float | None) -> float | None:
        """Same check as `CognitiveSample._finite`."""
        if v is not None and not math.isfinite(v):
            raise ValueError("must be a finite number")
        return v

    @field_validator("stress_category")
    @classmethod
    def _known_category(cls, v: str | None) -> str | None:
        if v is not None and v not in _HEART_STRESS_CATEGORIES:
            raise ValueError("unknown stress category")
        return v


class HeartBatch(BaseModel):
    session_id: str
    # `Any`, validated per sample in the endpoint, as `CognitiveBatch` is.
    samples:    list[Any] = Field(max_length=_INGEST_MAX_BATCH)


def _validate_each(model, raw_samples) -> tuple[list, int]:
    """Each sample on its own: `(valid, malformed count)`. One bad sample would otherwise 422 the
    batch, and the push client retries a refused batch, so it would never be delivered."""
    valid, malformed = [], 0
    for raw in raw_samples:
        try:
            valid.append(model.model_validate(raw))
        except Exception:  # noqa: BLE001 -- pydantic's ValidationError, plus a non-dict entry
            malformed += 1
    return valid, malformed

def _rate_limit_ingest(user_id: str):
    """Raise 429 once a caller has spent its allowance for the window."""
    refused_after = _INGEST_LIMITER.check(user_id)

    # Recorded outside the limiter's lock, the most contended one (~1 Hz per student).
    if refused_after is not None:
        _record_security_event("rate_limited", user_id,
                               limiter=_INGEST_LIMITER.name)
        raise HTTPException(429, "Too many ingest batches. Slow down.",
                            headers={"Retry-After": str(refused_after)})


def _admit_ingest_rows(user_id: str, channel: str, n: int) -> None:
    """Raise 429 if `n` more rows would pass the student's per-channel ceiling; the batch is kept and retried."""
    if n <= 0:
        return
    refused_after = _INGEST_ROW_LIMITER.check(f"{user_id}:{channel}", cost=n)
    if refused_after is not None:
        _record_security_event("rate_limited", user_id, limiter=_INGEST_ROW_LIMITER.name)
        raise HTTPException(429, "Too many samples. Slow down.",
                            headers={"Retry-After": str(refused_after)})


def _write_ingest_rows(table: str, rows: list[dict], on_conflict: str,
                       user_id: str, channel: str) -> int:
    """Upsert admitted rows and return how many were new; a replayed or failed row is given back."""
    key = f"{user_id}:{channel}"
    try:
        resp = supabase.table(table).upsert(rows, on_conflict=on_conflict, ignore_duplicates=True,
                                            returning=ReturnMethod.representation).execute()
    except Exception:
        _INGEST_ROW_LIMITER.release(key, len(rows))
        raise
    # What the database wrote; push_client counts from it.
    written = len(resp.data or [])
    _INGEST_ROW_LIMITER.release(key, len(rows) - written)
    return written


def _permitted_heart_sources(gate: dict) -> set[str]:
    """The sources this student may currently be recorded from.

    Takes a `_may_record` result; a raw consent dict has no `record_*` keys and
    permits nothing.
    """
    allowed: set[str] = set()
    for flag, sources in _HEART_SOURCES_BY_RECORD_FLAG.items():
        if gate.get(flag):
            allowed.update(sources)
    return allowed


def _heart_consent_for_poller(student_id: str, source: str) -> bool:
    """Whether `student_id` may be recorded from heart `source`; the poller's gate.

    The same two calls `/api/signals/heart` makes, so both paths agree.
    """
    return source in _permitted_heart_sources(_may_record(student_id))


eeg_poller.set_heart_consent_check(_heart_consent_for_poller)


def _session_or_403(session_id: str, user_id: str, columns: str = "user_id",
                    row: dict | None = None) -> dict:
    """Fetch a session, refusing it unless the caller owns it. Returns the row.

    Ownership only. `columns` must include `user_id`; `row` is one already read, skipping
    the fetch. A missing row is a 404 and a failed read a 503, never a way past.
    """
    if row is None:
        row = _row_or_404(
            supabase.table("sessions").select(columns).eq("id", session_id),
            "Session")
    if row.get("user_id") != user_id:
        _record_security_event("authz_denied", user_id, row.get("user_id"),
                               check="session_owner", session_id=session_id)
        raise HTTPException(403, "Not your session")
    return row


def _verify_session_owner(session_id: str, user_id: str, columns: str = "user_id") -> dict:
    """`_session_or_403`: the refusal, and the row for a caller that names more columns."""
    return _session_or_403(session_id, user_id, columns)


# Clock drift tolerated between a sidecar's sample stamps and this server's session bounds.
_INGEST_TS_SLACK = timedelta(minutes=10)


def _gate_shape_fault(out) -> str | None:
    """None for `ingest_gate`'s object; otherwise its shape, by type and keys, never values."""
    if not isinstance(out, dict):
        return f"a {type(out).__name__}"
    if not {"session", "consent"} <= out.keys():
        return f"an object with keys {sorted(out)}"
    for key in ("session", "consent"):
        if out[key] is not None and not isinstance(out[key], dict):
            return f"{key} as a {type(out[key]).__name__}"
    if out["session"] is not None and "user_id" not in out["session"]:
        return f"a session with keys {sorted(out['session'])}"
    return None


def _ingest_gate(session_id: str, user_id: str) -> tuple[dict, dict | None]:
    """The caller's own session (`user_id, started_at, ended_at`) and their consent row, in one read.

    Missing 404, someone else's 403 through `_session_or_403`. A failed read, or an answer not
    shaped as the function's object, is a 503 with nothing written: the push client resends.
    """
    try:
        out = supabase.rpc("ingest_gate", {"p_session_id": session_id,
                                           "p_user_id": user_id}).execute().data
    except Exception as e:                                     # noqa: BLE001
        if _names_no_row(e):
            raise HTTPException(404, "Session not found")
        if not _missing_rpc(e, "ingest_gate", "20261003000000",
                            "every signal batch is a 503, held and resent by the push client, until then"):
            print(f"[ingest] could not read the gate for {session_id}: {e}")
        raise _read_failed("Could not check this session; try again")
    fault = _gate_shape_fault(out)
    if fault:
        print(f"[ingest] ingest_gate answered {fault} for {session_id}; a failed read, so a 503")
        raise _read_failed("Could not check this session; try again")
    if out["session"] is None:
        raise HTTPException(404, "Session not found")
    return _session_or_403(session_id, user_id, row=out["session"]), out["consent"]


def _ingest_ts_filter(session: dict):
    """A predicate: is a sample's `ts` inside the session, give or take `_INGEST_TS_SLACK`?

    The stamp comes from the student's laptop clock. One outside the session lands on a
    day its rollup never covers, which the expiry job then deletes unsummarised.
    A missing `ts` is stamped server-side and passes; an unparseable one does not.
    """
    session = session or {}
    started = _parse_ts(session.get("started_at"))
    ended = _parse_ts(session.get("ended_at")) or _utc_now()
    if started is None:
        return lambda ts: True      # no bound to check against; never refuse on our gap
    lo, hi = started - _INGEST_TS_SLACK, ended + _INGEST_TS_SLACK

    def inside(ts) -> bool:
        if ts is None:
            return True
        parsed = _parse_ts(ts)
        return parsed is not None and lo <= parsed <= hi
    return inside

@app.post("/api/signals/cognitive")
def ingest_cognitive(payload: CognitiveBatch, request: Request):
    user = get_user(request)
    # Rate-limit first: a flooding client never reaches the `ingest_gate` call.
    _rate_limit_ingest(user["id"])
    session, consent_row = _ingest_gate(payload.session_id, user["id"])

    # Last line of defence against a stale sidecar; fails closed, reason says which gate.
    consent = _may_record(user["id"], consent=_stored_consent(consent_row))
    if not consent["record_eeg"]:
        # `refused`: not permitted, which a fresh permit answer can act on; an unreadable gate is not that.
        return {"ok": True, "inserted": 0, "dropped": len(payload.samples),
                "refused": _definitely_refused(consent, "eeg"),
                "reason": _not_recording_reason(consent, "eeg not consented",
                                                switches=("record_eeg",))}

    # Warn once when a live poller also writes this session (checked and claimed atomically).
    if eeg_poller.claim_double_write_warning(payload.session_id):
        print(f"[ingest] session {payload.session_id[:8]} is being written by both "
              f"the poller and /api/signals/cognitive. Every EEG sample is landing "
              f"twice and cognitive_signals has no dedupe key to catch it.",
              flush=True)
    def _row(s: CognitiveSample) -> dict:
        # Sensor output goes through the shared mapper (scale, `stress = 1 - calm`);
        # flat samples are already in table units.
        if s.features is not None or s.bands is not None:
            # The mapper reads these envelope keys off the top level.
            raw = dict(s.raw or {})
            envelope = {k: raw.pop(k, None)
                        for k in ("device_id", "channels", "state", "ingestion")}
            return signal_mapping.map_eeg_to_cognitive(
                {"timestamp": s.ts or _utc_now().isoformat(),
                 "features": s.features or {}, "bands": s.bands or {},
                 **envelope,
                 "raw": raw},
                payload.session_id, user["id"])
        return signal_mapping.sanitise_flat_row({
            "session_id": payload.session_id,
            "user_id":    user["id"],
            "ts":         s.ts or _utc_now().isoformat(),
            # `engagement` is the focus index; the client's own value is ignored.
            "focus":      s.focus, "stress": s.stress, "engagement": s.focus,
            "alpha":      s.alpha, "beta":   s.beta,   "theta":      s.theta,
            "delta":      s.delta, "gamma":  s.gamma,  "raw":        s.raw,
        })

    # Mapper `None` = zeroed scores from a disconnected headband: dropped and counted.
    samples, malformed = _validate_each(CognitiveSample, payload.samples)
    inside = _ingest_ts_filter(session)
    placed = [s for s in samples if inside(s.ts)]
    out_of_window = len(samples) - len(placed)
    samples = placed
    rows = [r for r in (_row(s) for s in samples) if r is not None]
    # Upsert on `cog_session_ts_key`: a replayed batch is a no-op.
    inserted = 0
    if rows:
        _admit_ingest_rows(user["id"], "cognitive", len(rows))
        inserted = _write_ingest_rows("cognitive_signals", rows, "session_id,ts",
                                      user["id"], "cognitive")
    dropped = len(samples) - len(rows)
    return {"ok": True, "inserted": inserted,
            "dropped": dropped,
            "malformed": malformed,
            "out_of_window": out_of_window,
            "duplicates": len(rows) - inserted,
            # Recording is permitted by here, so a drop is a reading with nothing in it.
            "refused": False, "reason": _NO_USABLE_EEG if dropped else None}

@app.post("/api/signals/face")
def ingest_face(payload: FaceBatch, request: Request):
    user = get_user(request)
    _rate_limit_ingest(user["id"])
    session, consent_row = _ingest_gate(payload.session_id, user["id"])

    # Last line of defence against a stale sidecar; fails closed.
    consent = _may_record(user["id"], consent=_stored_consent(consent_row))
    if not consent["record_camera"]:
        return {"ok": True, "inserted": 0, "dropped": len(payload.samples),
                "refused": _definitely_refused(consent, "camera"),
                "reason": _not_recording_reason(consent, "camera not consented",
                                                switches=("record_camera",))}

    samples, malformed = _validate_each(FaceSample, payload.samples)
    inside = _ingest_ts_filter(session)
    placed = [s for s in samples if inside(s.ts)]
    # Through the shared mapper, so the field list can't drift.
    rows = [r for r in (
        signal_mapping.map_face_to_face_signal(
            {"timestamp": s.ts or _utc_now().isoformat(),
             "face": {"emotion": s.emotion, "attention": s.attention,
                      "gaze_x": s.gaze_x, "gaze_y": s.gaze_y,
                      "head_yaw": s.head_yaw, "head_pitch": s.head_pitch,
                      "head_roll": s.head_roll,
                      "emotion_confidence": s.emotion_confidence,
                      "trusted": s.emotion_trusted},
             "raw": s.raw},
            payload.session_id, user["id"])
        for s in placed
    ) if r is not None]
    # Upsert on `face_session_ts_key`: a replayed batch is a no-op.
    inserted = 0
    if rows:
        _admit_ingest_rows(user["id"], "face", len(rows))
        inserted = _write_ingest_rows("face_signals", rows, "session_id,ts",
                                      user["id"], "face")
    # Separate counts: push_client tells a quiet camera from a replay by them.
    return {"ok": True, "inserted": inserted,
            "dropped": len(placed) - len(rows),
            "malformed": malformed,
            "out_of_window": len(samples) - len(placed),
            "duplicates": len(rows) - inserted, "refused": False}


@app.post("/api/signals/heart")
def ingest_heart(payload: HeartBatch, request: Request):
    """Derived heart readings, from whichever sensor produced them.

    Consent is checked per sample against `source`; declined samples are
    dropped and counted rather than failing the batch.
    """
    user = get_user(request)
    _rate_limit_ingest(user["id"])
    session, consent_row = _ingest_gate(payload.session_id, user["id"])

    consent = _may_record(user["id"], consent=_stored_consent(consent_row))
    allowed = _permitted_heart_sources(consent)
    samples, malformed = _validate_each(HeartSample, payload.samples)
    kept = [s for s in samples if s.source in allowed]
    dropped = len(samples) - len(kept)
    inside = _ingest_ts_filter(session)
    placed = [s for s in kept if inside(s.ts)]
    out_of_window = len(kept) - len(placed)
    kept = placed

    # The consent flags of the sensors whose samples were dropped; an unknown sensor has none.
    flags = sorted({flag for s in samples if s.source not in allowed
                    for flag, sources in _HEART_SOURCES_BY_RECORD_FLAG.items() if s.source in sources})
    # Tells "every sensor declined" from "could not find out".
    reason, refused = None, False
    if not allowed:
        # Judged by the sensors these readings came from, not every heart sensor.
        refused = _definitely_refused(consent, *(flag.removeprefix("record_") for flag in flags))
        reason = _not_recording_reason(
            consent, "no consented heart sensor", switches=tuple(_HEART_SOURCES_BY_RECORD_FLAG),
            partly_switched="no heart sensor is both switched on and consented")
    elif dropped:
        # One sensor allowed, samples from another: name that one, so a drop never comes without a why.
        # Something is allowed, so the gate was read: a sensor refused here is definitely refused.
        refused = bool(flags)
        reason = "; ".join(_not_recording_reason(consent, _HEART_SENSOR_DECLINED[flag], switches=(flag,))
                           for flag in flags) or "unknown heart sensor"

    rows = [r for r in (
        signal_mapping.map_heart_to_heart_signal(
            {"timestamp": s.ts or _utc_now().isoformat(),
             "heart": {"source": s.source, "bpm": s.heart_rate_bpm,
                       "rmssd_ms": s.rmssd_ms,
                       "beat_coverage": s.beat_coverage,
                       "rmssd_rejected_by": s.rmssd_rejected_by,
                       "sqi": s.sqi,
                       "stress_score": s.stress_score,
                       "stress_category": s.stress_category,
                       "stress_baseline_bpm": s.stress_baseline_bpm,
                       "trusted": s.trusted,
                       "synthetic": s.synthetic},
             "raw": s.raw},
            payload.session_id, user["id"])
        for s in kept
    ) if r is not None]

    written = 0
    if rows:
        _admit_ingest_rows(user["id"], "heart", len(rows))
        # Idempotent on (session_id, source, ts).
        written = _write_ingest_rows("heart_signals", rows, "session_id,source,ts",
                                     user["id"], "heart")
    return {"ok": True, "inserted": written, "dropped": dropped,
            "malformed": malformed,
            "out_of_window": out_of_window,
            "duplicates": len(rows) - written, "refused": refused, "reason": reason}

@app.get("/api/signals/session/{session_id}")
def session_signals(session_id: str, request: Request, since: str | None = None):
    # Raw samples for a session. No include_face: see frontend/src/lib/viewPrefs.js.
    user = get_user(request)
    # Relationship, not ownership: teachers and parents read this too.
    sess = _row_or_404(
        supabase.table("sessions").select("user_id").eq("id", session_id), "Session")
    _verify_can_view_student(user, sess["user_id"])

    # As the reports do: a withdrawn channel is not read, and the payload says why it is empty.
    channels = _reportable_channels(sess["user_id"])
    skip = frozenset(t for t, on in (("face_signals", channels.emotion),
                                     ("heart_signals", channels.heart)) if not on)
    # Paged, through the archive's reader. Heart rows carry `source`: a mid-session
    # sensor failover must read as a sensor change, not a physiological event.
    cog_data, fac_data, hrt_data = chart_archive.read_session_signals(
        supabase, session_id, since, skip=skip)
    # Question embedded (one query, named columns). Left-joined: a deleted
    # question arrives as `questions: null` and the answer still shows.
    answers = (supabase.table("session_answers")
               .select("*, questions(question_text, options, correct_answer, "
                       "subject, difficulty, figure, ccss_standard)")
               .eq("session_id", session_id).order("answered_at")
               .execute().data or [])
    # Up to 20k rows a table: a Response skips FastAPI's per-element `jsonable_encoder`
    # walk. Safe because PostgREST rows are already JSON values.
    return JSONResponse({"cognitive": cog_data, "face": fac_data, "heart": hrt_data,
                         "answers": answers, "channels": _channel_flags(channels)})


def _channel_flags(channels: ReportChannels) -> dict:
    """Why a skipped channel is empty, in the reports' vocabulary: off, since when, or consent unreadable."""
    return {"face_included": channels.emotion, "heart_included": channels.heart,
            "consent_retrieved": channels.consent_retrieved,
            "emotion_revoked_at": channels.emotion_revoked_at,
            "heart_revoked_at": channels.heart_revoked_at}


# Archived charts drawing a channel, withheld as its rows are once that channel is withdrawn.
_CHARTS_BY_CHANNEL = {"emotion": ("emotion_pie",), "heart": ("heart_rate", "stress_pie")}


@app.get("/api/signals/session/{session_id}/charts")
def session_charts(session_id: str, request: Request):
    """Short-lived signed URLs for a closed session's archived charts.

    The bucket has no policies: `_verify_can_view_student` is the whole check. States: `archived:
    false` (never ran), `charts[name]: null` (nothing drawn), `unavailable` (object unreadable),
    `withdrawn` (channel off) and `unchecked` (consent unreadable); the last two are never signed.
    """
    user = get_user(request)
    sess = _row_or_404(
        supabase.table("sessions").select("user_id, chart_paths").eq("id", session_id),
        "Session")
    _verify_can_view_student(user, sess["user_id"])
    channels = _reportable_channels(sess["user_id"])
    held = sorted(name for channel, on in (("emotion", channels.emotion), ("heart", channels.heart))
                  if not on for name in _CHARTS_BY_CHANNEL[channel])
    # An unreadable consent row withholds too (fail closed), but is not a withdrawal.
    withdrawn, unchecked = (held, []) if channels.consent_retrieved else ([], held)
    common = {"withdrawn": withdrawn, "unchecked": unchecked, "channels": _channel_flags(channels),
              "expires_in": chart_archive.SIGNED_URL_TTL_SECONDS}

    paths = sess.get("chart_paths")
    if paths is None:
        # Column-NULL: the archive never ran. Distinct from `{}` and four nulls.
        return {"archived": False, "charts": {}, "unavailable": [], **common}

    # Security: paths derive from owner and id; `chart_paths` decides presence only.
    # A withheld chart is dropped before signing, so no URL to it is ever issued.
    kept = {name: path for name, path in paths.items() if name not in held}
    urls, unavailable = chart_archive.signed_chart_urls(
        supabase, kept, sess["user_id"], session_id)
    return {"archived": True, "charts": urls, "unavailable": unavailable, **common}


# ─── live monitoring (only show truly active sessions) ───────────────────



_SIGNAL_CHANNELS = ("cognitive", "face", "heart", "answer")


def _latest_signals_many(session_ids) -> dict[str, dict[str, dict]]:
    """The newest cognitive, face, heart and answer row for many sessions, in one RPC.

    `{session_id: {channel: row}}`. Don't fan this into a worker pool: it deadlocked.
    Errors propagate, so a failed read never draws every student as idle.
    """
    ids = _unique_ids(session_ids)
    if not ids:
        return {}
    res = supabase.rpc("latest_signals_for_sessions",
                       {"p_session_ids": ids}).execute()
    rows = res.data or []
    if isinstance(rows, dict):
        rows = [rows]
    out: dict[str, dict[str, dict]] = {}
    for r in rows:
        sid = r.get("session_id")
        channel = r.get("channel")
        if sid and channel in _SIGNAL_CHANNELS:
            out.setdefault(sid, {})[channel] = r.get("payload") or {}
    return out


@app.get("/api/teacher/classes/{class_id}/live")
def class_live(class_id: str, request: Request):
    """Live signals for the students of a class the caller owns. No include_face, by design."""
    user = get_user(request)
    _verify_class_owner(class_id, user["id"])

    LIVE_WINDOW_SEC = _LIVE_WINDOW_SEC
    STALE_AFTER_SEC = _STALE_AFTER_SEC
    now = _utc_now()
    live_cutoff  = (now - timedelta(seconds=LIVE_WINDOW_SEC)).isoformat()
    stale_cutoff = (now - timedelta(seconds=STALE_AFTER_SEC)).isoformat()

    members = supabase.table("class_memberships").select("student_id").eq("class_id", class_id).execute().data or []
    roster = [m["student_id"] for m in members]
    profiles = _profiles_many(roster)
    open_by_student = _open_sessions_many(roster)
    latest_by_session = _latest_signals_many(
        s["id"] for rows in open_by_student.values() for s in rows[:1])
    out = []
    for m in members:
        sid = m["student_id"]
        p = profiles.get(sid) or {}
        open_sessions = open_by_student.get(sid) or []

        active = None
        latest_cog = latest_face = latest_heart = None

        if open_sessions:
            sess = open_sessions[0]
            sid2 = sess["id"]
            newest = latest_by_session.get(sid2) or {}
            c = [newest["cognitive"]] if "cognitive" in newest else []
            f = [newest["face"]] if "face" in newest else []
            h = [newest["heart"]] if "heart" in newest else []
            a = [newest["answer"]] if "answer" in newest else []

            latest_cog  = c[0] if c else None
            latest_face = f[0] if f else None
            # Newest row, trusted or not; the card reads `trusted` itself.
            latest_heart = h[0] if h else None

            candidates = []
            if latest_cog and latest_cog.get("ts"):       candidates.append(latest_cog["ts"])
            if latest_face and latest_face.get("ts"):     candidates.append(latest_face["ts"])
            if latest_heart and latest_heart.get("ts"):   candidates.append(latest_heart["ts"])
            if a and a[0].get("answered_at"):             candidates.append(a[0]["answered_at"])
            if sess.get("started_at"):                    candidates.append(sess["started_at"])
            last_activity = max(candidates) if candidates else sess.get("started_at")
            # With no sensor, a student reading one question sends nothing for minutes; only a
            # sensor that went quiet says they left. A sensorless one waits for the sweep.
            sensed = bool(latest_cog or latest_face or latest_heart)

            if sensed and last_activity and last_activity < stale_cutoff:
                # `sid` is the student. Stop the poller before closing, or a tick
                # can land a row after the discard check looked.
                eeg_poller.stop(sid2, sid)
                # Ended when last seen, by the sweep's definition (measured rows), not this card's.
                _close_session(sid, sess, _ended_when_last_seen(sess, _last_seen_many([sess])),
                               closed_by=CLOSED_BY_SWEEP, reason=CLOSE_REASON_LIVE_STALE)
                active = None; latest_cog = None; latest_face = None; latest_heart = None
            elif last_activity and last_activity >= live_cutoff:
                active = sess

        out.append({
            "user_id":          sid,
            "name":             p.get("display_name") or "Student",
            "email":            p.get("email") or "",
            "active_session":   active,
            "latest_cognitive": latest_cog,
            "latest_face":      latest_face,
            # Carries `source`, so the card shows which sensor is live.
            "latest_heart":     latest_heart,
        })
    return out


# ─── EEG sidecar integration ─────────────────────────���───────────────────

def _poller_status(user_id: str, push: bool = False) -> dict:
    """`eeg_poller.status`, plus `stopped_reason` when it is known.

    Derived from current consent, not remembered on the poller, so it cannot go stale.
    Under `push` the poller never runs, so the mode is the reason once nothing else is.
    """
    status = eeg_poller.status(user_id)
    if status.get("running"):
        if status.get("withheld"):
            # Running but writing nothing, until the switch is turned back on.
            return {**status, "stopped_reason": "recording_switched_off"}
        return status
    # Window before consent, via `_may_record`, as at the recording sites.
    gate = _may_record(user_id)
    stopped = _window_meaning(gate["window_state"]).stopped_reason
    if stopped:
        return {**status, "stopped_reason": stopped,
                "window_starts_on": gate["window_starts_on"],
                "window_ends_on": gate["window_ends_on"]}
    # The switch before consent, as `_not_recording_reason` orders them.
    if "record_eeg" in gate.get("switched_off", ()):
        return {**status, "stopped_reason": "recording_switched_off"}
    if not gate.get("retrieved"):
        # A failed read is not a refusal.
        return {**status, "stopped_reason": "consent_unknown"}
    if not gate.get("eeg_enabled"):
        return {**status, "stopped_reason": "consent_withdrawn",
                "revoked_at": gate.get("eeg_revoked_at")}
    if push:
        return {**status, "stopped_reason": "push_ingestion"}
    return status


def _refuse_under_push(what: str) -> None:
    """Under push, 409 rather than the misleading liveness 503.

    Must run before `eeg_client.is_alive()`.
    """
    if eeg_poller.INGEST_MODE == "push":
        raise HTTPException(
            409,
            f"This deployment uses push ingestion: the sidecar on the student's "
            f"own device posts to /api/signals/*, and this backend cannot {what}. "
            f"Nothing is wrong with the headband.",
        )


# A pairing its pairer's page has not polled for this long is released: the tab was closed.
_PAIRING_IDLE_SEC = env_number("PAIRING_IDLE_SECONDS", 120.0, float, minimum=15.0)
# The same limit stops a pull poller whose page is gone; pagehide's stop is only the fast path.
eeg_poller.PAGE_IDLE_SECONDS = _PAIRING_IDLE_SEC
# The pairer's polls refresh `seen_at` at most this often, not on every 5 s poll.
_PAIRING_REFRESH_SEC = 30.0
# Owner rows only: a stale one refuses (the safe direction); a stale "free" would admit a takeover.
_PAIRING_CACHE_SEC = 10.0
_pairing_cache: dict[str, tuple[float, str, datetime | None]] = {}
_pairing_cache_lock = threading.Lock()


def _uncache_pairing(device_id: str | None = None, user_id: str | None = None) -> None:
    with _pairing_cache_lock:
        for dev in [d for d, (_t, u, _s) in _pairing_cache.items()
                    if d == device_id or (user_id is not None and u == user_id)]:
            del _pairing_cache[dev]


def _station_pairer(device_id: str):
    """(user_id, seen_at) of this station's pairing, or None. In the database, so a restart agrees; raises on a failed read."""
    now = time.monotonic()
    with _pairing_cache_lock:
        hit = _pairing_cache.get(device_id)
    if hit and now - hit[0] < _PAIRING_CACHE_SEC:
        return hit[1], hit[2]
    rows = supabase.table("station_pairings").select("user_id, seen_at") \
        .eq("device_id", device_id).limit(1).execute().data or []
    if not rows:
        _uncache_pairing(device_id)
        return None
    pairing = (rows[0]["user_id"], _parse_ts(rows[0].get("seen_at")))
    with _pairing_cache_lock:
        _pairing_cache[device_id] = (now, *pairing)
    return pairing


def _record_pairing(user_id: str, device_id: str, session_id: str | None = None) -> None:
    """Raises on failure: an unrecorded pairing would leave the headband open to anyone."""
    _uncache_pairing(device_id)
    supabase.table("station_pairings").upsert(
        {"device_id": device_id, "user_id": user_id, "session_id": session_id,
         "seen_at": _utc_now().isoformat()},
        on_conflict="device_id").execute()


# (user, device) -> monotonic time of this process's last refresh, so a recording student's
# polls refresh without reading the row first.
_pairing_touched: dict[tuple[str, str], float] = {}


def _touch_pairing(user_id: str, device_id: str) -> None:
    """The pairer's page is still open. Never raises: a missed refresh costs one idle interval at most."""
    _uncache_pairing(device_id)
    with _pairing_cache_lock:
        _pairing_touched[(user_id, device_id)] = time.monotonic()
    try:
        supabase.table("station_pairings").update({"seen_at": _utc_now().isoformat()}) \
            .eq("device_id", device_id).eq("user_id", user_id).execute()
    except Exception as e:
        print(f"[eeg] could not refresh the pairing on {device_id}: {type(e).__name__}")


def _touched_recently(user_id: str, device_id: str) -> bool:
    with _pairing_cache_lock:
        at = _pairing_touched.get((user_id, device_id))
    return at is not None and time.monotonic() - at < _PAIRING_REFRESH_SEC


def _release_idle_pairing(device_id: str, owner: str) -> bool:
    """Delete the pairing only if the database still has it idle; whether a row went. Never raises.

    The caller's `seen_at` may be a cached one, so another worker may have refreshed it since.
    """
    _uncache_pairing(device_id)
    cutoff = (_utc_now() - timedelta(seconds=_PAIRING_IDLE_SEC)).isoformat()
    try:
        return bool(supabase.table("station_pairings").delete(returning=ReturnMethod.representation)
                    .eq("device_id", device_id).eq("user_id", owner).lt("seen_at", cutoff).execute().data)
    except Exception as e:
        print(f"[eeg] could not release the idle pairing on {device_id}: {type(e).__name__}")
        return False


def _forget_pairing(device_id: str, user_id: str | None = None) -> None:
    """Clear the station's pairer; with user_id, only if it is still that user. Never raises."""
    _uncache_pairing(device_id)
    try:
        q = supabase.table("station_pairings").delete().eq("device_id", device_id)
        if user_id is not None:
            q = q.eq("user_id", user_id)
        q.execute()
    except Exception as e:
        print(f"[eeg] could not clear the pairing on {device_id}: {type(e).__name__}")


def _forget_session_pairings(user_id: str, session_id: str) -> None:
    """That lesson is over: its session closed or its poller stopped. Never raises.

    Only that session's: the sweep closing an old abandoned one must not free the lesson running now.
    """
    _uncache_pairing(user_id=user_id)
    try:
        supabase.table("station_pairings").delete().eq("user_id", user_id) \
            .eq("session_id", session_id).execute()
    except Exception as e:
        print(f"[eeg] could not clear {user_id[:8]}'s pairing for a session: {type(e).__name__}")


_PAIRER_UNKNOWN = object()


def _station_access(user_id: str, device_id: str) -> tuple[bool, dict | None]:
    """(open, the muse status if one was read, for the caller to reuse rather than fetch again).

    A headband another user paired stays theirs while their page polls and the bridge reports it
    connected; a failed pairing read counts as another user's. Fails closed when the bridge cannot say.
    """
    if not eeg_poller.can_use_device(user_id, device_id):
        return False, None
    if eeg_poller.live_poller_user(device_id) == user_id:
        # Their own poller holds the station: no read. Still refreshed, or a poller stopping
        # mid-lesson (consent withdrawn) leaves a pairing that already looks idle.
        eeg_poller.page_seen(user_id, device_id)
        if not _touched_recently(user_id, device_id):
            _touch_pairing(user_id, device_id)
        return True, None
    try:
        owner, seen = _station_pairer(device_id) or (None, None)
    except Exception as e:
        print(f"[eeg] pairing unreadable on {device_id}: {type(e).__name__}")
        owner, seen = _PAIRER_UNKNOWN, None
    idle = None if seen is None else (_utc_now() - seen).total_seconds()
    if owner is None:
        return True, None
    if owner == user_id:
        if idle is None or idle >= _PAIRING_REFRESH_SEC:
            _touch_pairing(user_id, device_id)
        return True, None
    if (owner is not _PAIRER_UNKNOWN and idle is not None and idle >= _PAIRING_IDLE_SEC
            and _release_idle_pairing(device_id, owner)):
        # Nothing has polled for it: the pairer's page is gone, whatever the headband says.
        return True, None
    try:
        status = eeg_client.get_muse_status(device_id)
        connected = (status.get("ingestion") or {}).get("muse_connected")
    except Exception:
        return False, None
    if connected is False:
        if owner is not _PAIRER_UNKNOWN:
            _forget_pairing(device_id, owner)
        return True, status
    return False, None


def _station_open_to(user_id: str, device_id: str) -> bool:
    """`_station_access` for a caller with no use for the status."""
    return _station_access(user_id, device_id)[0]


_BRIDGE_ERROR = "The headband service did not respond. Try again, or restart the headband."


def _reserve_and_call(user_id: str, device_id: str, fn, *args,
                      session_id: str | None = None):
    """Claim device_id's pre-claim reservation, then run the bridge call.

    `session_id`, if sent, scopes the reservation to that pairing attempt.
    Every failure path releases this device's reservation only.
    """
    if not _station_open_to(user_id, device_id) or not eeg_poller.reserve_device(user_id, device_id, session_id):
        ops_metrics.bump("station_refused", device_id)
        raise HTTPException(403, "Station in use by another user")
    if not eeg_client.is_alive():
        eeg_poller.release_reservation(user_id, device_id)
        raise HTTPException(503, "EEG service not running on port 8001")
    try:
        return fn(*args)
    except Exception as e:
        eeg_poller.release_reservation(user_id, device_id)
        # Logged, not sent: a requests error names the sidecar's internal URL.
        print(f"[eeg] bridge call on {device_id} failed: {e}")
        raise HTTPException(502, _BRIDGE_ERROR)


@app.post("/api/eeg/muse/refresh")
def eeg_muse_refresh(request: Request, body: dict = Body(default={})):
    """Trigger a Bluetooth scan for nearby Muse headbands."""
    user = get_user(request)
    device_id = (body or {}).get("device_id") or eeg_client.DEFAULT_DEVICE_ID
    # Before _reserve_and_call: under push nothing would ever release the reservation.
    _refuse_under_push("scan for headbands")
    return _reserve_and_call(user["id"], device_id, eeg_client.muse_refresh, device_id,
                             session_id=(body or {}).get("session_id"))

@app.post("/api/eeg/muse/connect")
def eeg_muse_connect(request: Request, body: dict = Body(...)):
    """Connect to a specific Muse headband by name."""
    user = get_user(request)
    name = (body.get("name") or "").strip()
    device_id = body.get("device_id") or eeg_client.DEFAULT_DEVICE_ID
    if not name:
        raise HTTPException(400, "Device name required")
    # Before _reserve_and_call -- see eeg_muse_refresh.
    _refuse_under_push("connect to a headband")
    # As /start refuses: a paired headband with no poller would sit readable on an open station.
    consent = _may_record(user["id"])
    if not consent["record_eeg"]:
        raise HTTPException(403, _as_sentence(_not_recording_reason(
            consent,
            "EEG recording is switched off for this student.",
            "Could not check whether EEG recording is allowed, so the headband was not connected.",
            switches=("record_eeg",))))
    out = _reserve_and_call(user["id"], device_id, eeg_client.muse_connect, name, device_id,
                            session_id=body.get("session_id"))
    try:
        sid = body.get("session_id")
        _record_pairing(user["id"], device_id, sid if isinstance(sid, str) else None)
    except Exception as e:
        # Unrecorded, the headband would be anyone's: undo the pairing rather than leave it open.
        print(f"[eeg] could not record the pairing on {device_id}: {type(e).__name__}")
        try:
            eeg_client.muse_disconnect(device_id)
        except Exception:
            pass
        eeg_poller.release_reservation(user["id"], device_id)
        raise HTTPException(503, "Could not record who connected this headband, so it was "
                                 "disconnected. Try again.")
    return out

@app.post("/api/eeg/muse/disconnect")
def eeg_muse_disconnect(request: Request, body: dict = Body(default={})):
    """Tell the native bridge to disconnect from the current headband."""
    user = get_user(request)
    device_id = (body or {}).get("device_id") or eeg_client.DEFAULT_DEVICE_ID
    # Checks, never claims: a claiming teardown could lock a free station forever.
    if not _station_open_to(user["id"], device_id):
        raise HTTPException(403, "Station in use by another user")
    # Disconnecting ends the caller's hold on the station, as /stop does.
    eeg_poller.release_reservation(user["id"], device_id)
    _refuse_under_push("disconnect a headband")
    if not eeg_client.is_alive():
        raise HTTPException(503, "EEG service not running on port 8001")
    try:
        out = eeg_client.muse_disconnect(device_id)
    except Exception as e:
        print(f"[eeg] disconnect on {device_id} failed: {e}")
        raise HTTPException(502, _BRIDGE_ERROR)
    _forget_pairing(device_id)
    return out

@app.get("/api/eeg/devices")
def eeg_devices(request: Request):
    """List the sidecar's registered devices (stations), for the frontend picker.

    Login only: no biometric values or ownership; "station X is in use" is all that leaks.
    """
    get_user(request)
    if eeg_poller.INGEST_MODE == "push":
        return {"available": None, "ingest_mode": "push", "devices": []}
    if not eeg_client.is_alive():
        return {"available": False, "ingest_mode": "pull", "devices": []}
    return {"available": True, "ingest_mode": "pull",
            "devices": eeg_client.list_devices()}

@app.get("/api/eeg/debug")
def eeg_debug(request: Request, device_id: str = eeg_client.DEFAULT_DEVICE_ID):
    """Raw EEG snapshot for local development -- returns the full state from EEGResearch. 404 in production."""
    user = get_user(request)
    if IS_PRODUCTION:
        raise HTTPException(404, "Not Found")
    if eeg_poller.INGEST_MODE == "push":
        return {"available": None, "ingest_mode": "push"}
    if not eeg_client.is_alive():
        return {"available": False, "ingest_mode": "pull"}
    # Security: another user's live station holds their biometric data.
    open_, muse = _station_access(user["id"], device_id)
    if not open_:
        return {"available": False, "reason": "in_use_by_other"}
    # A token misconfiguration raises by design; report it, not a 500.
    try:
        snapshot = eeg_client.get_state(device_id, timeout=1.5)
        muse     = muse or eeg_client.get_muse_status(device_id)
    except RuntimeError as e:
        return {"available": False, "error": str(e)}
    return {"available": True, "snapshot": snapshot, "muse": muse}


@app.get("/api/eeg/health")
def eeg_health():
    """Tells the frontend whether the EEGResearch sidecar service is reachable.

    No login, so a yes or no and nothing else: not the sidecar's URL, not its error text,
    and not the default headband's status, which carries live readings.
    """
    if eeg_poller.INGEST_MODE == "push":
        # None: "not probed in this deployment", not "down".
        return {"available": None, "ingest_mode": "push"}
    if not eeg_client.is_alive():
        return {"available": False, "ingest_mode": "pull"}
    # /healthz is unauthenticated; this call surfaces a token misconfiguration.
    try:
        eeg_client.get_muse_status()
    except RuntimeError as e:
        print(f"[eeg] health probe: {e}")
        return {"available": False, "ingest_mode": "pull",
                "error": "The headband service is not set up correctly on this computer."}
    return {"available": True, "ingest_mode": "pull"}

@app.post("/api/eeg/start")
def eeg_start(payload: EegSessionRequest, request: Request):
    user = get_user(request)
    sess = _session_or_403(payload.session_id, user["id"], "user_id, ended_at")
    if sess.get("ended_at"):
        raise HTTPException(400, "Session already ended")
    _refuse_under_push("start a poller")
    # Before the poller exists; fails closed.
    consent = _may_record(user["id"])
    if not consent["record_eeg"]:
        # 403, not 409: this is about the student or school, not the deployment.
        raise HTTPException(403, _as_sentence(_not_recording_reason(
            consent,
            "EEG recording is switched off for this student.",
            "Could not check whether EEG recording is allowed, so it was not started.",
            switches=("record_eeg",))))
    if not eeg_client.is_alive():
        raise HTTPException(503, "EEG service is not running on port 8001")
    device_id = payload.device_id or eeg_client.DEFAULT_DEVICE_ID
    # A typo'd device_id would otherwise report running with no data.
    # Empty known_ids (a transient list error) allows the start.
    known_ids = {d.get("device_id") for d in eeg_client.list_devices()}
    if known_ids and device_id not in known_ids:
        raise HTTPException(404, f"Unknown device_id: {device_id!r}")
    in_use = HTTPException(
        409,
        "This headband is already in use by another user. Ask them to "
        "disconnect, or wait a few seconds and try again.",
    )
    # Another student's paired headband is theirs, not a free station, past the reservation TTL.
    if not _station_open_to(user["id"], device_id):
        ops_metrics.bump("station_refused", device_id)
        raise in_use
    try:
        out = eeg_poller.start(supabase, user["id"], payload.session_id, device_id,
                               record=payload.record)
    except eeg_poller.ConsentError as e:
        # The gap since `_may_record`, or an unwired check; the exception says which.
        raise HTTPException(403, str(e))
    except eeg_poller.DeviceClaimedError:
        # A live poller or a reservation; both resolve by waiting.
        ops_metrics.bump("station_refused", device_id)
        raise in_use
    # A tab reuses a live headband across sessions without reconnecting, so the pairing follows
    # the session now recording; left on the old one, that session's close would release it.
    # Safe to overwrite: the gate above admitted the caller. The poller holds the station anyway.
    try:
        _record_pairing(user["id"], device_id, payload.session_id)
    except Exception as e:
        print(f"[eeg] could not move the pairing on {device_id} to this session: {type(e).__name__}")
    _mark_eeg_started(payload.session_id, device_id)
    return {"ok": True, **out}


class EegStartedReport(BaseModel):
    # Lenient, like the ingest models: this body rides with the headband-start stamp, and a 422 would lose the
    # stamp `signals_missing` needs. The kit version is the page's claim, checked by the handler, gating nothing.
    model_config = ConfigDict(extra="ignore")
    kit_version: object = None


_KIT_VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}")


def _record_kit_report(session_id: str, kit_version: str | None) -> None:
    """That the push report arrived (`kit_reported_at`, which the admin counts key on), and the version it named, if
    any. Never raises: it is for the admin page alone."""
    patch = {"kit_reported_at": _utc_now().isoformat(), **({"kit_version": kit_version} if kit_version else {})}
    try:
        supabase.table("sessions").update(patch).eq("id", session_id).execute()
    except Exception as e:                                     # noqa: BLE001
        print(f"[eeg] could not record the kit report for {session_id}: {type(e).__name__}")


@app.post("/api/sessions/{session_id}/eeg-started")
def session_eeg_started(session_id: str = Path(...), request: Request = None,
                        payload: EegStartedReport | None = None):
    """Under push, the page reports that the sidecar is streaming a headband for this session.

    Push's counterpart to the stamp `/api/eeg/start` writes, which `signals_missing` needs: the backend never sees a
    push start. Owner only, idempotent; a closed session is refused. A page from before the kit version sends no body.
    """
    user = get_user(request)
    session = _session_or_403(session_id, user["id"], "user_id, ended_at")
    if session.get("ended_at"):
        raise HTTPException(409, "This session has ended")
    _mark_eeg_started(session_id)
    # The field present is the page saying its sidecar answered (null: it named no version); absent, it could not
    # ask, and an unreadable value is no answer either. Only an answer is counted, never "no answer" as "no version".
    answered = payload is not None and "kit_version" in payload.model_fields_set
    version = payload.kit_version if answered else None
    if version is not None and not (isinstance(version, str) and _KIT_VERSION.fullmatch(version)):
        print(f"[eeg] dropped a kit version that is not x.y.z for {session_id}")
        answered = False
    if answered:
        _record_kit_report(session_id, version)
    return {"ok": True}

@app.post("/api/eeg/stop")
def eeg_stop(payload: EegSessionRequest, request: Request):
    user = get_user(request)
    _session_or_403(payload.session_id, user["id"])
    # Also releases the caller's reservation, poller or not.
    out = eeg_poller.stop(payload.session_id, user["id"])
    _forget_session_pairings(user["id"], payload.session_id)
    return {"ok": True, **out}

@app.get("/api/eeg/status")
def eeg_status(request: Request, device_id: str = eeg_client.DEFAULT_DEVICE_ID):
    user = get_user(request)
    # Under push there is no sidecar route; `is_alive()` would read as an outage.
    push = eeg_poller.INGEST_MODE == "push"
    # Before the muse probe, which raises without EEG_API_TOKEN (normal under push).
    if push:
        # None: "not probed in this deployment", not "absent".
        muse = {"available": None, "reason": "push_ingestion"}
    else:
        open_, read = _station_access(user["id"], device_id)
        if not open_:
            muse = {"available": False, "reason": "in_use_by_other"}
        else:
            # At most one sidecar read per poll: the gate's own, when it had to ask.
            muse = read or eeg_client.get_muse_status(device_id)
    return {
        "service": None if push else eeg_client.is_alive(),
        "ingest_mode": eeg_poller.INGEST_MODE,
        "muse":    muse,
        "poller":  _poller_status(user["id"], push=push),
    }


# ─── parent endpoints ────────────────────────────────────────────────────

# 10 redemptions/hour per account: makes guessing visible (each refusal is audited).
_LINK_CODE_LIMITER = _SlidingWindowLimiter("parent_link_code", 10, 3600.0)


@app.post("/api/parent/link-child")
def link_child(payload: LinkChildRequest, request: Request):
    """Link this parent to the child whose code they were given.

    A user id is not a secret; the code is. Notify, not block: the child is told after.
    """
    user = get_user(request)
    if _role(user["id"]) != "parent":
        raise HTTPException(403, "Only parents can link children")

    wait = _LINK_CODE_LIMITER.check(user["id"])
    if wait is not None:
        _record_security_event("rate_limited", user["id"],
                               limiter=_LINK_CODE_LIMITER.name)
        raise HTTPException(429, "Too many attempts. Ask your child for a new "
                                 "code and try again later.",
                            headers={"Retry-After": str(wait)})

    code = payload.link_code.strip().upper()
    refused = HTTPException(404, "That code is not valid or has expired. Ask "
                                 "your child to create a new one.")
    unavailable = HTTPException(503, "Could not check that code just now. Try "
                                     "again in a moment.")

    # The conditional delete is the claim: only one of two racing requests gets the row.
    # `returning` named explicitly: under `minimal` every good code would read as unknown.
    try:
        claimed = supabase.table("parent_link_codes") \
            .delete(returning=ReturnMethod.representation) \
            .eq("code", code).gt("expires_at", _utc_now().isoformat()) \
            .execute().data or []
    except Exception as e:                                     # noqa: BLE001
        # Fails closed as 503, not "not valid".
        print(f"[link-child] could not claim the code: {e}")
        raise unavailable

    if not claimed:
        # One message for unknown, expired and spent: the difference leaks.
        _record_security_event("authz_denied", user["id"],
                               check="parent_link_code")
        raise refused

    row = claimed[0]
    child_id = str(row["student_id"])

    def _give_back():
        # Never upserted: a newer code the child made must not be replaced.
        try:
            supabase.table("parent_link_codes").insert({
                k: row[k] for k in ("code", "student_id", "created_at", "expires_at")
                if k in row}).execute()
        except Exception as e:                                 # noqa: BLE001
            print(f"[link-child] a claimed code could not be given back: {e}")

    # Re-check the role; not via `_role`, which answers "student" on a failed read.
    try:
        prof = (supabase.table("profiles").select("role, display_name")
                .eq("id", child_id).limit(1).execute().data or [None])[0]
    except Exception as e:                                     # noqa: BLE001
        print(f"[link-child] could not read the child's profile: {e}")
        _give_back()
        raise unavailable
    if not prof or prof.get("role") != "student":
        # Spent, not given back; its own check so the log doesn't count it as guessing.
        _record_security_event("authz_denied", user["id"],
                               check="parent_link_code_not_student")
        raise refused

    def _linked():
        return bool(supabase.table("parent_child_links").select("id")
                    .eq("parent_id", user["id"]).eq("child_id", child_id)
                    .execute().data)

    try:
        already = _linked()
    except Exception as e:                                     # noqa: BLE001
        print(f"[link-child] could not check for an existing link: {e}")
        _give_back()
        raise unavailable
    if already:
        _give_back()
        raise HTTPException(409, "Already linked to this child")

    try:
        supabase.table("parent_child_links").insert({
            "parent_id": user["id"],
            "child_id":  child_id,
        }).execute()
    except Exception as e:                                     # noqa: BLE001
        # Never given back from here: a write that raised may still commit.
        print(f"[link-child] the link write raised: {e}")
        try:
            exists = _linked()
        except Exception:                                      # noqa: BLE001
            exists = False
        if not exists:
            raise HTTPException(503, "Could not confirm the link. If this child "
                                     "is not in your list, ask them for a new code.")

    return {"ok": True, "child_id": child_id,
            "child_name": prof.get("display_name") or "Student"}


@app.delete("/api/parent/children/{child_id}")
def unlink_child(child_id: str, request: Request):
    """Remove one of the caller's own parent-child links (scoped by `parent_id`).

    Consent and recorded signals are untouched. 404 if no such link.
    """
    user = get_user(request)
    res = supabase.table("parent_child_links").delete(returning=ReturnMethod.representation)         .eq("parent_id", user["id"]).eq("child_id", child_id).execute()
    if not res.data:
        raise HTTPException(404, "Not linked to this child")
    return {"ok": True, "child_id": child_id}


@app.get("/api/student/parent-links")
def my_unacknowledged_parent_links(request: Request):
    """Links to this student that they have not been told about yet.

    Notify, not block. Fails open to an empty list: it only draws a banner.
    """
    user = get_user(request)
    try:
        rows = supabase.table("parent_child_links")             .select("id, parent_id, created_at")             .eq("child_id", user["id"]).is_("student_ack_at", "null")             .order("created_at", desc=True).limit(10).execute().data or []
    except Exception as e:
        print(f"[parent-links] {user['id']}: {e}")
        return {"links": [], "retrieved": False}
    names = _profiles_many([r["parent_id"] for r in rows])
    return {
        "links": [{
            "id":          r["id"],
            "parent_name": (names.get(r["parent_id"]) or {}).get("display_name") or "A parent",
            "linked_at":   r["created_at"],
        } for r in rows],
        "retrieved": True,
    }


@app.post("/api/student/parent-links/ack")
def ack_parent_links(request: Request):
    """The student has seen the notice: stamps all of the caller's unacknowledged links.

    Scoped by `child_id`, never a client-supplied link id. 404 if nothing matched.
    """
    user = get_user(request)
    try:
        written = supabase.table("parent_child_links")             .update({"student_ack_at": _utc_now().isoformat()}, returning=ReturnMethod.representation)             .eq("child_id", user["id"]).is_("student_ack_at", "null")             .execute().data or []
    except Exception as e:
        print(f"[parent-links:ack] {user['id']}: {e}")
        raise HTTPException(500, "Could not acknowledge")
    if not written:
        raise HTTPException(404, "Nothing to acknowledge")
    return {"ok": True, "acknowledged": len(written)}


@app.post("/api/student/link-code")
def create_parent_link_code(request: Request):
    """Make a code this student can give a parent, replacing any outstanding one.

    Students only. One code at a time: upserted on `student_id` in one statement.
    """
    user = get_user(request)
    # Not `_role`, which answers "student" on a failed read or a missing row.
    try:
        role = _role_or_raise(user["id"])
    except Exception as e:
        print(f"[link_code] role unreadable for {user['id'][:8]}: {type(e).__name__}")
        raise HTTPException(503, "A code cannot be made right now. Try again soon.")
    if role != "student":
        raise HTTPException(403, "Only a student can create a code for their own account")
    code = _new_link_code()
    expires = _utc_now() + timedelta(seconds=_LINK_CODE_TTL_SEC)
    try:
        supabase.table("parent_link_codes").upsert({
            "code":       code,
            "student_id": user["id"],
            "created_at": _utc_now().isoformat(),
            "expires_at": expires.isoformat(),
        }, on_conflict="student_id").execute()
    except Exception as e:                                     # noqa: BLE001
        # Raises: an unstored code would be refused at redemption.
        print(f"[link-code] could not store a code for {user['id'][:8]}: {e}")
        raise HTTPException(503, "Could not create a code just now. Try again "
                                 "in a moment.")
    return {"code": code, "expires_at": expires.isoformat()}


@app.get("/api/student/link-code")
def my_parent_link_code(request: Request):
    """The outstanding code, if there is one and it has not expired.

    `retrieved: False` is a failed read; `code: null` with `retrieved: True` is none.
    """
    user = get_user(request)
    try:
        rows = supabase.table("parent_link_codes").select("code, expires_at") \
            .eq("student_id", user["id"]).limit(1).execute().data or []
    except Exception as e:                                     # noqa: BLE001
        print(f"[link-code] could not read the code for {user['id'][:8]}: {e}")
        return {"code": None, "expires_at": None, "retrieved": False}
    row = rows[0] if rows else None
    expires = _parse_ts(row.get("expires_at")) if row else None
    # An expired row lingers until the nightly sweep; redemption refuses it.
    if row is None or expires is None or expires <= _utc_now():
        return {"code": None, "expires_at": None, "retrieved": True}
    return {"code": row["code"], "expires_at": row["expires_at"],
            "retrieved": True}


# Rows read for one parent's banner (it dedupes to one line per channel anyway).
_MAX_WITHDRAWAL_NOTICES = 200


class ConsentNoticeAck(StrictModel):
    """`{child_id: iso8601}`: the server-given watermark the parent was shown, per child."""
    # Capped: the only field a client may grow, and the handler loops over it.
    through: dict[str, str] = Field(default={}, max_length=200)


CONSENT_CHANNEL_LABELS = {
    "eeg":              "the headband",
    "headband_optical": "the headband's heart-rate sensor",
    "camera":           "the camera",
}


@app.get("/api/parent/consent-notices")
def parent_consent_notices(request: Request):
    """What a linked child, or another parent account, changed since this parent last looked.

    From append-only logs, not `*_revoked_at` (nulled on re-enable), and acked
    per (parent, child) link. Fails open to [] with `retrieved: false`.
    """
    user = get_user(request)
    try:
        links = supabase.table("parent_child_links").select("child_id, parent_ack_at")             .eq("parent_id", user["id"]).execute().data or []
    except Exception as e:
        print(f"[consent-notices] {user['id']}: {e}")
        return {"notices": [], "retrieved": False}
    if not links:
        return {"notices": [], "retrieved": True}

    ids = [l["child_id"] for l in links]
    try:
        # The child's own withdrawals only: one a parent made is not "<child> turned off".
        # A student can withdraw only their own consent, so both filters on `ids` are exact.
        rows = supabase.table("consent_withdrawals")             .select("user_id, channel, withdrawn_at, withdrawn_by")             .in_("user_id", ids).in_("withdrawn_by", ids)             .order("withdrawn_at", desc=True)             .limit(_MAX_WITHDRAWAL_NOTICES).execute().data or []
        # Another parent account's link, turn-on or erasure: a second "parent" account is
        # otherwise how the one-way rules are undone with only the child ever told.
        # All or nothing: acking a partial feed would mark the unread part seen.
        other_links = supabase.table("parent_child_links")             .select("child_id, parent_id, created_at")             .in_("child_id", ids).neq("parent_id", user["id"]).execute().data or []
        enables = supabase.table("consent_enablements")             .select("user_id, channel, enabled_at, enabled_by")             .in_("user_id", ids).neq("enabled_by", user["id"])             .order("enabled_at", desc=True)             .limit(_MAX_WITHDRAWAL_NOTICES).execute().data or []
        # One row per (student, channel); `erased_by` goes null with its account, so filtered here.
        erasures = supabase.table("signal_erasure")             .select("user_id, channel, erased_at, erased_by")             .in_("user_id", ids).execute().data or []
    except Exception as e:
        print(f"[consent-notices] {user['id']}: {e}")
        return {"notices": [], "retrieved": False}

    names = _profiles_many(ids)
    since_by_child = {l["child_id"]: l.get("parent_ack_at") for l in links}

    def unseen(cid, stamp):
        since = since_by_child.get(cid)
        # Lexical compare is fine: both are PostgREST UTC, and this is advisory.
        return bool(stamp) and not (since and stamp <= since)

    by_child: dict[str, list[dict]] = {}
    for r in rows:
        cid = r["user_id"]
        stamp = r["withdrawn_at"]
        if not unseen(cid, stamp):
            continue
        # One line per channel, newest first.
        seen = by_child.setdefault(cid, [])
        if any(c["channel"] == r["channel"] for c in seen):
            continue
        seen.append({"channel": r["channel"],
                     "label": CONSENT_CHANNEL_LABELS.get(r["channel"], r["channel"]),
                     "at": stamp})

    # Never who: the account is the child's to name, so a name would be the attacker's word.
    changes: dict[str, list[dict]] = {}
    for r in other_links:
        if unseen(r["child_id"], r.get("created_at")):
            changes.setdefault(r["child_id"], []).append(
                {"kind": "parent_linked", "at": r["created_at"]})
    for kind, found, stamp_col, by_col in (("channel_enabled", enables, "enabled_at", "enabled_by"),
                                           ("channel_erased", erasures, "erased_at", "erased_by")):
        for r in found:
            cid, stamp = r["user_id"], r.get(stamp_col)
            if r.get(by_col) == user["id"] or not unseen(cid, stamp):
                continue
            listed = changes.setdefault(cid, [])
            # One line per channel and kind, newest first.
            if any(c["kind"] == kind and c["channel"] == r["channel"] for c in listed):
                continue
            listed.append({"kind": kind, "channel": r["channel"],
                           "label": CONSENT_CHANNEL_LABELS.get(r["channel"], r["channel"]),
                           "at": stamp})

    notices = []
    for cid in [*by_child, *(c for c in changes if c not in by_child)]:
        channels = by_child.get(cid, [])
        parent_changes = sorted(changes.get(cid, []), key=lambda c: c["at"], reverse=True)
        notices.append({
            "child_id":       cid,
            "child_name":     (names.get(cid) or {}).get("display_name") or "Your child",
            "channels":       channels,
            "parent_changes": parent_changes,
            # Watermark the client hands back on acknowledgement.
            "through":        max(c["at"] for c in [*channels, *parent_changes]),
        })
    return {"notices": notices, "retrieved": True}


@app.post("/api/parent/consent-notices/ack")
def ack_parent_consent_notices(payload: ConsentNoticeAck, request: Request):
    """The parent has seen these notices, up to the point they were shown.

    Stamps the given watermark, not `now()`, so an unseen withdrawal stays unseen.
    Scoped by `parent_id`; idempotent, so no 404.
    """
    user = get_user(request)
    try:
        for child_id, through in (payload.through or {}).items():
            supabase.table("parent_child_links")                 .update({"parent_ack_at": through})                 .eq("parent_id", user["id"]).eq("child_id", child_id).execute()
    except Exception as e:
        print(f"[consent-notices:ack] {user['id']}: {e}")
        raise HTTPException(500, "Could not acknowledge")
    return {"ok": True}


# The parent dashboard's week: its averages and its erasure window must be the same days.
_PARENT_SUMMARY_DAYS = 7


@app.get("/api/parent/children")
def my_children(request: Request, include_face: bool = True):
    """A parent's linked children with their headline signal averages.

    Consent is resolved per child: children are grouped by flag pair, so no
    declined channel is read under a sibling's permission.
    """
    user = get_user(request)
    links = supabase.table("parent_child_links").select("child_id, created_at") \
        .eq("parent_id", user["id"]).execute()
    child_ids = [lnk["child_id"] for lnk in (links.data or [])]
    # One consent read for every child; fails closed per child, as `_consent` does.
    channels_by_child = _reportable_channels_many(child_ids, include_face)
    # Keyed on the flags alone: `consent_retrieved` doesn't change the query.
    by_channels: dict[tuple[bool, bool], list[str]] = {}
    for cid, ch in channels_by_child.items():
        by_channels.setdefault((ch.heart, ch.emotion), []).append(cid)

    summaries: dict | None = {}
    since = _window_start(_PARENT_SUMMARY_DAYS)
    for (heart_flag, emotion_flag), group in by_channels.items():
        part = _signal_summaries(group, days=_PARENT_SUMMARY_DAYS, include_heart=heart_flag,
                                 include_emotion=emotion_flag,
                                 channels_by_student=channels_by_child)
        if part is None:
            # One failed group fails the whole call: one flag can't describe a partial read.
            summaries = None
            break
        summaries.update(part)
    # None is a failed read; {} found nothing. The fallback says which.
    summaries_retrieved = summaries is not None
    summaries = summaries or {}
    usual_start, usual_end = _report_period(_PARENT_SUMMARY_DAYS)
    child_summaries = {str(cid): summaries.get(str(cid)) or {"retrieved": summaries_retrieved}
                       for cid in child_ids}
    # One batched row check for every child, then one read per running lesson.
    context = _lesson_context(child_summaries,
                              {str(cid): ch.heart for cid, ch in channels_by_child.items()},
                              _PARENT_SUMMARY_DAYS)
    usual = _personal_baselines_many(
        {cid: {**_usual_current(s), **context[cid]} for cid, s in child_summaries.items()},
        {str(cid): ch.heart for cid, ch in channels_by_child.items()},
        {str(cid): ch.consent_retrieved for cid, ch in channels_by_child.items()},
        usual_start, usual_end)
    children = []
    kids = [lnk["child_id"] for lnk in (links.data or [])]
    all_stats = _stats_including_open_session_many(kids)
    profiles = _profiles_many(kids)
    all_perf = _topic_performance_many(kids)
    # "Top five per child" in one call; it has no PostgREST form, so it is a function.
    try:
        recent = _recent_sessions_many(kids, 5)
    except Exception as e:                                      # noqa: BLE001
        # `None` per child, as `class_sessions` sends: an empty list would say "no sessions".
        print(f"[parent:children] could not read recent sessions: {e}")
        recent = None
    for lnk in (links.data or []):
        cid = lnk["child_id"]
        stats = all_stats.get(cid) or {}
        p = profiles.get(cid) or {}
        children.append({
            "user_id":     cid,
            "name":        p.get("display_name") or "Student",
            "email":       p.get("email") or "",
            "linked_at":   lnk["created_at"],
            "stats":       stats,
            "sessions":    None if recent is None else recent.get(cid, []),
            "performance": all_perf.get(cid) or [],
            # Headline averages only, not the full weekly report.
            "signal_summary": summaries[str(cid)]
                              if str(cid) in summaries
                              else _shape_summary(None,
                                                channels_by_child[cid].heart,
                                                channels_by_child[cid].emotion,
                                                summaries_retrieved,
                                                channels_by_child[cid].consent_retrieved,
                                                emotion_revoked_at=channels_by_child[cid].emotion_revoked_at,
                                                heart_revoked_at=channels_by_child[cid].heart_revoked_at,
                                                eeg_enabled=channels_by_child[cid].eeg,
                                                eeg_revoked_at=channels_by_child[cid].eeg_revoked_at,
                                                erased=_erased_fields(channels_by_child[cid], since)),
        })
        children[-1]["signal_summary"] = {**children[-1]["signal_summary"],
                                          "usual": usual.get(str(cid))}
    return children


# ─── admin ───────────────────────────────────────────────────────────────
# Gated on `_require_admin`, which reads `profiles.role`, never `user_metadata.role`.
# See CLAUDE.md "Admin is a role".


def _require_admin(request: Request) -> dict:
    """The caller, if they are an admin. 401 without a token, 403 without the role, 503 if unread."""
    user = get_user(request)
    try:
        role = _role_or_raise(user["id"])
    except Exception as e:                                     # noqa: BLE001
        # Still refused, but not logged: an outage is not someone reaching for the console.
        print(f"[admin] could not read {user['id'][:8]}'s role: {e}")
        raise _read_failed("Could not check admin access; try again")
    if role != ADMIN_ROLE:
        # Own kind, not `authz_denied`. `.url` read defensively: the audit must
        # never break the 403 it records.
        _record_security_event(
            "admin_denied", user["id"],
            path=getattr(getattr(request, "url", None), "path", None))
        raise HTTPException(403, "Admin access required")
    return user


class FeatureFlagUpdate(StrictModel):
    enabled: bool
    # Only read when disabling `consent_enforcement_enabled`; capped below.
    bypass_minutes: int | None = None


# Longest single bypass; re-arming is a deliberate, audited act.
_MAX_BYPASS_MINUTES = 240


@app.get("/api/admin/me")
def admin_me(request: Request):
    """200 for an admin, 403 for anyone else. What the frontend guard calls."""
    user = _require_admin(request)
    return {"user_id": user["id"], "is_admin": True}


def _flag_rows() -> list:
    """Every known flag as a response row, in `_FEATURE_FLAG_DEFAULTS` order.

    A key missing from the table still appears, with the default in use.
    """
    flags = _feature_flags()
    try:
        rows = {r["key"]: r for r in
                (supabase.table("feature_flags").select("*").execute().data or [])
                if r.get("key")}
    except Exception as e:
        print(f"[admin:flags] {e}")
        rows = {}

    out = []
    for key in _FEATURE_FLAG_DEFAULTS:
        row = rows.get(key, {})
        out.append({
            "key": key,
            "enabled": flags[key]["enabled"],
            "bypass_until": flags[key]["bypass_until"],
            "description": row.get("description"),
            "updated_at": row.get("updated_at"),
            # No row: the default is answering, nobody set it.
            "is_default": key not in rows,
        })
    return out


@app.get("/api/admin/flags")
def admin_flags(request: Request):
    _require_admin(request)
    return {"flags": _flag_rows(),
            "consent_enforcement_active": _consent_enforcement_active()}


@app.put("/api/admin/flags/{key}")
def admin_set_flag(key: str, request: Request, payload: FeatureFlagUpdate):
    """Set one flag, audit the change, and drop the cache. 404 for an undeclared key."""
    user = _require_admin(request)
    if key not in _FEATURE_FLAG_DEFAULTS:
        raise HTTPException(404, f"Unknown flag {key!r}")

    bypass_until = None
    if key == CONSENT_ENFORCEMENT_FLAG and not payload.enabled:
        minutes = payload.bypass_minutes
        # Required, never defaulted: the admin chooses the duration.
        if minutes is None:
            raise HTTPException(
                422, "bypass_minutes is required when disabling consent enforcement")
        if minutes < 1 or minutes > _MAX_BYPASS_MINUTES:
            raise HTTPException(
                422, f"bypass_minutes must be between 1 and {_MAX_BYPASS_MINUTES}")
        bypass_until = (_utc_now() + timedelta(minutes=minutes)).isoformat()

    before = _feature_flags().get(key, {})
    row = {"key": key, "enabled": payload.enabled, "bypass_until": bypass_until,
           "updated_by": user["id"], "updated_at": _utc_now().isoformat()}
    try:
        supabase.table("feature_flags").upsert(row, on_conflict="key").execute()
    except Exception as e:
        print(f"[admin:set_flag] {e}")
        raise HTTPException(500, "Could not update the flag")

    # After the write and before the audit, so a failing audit can't leave it stale.
    _feature_flags_cache_clear()

    try:
        supabase.table("feature_flag_changes").insert({
            "key": key,
            "old_enabled": before.get("enabled"),
            "new_enabled": payload.enabled,
            "bypass_until": bypass_until,
            "changed_by": user["id"],
        }).execute()
    except Exception as e:
        # Never raises: the flag is already set.
        print(f"[admin:audit] {key} change not recorded: {e}")

    return {"flags": _flag_rows(),
            "consent_enforcement_active": _consent_enforcement_active()}


@app.get("/api/admin/flags/{key}/history")
def admin_flag_history(key: str, request: Request, limit: int = 20):
    _require_admin(request)
    if key not in _FEATURE_FLAG_DEFAULTS:
        raise HTTPException(404, f"Unknown flag {key!r}")
    limit = max(1, min(limit, 100))
    try:
        rows = supabase.table("feature_flag_changes").select("*") \
            .eq("key", key).order("changed_at", desc=True) \
            .limit(limit).execute().data or []
    except Exception as e:
        print(f"[admin:history] {e}")
        return {"key": key, "changes": [], "retrieved": False}

    names = _display_names({r.get("changed_by") for r in rows if r.get("changed_by")})
    return {"key": key, "retrieved": True, "changes": [{
        "changed_at": r.get("changed_at"),
        "old_enabled": r.get("old_enabled"),
        "new_enabled": r.get("new_enabled"),
        "bypass_until": r.get("bypass_until"),
        "changed_by": names.get(r.get("changed_by"), "Unknown"),
    } for r in rows]}


def _display_names(user_ids) -> dict:
    """Names for a set of ids, for the audit view. Never raises."""
    ids = [u for u in user_ids if u]
    if not ids:
        return {}
    try:
        rows = supabase.table("profiles").select("id, display_name") \
            .in_("id", ids).execute().data or []
        return {r["id"]: r.get("display_name") or "Unknown" for r in rows}
    except Exception as e:
        print(f"[admin:names] {e}")
        return {}


# Named, never enumerated: `os.environ` also holds the service-role key.
_DEPLOYMENT_FLAGS = (
    ("INGEST_MODE", "pull", "Whether the backend polls the sidecar, or the sidecar posts here."),
    ("EEG_API_URL", None, "Where the EEG sidecar is expected, under pull ingestion."),
    ("FACE_ENABLED", None, "Camera capture, in the sidecar's own environment."),
    ("FACE_EMOTION_ENABLED", None, "FER+ emotion classification, in the sidecar."),
    ("FACE_GAZE_ENABLED", None, "Gaze and head pose, in the sidecar."),
    ("FACE_HEART_ENABLED", None, "Camera rPPG. Validated and rejected -- expected off."),
    ("MUSE_ENABLE_OPTICS", None, "The headband's optical channels, read by the native bridge."),
    ("MUSE_OPTICS_PRESET", None, "Which optics rung the bridge asks for."),
)


@app.get("/api/admin/env-flags")
def admin_env_flags(request: Request):
    """The process's env-var switches, read-only.

    Several belong to the sidecar's environment: null means "not set for the backend".
    """
    _require_admin(request)
    return {"flags": [{
        "key": key,
        "value": os.getenv(key, default),
        "description": description,
        "editable": False,
    } for key, default, description in _DEPLOYMENT_FLAGS]}


class RetentionWindowUpdate(StrictModel):
    enforced: bool
    starts_on: str | None = Field(None, max_length=_SHORT_MAX)
    ends_on: str | None = Field(None, max_length=_SHORT_MAX)
    timezone: str = Field("UTC", max_length=_TIMEZONE_MAX)
    # Sent only after the 409 that names the delete cutoff this save would move.
    confirm_expiry: bool = False


@app.get("/api/admin/retention-window")
def admin_get_retention_window(request: Request):
    """The school year as configured, plus where today sits in it."""
    _require_admin(request)
    window = _retention_window()
    try:
        rows = supabase.table("retention_window").select("*").limit(1).execute().data or []
    except Exception as e:
        print(f"[admin:window] {e}")
        rows = []
    row = rows[0] if rows else {}
    return {"state": window["state"],
            "configured": bool(rows),
            "enforced": row.get("enforced"),
            "starts_on": row.get("starts_on"),
            "ends_on": row.get("ends_on"),
            "timezone": row.get("timezone") or "UTC"}


def _confirm_expiry_move(starts, ends, tz_name: str, confirmed: bool,
                         enforced: bool = True) -> None:
    """409 unless confirmed, when these dates move the delete cutoff later with today outside them.

    The nightly delete that cutoff drives cannot be undone: a mistyped year would clear this
    year's rows. An unenforced year has no cutoff, so saving one never asks.
    """
    today = _utc_now().astimezone(ZoneInfo(tz_name)).date()
    new_cutoff = _expiry_cutoff(starts, ends, today, enforced=enforced)
    if new_cutoff is None:
        return                      # unenforced or no usable dates: nothing is deleted
    current = _expired_through(today)
    moves_later = current in (None, date.max) or new_cutoff > current
    inside = date.fromisoformat(str(starts)) <= today <= date.fromisoformat(str(ends))
    if confirmed or not moves_later or inside:
        return
    raise HTTPException(409, (
        f"Today is outside these dates, so saving them lets the nightly job delete "
        f"per-sample signal rows up to {new_cutoff.isoformat()}; that cannot be undone. "
        "Check the years, then confirm to save anyway."))


@app.put("/api/admin/retention-window")
def admin_set_retention_window(request: Request, payload: RetentionWindowUpdate):
    """Replace the school-year row. Validated here for a 422; the CHECKs stay as backup."""
    user = _require_admin(request)

    try:
        ZoneInfo(payload.timezone)
    except Exception:
        # An unknown zone would make `_retention_window` deny all recording.
        raise HTTPException(422, f"Unknown timezone {payload.timezone!r}")

    starts, ends = payload.starts_on, payload.ends_on
    if payload.enforced:
        if not starts or not ends:
            raise HTTPException(
                422, "starts_on and ends_on are required when the year is enforced")
        try:
            starts_d, ends_d = date.fromisoformat(starts), date.fromisoformat(ends)
        except ValueError:
            raise HTTPException(422, "starts_on and ends_on must be YYYY-MM-DD")
        if ends_d <= starts_d:
            raise HTTPException(422, "ends_on must be after starts_on")
    _confirm_expiry_move(starts, ends, payload.timezone, payload.confirm_expiry,
                         enforced=payload.enforced)

    row = {"id": True, "enforced": payload.enforced,
           "starts_on": starts or None, "ends_on": ends or None,
           "timezone": payload.timezone,
           "updated_by": user["id"],
           "updated_at": _utc_now().isoformat()}
    try:
        supabase.table("retention_window").upsert(row, on_conflict="id").execute()
    except Exception as e:
        print(f"[admin:set_window] {e}")
        raise HTTPException(500, "Could not update the school year")

    _retention_cache_clear()
    return admin_get_retention_window(request)


# Flowing / stale thresholds (s), shared with `class_live` so both pages agree.
_LIVE_WINDOW_SEC = 90
_STALE_AFTER_SEC = 600

# Platform-wide open-session cap; the payload reports when it bites.
_ADMIN_LIVE_SESSION_CAP = 200

# A failed read; distinct from None ("nothing has ever arrived").
_TS_UNREADABLE = object()


def _latest_signal_ts(session_ids: list[str]) -> dict:
    """Newest timestamp per session per channel, and nothing else.

    `{session_id: {"eeg": ts|None|_TS_UNREADABLE, "camera": ...}}`. The function returns
    timestamps alone, so readings never leave the database. One read per channel, so one
    channel failing leaves the other read.
    """
    out = {sid: {} for sid in session_ids}
    if not session_ids:
        return out
    for name, channel in (("eeg", "cognitive"), ("camera", "face")):
        try:
            rows = supabase.rpc("latest_signal_ts_for_sessions",
                                {"p_session_ids": list(session_ids),
                                 "p_channel": channel}).execute().data or []
            newest = {r.get("session_id"): r.get("ts") for r in rows}
            for sid in session_ids:
                out[sid][name] = newest.get(sid)
        except Exception as e:                                  # noqa: BLE001
            print(f"[admin:live:{channel}] {e}")
            for sid in session_ids:
                out[sid][name] = _TS_UNREADABLE
    return out


@app.get("/api/admin/live-signals")
def admin_live_signals(request: Request):
    """Whether signals are *arriving* for each open session. Not what they say.

    Privacy: only the newest timestamp per channel leaves; no readings.
    """
    _require_admin(request)

    now = _utc_now()
    live_cutoff = now - timedelta(seconds=_LIVE_WINDOW_SEC)
    stale_cutoff = now - timedelta(seconds=_STALE_AFTER_SEC)

    try:
        sessions = supabase.table("sessions").select("id, user_id, started_at") \
            .is_("ended_at", "null").order("started_at", desc=True) \
            .limit(_ADMIN_LIVE_SESSION_CAP).execute().data or []
    except Exception as e:
        print(f"[admin:live] {e}")
        return {"sessions": [], "retrieved": False}

    names = _display_names({s.get("user_id") for s in sessions})
    stamps = _latest_signal_ts([s["id"] for s in sessions])

    def _channel(raw):
        """A channel's liveness; `seen` None is a failed read, False never reported."""
        if raw is _TS_UNREADABLE:
            return {"flowing": False, "stale": False, "seen": None}
        ts = _parse_ts(raw)
        if ts is None:
            return {"flowing": False, "stale": False, "seen": False}
        return {"flowing": ts >= live_cutoff,
                "stale": ts < stale_cutoff,
                "seen": True,
                # Lets the dashboard pulse on a new sample, not every poll.
                "last_ts": raw}

    out = []
    for s in sessions:
        seen = stamps.get(s["id"], {})
        out.append({
            "session_id": s["id"],
            "student_id": s.get("user_id"),
            "student_name": names.get(s.get("user_id"), "Student"),
            "started_at": s.get("started_at"),
            "eeg": _channel(seen.get("eeg")),
            "camera": _channel(seen.get("camera")),
        })
    return {"sessions": out, "retrieved": True,
            "capped": len(sessions) >= _ADMIN_LIVE_SESSION_CAP}


@app.get("/api/admin/health")
def admin_health(request: Request):
    """One place to see whether the moving parts are moving.

    `ok` / `degraded` / `unknown`; a check that could not run is `unknown`, never `ok`.
    """
    _require_admin(request)

    checks = []

    mode = eeg_poller.INGEST_MODE
    if mode == "push":
        # Not a fault: under push there is no route to the sidecar.
        checks.append({"key": "eeg_sidecar", "status": "unknown",
                       "detail": "Not probed: this deployment uses push ingestion."})
    else:
        try:
            alive = eeg_client.is_alive()
            checks.append({"key": "eeg_sidecar",
                           "status": "ok" if alive else "degraded",
                           "detail": eeg_client.EEG_API_URL if alive
                                     else f"Not answering on {eeg_client.EEG_API_URL}"})
        except Exception as e:
            checks.append({"key": "eeg_sidecar", "status": "unknown",
                           "detail": f"Could not probe: {e}"})

    checks.append({"key": "ingest_mode", "status": "ok", "detail": mode})

    window = _retention_window()
    meaning = _WINDOW_STATES.get(window["state"])
    checks.append({
        "key": "school_year",
        "status": "ok" if meaning and meaning.records else "degraded",
        "detail": window["state"],
    })

    # Newest rollup day: a date for a person to judge, not a verdict.
    try:
        rows = supabase.table("signal_daily_rollup").select("day") \
            .order("day", desc=True).limit(1).execute().data or []
        checks.append({"key": "last_rollup", "status": "ok" if rows else "degraded",
                       "detail": rows[0]["day"] if rows else "No rollup rows yet"})
    except Exception as e:
        print(f"[admin:health:rollup] {e}")
        checks.append({"key": "last_rollup", "status": "unknown",
                       "detail": "Could not read the rollup table"})

    # One call, so status and detail can't straddle a cache expiry.
    enforced = _consent_enforcement_active()
    checks.append({
        "key": "consent_enforcement",
        "status": "ok" if enforced else "degraded",
        "detail": "Enforced" if enforced
                  else "BYPASSED -- recording without consent",
    })

    return {"checks": checks}


# ─── operational counters (ops_metrics) ──────────────────────────────────

_OPS_MAX_HOURS = 24 * 7

# Dollars per million tokens for the cost estimate; defaults are claude-haiku-4-5's list price.
CLAUDE_PRICE_INPUT_PER_MTOK  = env_number("CLAUDE_PRICE_INPUT_PER_MTOK", 1.0, float, minimum=0.0)
CLAUDE_PRICE_OUTPUT_PER_MTOK = env_number("CLAUDE_PRICE_OUTPUT_PER_MTOK", 5.0, float, minimum=0.0)


@app.get("/api/admin/generation")
def admin_generation(request: Request, hours: int = 24):
    """Model calls, their outcomes, waits and tokens, and questions served, over `hours`.

    Counts only: no prompt, no answer, no student. Cost is an estimate from list prices.
    """
    _require_admin(request)
    hours = max(1, min(hours, _OPS_MAX_HOURS))
    got = ops_metrics.read(supabase, ["llm_call", "llm_latency_ms", "llm_tokens", "question"], hours)

    outcomes: dict[str, int] = {}
    tokens: dict[str, int] = {}
    questions: dict[str, int] = {}
    latency: dict[str, dict] = {}
    hourly: dict[str, dict] = {}
    for r in got["rows"]:
        kind, key, n = r["kind"], r["key"], r["n"]
        if kind == "llm_call":
            outcomes[key] = outcomes.get(key, 0) + n
            slot = hourly.setdefault(r["hour"], {"hour": r["hour"], "ok": 0, "failed": 0})
            slot["ok" if key.endswith(":ok") else "failed"] += n
        elif kind == "llm_tokens":
            tokens[key] = tokens.get(key, 0) + n
        elif kind == "question":
            questions[key] = questions.get(key, 0) + n
        elif kind == "llm_latency_ms":
            agg = latency.setdefault(key, {"calls": 0, "sum": 0.0, "max": None})
            agg["calls"] += n
            agg["sum"] += r["sum"] or 0.0
            if r["max"] is not None:
                agg["max"] = r["max"] if agg["max"] is None else max(agg["max"], r["max"])

    waits = {p: {"calls": a["calls"],
                 "mean_ms": round(a["sum"] / a["calls"]) if a["calls"] else None,
                 "max_ms": round(a["max"]) if a["max"] is not None else None}
             for p, a in latency.items()}
    cost = (tokens.get("claude:in", 0) * CLAUDE_PRICE_INPUT_PER_MTOK
            + tokens.get("claude:out", 0) * CLAUDE_PRICE_OUTPUT_PER_MTOK) / 1_000_000

    return {
        "retrieved": got["retrieved"],
        "hours": hours,
        "provider": llm_client.LLM_PROVIDER,
        "model": llm_client.CLAUDE_MODEL if llm_client.LLM_PROVIDER == "claude" else None,
        "outcomes": outcomes,
        "waits": waits,
        "tokens": tokens,
        "estimated_cost_usd": round(cost, 4),
        "prices_per_mtok": {"input": CLAUDE_PRICE_INPUT_PER_MTOK,
                            "output": CLAUDE_PRICE_OUTPUT_PER_MTOK},
        "questions": questions,
        "hourly": sorted(hourly.values(), key=lambda h: h["hour"]),
        # In memory and per process: a restart starts the 24 h window again. Claude calls only.
        "daily_ceiling": ({"used": llm_client._calls_in_window(),
                           "limit": llm_client.GENERATION_DAILY_CALL_LIMIT,
                           "scope": "this server process"}
                          if llm_client.LLM_PROVIDER == "claude" else None),
        "complete": got["complete"],
        "dropped": got["dropped"],
    }


@app.get("/api/admin/refusals")
def admin_refusals(request: Request, hours: int = 24):
    """Requests answered 4xx/5xx (404 aside) over `hours`, by status, method and route template."""
    _require_admin(request)
    hours = max(1, min(hours, _OPS_MAX_HOURS))
    got = ops_metrics.read(supabase, ["refusal"], hours)

    # Buckets are whole UTC hours, so "recent" is this hour's bucket and the one before it.
    recent_from = ops_metrics.since_hour(2)
    totals: dict[str, int] = {}
    recent: dict[str, int] = {}
    hourly: dict[str, int] = {}
    for r in got["rows"]:
        totals[r["key"]] = totals.get(r["key"], 0) + r["n"]
        hourly[r["hour"]] = hourly.get(r["hour"], 0) + r["n"]
        if datetime.fromisoformat(r["hour"]) >= recent_from:
            recent[r["key"]] = recent.get(r["key"], 0) + r["n"]

    def _rows(counts: dict[str, int]) -> list[dict]:
        out = []
        for key, n in counts.items():
            status, method, route = key.split(" ", 2)
            out.append({"status": int(status), "method": method, "route": route, "count": n})
        return sorted(out, key=lambda x: (-x["count"], x["route"]))

    return {
        "retrieved": got["retrieved"],
        "hours": hours,
        "refusals": _rows(totals),
        "recent": _rows(recent),
        "recent_from": recent_from.isoformat(),
        "hourly": [{"hour": h, "count": n} for h, n in sorted(hourly.items())],
        "complete": got["complete"],
        "dropped": got["dropped"],
    }


def _school_day_start(now: datetime | None = None) -> datetime:
    """UTC instant of the current school day's local midnight."""
    tz = _school_timezone()
    local = (now or _utc_now()).astimezone(tz)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


@app.get("/api/admin/today")
def admin_today(request: Request):
    """Today's sessions started, ended by reason and open, answers and active students, at the school.

    Counts only. Discarded empty sessions leave no row, so they come from the counters instead.
    """
    _require_admin(request)
    since = _school_day_start()
    counts = None
    try:
        data = supabase.rpc("admin_today", {"p_since": since.isoformat()}).execute().data
        counts = data[0] if isinstance(data, list) else data
    except Exception as e:                                     # noqa: BLE001
        if not _missing_rpc(e, "admin_today", "20261010000000",
                            "the admin Today strip reads nothing until then"):
            print(f"[admin:today] {e}")
    discards = ops_metrics.read(supabase, ["session_discarded"], since=since)
    discarded: dict[str, int] = {}
    for r in discards["rows"]:
        discarded[r["key"]] = discarded.get(r["key"], 0) + r["n"]
    return {
        "since": since.isoformat(),
        "timezone": _school_timezone_name(),
        "retrieved": isinstance(counts, dict),
        "counts": counts if isinstance(counts, dict) else None,
        "discarded": discarded,
        "discarded_retrieved": discards["retrieved"],
    }


# A page that polled within this long is still watching its station (pull polls every 5 s).
_PAGE_WATCHING_SEC = 30.0

# Names and today's refusal counts change slowly, so a 5 s Stations poll reuses them this long.
# Last lessons are read fresh: one can end with no station changing hands.
_STATIONS_SLOW_SEC = 30.0
_stations_slow: dict = {"at": None}
# Today's ended lessons read for "last ended here"; a station whose last one is older than this many reads none.
_STATIONS_LAST_ENDED_ROWS = 1000


def _stations_last_ended(day_start: datetime) -> tuple[dict[str, dict], bool]:
    """Each station's last lesson ended today: {device: {close_reason, ended_at}}, and whether it was read."""
    last_ended: dict[str, dict] = {}
    try:
        # Newest first, so the first row per station is its last; the cap bounds a long school day.
        for s in (supabase.table("sessions").select("eeg_device_id, close_reason, ended_at")
                  .gte("ended_at", day_start.isoformat()).not_.is_("eeg_device_id", "null")
                  .order("ended_at", desc=True).limit(_STATIONS_LAST_ENDED_ROWS).execute().data or []):
            # A read, not a close; `close_sites()` takes an ended_at key literal for a hand-rolled stamp.
            last_ended.setdefault(s["eeg_device_id"], {k: s.get(k) for k in ("close_reason", "ended_at")})
    except Exception as e:                                     # noqa: BLE001
        print(f"[admin:stations] last ended: {e}")
        return {}, False
    return last_ended, True


def _stations_slow_reads(ids: list[str]) -> dict:
    """Holders' names and today's `station_refused` counts, cached `_STATIONS_SLOW_SEC`.

    A new holder or a failed read is never served from the cache.
    """
    global _stations_slow
    cached = _stations_slow
    if (cached["at"] is not None and time.monotonic() - cached["at"] < _STATIONS_SLOW_SEC
            and set(ids) <= cached["ids"] and cached["names_retrieved"] and cached["refusals_retrieved"]):
        return cached
    # Not `_profiles_many`: its placeholder "Student" would name a holder it could not read.
    names: dict[str, str] = {}
    names_retrieved = True
    if ids:
        try:
            for p in (supabase.table("profiles").select("id, display_name")
                      .in_("id", ids).execute().data or []):
                if p.get("display_name"):
                    names[p["id"]] = p["display_name"]
        except Exception as e:                                 # noqa: BLE001
            print(f"[admin:stations] names: {e}")
            names_retrieved = False
    refusals = ops_metrics.read(supabase, ["station_refused"], since=_school_day_start())
    refused: dict[str, int] = {}
    for r in refusals["rows"]:
        refused[r["key"]] = refused.get(r["key"], 0) + r["n"]
    fresh = {"at": time.monotonic(), "ids": frozenset(ids), "names": names, "names_retrieved": names_retrieved,
             "refused": refused, "refusals_retrieved": refusals["retrieved"]}
    # Swapped in whole: a concurrent request holds either the old dict or this one, never a half-cleared one.
    _stations_slow = fresh
    return fresh


@app.get("/api/admin/stations")
def admin_stations(request: Request):
    """Each headband station: who holds it, how long since their page polled, its poller, refusals today.

    Poller state is this server process's. Names are null with `names_retrieved` when unreadable.
    """
    _require_admin(request)
    mode = eeg_poller.INGEST_MODE
    try:
        pairings = supabase.table("station_pairings").select("device_id, user_id, session_id, seen_at") \
            .order("device_id").execute().data or []
        retrieved = True
    except Exception as e:                                     # noqa: BLE001
        print(f"[admin:stations] {e}")
        pairings, retrieved = [], False
    pollers = eeg_poller.snapshot()
    ids = _unique_ids([p.get("user_id") for p in pairings] + [p["user_id"] for p in pollers])
    slow = _stations_slow_reads(ids)
    names, names_retrieved = slow["names"], slow["names_retrieved"]
    refused = slow["refused"]
    refusals = {"retrieved": slow["refusals_retrieved"]}
    last_ended, last_ended_retrieved = _stations_last_ended(_school_day_start())

    now = _utc_now()
    by_device: dict[str, dict] = {}
    for row in pairings:
        seen = _parse_ts(row.get("seen_at"))
        age = None if seen is None else round((now - seen).total_seconds(), 1)
        by_device.setdefault(row["device_id"], {})["pairing"] = {
            "user_id": row.get("user_id"), "name": names.get(row.get("user_id")),
            "session_id": row.get("session_id"), "seen_seconds_ago": age,
            "idle": age is not None and age >= _PAIRING_IDLE_SEC,
        }
    for p in pollers:
        by_device.setdefault(p["device_id"], {}).setdefault("pollers", []).append({
            **p, "name": names.get(p["user_id"]),
            "page_watching": p["page_seen_seconds_ago"] < _PAGE_WATCHING_SEC,
        })
    for device in [*refused, *last_ended]:
        by_device.setdefault(device, {})
    stations = [{"device_id": d, "pairing": v.get("pairing"), "pollers": v.get("pollers", []),
                 "refused_today": refused.get(d, 0), "last_ended": last_ended.get(d)}
                for d, v in sorted(by_device.items())]
    return {
        "ingest_mode": mode,
        "retrieved": retrieved,
        "names_retrieved": names_retrieved,
        "refusals_retrieved": refusals["retrieved"],
        "last_ended_retrieved": last_ended_retrieved,
        "idle_after_seconds": _PAIRING_IDLE_SEC,
        "timezone": _school_timezone_name(),
        "stations": stations,
        "poller_scope": "this server process",
    }


# ─── read-only totals: funnel, consent changes, signal quality ───────────

def _jsonb_as(data, shape: type):
    """An RPC's jsonb result as `shape` (dict or list), or None if misshapen."""
    # A scalar jsonb object can come back wrapped in a one-row list; a one-row array must stay a list.
    if shape is dict and isinstance(data, list) and len(data) == 1:
        data = data[0]
    return data if isinstance(data, shape) else None


@app.get("/api/admin/funnel")
def admin_funnel(request: Request):
    """How many students, teachers and parents have reached each adoption step. Counts only."""
    _require_admin(request)
    funnel = None
    try:
        funnel = _jsonb_as(supabase.rpc("admin_funnel", {}).execute().data, dict)
    except Exception as e:                                     # noqa: BLE001
        if not _missing_rpc(e, "admin_funnel", "20261011000000",
                            "the admin funnel reads nothing until then"):
            print(f"[admin:funnel] {e}")
    return {"retrieved": funnel is not None, "funnel": funnel}


# `consent_enablements` was created by 20260928040000 with no backfill: no turn-on before it is recorded.
_ENABLEMENTS_RECORDED_FROM = "2026-09-28"
_CONSENT_OPS_MAX_WEEKS = 26


@app.get("/api/admin/consent-ops")
def admin_consent_ops(request: Request, weeks: int = 8):
    """Consent withdrawals, parent turn-ons and erasures per school week and channel."""
    _require_admin(request)
    weeks = max(1, min(weeks, _CONSENT_OPS_MAX_WEEKS))
    today = _school_day_start()
    local = today.astimezone(_school_timezone())
    since = (local - timedelta(days=local.weekday() + 7 * (weeks - 1))).astimezone(timezone.utc)
    rows = None
    try:
        rows = _jsonb_as(supabase.rpc("admin_consent_ops", {"p_since": since.isoformat(),
                                                            "p_tz": _school_timezone_name()}).execute().data, list)
    except Exception as e:                                     # noqa: BLE001
        if not _missing_rpc(e, "admin_consent_ops", "20261011000000",
                            "the admin consent-changes panel reads nothing until then"):
            print(f"[admin:consent_ops] {e}")
    return {
        "retrieved": rows is not None,
        "weeks": weeks,
        "since": since.isoformat(),
        "timezone": _school_timezone_name(),
        "changes": rows or [],
        "enablements_recorded_from": _ENABLEMENTS_RECORDED_FROM,
    }


_SIGNAL_QUALITY_MAX_DAYS = 60


@app.get("/api/admin/signal-quality")
def admin_signal_quality(request: Request, days: int = 14):
    """EEG trusted share and heart SQI, time-to-calibrate and simulated share, per school day.

    Aggregates only; a day with fewer than `_COHORT_MIN_STUDENTS` students is withheld.
    """
    _require_admin(request)
    days = max(1, min(days, _SIGNAL_QUALITY_MAX_DAYS))
    first_day = (_school_day_start().astimezone(_school_timezone()).date() - timedelta(days=days - 1))
    got = None
    try:
        got = _jsonb_as(supabase.rpc("admin_signal_quality", {
            "p_since": first_day.isoformat(), "p_tz": _school_timezone_name(),
            "p_min_students": _COHORT_MIN_STUDENTS}).execute().data, dict)
    except Exception as e:                                     # noqa: BLE001
        if not _missing_rpc(e, "admin_signal_quality", "20261011000000",
                            "the admin signal-quality panel reads nothing until then"):
            print(f"[admin:signal_quality] {e}")
    return {
        "retrieved": got is not None,
        "days": days,
        "since": first_day.isoformat(),
        "timezone": _school_timezone_name(),
        "min_students": _COHORT_MIN_STUDENTS,
        "eeg": (got or {}).get("eeg") or [],
        "heart": (got or {}).get("heart") or [],
    }


_ADAPTIVE_MAX_DAYS = 60


@app.get("/api/admin/adaptive")
def admin_adaptive(request: Request, days: int = 14):
    """Adaptive decisions per school day: eased, raised or held, and why. Aggregates only.

    A day with fewer than `_COHORT_MIN_STUDENTS` students is withheld; a withdrawn heart or camera channel's
    decisions are left out.
    """
    _require_admin(request)
    days = max(1, min(days, _ADAPTIVE_MAX_DAYS))
    first_day = (_school_day_start().astimezone(_school_timezone()).date() - timedelta(days=days - 1))
    got = None
    try:
        got = _jsonb_as(supabase.rpc("admin_adaptive_reasons", {
            "p_since": first_day.isoformat(), "p_tz": _school_timezone_name(),
            "p_min_students": _COHORT_MIN_STUDENTS}).execute().data, list)
    except Exception as e:                                     # noqa: BLE001
        if not _missing_rpc(e, "admin_adaptive_reasons", "20261012000000",
                            "the admin adaptive-decisions panel reads nothing until then"):
            print(f"[admin:adaptive] {e}")
    # Counted beside the rows, or a write path that keeps failing reads as a quiet fortnight.
    failed = ops_metrics.read(supabase, ["adaptive_decision"],
                              since=datetime(first_day.year, first_day.month, first_day.day,
                                             tzinfo=_school_timezone()))
    return {
        "retrieved": got is not None,
        "days": days,
        "since": first_day.isoformat(),
        "timezone": _school_timezone_name(),
        "min_students": _COHORT_MIN_STUDENTS,
        "decisions": got or [],
        "write_failures": (sum(r["n"] for r in failed["rows"] if r["key"] == "write_failed")
                           if failed["retrieved"] else None),
    }


_SECURITY_EVENT_KINDS = (
    "authz_denied", "admin_denied", "rate_limited", "consent_changed")
_SECURITY_EVENTS_MAX = 200


@app.get("/api/admin/security-events")
def admin_security_events(request: Request, kind: str | None = None,
                          limit: int = 50):
    """The security log, newest first, optionally narrowed to one kind.

    Adds display names for the two ids on each row, and nothing else about either person.
    """
    _require_admin(request)

    if kind is not None and kind not in _SECURITY_EVENT_KINDS:
        # 422, not an empty filter that reads as "no events".
        raise HTTPException(422, f"Unknown kind; expected one of {', '.join(_SECURITY_EVENT_KINDS)}")

    limit = max(1, min(limit, _SECURITY_EVENTS_MAX))

    try:
        q = supabase.table("security_events") \
            .select("id, kind, actor_user_id, subject_user_id, detail, created_at") \
            .order("created_at", desc=True).limit(limit)
        if kind:
            q = q.eq("kind", kind)
        rows = q.execute().data or []
    except Exception as e:                                     # noqa: BLE001
        print(f"[admin:security_events] {e}")
        return {"retrieved": False, "names_retrieved": False,
                "events": [], "kinds": list(_SECURITY_EVENT_KINDS)}

    # Not `_profiles_many`: its placeholder name "Student" would mislabel audit rows.
    ids = _unique_ids([r.get("actor_user_id") for r in rows]
                      + [r.get("subject_user_id") for r in rows])
    names: dict[str, str] = {}
    names_retrieved = True
    if ids:
        try:
            for p in (supabase.table("profiles").select("id, display_name")
                      .in_("id", ids).execute().data or []):
                if p.get("display_name"):
                    names[p["id"]] = p["display_name"]
        except Exception as e:                                 # noqa: BLE001
            print(f"[admin:security_events] names: {e}")
            # Own flag: "could not look up" is not "has no profile".
            names_retrieved = False

    def _who(uid):
        if not uid:
            return None
        return {"id": uid, "name": names.get(uid)}

    return {
        "retrieved": True,
        "names_retrieved": names_retrieved,
        "kinds": list(_SECURITY_EVENT_KINDS),
        "events": [{
            "id": r["id"],
            "kind": r["kind"],
            "actor": _who(r.get("actor_user_id")),
            "subject": _who(r.get("subject_user_id")),
            "detail": r.get("detail") or {},
            "created_at": r["created_at"],
        } for r in rows],
    }


@app.get("/api/admin/consent-summary")
def admin_consent_summary(request: Request):
    """Counts only: how many students, how many have said yes to each channel."""
    _require_admin(request)
    try:
        students = supabase.table("profiles").select("id") \
            .eq("role", "student").execute().data or []
        consents = supabase.table("signal_consent").select("*").execute().data or []
    except Exception as e:
        print(f"[admin:consent_summary] {e}")
        return {"retrieved": False}

    def _n(channel):
        return sum(1 for c in consents if c.get(f"{channel}_enabled"))

    return {
        "retrieved": True,
        "students": len(students),
        "with_any_consent_row": len(consents),
        "eeg": _n("eeg"),
        "headband_optical": _n("headband_optical"),
        "camera": _n("camera"),
        "awaiting_student_ack": sum(
            1 for c in consents
            if c.get("parent_enabled_at") and not c.get("student_ack_at")),
    }


@app.get("/api/admin/students/search")
def admin_student_search(request: Request, q: str = "", limit: int = 10):
    """Find a student by name or email, to jump to their existing report. Identifiers only."""
    _require_admin(request)
    term = (q or "").strip()
    if len(term) < 2:
        return {"students": [], "query": term}
    limit = max(1, min(limit, 25))

    # Security: `, ( )` stripped and `\ %` escaped, or they parse as PostgREST filter syntax.
    safe = term.replace("\\", "\\\\").replace("%", "\\%").replace(",", "").replace("(", "").replace(")", "")
    try:
        rows = supabase.table("profiles") \
            .select("id, display_name, email, grade_level") \
            .eq("role", "student") \
            .or_(f"display_name.ilike.%{safe}%,email.ilike.%{safe}%") \
            .limit(limit).execute().data or []
    except Exception as e:
        print(f"[admin:search] {e}")
        return {"students": [], "query": term, "retrieved": False}

    return {"query": term, "retrieved": True, "students": [{
        "id": r["id"],
        "display_name": r.get("display_name") or "Student",
        "email": r.get("email") or "",
        "grade_level": r.get("grade_level"),
    } for r in rows]}


# ─── admin: the student kit's installer ──────────────────────────────────
# The gate serves it only to a short link this server signs. See kit_gate.py.

_KIT_GATE = kit_gate.settings()
if _KIT_GATE is None:
    print("[config] KIT_GATE_URL and KIT_LINK_SECRET are not both set: the admin page offers no kit installer")


def _kit_offered() -> dict | None:
    """The installer the gate offers, or None. An outage is a 503; a GateError that a retry cannot fix goes up."""
    try:
        return kit_gate.current(*_KIT_GATE, _utc_now().timestamp())
    except kit_gate.GateError as e:
        print(f"[admin:kit] {e}")
        if e.retryable:
            raise _read_failed("The kit's installer could not be looked up; try again")
        raise


@app.get("/api/admin/kit")
def admin_kit(request: Request):
    """The Setup installer the gate offers. Also `configured: false`, or a `problem` to fix; 503 if unread."""
    _require_admin(request)
    if _KIT_GATE is None:
        return {"configured": False}
    try:
        found = _kit_offered()
    except kit_gate.GateError as e:
        return {"configured": True, "problem": str(e), "problem_is": e.kind}
    if found is None:
        return {"configured": True, "published": False}
    return {"configured": True, "published": True, **found}


# What the page heads a non-retryable GateError with, by its kind.
_KIT_PROBLEM = {"setup": "The download gate is set up wrong", "publish": "The last publish did not finish"}


class KitLinkRequest(StrictModel):
    # The installer the page showed: a link to any other would deliver bytes it did not describe.
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$", max_length=64)


@app.post("/api/admin/kit/download-link")
def admin_kit_download_link(request: Request, payload: KitLinkRequest):
    """A link to the installer the page showed, for `kit_gate.LINK_TTL_S`. It names no user and is never cached."""
    user = _require_admin(request)
    if _KIT_GATE is None:
        raise HTTPException(503, "This server has no kit download gate set up")
    try:
        found = _kit_offered()
    except kit_gate.GateError as e:
        raise HTTPException(409, f"{_KIT_PROBLEM[e.kind]}: {e}")
    if found is None:
        raise HTTPException(409, "No installer is published any more; reload the page")
    if found["sha256"] != payload.sha256:
        raise HTTPException(409, "A different installer was published after this page loaded; reload it")
    url, exp = kit_gate.link(*_KIT_GATE, _utc_now().timestamp(), found["file"])
    print(f"[admin:kit] a download link for {user['id'][:8]}")
    return JSONResponse({"url": url, "expires_at": datetime.fromtimestamp(exp, timezone.utc).isoformat()},
                        headers={"Cache-Control": "no-store"})


_KIT_VERSION_DAYS = 14


@app.get("/api/admin/kit-versions")
def admin_kit_versions(request: Request):
    """Students by the newest kit version their headband lessons reported in `_KIT_VERSION_DAYS`. Counts only."""
    _require_admin(request)
    versions = None
    try:
        versions = _jsonb_as(supabase.rpc("admin_kit_versions", {
            "p_since": (_utc_now() - timedelta(days=_KIT_VERSION_DAYS)).isoformat(),
        }).execute().data, dict)
    except Exception as e:                                     # noqa: BLE001
        if not _missing_rpc(e, "admin_kit_versions", "20261014000000",
                            "the Sensors kit page shows no kit versions until then"):
            print(f"[admin:kit-versions] {e}")
    return {"retrieved": versions is not None, "days": _KIT_VERSION_DAYS, "versions": versions}


if __name__ == "__main__":
    import uvicorn
    # Loopback: with ENV unset /docs is on, and 0.0.0.0 served it and the API to the whole LAN.
    uvicorn.run("main:app", host="127.0.0.1", port=BACKEND_PORT, reload=True)