"""The teacher's roster across classes: each student once, and only from the caller's classes."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

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
            {"id": "m1", "class_id": "stats", "student_id": ALICE,
             "joined_at": "2026-01-15T09:00:00+00:00"},
            # 08:00 UTC, so earlier than the row above although it sorts later as text.
            {"id": "m2", "class_id": "maths", "student_id": ALICE,
             "joined_at": "2026-01-15T10:00:00+02:00"},
            {"id": "m3", "class_id": "maths", "student_id": BOB,
             "joined_at": "2026-02-01T09:00:00+00:00"},
            {"id": "m4", "class_id": "other", "student_id": CARA,
             "joined_at": "2026-01-01T09:00:00+00:00"},
        ],
        "profiles": [
            {"id": ALICE, "display_name": "alice", "email": "alice@example.test",
             "role": "student", "grade_level": "Grade 5"},
            {"id": BOB, "display_name": "Bob", "email": "bob@example.test", "role": "student"},
            {"id": CARA, "display_name": "Cara", "email": "cara@example.test", "role": "student"},
        ],
    }


def _big_roster(n):
    """One class of `n` students, membership ids in key order so pages split where expected."""
    return {
        "classes": [{"id": "big", "teacher_id": "teacher-1"}],
        "class_memberships": [{"id": f"m{i:04d}", "class_id": "big", "student_id": f"s{i:04d}",
                               "joined_at": "2026-03-01T09:00:00+00:00"} for i in range(n)],
        "profiles": [{"id": f"s{i:04d}", "display_name": f"Pupil {i:04d}",
                      "email": f"s{i:04d}@example.test", "role": "student"} for i in range(n)],
    }


def _as(monkeypatch, uid, tables=None, **fake_kw):
    fake = _FakeSupabase(tables or _tables(), **fake_kw)
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
    assert ("classes.teacher_id", "teacher-1") in _query(fake, "class_memberships").filters
    # One joined read: the class list is never fetched on its own.
    assert set(fake.table_calls) == {"class_memberships"}


def test_a_caller_who_teaches_nothing_gets_an_empty_roster(monkeypatch):
    # A student in two classes: without the inner join, every membership would come back.
    _as(monkeypatch, ALICE)
    assert main.teacher_students(None) == []


def test_a_failed_roster_read_is_a_503_not_an_empty_class(monkeypatch):
    _as(monkeypatch, "teacher-1", table_raises={"class_memberships"})
    with pytest.raises(HTTPException) as exc:
        main.teacher_students(None)
    assert exc.value.status_code == 503
    assert "Retry-After" in exc.value.headers


def test_a_failed_name_read_is_a_503_not_a_roster_of_students(monkeypatch):
    _as(monkeypatch, "teacher-1", table_raises={"profiles"})
    with pytest.raises(HTTPException) as exc:
        main.teacher_students(None)
    assert exc.value.status_code == 503


def test_a_blank_name_is_sent_as_null_not_invented(monkeypatch):
    tables = _tables()
    tables["profiles"][1]["display_name"] = ""
    _as(monkeypatch, "teacher-1", tables)
    out = main.teacher_students(None)
    # Nameless last; the page chooses what to draw for it.
    assert [(r["user_id"], r["name"]) for r in out] == [(ALICE, "alice"), (BOB, None)]


# 400: a project can set db-max-rows below the page size, so only an empty page ends the read.
@pytest.mark.parametrize("cap", [1000, 400])
def test_a_roster_past_the_row_cap_comes_back_whole(monkeypatch, cap):
    _as(monkeypatch, "teacher-1", _big_roster(1100), max_rows={"class_memberships": cap})
    out = main.teacher_students(None)
    assert len(out) == 1100
    assert {r["user_id"] for r in out} == {f"s{i:04d}" for i in range(1100)}
    assert all(r["name"] for r in out)


def test_a_student_split_across_pages_keeps_their_earliest_join(monkeypatch):
    tables = _big_roster(1100)
    # s0999's second class, joined first, is the last row by id: a page apart from their first.
    tables["class_memberships"].append({"id": "m1100", "class_id": "big", "student_id": "s0999",
                                        "joined_at": "2026-01-01T09:00:00+00:00"})
    _as(monkeypatch, "teacher-1", tables, max_rows={"class_memberships": 1000})
    out = {r["user_id"]: r for r in main.teacher_students(None)}
    assert out["s0999"]["joined_at"] == "2026-01-01T09:00:00+00:00"
