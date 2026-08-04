"""Consent rules for the three signal channels.

signal_consent has no insert/update RLS policy for anyone, so PostgREST cannot
write it whatever JWT it carries, and every write arrives through the
service-role client here. These checks are therefore the enforcement rather than
a convenience layer over it -- the same argument as test_access_control.py, one
step further: getting this wrong does not leak a student's data to the wrong
reader, it records a child's body against their refusal.

The asymmetry is the part worth pinning down. A student may withdraw at any time
and that decision stands until a parent revisits it; a student may not re-enable,
or the parent's control is nominal. Both halves are tested, in both directions.
"""
import os
import sys

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import main  # noqa: E402

STUDENT = {"id": "student-1"}
PARENT = {"id": "parent-1"}
TEACHER = {"id": "teacher-1"}
STRANGER = {"id": "stranger-1"}


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, store, table, raises=None):
        self._store, self._table, self._raises = store, table, raises
        self._filters = {}
        self._pending = None
        self._mode = None

    def select(self, *_a, **_k):
        self._mode = "select"
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def limit(self, *_a, **_k):
        return self

    def upsert(self, row, **_k):
        self._mode, self._pending = "upsert", row
        return self

    def update(self, row, **_k):
        self._mode, self._pending = "update", row
        return self

    def _matches(self, row):
        return all(row.get(c) == v for c, v in self._filters.items())

    def execute(self):
        if self._raises:
            raise self._raises
        rows = self._store.setdefault(self._table, [])
        if self._mode == "select":
            return _Result([r for r in rows if self._matches(r)])
        if self._mode == "upsert":
            key = self._pending["user_id"]
            for r in rows:
                if r["user_id"] == key:
                    r.update(self._pending)
                    return _Result([r])
            rows.append(dict(self._pending))
            return _Result([rows[-1]])
        if self._mode == "update":
            hit = [r for r in rows if self._matches(r)]
            for r in hit:
                r.update(self._pending)
            return _Result(hit)
        raise AssertionError("unreachable")


class _FakeSupabase:
    def __init__(self, store, raises_on=()):
        self.store = store
        self._raises_on = set(raises_on)

    def table(self, name):
        raises = RuntimeError(f"{name} unavailable") if name in self._raises_on else None
        return _Query(self.store, name, raises)


def _fake(monkeypatch, viewer, consent_row=None, links=True, raises_on=()):
    store = {
        "signal_consent": [dict(consent_row)] if consent_row else [],
        "parent_child_links": (
            [{"id": "l1", "parent_id": "parent-1", "child_id": "student-1"}] if links else []
        ),
    }
    fake = _FakeSupabase(store, raises_on=raises_on)
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "get_user", lambda _r: viewer)
    # Reads are gated by the shared relationship helper, which has its own
    # coverage; these tests are about the consent rules on top of it.
    monkeypatch.setattr(main, "_can_view_student", lambda v, s: v["id"] != "stranger-1")
    return fake


def _row(**over):
    base = {
        "user_id": "student-1",
        "eeg_enabled": True, "headband_optical_enabled": True, "camera_enabled": True,
        "eeg_revoked_at": None, "headband_optical_revoked_at": None, "camera_revoked_at": None,
        "updated_by": "parent-1", "updated_at": "2026-08-01T00:00:00+00:00",
        "student_ack_at": "2026-08-01T00:00:00+00:00",
    }
    base.update(over)
    return base


# ── defaults: nothing is recorded until someone says so ──────────────────────

def test_absent_row_denies_every_channel(monkeypatch):
    """A student nobody has configured records nothing.

    The table has no row for them and this must read as denial, not as an
    unset value to be filled in with a default of "on"."""
    _fake(monkeypatch, STUDENT, consent_row=None)
    out = main.get_consent("student-1", None)
    assert [c["enabled"] for c in out["channels"].values()] == [False, False, False]
    assert out["retrieved"] is True


def test_failed_read_denies_and_says_so(monkeypatch):
    """Fail closed, and be able to explain which kind of nothing this is.

    The reporting helpers elsewhere swallow errors and answer with an empty
    payload; that is right for a dashboard and wrong here. Defaulting to
    enabled on a failed read would record data against a refusal, and look
    identical to a working system while doing it."""
    _fake(monkeypatch, STUDENT, consent_row=_row(), raises_on={"signal_consent"})
    out = main.get_consent("student-1", None)
    assert [c["enabled"] for c in out["channels"].values()] == [False, False, False]
    assert out["retrieved"] is False


# ── the asymmetry ────────────────────────────────────────────────────────────

def test_student_may_turn_a_channel_off(monkeypatch):
    fake = _fake(monkeypatch, STUDENT, consent_row=_row())
    out = main.update_consent("student-1", main.ConsentUpdate(camera_enabled=False), None)
    assert out["channels"]["camera"]["enabled"] is False
    assert out["channels"]["camera"]["revoked_at"] is not None
    assert fake.store["signal_consent"][0]["updated_by"] == "student-1"


def test_student_may_not_turn_a_channel_back_on(monkeypatch):
    """The half that makes parent control real rather than nominal."""
    fake = _fake(monkeypatch, STUDENT,
                 consent_row=_row(camera_enabled=False, camera_revoked_at="2026-08-02T00:00:00+00:00"))
    with pytest.raises(main.HTTPException) as exc:
        main.update_consent("student-1", main.ConsentUpdate(camera_enabled=True), None)
    assert exc.value.status_code == 403
    assert fake.store["signal_consent"][0]["camera_enabled"] is False


