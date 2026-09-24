"""The EEG poller: device claims, reservations, recording arm, backoff and answer notifications."""
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
    # Records every arm so a test can assert when the sidecar was told recording started.
    eeg_client_arms = []
    monkeypatch.setattr(eeg_client, "arm_session",
                        lambda device_id=eeg_client.DEFAULT_DEVICE_ID: eeg_client_arms.append(device_id) or {"status": "armed"})
    monkeypatch.setattr(eeg_client, "_test_arms", eeg_client_arms, raising=False)
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
    assert out == {"running": True, "already": True, "recording": True}


# ── recording is armed by the first question, not by pairing ────────────────
# Pairing under pull starts the poller; samples belong to first question through Finish.

class _CountingSupabase:
    def __init__(self):
        self.rows = []

    def table(self, name):
        outer = self
        class _T:
            def upsert(self, row, **_k):
                outer.rows.append((name, row))
                class _R:
                    data = [row]
                    def execute(self_inner): return self_inner
                return _R()
        return _T()


def _streaming(monkeypatch):
    """A sidecar answering a fresh, good sample on every read."""
    n = {"i": 0}
    def get_state(device_id=eeg_client.DEFAULT_DEVICE_ID, timeout=2.0):
        n["i"] += 1
        return {"timestamp": f"2026-09-03T10:00:{n['i'] % 60:02d}+00:00",
                "features": {"focus_score": 50.0, "calm_score": 50.0, "confidence": 80.0,
                             "signal_quality": "good"},
                "bands": {}}
    monkeypatch.setattr(eeg_client, "get_state", get_state)


def test_a_poller_started_without_record_writes_nothing(monkeypatch):
    _streaming(monkeypatch)
    db = _CountingSupabase()
    out = eeg_poller.start(db, "user-a", "session-1", "station-a", record=False)
    assert out["running"] and out["recording"] is False
    time.sleep(0.15)
    p = eeg_poller._active["session-1"]
    assert db.rows == []
    # Polling but not recording: a paired-but-idle headband.
    assert p.last_ts is not None
    assert eeg_poller.status("user-a")["recording"] is False


def test_start_with_record_arms_the_running_poller_in_place(monkeypatch):
    _streaming(monkeypatch)
    db = _CountingSupabase()
    eeg_poller.start(db, "user-a", "session-1", "station-a", record=False)
    time.sleep(0.1)
    before = eeg_poller._active["session-1"]
    out = eeg_poller.start(db, "user-a", "session-1", "station-a", record=True)
    assert out == {"running": True, "already": True, "recording": True}
    # Same thread, flipped -- not stopped and replaced.
    assert eeg_poller._active["session-1"] is before
    deadline = time.monotonic() + 2.0
    while not db.rows and time.monotonic() < deadline:
        time.sleep(0.01)
    assert db.rows and db.rows[0][0] == "cognitive_signals"
    assert eeg_poller.status("user-a")["recording"] is True


def test_the_default_still_records_from_the_first_tick(monkeypatch):
    """Callers that don't pass `record` record from the first tick."""
    _streaming(monkeypatch)
    db = _CountingSupabase()
    eeg_poller.start(db, "user-a", "session-1", "station-a")
    deadline = time.monotonic() + 2.0
    while not db.rows and time.monotonic() < deadline:
        time.sleep(0.01)
    assert db.rows


def test_released_device_can_be_reclaimed_by_another_user():
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    eeg_poller.stop("session-1")  # the path /api/eeg/stop and the stale sweep use
    out = eeg_poller.start(_FakeSupabase(), "user-b", "session-2", "station-a")
    assert out["running"]


def test_can_use_device_blocks_other_user_on_live_station():
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    assert eeg_poller.can_use_device("user-a", "station-a")      # owner
    assert not eeg_poller.can_use_device("user-b", "station-a")  # stranger
    assert eeg_poller.can_use_device("user-b", "station-b")      # unclaimed


# ── pre-claim reservations ───────────────────────────────────────────────────
# reserve_device covers the scan/connect window before a poller exists.

def test_reserve_device_first_caller_wins_unclaimed_station():
    assert eeg_poller.reserve_device("user-a", "station-a")
    assert not eeg_poller.reserve_device("user-b", "station-a")
    # The loser is locked out of reading and controlling, not just re-reserving.
    assert not eeg_poller.can_use_device("user-b", "station-a")


