"""Tests for the EEG status endpoints' tolerance of a missing/misconfigured token.

Since the auth hardening in #25, eeg_client's header helpers raise RuntimeError
when EEG_API_TOKEN / EEG_ADMIN_TOKEN are unset, and get_state / get_muse_status
deliberately let that propagate rather than mask a config error as an outage.
These endpoints must catch it and report a status, not turn a developer's
missing-token setup into a bare 500.
"""
import os

# main.py builds a Supabase client at import time and raises without these.
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

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


# ── the pre-claim pairing window (#34), exercised end to end ─────────────
#
# The tests above cover a station a live poller already owns. This is the gap
# that guard deliberately didn't close: the few seconds between two users
# both reaching for the same *unclaimed* station, before either has a poller.
# Real registry throughout, same reasoning as the block above -- this proves
# the endpoint wiring, not just eeg_poller.reserve_device in isolation.

def test_two_users_racing_an_unclaimed_station_the_second_is_blocked(monkeypatch):
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_refresh", lambda device_id: {"ok": True})

    # user-a scans first and wins the station.
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    assert main.eeg_muse_refresh(request=None, body={"device_id": "station-race"}) == {"ok": True}

    # user-b, racing a moment later, is refused -- not just on a second
    # refresh, but on reading the station too. Before #34 this read the
    # station's live snapshot freely, since no live poller existed yet to
    # trip can_use_device.
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

    # user-a, meanwhile, can keep interacting with the station they reserved.
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    assert main.eeg_muse_refresh(request=None, body={"device_id": "station-race"}) == {"ok": True}


def test_a_failed_refresh_releases_its_reservation_instead_of_squatting(monkeypatch):
    """reserve_device claims the station before the endpoint knows whether the
    attempt will actually go anywhere. A request that never reaches the
    bridge (sidecar down) is not evidence of active pairing worth protecting
    -- squatting the claim for it would lock a *different* user out of a
    station neither of them is doing anything with."""
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
    """The release above must not overreach: it is scoped to the caller who
    just failed, not to every reservation the module knows about."""
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_refresh", lambda device_id: {"ok": True})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    main.eeg_muse_refresh(request=None, body={"device_id": "station-a"})

    # A second, unrelated user's failed attempt on a *different* station must
    # not touch user-a's still-active claim on station-a.
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
    """The narrower failure mode the cross-user test above can't catch: one
    user legitimately holding two reservations at once, where a release
    scoped only to user_id -- not device_id -- would drop both on a failure
    that only concerned one of them."""
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
    # station-b, meanwhile, is genuinely free again -- the failed connect
    # released only what it had just claimed.
    monkeypatch.setattr(eeg_client, "muse_refresh", lambda device_id: {"ok": True})
    assert main.eeg_muse_refresh(request=None, body={"device_id": "station-b"}) == {"ok": True}


def test_closing_one_session_spares_another_sessions_reservation(monkeypatch):
    """#88's failure scenario, end to end through the endpoints.

    A student is mid-pairing station B under session S2 when a reload calls
    /api/sessions/start, finds S1 stale, and stops it. S1's close must not
    release S2's station: the release is keyed on user_id, so before session_id
    was threaded through it dropped every reservation the student held.

    Bounded, and still worth closing -- station B became claimable by a
    different student up to RESERVATION_TTL_SECONDS early, in the middle of
    someone actively pairing it.
    """
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_refresh", lambda device_id: {"ok": True})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})

    # Two sessions, two stations, one student -- the state the old docstring
    # admitted was reachable and could not scope.
    main.eeg_muse_refresh(request=None,
                          body={"device_id": "station-a", "session_id": "S1"})
    main.eeg_muse_refresh(request=None,
                          body={"device_id": "station-b", "session_id": "S2"})

    monkeypatch.setattr(main, "supabase", _SessionsTable("user-a"))
    main.eeg_stop(main.EegSessionRequest(session_id="S1"), request=None)

    # S1's station is free, which is the point of the close.
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    assert main.eeg_muse_refresh(
        request=None, body={"device_id": "station-a"}) == {"ok": True}

    # S2's is not. Without the fix this 200s and a stranger takes the station
    # out from under an active pairing flow.
    with pytest.raises(main.HTTPException) as exc_info:
        main.eeg_muse_refresh(request=None, body={"device_id": "station-b"})
    assert exc_info.value.status_code == 403


