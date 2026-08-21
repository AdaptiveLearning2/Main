"""Consent rules for the three signal channels.

`signal_consent` has no insert/update RLS policy for anyone, so PostgREST can't
write it. Every write goes through the service-role client here, which makes
these checks the actual enforcement, not just a convenience layer -- getting
this wrong doesn't leak data, it records a child's body against their refusal.

The key asymmetry: a student can withdraw consent at any time and it stands
until a parent revisits it. A student cannot re-enable a channel themselves,
or the parent's control would be meaningless. Both directions are tested.
"""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

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
    # The shared relationship helper has its own tests; this file only tests
    # the consent rules built on top of it.
    monkeypatch.setattr(main, "_can_view_student", lambda v, s: v["id"] != "stranger-1")
    return fake


def _row(**over):
    base = {
        "user_id": "student-1",
        "eeg_enabled": True, "headband_optical_enabled": True, "camera_enabled": True,
        "eeg_revoked_at": None, "headband_optical_revoked_at": None, "camera_revoked_at": None,
        "eeg_revoked_by": None, "headband_optical_revoked_by": None, "camera_revoked_by": None,
        "updated_by": "parent-1", "updated_at": "2026-08-01T00:00:00+00:00",
        "parent_enabled_at": None, "student_ack_at": None,
    }
    base.update(over)
    return base


# ── defaults: nothing is recorded until someone says so ──────────────────────

def test_absent_row_denies_every_channel(monkeypatch):
    """A student with no row records nothing. A missing row must read as a
    denial, not as unset and defaulting to "on"."""
    _fake(monkeypatch, STUDENT, consent_row=None)
    out = main.get_consent("student-1", None)
    assert [c["enabled"] for c in out["channels"].values()] == [False, False, False]
    assert out["retrieved"] is True


def test_failed_read_denies_and_says_so(monkeypatch):
    """Fails closed, and says why. Reporting dashboards elsewhere swallow
    errors and return an empty payload, which is fine there but wrong here --
    defaulting to enabled on a failed read would record data against a
    refusal while looking like a working system."""
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
    """This is what makes parent control real rather than nominal."""
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
    # revoked_at must clear too: a CHECK constraint forbids a channel being
    # both enabled and revoked, so leaving it set would fail the write.
    assert fake.store["signal_consent"][0]["camera_revoked_at"] is None


# ── who may write at all ─────────────────────────────────────────────────────

def test_teacher_may_read_but_not_write(monkeypatch):
    """Reading and writing are different relationships. A teacher needs to see
    that a channel is off, or a blank tile looks like a broken query -- but
    changing it is not theirs to do."""
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
    """A teacher learns which role made a decision, not which guardian --
    no id should reach the payload."""
    _fake(monkeypatch, TEACHER,
          consent_row=_row(camera_enabled=False, camera_revoked_at="2026-08-02T00:00:00+00:00",
                           camera_revoked_by="parent-1"))
    channel = main.get_consent("student-1", None)["channels"]["camera"]
    assert channel["revoked_by"] == "parent"
    assert "parent-1" not in str(channel)


def test_revoked_by_reports_the_student_when_they_withdrew(monkeypatch):
    _fake(monkeypatch, TEACHER,
          consent_row=_row(camera_enabled=False, camera_revoked_at="2026-08-02T00:00:00+00:00",
                           camera_revoked_by="student-1"))
    assert main.get_consent("student-1", None)["channels"]["camera"]["revoked_by"] == "student"


def test_revoked_by_is_per_channel_not_per_row(monkeypatch):
    """The row has one updated_by, but channels are revoked independently.
    Student turns the camera off, parent later turns eeg on -- deriving the
    role from updated_by would wrongly report the parent as having withdrawn
    the camera."""
    _fake(monkeypatch, TEACHER, consent_row=_row(
        camera_enabled=False,
        camera_revoked_at="2026-08-02T00:00:00+00:00",
        camera_revoked_by="student-1",
        updated_by="parent-1",              # a later, unrelated eeg write
        updated_at="2026-08-03T00:00:00+00:00",
    ))
    assert main.get_consent("student-1", None)["channels"]["camera"]["revoked_by"] == "student"


