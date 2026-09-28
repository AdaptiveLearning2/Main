"""EEG endpoints: a missing token reports a status rather than a 500, and cross-user station guards."""
import os

# main.py builds a Supabase client at import time and raises without these.
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import eeg_client  # noqa: E402
import eeg_poller  # noqa: E402
import main  # noqa: E402
from conftest import pairings, real_pairing_funcs  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_poller_state(monkeypatch):
    """Isolates eeg_poller._active and stubs sidecar calls, which raise without EEG_API_TOKEN."""
    monkeypatch.setattr(eeg_client, "start_session", lambda device_id=eeg_client.DEFAULT_DEVICE_ID: {"ok": True})
    monkeypatch.setattr(eeg_client, "stop_session", lambda device_id=eeg_client.DEFAULT_DEVICE_ID: {"ok": True})
    monkeypatch.setattr(eeg_client, "get_state", lambda device_id=eeg_client.DEFAULT_DEVICE_ID, timeout=2.0: None)
    monkeypatch.setattr(eeg_poller, "POLL_INTERVAL", 0.01)
    # EEG consented unless a test says otherwise (/muse/connect checks it); the real _may_record composes it.
    monkeypatch.setattr(main, "_consent", lambda _uid: {**main._CONSENT_ENABLED_ALL, "retrieved": True, "exists": True})
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
    # Sidecar reachable, but the learner token is unset, so get_muse_status raises.
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
# Real eeg_poller._active registry, not a mocked can_use_device.

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


# ── the pre-claim pairing window, exercised end to end ───────────────────
# Two users reaching for one unclaimed station before either has a poller.

def test_two_users_racing_an_unclaimed_station_the_second_is_blocked(monkeypatch):
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_refresh", lambda device_id: {"ok": True})

    # user-a scans first and wins the station.
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    assert main.eeg_muse_refresh(request=None, body={"device_id": "station-race"}) == {"ok": True}

    # user-b is refused on refresh and on reading the station too.
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    with pytest.raises(main.HTTPException) as exc_info:
        main.eeg_muse_refresh(request=None, body={"device_id": "station-race"})
    assert exc_info.value.status_code == 403

    monkeypatch.setattr(eeg_client, "get_muse_status", lambda device_id=None: {"available": True, "ingestion": {}})
    out = main.eeg_status(request=None, device_id="station-race")
    assert out["muse"] == {"available": False, "reason": "in_use_by_other"}
    assert main.eeg_debug(request=None, device_id="station-race") == {
        "available": False, "reason": "in_use_by_other",
    }

    # user-a keeps the station they reserved.
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    assert main.eeg_muse_refresh(request=None, body={"device_id": "station-race"}) == {"ok": True}


def test_a_failed_refresh_releases_its_reservation_instead_of_squatting(monkeypatch):
    """A request that never reaches the bridge is not active pairing worth protecting."""
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: False)
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    with pytest.raises(main.HTTPException) as exc_info:
        main.eeg_muse_refresh(request=None, body={"device_id": "station-race"})
    assert exc_info.value.status_code == 503

    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_refresh", lambda device_id: {"ok": True})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    assert main.eeg_muse_refresh(request=None, body={"device_id": "station-race"}) == {"ok": True}


def test_a_bridge_error_on_connect_releases_its_reservation(monkeypatch):
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)

    def _explode(name, device_id):
        raise RuntimeError("bridge unreachable")
    monkeypatch.setattr(eeg_client, "muse_connect", _explode)
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    body = {"name": "MuseS-1234", "device_id": "station-race"}
    with pytest.raises(main.HTTPException) as exc_info:
        main.eeg_muse_connect(request=None, body=body)
    assert exc_info.value.status_code == 502

    monkeypatch.setattr(eeg_client, "muse_connect", lambda name, device_id: {"ok": True})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    assert main.eeg_muse_connect(request=None, body=body) == {"ok": True}


def test_a_successful_scan_still_holds_its_reservation_through_a_later_failure(monkeypatch):
    """The release is scoped to the caller who just failed."""
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_refresh", lambda device_id: {"ok": True})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    main.eeg_muse_refresh(request=None, body={"device_id": "station-a"})

    # user-b failing on station-b must not touch user-a's claim on station-a.
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: False)
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    with pytest.raises(main.HTTPException):
        main.eeg_muse_refresh(request=None, body={"device_id": "station-b"})

    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-c"})
    with pytest.raises(main.HTTPException) as exc_info:
        main.eeg_muse_refresh(request=None, body={"device_id": "station-a"})
    assert exc_info.value.status_code == 403