def test_a_reservation_with_no_session_is_still_released(monkeypatch):
    """The compatibility path, and the reason unattributed entries are dropped
    rather than spared.

    A frontend that has not been updated sends no session_id. Sparing those
    would look safer and would be worse: no session close could ever name one,
    so an abandoned pairing would hold a station against everybody until its
    TTL, instead of being released a little early for one person.
    """
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
    """A pairing flow calls refresh repeatedly. If a later call that cannot
    name a session overwrote the recorded one with None, the scoping would
    decay during the flow rather than fail outright -- the harder version to
    notice, since it needs a particular call order to show up."""
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

    # user-a gave up on pairing without ever reaching /start -- no live
    # poller exists for eeg_poller.stop to find, but the reservation from the
    # scan above is still theirs to release.
    monkeypatch.setattr(main, "supabase", _SessionsTable("user-a"))
    main.eeg_stop(main.EegSessionRequest(session_id="session-1"), request=None)

    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    assert main.eeg_muse_refresh(request=None, body={"device_id": "station-race"}) == {"ok": True}


# ── /api/eeg/start: device_id validation ─────────────────────────────────

def test_start_rejects_unknown_device_id(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    # The sessions stub answers for one table; /api/eeg/start also gates on
    # consent now, and on the retention window -- the latter comes from the
    # autouse fixture in conftest. Only consent is stubbed here, so the real
    # `_may_record` still composes the two; these stay tests of device
    # handling and both gates keep their own files.
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
    # The sessions stub answers for one table; /api/eeg/start also gates on
    # consent now, and on the retention window -- the latter comes from the
    # autouse fixture in conftest. Only consent is stubbed here, so the real
    # `_may_record` still composes the two; these stay tests of device
    # handling and both gates keep their own files.
    monkeypatch.setattr(main, "_consent",
                        lambda _s: {"eeg_enabled": True, "retrieved": True})
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
    # The sessions stub answers for one table; /api/eeg/start also gates on
    # consent now, and on the retention window -- the latter comes from the
    # autouse fixture in conftest. Only consent is stubbed here, so the real
    # `_may_record` still composes the two; these stay tests of device
    # handling and both gates keep their own files.
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


def test_muse_disconnect_does_not_reserve_the_station(monkeypatch):
    """Unlike refresh/connect, disconnect is a teardown action, not the start
    of a pairing attempt -- calling it against a free station must not claim
    it. An earlier version of this fix used reserve_device here too, which
    meant a disconnect against nobody's station locked it out from under
    anyone else for the TTL, indefinitely renewable by repeating the call."""
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_disconnect", lambda device_id: {"ok": True})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    main.eeg_muse_disconnect(request=None, body={"device_id": "station-free"})

    assert "station-free" not in eeg_poller._reservations
    monkeypatch.setattr(eeg_client, "muse_refresh", lambda device_id: {"ok": True})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    assert main.eeg_muse_refresh(request=None, body={"device_id": "station-free"}) == {"ok": True}


def test_muse_disconnect_releases_the_callers_own_reservation(monkeypatch):
    """The other half of #34's leftover gap: a user who scanned/connected and
    then disconnects instead of pairing is giving up on the station exactly
    as explicitly as calling /api/eeg/stop -- without a release here, the
    station stayed locked to them for up to the TTL after they visibly moved
    on, even though they still owned the reservation and can_use_device let
    the disconnect through."""
    monkeypatch.setattr(eeg_client, "is_alive", lambda *a, **k: True)
    monkeypatch.setattr(eeg_client, "muse_refresh", lambda device_id: {"ok": True})
    monkeypatch.setattr(eeg_client, "muse_disconnect", lambda device_id: {"ok": True})
    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-a"})
    main.eeg_muse_refresh(request=None, body={"device_id": "station-x"})

    main.eeg_muse_disconnect(request=None, body={"device_id": "station-x"})

    monkeypatch.setattr(main, "get_user", lambda request: {"id": "user-b"})
    assert main.eeg_muse_refresh(request=None, body={"device_id": "station-x"}) == {"ok": True}
