"""Consent stops future recording on the pull path too; withdrawal is not deletion."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import eeg_poller  # noqa: E402
import main  # noqa: E402


class _Session:
    data = {"user_id": "u1", "ended_at": None}

    def select(self, *_a): return self
    def eq(self, *_a): return self
    def single(self): return self
    def execute(self): return self


def _endpoint_stubs(monkeypatch):
    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "pull")
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u1"})
    monkeypatch.setattr(main, "supabase",
                        type("S", (), {"table": lambda _s, _n: _Session()})())
    monkeypatch.setattr(main, "eeg_client",
                        type("C", (), {"is_alive": staticmethod(lambda: True),
                                       "DEFAULT_DEVICE_ID": "default"}))


def _payload():
    return type("P", (), {"session_id": "s1", "device_id": None})()


def test_a_withdrawn_student_cannot_start_a_poller(monkeypatch):
    _endpoint_stubs(monkeypatch)
    monkeypatch.setattr(main, "_consent",
                        lambda _s: {"eeg_enabled": False, "retrieved": True})

    with pytest.raises(main.HTTPException) as exc:
        main.eeg_start(_payload(), None)

    assert exc.value.status_code == 403
    # 403, not push's 409: a refusal, not a misconfiguration.
    assert "switched off" in exc.value.detail


def test_an_unreadable_consent_row_does_not_start_one_either(monkeypatch):
    """`_consent` fails closed, unlike the reporting helpers."""
    _endpoint_stubs(monkeypatch)
    monkeypatch.setattr(main, "_consent", lambda _s: {"retrieved": False})

    with pytest.raises(main.HTTPException) as exc:
        main.eeg_start(_payload(), None)

    assert exc.value.status_code == 403
    assert "Could not check" in exc.value.detail


def test_the_poller_refuses_when_no_check_is_wired(monkeypatch):
    """No "assume yes" default: unwired would look identical to wired."""
    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "pull")
    monkeypatch.setattr(eeg_poller, "_consent_check", None)

    with pytest.raises(eeg_poller.ConsentError) as exc:
        eeg_poller.start(None, "u1", "s1", "default")

    assert "set_consent_check" in str(exc.value)


def test_the_poller_refuses_a_withdrawn_student(monkeypatch):
    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "pull")
    monkeypatch.setattr(eeg_poller, "_consent_check", lambda _u: False)
    # Unstubbed, the real reason hook's failed read also refuses: right result, wrong reason.
    monkeypatch.setattr(eeg_poller, "_consent_reason_check",
                        lambda _u: "EEG recording is switched off for this student.")

    with pytest.raises(eeg_poller.ConsentError):
        eeg_poller.start(None, "u1", "s1", "default")


def test_the_wiring_is_live_not_just_available(monkeypatch):
    """`main` must call `set_consent_check` at import.

    Only `main` is reloaded: reloading `eeg_poller` would rebind its registry for later tests.
    """
    monkeypatch.setattr(eeg_poller, "_consent_check", None)
    import importlib
    importlib.reload(main)

    assert eeg_poller._consent_check is not None, "main never wired the check"


def test_a_self_terminated_poller_deregisters_itself(monkeypatch):
    """With no caller to clean up, it must pop `_active` and clear the warning itself."""
    import threading
    import time as _time

    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "pull")
    monkeypatch.setattr(eeg_poller, "POLL_INTERVAL", 0.01)
    monkeypatch.setattr(eeg_poller, "CONSENT_RECHECK_SECONDS", 0.0)
    monkeypatch.setattr(eeg_poller, "_consent_check", lambda _u: True)
    monkeypatch.setattr(eeg_poller.eeg_client, "start_session", lambda *_a, **_k: {"ok": True})
    monkeypatch.setattr(eeg_poller.eeg_client, "stop_session", lambda *_a, **_k: {"ok": True})
    monkeypatch.setattr(eeg_poller.eeg_client, "get_state", lambda *_a, **_k: None)

    eeg_poller.start(None, "u1", "s1", "default")
    eeg_poller.claim_double_write_warning("s1")
    assert "s1" in eeg_poller._warned_double_write

    # Consent goes away mid-session.
    monkeypatch.setattr(eeg_poller, "_consent_check", lambda _u: False)

    deadline = _time.monotonic() + 5
    while _time.monotonic() < deadline and eeg_poller.is_polling("s1"):
        _time.sleep(0.02)

    assert not eeg_poller.is_polling("s1"), "the poller kept running after withdrawal"
    for t in threading.enumerate():
        if type(t).__name__ == "_Poller":
            t.join(timeout=2)
    assert "s1" not in eeg_poller._active, "a dead poller stayed registered"
    assert "s1" not in eeg_poller._warned_double_write, "the warning record outlived it"


def test_the_status_endpoint_says_why_it_is_not_running(monkeypatch):
    """The reason is derived from current consent, not remembered on the poller, so it can't go stale."""
    monkeypatch.setattr(eeg_poller, "status", lambda _u: {"running": False})
    monkeypatch.setattr(main, "_consent",
                        lambda _s: {"eeg_enabled": False, "retrieved": True,
                                    "eeg_revoked_at": "2026-08-10T09:00:00Z"})

    out = main._poller_status("u1")

    assert out["stopped_reason"] == "consent_withdrawn"
    assert out["revoked_at"] == "2026-08-10T09:00:00Z"


