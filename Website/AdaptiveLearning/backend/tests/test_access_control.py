"""Access control for student/class data: the service-role client bypasses RLS, so main.py's checks are all there is."""
import asyncio
import inspect
import os
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

# main.py builds a Supabase client at import time and raises without these.
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import llm_client  # noqa: E402
import main  # noqa: E402
from conftest import tighten  # noqa: E402


class _Result:
    def __init__(self, data, count=None):
        self.data = data
        # count="exact" total, independent of the row limit.
        self.count = count


class _Query:
    """Minimal stand-in for the supabase-py query builder chain."""

    def __init__(self, rows, max_rows=None, raises=None):
        self._rows = rows
        self._max_rows = max_rows
        # A read that fails, as opposed to one that returns nothing.
        self._raises = raises
        self._filters = self.filters = []
        self._limit = None
        self._order = None
        self._desc = False
        self._count = None
        self._cols = None

    def select(self, *cols, **kw):
        self._count = kw.get("count")
        # Only named columns come back, as in PostgREST. Embeds ("a(b)") aren't
        # modelled and fall back to whole rows.
        spec = ",".join(cols)
        if spec and "*" not in spec and "(" not in spec:
            self._cols = [c.strip() for c in spec.split(",") if c.strip()]
        return self

    def _project(self, row):
        if self._cols is None:
            return row
        return {c: row[c] for c in self._cols if c in row}

    def order(self, col, desc=False, **_k):
        self._order, self._desc = col, desc
        return self

    def limit(self, n, *_a, **_k):
        self._limit = n
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def gte(self, col, val):
        self._filters.append((col, ("gte", val)))
        return self

    def lte(self, col, val):
        self._filters.append((col, ("lte", val)))
        return self

    def in_(self, col, vals):
        self._filters.append((col, ("in", list(vals))))
        return self

    def lt(self, col, val):
        self._filters.append((col, ("lt", val)))
        return self

    def is_(self, col, val):
        # Matches the string the real client sends ("null"), not Python's None.
        self._filters.append((col, ("is", val)))
        return self

    def or_(self, expr):
        # Recorded, not evaluated, so `execute` refuses it against a non-empty
        # table rather than silently ignoring the filter.
        self.or_filters = getattr(self, "or_filters", []) + [expr]
        return self

    def _matches(self, row):
        for col, want in self._filters:
            have = row.get(col)
            if isinstance(want, tuple) and want[0] == "in":
                if have not in want[1]:
                    return False
            elif isinstance(want, tuple) and want[0] == "gte":
                if have is None or str(have) < str(want[1]):
                    return False
            elif isinstance(want, tuple) and want[0] == "lte":
                if have is None or str(have) > str(want[1]):
                    return False
            elif isinstance(want, tuple) and want[0] == "lt":
                if have is None or str(have) >= str(want[1]):
                    return False
            elif isinstance(want, tuple) and want[0] == "is":
                # Anything but "null"/"not.null" raises rather than being ignored.
                if want[1] == "null":
                    if have is not None:
                        return False
                elif want[1] == "not.null":
                    if have is None:
                        return False
                else:
                    raise AssertionError(f"unsupported is_() value {want[1]!r}")
            elif have != want:
                return False
        return True

    def execute(self):
        if self._raises:
            raise self._raises
        if getattr(self, "or_filters", None) and self._rows:
            raise AssertionError(
                "or_() is recorded but not evaluated by this fake; give the "
                "table no rows, or model the filter")
        rows = [r for r in self._rows if self._matches(r)]
        if self._order:
            rows = sorted(rows, key=lambda r: str(r.get(self._order, "")), reverse=self._desc)
        total = len(rows)
        # _max_rows mirrors db-max-rows, a server cap .limit() cannot raise.
        ceilings = [n for n in (self._limit, self._max_rows) if n is not None]
        if ceilings:
            rows = rows[:min(ceilings)]
        return _Result([self._project(r) for r in rows],
                       count=(total if self._count == "exact"
                              and not getattr(self, "_drop_count", False) else None))

    def single(self):
        rows = self.execute().data
        if not rows:
            raise RuntimeError("no rows")  # supabase-py raises rather than returning empty
        return _Single(rows[0])


class _Single:
    def __init__(self, row):
        self._row = row

    def execute(self):
        return _Result(self._row)


class _FakeSupabase:
    def __init__(self, tables, max_rows=None, rpc_results=None, rpc_raises=None,
                 table_raises=None, count_missing=False):
        self._tables = tables
        # count="exact" requested but none returned: a third state for totals.
        self._count_missing = count_missing
        # Table names whose reads fail, independently of each other.
        self._table_raises = set(table_raises or ())
        # int: every table; dict: per-table cap.
        self._max_rows = max_rows
        self._rpc_results = rpc_results or {}
        # (name, params) -> Exception or None, e.g. code ahead of its migration.
        self._rpc_raises = rpc_raises
        self.rpc_calls = []
        # Tables queried, in order: "never asked" vs "asked and got nothing".
        self.table_calls = []
        # Every query built, so tests can assert on its filters.
        self.queries = []

    def table(self, name):
        self.table_calls.append(name)
        cap = self._max_rows.get(name) if isinstance(self._max_rows, dict) else self._max_rows
        exc = RuntimeError(f"{name} read failed") if name in self._table_raises else None
        query = _Query(self._tables.get(name, []), max_rows=cap, raises=exc)
        query._drop_count = self._count_missing
        self.queries.append(query)
        return query

    def rpc(self, name, params=None):
        params = params or {}
        self.rpc_calls.append((name, params))
        exc = self._rpc_raises(name, params) if self._rpc_raises else None
        return _Rpc(self._rpc_results.get(name, []), exc)


class _Rpc:
    def __init__(self, data, exc=None):
        self._data = data
        self._exc = exc

    def execute(self):
        if self._exc:
            raise self._exc
        return _Result(self._data)


TEACHER = {"id": "teacher-1"}
OTHER_TEACHER = {"id": "teacher-2"}
STUDENT = {"id": "student-1"}
OTHER_STUDENT = {"id": "student-2"}
PARENT = {"id": "parent-1"}
STRANGER = {"id": "stranger-1"}

TABLES = {
    "classes": [
        {"id": "class-1", "teacher_id": "teacher-1"},
        {"id": "class-2", "teacher_id": "teacher-2"},
    ],
    "class_memberships": [
        {"id": "m1", "class_id": "class-1", "student_id": "student-1"},
    ],
    "parent_child_links": [
        {"id": "l1", "parent_id": "parent-1", "child_id": "student-1"},
    ],
    "sessions": [
        {"id": "session-1", "user_id": "student-1"},
    ],
}


@pytest.fixture(autouse=True)
def fake_supabase(monkeypatch):
    monkeypatch.setattr(main, "supabase", _FakeSupabase(TABLES))


# ── _can_view_student ────────────────────────────────────────────────────

def test_student_can_view_their_own_data():
    assert main._can_view_student(STUDENT, "student-1") is True


def test_teacher_can_view_a_student_in_their_class():
    assert main._can_view_student(TEACHER, "student-1") is True


def test_teacher_cannot_view_a_student_not_in_their_class():
    # teacher-2 owns class-2; student-1 isn't enrolled in it.
    assert main._can_view_student(OTHER_TEACHER, "student-1") is False


def test_linked_parent_can_view_their_child():
    assert main._can_view_student(PARENT, "student-1") is True


def test_parent_cannot_view_an_unlinked_child():
    assert main._can_view_student(PARENT, "student-2") is False


def test_unrelated_student_cannot_view_another_student():
    assert main._can_view_student(OTHER_STUDENT, "student-1") is False


def test_stranger_cannot_view_any_student():
    assert main._can_view_student(STRANGER, "student-1") is False


def test_verify_raises_403_for_unauthorized_viewer():
    with pytest.raises(main.HTTPException) as exc:
        main._verify_can_view_student(STRANGER, "student-1")
    assert exc.value.status_code == 403


# ── _verify_class_owner ──────────────────────────────────────────────────

def test_owning_teacher_passes_class_check():
    main._verify_class_owner("class-1", "teacher-1")  # must not raise


def test_non_owning_teacher_is_rejected():
    with pytest.raises(main.HTTPException) as exc:
        main._verify_class_owner("class-1", "teacher-2")
    assert exc.value.status_code == 403


def test_student_is_rejected_from_class_roster():
    with pytest.raises(main.HTTPException) as exc:
        main._verify_class_owner("class-1", "student-1")
    assert exc.value.status_code == 403


# ── GET /api/classes/{id} ────────────────────────────────────────────────
# Confirms the route actually calls the helper tested above.

def test_class_detail_returns_the_class_to_its_owner(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: TEACHER)
    monkeypatch.setattr(main, "supabase", _FakeSupabase({
        "classes": [{"id": "class-1", "teacher_id": "teacher-1", "name": "Algebra",
                     "join_code": "ABC123", "grade_level": "7"}],
    }))
    # Every column the page uses: a typo in a named select drops one silently.
    assert main.get_class("class-1", None) == {
        "id": "class-1", "name": "Algebra", "join_code": "ABC123", "grade_level": "7",
    }


def test_class_detail_rejects_a_non_owning_teacher(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: OTHER_TEACHER)
    with pytest.raises(main.HTTPException) as exc:
        main.get_class("class-1", None)
    assert exc.value.status_code == 403


def test_class_detail_404s_an_unknown_class(monkeypatch):
    # The frontend shows "Class not found" for this status only.
    monkeypatch.setattr(main, "get_user", lambda _r: TEACHER)
    with pytest.raises(main.HTTPException) as exc:
        main.get_class("class-does-not-exist", None)
    assert exc.value.status_code == 404


def test_class_detail_does_not_return_unnamed_columns(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: TEACHER)
    monkeypatch.setattr(main, "supabase", _FakeSupabase({
        "classes": [{"id": "class-1", "teacher_id": "teacher-1",
                     "name": "Algebra", "join_code": "ABC123",
                     "grade_level": "7", "secret_note": "not for the client"}],
    }))
    assert "secret_note" not in main.get_class("class-1", None)


def test_weekly_report_averages_only_non_null_values():
    assert main._avg([10, 20, None, 30]) == 20.0
    assert main._avg([None, None]) is None
    assert main._avg([]) is None


def test_topic_breakdown_handles_zero_attempts_without_dividing_by_zero(monkeypatch):
    monkeypatch.setattr(main, "supabase", _FakeSupabase({
        "user_math_performance": [
            {"user_id": "student-1", "topic_id": "t1", "attempted_questions": 0,
             "correct_questions": 0, "math_topics": {"topic_name": "algebra"}},
            {"user_id": "student-1", "topic_id": "t2", "attempted_questions": 4,
             "correct_questions": 3, "math_topics": {"topic_name": "geometry"}},
        ],
    }))
    out = main._topic_breakdown("student-1")
    by_topic = {r["topic_name"]: r for r in out}
    assert by_topic["algebra"]["accuracy"] == 0
    assert by_topic["geometry"]["accuracy"] == 75


def test_topic_breakdown_survives_a_missing_topic_join(monkeypatch):
    monkeypatch.setattr(main, "supabase", _FakeSupabase({
        "user_math_performance": [
            {"user_id": "student-1", "topic_id": "t1", "attempted_questions": 2,
             "correct_questions": 1, "math_topics": None},
        ],
    }))
    out = main._topic_breakdown("student-1")
    assert out[0]["topic_name"] == "Unknown"


# ── _weekly_signal_report / _signal_summary ──────────────────────────────

def _ts(days_ago: int, hour: int = 12) -> str:
    """An ISO timestamp N days back, in the same format the report compares."""
    d = main._utc_now() - main.timedelta(days=days_ago)
    return d.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


# Permissive default: `_consent` fails closed, so no row would report nothing.
_CONSENT_ALL = {"user_id": "student-1", "eeg_enabled": True,
                "headband_optical_enabled": True, "camera_enabled": True}


def _signal_tables(cog_rows, face_rows=None, session_rows=None,
                   heart_rows=None, consent_rows=None, rollup_rows=None):
    return {
        "cognitive_signals": cog_rows,
        "face_signals": face_rows if face_rows is not None else [],
        "heart_signals": heart_rows or [],
        "sessions": session_rows or [],
        "signal_consent": consent_rows if consent_rows is not None else [_CONSENT_ALL],
        # Present and empty: a missing table would read as a failed rollup read.
        "signal_daily_rollup": rollup_rows or [],
    }


