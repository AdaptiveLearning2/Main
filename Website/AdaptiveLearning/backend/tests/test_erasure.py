"""Erasure on request.

Distinct from withdrawal: revoking consent stops future recording and keeps
what's already stored. Erasure is the separate case of a linked parent asking
for the history itself to go.

This file tests the endpoint: who may call it, what it refuses, and that it
never quietly becomes a second name for withdrawal. The actual delete is a
Postgres function, tested against a real stack in
`scripts/assert_signal_rls.sql`, since a fake client proves nothing about
whether the rows actually went.
"""

import json
import os
from zoneinfo import ZoneInfo

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import chart_archive  # noqa: E402
import main  # noqa: E402

PARENT = {"id": "parent-1"}
STUDENT = "student-1"
OTHER = {"id": "someone-else"}


class _Rpc:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return type("R", (), {"data": self._data})()


class _Fake:
    """Enough of the client for the endpoint: one RPC, one erasure read."""

    def __init__(self, rpc_result=None, erasures=(), fail_rpc=False):
        self.rpc_calls = []
        self._rpc_result = rpc_result if rpc_result is not None else {
            "channel": "camera", "face_signals": 12, "heart_signals": 3,
            "cognitive_signals": 0, "signal_daily_rollup": 2,
            "object_paths": [],
        }
        self._erasures = list(erasures)
        self._fail_rpc = fail_rpc
        self.removed = []

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        if self._fail_rpc:
            raise RuntimeError("boom")
        return _Rpc(dict(self._rpc_result))

    def table(self, name):
        rows = self._erasures if name == "signal_erasure" else []

        class _Q:
            def select(self, *_a, **_k):
                return self

            def eq(self, *_a, **_k):
                return self

            def execute(self):
                return type("R", (), {"data": rows})()

        return _Q()

    @property
    def storage(self):
        client = self

        class _S:
            def from_(self_inner, _bucket):
                class _B:
                    def remove(self_b, paths):
                        client.removed.extend(paths)
                return _B()

        return _S()


@pytest.fixture
def parent(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: PARENT)
    monkeypatch.setattr(main, "_is_linked_parent",
                        lambda viewer, student: viewer == "parent-1")
    # A real ZoneInfo, since that's what the real function returns. Stubbing
    # a plain string instead would model a return type `_school_timezone`
    # never actually produces, and would hide a bug where params are
    # serialised with json.dumps, which raises on a ZoneInfo, inside the try
    # that turns it into a 500 -- testing the stub rather than the code.
    monkeypatch.setattr(main, "_school_timezone",
                        lambda: ZoneInfo("America/Los_Angeles"))


def _req(channel="camera", confirm=True):
    return main.ErasureRequest(channel=channel, confirm=confirm)


# ── who may ask ─────────────────────────────────────────────────────────────

def test_only_a_linked_parent_can_erase(monkeypatch, parent):
    """Not `_consent_actor`, which admits the student too. A student may
    withdraw because a parent can undo it -- nothing undoes this."""
    monkeypatch.setattr(main, "get_user", lambda _r: OTHER)

    with pytest.raises(main.HTTPException) as exc:
        main.erase_consent_channel(STUDENT, _req(), None)
    assert exc.value.status_code == 403


