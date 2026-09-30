"""The one way this backend builds a Supabase client: HTTP/1.1, one pool shared by every sub-client.

supabase-py's sync clients default to HTTP/2, and a single HTTP/2 connection used from several
threads fails with EAGAIN ("Resource temporarily unavailable"). See CLAUDE.md, Database.
"""
import httpx
import supabase

# Postgrest's own default read budget; Storage's was 20 s, but uploads run off the request path.
_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


def make_client(url: str, key: str) -> "supabase.Client":
    """A Supabase client whose Postgrest, Storage, Auth and Functions share one HTTP/1.1 pool.

    Resolves `supabase.create_client` at call time, so a test patching it there still applies.
    """
    http = httpx.Client(http2=False, follow_redirects=True, timeout=_TIMEOUT)
    return supabase.create_client(url, key, options=supabase.ClientOptions(httpx_client=http))