def test_the_same_users_failed_attempt_on_one_device_spares_their_other(monkeypatch):
    """One user, two reservations: the release is scoped to device_id, not just user_id."""
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_refresh", lambda device_id: {"ok": True})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    main.eeg_muse_refresh(request=None, body={"device_id": "station-a"})

    def _explode(name, device_id):
        raise RuntimeError("bridge unreachable")
    monkeypatch.setattr(eeg_client, "muse_connect", _explode)
    with pytest.raises(main.HTTPException) as exc_info:
        main.eeg_muse_connect(request=None, body={"name": "Muse-1", "device_id": "station-b"})
    assert exc_info.value.status_code == 502

    # station-a's reservation must have survived the station-b failure.
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-d"})
    with pytest.raises(main.HTTPException) as exc_info:
        main.eeg_muse_refresh(request=None, body={"device_id": "station-a"})
    assert exc_info.value.status_code == 403
    # station-b is free again.
    monkeypatch.setattr(eeg_client, "muse_refresh", lambda device_id: {"ok": True})
    assert main.eeg_muse_refresh(request=None, body={"device_id": "station-b"}) == {"ok": True}


def test_closing_one_session_spares_another_sessions_reservation(monkeypatch):
    """Stopping stale S1 must not release the station S2 is mid-pairing on."""
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_refresh", lambda device_id: {"ok": True})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})

    # Two sessions, two stations, one student.
    main.eeg_muse_refresh(request=None,
                          body={"device_id": "station-a", "session_id": "S1"})
    main.eeg_muse_refresh(request=None,
                          body={"device_id": "station-b", "session_id": "S2"})

    monkeypatch.setattr(main, "supabase", _SessionsTable("user-a"))
    main.eeg_stop(main.EegSessionRequest(session_id="S1"), request=None)

    # S1's station is free.
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    assert main.eeg_muse_refresh(
        request=None, body={"device_id": "station-a"}) == {"ok": True}

    # S2's is not.
    with pytest.raises(main.HTTPException) as exc_info:
        main.eeg_muse_refresh(request=None, body={"device_id": "station-b"})
    assert exc_info.value.status_code == 403


def test_a_reservation_with_no_session_is_still_released(monkeypatch):
    """No session close could ever name a session-less entry, so it must be released."""
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_refresh", lambda device_id: {"ok": True})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    main.eeg_muse_refresh(request=None, body={"device_id": "station-old"})

    monkeypatch.setattr(main, "supabase", _SessionsTable("user-a"))
    main.eeg_stop(main.EegSessionRequest(session_id="S1"), request=None)

    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    assert main.eeg_muse_refresh(
        request=None, body={"device_id": "station-old"}) == {"ok": True}


def test_refreshing_without_a_session_id_keeps_the_one_already_recorded(monkeypatch):
    """A later refresh with no session_id must not overwrite the recorded one with None."""
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_refresh", lambda device_id: {"ok": True})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})

    main.eeg_muse_refresh(request=None,
                          body={"device_id": "station-c", "session_id": "S2"})
    main.eeg_muse_refresh(request=None, body={"device_id": "station-c"})

    monkeypatch.setattr(main, "supabase", _SessionsTable("user-a"))
    main.eeg_stop(main.EegSessionRequest(session_id="S1"), request=None)

    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    with pytest.raises(main.HTTPException) as exc_info:
        main.eeg_muse_refresh(request=None, body={"device_id": "station-c"})
    assert exc_info.value.status_code == 403


def test_stop_releases_the_reservation_for_another_user(monkeypatch):
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_refresh", lambda device_id: {"ok": True})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    main.eeg_muse_refresh(request=None, body={"device_id": "station-race"})

    # No live poller exists, but the scan's reservation is still theirs to release.
    monkeypatch.setattr(main, "supabase", _SessionsTable("user-a"))
    main.eeg_stop(main.EegSessionRequest(session_id="session-1"), request=None)

    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    assert main.eeg_muse_refresh(request=None, body={"device_id": "station-race"}) == {"ok": True}


# ── /api/eeg/start: device_id validation ─────────────────────────────────