def test_an_unreadable_consent_row_is_not_reported_as_a_withdrawal(monkeypatch):
    """A failed read is not a refusal."""
    monkeypatch.setattr(eeg_poller, "status", lambda _u: {"running": False})
    monkeypatch.setattr(main, "_consent", lambda _s: {"retrieved": False})

    assert main._poller_status("u1")["stopped_reason"] == "consent_unknown"


def test_a_running_poller_is_not_annotated(monkeypatch):
    """No consent read on the hot path: this is polled every 3 seconds."""
    reads = []
    monkeypatch.setattr(eeg_poller, "status", lambda _u: {"running": True})
    monkeypatch.setattr(main, "_consent",
                        lambda _s: reads.append(1) or {"eeg_enabled": True, "retrieved": True})

    out = main._poller_status("u1")

    assert "stopped_reason" not in out
    assert reads == [], "a running poller cost a consent read every 3s"


def test_an_actual_withdrawal_deletes_nothing(monkeypatch):
    """Drives the real write endpoint, not just a read."""
    calls = []

    class _Tbl:
        def __init__(self, name): self.name = name
        def delete(self, *_a, **_k):
            calls.append(("delete", self.name))
            return self
        def update(self, fields, *_a, **_k):
            calls.append(("update", self.name, fields))
            return self
        def insert(self, fields, *_a, **_k):
            calls.append(("insert", self.name, fields))
            return self
        def select(self, *_a, **_k): return self
        def eq(self, *_a, **_k): return self
        def limit(self, *_a, **_k): return self
        def execute(self):
            return type("R", (), {"data": [{"user_id": "u1", "eeg_enabled": False}]})()

    monkeypatch.setattr(main, "supabase",
                        type("S", (), {"table": lambda _s, n: _Tbl(n)})())
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u1"})
    monkeypatch.setattr(main, "_consent_actor", lambda *_a: "student")
    monkeypatch.setattr(main, "_consent",
                        lambda _s: {"eeg_enabled": True, "camera_enabled": False,
                                    "headband_optical_enabled": False,
                                    "retrieved": True, "exists": True})

    main.update_consent("u1", main.ConsentUpdate(eeg_enabled=False), None)

    assert not any(c[0] == "delete" for c in calls),         f"withdrawal deleted stored data: {calls}"
    # The update ran, so the assertion above isn't vacuous.
    wrote = [c for c in calls if c[0] in ("update", "insert")]
    assert wrote, "the withdrawal never reached the database"
    assert wrote[0][2]["eeg_enabled"] is False