def test_weekly_report_summary_renders_ratios_as_percentages(monkeypatch):
    """Signals are stored as 0..1 ratios."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_signal_tables([
        {"user_id": "student-1", "ts": _ts(1), "focus": 0.70, "stress": 0.30, "engagement": 0.6},
        {"user_id": "student-1", "ts": _ts(2), "focus": 0.74, "stress": 0.32, "engagement": 0.6},
    ])))
    report = main._weekly_signal_report("student-1")
    assert report["averages"]["focus"] == 0.72     # still a ratio on the wire
    assert "average focus was 72%" in report["summary"]
    assert "0.72%" not in report["summary"]


def test_weekly_report_flags_days_it_could_not_retrieve(monkeypatch):
    """The row cap is per table, so face rows must not hide cognitive's cutoff."""
    # Only cognitive is capped: it reaches 3 days back while face covers 7.
    cog = [{"user_id": "student-1", "ts": _ts(d), "focus": 0.5, "stress": 0.4, "engagement": 0.5}
           for d in range(0, 7)]
    face = [{"user_id": "student-1", "ts": _ts(d), "attention": 0.8} for d in range(0, 7)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _signal_tables(cog, face), max_rows={"cognitive_signals": 3}))
    report = main._weekly_signal_report("student-1")

    assert report["truncated"] is True
    days = {d["date"]: d for d in report["daily"]}
    unretrieved = [d for d in report["daily"] if not d["cognitive_retrieved"]]
    assert unretrieved, "older days must be flagged, not silently nulled"
    for d in unretrieved:
        assert d["focus"] is None
        assert d["face_retrieved"] is True   # face data for that day is real
        assert d["attention"] is not None
    covered = days[_ts(0)[:10]]
    assert covered["cognitive_retrieved"] is True


def test_weekly_report_detects_truncation_from_count_not_row_length(monkeypatch):
    """db-max-rows can cap below _REPORT_ROW_CAP, so row length cannot detect it."""
    cog = [{"user_id": "student-1", "ts": _ts(d % 7), "focus": 0.5} for d in range(50)]
    monkeypatch.setattr(main, "supabase",
                        _FakeSupabase(_signal_tables(cog), max_rows=10))
    report = main._weekly_signal_report("student-1")
    assert len(cog) < main._REPORT_ROW_CAP, "fixture must stay under our own cap"
    assert report["truncated"] is True
    assert report["sample_counts"]["cognitive"] == 10


def test_weekly_report_reports_session_truncation(monkeypatch):
    """sample_counts.sessions is rendered, so its truncation must set `truncated`."""
    sessions = [{"id": f"s{i}", "user_id": "student-1", "started_at": _ts(i % 7)}
                for i in range(40)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _signal_tables([], session_rows=sessions), max_rows={"sessions": 10}))
    report = main._weekly_signal_report("student-1")

    assert report["sample_counts"]["sessions"] == 10
    assert report["truncated"] is True


def test_weekly_report_keeps_a_day_whose_sessions_survived_the_cap(monkeypatch):
    """Sessions have their own cap, so a day with trimmed signals may still count sessions."""
    cog = [{"user_id": "student-1", "ts": _ts(d), "focus": 0.5} for d in range(0, 7)]
    sessions = [{"id": f"s{d}", "user_id": "student-1", "started_at": _ts(d)}
                for d in range(0, 7)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _signal_tables(cog, session_rows=sessions), max_rows={"cognitive_signals": 3}))
    # Face off, so only the session count can keep these days.
    report = main._weekly_signal_report("student-1", include_emotion=False)

    trimmed = [d for d in report["daily"] if d["cognitive_retrieved"] is False]
    assert trimmed, "days beyond cognitive's reach must still be reported"
    for d in trimmed:
        assert d["focus"] is None            # not read at all
        assert d["sessions_retrieved"] is True
        assert d["sessions"] == 1            # but the session count was read


def test_weekly_report_nulls_a_day_whose_sessions_were_cut(monkeypatch):
    """A day the cap kept us from reading did not have zero sessions."""
    sessions = [{"id": f"s{d}", "user_id": "student-1", "started_at": _ts(d)}
                for d in range(0, 7)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _signal_tables([], session_rows=sessions), max_rows={"sessions": 2}))
    report = main._weekly_signal_report("student-1")

    cut = [d for d in report["daily"] if d["sessions_retrieved"] is False]
    assert cut, "days beyond the session cap must be flagged"
    for d in cut:
        assert d["sessions"] is None, "0 would read as a day with no sessions"


# ── the day the cap cut into ─────────────────────────────────────────────
# The cap trims oldest-first; the oldest day returned is partial and is withheld.

def test_weekly_report_withholds_the_day_the_cap_cut_into(monkeypatch):
    """Three readings a day, cap of four: day 1 keeps one reading and is withheld."""
    cog = [{"user_id": "student-1", "ts": _ts(d, hour=h),
            "focus": 0.5, "stress": 0.4, "engagement": 0.6}
           for d in range(0, 3) for h in (9, 12, 15)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _signal_tables(cog), max_rows={"cognitive_signals": 4}))
    report = main._weekly_signal_report("student-1", include_emotion=False)

    assert report["sample_counts"]["cognitive"] == 4, "fixture must actually be cut"
    days = {d["date"]: d for d in report["daily"]}
    whole, boundary, beyond = _ts(0)[:10], _ts(1)[:10], _ts(2)[:10]

    assert days[whole]["cognitive_retrieved"] is True
    assert days[whole]["focus"] == 0.5

    # Withheld, but kept in the series because something was read for it.
    assert boundary in days, "a partly-read day must not be dropped as absent"
    assert days[boundary]["cognitive_retrieved"] is False
    assert days[boundary]["focus"] is None
    assert days[boundary]["stress"] is None
    assert days[boundary]["engagement"] is None

    assert days[beyond]["cognitive_retrieved"] is False
    assert days[beyond]["focus"] is None


def test_weekly_report_withholds_a_session_count_the_cap_cut_into(monkeypatch):
    """A count over a fraction of a day is simply wrong, with nothing to say so."""
    sessions = [{"id": f"s{d}-{h}", "user_id": "student-1", "started_at": _ts(d, hour=h)}
                for d in range(0, 3) for h in (9, 12, 15)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _signal_tables([], session_rows=sessions), max_rows={"sessions": 4}))
    report = main._weekly_signal_report("student-1", include_emotion=False)

    days = {d["date"]: d for d in report["daily"]}
    whole, boundary = _ts(0)[:10], _ts(1)[:10]

    assert days[whole]["sessions_retrieved"] is True
    assert days[whole]["sessions"] == 3
    # Not 1, which is what a third of the day's rows counts to.
    assert days[boundary]["sessions_retrieved"] is False
    assert days[boundary]["sessions"] is None


def test_a_cap_landing_on_a_day_boundary_understates_rather_than_overstates(monkeypatch):
    """A cut exactly between days is indistinguishable from mid-day, so it resolves conservatively.

    A complete day may be reported partial, never the reverse. Deliberate, not an off-by-one.
    """
    cog = [{"user_id": "student-1", "ts": _ts(d), "focus": 0.5} for d in range(0, 5)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _signal_tables(cog), max_rows={"cognitive_signals": 3}))
    report = main._weekly_signal_report("student-1", include_emotion=False)

    days = {d["date"]: d for d in report["daily"]}
    assert days[_ts(0)[:10]]["cognitive_retrieved"] is True
    assert days[_ts(2)[:10]]["cognitive_retrieved"] is False
    assert days[_ts(2)[:10]]["focus"] is None


def test_weekly_report_counts_every_session_not_just_the_retrieved_rows(monkeypatch):
    """sample_counts is rows retrieved; sessions_recorded is the true count."""
    sessions = [{"id": f"s{i}", "user_id": "student-1", "started_at": _ts(1, hour=i % 24)}
                for i in range(137)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_signal_tables([], [], sessions)))
    report = main._weekly_signal_report("student-1")
    assert report["sample_counts"]["sessions"] == main._SESSION_ROW_CAP   # rows we hold
    assert report["sessions_recorded"] == 137                             # sessions there were
    assert report["truncated"] is True


def test_weekly_report_session_count_falls_back_when_none_is_reported(monkeypatch):
    """With no exact count, the row count is the only figure available."""
    class _NoCountQuery(_Query):
        def select(self, *a, **kw):
            kw.pop("count", None)   # simulates a server that answers without one
            return super().select(*a, **kw)

    class _NoCount(_FakeSupabase):
        def table(self, name):
            self.table_calls.append(name)
            return _NoCountQuery(self._tables.get(name, []))

    monkeypatch.setattr(main, "supabase", _NoCount(_signal_tables(
        [], [], [{"id": "s1", "user_id": "student-1", "started_at": _ts(1)}])))
    report = main._weekly_signal_report("student-1")
    assert report["sessions_recorded"] == 1


def test_a_failed_read_is_not_reported_as_a_quiet_week(monkeypatch):
    """_fetch swallows its exception; `retrieved` is what separates that from recording nothing."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _signal_tables([], [], []), table_raises={"cognitive_signals"}))
    report = main._weekly_signal_report("student-1")

    assert report["retrieved"]["cognitive"] is False
    assert report["retrieved"]["face"] is True        # this one was read
    assert report["retrieved"]["sessions"] is True
    assert "no eeg" not in report["summary"].lower()
    assert "could not be loaded" in report["summary"]


def test_a_failed_read_marks_every_day_unretrieved(monkeypatch):
    """To the cap logic alone, a failed read looks like an untruncated empty one."""
    face = [{"user_id": "student-1", "ts": _ts(d), "attention": 0.8} for d in range(0, 7)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _signal_tables([], face, []), table_raises={"cognitive_signals"}))
    report = main._weekly_signal_report("student-1")

    assert report["daily"], "the face read succeeded, so the days must survive"
    for d in report["daily"]:
        assert d["cognitive_retrieved"] is False
        assert d["focus"] is None
        assert d["face_retrieved"] is True   # the table that was read is unaffected


def test_a_failed_sessions_read_does_not_report_zero_sessions(monkeypatch):
    """sessions_recorded must not fall back to the length of a failed read's empty list."""
    cog = [{"user_id": "student-1", "ts": _ts(1), "focus": 0.5}]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _signal_tables(cog, [], []), table_raises={"sessions"}))
    report = main._weekly_signal_report("student-1")

    assert report["sessions_recorded"] is None
    assert report["retrieved"]["sessions"] is False
    assert all(d["sessions"] is None and d["sessions_retrieved"] is False
               for d in report["daily"])
    assert report["retrieved"]["cognitive"] is True
    assert report["averages"]["focus"] == 0.5


def test_a_failed_face_read_is_not_the_opt_out(monkeypatch):
    """retrieved.face is None when opted out (no read made), False when the read broke."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _signal_tables([], [], []), table_raises={"face_signals"}))
    failed = main._weekly_signal_report("student-1")
    assert failed["retrieved"]["face"] is False
    assert "facial recognition data could not be loaded" in failed["summary"].lower()

    monkeypatch.setattr(main, "supabase", _FakeSupabase(_signal_tables([], [], [])))
    opted_out = main._weekly_signal_report("student-1", include_emotion=False)
    assert opted_out["retrieved"]["face"] is None
    # Neither an absence nor a failure is assertable about a read never made.
    assert "facial" not in opted_out["summary"].lower()


def test_a_read_trimmed_to_nothing_is_not_treated_as_untrimmed(monkeypatch):
    """A server cap of zero must not fall into the "nothing was trimmed" branch."""
    cog = [{"user_id": "student-1", "ts": _ts(d), "focus": 0.5} for d in range(0, 7)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _signal_tables(cog, [], []), max_rows={"cognitive_signals": 0}))
    report = main._weekly_signal_report("student-1", include_emotion=False)

    assert report["truncated"] is True
    assert report["sample_counts"]["cognitive"] == 0
    for d in report["daily"]:
        assert d["cognitive_retrieved"] is False
        assert d["focus"] is None


def test_a_quiet_week_is_still_reported_as_one(monkeypatch):
    """A read that succeeded and found nothing is still just an absence."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_signal_tables([], [], [])))
    report = main._weekly_signal_report("student-1")

    assert report["retrieved"] == {"cognitive": True, "face": True,
                                   "heart": True, "sessions": True,
                                   "rollup": True}
    assert report["summary"] == "No EEG, facial recognition or heart rate samples were recorded this week."
    assert report["sessions_recorded"] == 0


