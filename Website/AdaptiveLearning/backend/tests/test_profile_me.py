"""GET /api/profile/me answers with the caller's row or an error, never a student-shaped placeholder."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402

TEACHER = {"id": "teacher-1"}


class _Profiles:
    def __init__(self, rows=(), raises=False):
        self.rows, self.raises, self.filters = list(rows), raises, []

    def table(self, name):
        db = self
        assert name == "profiles"
        q = type("Q", (), {})()
        q.select = lambda *a: q
        q.limit = lambda *a: q
        q.eq = lambda col, val: (db.filters.append((col, val)), q)[1]
        q.execute = lambda: (_ for _ in ()).throw(RuntimeError("down")) if db.raises \
            else type("R", (), {"data": [r for r in db.rows if r["id"] == dict(db.filters)["id"]]})()
        return q


@pytest.fixture(autouse=True)
def _as_teacher(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: TEACHER)


def test_the_callers_own_row_is_returned(monkeypatch):
    db = _Profiles([{"id": "teacher-1", "role": "teacher", "display_name": "Ms Rao"}])
    monkeypatch.setattr(main, "supabase", db)
    assert main.get_my_profile(None)["role"] == "teacher"
    assert db.filters == [("id", "teacher-1")]


@pytest.mark.parametrize("db,status", [
    (_Profiles(raises=True), 503),       # a blip: AuthContext falls back to the claim
    (_Profiles([]), 404),                # no row at all
], ids=["failed read", "missing row"])
def test_a_failed_or_missing_read_is_an_error_not_a_student(monkeypatch, db, status):
    """The placeholder said role 'student', so a teacher was routed into the student app."""
    monkeypatch.setattr(main, "supabase", db)
    with pytest.raises(main.HTTPException) as caught:
        main.get_my_profile(None)
    assert caught.value.status_code == status
