"""The one way this backend builds a Supabase client: HTTP/1.1, one pool per process.

supabase-py's sync clients default to HTTP/2, and a single HTTP/2 connection used from several
threads fails with EAGAIN ("Resource temporarily unavailable"). See CLAUDE.md, Database.
"""
import threading

import httpx
import supabase

# Above the threads that can hold a request at once (main's 96, the worker pools, one poller per
# pull-mode session), and all kept alive 30 s, not httpx's 5, so a quiet gap needs no re-handshake.
_LIMITS = httpx.Limits(max_connections=128, max_keepalive_connections=128, keepalive_expiry=30.0)
# Each service keeps the budget its own client had. The database API: Postgrest's 120 s, and a
# starved pool fails in 10 s rather than queueing for the whole read budget.
_REST_TIMEOUT = httpx.Timeout(120.0, connect=10.0, pool=10.0)
# Auth: httpx's 5 s default, which GoTrue used.
_AUTH_TIMEOUT = httpx.Timeout(5.0)
# Everything else, Storage included (its own 20 s: `/charts` signs up to four URLs in turn).
_OTHER_TIMEOUT = httpx.Timeout(20.0, connect=10.0, pool=10.0)

_clients: dict = {}
_clients_lock = threading.Lock()


def _timeout_by_service(url: str):
    """A request hook choosing each call's budget by service, under the project URL's own path."""
    base = httpx.URL(url).path.rstrip("/")
    rest, auth = f"{base}/rest/v1/", f"{base}/auth/v1/"

    def hook(request: httpx.Request) -> None:
        path = request.url.path
        if not path.startswith(rest):
            request.extensions["timeout"] = (
                _AUTH_TIMEOUT if path.startswith(auth) else _OTHER_TIMEOUT).as_dict()
    return hook


def make_client(url: str, key: str) -> "supabase.Client":
    """A new client, all sub-clients on one HTTP/1.1 pool. One-shot scripts only; else `get_client`.

    Resolves `supabase.create_client` at call time, so a test patching it there still applies.
    """
    http = httpx.Client(http2=False, follow_redirects=True, timeout=_REST_TIMEOUT, limits=_LIMITS)
    client = supabase.create_client(url, key, options=supabase.ClientOptions(httpx_client=http))
    # After the call, so a missing URL still fails with supabase's own error.
    http.event_hooks = {"request": [_timeout_by_service(url)]}
    return client


def get_client(url: str, key: str) -> "supabase.Client":
    """The process's one client for these credentials, built on first use."""
    with _clients_lock:
        client = _clients.get((url, key))
        if client is None:
            client = _clients[(url, key)] = make_client(url, key)
        return client