def test_a_failed_rpc_reports_not_retrieved(monkeypatch):
    """A broken aggregate must not read as a quiet week."""
    fake = _FakeSupabase({}, rpc_raises=lambda *_a: RuntimeError("57014: statement timeout"))
    monkeypatch.setattr(main, "supabase", fake)

    out = main._signal_summary("student-1")
    # Revocation dates are null: the channel isn't off, the read failed.
    assert out == {**main._EMPTY_SUMMARY, "face_included": True,
                   "retrieved": False, "dominant_emotion": None,
                   "emotion_revoked_at": None, "heart_revoked_at": None}


def test_signal_summary_surfaces_sample_counts(monkeypatch):
    monkeypatch.setattr(main, "supabase", _FakeSupabase({}, rpc_results={
        "student_signal_summary": [{
            "focus": 0.6, "stress": 0.3, "engagement": 0.5, "face_attention": 0.8,
            "sessions": 3, "cognitive_samples": 120, "face_samples": 40,
        }],
    }))
    out = main._signal_summary("student-1")
    assert out["cognitive_samples"] == 120
    assert out["face_samples"] == 40


def test_signal_summary_returns_empty_shape_when_rpc_yields_nothing(monkeypatch):
    monkeypatch.setattr(main, "supabase", _FakeSupabase({}, rpc_results={}))
    out = main._signal_summary("student-1")
    assert out == {**main._EMPTY_SUMMARY, "dominant_emotion": None,
                   "emotion_revoked_at": None, "heart_revoked_at": None}
    assert out is not main._EMPTY_SUMMARY, "callers must not share the module-level dict"


def test_signal_summaries_fetches_every_child_in_one_round_trip(monkeypatch):
    fake = _FakeSupabase({}, rpc_results={
        "student_signal_summary_many": [
            {"student_id": "student-1", "focus": 0.6, "sessions": 2,
             "cognitive_samples": 10, "face_samples": 5},
            {"student_id": "student-2", "focus": 0.4, "sessions": 1,
             "cognitive_samples": 8, "face_samples": 0},
        ],
    })
    monkeypatch.setattr(main, "supabase", fake)
    out = main._signal_summaries(["student-1", "student-2"])
    assert len(fake.rpc_calls) == 1, "one call for all children, not one each"
    assert fake.rpc_calls[0][0] == "student_signal_summary_many"
    assert out["student-1"]["focus"] == 0.6
    assert out["student-2"]["cognitive_samples"] == 8


def test_signal_summaries_skips_the_round_trip_for_no_children(monkeypatch):
    fake = _FakeSupabase({}, rpc_results={})
    monkeypatch.setattr(main, "supabase", fake)
    assert main._signal_summaries([]) == {}
    assert fake.rpc_calls == []


# ── a failed aggregate is not a quiet week ───────────────────────────────
# Both helpers swallow exceptions and answer 200; `retrieved` tells the cases apart.

def test_a_failed_summary_says_so_rather_than_reporting_zero_samples(monkeypatch):
    def boom(name, params):
        return RuntimeError("connection reset")
    monkeypatch.setattr(main, "supabase",
                        _FakeSupabase({}, rpc_results={}, rpc_raises=boom))
    out = main._signal_summary("student-1")
    assert out["retrieved"] is False
    # Still defaults, but labelled as such.
    assert out["cognitive_samples"] == 0
    assert out["focus"] is None


def test_a_summary_that_reached_the_database_is_retrieved_even_with_no_rows(monkeypatch):
    """The flag is about whether the query ran, not whether it found anything."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase({}, rpc_results={}))
    assert main._signal_summary("student-1")["retrieved"] is True


def test_a_failed_batch_summary_is_distinguishable_from_an_empty_one(monkeypatch):
    """None for a failed read, {} for one that succeeded and found nothing."""
    def boom(name, params):
        return RuntimeError("connection reset")
    monkeypatch.setattr(main, "supabase",
                        _FakeSupabase({}, rpc_results={}, rpc_raises=boom))
    assert main._signal_summaries(["student-1"]) is None

    monkeypatch.setattr(main, "supabase", _FakeSupabase({}, rpc_results={}))
    assert main._signal_summaries(["student-1"]) == {}


def test_children_endpoint_marks_a_failed_batch_summary_as_unretrieved(monkeypatch):
    """The parent dashboard renders "no data yet" straight off this payload."""
    def boom(name, params):
        return RuntimeError("connection reset")
    monkeypatch.setattr(main, "supabase", _FakeSupabase({
        "parent_child_links": [{"parent_id": "parent-1", "child_id": "student-1",
                                "created_at": "2026-01-01"}],
        "user_stats": [], "sessions": [], "user_math_performance": [],
    }, rpc_results={}, rpc_raises=boom))
    monkeypatch.setattr(main, "get_user", lambda r: {"id": "parent-1"})

    children = main.my_children(None)
    assert children[0]["signal_summary"]["retrieved"] is False


def test_children_endpoint_reports_a_working_read_with_no_rows_as_retrieved(monkeypatch):
    """Mirror of the above: an empty result is a quiet week, not a failure."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase({
        "parent_child_links": [{"parent_id": "parent-1", "child_id": "student-1",
                                "created_at": "2026-01-01"}],
        "user_stats": [], "sessions": [], "user_math_performance": [],
    }, rpc_results={}))
    monkeypatch.setattr(main, "get_user", lambda r: {"id": "parent-1"})

    children = main.my_children(None)
    assert children[0]["signal_summary"]["retrieved"] is True


def test_strategies_basis_reports_that_its_signals_did_not_load(monkeypatch, set_flag):
    """The advice degrades to generic; the response must say the signals failed to load.

    strategy_llm_enabled is pinned off: this is about the rule-based path.
    """
    def boom(name, params):
        if name == "student_signal_summary":
            return RuntimeError("connection reset")
        return None
    monkeypatch.setattr(main, "supabase", _FakeSupabase({
        "user_math_performance": [],
    }, rpc_results={}, rpc_raises=boom))
    monkeypatch.setattr(main, "get_user", lambda r: {"id": "student-1"})
    set_flag("strategy_llm_enabled", False)

    out = main.student_learning_strategies(
        "student-1", None, main.LearningStrategyRequest())
    assert out["basis"]["signals_retrieved"] is False
    assert out["source"] == "rule-based"
    assert len(out["strategies"]) >= 3


# ── the opt-out on the headline summaries (parent dashboard) ─────────────

def test_signal_summaries_pass_the_opt_out_into_the_aggregate(monkeypatch):
    """The aggregate takes the opt-out itself; nulling on the way out would still read the rows."""
    fake = _FakeSupabase({}, rpc_results={"student_signal_summary_many": []})
    monkeypatch.setattr(main, "supabase", fake)
    main._signal_summaries(["student-1"], include_emotion=False)
    assert fake.rpc_calls[0][1]["p_include_emotion"] is False

    fake.rpc_calls.clear()
    main._signal_summaries(["student-1"])
    assert fake.rpc_calls[0][1]["p_include_emotion"] is True, "included unless asked otherwise"


def test_signal_summary_passes_the_opt_out_into_the_aggregate(monkeypatch):
    fake = _FakeSupabase({}, rpc_results={"student_signal_summary": []})
    monkeypatch.setattr(main, "supabase", fake)
    main._signal_summary("student-1", include_emotion=False)
    assert fake.rpc_calls[0][1]["p_include_emotion"] is False


def test_summary_marks_the_opt_out_rather_than_reporting_no_face_data(monkeypatch):
    """face_included separates the opt-out from a student the camera never saw."""
    fake = _FakeSupabase({}, rpc_results={"student_signal_summary_many": [
        {"student_id": "student-1", "focus": 0.6, "face_attention": None,
         "sessions": 2, "cognitive_samples": 10, "face_samples": 0},
    ]})
    monkeypatch.setattr(main, "supabase", fake)
    off = main._signal_summaries(["student-1"], include_emotion=False)
    assert off["student-1"]["face_included"] is False
    on = main._signal_summaries(["student-1"])
    assert on["student-1"]["face_included"] is True


def test_children_endpoint_threads_the_opt_out(monkeypatch):
    """Facial reporting switched off on a report stays off on the dashboard."""
    tables = {**TABLES, "parent_child_links": [
        {"id": "l1", "parent_id": "parent-1", "child_id": "student-1",
         "created_at": "2026-07-01T00:00:00Z"},
    ]}
    fake = _FakeSupabase(tables, rpc_results={"student_signal_summary_many": []})
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "get_user", lambda _r: PARENT)
    children = main.my_children(None, include_face=False)
    assert fake.rpc_calls[0][1]["p_include_emotion"] is False
    # The fallback shape carries the flag too, or the tile reads "N/A", not "Off".
    assert all(c["signal_summary"]["face_included"] is False for c in children)
    assert children, "fixture should link at least one child to this parent"


# ── /api/students/{id}/signal-summary ────────────────────────────────────
# Aggregated in Postgres over the whole window, not a capped row read.

_SUMMARY_ROW = {
    "focus": 0.7, "stress": 0.3, "engagement": 0.5, "face_attention": 0.8,
    "sessions": 4, "cognitive_samples": 51840, "face_samples": 51840,
    "dominant_emotion": "focused",
}


def _summary_fake(monkeypatch, viewer, row=None, consent=None):
    fake = _FakeSupabase({**TABLES,
                          "signal_consent": [consent] if consent else [_CONSENT_ALL]},
                         rpc_results={
        "student_signal_summary": [row if row is not None else _SUMMARY_ROW],
    })
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "get_user", lambda _r: viewer)
    return fake


def test_signal_summary_endpoint_rejects_a_viewer_with_no_relationship(monkeypatch):
    fake = _summary_fake(monkeypatch, STRANGER)
    with pytest.raises(main.HTTPException) as exc:
        main.student_signal_summary("student-1", None)
    assert exc.value.status_code == 403
    assert fake.rpc_calls == [], "access is decided before the aggregate runs"


def test_signal_summary_endpoint_allows_a_teacher_of_the_students_class(monkeypatch):
    _summary_fake(monkeypatch, TEACHER)
    out = main.student_signal_summary("student-1", None)
    assert out["focus"] == 0.7


def test_signal_summary_endpoint_rejects_a_teacher_of_a_different_class(monkeypatch):
    _summary_fake(monkeypatch, OTHER_TEACHER)
    with pytest.raises(main.HTTPException) as exc:
        main.student_signal_summary("student-1", None)
    assert exc.value.status_code == 403


def test_signal_summary_endpoint_allows_a_linked_parent(monkeypatch):
    """Gated on the relationship, not the role of its main caller."""
    _summary_fake(monkeypatch, PARENT)
    assert main.student_signal_summary("student-1", None)["focus"] == 0.7


def test_signal_summary_endpoint_counts_the_whole_window_not_a_row_cap(monkeypatch):
    """The figure a teacher sees is Postgres's count over the window."""
    _summary_fake(monkeypatch, TEACHER)
    out = main.student_signal_summary("student-1", None)
    assert out["cognitive_samples"] == 51840
    assert out["face_samples"] == 51840


def test_signal_summary_endpoint_threads_the_opt_out(monkeypatch):
    fake = _summary_fake(monkeypatch, TEACHER)
    main.student_signal_summary("student-1", None, include_face=False)
    assert fake.rpc_calls[0][1]["p_include_emotion"] is False

    fake.rpc_calls.clear()
    main.student_signal_summary("student-1", None)
    assert fake.rpc_calls[0][1]["p_include_emotion"] is True, "included unless asked otherwise"


def test_signal_summary_endpoint_clamps_the_day_range(monkeypatch):
    """Same bounds as the weekly report: no unbounded scan from the query string."""
    fake = _summary_fake(monkeypatch, TEACHER)
    main.student_signal_summary("student-1", None, days=9999)
    assert fake.rpc_calls[0][1]["p_days"] == 30

    fake.rpc_calls.clear()
    main.student_signal_summary("student-1", None, days=0)
    assert fake.rpc_calls[0][1]["p_days"] == 1