def test_enabled_channel_reports_no_revoker(monkeypatch):
    """A role on an enabled channel would read as "turned on by" -- a
    different claim than what this field is supposed to mean."""
    _fake(monkeypatch, TEACHER, consent_row=_row(updated_by="parent-1"))
    assert main.get_consent("student-1", None)["channels"]["camera"]["revoked_by"] is None


def test_a_disable_records_who_did_it(monkeypatch):
    fake = _fake(monkeypatch, STUDENT, consent_row=_row())
    main.update_consent("student-1", main.ConsentUpdate(camera_enabled=False), None)
    assert fake.store["signal_consent"][0]["camera_revoked_by"] == "student-1"


def test_re_enabling_clears_the_revoker(monkeypatch):
    """A CHECK constraint forbids an enabled channel from having a revoker,
    so leaving it set would fail the write."""
    fake = _fake(monkeypatch, PARENT, consent_row=_row(
        camera_enabled=False, camera_revoked_at="2026-08-02T00:00:00+00:00",
        camera_revoked_by="student-1"))
    main.update_consent("student-1", main.ConsentUpdate(camera_enabled=True), None)
    row = fake.store["signal_consent"][0]
    assert row["camera_revoked_by"] is None and row["camera_revoked_at"] is None


def test_parent_re_enabling_raises_a_notice_for_the_student(monkeypatch):
    """A student must not discover a sensor resumed by noticing data reappear."""
    _fake(monkeypatch, PARENT, consent_row=_row(
        camera_enabled=False, camera_revoked_at="2026-08-02T00:00:00+00:00",
        camera_revoked_by="student-1"))
    out = main.update_consent("student-1", main.ConsentUpdate(camera_enabled=True), None)
    assert out["needs_student_ack"] is True


def test_parent_disabling_raises_no_notice(monkeypatch):
    """Only a re-enable needs a notice, not any parent write -- the student
    loses nothing here, and can see the state in settings."""
    _fake(monkeypatch, PARENT, consent_row=_row())
    out = main.update_consent("student-1", main.ConsentUpdate(camera_enabled=False), None)
    assert out["needs_student_ack"] is False


def test_acknowledging_clears_the_notice(monkeypatch):
    fake = _fake(monkeypatch, STUDENT,
                 consent_row=_row(parent_enabled_at="2026-08-03T00:00:00+00:00",
                                  student_ack_at=None))
    assert main.get_consent("student-1", None)["needs_student_ack"] is True
    main.ack_consent(None)
    assert fake.store["signal_consent"][0]["student_ack_at"] is not None
    assert main.get_consent("student-1", None)["needs_student_ack"] is False


def test_ack_with_no_consent_row_is_not_a_success(monkeypatch):
    """Reporting success for a write that matched nothing would make the
    client believe it dismissed a notice that's still there."""
    _fake(monkeypatch, STUDENT, consent_row=None)
    with pytest.raises(main.HTTPException) as exc:
        main.ack_consent(None)
    assert exc.value.status_code == 404


def test_a_students_own_change_does_not_notify_them(monkeypatch):
    _fake(monkeypatch, STUDENT,
          consent_row=_row(camera_enabled=False, camera_revoked_at="2026-08-02T00:00:00+00:00",
                           camera_revoked_by="student-1"))
    assert main.get_consent("student-1", None)["needs_student_ack"] is False


def test_ack_comparison_tolerates_mixed_timestamp_spellings(monkeypatch):
    """`Z` and `+00:00` mean the same instant but sort differently as strings.
    This comparison decides whether a student sees a notice, so it parses the
    timestamps rather than assuming both sides use the same format."""
    _fake(monkeypatch, STUDENT,
          consent_row=_row(parent_enabled_at="2026-08-03T00:00:00Z",
                           student_ack_at="2026-08-04T00:00:00+00:00"))
    assert main.get_consent("student-1", None)["needs_student_ack"] is False