def test_start_rejects_unknown_device_id(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    # Consent stubbed open; the retention window is open via conftest.
    monkeypatch.setattr(main, "_consent",
                        lambda _s: {"eeg_enabled": True, "retrieved": True})
    monkeypatch.setattr(main, "supabase", _SessionsTable("user-a"))
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "list_devices", lambda: [{"device_id": "default"}])

    payload = main.EegSessionRequest(session_id="session-1", device_id="typo-station")
    with pytest.raises(main.HTTPException) as exc_info:
        main.eeg_start(payload, request=None)
    assert exc_info.value.status_code == 404


def test_start_allows_known_device_id(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    # Consent stubbed open; the retention window is open via conftest.
    monkeypatch.setattr(main, "_consent",
                        lambda _s: {"eeg_enabled": True, "retrieved": True})
    monkeypatch.setattr(main, "supabase", _SessionsTable("user-a"))
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "list_devices", lambda: [{"device_id": "station-a"}])
    monkeypatch.setattr(eeg_poller, "start", lambda *a, **k: {"running": True, "already": False})

    payload = main.EegSessionRequest(session_id="session-1", device_id="station-a")
    out = main.eeg_start(payload, request=None)
    assert out == {"ok": True, "running": True, "already": False}


def test_a_start_stamps_the_session_and_a_refused_one_does_not(monkeypatch):
    """`signals_missing` needs to know a headband was started; a refusal started nothing."""
    stamped = []
    monkeypatch.setattr(main, "_mark_eeg_started", stamped.append)
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    monkeypatch.setattr(main, "_consent",
                        lambda _s: {"eeg_enabled": True, "retrieved": True})
    monkeypatch.setattr(main, "supabase", _SessionsTable("user-a"))
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "list_devices", lambda: [{"device_id": "station-a"}])
    monkeypatch.setattr(eeg_poller, "start", lambda *a, **k: {"running": True, "already": False})
    main.eeg_start(main.EegSessionRequest(session_id="session-1", device_id="station-a"), request=None)
    assert stamped == ["session-1"]

    with pytest.raises(main.HTTPException):
        main.eeg_start(main.EegSessionRequest(session_id="session-1", device_id="typo"), request=None)
    assert stamped == ["session-1"]


def test_the_stamp_is_written_once_and_never_raises(monkeypatch):
    calls = []

    class _Sessions:
        def table(self, name):
            calls.append(name)
            q = type("Q", (), {})()
            q.update = lambda patch: (calls.append(patch), q)[1]
            q.eq = lambda *a: (calls.append(("eq",) + a), q)[1]
            q.is_ = lambda *a: (calls.append(("is",) + a), q)[1]
            q.execute = lambda: type("R", (), {"data": []})()
            return q
    monkeypatch.setattr(main, "supabase", _Sessions())
    main._mark_eeg_started("session-1")
    assert calls[0] == "sessions" and "eeg_started_at" in calls[1]
    assert ("eq", "id", "session-1") in calls and ("is", "eeg_started_at", "null") in calls

    class _Down:
        def table(self, name):
            raise RuntimeError("down")
    monkeypatch.setattr(main, "supabase", _Down())
    main._mark_eeg_started("session-1")


@pytest.mark.parametrize("owner,ended_at,status", [
    ("user-a", None, None),
    ("user-a", "2026-09-26T10:00:00Z", 409),      # closed: its alerts are already decided
    ("user-b", None, 403),                        # someone else's session
], ids=["owner, open", "closed", "stranger"])
def test_the_push_report_stamps_only_the_owners_open_session(monkeypatch, owner, ended_at, status):
    """Under push the backend never sees a start; without this, signals_missing never fires."""
    stamped = []
    monkeypatch.setattr(main, "_mark_eeg_started", stamped.append)
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    monkeypatch.setattr(main, "supabase", _SessionsTable(owner, ended_at))
    if status is None:
        assert main.session_eeg_started("session-1", request=None) == {"ok": True}
        assert stamped == ["session-1"]
        return
    with pytest.raises(main.HTTPException) as caught:
        main.session_eeg_started("session-1", request=None)
    assert caught.value.status_code == status and stamped == []


def test_start_falls_back_to_permissive_when_list_devices_unreachable(monkeypatch):
    """An empty known_ids (a transient list_devices() error) must not block a start."""
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    # Consent stubbed open; the retention window is open via conftest.
    monkeypatch.setattr(main, "_consent",
                        lambda _s: {"eeg_enabled": True, "retrieved": True})
    monkeypatch.setattr(main, "supabase", _SessionsTable("user-a"))
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "list_devices", lambda: [])
    monkeypatch.setattr(eeg_poller, "start", lambda *a, **k: {"running": True, "already": False})

    payload = main.EegSessionRequest(session_id="session-1", device_id="anything")
    out = main.eeg_start(payload, request=None)
    assert out["running"] is True