def test_signal_summary_carries_the_dominant_emotion(monkeypatch):
    """Computed in the aggregate, not from a capped client-side row read."""
    _summary_fake(monkeypatch, TEACHER)
    assert main.student_signal_summary("student-1", None)["dominant_emotion"] == "focused"


def test_signal_summary_withholds_the_dominant_emotion_when_the_opt_out_is_on(monkeypatch):
    """emotion is a facial reading."""
    _summary_fake(monkeypatch, TEACHER)
    out = main.student_signal_summary("student-1", None, include_face=False)
    assert out["dominant_emotion"] is None
    assert out["face_included"] is False


def test_batch_summaries_carry_no_dominant_emotion(monkeypatch):
    """Only the single-student RPC computes it; in the batch it would be always-null."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase({}, rpc_results={
        "student_signal_summary_many": [
            {"student_id": "student-1", "focus": 0.4, "sessions": 1, "cognitive_samples": 3},
        ],
    }))
    assert "dominant_emotion" not in main._signal_summaries(["student-1"])["student-1"]


# ── where the facial-recognition opt-out reaches, and where it does not ──
# Reporting surfaces honour it; live monitoring and session review, which
# never render the switch, deliberately do not.

def test_every_reporting_endpoint_takes_the_opt_out():
    for fn in (main.student_weekly_report, main.student_signal_summary, main.my_children):
        assert "include_face" in inspect.signature(fn).parameters, (
            f"{fn.__name__} renders facial data on a surface that shows the switch"
        )


def test_the_opt_out_deliberately_does_not_reach_live_or_session_review():
    """Asserts an absence, on purpose: adding one means the page needs the switch too."""
    for fn in (main.class_live, main.session_signals):
        assert "include_face" not in inspect.signature(fn).parameters, (
            f"{fn.__name__} now honours the opt-out; update the scope note in "
            "frontend/src/lib/facePref.js and render the switch on that page"
        )


# ── a session belongs to one student ─────────────────────────────────────
# `record_answer` and `end_session` check ownership before writing anything.

class _OwnedSessionClient:
    """One `sessions` row with a given owner. Records what was written."""

    def __init__(self, owner):
        self.owner = owner
        self.writes = []

    def rpc(self, name, params):
        client = self

        class _R:
            def execute(self):
                client.writes.append(("rpc", name))
                return type("R", (), {"data": None})()

        return _R()

    def table(self, name):
        client, table = self, name

        class _Q:
            def select(self, *_a, **_k): return self
            def eq(self, *_a, **_k): return self
            def is_(self, *_a, **_k): return self
            def order(self, *_a, **_k): return self
            def limit(self, *_a, **_k): return self
            def single(self): return self

            def insert(self, row):
                client.writes.append((table, "insert", row))
                return self

            def update(self, row):
                client.writes.append((table, "update", row))
                return self

            def execute(self):
                if table == "sessions":
                    return type("R", (), {"data": {
                        "id": "session-1", "user_id": client.owner,
                        "questions_answered": 2, "correct_answers": 1,
                        "started_at": "2026-08-15T10:00:00Z", "ended_at": None}})()
                return type("R", (), {"data": []})()

        return _Q()


@pytest.mark.parametrize("endpoint", ["answer", "end"])
def test_a_student_may_not_touch_another_students_session(monkeypatch, endpoint):
    client = _OwnedSessionClient("student-1")
    monkeypatch.setattr(main, "supabase", client)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "student-2"})
    stopped = []
    monkeypatch.setattr(main.eeg_poller, "stop",
                        lambda *a, **k: stopped.append(a) or {"running": False})

    with pytest.raises(main.HTTPException) as exc:
        if endpoint == "answer":
            main.record_answer(
                session_id="session-1",
                payload=main.AnswerPayload(question_id="q-1", selected_index=0,
                                           correct=True),
                request=None)
        else:
            main.end_session(session_id="session-1", request=None)

    assert exc.value.status_code == 403
    # Refused before any write. `security_events` is excluded by name: the
    # refusal's own audit row is expected.
    product_writes = [w for w in client.writes if w[0] != "security_events"]
    assert product_writes == [], f"the refusal came too late: {product_writes}"
    assert stopped == [], "another student's poller was stopped before the check"


@pytest.mark.parametrize("endpoint", ["answer", "end"])
def test_the_owner_is_still_allowed(monkeypatch, endpoint):
    """Mirror of the test above, so it can't be satisfied by refusing everyone."""
    client = _OwnedSessionClient("student-1")
    monkeypatch.setattr(main, "supabase", client)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "student-1"})
    monkeypatch.setattr(main.eeg_poller, "stop", lambda *_a, **_k: {"running": False})
    monkeypatch.setattr(main, "_close_session", lambda *_a: {"discarded": False})

    if endpoint == "answer":
        out = main.record_answer(
            session_id="session-1",
            payload=main.AnswerPayload(question_id="q-1", selected_index=0,
                                       correct=True),
            request=None)
        assert any(w[:2] == ("session_answers", "insert") for w in client.writes)
    else:
        out = main.end_session(session_id="session-1", request=None)
    assert out["ok"] is True


def test_the_two_academic_write_endpoints_check_ownership():
    """Derived from the source, so the ownership check can't be dropped silently."""
    for fn in (main.record_answer, main.end_session):
        source = inspect.getsource(fn)
        assert "_session_or_403(" in source or "_verify_session_owner(" in source, (
            f"{fn.__name__} writes a student's academic history without "
            "checking whose session it is")


class _RaisingSessions:
    """`.single()` on zero rows raises PGRST116, as PostgREST does, rather than returning empty."""

    def table(self, _name):
        class _Q:
            def select(self, *_a, **_k): return self
            def eq(self, *_a, **_k): return self
            def single(self): return self

            def execute(self):
                raise RuntimeError(
                    "{'code': 'PGRST116', 'details': 'The result contains 0 rows'}")

        return _Q()


@pytest.mark.parametrize("call", [
    lambda: main._session_or_403("does-not-exist", "someone"),
    lambda: main._verify_session_owner("does-not-exist", "someone"),
])
def test_a_missing_session_returns_404_not_500(monkeypatch, call):
    """Mirror of `test_missing_class_returns_404_not_500`, for the session helper."""
    monkeypatch.setattr(main, "supabase", _RaisingSessions())

    with pytest.raises(main.HTTPException) as exc:
        call()
    assert exc.value.status_code == 404


def test_every_single_row_lookup_handles_the_missing_row():
    """Every `.single()` sits inside a `try` body, since zero rows raises rather than returning empty.

    AST, not grep: a regex matches docstring mentions and can't see real nesting.
    """
    import ast

    source = open(main.__file__, encoding="utf-8").read()
    tree = ast.parse(source)

    # Nearest enclosing function for a line, so a failure names something findable.
    scopes = sorted(
        ((n.lineno, n.end_lineno, n.name) for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))),
        key=lambda s: s[1] - s[0])

    def enclosing(lineno):
        for start, end, name in scopes:
            if start <= lineno <= end:
                return name
        return "<module>"

    offenders = []

    def walk(node, in_try):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "single" and not in_try):
            offenders.append(f"{enclosing(node.lineno)} (main.py:{node.lineno})")
        if isinstance(node, ast.Try):
            # Only the `try` body is protected, not except/else/finally.
            for child in node.body:
                walk(child, True)
            for child in [*node.handlers, *node.orelse, *node.finalbody]:
                walk(child, in_try)
            return
        for child in ast.iter_child_nodes(node):
            walk(child, in_try)

    walk(tree, False)

    assert not offenders, (
        "these .single() lookups answer a missing row with an unhandled "
        f"APIError -- a 500 for an id any client can supply: {offenders}. "
        "Use _row_or_404(), or catch it where 'absent' is a legitimate answer.")


def test_the_class_summary_route_is_not_shadowed_by_the_id_route():
    """FastAPI matches in registration order, so a literal after a placeholder is unreachable."""
    from fastapi.routing import APIRoute

    order = [r.endpoint.__name__ for r in main.app.routes
             if isinstance(r, APIRoute) and r.path in
             ("/api/classes/summary", "/api/classes/{class_id}")]

    assert order.index("class_summaries") < order.index("get_class"), (
        "/api/classes/summary is registered after /api/classes/{class_id}, so "
        "it is shadowed and answers 404")


class _ClassSummaryClient:
    """Classes, memberships and the stats the roster averages come from."""

    def __init__(self, classes, members, stats, raises=()):
        self.classes, self.members, self.stats = classes, members, stats
        self.raises = set(raises)
        self.tables = []

    def table(self, name):
        client, table = self, name

        class _Q:
            def select(self, *_a, **_k): return self
            def eq(self, *_a, **_k): return self
            def in_(self, *_a, **_k): return self
            def is_(self, *_a, **_k): return self

            def execute(self):
                client.tables.append(table)
                if table in client.raises:
                    raise RuntimeError(f"{table} unavailable")
                return _Result({"classes": client.classes,
                                "class_memberships": client.members,
                                "user_stats": client.stats}.get(table, []))

        return _Q()


def test_class_summary_averages_accuracy_over_students_who_attempted(monkeypatch):
    """A student with no attempts is out of the accuracy average but in the streak one."""
    monkeypatch.setattr(main, "get_user", lambda _r: TEACHER)
    monkeypatch.setattr(main, "supabase", _ClassSummaryClient(
        classes=[{"id": "c1"}],
        members=[{"class_id": "c1", "student_id": s} for s in ("a", "b", "c")],
        stats=[{"user_id": "a", "total_questions": 10, "total_correct": 8, "current_streak": 4},
               {"user_id": "b", "total_questions": 10, "total_correct": 4, "current_streak": 2},
               {"user_id": "c", "total_questions": 0, "total_correct": 0, "current_streak": 0}],
    ))

    out = main.class_summaries(None)

    assert out["c1"]["avgAccuracy"] == 60      # (80 + 40) / 2, not / 3
    assert out["c1"]["avgStreak"] == 2         # (4 + 2 + 0) / 3
    assert out["c1"]["retrieved"] is True


def test_a_class_nobody_has_attempted_reports_null_not_zero(monkeypatch):
    """`0` would be a class that tried and got everything wrong."""
    monkeypatch.setattr(main, "get_user", lambda _r: TEACHER)
    monkeypatch.setattr(main, "supabase", _ClassSummaryClient(
        classes=[{"id": "c1"}],
        members=[{"class_id": "c1", "student_id": "a"}],
        stats=[{"user_id": "a", "total_questions": 0, "total_correct": 0, "current_streak": 0}],
    ))

    assert main.class_summaries(None)["c1"]["avgAccuracy"] is None


def test_a_failed_membership_read_is_not_a_class_of_zeros(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: TEACHER)
    monkeypatch.setattr(main, "supabase", _ClassSummaryClient(
        classes=[{"id": "c1"}], members=[], stats=[],
        raises=["class_memberships"]))

    out = main.class_summaries(None)

    assert out["c1"]["retrieved"] is False
    assert out["c1"]["avgAccuracy"] is None


def test_the_summary_does_not_read_a_roster_per_class(monkeypatch):
    """One query per class here would move the N+1, not remove it."""
    monkeypatch.setattr(main, "get_user", lambda _r: TEACHER)
    client = _ClassSummaryClient(
        classes=[{"id": f"c{i}"} for i in range(12)],
        members=[{"class_id": f"c{i}", "student_id": f"s{i}"} for i in range(12)],
        stats=[{"user_id": f"s{i}", "total_questions": 4, "total_correct": 2,
                "current_streak": 1} for i in range(12)])
    monkeypatch.setattr(main, "supabase", client)

    main.class_summaries(None)

    assert len(client.tables) <= 4, (
        f"twelve classes cost {len(client.tables)} queries: {client.tables}")


def test_missing_class_returns_404_not_500():
    # .single() raises on zero rows.
    with pytest.raises(main.HTTPException) as exc:
        main._verify_class_owner("class-does-not-exist", "teacher-1")
    assert exc.value.status_code == 404


