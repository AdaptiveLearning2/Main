"""Tests for the EEG poller's device-claim guard.

Several users can each have a live poller thread at once, one per device_id
(sidecar "station"). Two different users' pollers reading the same station
would silently attribute one device's signal to two different sessions.
`eeg_poller.start()` must reject a second user claiming a device_id another
user's live poller already holds, while still letting the same user replace
their own poller (e.g. switching sessions or devices).
"""
import time

import pytest  # noqa: E402

import eeg_client  # noqa: E402
import eeg_poller  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Isolate _active across tests and stub out real sidecar/network calls."""
    monkeypatch.setattr(eeg_client, "start_session", lambda device_id=eeg_client.DEFAULT_DEVICE_ID: {"ok": True})
    monkeypatch.setattr(eeg_client, "stop_session", lambda device_id=eeg_client.DEFAULT_DEVICE_ID: {"ok": True})
    monkeypatch.setattr(eeg_client, "get_state", lambda device_id=eeg_client.DEFAULT_DEVICE_ID, timeout=2.0: None)
    monkeypatch.setattr(eeg_poller, "POLL_INTERVAL", 0.01)
    eeg_poller._active.clear()
    eeg_poller._reservations.clear()
    yield
    for sid in list(eeg_poller._active):
        eeg_poller.stop(sid)
    eeg_poller._reservations.clear()


class _FakeSupabase:
    def table(self, *_a, **_k):
        raise AssertionError("no data should be inserted in these tests")


def test_different_users_same_device_conflict():
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    with pytest.raises(eeg_poller.DeviceClaimedError):
        eeg_poller.start(_FakeSupabase(), "user-b", "session-2", "station-a")
    # user-a's poller must still hold session-1.
    assert eeg_poller._active["session-1"].user_id == "user-a"


def test_different_users_different_device_both_allowed():
    out_a = eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    out_b = eeg_poller.start(_FakeSupabase(), "user-b", "session-2", "station-b")
    assert out_a["running"] and out_b["running"]
    assert set(eeg_poller._active) == {"session-1", "session-2"}


def test_same_user_replaces_own_poller_even_on_different_device():
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    time.sleep(0.05)
    out = eeg_poller.start(_FakeSupabase(), "user-a", "session-2", "station-b")
    assert out["running"] and not out["already"]
    # The old session's poller was replaced, not left dangling as a stale claim.
    assert "session-1" not in eeg_poller._active
    assert eeg_poller._active["session-2"].device_id == "station-b"


def test_restarting_same_session_is_a_noop():
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    out = eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    assert out == {"running": True, "already": True}


def test_released_device_can_be_reclaimed_by_another_user():
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    eeg_poller.stop("session-1")  # same path /api/eeg/stop and main.py's
                                  # stale-session sweep both use
    out = eeg_poller.start(_FakeSupabase(), "user-b", "session-2", "station-a")
    assert out["running"]


def test_can_use_device_blocks_other_user_on_live_station():
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    assert eeg_poller.can_use_device("user-a", "station-a")      # owner
    assert not eeg_poller.can_use_device("user-b", "station-a")  # stranger
    assert eeg_poller.can_use_device("user-b", "station-b")      # unclaimed


# ── pre-claim reservations ───────────────────────────────────────────────────
#
# eeg_poller.start()'s claim guard only recognises an owner once a poller
# exists, leaving the scan/connect window before that open to anyone who knows
# the device_id. reserve_device narrows that window to whoever reserves first.

def test_reserve_device_first_caller_wins_unclaimed_station():
    assert eeg_poller.reserve_device("user-a", "station-a")
    assert not eeg_poller.reserve_device("user-b", "station-a")
    # The loser is genuinely locked out of both reading and controlling the
    # device, not just of re-reserving it.
    assert not eeg_poller.can_use_device("user-b", "station-a")


def test_reserve_device_is_idempotent_and_refreshable_for_the_same_user():
    assert eeg_poller.reserve_device("user-a", "station-a")
    # A second scan, or a connect attempt following the scan -- both are the
    # same user continuing to pair, not a second claimant.
    assert eeg_poller.reserve_device("user-a", "station-a")
    assert eeg_poller.can_use_device("user-a", "station-a")


def test_can_use_device_respects_an_unexpired_reservation():
    eeg_poller.reserve_device("user-a", "station-a")
    assert eeg_poller.can_use_device("user-a", "station-a")       # reserver
    assert not eeg_poller.can_use_device("user-b", "station-a")   # stranger
    assert eeg_poller.can_use_device("user-b", "station-b")       # untouched


def test_reservation_expires_and_reopens_the_station(monkeypatch):
    """An abandoned scan (tab closed mid-pairing, /stop never called) must not
    permanently lock a physical station."""
    monkeypatch.setattr(eeg_poller, "RESERVATION_TTL_SECONDS", 0.05)
    assert eeg_poller.reserve_device("user-a", "station-a")
    assert not eeg_poller.reserve_device("user-b", "station-a")
    time.sleep(0.08)
    assert eeg_poller.reserve_device("user-b", "station-a")
    assert eeg_poller.can_use_device("user-b", "station-a")
    assert not eeg_poller.can_use_device("user-a", "station-a")


def test_a_live_poller_outranks_a_reservation():
    """A live poller always wins over a reservation -- the reservation is only
    consulted when no live poller already answers the question."""
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    # user-b can't reserve a station that already has a live poller.
    assert not eeg_poller.reserve_device("user-b", "station-a")


def test_start_refuses_a_station_someone_else_has_reserved():
    """A caller can skip the scan/connect UI and hit /start directly, so
    reserve_device's claim must bind /start too, or a stranger could walk
    around someone else's in-progress pairing."""
    assert eeg_poller.reserve_device("user-a", "station-a")
    with pytest.raises(eeg_poller.DeviceClaimedError):
        eeg_poller.start(_FakeSupabase(), "user-b", "session-2", "station-a")


