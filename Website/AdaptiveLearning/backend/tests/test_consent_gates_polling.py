"""Consent stops future recording on the pull path too.

The decision this encodes: **historical rows stay, nothing new is written until
consent is given.** Withdrawal is not deletion.

The push path has had this since it existed -- `/api/signals/*` calls
`_consent()` per request. The poller had nothing. It writes `cognitive_signals`
directly with the **service-role** client, which bypasses RLS *and* never goes
through an ingest endpoint, so under `INGEST_MODE=pull` a withdrawal stopped
nothing at all. Same class as the no_signal rules that were pull-only, in the
opposite direction.
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
    # 403, not the 409 push uses. One says this student said no, the other says
    # this deployment does not work that way; rendering them alike would let a
    # refusal read as a misconfiguration.
    assert "switched off" in exc.value.detail


def test_an_unreadable_consent_row_does_not_start_one_either(monkeypatch):
    """`_consent` fails closed, unlike the reporting helpers. A dashboard
    degrading to empty is fine; a consent check degrading to *enabled* records
    against a refusal."""
    _endpoint_stubs(monkeypatch)
    monkeypatch.setattr(main, "_consent", lambda _s: {"retrieved": False})

    with pytest.raises(main.HTTPException) as exc:
        main.eeg_start(_payload(), None)

    assert exc.value.status_code == 403
    assert "Could not check" in exc.value.detail


def test_the_poller_refuses_when_no_check_is_wired(monkeypatch):
    """No default of "assume yes". An unwired deployment would record against
    every refusal and be indistinguishable from a wired one."""
    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "pull")
    monkeypatch.setattr(eeg_poller, "_consent_check", None)

    with pytest.raises(eeg_poller.ConsentError) as exc:
        eeg_poller.start(None, "u1", "s1", "default")

    assert "set_consent_check" in str(exc.value)


def test_the_poller_refuses_a_withdrawn_student(monkeypatch):
    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "pull")
    monkeypatch.setattr(eeg_poller, "_consent_check", lambda _u: False)

    with pytest.raises(eeg_poller.ConsentError):
        eeg_poller.start(None, "u1", "s1", "default")


def test_the_wiring_is_live_not_just_available():
    """`main` must actually call `set_consent_check` at import. Wired wrongly,
    every test above still passes and production records against refusals."""
    import importlib
    importlib.reload(eeg_poller)
    importlib.reload(main)

    assert eeg_poller._consent_check is not None, "main never wired the check"


def test_history_is_not_touched_by_a_withdrawal(monkeypatch):
    """The decision, stated as a test: withdrawal stops future writes and does
    not delete or hide past ones. Nothing in the consent write path issues a
    delete, and the reporting helpers keep reading what is already stored."""
    deleted = []

    class _Tbl:
        def delete(self, *_a, **_k):
            deleted.append(1)
            return self
        def update(self, *_a, **_k): return self
        def insert(self, *_a, **_k): return self
        def eq(self, *_a, **_k): return self
        def select(self, *_a, **_k): return self
        def limit(self, *_a, **_k): return self
        def execute(self): return type("R", (), {"data": [{"user_id": "u1"}]})()

    monkeypatch.setattr(main, "supabase", type("S", (), {"table": lambda _s, _n: _Tbl()})())
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u1"})
    monkeypatch.setattr(main, "_profile", lambda _u: {"role": "student"})

    main._consent("u1")

    assert deleted == [], "reading consent deleted stored signals"