# ── /api/eeg/muse/refresh|connect|disconnect: cross-user guard ──────────
# Each handler: owner allowed, stranger 403, unclaimed open.

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


def test_muse_disconnect_does_not_reserve_the_station(monkeypatch):
    """Disconnect is teardown, not the start of a pairing attempt."""
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_disconnect", lambda device_id: {"ok": True})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    main.eeg_muse_disconnect(request=None, body={"device_id": "station-free"})

    assert "station-free" not in eeg_poller._reservations
    monkeypatch.setattr(eeg_client, "muse_refresh", lambda device_id: {"ok": True})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    assert main.eeg_muse_refresh(request=None, body={"device_id": "station-free"}) == {"ok": True}


def test_muse_disconnect_releases_the_callers_own_reservation(monkeypatch):
    """Disconnecting gives up the station as explicitly as /api/eeg/stop."""
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_refresh", lambda device_id: {"ok": True})
    monkeypatch.setattr(eeg_client, "muse_disconnect", lambda device_id: {"ok": True})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    main.eeg_muse_refresh(request=None, body={"device_id": "station-x"})

    main.eeg_muse_disconnect(request=None, body={"device_id": "station-x"})

    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    assert main.eeg_muse_refresh(request=None, body={"device_id": "station-x"}) == {"ok": True}


# ── a paired headband is its pairer's, past the reservation TTL ──────────

def _paired_by_a(monkeypatch, connected):
    """user-a pairs station-p, then the reservation lapses; the bridge reports `connected`."""
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_connect", lambda name, device_id: {"ok": True})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    main.eeg_muse_connect(request=None, body={"name": "MuseS-1", "device_id": "station-p", "session_id": "s-a"})
    eeg_poller.release_reservation("user-a", "station-p")   # what the 30 s TTL does
    status = {"available": True, "ingestion": {"muse_connected": connected}, "brain_signals": {"tp9": 1.0}}
    monkeypatch.setattr(eeg_client, "get_muse_status", lambda device_id=None: status)
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})


def test_another_user_cannot_read_or_take_a_headband_still_paired(monkeypatch):
    _paired_by_a(monkeypatch, connected=True)

    assert main.eeg_status(request=None, device_id="station-p")["muse"] == {
        "available": False, "reason": "in_use_by_other"}
    assert main.eeg_debug(request=None, device_id="station-p") == {
        "available": False, "reason": "in_use_by_other"}
    for call in (lambda: main.eeg_muse_connect(request=None, body={"name": "MuseS-1", "device_id": "station-p"}),
                 lambda: main.eeg_muse_disconnect(request=None, body={"device_id": "station-p"})):
        with pytest.raises(main.HTTPException) as e:
            call()
        assert e.value.status_code == 403
    # The pairer still reads their own station.
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    assert main.eeg_status(request=None, device_id="station-p")["muse"]["available"] is True


def test_a_headband_the_bridge_reports_gone_frees_the_station(monkeypatch):
    _paired_by_a(monkeypatch, connected=False)

    assert main.eeg_status(request=None, device_id="station-p")["muse"]["available"] is True
    assert "station-p" not in pairings


def test_a_bridge_that_cannot_say_keeps_the_station_closed(monkeypatch):
    _paired_by_a(monkeypatch, connected=True)

    def _down(*_a, **_k):
        raise RuntimeError("sidecar unreachable")
    monkeypatch.setattr(eeg_client, "get_muse_status", _down)
    assert main._station_open_to("user-b", "station-p") is False


def test_start_refuses_another_users_paired_headband(monkeypatch):
    _paired_by_a(monkeypatch, connected=True)
    monkeypatch.setattr(main, "_session_or_403", lambda *_a, **_k: {"user_id": "user-b", "ended_at": None})
    monkeypatch.setattr(eeg_client, "list_devices", lambda: [{"device_id": "station-p"}])
    payload = type("P", (), {"session_id": "s-b", "device_id": "station-p", "record": True})()

    with pytest.raises(main.HTTPException) as e:
        main.eeg_start(payload, request=None)
    assert e.value.status_code == 409
    assert "s-b" not in eeg_poller._active


