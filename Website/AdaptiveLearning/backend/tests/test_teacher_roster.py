"""The teacher's roster across classes: each student once, and only from the caller's classes."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

from types import SimpleNamespace  # noqa: E402

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


def _install(monkeypatch, uid, fake):
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": uid})
    return fake


def _as(monkeypatch, uid, tables=None, cls=_FakeSupabase, **fake_kw):
    return _install(monkeypatch, uid, cls(tables or _tables(), **fake_kw))


def _reads(fake):
    return fake.table_calls.count("class_memberships")


class _CursorIgnored(_FakeSupabase):
    """A server that drops `.gt()`, so every read answers the first page again; refuses a 21st read."""

    def table(self, name):
        query = super().table(name)
        query.gt = lambda _col, _val: query
        if len(self.table_calls) > 20:
            raise AssertionError("runaway roster read")
        return query


class _Endless:
    """`first` rows on the first read, then one row past the cursor on every read; refuses past 10,000."""

    def __init__(self, first, count=None):
        self.reads, self._first, self._count, self._after = 0, first, count, None

    def table(self, _name):
        self._after = None
        return self

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a):
        return self

    def gt(self, _col, val):
        self._after = val
        return self

    def execute(self):
        self.reads += 1
        if self.reads > 10_000:
            raise AssertionError("runaway roster read")
        start = 0 if self._after is None else int(self._after[1:]) + 1
        size = self._first if self._after is None else 1
        rows = [{"id": f"m{i:06d}", "student_id": f"s{i:06d}", "joined_at": "2026-03-01T09:00:00+00:00",
                 "profiles": {"display_name": f"Pupil {i}", "email": None}} for i in range(start, start + size)]
        return SimpleNamespace(data=rows, count=self._count if self._after is None else None)


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


# The first page's exact count ends the read, so no empty read confirms the end.
@pytest.mark.parametrize("cap, reads", [(1000, 2), (400, 3)])
def test_a_roster_past_the_row_cap_stops_at_its_count(monkeypatch, cap, reads):
    fake = _as(monkeypatch, "teacher-1", _big_roster(1100), max_rows={"class_memberships": cap})
    assert {r["user_id"] for r in main.teacher_students(None)} == {f"s{i:04d}" for i in range(1100)}
    assert _reads(fake) == reads
    # A later page's count is only what lies past the cursor, so only the first asks for one.
    assert [q._count for q in fake.queries] == ["exact"] + [None] * (reads - 1)


def test_a_roster_under_one_page_is_one_read(monkeypatch):
    # teacher-2's member is in the table, so a count that ignored the filter would read again.
    fake = _as(monkeypatch, "teacher-1")
    assert {r["user_id"] for r in main.teacher_students(None)} == {ALICE, BOB}
    assert _reads(fake) == 1


def test_with_no_count_the_roster_still_ends_on_an_empty_page(monkeypatch):
    fake = _as(monkeypatch, "teacher-1", _big_roster(1100), max_rows={"class_memberships": 1000},
               count_missing=True)
    assert len(main.teacher_students(None)) == 1100
    assert _reads(fake) == 3


@pytest.mark.parametrize("big, count_missing", [(True, False), (False, True)], ids=["counted", "uncounted"])
def test_a_cursor_that_stops_advancing_is_a_503_not_an_endless_read(monkeypatch, big, count_missing):
    fake = _as(monkeypatch, "teacher-1", _big_roster(1100) if big else _tables(), cls=_CursorIgnored,
               max_rows={"class_memberships": 1000}, count_missing=count_missing)
    with pytest.raises(HTTPException) as exc:
        main.teacher_students(None)
    assert exc.value.status_code == 503
    assert _reads(fake) == 2


def test_an_uncounted_read_that_never_ends_is_a_503_at_the_page_bound(monkeypatch):
    fake = _install(monkeypatch, "teacher-1", _Endless(first=1))
    with pytest.raises(HTTPException) as exc:
        main.teacher_students(None)
    assert exc.value.status_code == 503
    assert fake.reads == main._ROSTER_MAX_PAGES


def test_pages_past_what_the_count_needs_are_a_503(monkeypatch):
    # 1100 counted over a 1000-row first page needs 2 pages; later one-row pages would take 101.
    fake = _install(monkeypatch, "teacher-1", _Endless(first=1000, count=1100))
    with pytest.raises(HTTPException) as exc:
        main.teacher_students(None)
    assert exc.value.status_code == 503
    assert fake.reads == 2 + main._ROSTER_SPARE_PAGES