def test_reserve_device_is_idempotent_and_refreshable_for_the_same_user():
    assert eeg_poller.reserve_device("user-a", "station-a")
    # The same user continuing to pair, not a second claimant.
    assert eeg_poller.reserve_device("user-a", "station-a")
    assert eeg_poller.can_use_device("user-a", "station-a")


def test_can_use_device_respects_an_unexpired_reservation():
    eeg_poller.reserve_device("user-a", "station-a")
    assert eeg_poller.can_use_device("user-a", "station-a")       # reserver
    assert not eeg_poller.can_use_device("user-b", "station-a")   # stranger
    assert eeg_poller.can_use_device("user-b", "station-b")       # untouched


# ── backoff while the sidecar answers nothing ────────────────────────────────
# The first miss is free; further misses double the wait up to POLL_BACKOFF_MAX_S.

def test_the_wait_grows_with_misses_and_is_capped():
    base = eeg_poller.POLL_INTERVAL
    assert eeg_poller._poll_wait(0) == base
    assert eeg_poller._poll_wait(1) == base
    assert eeg_poller._poll_wait(2) == pytest.approx(min(eeg_poller.POLL_BACKOFF_MAX_S, base * 2))
    assert eeg_poller._poll_wait(3) == pytest.approx(min(eeg_poller.POLL_BACKOFF_MAX_S, base * 4))
    assert eeg_poller._poll_wait(50) == eeg_poller.POLL_BACKOFF_MAX_S


def test_a_run_of_empty_reads_is_counted_and_one_payload_resets_it(monkeypatch):
    answers = {"data": None}
    monkeypatch.setattr(eeg_client, "get_state",
                        lambda device_id=eeg_client.DEFAULT_DEVICE_ID, timeout=2.0: answers["data"])
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    p = eeg_poller._active["session-1"]
    deadline = time.monotonic() + 2.0
    while p.consecutive_misses < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert p.consecutive_misses >= 3
    assert eeg_poller.status("user-a")["consecutive_misses"] >= 3

    # A refused payload (no_signal) still counts: backoff is about reachability.
    answers["data"] = {"timestamp": "2026-09-02T10:00:00+00:00",
                       "features": {"signal_quality": "no_signal"}}
    deadline = time.monotonic() + 2.0
    while p.consecutive_misses != 0 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert p.consecutive_misses == 0


def test_reservation_expires_and_reopens_the_station(monkeypatch):
    """An abandoned scan must not permanently lock a physical station."""
    monkeypatch.setattr(eeg_poller, "RESERVATION_TTL_SECONDS", 0.05)
    assert eeg_poller.reserve_device("user-a", "station-a")
    assert not eeg_poller.reserve_device("user-b", "station-a")
    time.sleep(0.08)
    assert eeg_poller.reserve_device("user-b", "station-a")
    assert eeg_poller.can_use_device("user-b", "station-a")
    assert not eeg_poller.can_use_device("user-a", "station-a")


def test_a_live_poller_outranks_a_reservation():
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    assert not eeg_poller.reserve_device("user-b", "station-a")


def test_start_refuses_a_station_someone_else_has_reserved():
    """A caller can skip the scan/connect UI and hit /start directly."""
    assert eeg_poller.reserve_device("user-a", "station-a")
    with pytest.raises(eeg_poller.DeviceClaimedError):
        eeg_poller.start(_FakeSupabase(), "user-b", "session-2", "station-a")


def test_start_by_the_reserving_user_succeeds_and_clears_the_reservation():
    eeg_poller.reserve_device("user-a", "station-a")
    out = eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    assert out["running"]
    # Ownership moved to the live poller.
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
    """main.py's control endpoints release only the failing device's claim."""
    eeg_poller.reserve_device("user-a", "station-a")
    eeg_poller.reserve_device("user-a", "station-b")
    eeg_poller.release_reservation("user-a", "station-a")
    assert "station-a" not in eeg_poller._reservations
    assert not eeg_poller.reserve_device("user-c", "station-b")  # untouched


def test_release_reservation_with_device_id_ignores_a_different_users_claim():
    """A device_id-scoped release must still check ownership."""
    eeg_poller.reserve_device("user-a", "station-a")
    eeg_poller.release_reservation("user-b", "station-a")
    assert not eeg_poller.reserve_device("user-c", "station-a")  # still user-a's


