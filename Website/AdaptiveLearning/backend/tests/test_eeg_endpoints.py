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
import eeg_poller  # noqa: E402
import main  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_poller_state(monkeypatch):
    """Isolate eeg_poller._active across tests in this file and stub out real
    sidecar/network calls (mirrors test_eeg_poller.py's fixture, scoped
    separately since fixtures are per-file unless moved to conftest.py).

    Without the eeg_client stubs, _Poller.run()'s background thread would
    hit get_state()'s real _learner_headers() call -- which raises
    RuntimeError, uncaught, in the poller loop when EEG_API_TOKEN isn't set
    -- silently killing the thread out from under a test's is_alive() check.
    """
    monkeypatch.setattr(eeg_client, "start_session", lambda device_id=eeg_client.DEFAULT_DEVICE_ID: {"ok": True})
    monkeypatch.setattr(eeg_client, "stop_session", lambda device_id=eeg_client.DEFAULT_DEVICE_ID: {"ok": True})
    monkeypatch.setattr(eeg_client, "get_state", lambda device_id=eeg_client.DEFAULT_DEVICE_ID, timeout=2.0: None)
    monkeypatch.setattr(eeg_poller, "POLL_INTERVAL", 0.01)
    eeg_poller._active.clear()
    yield
    for sid in list(eeg_poller._active):
        eeg_poller.stop(sid)


class _FakeSupabase:
    def table(self, *_a, **_k):
        raise AssertionError("no data should be inserted in these tests")


class _SessionRow:
    def __init__(self, user_id, ended_at=None):
        self.data = {"user_id": user_id, "ended_at": ended_at}


class _SessionsQuery:
    """Minimal stand-in for supabase.table("sessions").select(...).eq(...).single().execute()."""

    def __init__(self, row):
        self._row = row

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def single(self):
        return self

    def execute(self):
        return self._row


class _SessionsTable:
    def __init__(self, user_id, ended_at=None):
        self._row = _SessionRow(user_id, ended_at)

    def table(self, name):
        assert name == "sessions"
        return _SessionsQuery(self._row)


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


# ── cross-user guard on /api/eeg/status & /api/eeg/debug ────────────────
#
# These exercise the real eeg_poller._active registry (not a mocked
# can_use_device) so a future refactor that breaks the wiring between the
# endpoint and the guard shows up here, not just in can_use_device's own
# unit tests.

def test_status_blocks_user_b_from_user_as_claimed_station(monkeypatch):
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-x")
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "get_muse_status", lambda device_id=None: {"available": True, "ingestion": {}})

    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    out = main.eeg_status(request=None, device_id="station-x")
    assert out["muse"] == {"available": False, "reason": "in_use_by_other"}

    # The owner querying their own station is unaffected.
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    out = main.eeg_status(request=None, device_id="station-x")
    assert out["muse"] == {"available": True, "ingestion": {}}


def test_status_allows_anyone_on_an_unclaimed_station(monkeypatch):
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "get_muse_status", lambda device_id=None: {"available": True, "ingestion": {}})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    out = main.eeg_status(request=None, device_id="station-unclaimed")
    assert out["muse"] == {"available": True, "ingestion": {}}


def test_debug_blocks_user_b_from_user_as_claimed_station(monkeypatch):
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-x")
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    out = main.eeg_debug(request=None, device_id="station-x")
    assert out == {"available": False, "reason": "in_use_by_other"}


# ── /api/eeg/start: device_id validation ─────────────────────────────────

def test_start_rejects_unknown_device_id(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    monkeypatch.setattr(main, "supabase", _SessionsTable("user-a"))
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "list_devices", lambda: [{"device_id": "default"}])

    payload = main.EegSessionRequest(session_id="session-1", device_id="typo-station")
    with pytest.raises(main.HTTPException) as exc_info:
        main.eeg_start(payload, request=None)
    assert exc_info.value.status_code == 404


def test_start_allows_known_device_id(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    monkeypatch.setattr(main, "supabase", _SessionsTable("user-a"))
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "list_devices", lambda: [{"device_id": "station-a"}])
    monkeypatch.setattr(eeg_poller, "start", lambda *a, **k: {"running": True, "already": False})

    payload = main.EegSessionRequest(session_id="session-1", device_id="station-a")
    out = main.eeg_start(payload, request=None)
    assert out == {"ok": True, "running": True, "already": False}


def test_start_falls_back_to_permissive_when_list_devices_unreachable(monkeypatch):
    """An empty known_ids (list_devices() erroring even though is_alive() just
    succeeded -- a transient sidecar glitch) must not block a legitimate
    start; see the comment in eeg_start."""
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    monkeypatch.setattr(main, "supabase", _SessionsTable("user-a"))
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "list_devices", lambda: [])
    monkeypatch.setattr(eeg_poller, "start", lambda *a, **k: {"running": True, "already": False})

    payload = main.EegSessionRequest(session_id="session-1", device_id="anything")
    out = main.eeg_start(payload, request=None)
    assert out["running"] is True


# ── /api/eeg/muse/refresh|connect|disconnect: cross-user guard ──────────
#
# Same real-registry approach as the status/debug guard tests above: a
# stranger disconnecting/reconnecting/rescanning someone else's live station
# is per-victim griefing, not just an unwanted side effect, so each handler
# gets owner-allowed / stranger-403 / unclaimed-open coverage.

def test_muse_refresh_blocks_stranger_allows_owner(monkeypatch):
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-x")
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_refresh", lambda device_id: {"ok": True})

    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    with pytest.raises(main.HTTPException) as exc_info:
        main.eeg_muse_refresh(request=None, body={"device_id": "station-x"})
    assert exc_info.value.status_code == 403

    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    assert main.eeg_muse_refresh(request=None, body={"device_id": "station-x"}) == {"ok": True}


def test_muse_refresh_allows_unclaimed_station(monkeypatch):
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_refresh", lambda device_id: {"ok": True})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    assert main.eeg_muse_refresh(request=None, body={"device_id": "station-unclaimed"}) == {"ok": True}


def test_muse_connect_blocks_stranger_allows_owner(monkeypatch):
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-x")
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_connect", lambda name, device_id: {"ok": True})

    body = {"name": "MuseS-1234", "device_id": "station-x"}
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    with pytest.raises(main.HTTPException) as exc_info:
        main.eeg_muse_connect(request=None, body=body)
    assert exc_info.value.status_code == 403

    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    assert main.eeg_muse_connect(request=None, body=body) == {"ok": True}


def test_muse_connect_allows_unclaimed_station(monkeypatch):
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_connect", lambda name, device_id: {"ok": True})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    body = {"name": "MuseS-1234", "device_id": "station-unclaimed"}
    assert main.eeg_muse_connect(request=None, body=body) == {"ok": True}


def test_muse_disconnect_blocks_stranger_allows_owner(monkeypatch):
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-x")
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_disconnect", lambda device_id: {"ok": True})

    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    with pytest.raises(main.HTTPException) as exc_info:
        main.eeg_muse_disconnect(request=None, body={"device_id": "station-x"})
    assert exc_info.value.status_code == 403

    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    assert main.eeg_muse_disconnect(request=None, body={"device_id": "station-x"}) == {"ok": True}


def test_muse_disconnect_allows_unclaimed_station(monkeypatch):
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_disconnect", lambda device_id: {"ok": True})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    assert main.eeg_muse_disconnect(request=None, body={"device_id": "station-unclaimed"}) == {"ok": True}
