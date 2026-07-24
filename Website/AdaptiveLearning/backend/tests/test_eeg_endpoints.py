"""Tests for the EEG status endpoints' tolerance of a missing/misconfigured token.

Since the auth hardening in #25, eeg_client's header helpers raise RuntimeError
when EEG_API_TOKEN / EEG_ADMIN_TOKEN are unset, and get_state / get_muse_status
deliberately let that propagate rather than mask a config error as an outage.
These endpoints must catch it and report a status, not turn a developer's
missing-token setup into a bare 500.
"""
import os
import sys

# main.py builds a Supabase client at import time and raises without these.
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import eeg_client  # noqa: E402
import main  # noqa: E402


# ── /api/eeg/health ──────────────────────────────────────────────────────

def test_health_reports_unavailable_when_sidecar_is_down(monkeypatch):
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: False)
    out = main.eeg_health()
    assert out["available"] is False


def test_health_reports_error_instead_of_500_on_missing_token(monkeypatch):
    # Sidecar reachable (unauthenticated /healthz), but the learner token is
    # unset, so get_muse_status raises. This used to 500 the health check.
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)

    def _raise():
        raise RuntimeError("Missing EEG_API_TOKEN environment variable")

    monkeypatch.setattr(eeg_client, "get_muse_status", _raise)
    out = main.eeg_health()  # must not raise
    assert out["available"] is False
    assert "EEG_API_TOKEN" in out["error"]


def test_health_healthy_path_returns_muse(monkeypatch):
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "get_muse_status", lambda: {"available": True})
    out = main.eeg_health()
    assert out["available"] is True
    assert out["muse"] == {"available": True}


# ── /api/eeg/debug ───────────────────────────────────────────────────────

def test_debug_reports_error_instead_of_500_on_missing_token(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "u"})
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)

    def _raise(*a, **k):
        raise RuntimeError("Missing EEG_API_TOKEN environment variable")

    monkeypatch.setattr(eeg_client, "get_state", _raise)
    monkeypatch.setattr(eeg_client, "get_muse_status", _raise)
    out = main.eeg_debug(request=None)  # get_user is stubbed, so request is unused
    assert out["available"] is False
    assert "EEG_API_TOKEN" in out["error"]