def test_stop_releases_a_reservation_even_with_no_poller_to_pop():
    """With nothing in _active, stop() needs an explicit user_id to find the reservation."""
    eeg_poller.reserve_device("user-a", "station-a")
    eeg_poller.stop("session-that-never-started", "user-a")
    assert eeg_poller.reserve_device("user-b", "station-a")


def test_stop_falls_back_to_the_popped_pollers_own_user_id():
    """With user_id omitted, stop() falls back to the popped poller's p.user_id."""
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    eeg_poller.reserve_device("user-a", "station-b")  # a second, unrelated hold
    eeg_poller.stop("session-1")
    # station-b is only reachable through p.user_id.
    assert eeg_poller.reserve_device("user-c", "station-b")


def test_stop_all_joins_every_poller_and_empties_the_registry():
    """stop_all must join live threads, including one popped from _active but still running.

    The orphan is popped by hand, unsignalled, so it can't exit on its own and the counts are exact.
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
    """A `_stop` attribute on `_Poller` would shadow `Thread._stop()`, used by `is_alive()`/`join()`."""
    p = eeg_poller._Poller(_FakeSupabase(), "user-a", "session-1", "station-a")
    p.start()
    p.stop()
    # Calls is_alive()/join() every iteration; the deadline only fires if it never exits.
    deadline = time.monotonic() + 15.0
    while p.is_alive() and time.monotonic() < deadline:
        p.join(timeout=0.05)
    assert p.is_alive() is False, "poller thread did not exit after stop()"


# ── arming the sidecar's baseline ───────────────────────────────────────────

def test_arming_recording_tells_the_sidecar_to_take_its_baseline_from_now(monkeypatch):
    """The baseline starts at the first question (`record` true), not at Connect."""
    db = _FakeSupabase()
    eeg_poller.start(db, "user-a", "session-1", "station-a", record=False)
    assert eeg_client._test_arms == [], "Connect alone must not arm the baseline"
    eeg_poller.start(db, "user-a", "session-1", "station-a", record=True)
    assert eeg_client._test_arms == ["station-a"]
    # A repeat with record already true is not a new question.
    eeg_poller.start(db, "user-a", "session-1", "station-a", record=True)
    assert eeg_client._test_arms == ["station-a"]


def test_a_poller_started_recording_arms_on_start(monkeypatch):
    db = _FakeSupabase()
    eeg_poller.start(db, "user-a", "session-1", "station-a")
    assert eeg_client._test_arms == ["station-a"]


def test_a_sidecar_that_cannot_be_armed_does_not_stop_recording(monkeypatch, capsys):
    """Best effort, but logged: an older sidecar lacks the route."""
    def refuse(device_id=eeg_client.DEFAULT_DEVICE_ID):
        raise RuntimeError("404 Not Found")
    monkeypatch.setattr(eeg_client, "arm_session", refuse)
    db = _FakeSupabase()
    out = eeg_poller.start(db, "user-a", "session-1", "station-a", record=True)
    assert out["running"] is True and out["recording"] is True
    assert "could not arm the sidecar baseline" in capsys.readouterr().out


def test_the_sidecar_arm_runs_outside_the_poller_lock(monkeypatch):
    """The arm is a blocking POST; a socket wait under _lock stalls every other taker."""
    held_during_arm = []

    def arm(device_id=eeg_client.DEFAULT_DEVICE_ID):
        got = eeg_poller._lock.acquire(blocking=False)
        held_during_arm.append(not got)
        if got:
            eeg_poller._lock.release()
        return {"status": "armed"}
    monkeypatch.setattr(eeg_client, "arm_session", arm)
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a", record=False)
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a", record=True)
    eeg_poller.start(_FakeSupabase(), "user-b", "session-2", "station-b")
    assert held_during_arm == [False, False]


# --- notify_answer -----------------------------------------------------------


def _record_reports(monkeypatch):
    seen = []

    def report(device_id=eeg_client.DEFAULT_DEVICE_ID, *, correct, difficulty=None):
        seen.append((device_id, correct, difficulty))
        return {"status": "ok", "data": {"ok": True, "applied": True}}

    monkeypatch.setattr(eeg_client, "report_answer", report, raising=False)
    return seen


def test_an_answer_reaches_the_sidecar_behind_the_sessions_poller(monkeypatch):
    seen = _record_reports(monkeypatch)
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    futures = [eeg_poller.notify_answer("session-1", True),
               eeg_poller.notify_answer("session-1", False, "hard")]
    assert all(f is not None for f in futures)
    assert [f.result(timeout=5) for f in futures] == [True, True]
    assert seen == [("station-a", True, None), ("station-a", False, "hard")]


def test_the_answer_is_delivered_off_the_calling_thread(monkeypatch):
    """`record_answer` holds an anyio pool slot, so a stalled sidecar must not block it."""
    import threading
    threads = []

    def report(device_id=eeg_client.DEFAULT_DEVICE_ID, *, correct, difficulty=None):
        threads.append(threading.current_thread().name)
        return {}
    monkeypatch.setattr(eeg_client, "report_answer", report, raising=False)
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    eeg_poller.notify_answer("session-1", True).result(timeout=5)
    assert threads and threads[0] != threading.current_thread().name
    assert threads[0].startswith("eeg-notify")


def test_a_stalled_sidecar_costs_dropped_notifications_not_threads(monkeypatch, capsys):
    import threading
    release = threading.Event()
    started = threading.Event()

    def stall(device_id=eeg_client.DEFAULT_DEVICE_ID, *, correct, difficulty=None):
        started.set()
        release.wait(timeout=10)
        return {}
    monkeypatch.setattr(eeg_client, "report_answer", stall, raising=False)
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    first = eeg_poller.notify_answer("session-1", True)
    assert started.wait(timeout=5)
    # Past the cap: refused at once, on the calling thread, and logged.
    accepted = [eeg_poller.notify_answer("session-1", True)
                for _ in range(eeg_poller.NOTIFY_MAX_PENDING + 3)]
    assert sum(f is not None for f in accepted) == eeg_poller.NOTIFY_MAX_PENDING - 1
    assert "answer notification dropped" in capsys.readouterr().out
    release.set()
    first.result(timeout=5)
    for f in accepted:
        if f is not None:
            f.result(timeout=5)
    # Drained, the cap is available again.
    assert eeg_poller.notify_answer("session-1", True) is not None


def test_a_session_with_no_poller_reports_nothing(monkeypatch):
    seen = _record_reports(monkeypatch)
    assert eeg_poller.notify_answer("session-9", True) is None
    assert seen == []


def test_under_push_the_backend_never_reaches_for_the_sidecar(monkeypatch):
    seen = _record_reports(monkeypatch)
    # A poller existing is not enough; the mode decides if the sidecar is reachable.
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "push")
    assert eeg_poller.notify_answer("session-1", True) is None
    assert seen == []


def test_a_sidecar_that_refuses_the_answer_costs_a_log_line_not_the_answer(monkeypatch, capsys):
    def refuse(device_id=eeg_client.DEFAULT_DEVICE_ID, *, correct, difficulty=None):
        raise RuntimeError("404 Not Found")
    monkeypatch.setattr(eeg_client, "report_answer", refuse, raising=False)
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    future = eeg_poller.notify_answer("session-1", True)
    assert future is not None and future.result(timeout=5) is False
    assert "could not report the answer" in capsys.readouterr().out


def test_stop_all_joins_the_notify_worker(monkeypatch):
    _record_reports(monkeypatch)
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    eeg_poller.notify_answer("session-1", True).result(timeout=5)
    assert eeg_poller._notify_pool is not None
    eeg_poller.stop_all()
    assert eeg_poller._notify_pool is None and eeg_poller._notify_pending == 0
    # Joined, not forgotten: the print-at-shutdown hazard.
    import threading
    assert not any(t.name.startswith("eeg-notify") and t.is_alive() for t in threading.enumerate())


def test_stop_all_leaves_the_notify_counter_at_zero_with_a_delivery_in_flight(monkeypatch):
    """Zeroing before the join lets the in-flight `finally` take the counter to -1."""
    import threading
    release = threading.Event()

    def stall(device_id=eeg_client.DEFAULT_DEVICE_ID, *, correct, difficulty=None):
        release.wait(timeout=10)
        return {}
    monkeypatch.setattr(eeg_client, "report_answer", stall, raising=False)
    eeg_poller.start(_FakeSupabase(), "user-a", "session-1", "station-a")
    assert eeg_poller.notify_answer("session-1", True) is not None
    assert eeg_poller._notify_pending == 1
    threading.Timer(0.2, release.set).start()
    eeg_poller.stop_all()
    assert eeg_poller._notify_pending == 0