def test_start_by_the_reserving_user_succeeds_and_clears_the_reservation():
    eeg_poller.reserve_device("user-a", "station-a")
    out = eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    assert out["running"]
    # Ownership moved to the (stronger) live poller. Leaving the reservation
    # behind would just be a dead slot in the registry until its TTL passes.
    assert "station-a" not in eeg_poller._reservations


def test_release_reservation_frees_the_station_for_another_user():
    eeg_poller.reserve_device("user-a", "station-a")
    eeg_poller.release_reservation("user-a")
    assert eeg_poller.reserve_device("user-b", "station-a")


def test_release_reservation_only_touches_the_calling_users_holdings():
    eeg_poller.reserve_device("user-a", "station-a")
    eeg_poller.reserve_device("user-b", "station-b")
    eeg_poller.release_reservation("user-a")
    assert "station-a" not in eeg_poller._reservations
    assert not eeg_poller.reserve_device("user-c", "station-b")  # untouched


def test_release_reservation_with_device_id_spares_the_users_other_holdings():
    """main.py's control endpoints rely on this scoping: a failed request on
    one device must release only that device's claim, not every reservation
    the same user holds elsewhere."""
    eeg_poller.reserve_device("user-a", "station-a")
    eeg_poller.reserve_device("user-a", "station-b")
    eeg_poller.release_reservation("user-a", "station-a")
    assert "station-a" not in eeg_poller._reservations
    assert not eeg_poller.reserve_device("user-c", "station-b")  # untouched


def test_release_reservation_with_device_id_ignores_a_different_users_claim():
    """A device_id-scoped release must still check ownership, not just delete
    the key -- otherwise one user's failed call could evict a different
    user's valid reservation on the same device just by naming it."""
    eeg_poller.reserve_device("user-a", "station-a")
    eeg_poller.release_reservation("user-b", "station-a")
    assert not eeg_poller.reserve_device("user-c", "station-a")  # still user-a's