def test_class_live_clears_the_heart_reading_alongside_cognitive_and_face_when_stale(monkeypatch):
    """A stale session's heart reading is nulled like cognitive and face."""
    from datetime import datetime, timedelta

    stale_ts = (datetime.utcnow() - timedelta(seconds=700)).isoformat()

    class _Tbl:
        def __init__(self, name, tables, updates):
            self._name, self._tables, self._updates = name, tables, updates
            self._single = False
            self._update_fields = None

        def select(self, *_a, **_k): return self
        def eq(self, *_a, **_k): return self
        def in_(self, *_a, **_k): return self
        def is_(self, *_a, **_k): return self
        def order(self, *_a, **_k): return self
        def limit(self, *_a, **_k): return self
        def single(self): self._single = True; return self

        def update(self, fields, *_a, **_k):
            self._update_fields = fields
            return self

        def execute(self):
            if self._update_fields is not None:
                self._updates.append((self._name, self._update_fields))
                return type("R", (), {"data": [self._update_fields]})()
            rows = self._tables.get(self._name, [])
            return type("R", (), {"data": (rows[0] if rows else None) if self._single else rows})()

    updates = []
    tables = {
        "classes":            [{"teacher_id": "teacher-1"}],
        "class_memberships":  [{"student_id": "student-1"}],
        # `user_id` groups the batched open-session read back per student.
        "sessions":           [{"id": "session-1", "user_id": "student-1",
                                "started_at": stale_ts}],
        "cognitive_signals":  [{"ts": stale_ts, "focus": 0.5}],
        "face_signals":       [{"ts": stale_ts, "emotion": "neutral"}],
        "heart_signals":      [{"ts": stale_ts, "heart_rate_bpm": 72, "trusted": True}],
        "session_answers":    [],
        "profiles":           [{"id": "student-1", "display_name": "Ada", "email": "a@x.com"}],
    }
    # All channels come through one RPC, answered from the same canned tables.
    _CHANNEL_TABLE = {"cognitive": "cognitive_signals", "face": "face_signals",
                      "heart": "heart_signals", "answer": "session_answers"}

    def _rpc(_self, name, params):
        assert name == "latest_signals_for_sessions", f"unexpected rpc {name}"
        rows = []
        for sid in params["p_session_ids"]:
            for channel, table in _CHANNEL_TABLE.items():
                canned = tables.get(table) or []
                if canned:
                    rows.append({"session_id": sid, "channel": channel,
                                 "payload": canned[0]})
        return type("R", (), {"execute": lambda _s: type("X", (), {"data": rows})()})()

    monkeypatch.setattr(main, "supabase",
                        type("S", (), {"table": lambda _s, n: _Tbl(n, tables, updates),
                                       "rpc": _rpc})())
    monkeypatch.setattr(main, "get_user", lambda _r: TEACHER)
    stop_calls = []
    monkeypatch.setattr(main.eeg_poller, "stop",
                        lambda sid, uid=None: stop_calls.append((sid, uid))
                        or {"running": False, "samples": 0})

    out = main.class_live("class-1", None)

    assert any(t == "sessions" for t, _ in updates), "the stale session was never marked ended"
    assert out[0]["latest_cognitive"] is None
    assert out[0]["latest_face"] is None
    assert out[0]["latest_heart"] is None, (
        "a stale session still reported a live-looking heart reading"
    )
    # stop releases the reservation by user_id: the student's, not the teacher's.
    assert stop_calls == [("session-1", "student-1")], (
        "the stale-session sweep must release the student's reservation, "
        "not the teacher's (or none at all)"
    )


# ── facial-recognition opt-out ───────────────────────────────────────────

def test_report_without_face_never_queries_face_signals(monkeypatch):
    """The opt-out means "not read", not "read and hidden"."""
    fake = _FakeSupabase(_signal_tables(
        [{"user_id": "student-1", "ts": _ts(1), "focus": 0.7}],
        [{"user_id": "student-1", "ts": _ts(1), "attention": 0.9, "emotion": "happy"}],
    ))
    monkeypatch.setattr(main, "supabase", fake)
    report = main._weekly_signal_report("student-1", include_emotion=False)

    assert "face_signals" not in fake.table_calls
    assert report["face_included"] is False
    assert report["averages"]["face_attention"] is None
    assert report["highlights"]["dominant_emotion"] is None
    assert report["sample_counts"]["face"] == 0
    assert report["averages"]["focus"] == 0.7


def test_report_without_face_marks_days_not_applicable_rather_than_unretrieved(monkeypatch):
    """False means "the cap stopped us", and the UI checks `=== false`."""
    fake = _FakeSupabase(_signal_tables(
        [{"user_id": "student-1", "ts": _ts(1), "focus": 0.7}]))
    monkeypatch.setattr(main, "supabase", fake)
    report = main._weekly_signal_report("student-1", include_emotion=False)
    assert report["daily"], "days should still be reported"
    assert all(d["face_retrieved"] is None for d in report["daily"])


def test_report_without_face_does_not_claim_facial_data_was_absent(monkeypatch):
    """An absence can't be claimed for a read the opt-out skipped."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_signal_tables([])))
    off = main._weekly_signal_report("student-1", include_emotion=False)
    on = main._weekly_signal_report("student-1", include_emotion=True)
    assert "facial recognition" not in off["summary"]
    assert "facial recognition" in on["summary"]


def test_report_with_face_still_included_by_default(monkeypatch):
    fake = _FakeSupabase(_signal_tables(
        [{"user_id": "student-1", "ts": _ts(1), "focus": 0.7}],
        [{"user_id": "student-1", "ts": _ts(1), "attention": 0.9}],
    ))
    monkeypatch.setattr(main, "supabase", fake)
    report = main._weekly_signal_report("student-1")
    assert "face_signals" in fake.table_calls
    assert report["face_included"] is True
    assert report["averages"]["face_attention"] == 0.9


# ── learning strategies ──────────────────────────────────────────────────

def _strategy_tables(topic_rows=None, cog_rows=None):
    return {
        **_signal_tables(cog_rows or []),
        "user_math_performance": topic_rows or [],
    }


def test_strategy_basis_aggregates_instead_of_reading_signal_rows(monkeypatch, set_flag):
    """Six numbers come from one aggregate, not thousands of rows; asserted on the queries."""
    fake = _FakeSupabase(
        {**TABLES, **_strategy_tables()},
        rpc_results={"student_signal_summary": [{
            "focus": 0.7, "stress": 0.3, "engagement": 0.5,
            "face_attention": 0.8, "sessions": 137,
            "cognitive_samples": 900, "face_samples": 400,
        }]},
    )
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "get_user", lambda _r: PARENT)
    set_flag("strategy_llm_enabled", False)
    out = main.student_learning_strategies("student-1", None, main.LearningStrategyRequest())

    assert "cognitive_signals" not in fake.table_calls
    assert "face_signals" not in fake.table_calls
    assert [name for name, _ in fake.rpc_calls] == ["student_signal_summary"]
    assert out["basis"]["averages"]["focus"] == 0.7


def test_strategy_basis_counts_sessions_in_postgres(monkeypatch):
    """The aggregate's count is exact; the report's is capped at _SESSION_ROW_CAP."""
    fake = _FakeSupabase(
        {**TABLES, **_strategy_tables()},
        rpc_results={"student_signal_summary": [{"sessions": 137}]},
    )
    monkeypatch.setattr(main, "supabase", fake)
    basis = main._strategy_basis("student-1", 7, True)
    assert basis["sample_counts"]["sessions"] == 137


def test_strategy_basis_averages_are_an_explicit_contract(monkeypatch):
    """basis.averages is a named list, not a wholesale copy that leaks unrelated keys."""
    fake = _FakeSupabase(
        {**TABLES, **_strategy_tables()},
        rpc_results={"student_signal_summary": [{"focus": 0.7}]},
    )
    monkeypatch.setattr(main, "supabase", fake)
    basis = main._strategy_basis("student-1", 7, True)
    assert set(basis["averages"]) == {"focus", "stress", "engagement", "face_attention"}


def test_strategy_basis_threads_the_opt_out_into_the_aggregate(monkeypatch):
    fake = _FakeSupabase({**TABLES, **_strategy_tables()},
                         rpc_results={"student_signal_summary": []})
    monkeypatch.setattr(main, "supabase", fake)
    main._strategy_basis("student-1", 7, False)
    assert fake.rpc_calls[0][1]["p_include_emotion"] is False
    assert "face_signals" not in fake.table_calls


def test_strategy_basis_does_not_reuse_the_reports_retrieved_key(monkeypatch):
    """A report's `retrieved` is a dict; this is a bool, so it gets its own key."""
    fake = _FakeSupabase({**TABLES, **_strategy_tables()},
                         rpc_results={"student_signal_summary": [{"focus": 0.7}]})
    monkeypatch.setattr(main, "supabase", fake)
    basis = main._strategy_basis("student-1", 7, True)
    assert basis["signals_retrieved"] is True
    assert "retrieved" not in basis
    report = main._weekly_signal_report("student-1", 7)
    assert isinstance(report["retrieved"], dict)


def test_learning_strategies_reports_a_bool_for_signals_retrieved(monkeypatch, set_flag):
    """A bool on the success path too: the frontend tests it with `=== false`."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        {"user_math_performance": []},
        rpc_results={"student_signal_summary": [{"focus": 0.7, "sessions": 3}]}))
    monkeypatch.setattr(main, "get_user", lambda r: {"id": "student-1"})
    set_flag("strategy_llm_enabled", False)

    out = main.student_learning_strategies(
        "student-1", None, main.LearningStrategyRequest())
    assert out["basis"]["signals_retrieved"] is True


def test_learning_strategies_rejects_a_viewer_with_no_relationship(monkeypatch):
    monkeypatch.setattr(main, "supabase", _FakeSupabase({**TABLES, **_strategy_tables()}))
    monkeypatch.setattr(main, "get_user", lambda _r: STRANGER)
    with pytest.raises(main.HTTPException) as exc:
        main.student_learning_strategies("student-1", None, main.LearningStrategyRequest())
    assert exc.value.status_code == 403


def test_learning_strategies_allows_a_linked_parent(monkeypatch, set_flag):
    """strategy_llm_enabled is pinned off: this is about the gate and rule-based shape."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase({**TABLES, **_strategy_tables()}))
    monkeypatch.setattr(main, "get_user", lambda _r: PARENT)
    set_flag("strategy_llm_enabled", False)
    out = main.student_learning_strategies("student-1", None, main.LearningStrategyRequest())
    assert out["student_id"] == "student-1"
    # Four with no signal data; the cap is five, not a quota.
    assert len(out["strategies"]) == 4
    assert out["source"] == "rule-based"


def test_weakest_topic_ignores_topics_with_no_attempts():
    """An unattempted topic reports 0% and would always "win"."""
    topics = [
        {"topic_name": "algebra", "attempted_questions": 0, "accuracy": 0},
        {"topic_name": "geometry", "attempted_questions": 10, "accuracy": 40},
        {"topic_name": "mean", "attempted_questions": 5, "accuracy": 80},
    ]
    assert main._weakest_topic(topics)["topic_name"] == "geometry"
    assert main._weakest_topic([]) is None
    assert main._weakest_topic([{"topic_name": "x", "attempted_questions": 0, "accuracy": 0}]) is None


def test_weakest_topic_summary_carries_only_the_named_fields():
    """Returning the row whole would make its other fields part of the contract."""
    topics = [{
        "topic_id": 3, "topic_name": "geometry", "attempted_questions": 10,
        "correct_questions": 4, "accuracy": 40, "stress": 0.8,
        "updated_at": "2026-07-30T00:00:00Z",
    }]
    assert main._weakest_topic_summary(topics) == {
        "topic_name": "geometry", "accuracy": 40, "attempted_questions": 10,
    }
    assert main._weakest_topic_summary([]) is None


def test_rule_based_strategies_react_to_elevated_stress():
    high = main._rule_based_strategies({"averages": {"stress": 0.8, "focus": 0.7}}, [])
    calm = main._rule_based_strategies({"averages": {"stress": 0.2, "focus": 0.7}}, [])
    assert any("shorter blocks" in s for s in high)
    assert not any("shorter blocks" in s for s in calm)


def test_no_strategy_is_derived_from_face_attention():
    """`attention` has no producer; asserted with a populated value so a restored rule fails."""
    averages = {"face_attention": 0.2, "focus": 0.7, "stress": 0.3}
    for included in (True, False):
        out = main._rule_based_strategies(
            {"averages": averages, "face_included": included}, [])
        assert not any("attention" in s.lower() for s in out)