def test_the_student_themselves_cannot_erase(monkeypatch, parent):
    """Still refused, even for the student themselves. Withdrawal and erasure
    are different decisions with different stakes, so they get different
    gates."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": STUDENT})

    with pytest.raises(main.HTTPException) as exc:
        main.erase_consent_channel(STUDENT, _req(), None)
    assert exc.value.status_code == 403


# ── what it refuses ─────────────────────────────────────────────────────────

def test_an_unconfirmed_request_erases_nothing(monkeypatch, parent):
    """There's no undo anywhere in the system, so the request carries its own
    confirmation instead of relying on a dialog nobody can audit."""
    client = _Fake()
    monkeypatch.setattr(main, "supabase", client)

    with pytest.raises(main.HTTPException) as exc:
        main.erase_consent_channel(STUDENT, _req(confirm=False), None)
    assert exc.value.status_code == 422
    assert client.rpc_calls == [], "refused and still called the delete"


def test_an_unknown_channel_is_refused_before_the_delete(monkeypatch, parent):
    client = _Fake()
    monkeypatch.setattr(main, "supabase", client)

    with pytest.raises(main.HTTPException) as exc:
        main.erase_consent_channel(STUDENT, _req(channel="microphone"), None)
    assert exc.value.status_code == 422
    assert client.rpc_calls == []


def test_the_refusals_come_before_the_delete_not_after(monkeypatch, parent):
    """Ordering is the whole point. A check that runs after the rows are gone
    is a log line, not a gate."""
    import inspect

    source = inspect.getsource(main.erase_consent_channel)
    guard = min(source.index("_is_linked_parent"), source.index("payload.confirm"),
                source.index("CONSENT_CHANNELS"))
    assert guard < source.index("erase_signals")


# ── what it does ────────────────────────────────────────────────────────────

def test_the_channel_and_the_parent_reach_the_delete(monkeypatch, parent):
    client = _Fake(erasures=[{"channel": "camera", "erased_at": "2026-08-11T00:00:00Z"}])
    monkeypatch.setattr(main, "supabase", client)

    out = main.erase_consent_channel(STUDENT, _req(), None)

    name, params = client.rpc_calls[0]
    assert name == "erase_signals"
    assert params["p_user_id"] == STUDENT
    assert params["p_channel"] == "camera"
    assert params["p_erased_by"] == "parent-1"
    assert out["erased_at"] == "2026-08-11T00:00:00Z"


def test_the_rebuild_uses_the_school_timezone_not_utc(monkeypatch, parent):
    """The rollup rows being rebuilt here are bucketed by school day.
    Rebuilding against UTC would leave the survivors bucketed one way and the
    rest of the year bucketed the other."""
    client = _Fake()
    monkeypatch.setattr(main, "supabase", client)

    main.erase_consent_channel(STUDENT, _req(), None)

    assert client.rpc_calls[0][1]["p_timezone"] == "America/Los_Angeles"


def test_every_rpc_parameter_survives_json(monkeypatch, parent):
    """postgrest-py serialises params with plain `json.dumps`, inside a try
    that turns any failure into a 500 -- so a parameter it can't encode
    doesn't surface as a type error, it turns every erasure into an identical
    server error naming no cause.

    Checking one parameter's type would only cover the one that's already
    been wrong before. This checks all of them at once.
    """
    client = _Fake()
    monkeypatch.setattr(main, "supabase", client)

    main.erase_consent_channel(STUDENT, _req(), None)

    json.dumps(client.rpc_calls[0][1])


def test_the_archived_charts_are_removed_too(monkeypatch, parent):
    """Charts are a third copy of the data, and the only one outside the
    database. Leaving them behind means the rows are gone but pictures of
    them aren't."""
    paths = [f"{STUDENT}/s1/emotion_pie.svg", f"{STUDENT}/s1/heart_rate.svg"]
    client = _Fake(rpc_result={"channel": "camera", "object_paths": paths})
    monkeypatch.setattr(main, "supabase", client)

    out = main.erase_consent_channel(STUDENT, _req(), None)

    assert client.removed == paths
    assert out["charts_removed"] == 2 and out["charts_failed"] == 0
    assert "object_paths" not in out, "the work list is not part of the answer"


def test_a_storage_failure_is_counted_rather_than_hidden(monkeypatch, parent):
    """By the time storage removal runs, the database half has already
    committed and `chart_paths` no longer points at the objects, so a
    failure just orphans unservable files -- worth reporting a count for, not
    worth rolling the erasure back over."""
    client = _Fake(rpc_result={"channel": "camera", "object_paths": ["a.svg"]})
    monkeypatch.setattr(main, "supabase", client)
    monkeypatch.setattr(chart_archive, "remove_objects",
                        lambda _c, paths: (0, list(paths)))

    out = main.erase_consent_channel(STUDENT, _req(), None)

    assert out["charts_removed"] == 0
    assert out["charts_failed"] == 1


def test_a_failed_delete_is_a_500_and_not_a_partial_success(monkeypatch, parent):
    client = _Fake(fail_rpc=True)
    monkeypatch.setattr(main, "supabase", client)

    with pytest.raises(main.HTTPException) as exc:
        main.erase_consent_channel(STUDENT, _req(), None)
    assert exc.value.status_code == 500


# ── it is not withdrawal ────────────────────────────────────────────────────

def test_changing_consent_never_erases(monkeypatch):
    """Withdrawal keeps history. If a later change wires a revocation to the
    delete, it has to break this test to do it."""
    import inspect

    source = inspect.getsource(main.update_consent)
    assert "erase_signals" not in source
    assert "erase" not in source.replace("erased", "")


def test_erasure_is_reported_beside_consent_not_instead_of_it(monkeypatch):
    """A parent who erased and then re-consented has a channel that's on and
    a past that's gone. A payload carrying only one of those is wrong about
    the other."""
    row = {**main._CONSENT_DENIED, "camera_enabled": True, "retrieved": True}

    shaped = main._shape_consent(row, STUDENT,
                                 {"camera": "2026-08-11T00:00:00Z"})

    assert shaped["channels"]["camera"]["enabled"] is True
    assert shaped["channels"]["camera"]["erased_at"] == "2026-08-11T00:00:00Z"
    assert shaped["channels"]["eeg"]["erased_at"] is None


def test_an_unreadable_erasure_table_does_not_blank_the_consent_screen(monkeypatch):
    """Fails open, unlike `_consent()`, because this only decides whether a
    tile says "erased" or "no sensor" -- it never decides whether anything
    may be recorded."""
    class _Boom:
        def table(self, _n):
            raise RuntimeError("down")

    monkeypatch.setattr(main, "supabase", _Boom())

    assert main._erasures(STUDENT) == {}
