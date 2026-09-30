"""The one way this backend builds a Supabase client: HTTP/1.1, one pool per process.

supabase-py's sync clients default to HTTP/2, and a single HTTP/2 connection used from several
threads fails with EAGAIN ("Resource temporarily unavailable"). See CLAUDE.md, Database.
"""
import threading

import httpx
import supabase

# Above the threads that can hold a request at once (anyio's 40, the worker pools, one poller per
# pull-mode session), and all kept alive, so a burst reuses connections instead of re-handshaking.
_LIMITS = httpx.Limits(max_connections=128, max_keepalive_connections=128)
# Postgrest's own read budget; a starved pool fails in 10 s rather than queueing for 120.
_TIMEOUT = httpx.Timeout(120.0, connect=10.0, pool=10.0)
# Storage's own default: `/charts` signs up to four URLs in turn while a request waits.
_STORAGE_TIMEOUT = httpx.Timeout(20.0, connect=10.0, pool=10.0)

_clients: dict = {}
_clients_lock = threading.Lock()


def _storage_timeout(request: httpx.Request) -> None:
    if request.url.path.startswith("/storage/v1/"):
        request.extensions["timeout"] = _STORAGE_TIMEOUT.as_dict()


def make_client(url: str, key: str) -> "supabase.Client":
    """A new client, all sub-clients on one HTTP/1.1 pool. One-shot scripts only; else `get_client`.

    Resolves `supabase.create_client` at call time, so a test patching it there still applies.
    """
    http = httpx.Client(http2=False, follow_redirects=True, timeout=_TIMEOUT, limits=_LIMITS,
                        event_hooks={"request": [_storage_timeout]})
    return supabase.create_client(url, key, options=supabase.ClientOptions(httpx_client=http))


def get_client(url: str, key: str) -> "supabase.Client":
    """The process's one client for these credentials, built on first use."""
    with _clients_lock:
        client = _clients.get((url, key))
        if client is None:
            client = _clients[(url, key)] = make_client(url, key)
        return client