def test_connect_is_refused_without_eeg_consent(monkeypatch):
    """As /start: otherwise a paired headband with no poller sits on the station."""
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    called = []
    monkeypatch.setattr(eeg_client, "muse_connect", lambda name, device_id: called.append(name))
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-c"})
    monkeypatch.setattr(main, "_consent", lambda _uid: {
        **main._CONSENT_ENABLED_ALL, "eeg_enabled": False, "retrieved": True, "exists": True})

    with pytest.raises(main.HTTPException) as e:
        main.eeg_muse_connect(request=None, body={"name": "MuseS-1", "device_id": "station-c"})
    assert e.value.status_code == 403
    assert called == [] and "station-c" not in pairings


def test_debug_is_not_served_in_production(monkeypatch):
    monkeypatch.setattr(main, "IS_PRODUCTION", True)
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "u"})
    with pytest.raises(main.HTTPException) as e:
        main.eeg_debug(request=None)
    assert e.value.status_code == 404


# ── the pairing lives in the database, so a restart keeps it ─────────────

class _PairingsDb:
    """Only `station_pairings`, rows as the table holds them; `fail` names the operations that raise."""

    def __init__(self, fail=()):
        self.rows, self.fail, self.ops = {}, set(fail), []

    def owners(self):
        return {d: r["user_id"] for d, r in self.rows.items()}

    def table(self, name):
        assert name == "station_pairings", name
        db = self

        class _Q:
            def __init__(self):
                self.op, self.filters, self.row = None, {}, None

            def select(self, *_a):
                self.op = "select"
                return self

            def upsert(self, row, on_conflict=None):
                assert on_conflict == "device_id"
                self.op, self.row = "upsert", row
                return self

            def update(self, fields):
                self.op, self.row = "update", fields
                return self

            def delete(self):
                self.op = "delete"
                return self

            def eq(self, col, val):
                self.filters[col] = val
                return self

            def lt(self, col, val):
                # Timestamps only; compared as instants, not as strings.
                self.below = (col, main._parse_ts(val))
                return self

            def limit(self, _n):
                return self

            def _keep(self, row):
                if not all(row.get(c) == v for c, v in self.filters.items()):
                    return False
                col, bound = getattr(self, "below", (None, None))
                return col is None or main._parse_ts(row.get(col)) < bound

            def execute(self):
                db.ops.append(self.op)
                if self.op in db.fail:
                    raise RuntimeError(f"{self.op} failed")
                if self.op == "upsert":
                    db.rows[self.row["device_id"]] = {k: v for k, v in self.row.items() if k != "device_id"}
                    return type("R", (), {"data": [self.row]})()
                hit = [{"device_id": d, **r} for d, r in db.rows.items() if self._keep({"device_id": d, **r})]
                for r in hit:
                    if self.op == "delete":
                        db.rows.pop(r["device_id"])
                    elif self.op == "update":
                        db.rows[r["device_id"]].update(self.row)
                return type("R", (), {"data": hit})()
        return _Q()


@pytest.fixture
def pairings_db(monkeypatch):
    for name, fn in real_pairing_funcs().items():
        monkeypatch.setattr(main, name, fn)

    def install(**kw):
        db = _PairingsDb(**kw)
        monkeypatch.setattr(main, "supabase", db)
        return db
    return install


def test_a_restart_does_not_free_a_paired_headband(monkeypatch, pairings_db):
    """Nothing in memory survives a restart; the row does, so user-b is still refused."""
    db = pairings_db()
    _paired_by_a(monkeypatch, connected=True)
    assert db.owners() == {"station-p": "user-a"}
    assert main._station_open_to("user-b", "station-p") is False
    assert main._station_open_to("user-a", "station-p") is True


def test_the_row_goes_when_the_bridge_reports_the_headband_gone(monkeypatch, pairings_db):
    db = pairings_db()
    _paired_by_a(monkeypatch, connected=False)
    assert main._station_open_to("user-b", "station-p") is True
    assert db.rows == {}


def test_disconnecting_clears_the_row(monkeypatch, pairings_db):
    db = pairings_db()
    _paired_by_a(monkeypatch, connected=True)
    monkeypatch.setattr(eeg_client, "muse_disconnect", lambda device_id: {"ok": True})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    main.eeg_muse_disconnect(request=None, body={"device_id": "station-p"})
    assert db.rows == {}


