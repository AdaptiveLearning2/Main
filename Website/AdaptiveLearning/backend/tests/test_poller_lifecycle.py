"""A pull poller stops once its page is gone, and waits out an admin switch rather than stopping."""
import os
import threading
import time

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import eeg_poller  # noqa: E402
import main  # noqa: E402
from tests.test_poller_heart import _FakeSupabase  # noqa: E402


@pytest.fixture
def stream(monkeypatch):
    """A sidecar ticking a fresh timestamp per read; `reads` counts loop iterations."""
    reads = []
    lock = threading.Lock()

    def get_state(*_a, **_k):
        with lock:
            reads.append(1)
            return {"timestamp": f"t{len(reads)}"}

    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "pull")
    monkeypatch.setattr(eeg_poller, "POLL_INTERVAL", 0.01)
    monkeypatch.setattr(eeg_poller, "CONSENT_RECHECK_SECONDS", 0.0)
    monkeypatch.setattr(eeg_poller.eeg_client, "start_session", lambda *_a, **_k: {"ok": True})
    monkeypatch.setattr(eeg_poller.eeg_client, "stop_session", lambda *_a, **_k: {"ok": True})
    monkeypatch.setattr(eeg_poller.eeg_client, "get_state", get_state)
    monkeypatch.setattr(eeg_poller.eeg_client, "map_eeg_to_cognitive",
                        lambda data, sid, uid: {"session_id": sid, "user_id": uid, "ts": data["timestamp"]})
    monkeypatch.setattr(eeg_poller.signal_mapping, "eeg_quality", lambda _d: "good")
    return reads