def test_parent_may_turn_a_channel_back_on(monkeypatch):
    fake = _fake(monkeypatch, PARENT,
                 consent_row=_row(camera_enabled=False, camera_revoked_at="2026-08-02T00:00:00+00:00"))
    out = main.update_consent("student-1", main.ConsentUpdate(camera_enabled=True), None)
    assert out["channels"]["camera"]["enabled"] is True
    # The revoked_at has to clear with it: a CHECK constraint in the migration
    # makes enabled-with-a-revocation-date unrepresentable, so leaving it would
    # fail the write rather than store a contradiction.
    assert fake.store["signal_consent"][0]["camera_revoked_at"] is None


# ── who may write at all ─────────────────────────────────────────────────────

def test_teacher_may_read_but_not_write(monkeypatch):
    """Reading and writing are different relationships.

    A teacher has to be able to see that a channel is off, or a blank tile
    reads as a broken query. Changing it is not theirs to do."""
    _fake(monkeypatch, TEACHER, consent_row=_row())
    assert main.get_consent("student-1", None)["channels"]["camera"]["enabled"] is True
    with pytest.raises(main.HTTPException) as exc:
        main.update_consent("student-1", main.ConsentUpdate(camera_enabled=False), None)
    assert exc.value.status_code == 403


def test_unlinked_parent_may_not_write(monkeypatch):
    fake = _fake(monkeypatch, PARENT, consent_row=_row(), links=False)
    with pytest.raises(main.HTTPException) as exc:
        main.update_consent("student-1", main.ConsentUpdate(camera_enabled=False), None)
    assert exc.value.status_code == 403
    assert fake.store["signal_consent"][0]["camera_enabled"] is True


def test_stranger_may_not_read(monkeypatch):
    _fake(monkeypatch, STRANGER, consent_row=_row())
    with pytest.raises(main.HTTPException) as exc:
        main.get_consent("student-1", None)
    assert exc.value.status_code == 403


def test_write_is_refused_when_the_current_state_could_not_be_read(monkeypatch):
    """Never decide what to change from a read that failed."""
    _fake(monkeypatch, PARENT, consent_row=_row(), raises_on={"signal_consent"})
    with pytest.raises(main.HTTPException) as exc:
        main.update_consent("student-1", main.ConsentUpdate(camera_enabled=True), None)
    assert exc.value.status_code == 503


# ── what a teacher is told, and what the student is told ─────────────────────

def test_revoked_by_is_a_role_not_an_identity(monkeypatch):
    """A teacher learns a decision was made and by which role. Which guardian
    made it is none of their business, so no id reaches the payload."""
    _fake(monkeypatch, TEACHER,
          consent_row=_row(camera_enabled=False, camera_revoked_at="2026-08-02T00:00:00+00:00",
                           updated_by="parent-1"))
    channel = main.get_consent("student-1", None)["channels"]["camera"]
    assert channel["revoked_by"] == "parent"
    assert "parent-1" not in str(channel)


def test_revoked_by_reports_the_student_when_they_withdrew(monkeypatch):
    _fake(monkeypatch, TEACHER,
          consent_row=_row(camera_enabled=False, camera_revoked_at="2026-08-02T00:00:00+00:00",
                           updated_by="student-1"))
    assert main.get_consent("student-1", None)["channels"]["camera"]["revoked_by"] == "student"


def test_enabled_channel_reports_no_revoker(monkeypatch):
    """A role on an enabled channel would read as "turned on by", which is a
    different claim than the one this field makes."""
    _fake(monkeypatch, TEACHER, consent_row=_row(updated_by="parent-1"))
    assert main.get_consent("student-1", None)["channels"]["camera"]["revoked_by"] is None


def test_parent_change_raises_a_notice_for_the_student(monkeypatch):
    """A student must not discover a sensor resumed by noticing data reappear."""
    _fake(monkeypatch, STUDENT,
          consent_row=_row(updated_by="parent-1", updated_at="2026-08-03T00:00:00+00:00",
                           student_ack_at=None))
    assert main.get_consent("student-1", None)["needs_student_ack"] is True


def test_acknowledging_clears_the_notice(monkeypatch):
    fake = _fake(monkeypatch, STUDENT,
                 consent_row=_row(updated_by="parent-1", updated_at="2026-08-03T00:00:00+00:00",
                                  student_ack_at=None))
    main.ack_consent(None)
    assert fake.store["signal_consent"][0]["student_ack_at"] is not None
    assert main.get_consent("student-1", None)["needs_student_ack"] is False


def test_a_students_own_change_does_not_notify_them(monkeypatch):
    _fake(monkeypatch, STUDENT,
          consent_row=_row(updated_by="student-1", updated_at="2026-08-03T00:00:00+00:00",
                           student_ack_at=None))
    assert main.get_consent("student-1", None)["needs_student_ack"] is False


# ── no-op writes ─────────────────────────────────────────────────────────────

def test_setting_a_channel_to_its_current_value_touches_nothing(monkeypatch):
    """Otherwise a re-save would restamp updated_by and updated_at, which would
    raise a notice at the student about a change that did not happen."""
    fake = _fake(monkeypatch, PARENT, consent_row=_row(updated_at="2026-08-01T00:00:00+00:00"))
    main.update_consent("student-1", main.ConsentUpdate(camera_enabled=True), None)
    assert fake.store["signal_consent"][0]["updated_at"] == "2026-08-01T00:00:00+00:00"