def test_the_model_is_never_told_about_attention():
    """The prompt half of the same rule."""
    report = {
        "days": 7, "face_included": True,
        "averages": {"focus": 0.7, "stress": 0.3, "engagement": 0.5,
                     "face_attention": 0.83},
        "sample_counts": {"sessions": 3},
    }
    prompt = main._strategy_prompt(report, [], ["baseline"])

    assert "attention" not in prompt.lower()
    assert "83%" not in prompt, "the attention average reached the model"
    assert "70%" in prompt and "30%" in prompt


def test_strategy_prompt_carries_no_identifying_data():
    """The model needs the shape of the week, not the student id or raw rows."""
    report = {
        "student_id": "student-1", "days": 7, "face_included": True,
        "averages": {"focus": 0.7, "stress": 0.3, "engagement": 0.5, "face_attention": 0.8},
        "sample_counts": {"sessions": 3},
        "latest": {"cognitive": {"focus": 0.7, "session_id": "session-9"}},
    }
    prompt = main._strategy_prompt(report, [], ["baseline"])
    assert "student-1" not in prompt
    assert "session-9" not in prompt
    assert "70%" in prompt


# Three valid items, so a rejection below is the clinical filter, not the
# "fewer than three items" check.
_THREE_SAFE = (
    "1. Review fractions for ten minutes\n"
    "2. Take a short break between sets\n"
    "3. Ask which problem felt hardest"
)


@pytest.mark.parametrize("bad", [
    "4. This suggests your child may have dyslexia",
    "4. Ask about symptoms of their learning disorder",
    "4. Consider whether ADHD medication would help",
    "4. Speak to a therapist about the results",
    "4. This may point to a learning disability",
    "4. Ask the school counsellor to take a look",
    "4. A bit of psychology explains the pattern",
    "4. These are classic signs of anxiety",
    "4. Look into special educational needs support",
])
def test_validated_strategies_rejects_clinical_language(bad):
    assert main._validated_strategies(f"{_THREE_SAFE}\n{bad}") is None


@pytest.mark.parametrize("ok", [
    # Over-blocking rejects the whole reply, indistinguishable from an unsafe model.
    "4. Be patient when they get stuck on a question",
    "4. Working through it slowly and patiently helps more than speed",
    "4. Practising patience with word problems pays off later",
])
def test_validated_strategies_allows_ordinary_uses_of_patience(ok):
    out = main._validated_strategies(f"{_THREE_SAFE}\n{ok}")
    assert out is not None
    assert len(out) == 4


def test_validated_strategies_still_rejects_the_clinical_sense_of_patient():
    """The narrower stem `patients?` still catches the clinical noun."""
    assert main._validated_strategies(
        f"{_THREE_SAFE}\n4. Treat them as a patient rather than a learner") is None
    assert main._validated_strategies(
        f"{_THREE_SAFE}\n4. Other patients show the same pattern") is None


def test_validated_strategies_rejects_clinical_language_outside_the_list():
    """Checked against the whole reply: a model volunteering a diagnosis isn't trusted."""
    assert main._validated_strategies(
        f"Note: these results suggest dyslexia.\n{_THREE_SAFE}") is None


def test_validated_strategies_drops_an_unmarked_preamble():
    """A lead-in line must not become numbered advice."""
    out = main._validated_strategies(
        "Here are five strategies for your child:\n"
        f"{_THREE_SAFE}\n"
        "Hope this helps!"
    )
    assert out == [
        "Review fractions for ten minutes",
        "Take a short break between sets",
        "Ask which problem felt hardest",
    ]


def test_validated_strategies_rejects_prose_with_no_list():
    """No markers means no items, so the rule-based list stands."""
    assert main._validated_strategies(
        "Your child should practise more often.\n"
        "Keep the sessions short.\n"
        "Praise the effort rather than the score."
    ) is None


def test_validated_strategies_rejects_a_reply_that_is_too_short():
    assert main._validated_strategies("1. Just do more practice") is None
    assert main._validated_strategies("") is None


def test_validated_strategies_rejects_an_overlong_line():
    long_line = "1. " + ("practice " * 60)
    assert main._validated_strategies(f"{long_line}\n2. Take a short break between sets\n"
                                      "3. Ask which problem felt hardest") is None


def test_validated_strategies_rejects_list_scaffolding_with_nothing_in_it():
    """Without a length floor, "1. a" passes every other check."""
    assert main._validated_strategies("1. a\n2. b\n3. c") is None
    assert main._validated_strategies(
        f"{_THREE_SAFE}\n4. Practice more") is None, "one stub rejects the whole reply"


def test_validated_strategies_accepts_ordinary_advice_at_the_floor():
    """The length floor must clear real one-liners, or it disables the model."""
    out = main._validated_strategies(_THREE_SAFE)
    assert out and len(out) == 3


def test_validated_strategies_unwraps_markdown_emphasis():
    """Nothing renders markdown; a bold marker before the number must not read as a bullet."""
    assert main._validated_strategies(
        "1. **Review fractions for ten minutes**\n"
        "**2. Take a short break between sets**\n"
        "3. Ask which _problem felt hardest_ today"
    ) == [
        "Review fractions for ten minutes",
        "Take a short break between sets",
        "Ask which problem felt hardest today",
    ]


def test_validated_strategies_leaves_snake_case_intact():
    """Underscores are only emphasis at a word boundary, or two snake_case names merge."""
    out = main._validated_strategies(
        "1. Review angle_relationships and mean_median for ten minutes each evening\n"
        "2. Alternate practice with a short break between each set of questions\n"
        "3. Ask which _problem felt hardest_ at the end of the session"
    )
    assert out == [
        "Review angle_relationships and mean_median for ten minutes each evening",
        "Alternate practice with a short break between each set of questions",
        # Genuine emphasis, delimiters at a word boundary: still unwrapped.
        "Ask which problem felt hardest at the end of the session",
    ]


def test_validated_strategies_leaves_arithmetic_intact():
    """Asterisks are only emphasis at a word boundary: "7*8 and 9*6" must not become "78 and 96"."""
    out = main._validated_strategies(
        "1. Practise times tables such as 7*8 and 9*6 for five minutes each evening\n"
        "2. Alternate practice with a short break between each set of questions\n"
        "3. Ask which *problem felt hardest* at the end of the session"
    )
    assert out == [
        "Practise times tables such as 7*8 and 9*6 for five minutes each evening",
        "Alternate practice with a short break between each set of questions",
        # Genuine emphasis, delimiters at a word boundary: still unwrapped.
        "Ask which problem felt hardest at the end of the session",
    ]


def test_validated_strategies_still_reads_asterisk_bullets():
    """A leading "* " is a bullet, not emphasis; unwrapping must not eat it."""
    out = main._validated_strategies(
        "* Review fractions for ten minutes\n"
        "* Take a short break between sets\n"
        "* Ask which problem felt hardest"
    )
    assert out == [
        "Review fractions for ten minutes",
        "Take a short break between sets",
        "Ask which problem felt hardest",
    ]


def test_validated_strategies_accepts_and_strips_list_markers():
    out = main._validated_strategies(
        "1. Review fractions for ten minutes\n"
        "2) Take a short break between sets\n"
        "- Ask which problem felt hardest\n"
        "• Keep the study time consistent\n"
        "* Praise the effort, not the score\n"
        "6. A sixth one that must be dropped"
    )
    assert out == [
        "Review fractions for ten minutes",
        "Take a short break between sets",
        "Ask which problem felt hardest",
        "Keep the study time consistent",
        "Praise the effort, not the score",
    ]


def test_learning_strategies_skips_the_model_when_not_enabled(monkeypatch, set_flag):
    """Off, the endpoint answers without opening a socket."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase({**TABLES, **_strategy_tables()}))
    monkeypatch.setattr(main, "get_user", lambda _r: PARENT)
    set_flag("strategy_llm_enabled", False)
    called = []
    monkeypatch.setattr(main, "_llm_strategies", lambda *_a: called.append(1))
    out = main.student_learning_strategies("student-1", None, main.LearningStrategyRequest())
    assert called == []
    assert out["source"] == "rule-based"


def test_learning_strategies_keeps_the_safe_list_when_the_model_is_rejected(monkeypatch, set_flag):
    """Rejected output never reaches a parent, and the response says it was discarded."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase({**TABLES, **_strategy_tables()}))
    monkeypatch.setattr(main, "get_user", lambda _r: PARENT)
    set_flag("strategy_llm_enabled", True)
    monkeypatch.setattr(main, "_llm_strategies", lambda *_a: None)
    out = main.student_learning_strategies("student-1", None, main.LearningStrategyRequest())
    baseline = main._rule_based_strategies(
        main._strategy_basis("student-1", 7, True), [])
    assert out["strategies"] == baseline
    assert out["source"] == "rule-based (model output rejected)"


def test_learning_strategies_uses_validated_model_output(monkeypatch, set_flag):
    monkeypatch.setattr(main, "supabase", _FakeSupabase({**TABLES, **_strategy_tables()}))
    monkeypatch.setattr(main, "get_user", lambda _r: PARENT)
    set_flag("strategy_llm_enabled", True)
    refined = ["Review fractions for ten minutes", "Take a short break between sets"]
    monkeypatch.setattr(main, "_llm_strategies", lambda *_a: refined)
    out = main.student_learning_strategies("student-1", None, main.LearningStrategyRequest())
    assert out["strategies"] == refined
    assert out["source"] == "model-refined"


def _fake_ollama(monkeypatch, generate):
    """Installs a stand-in ollama module exposing a Client with `generate`.

    Pins the provider too: a local `.env` with `LLM_PROVIDER=claude` would
    otherwise bypass this fake and pass vacuously.
    """
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "ollama")
    class _Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            _Client.last_kwargs = kwargs

        def generate(self, **call_kwargs):
            return generate(**call_kwargs)

    module = type(sys)("ollama")
    module.Client = _Client
    monkeypatch.setitem(sys.modules, "ollama", module)
    return _Client


def test_llm_strategies_returns_none_when_ollama_is_unreachable(monkeypatch):
    """A failure reaching the model must fall back, not raise a 500."""
    def _boom(**_k):
        raise ConnectionError("connection refused")
    _fake_ollama(monkeypatch, _boom)
    assert main._llm_strategies("prompt") is None


def test_llm_strategies_bounds_the_call_with_a_timeout(monkeypatch):
    """A server that accepts and then stalls never raises."""
    client = _fake_ollama(monkeypatch, lambda **_k: {"response": _THREE_SAFE})
    monkeypatch.setattr(main, "STRATEGY_LLM_TIMEOUT", 7.5)
    assert main._llm_strategies("prompt") == [
        "Review fractions for ten minutes",
        "Take a short break between sets",
        "Ask which problem felt hardest",
    ]
    # At most the budget, not exactly: the call is charged what's left after queueing.
    assert 7.4 < client.last_kwargs.get("timeout") <= 7.5