def test_an_unreadable_pairing_is_someone_elses_until_the_bridge_says_gone(monkeypatch, pairings_db):
    pairings_db(fail={"select"})
    monkeypatch.setattr(eeg_client, "get_muse_status",
                        lambda device_id=None: {"ingestion": {"muse_connected": True}})
    assert main._station_open_to("user-b", "station-p") is False
    monkeypatch.setattr(eeg_client, "get_muse_status",
                        lambda device_id=None: {"ingestion": {"muse_connected": False}})
    assert main._station_open_to("user-b", "station-p") is True


def test_a_pairing_that_cannot_be_recorded_is_undone(monkeypatch, pairings_db):
    """Left connected and unrecorded, the headband would be open to anyone."""
    pairings_db(fail={"upsert"})
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_connect", lambda name, device_id: {"ok": True})
    dropped = []
    monkeypatch.setattr(eeg_client, "muse_disconnect", lambda device_id: dropped.append(device_id))
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    with pytest.raises(main.HTTPException) as e:
        main.eeg_muse_connect(request=None, body={"name": "MuseS-1", "device_id": "station-p"})
    assert e.value.status_code == 503
    assert dropped == ["station-p"]
    # Its reservation goes too, so the station is not held by a failed connect.
    assert eeg_poller.can_use_device("user-b", "station-p") is True


def test_the_status_poll_reads_the_sidecar_once_when_the_gate_had_to_ask(monkeypatch):
    """A released station is read by the gate; the poll reuses that rather than asking again."""
    _paired_by_a(monkeypatch, connected=False)
    reads = []
    monkeypatch.setattr(eeg_client, "get_muse_status",
                        lambda device_id=None: reads.append(device_id) or {"ingestion": {"muse_connected": False}})
    main.eeg_status(request=None, device_id="station-p")
    assert reads == ["station-p"]


# ── a pairing ends with its pairer's lesson, or when nothing polls for it ─

def _pairing_seen(db, device, user, seconds_ago):
    from datetime import timedelta
    db.rows[device] = {"user_id": user,
                       "seen_at": (main._utc_now() - timedelta(seconds=seconds_ago)).isoformat()}


def test_stopping_the_poller_frees_the_station(monkeypatch):
    _paired_by_a(monkeypatch, connected=True)
    monkeypatch.setattr(main, "_session_or_403", lambda *_a, **_k: {"user_id": "user-a"})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    payload = type("P", (), {"session_id": "s-a", "device_id": "station-p"})()
    main.eeg_stop(payload, request=None)
    assert "station-p" not in pairings
    assert main._station_open_to("user-b", "station-p") is True


def test_closing_the_pairers_session_frees_the_station(monkeypatch):
    """Every close site goes through `_close_session`, the stale sweep included."""
    _paired_by_a(monkeypatch, connected=True)
    monkeypatch.setattr(main, "_claim_session_close", lambda *_a: True)
    monkeypatch.setattr(main, "_answer_counts", lambda *_a: (0, 0, 0))
    monkeypatch.setattr(main, "_discard_if_nothing_recorded", lambda *_a, **_k: True)
    main._close_session("user-a", {"id": "s-a"}, "2026-09-28T10:00:00+00:00")
    assert "station-p" not in pairings


def test_a_pairing_nothing_has_polled_for_is_released(monkeypatch, pairings_db):
    """The pairer closed the tab: the next student is not told to wait for the headband to go off."""
    db = pairings_db()
    _pairing_seen(db, "station-p", "user-a", main._PAIRING_IDLE_SEC + 5)
    asked = []
    monkeypatch.setattr(eeg_client, "get_muse_status",
                        lambda device_id=None: asked.append(1) or {"ingestion": {"muse_connected": True}})
    assert main._station_open_to("user-b", "station-p") is True
    assert db.rows == {} and asked == []


def test_the_pairers_polls_keep_it_theirs(monkeypatch, pairings_db):
    db = pairings_db()
    _pairing_seen(db, "station-p", "user-a", main._PAIRING_IDLE_SEC - 10)
    assert main._station_open_to("user-a", "station-p") is True
    # Refreshed, so the idle clock restarts.
    assert main._parse_ts(db.rows["station-p"]["seen_at"]) > main._utc_now() - __import__("datetime").timedelta(seconds=5)
    monkeypatch.setattr(eeg_client, "get_muse_status",
                        lambda device_id=None: {"ingestion": {"muse_connected": True}})
    assert main._station_open_to("user-b", "station-p") is False