def test_signal_tables_are_never_touched_by_a_consent_write(monkeypatch):
    """Not just "no delete" -- no write to the signal tables at all."""
    touched = []

    class _Tbl:
        def __init__(self, name): touched.append(name)
        def delete(self, *_a, **_k): return self
        def update(self, *_a, **_k): return self
        def insert(self, *_a, **_k): return self
        def select(self, *_a, **_k): return self
        def eq(self, *_a, **_k): return self
        def limit(self, *_a, **_k): return self
        def execute(self):
            return type("R", (), {"data": [{"user_id": "u1"}]})()

    monkeypatch.setattr(main, "supabase",
                        type("S", (), {"table": lambda _s, n: _Tbl(n)})())
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u1"})
    monkeypatch.setattr(main, "_consent_actor", lambda *_a: "student")
    monkeypatch.setattr(main, "_consent",
                        lambda _s: {"eeg_enabled": True, "camera_enabled": False,
                                    "headband_optical_enabled": False,
                                    "retrieved": True, "exists": True})

    main.update_consent("u1", main.ConsentUpdate(eeg_enabled=False), None)

    for table in ("cognitive_signals", "heart_signals", "face_signals"):
        assert table not in touched, f"a consent write reached {table}"


def test_a_dying_poller_does_not_deregister_its_replacement(monkeypatch):
    """Reconnect while the old thread is mid-`get_state`: its teardown pop is guarded by `is self`."""
    import threading
    import time as _time

    # Gated, not slept: the first read blocks until the replacement is registered.
    entered_read = threading.Event()
    may_finish_read = threading.Event()

    def _gated_get_state(*_a, **_k):
        entered_read.set()
        may_finish_read.wait(5)
        return None

    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "pull")
    monkeypatch.setattr(eeg_poller, "POLL_INTERVAL", 0.01)
    monkeypatch.setattr(eeg_poller, "_consent_check", lambda _u: True)
    monkeypatch.setattr(eeg_poller.eeg_client, "start_session", lambda *_a, **_k: {"ok": True})
    stopped_streams = []
    monkeypatch.setattr(eeg_poller.eeg_client, "stop_session",
                        lambda device_id=None, *_a, **_k: stopped_streams.append(device_id) or {"ok": True})
    monkeypatch.setattr(eeg_poller.eeg_client, "get_state", _gated_get_state)

    eeg_poller.start(None, "u1", "s1", "devA")
    old = eeg_poller._active["s1"]
    assert entered_read.wait(5), "the poller never reached a read"

    # Disconnect and reconnect while the old thread is still inside that read.
    eeg_poller.stop("s1")
    eeg_poller.start(None, "u1", "s1", "devA")
    new = eeg_poller._active["s1"]
    assert new is not old

    may_finish_read.set()

    deadline = _time.monotonic() + 5
    while _time.monotonic() < deadline and old.is_alive():
        _time.sleep(0.02)
    assert not old.is_alive(), "the old poller never finished"

    assert eeg_poller._active.get("s1") is new, "the replacement was deregistered"
    assert eeg_poller.is_polling("s1"), "a live poller became invisible to the registry"
    assert not eeg_poller.can_use_device("u2", "devA"), \
        "another user could claim a device with a live poller on it"
    assert eeg_poller.status("u1")["running"] is True
    # The dying thread must not stop the sidecar stream the replacement reads.
    assert stopped_streams == [],         f"the sidecar stream was stopped under a live poller: {stopped_streams}"

    eeg_poller.stop("s1")
    assert not eeg_poller.is_polling("s1")
    for t in threading.enumerate():
        if type(t).__name__ == "_Poller":
            t.join(timeout=2)
    assert stopped_streams == ["devA"]
