"""Tests for the EEG poller's device-claim guard.

Several users can each have a live poller thread at once, one per device_id
(sidecar "station"). Each station is its own independent physical stream, but
within a *single* station two different users' pollers both reading it would
silently attribute one device's signal to two different sessions.
eeg_poller.start() must reject a second user trying to claim a device_id
another user's live poller already holds, while still letting the *same*
user replace their own poller (e.g. switching sessions or devices).
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
    yield
    for sid in list(eeg_poller._active):
        eeg_poller.stop(sid)


class _FakeSupabase:
    def table(self, *_a, **_k):
        raise AssertionError("no data should be inserted in these tests")


def test_different_users_same_device_conflict():
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    with pytest.raises(eeg_poller.DeviceClaimedError):
        eeg_poller.start(_FakeSupabase(), "user-b", "session-2", "station-a")
    # user-a's poller must still be the one holding session-1.
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
    # old session's poller was replaced, not left dangling as a stale claim.
    assert "session-1" not in eeg_poller._active
    assert eeg_poller._active["session-2"].device_id == "station-b"


def test_restarting_same_session_is_a_noop():
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    out = eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    assert out == {"running": True, "already": True}


def test_released_device_can_be_reclaimed_by_another_user():
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    eeg_poller.stop("session-1")  # the same path /api/eeg/stop and the
                                  # stale-session sweep in main.py use
    out = eeg_poller.start(_FakeSupabase(), "user-b", "session-2", "station-a")
    assert out["running"]


def test_can_use_device_blocks_other_user_on_live_station():
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    assert eeg_poller.can_use_device("user-a", "station-a")      # owner
    assert not eeg_poller.can_use_device("user-b", "station-a")  # stranger
    assert eeg_poller.can_use_device("user-b", "station-b")      # unclaimed


def test_is_alive_after_poller_finishes_does_not_raise():
    """Regression: _Poller used to store its stop signal in an attribute named
    `_stop`, shadowing threading.Thread's own private `_stop()` method (which
    Thread calls internally once the tstate lock shows the thread has actually
    finished -- from inside is_alive() and join()). That made the first
    is_alive()/join() call on an already-finished poller raise
    TypeError: 'Event' object is not callable -- exactly the check
    eeg_poller.start()'s device-claim guard runs against every other live
    poller on every call, so a poller that died mid-run (e.g. on malformed
    sidecar data) would take down the next different-user start() with it.
    """
    p = eeg_poller._Poller(_FakeSupabase(), "user-a", "session-1", "station-a")
    p.start()
    p.stop()
    # Wait until the thread finishes rather than asserting on a fixed deadline.
    # The run loop waits on the stop event, so this is normally immediate --
    # but a loaded runner can still make is_alive() legitimately True for a
    # moment, and a fixed deadline here would be flake waiting to happen.
    #
    # The regression this guards is is_alive()/join() *raising*, not the thread
    # being quick, so the loop below exercises it on every iteration and the
    # generous deadline only fires for a poller that genuinely never exits.
    deadline = time.monotonic() + 15.0
    while p.is_alive() and time.monotonic() < deadline:
        p.join(timeout=0.05)
    assert p.is_alive() is False, "poller thread did not exit after stop()"