def test_llm_call_is_abandoned_once_it_outlives_the_deadline(monkeypatch, set_flag):
    """The client timeout is per read; a server dribbling bytes needs a total deadline too."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase({**TABLES, **_strategy_tables()}))
    monkeypatch.setattr(main, "get_user", lambda _r: PARENT)
    set_flag("strategy_llm_enabled", True)
    monkeypatch.setattr(main, "STRATEGY_LLM_TIMEOUT", 0.05)

    released = threading.Event()

    def _hang(*_a):
        # Bounded so the thread can't outlive the suite.
        released.wait(timeout=10)
        return ["Model output that arrived far too late to be used"]

    monkeypatch.setattr(main, "_llm_strategies", _hang)
    try:
        started = time.monotonic()
        out = main.student_learning_strategies(
            "student-1", None, main.LearningStrategyRequest())
        elapsed = time.monotonic() - started

        assert elapsed < 5, "the caller waited on the stalled model call"
        assert out["source"] == "rule-based (model output rejected)"
        assert not any("too late" in s for s in out["strategies"])
    finally:
        released.set()


def test_abandoned_model_call_is_cancelled_rather_than_left_queued():
    """max_workers bounds the threads, not the queue: abandoned prompts would run on recovery."""
    released = threading.Event()
    started = []

    def _work(prompt, *_a):
        started.append(prompt)
        released.wait(timeout=10)
        return None

    # A dedicated pool, so occupying it can't disturb the shared one.
    pool = ThreadPoolExecutor(max_workers=1)
    original_pool = main._STRATEGY_LLM_POOL
    original_llm = main._llm_strategies
    original_timeout = main.STRATEGY_LLM_TIMEOUT
    main._STRATEGY_LLM_POOL = pool
    main._llm_strategies = _work
    main.STRATEGY_LLM_TIMEOUT = 0.05
    try:
        blocker = pool.submit(main._llm_strategies, "occupying")
        while not started:            # occupy the worker so the next one queues
            time.sleep(0.01)
        assert main._llm_strategies_bounded("queued") is None
        released.set()
        blocker.result(timeout=10)
    finally:
        released.set()
        pool.shutdown(wait=True)
        main._STRATEGY_LLM_POOL = original_pool
        main._llm_strategies = original_llm
        main.STRATEGY_LLM_TIMEOUT = original_timeout

    assert "queued" not in started, \
        "the abandoned prompt still ran once a worker freed up"


def test_a_queued_call_is_charged_the_remaining_budget_not_a_fresh_one():
    """The wait and the work share one deadline, or the pool stays busy for twice the setting."""
    charged = []
    release_blocker = threading.Event()
    blocking = threading.Event()

    def _record(_prompt, timeout=None):
        charged.append(timeout)
        return None

    def _blocker(*_a):
        blocking.set()
        release_blocker.wait(timeout=10)
        return None

    pool = ThreadPoolExecutor(max_workers=1)
    original_pool = main._STRATEGY_LLM_POOL
    original_llm = main._llm_strategies
    original_timeout = main.STRATEGY_LLM_TIMEOUT
    main._STRATEGY_LLM_POOL = pool
    main.STRATEGY_LLM_TIMEOUT = 1.0
    try:
        main._llm_strategies = _blocker
        pool.submit(main._llm_strategies, "occupying")
        assert blocking.wait(timeout=5), "the blocker never reached the worker"

        # Queued for part of the budget, then released.
        main._llm_strategies = _record
        waiter = ThreadPoolExecutor(max_workers=1)
        try:
            result = waiter.submit(main._llm_strategies_bounded, "queued")
            time.sleep(0.4)
            release_blocker.set()
            assert result.result(timeout=10) is None
        finally:
            waiter.shutdown(wait=True)
    finally:
        release_blocker.set()
        pool.shutdown(wait=True)
        main._STRATEGY_LLM_POOL = original_pool
        main._llm_strategies = original_llm
        main.STRATEGY_LLM_TIMEOUT = original_timeout

    assert charged, "the queued call never ran"
    # Queueing time comes out of the budget.
    assert 0 < charged[0] < main.STRATEGY_LLM_TIMEOUT


def test_a_call_that_starts_after_the_deadline_does_not_open_a_socket():
    """cancel() catches queued items; this covers one a worker already picked up."""
    called = []
    original_llm = main._llm_strategies
    original_timeout = main.STRATEGY_LLM_TIMEOUT

    class _LatePool:
        """Runs the work item inline, after the deadline has passed."""
        def submit(self, fn, *a, **kw):
            time.sleep(0.05)                  # outlive STRATEGY_LLM_TIMEOUT
            future = Future()
            future.set_result(fn(*a, **kw))
            return future

    original_pool = main._STRATEGY_LLM_POOL
    main._STRATEGY_LLM_POOL = _LatePool()
    main._llm_strategies = lambda *_a, **_k: called.append(1)
    main.STRATEGY_LLM_TIMEOUT = 0.01
    try:
        assert main._llm_strategies_bounded("prompt") is None
    finally:
        main._STRATEGY_LLM_POOL = original_pool
        main._llm_strategies = original_llm
        main.STRATEGY_LLM_TIMEOUT = original_timeout

    assert called == [], "the model was called for an answer nobody was waiting on"


def test_waiters_on_the_model_are_capped_and_the_excess_falls_back():
    """Each blocked waiter holds a shared anyio threadpool slot; past the cap, skip the model.

    The per-user rate limit doesn't bound distinct callers.
    """
    admitted = threading.Event()
    release = threading.Event()
    calls = []

    def _blocking(prompt, *_a, **_kw):
        calls.append(prompt)
        admitted.set()
        release.wait(timeout=10)
        return None

    pool = ThreadPoolExecutor(max_workers=2)
    original_pool = main._STRATEGY_LLM_POOL
    original_llm = main._llm_strategies
    original_timeout = main.STRATEGY_LLM_TIMEOUT
    original_sem = main._strategy_llm_waiters
    main._STRATEGY_LLM_POOL = pool
    main._llm_strategies = _blocking
    main.STRATEGY_LLM_TIMEOUT = 5.0
    main._strategy_llm_waiters = threading.BoundedSemaphore(1)
    waiter = ThreadPoolExecutor(max_workers=1)
    try:
        held = waiter.submit(main._llm_strategies_bounded, "admitted")
        assert admitted.wait(timeout=5), "the first caller never got in"

        # The cap is full: return at once rather than block.
        started = time.monotonic()
        assert main._llm_strategies_bounded("over-capacity") is None
        elapsed = time.monotonic() - started
        assert elapsed < 1.0, \
            "the over-capacity caller waited instead of falling back at once"

        release.set()
        assert held.result(timeout=10) is None
    finally:
        release.set()
        waiter.shutdown(wait=True)
        pool.shutdown(wait=True)
        main._STRATEGY_LLM_POOL = original_pool
        main._llm_strategies = original_llm
        main.STRATEGY_LLM_TIMEOUT = original_timeout
        main._strategy_llm_waiters = original_sem

    assert calls == ["admitted"], \
        "the over-capacity prompt reached the model instead of falling back"


def test_the_waiter_cap_is_released_so_it_does_not_leak_a_slot():
    """A leaked slot silently disables the model pass; every exit path must release it."""
    original_sem = main._strategy_llm_waiters
    original_pool = main._STRATEGY_LLM_POOL
    original_timeout = main.STRATEGY_LLM_TIMEOUT
    dead = ThreadPoolExecutor(max_workers=1)
    dead.shutdown(wait=True)
    main._strategy_llm_waiters = threading.BoundedSemaphore(1)
    main.STRATEGY_LLM_TIMEOUT = 0.05
    try:
        # The pool-refused path, three times over.
        main._STRATEGY_LLM_POOL = dead
        for _ in range(3):
            assert main._llm_strategies_bounded("prompt") is None
        assert main._strategy_llm_waiters.acquire(blocking=False)
        main._strategy_llm_waiters.release()
    finally:
        main._strategy_llm_waiters = original_sem
        main._STRATEGY_LLM_POOL = original_pool
        main.STRATEGY_LLM_TIMEOUT = original_timeout


def test_the_waiter_cap_has_a_floor_like_the_other_settings(monkeypatch):
    """Zero would admit nobody, silently disabling the model pass."""
    monkeypatch.setenv("STRATEGY_LLM_MAX_WAITERS", "0")
    assert main._env_number("STRATEGY_LLM_MAX_WAITERS", 4, int, minimum=1) == 1
    monkeypatch.setenv("STRATEGY_LLM_MAX_WAITERS", "-3")
    assert main._env_number("STRATEGY_LLM_MAX_WAITERS", 4, int, minimum=1) == 1


def test_pool_refusing_the_work_falls_back_rather_than_raising():
    """submit() raises on a shut-down pool; that must degrade, not 500."""
    pool = ThreadPoolExecutor(max_workers=1)
    pool.shutdown(wait=True)
    original_pool = main._STRATEGY_LLM_POOL
    main._STRATEGY_LLM_POOL = pool
    try:
        assert main._llm_strategies_bounded("prompt") is None
    finally:
        main._STRATEGY_LLM_POOL = original_pool


def test_the_pool_is_not_built_until_a_model_call_needs_it():
    """A deployment without the model pass shouldn't carry an idle executor."""
    original_pool = main._STRATEGY_LLM_POOL
    main._STRATEGY_LLM_POOL = None
    try:
        assert main._STRATEGY_LLM_POOL is None
        pool = main._strategy_pool()
        assert pool is main._STRATEGY_LLM_POOL, "the pool must be the shared one"
        # Built once, or a second executor escapes max_workers and the shutdown hook.
        assert main._strategy_pool() is pool
    finally:
        if main._STRATEGY_LLM_POOL is not None and main._STRATEGY_LLM_POOL is not original_pool:
            main._STRATEGY_LLM_POOL.shutdown(wait=False)
        main._STRATEGY_LLM_POOL = original_pool


def test_shutdown_releases_the_pool_rather_than_leaving_it_behind():
    """The hook clears the global too; a shut-down pool handed out would fail forever."""
    original_pool = main._STRATEGY_LLM_POOL
    main._STRATEGY_LLM_POOL = ThreadPoolExecutor(max_workers=1)
    try:
        main._shutdown_strategy_pool()
        assert main._STRATEGY_LLM_POOL is None
        fresh = main._strategy_pool()
        assert fresh.submit(lambda: 42).result(timeout=5) == 42
        fresh.shutdown(wait=False)
    finally:
        main._STRATEGY_LLM_POOL = original_pool


def test_the_app_runs_the_shutdown_on_the_way_out():
    """The hook must actually be wired to the app's lifespan."""
    assert main.app.router.lifespan_context is main._lifespan

    original_pool = main._STRATEGY_LLM_POOL
    main._STRATEGY_LLM_POOL = ThreadPoolExecutor(max_workers=1)
    try:
        async def _cycle():
            async with main._lifespan(main.app):
                pass          # startup, then straight to shutdown

        asyncio.run(_cycle())
        assert main._STRATEGY_LLM_POOL is None
    finally:
        main._STRATEGY_LLM_POOL = original_pool


# ── numeric configuration ────────────────────────────────────────────────

def test_bad_numeric_env_falls_back_instead_of_killing_the_process(monkeypatch):
    """Read at import, so a typo would take down every endpoint."""
    monkeypatch.setenv("STRATEGY_RATE_LIMIT", "ten")
    assert main._env_number("STRATEGY_RATE_LIMIT", 10, int) == 10


def test_unset_and_empty_numeric_env_use_the_default(monkeypatch):
    monkeypatch.delenv("STRATEGY_RATE_WINDOW", raising=False)
    assert main._env_number("STRATEGY_RATE_WINDOW", 60.0, float) == 60.0
    # Exported-but-empty: float("") raises like float("abc").
    monkeypatch.setenv("STRATEGY_RATE_WINDOW", "   ")
    assert main._env_number("STRATEGY_RATE_WINDOW", 60.0, float) == 60.0


def test_valid_numeric_env_is_still_honoured(monkeypatch):
    monkeypatch.setenv("STRATEGY_LLM_TIMEOUT", "2.5")
    assert main._env_number("STRATEGY_LLM_TIMEOUT", 20.0, float) == 2.5


@pytest.mark.parametrize("raw", ["0", "-1"])
def test_out_of_range_numeric_env_is_clamped_not_honoured(raw, monkeypatch):
    """A number is not automatically a usable setting: these parse, and each disables its feature."""
    monkeypatch.setenv("STRATEGY_RATE_LIMIT", raw)
    assert main._env_number("STRATEGY_RATE_LIMIT", 10, int, minimum=1) == 1


def test_clamping_is_to_the_minimum_not_back_to_the_default(monkeypatch):
    """A small number asked for a small number; the nearest usable value honours that."""
    monkeypatch.setenv("STRATEGY_RATE_WINDOW", "0.25")
    assert main._env_number("STRATEGY_RATE_WINDOW", 60.0, float, minimum=1.0) == 1.0


def test_in_range_values_are_untouched_by_the_floor(monkeypatch):
    monkeypatch.setenv("STRATEGY_LLM_TIMEOUT", "2.5")
    assert main._env_number("STRATEGY_LLM_TIMEOUT", 20.0, float, minimum=1.0) == 2.5
    # No minimum given: no floor.
    monkeypatch.setenv("STRATEGY_RATE_LIMIT", "0")
    assert main._env_number("STRATEGY_RATE_LIMIT", 10, int) == 0


