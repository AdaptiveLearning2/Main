"""Pytest configuration for the EEGResearch suite.

``API_TOKEN`` and ``ADMIN_TOKEN`` have no defaults in ``Settings`` (config.py) --
the sidecar deliberately refuses to start without them -- so importing
``src.app.main``, which calls ``get_settings()`` at module load, raises a
``ValidationError`` before a single test can be collected unless both are set.
That is why the suite previously failed at collection with "Field required"
unless you exported the tokens by hand first.

These throwaway values let the suite run with zero setup. They are set at module
top level rather than in a fixture on purpose: pytest imports the test modules
(each does ``from src.app.main import app``) during collection, which happens
*before* any fixture runs, so a fixture would be too late to matter.

``setdefault`` means a real token exported in the environment still wins -- e.g.
when running the suite against a live sidecar -- so this only fills the gap, it
never overrides a deliberate choice. And because ``test_security.py`` reads the
configured token back via ``get_settings().api_token`` instead of hard-coding
one, its valid-token case picks up whatever is set here automatically.
"""
import os

os.environ.setdefault("API_TOKEN", "test-learner-token")
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")

# get_settings() is lru_cached. If anything read it before these were set, the
# cached instance would be missing the tokens; clear it so the first real use
# rebuilds with them present. A no-op when nothing has cached yet.
from src.app.config import get_settings  # noqa: E402

get_settings.cache_clear()