def _wait(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def _eeg_writes(db):
    return [w for w in db.writes if w[0] == "cognitive_signals"]


# ── a page that died without its pagehide stop ─────────────────────────────

def test_a_poller_no_page_polls_for_stops_itself(monkeypatch, stream):
    monkeypatch.setattr(eeg_poller, "PAGE_IDLE_SECONDS", 0.2)
    eeg_poller.start(_FakeSupabase(), "u1", "s1", "default")
    assert _wait(lambda: not eeg_poller.is_polling("s1")), "the poller outlived its page"


def test_a_polling_page_keeps_its_poller(monkeypatch, stream):
    """Kept alive across several idle limits, then stops once the polls stop."""
    monkeypatch.setattr(eeg_poller, "PAGE_IDLE_SECONDS", 0.2)
    eeg_poller.start(_FakeSupabase(), "u1", "s1", "default")
    until = time.monotonic() + 1.0
    while time.monotonic() < until:
        eeg_poller.page_seen("u1", "default")
        time.sleep(0.02)
    assert eeg_poller.is_polling("s1"), "a page still polling lost its poller"
    assert _wait(lambda: not eeg_poller.is_polling("s1"))


@pytest.mark.parametrize("user,device", [("u2", "default"), ("u1", "s2")])
def test_another_page_does_not_keep_it_alive(monkeypatch, stream, user, device):
    monkeypatch.setattr(eeg_poller, "PAGE_IDLE_SECONDS", 0.2)
    eeg_poller.start(_FakeSupabase(), "u1", "s1", "default")
    stop = threading.Event()

    def other_page():
        while not stop.is_set():
            eeg_poller.page_seen(user, device)
            time.sleep(0.02)

    t = threading.Thread(target=other_page)
    t.start()
    try:
        assert _wait(lambda: not eeg_poller.is_polling("s1"))
    finally:
        stop.set()
        t.join()


def test_main_wires_the_pairing_idle_limit(monkeypatch):
    """Through a reload: another file reloads `eeg_poller`, which resets the attribute."""
    import importlib
    monkeypatch.setattr(eeg_poller, "PAGE_IDLE_SECONDS", None)
    importlib.reload(main)
    assert eeg_poller.PAGE_IDLE_SECONDS == main._PAIRING_IDLE_SEC is not None


def test_the_owners_status_poll_marks_the_page_seen(monkeypatch):
    seen = []
    monkeypatch.setattr(main.eeg_poller, "can_use_device", lambda *_a: True)
    monkeypatch.setattr(main.eeg_poller, "live_poller_user", lambda _d: "u1")
    monkeypatch.setattr(main.eeg_poller, "page_seen", lambda *a: seen.append(a))
    monkeypatch.setattr(main, "_touched_recently", lambda *_a: True)

    assert main._station_access("u1", "default") == (True, None)
    assert seen == [("u1", "default")]


def test_another_users_request_does_not_mark_the_page_seen(monkeypatch):
    seen = []
    monkeypatch.setattr(main.eeg_poller, "can_use_device", lambda *_a: False)
    monkeypatch.setattr(main.eeg_poller, "page_seen", lambda *a: seen.append(a))

    assert main._station_access("u2", "default") == (False, None)
    assert seen == []


# ── an admin switch withholds recording; turned back on, it resumes ─────────

def test_a_switched_off_poller_waits_writes_nothing_and_resumes(monkeypatch, stream):
    allowed = {"ok": True}
    eeg_poller.set_consent_check(lambda _u: allowed["ok"])
    eeg_poller.set_pause_check(lambda _u: True)
    db = _FakeSupabase()
    eeg_poller.start(db, "u1", "s1", "default")
    p = eeg_poller._active["s1"]
    assert _wait(lambda: _eeg_writes(db)), "never recorded before the switch"

    allowed["ok"] = False
    assert _wait(lambda: p.withheld)
    # Withheld is set at the top of a loop, before that loop's write: nothing after this counts.
    written, read = len(_eeg_writes(db)), len(stream)
    assert _wait(lambda: len(stream) >= read + 10), "the paused poller stopped reading"
    assert len(_eeg_writes(db)) == written, "a withheld poller wrote"
    assert eeg_poller.is_polling("s1"), "the switch stopped the poller instead of pausing it"
    assert eeg_poller.status("u1")["withheld"] is True

    allowed["ok"] = True
    assert _wait(lambda: len(_eeg_writes(db)) > written), "recording did not resume"
    assert p.withheld is False


@pytest.mark.parametrize("pause", [lambda _u: False, None, "raises"])
def test_any_other_refusal_still_stops(monkeypatch, stream, pause):
    """Consent withdrawn, unwired, or a failed check: the poller stops, as before."""
    if pause == "raises":
        def pause(_u):
            raise RuntimeError("consent unreadable")
    allowed = {"ok": True}
    eeg_poller.set_consent_check(lambda _u: allowed["ok"])
    eeg_poller.set_pause_check(pause)
    eeg_poller.start(_FakeSupabase(), "u1", "s1", "default")
    allowed["ok"] = False
    assert _wait(lambda: not eeg_poller.is_polling("s1"))


def _gate(**over):
    gate = {"switched_off": ["record_eeg"], "window_state": "open",
            "retrieved": True, "eeg_enabled": True}
    return {**gate, **over}


@pytest.mark.parametrize("gate,paused", [
    (_gate(), True),
    (_gate(switched_off=[]), False),                                  # refused for another reason
    (_gate(switched_off=["record_headband_optical"]), False),
    (_gate(eeg_enabled=False), False),                                # consent withdrawn too
    (_gate(retrieved=False), False),                                  # could not check consent
    (_gate(window_state=next(iter(main._WINDOW_DENIED))), False),     # the school year closed too
])
def test_only_the_eeg_switch_alone_pauses(monkeypatch, gate, paused):
    monkeypatch.setattr(main, "_may_record", lambda _s: gate)
    assert main._poller_paused_by_switch("u1") is paused


def test_main_wires_the_pause_check():
    import importlib
    eeg_poller.set_pause_check(None)
    importlib.reload(main)
    assert eeg_poller._pause_check is main._poller_paused_by_switch


def test_a_withheld_poller_reports_the_switch_without_a_consent_read(monkeypatch):
    reads = []
    monkeypatch.setattr(eeg_poller, "status", lambda _u: {"running": True, "withheld": True})
    monkeypatch.setattr(main, "_may_record", lambda _s: reads.append(1) or {})

    out = main._poller_status("u1")

    assert out["running"] is True
    assert out["stopped_reason"] == "recording_switched_off"
    assert reads == []