def test_the_shipped_settings_carry_a_floor():
    assert main._STRATEGY_RATE_LIMIT >= 1
    assert main._STRATEGY_RATE_WINDOW >= 1.0


@pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "Infinity", "NaN"])
def test_non_finite_numeric_env_falls_back_to_the_default(raw, monkeypatch):
    """float() accepts these and the floor misses inf/nan; -inf falls back too, not clamped."""
    monkeypatch.setenv("STRATEGY_LLM_TIMEOUT", raw)
    assert main._env_number("STRATEGY_LLM_TIMEOUT", 20.0, float, minimum=1.0) == 20.0


def test_non_finite_rate_window_would_otherwise_turn_a_429_into_a_500():
    """An infinite window makes Retry-After compute int(inf): a permanent 500."""
    with pytest.raises(OverflowError):
        int(float("inf") - 1.0)


def test_non_finite_timeout_would_otherwise_disable_the_model_pass(monkeypatch):
    """A nan wait expires immediately, silently disabling the model pass."""
    from concurrent.futures import Future
    with pytest.raises(TimeoutError):
        Future().result(timeout=float("nan"))


def test_a_finite_value_below_the_floor_still_clamps(monkeypatch):
    """The non-finite check runs before the floor, so it must not shadow it."""
    monkeypatch.setenv("STRATEGY_LLM_TIMEOUT", "0")
    assert main._env_number("STRATEGY_LLM_TIMEOUT", 20.0, float, minimum=1.0) == 1.0
    assert main.STRATEGY_LLM_TIMEOUT >= 1.0


# ── strategy rate limit ──────────────────────────────────────────────────

def _strategies_as(viewer, monkeypatch, set_flag):
    # Model pass pinned off: these are about access and rate limiting.
    set_flag("strategy_llm_enabled", False)
    monkeypatch.setattr(main, "get_user", lambda _r: viewer)
    return main.student_learning_strategies(
        "student-1", None, main.LearningStrategyRequest())


def test_learning_strategies_rate_limits_a_repeating_caller(monkeypatch, set_flag):
    """The heaviest endpoint a click can trigger."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase({**TABLES, **_strategy_tables()}))
    tighten(monkeypatch, main._STRATEGY_LIMITER, limit=3)

    for _ in range(3):
        assert _strategies_as(PARENT, monkeypatch, set_flag)["student_id"] == "student-1"

    with pytest.raises(main.HTTPException) as exc:
        _strategies_as(PARENT, monkeypatch, set_flag)
    assert exc.value.status_code == 429
    assert int(exc.value.headers["Retry-After"]) >= 1


def test_learning_strategies_rate_limit_is_per_caller(monkeypatch, set_flag):
    monkeypatch.setattr(main, "supabase", _FakeSupabase({**TABLES, **_strategy_tables()}))
    tighten(monkeypatch, main._STRATEGY_LIMITER, limit=1)

    _strategies_as(PARENT, monkeypatch, set_flag)
    with pytest.raises(main.HTTPException):
        _strategies_as(PARENT, monkeypatch, set_flag)

    # A teacher of student-1's class, with their own untouched allowance.
    assert _strategies_as(TEACHER, monkeypatch, set_flag)["student_id"] == "student-1"


def test_learning_strategies_checks_access_before_the_rate_limit(monkeypatch, set_flag):
    """403, not 429: the access decision must not depend on how often they asked."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase({**TABLES, **_strategy_tables()}))
    tighten(monkeypatch, main._STRATEGY_LIMITER, limit=1)

    for _ in range(3):
        with pytest.raises(main.HTTPException) as exc:
            _strategies_as(STRANGER, monkeypatch, set_flag)
        assert exc.value.status_code == 403


def _sweep_is_due(monkeypatch):
    """Put the last sweep one interval in the past.

    Explicit, since monotonic() counts from boot and a fresh CI runner may be under a minute.
    """
    monkeypatch.setattr(main._STRATEGY_LIMITER, "sweep_at",
                        time.monotonic() - main._STRATEGY_LIMITER._sweep_every)


def test_rate_limit_sweep_reclaims_callers_whose_window_has_passed(monkeypatch):
    """The dict grows with everyone who's ever used the endpoint."""
    monkeypatch.setattr(main._STRATEGY_LIMITER, "_sweep_above", 2)
    monkeypatch.setattr(main._STRATEGY_LIMITER, "window", 0.0)  # every hit already expired
    _sweep_is_due(monkeypatch)
    for i in range(4):
        main._rate_limit_strategies(f"user-{i}")
    # Only the caller being served survives the sweep.
    assert len(main._STRATEGY_LIMITER.hits) == 1


def test_rate_limit_sweep_does_not_run_on_every_request(monkeypatch):
    """With many active callers, a size-only trigger would rescan under the lock every request."""
    monkeypatch.setattr(main._STRATEGY_LIMITER, "_sweep_above", 2)
    _sweep_is_due(monkeypatch)
    scans = []
    real_items = dict.items
    monkeypatch.setattr(main._STRATEGY_LIMITER, "hits",
                        type("_Counted", (dict,), {
                            "items": lambda self: (scans.append(1), real_items(self))[1],
                        })())
    for i in range(6):
        main._rate_limit_strategies(f"user-{i}")
    assert len(scans) == 1, f"swept {len(scans)} times for 6 requests"


def test_learning_strategies_clamps_the_day_range(monkeypatch, set_flag):
    fake = _FakeSupabase({**TABLES, **_strategy_tables()})
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "get_user", lambda _r: PARENT)
    set_flag("strategy_llm_enabled", False)
    assert main.student_learning_strategies(
        "student-1", None, main.LearningStrategyRequest(days=999))["basis"]["days"] == 30
    assert main.student_learning_strategies(
        "student-1", None, main.LearningStrategyRequest(days=0))["basis"]["days"] == 1
# ── leaderboard ──────────────────────────────────────────────────────────
# Service-role read with names on it; only the code bounds what it returns.

def _leaderboard_tables(n=5):
    # Scores stay three digits: _Query.order sorts by str().
    # `profiles` is real data: names come from one batched read, not `_profile`.
    return {
        "user_stats": [
            {"user_id": f"student-{i}", "total_correct": 900 - i, "total_questions": 900,
             "current_streak": 1, "best_streak": 2}
            for i in range(n)
        ],
        "profiles": [
            {"id": f"student-{i}", "display_name": f"Name {i}", "email": f"s{i}@x.com"}
            for i in range(n)
        ],
    }


def test_leaderboard_clamps_an_oversized_limit(monkeypatch):
    """An unclamped limit would be a directory of the platform, not a top-N board."""
    # More rows than the cap, so a failure to clamp is visible.
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_leaderboard_tables(main._LEADERBOARD_MAX + 50)))
    monkeypatch.setattr(main, "get_user", lambda _r: STUDENT)
    assert len(main.leaderboard(None, limit=999999)) == main._LEADERBOARD_MAX


def test_leaderboard_rejects_a_nonsense_limit(monkeypatch):
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_leaderboard_tables(5)))
    monkeypatch.setattr(main, "get_user", lambda _r: STUDENT)
    assert len(main.leaderboard(None, limit=0)) == 1
    assert len(main.leaderboard(None, limit=-5)) == 1


def test_leaderboard_never_returns_user_ids(monkeypatch):
    """user_id beside display_name would be a UUID -> name map for every caller."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_leaderboard_tables(3)))
    monkeypatch.setattr(main, "get_user", lambda _r: STUDENT)
    rows = main.leaderboard(None)
    assert rows, "fixture must produce rows"
    for row in rows:
        assert "user_id" not in row


def test_leaderboard_names_the_board_in_one_read(monkeypatch):
    """No display-name lookup per row inside a loop."""
    fake = _FakeSupabase(_leaderboard_tables(30))
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "get_user", lambda _r: STUDENT)

    rows = main.leaderboard(None, limit=30)

    assert fake.table_calls.count("profiles") == 1, (
        f"one read for the board, got {fake.table_calls.count('profiles')}")
    # Names still land, so the above can't pass by not reading profiles.
    assert rows[0]["display_name"] == "Name 0"


def test_leaderboard_still_names_a_student_with_no_profile_row(monkeypatch):
    """The placeholder name is resolved per student, not for the whole call."""
    tables = _leaderboard_tables(3)
    tables["profiles"] = [r for r in tables["profiles"] if r["id"] != "student-1"]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(tables))
    monkeypatch.setattr(main, "get_user", lambda _r: STUDENT)

    rows = main.leaderboard(None)

    assert [r["display_name"] for r in rows] == ["Name 0", "Student", "Name 2"]


def test_leaderboard_marks_the_callers_own_row(monkeypatch):
    """The server marks the viewer's row, so the page needs no ids."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_leaderboard_tables(3)))
    monkeypatch.setattr(main, "get_user", lambda _r: STUDENT)   # student-1
    rows = main.leaderboard(None)
    mine = [r for r in rows if r["is_me"]]
    assert len(mine) == 1
    assert mine[0]["rank"] == 2   # student-1 is second by total_correct


# ── the student-scoped question list ─────────────────────────────────────
# The bank is public; which questions a named child was asked is student data.

def _student_question_rows():
    return {**TABLES, "session_answers": [
        {"question_id": "q-1", "session_id": "session-1", "user_id": "student-1",
         "correct": True,  "answered_at": "2026-09-03T10:00:00Z",
         "questions": {"question_text": "2+2?", "subject": "algebra", "difficulty": "easy"}},
        {"question_id": "q-1", "session_id": "session-0", "user_id": "student-1",
         "correct": False, "answered_at": "2026-09-01T10:00:00Z",
         "questions": {"question_text": "2+2?", "subject": "algebra", "difficulty": "easy"}},
        {"question_id": "q-2", "session_id": "session-1", "user_id": "student-1",
         "correct": False, "answered_at": "2026-09-02T10:00:00Z",
         "questions": {"question_text": "3+3?", "subject": "algebra", "difficulty": "easy"}},
    ]}


def test_student_questions_rejects_a_viewer_with_no_relationship(monkeypatch):
    """The whole reason this is not a filter on the open /api/questions."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_student_question_rows()))
    monkeypatch.setattr(main, "get_user", lambda _r: STRANGER)
    with pytest.raises(main.HTTPException) as exc:
        main.student_questions("student-1", None)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("viewer", [PARENT, TEACHER, STUDENT],
                         ids=["linked parent", "teacher of their class", "the student"])
def test_student_questions_allows_everyone_with_a_relationship(viewer, monkeypatch):
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_student_question_rows()))
    monkeypatch.setattr(main, "get_user", lambda _r: viewer)
    assert main.student_questions("student-1", None)["student_id"] == "student-1"


def test_student_questions_collapses_repeat_attempts(monkeypatch):
    """One row per question, linked to the newest attempt's session."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_student_question_rows()))
    monkeypatch.setattr(main, "get_user", lambda _r: TEACHER)
    out = main.student_questions("student-1", None)
    by_id = {q["question_id"]: q for q in out["questions"]}
    assert len(out["questions"]) == 2
    assert by_id["q-1"]["attempts"] == 2
    assert by_id["q-1"]["correct"] == 1
    assert by_id["q-1"]["session_id"] == "session-1", "should link to the newest attempt"
    assert by_id["q-2"]["attempts"] == 1


def test_student_questions_reports_expired_questions_separately(monkeypatch):
    """An expired question arrives as `questions: null`; counting it separates "aged out" from "answered nothing"."""
    rows = {**TABLES, "session_answers": [
        {"question_id": "q-9", "session_id": "session-1", "user_id": "student-1",
         "correct": True, "answered_at": "2026-09-03T10:00:00Z", "questions": None},
    ]}
    monkeypatch.setattr(main, "supabase", _FakeSupabase(rows))
    monkeypatch.setattr(main, "get_user", lambda _r: TEACHER)
    out = main.student_questions("student-1", None)
    assert out["questions"] == []
    assert out["answers_read"] == 1
    assert out["expired_questions"] == 1


def test_student_questions_bounds_the_limit(monkeypatch):
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_student_question_rows()))
    monkeypatch.setattr(main, "get_user", lambda _r: TEACHER)
    assert main.student_questions("student-1", None, limit=9999)["truncated"] is False
    assert main._STUDENT_QUESTIONS_MAX == 200
