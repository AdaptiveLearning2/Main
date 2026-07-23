"""Tests for the EEG poller's device-claim guard.

Post-multi-tenancy, several users can each have a live poller thread at once.
The sidecar itself is a single shared stream (one physical headband/simulator
connection), so two different users' pollers both reading it would silently
attribute one device's signal to two different sessions. eeg_poller.start()
must reject a second user trying to claim a device another user's live
poller already holds, while still letting the *same* user replace their own
poller (e.g. switching sessions or devices).
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import eeg_client  # noqa: E402
import eeg_poller  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Isolate _active across tests and stub out real sidecar/network calls.

    Teardown goes through the public eeg_poller.stop() (as production code
    does) rather than poking _Poller.stop()/is_alive()/join() directly:
    _Poller._stop is a threading.Event that shadows Thread's own internal
    _stop() method, so is_alive()/join() crash the *first* time either is
    called after the thread has actually finished. That's a real,
    pre-existing bug (unrelated to this change -- nothing in production
    calls join(), and is_alive() is only ever called on entries still in
    _active, which today are always removed atomically with their stop()),
    flagged separately rather than fixed here.
    """
    monkeypatch.setattr(eeg_client, "start_session", lambda: {"ok": True})
    monkeypatch.setattr(eeg_client, "stop_session", lambda: {"ok": True})
    monkeypatch.setattr(eeg_client, "get_state", lambda timeout=2.0: None)
    monkeypatch.setattr(eeg_poller, "POLL_INTERVAL", 0.01)
    eeg_poller._active.clear()
    yield
    for sid in list(eeg_poller._active):
        eeg_poller.stop(sid)


class _FakeSupabase:
    def table(self, *_a, **_k):
        raise AssertionError("no data should be inserted in these tests")


def test_different_users_same_device_conflict():
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "muse-ABCD")
    with pytest.raises(eeg_poller.DeviceClaimedError):
        eeg_poller.start(_FakeSupabase(), "user-b", "session-2", "muse-ABCD")
    # user-a's poller must still be the one holding session-1.
    assert eeg_poller._active["session-1"].user_id == "user-a"


def test_different_users_different_device_both_allowed():
    out_a = eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "muse-ABCD")
    out_b = eeg_poller.start(_FakeSupabase(), "user-b", "session-2", "muse-WXYZ")
    assert out_a["running"] and out_b["running"]
    assert set(eeg_poller._active) == {"session-1", "session-2"}


def test_same_user_replaces_own_poller_even_on_different_device():
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "muse-ABCD")
    time.sleep(0.05)
    out = eeg_poller.start(_FakeSupabase(), "user-a", "session-2", "muse-WXYZ")
    assert out["running"] and not out["already"]
    # old session's poller was replaced, not left dangling as a stale claim.
    assert "session-1" not in eeg_poller._active
    assert eeg_poller._active["session-2"].device_id == "muse-WXYZ"


def test_restarting_same_session_is_a_noop():
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "muse-ABCD")
    out = eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "muse-ABCD")
    assert out == {"running": True, "already": True}


def test_released_device_can_be_reclaimed_by_another_user():
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "muse-ABCD")
    eeg_poller.stop("session-1")  # the same path /api/eeg/stop and the
                                  # stale-session sweep in main.py use
    out = eeg_poller.start(_FakeSupabase(), "user-b", "session-2", "muse-ABCD")
    assert out["running"]