# ── concurrency ──────────────────────────────────────────────────────────────

def test_write_is_refused_when_the_state_moved_underneath_it(monkeypatch):
    """Read-then-write is not atomic. The race here is a student's withdrawal
    against a parent's re-enable on the same channel -- losing it silently
    would mean recording against a refusal."""
    fake = _fake(monkeypatch, PARENT, consent_row=_row(
        camera_enabled=False, camera_revoked_at="2026-08-02T00:00:00+00:00",
        camera_revoked_by="student-1"))

    real_table = fake.table
    state = {"swapped": False}

    def table(name):
        q = real_table(name)
        if name == "signal_consent" and not state["swapped"]:
            # Simulates someone else re-enabling it between the read and the write.
            original_update = q.update

            def update(row, **kw):
                state["swapped"] = True
                fake.store["signal_consent"][0]["camera_enabled"] = True
                return original_update(row, **kw)

            q.update = update
        return q

    monkeypatch.setattr(fake, "table", table)

    with pytest.raises(main.HTTPException) as exc:
        main.update_consent("student-1", main.ConsentUpdate(camera_enabled=True), None)
    assert exc.value.status_code == 409


@pytest.mark.parametrize("boom", [
    RuntimeError('duplicate key value violates unique constraint "signal_consent_pkey"'),
    RuntimeError({"code": "23505", "message": "duplicate key"}),
])
def test_losing_the_insert_race_answers_409_like_the_update_race(monkeypatch, boom):
    """Same race, same answer. If one lost race answered 500 and the other
    409, a client couldn't tell that "reload and try again" fits both."""
    fake = _fake(monkeypatch, PARENT, consent_row=None)

    real_table = fake.table

    def table(name):
        q = real_table(name)
        if name == "signal_consent":
            def insert(_row, **_kw):
                raise boom
            q.insert = insert
        return q

    monkeypatch.setattr(fake, "table", table)

    with pytest.raises(main.HTTPException) as exc:
        main.update_consent("student-1", main.ConsentUpdate(camera_enabled=True), None)
    assert exc.value.status_code == 409


def test_a_non_duplicate_insert_failure_is_still_a_500(monkeypatch):
    """The 409 is for a lost race specifically, not for every failed write."""
    fake = _fake(monkeypatch, PARENT, consent_row=None)
    real_table = fake.table

    def table(name):
        q = real_table(name)
        if name == "signal_consent":
            def insert(_row, **_kw):
                raise RuntimeError("connection reset")
            q.insert = insert
        return q

    monkeypatch.setattr(fake, "table", table)
    with pytest.raises(main.HTTPException) as exc:
        main.update_consent("student-1", main.ConsentUpdate(camera_enabled=True), None)
    assert exc.value.status_code == 500


def test_unparseable_timestamp_is_logged_not_swallowed(monkeypatch, capsys):
    """An unparseable timestamp suppresses the notice, so that must be
    logged, not silent."""
    _fake(monkeypatch, STUDENT, consent_row=_row(parent_enabled_at="not-a-timestamp"))
    assert main.get_consent("student-1", None)["needs_student_ack"] is False
    assert "unparseable timestamp" in capsys.readouterr().out


# ── no-op writes ─────────────────────────────────────────────────────────────

def test_setting_a_channel_to_its_current_value_touches_nothing(monkeypatch):
    """Otherwise a re-save would restamp updated_by/updated_at and raise a
    notice about a change that never happened."""
    fake = _fake(monkeypatch, PARENT, consent_row=_row(updated_at="2026-08-01T00:00:00+00:00"))
    main.update_consent("student-1", main.ConsentUpdate(camera_enabled=True), None)
    assert fake.store["signal_consent"][0]["updated_at"] == "2026-08-01T00:00:00+00:00"
