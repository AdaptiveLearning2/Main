"""Consent stops future recording on the pull path too.

The rule: historical rows stay, and nothing new is written until consent is
given again. Withdrawal is not deletion.

The push path has always had this -- `/api/signals/*` calls `_consent()` per
request. The poller had nothing: it writes `cognitive_signals` directly with
the service-role client, bypassing both RLS and the ingest endpoint, so under
`INGEST_MODE=pull` a withdrawal used to stop nothing at all.
"""
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
    # 403, not the 409 push uses: one says this student said no, the other
    # says this deployment doesn't work that way. Merging them would make a
    # refusal look like a misconfiguration.
    assert "switched off" in exc.value.detail


def test_an_unreadable_consent_row_does_not_start_one_either(monkeypatch):
    """`_consent` fails closed, unlike the reporting helpers. A dashboard
    degrading to empty is fine, but a consent check degrading to "enabled"
    would record against a refusal."""
    _endpoint_stubs(monkeypatch)
    monkeypatch.setattr(main, "_consent", lambda _s: {"retrieved": False})

    with pytest.raises(main.HTTPException) as exc:
        main.eeg_start(_payload(), None)

    assert exc.value.status_code == 403
    assert "Could not check" in exc.value.detail


def test_the_poller_refuses_when_no_check_is_wired(monkeypatch):
    """No default of "assume yes" -- an unwired deployment would otherwise
    record against every refusal and look identical to a wired one."""
    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "pull")
    monkeypatch.setattr(eeg_poller, "_consent_check", None)

    with pytest.raises(eeg_poller.ConsentError) as exc:
        eeg_poller.start(None, "u1", "s1", "default")

    assert "set_consent_check" in str(exc.value)


def test_the_poller_refuses_a_withdrawn_student(monkeypatch):
    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "pull")
    monkeypatch.setattr(eeg_poller, "_consent_check", lambda _u: False)
    # The reason hook needs stubbing too. Left unstubbed, `start()` refuses and
    # then asks the real `main` callback for the message, which reads
    # `_may_record` against a real Supabase client -- that read fails fast
    # here, and a failed read is also a refusal, so the test would still pass
    # but for the wrong reason.
    monkeypatch.setattr(eeg_poller, "_consent_reason_check",
                        lambda _u: "EEG recording is switched off for this student.")

    with pytest.raises(eeg_poller.ConsentError):
        eeg_poller.start(None, "u1", "s1", "default")


def test_the_wiring_is_live_not_just_available(monkeypatch):
    """`main` must actually call `set_consent_check` at import time. If it
    were wired wrongly, every test above would still pass and production
    would record against refusals.

    Only `main` gets reloaded here. Reloading `eeg_poller` too rebinds
    `_active`, `_lock` and `_Poller` for the rest of the session, which is a
    registry-identity hazard for every later test -- clearing the fixture's
    permissive value is enough, and monkeypatch restores it after.
    """
    monkeypatch.setattr(eeg_poller, "_consent_check", None)
    import importlib
    importlib.reload(main)

    assert eeg_poller._consent_check is not None, "main never wired the check"


def test_a_self_terminated_poller_deregisters_itself(monkeypatch):
    """A poller can stop itself (on a withdrawal) with no caller around to
    clean up after it, unlike `stop`, `stop_for_user`, `start`'s replacement
    path, or `stop_all` -- all of which pop `_active` and clear the warning.
    A self-terminated poller must do the same or both outlive it."""
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
    """"Stopped because the student withdrew" and "stopped because the headband
    dropped" are different sentences, but the frontend used to render both as
    a poller that's simply not running.

    The reason is derived from current consent, not remembered on the poller
    -- a remembered reason would need the dead poller left in `_active` to
    stay readable, and could go stale the moment a parent re-enabled it."""
    monkeypatch.setattr(eeg_poller, "status", lambda _u: {"running": False})
    monkeypatch.setattr(main, "_consent",
                        lambda _s: {"eeg_enabled": False, "retrieved": True,
                                    "eeg_revoked_at": "2026-08-10T09:00:00Z"})

    out = main._poller_status("u1")

    assert out["stopped_reason"] == "consent_withdrawn"
    assert out["revoked_at"] == "2026-08-10T09:00:00Z"


