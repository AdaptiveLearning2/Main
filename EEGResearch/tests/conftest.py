"""Pytest configuration for the EEGResearch suite.

``API_TOKEN`` and ``ADMIN_TOKEN`` are required settings with no default, and
importing ``src.app.main`` reads them at module load. Set throwaway values here,
at module level, so collection succeeds before any fixture would run.

``setdefault`` leaves a real exported token in place, so this only fills gaps
when running against a live sidecar. ``test_security.py`` reads the token back
via ``get_settings().api_token`` rather than hard-coding one, so it picks up
whatever is set here.
"""
import os

os.environ.setdefault("API_TOKEN", "test-learner-token")
os.environ.setdefault("ADMIN_TOKEN", "test-admin-token")

# Clear the lru_cache in case something already read settings before the
# tokens above were set.
from src.app.config import get_settings  # noqa: E402

get_settings.cache_clear()