def test_a_recent_pairing_is_not_rewritten_on_every_poll(pairings_db):
    db = pairings_db()
    _pairing_seen(db, "station-p", "user-a", 5)
    main._station_open_to("user-a", "station-p")
    assert "update" not in db.ops


# ── the status poll does not read the table every 5 s ────────────────────

def test_a_student_whose_poller_holds_the_station_reads_no_pairing(monkeypatch, pairings_db):
    """It refreshes without reading, and no more often than the refresh interval."""
    db = pairings_db()
    monkeypatch.setattr(eeg_poller, "live_poller_user", lambda _d: "user-a")
    monkeypatch.setattr(eeg_poller, "can_use_device", lambda u, _d: u == "user-a")
    assert main._station_open_to("user-a", "station-p") is True
    assert main._station_open_to("user-a", "station-p") is True
    assert db.ops == ["update"]


def test_a_poller_stopping_mid_lesson_does_not_leave_the_pairing_looking_idle(monkeypatch, pairings_db):
    """Consent withdrawn stops the poller with the page still open; the headband stays the pairer's."""
    db = pairings_db()
    _pairing_seen(db, "station-p", "user-a", main._PAIRING_IDLE_SEC + 60)   # connected long ago
    monkeypatch.setattr(eeg_poller, "live_poller_user", lambda _d: "user-a")
    monkeypatch.setattr(eeg_poller, "can_use_device", lambda u, _d: u == "user-a")
    main._station_open_to("user-a", "station-p")                            # a poll while recording
    monkeypatch.setattr(eeg_poller, "live_poller_user", lambda _d: None)    # the poller stops
    monkeypatch.setattr(eeg_poller, "can_use_device", lambda _u, _d: True)
    monkeypatch.setattr(eeg_client, "get_muse_status",
                        lambda device_id=None: {"ingestion": {"muse_connected": True}})
    assert main._station_open_to("user-b", "station-p") is False
    assert db.owners() == {"station-p": "user-a"}


def test_an_idle_look_from_the_cache_does_not_release_a_pairing_just_refreshed(monkeypatch, pairings_db):
    """Another worker refreshed it after this one cached `seen_at`: the delete must find nothing."""
    from datetime import timedelta
    db = pairings_db()
    _pairing_seen(db, "station-p", "user-a", 1)
    stale = main._utc_now() - timedelta(seconds=main._PAIRING_IDLE_SEC + 60)
    main._pairing_cache["station-p"] = (__import__("time").monotonic(), "user-a", stale)
    monkeypatch.setattr(eeg_client, "get_muse_status",
                        lambda device_id=None: {"ingestion": {"muse_connected": True}})
    assert main._station_open_to("user-b", "station-p") is False
    assert db.owners() == {"station-p": "user-a"}


def test_closing_another_session_leaves_the_pairing(monkeypatch):
    """The sweep closing a 6-hour-old abandoned session must not free the lesson running now."""
    _paired_by_a(monkeypatch, connected=True)                                # paired in s-a
    monkeypatch.setattr(main, "_claim_session_close", lambda *_a: True)
    monkeypatch.setattr(main, "_answer_counts", lambda *_a: (0, 0, 0))
    monkeypatch.setattr(main, "_discard_if_nothing_recorded", lambda *_a, **_k: True)
    main._close_session("user-a", {"id": "s-old"}, "2026-09-28T10:00:00+00:00")
    assert pairings == {"station-p": "user-a"}


def test_the_pairing_records_its_session(monkeypatch, pairings_db):
    db = pairings_db()
    _paired_by_a(monkeypatch, connected=True)
    assert db.rows["station-p"]["session_id"] == "s-a"


def test_an_owner_is_cached_and_free_is_not(monkeypatch, pairings_db):
    """A stale owner refuses, the safe direction; a stale "free" would admit a takeover."""
    db = pairings_db()
    monkeypatch.setattr(eeg_client, "get_muse_status",
                        lambda device_id=None: {"ingestion": {"muse_connected": True}})
    main._station_open_to("user-b", "station-free")
    main._station_open_to("user-b", "station-free")
    assert db.ops.count("select") == 2
    _pairing_seen(db, "station-p", "user-a", 1)
    db.ops.clear()
    main._station_open_to("user-b", "station-p")
    main._station_open_to("user-b", "station-p")
    assert db.ops.count("select") == 1