def test_an_unreadable_consent_row_is_not_reported_as_a_withdrawal(monkeypatch):
    """The three-state rule: a failed read is not a refusal. Telling a parent
    their child withdrew, when the truth is we couldn't find out, is worse
    than saying nothing."""
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
    """Drives the real write endpoint, not just a read. An earlier version of
    this test called `_consent()` -- a read -- while its docstring claimed
    "nothing in the consent write path issues a delete", so a change adding a
    delete to the update endpoint would have passed it untouched.
    """
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
    # Confirms the update actually ran, so the assertion above isn't passing
    # just because nothing happened.
    wrote = [c for c in calls if c[0] in ("update", "insert")]
    assert wrote, "the withdrawal never reached the database"
    assert wrote[0][2]["eeg_enabled"] is False


def test_signal_tables_are_never_touched_by_a_consent_write(monkeypatch):
    """Not just "no delete" -- no write to the signal tables at all.
    Withdrawal changes what happens next, not what was already recorded."""
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
    """Disconnect then reconnect on the same session id.

    The old thread is mid-`get_state` (a real HTTP call, so this is the
    normal case, not a tight race) when `start()` registers a replacement
    under the same key. An unconditional pop in the old thread's teardown
    would then deregister the live poller: it would keep writing
    `cognitive_signals` while `stop()` could no longer reach it,
    `can_use_device` would hand its device to another user, and the
    teardown's liveness scan would stop the sidecar stream out from under it.

    The pop is guarded by `is self`, which leaves the consent path unchanged
    -- there, `self` is the registered poller.
    """
    import threading
    import time as _time

    # Gated, not slept. A fixed sleep to hit "the old thread is inside a read
    # while the replacement registers" only works if the scheduler cooperates
    # -- on CI the old thread sometimes reached its teardown between reads
    # instead, making the test fail on behavior that was actually correct.
    #
    # The gate makes the ordering guaranteed instead of probabilistic: the
    # first read blocks until the test has registered the replacement, so the
    # old thread is guaranteed to still be inside it.
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

    # Disconnect, then reconnect the same session while the old thread is
    # still inside that read.
    eeg_poller.stop("s1")
    eeg_poller.start(None, "u1", "s1", "devA")
    new = eeg_poller._active["s1"]
    assert new is not old

    # Only now let the read return, so the old thread's teardown runs after
    # the replacement is already registered.
    may_finish_read.set()

    # Let the old thread reach its teardown.
    deadline = _time.monotonic() + 5
    while _time.monotonic() < deadline and old.is_alive():
        _time.sleep(0.02)
    assert not old.is_alive(), "the old poller never finished"

    assert eeg_poller._active.get("s1") is new, "the replacement was deregistered"
    assert eeg_poller.is_polling("s1"), "a live poller became invisible to the registry"
    # These are the consequences the registry exists to prevent.
    assert not eeg_poller.can_use_device("u2", "devA"), \
        "another user could claim a device with a live poller on it"
    assert eeg_poller.status("u1")["running"] is True
    # A refactor of the liveness scan could break this without tripping any
    # of the checks above: the dying thread must not tear down the sidecar
    # stream the replacement is reading. It holds because the scan runs after
    # the guarded pop and so sees the live replacement -- assert it directly
    # rather than relying on that ordering staying put.
    assert stopped_streams == [],         f"the sidecar stream was stopped under a live poller: {stopped_streams}"

    # Still stoppable, which is what ending the session relies on.
    eeg_poller.stop("s1")
    assert not eeg_poller.is_polling("s1")
    for t in threading.enumerate():
        if type(t).__name__ == "_Poller":
            t.join(timeout=2)
    # Now it is right to stop it: nothing is reading it any more.
    assert stopped_streams == ["devA"]
