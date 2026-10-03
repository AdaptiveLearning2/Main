"""The teacher's roster across classes: each student once, and only from the caller's classes."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402
from tests.test_access_control import _FakeSupabase  # noqa: E402

ALICE, BOB, CARA = "student-1", "student-2", "student-3"


def _tables():
    # Alice is in both of teacher-1's classes; Cara is only in teacher-2's.
    return {
        "classes": [{"id": "maths", "teacher_id": "teacher-1"},
                    {"id": "stats", "teacher_id": "teacher-1"},
                    {"id": "other", "teacher_id": "teacher-2"}],
        "class_memberships": [
            {"class_id": "stats", "student_id": ALICE, "joined_at": "2026-01-15T09:00:00+00:00"},
            # 08:00 UTC, so earlier than the row above although it sorts later as text.
            {"class_id": "maths", "student_id": ALICE, "joined_at": "2026-01-15T10:00:00+02:00"},
            {"class_id": "maths", "student_id": BOB, "joined_at": "2026-02-01T09:00:00+00:00"},
            {"class_id": "other", "student_id": CARA, "joined_at": "2026-01-01T09:00:00+00:00"},
        ],
        "profiles": [
            {"id": ALICE, "display_name": "alice", "email": "alice@example.test",
             "role": "student", "grade_level": "Grade 5"},
            {"id": BOB, "display_name": "Bob", "email": "bob@example.test", "role": "student"},
            {"id": CARA, "display_name": "Cara", "email": "cara@example.test", "role": "student"},
        ],
    }


def _as(monkeypatch, uid, **fake_kw):
    fake = _FakeSupabase(_tables(), **fake_kw)
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": uid})
    return fake


def _query(fake, table):
    return next(q for name, q in zip(fake.table_calls, fake.queries) if name == table)


def test_each_student_appears_once_with_their_earliest_join(monkeypatch):
    _as(monkeypatch, "teacher-1")
    # Named columns only, ordered by name without regard to case.
    assert main.teacher_students(None) == [
        {"user_id": ALICE, "name": "alice", "email": "alice@example.test",
         "joined_at": "2026-01-15T10:00:00+02:00"},
        {"user_id": BOB, "name": "Bob", "email": "bob@example.test",
         "joined_at": "2026-02-01T09:00:00+00:00"},
    ]


def test_only_the_callers_classes_are_read(monkeypatch):
    fake = _as(monkeypatch, "teacher-1")
    out = main.teacher_students(None)

    assert CARA not in {r["user_id"] for r in out}
    assert ("teacher_id", "teacher-1") in _query(fake, "classes").filters
    assert ("class_id", ("in", ["maths", "stats"])) in _query(fake, "class_memberships").filters


def test_a_caller_who_teaches_nothing_reads_no_roster(monkeypatch):
    fake = _as(monkeypatch, ALICE)
    assert main.teacher_students(None) == []
    assert "class_memberships" not in fake.table_calls


def test_a_failed_roster_read_is_an_error_not_an_empty_class(monkeypatch):
    _as(monkeypatch, "teacher-1", table_raises={"class_memberships"})
    with pytest.raises(Exception):
        main.teacher_students(None)