def test_stop_releases_a_reservation_even_with_no_poller_to_pop():
    """A user who scanned/connected and gave up before reaching /start holds
    a reservation with nothing in _active for it, so stop() needs an explicit
    user_id to find and release it. Matters at three call sites: end_session,
    start_session's stale cleanup, and class_live's stale sweep."""
    eeg_poller.reserve_device("user-a", "station-a")
    eeg_poller.stop("session-that-never-started", "user-a")
    assert eeg_poller.reserve_device("user-b", "station-a")


def test_stop_falls_back_to_the_popped_pollers_own_user_id():
    """A caller that omits user_id (the parameter is optional) must still
    release whatever the popped poller's own user held, falling back to
    p.user_id."""
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    eeg_poller.reserve_device("user-a", "station-b")  # a second, unrelated hold
    eeg_poller.stop("session-1")
    # station-b was never touched by session-1's own device (station-a), but
    # it belongs to the same user_id the popped poller carried -- this proves
    # the fallback actually read p.user_id instead of doing nothing.
    assert eeg_poller.reserve_device("user-c", "station-b")


def test_stop_all_joins_every_poller_and_empties_the_registry():
    """stop_all keeps a printing daemon thread from surviving into interpreter
    shutdown, where a print against an already-held stdout lock aborts the
    process. The autouse fixture in conftest.py runs it after every test, so a
    break here would show up as unexplained flake elsewhere -- this pins the
    behaviour directly.

    The unregistered poller matters: start() pops a same-user poller out of
    _active while its thread keeps running, so a stop_all that reads the
    registry instead of the live threads would leave that one running.

    That state is built by popping the registry entry by hand, not by letting
    a third start() do it -- start() also signals the poller it pops, and with
    the sidecar stubbed that thread would exit almost immediately, making a
    count taken afterward race the thread's own exit. A poller nobody has
    signalled can't exit on its own, so every count below is exact.
    """
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    eeg_poller.start(_FakeSupabase(), "user-b", "session-2", "station-b")
    eeg_poller.start(_FakeSupabase(), "user-c", "session-3", "station-c")
    orphan = eeg_poller._active.pop("session-1")

    assert {p.session_id for p in eeg_poller.live_pollers()} == {
        "session-1", "session-2", "session-3",
    }
    assert set(eeg_poller._active) == {"session-2", "session-3"}

    assert eeg_poller.stop_all(timeout=15.0) == 3
    assert eeg_poller.live_pollers() == []
    assert eeg_poller._active == {}
    assert not orphan.is_alive()


def test_is_alive_after_poller_finishes_does_not_raise():
    """`_Poller` must not name its stop signal `_stop`, since that shadows
    `threading.Thread`'s own private `_stop()` method, which Thread calls
    internally from `is_alive()`/`join()` once the thread has finished. A
    name collision there makes the first `is_alive()`/`join()` call on an
    already-finished poller raise `TypeError: 'Event' object is not
    callable` -- exactly the check `eeg_poller.start()`'s device-claim guard
    runs against every other live poller on every call, so a poller that died
    mid-run (e.g. on malformed sidecar data) would take down the next
    different-user start() with it.
    """
    p = eeg_poller._Poller(_FakeSupabase(), "user-a", "session-1", "station-a")
    p.start()
    p.stop()
    # Waits until the thread finishes rather than asserting on a fixed
    # deadline. The run loop waits on the stop event so this is normally
    # immediate, but a loaded runner can briefly leave is_alive() True.
    #
    # What's being guarded against is is_alive()/join() raising, not the
    # thread being slow, so the loop below calls them on every iteration and
    # the generous deadline only fires if a poller genuinely never exits.
    deadline = time.monotonic() + 15.0
    while p.is_alive() and time.monotonic() < deadline:
        p.join(timeout=0.05)
    assert p.is_alive() is False, "poller thread did not exit after stop()"
