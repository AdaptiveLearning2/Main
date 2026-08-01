"""Access-control tests for the student/class data endpoints.

These endpoints all read through the service-role Supabase client, which
bypasses RLS -- so the checks in main.py are the only thing standing between a
caller and another student's data. Six of them shipped with no ownership check
at all, which is what these tests exist to prevent recurring.
"""
import os
import sys

# main.py builds a Supabase client at import time and raises without these.
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import main  # noqa: E402


class _Result:
    def __init__(self, data, count=None):
        self.data = data
        # PostgREST returns the total matching row count when the caller asks
        # for count="exact", independently of how many rows the limit let
        # through. _weekly_signal_report relies on that to notice truncation.
        self.count = count


class _Query:
    """Minimal stand-in for the supabase-py query builder chain."""

    def __init__(self, rows, max_rows=None):
        self._rows = rows
        self._max_rows = max_rows
        self._filters = {}
        self._limit = None
        self._order = None
        self._desc = False
        self._count = None

    def select(self, *_a, **kw):
        self._count = kw.get("count")
        return self

    def order(self, col, desc=False, **_k):
        self._order, self._desc = col, desc
        return self

    def limit(self, n, *_a, **_k):
        self._limit = n
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def gte(self, col, val):
        self._filters[col] = ("gte", val)
        return self

    def in_(self, col, vals):
        self._filters[col] = ("in", list(vals))
        return self

    def _matches(self, row):
        for col, want in self._filters.items():
            have = row.get(col)
            if isinstance(want, tuple) and want[0] == "in":
                if have not in want[1]:
                    return False
            elif isinstance(want, tuple) and want[0] == "gte":
                if have is None or str(have) < str(want[1]):
                    return False
            elif have != want:
                return False
        return True

    def execute(self):
        rows = [r for r in self._rows if self._matches(r)]
        if self._order:
            rows = sorted(rows, key=lambda r: str(r.get(self._order, "")), reverse=self._desc)
        total = len(rows)
        # Whichever ceiling binds first. _max_rows mirrors PostgREST's
        # db-max-rows: a server-side cap the caller's .limit() cannot raise,
        # and the reason a "len(rows) >= our limit" truncation check is
        # unreliable.
        ceilings = [n for n in (self._limit, self._max_rows) if n is not None]
        if ceilings:
            rows = rows[:min(ceilings)]
        return _Result(rows, count=total if self._count == "exact" else None)

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
    def __init__(self, tables, max_rows=None, rpc_results=None):
        self._tables = tables
        # int applies to every table; dict is per table, which is what the
        # mixed-truncation case needs (the row cap is per table in reality).
        self._max_rows = max_rows
        self._rpc_results = rpc_results or {}
        self.rpc_calls = []

    def table(self, name):
        cap = self._max_rows.get(name) if isinstance(self._max_rows, dict) else self._max_rows
        return _Query(self._tables.get(name, []), max_rows=cap)

    def rpc(self, name, params=None):
        self.rpc_calls.append((name, params or {}))
        return _Rpc(self._rpc_results.get(name, []))


class _Rpc:
    def __init__(self, data):
        self._data = data

    def execute(self):
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
    # teacher-2 owns class-2, which student-1 is not enrolled in.
    assert main._can_view_student(OTHER_TEACHER, "student-1") is False


def test_linked_parent_can_view_their_child():
    assert main._can_view_student(PARENT, "student-1") is True


def test_parent_cannot_view_an_unlinked_child():
    assert main._can_view_student(PARENT, "student-2") is False


def test_unrelated_student_cannot_view_another_student():
    # The core bug: any authenticated account could read anyone's data.
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
    # Regression guard: the original check was
    #   `owner != user AND role != "teacher"`
    # which let ANY teacher through for ANY class.
    with pytest.raises(main.HTTPException) as exc:
        main._verify_class_owner("class-1", "teacher-2")
    assert exc.value.status_code == 403


def test_student_is_rejected_from_class_roster():
    with pytest.raises(main.HTTPException) as exc:
        main._verify_class_owner("class-1", "student-1")
    assert exc.value.status_code == 403


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


def _signal_tables(cog_rows, face_rows=None):
    return {
        "cognitive_signals": cog_rows,
        "face_signals": face_rows if face_rows is not None else [],
        "sessions": [],
    }


def test_weekly_report_summary_renders_ratios_as_percentages(monkeypatch):
    """Signals are stored as 0..1 ratios. Interpolating them into a "%"
    sentence produced "average focus was 0.72%"."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_signal_tables([
        {"user_id": "student-1", "ts": _ts(1), "focus": 0.70, "stress": 0.30, "engagement": 0.6},
        {"user_id": "student-1", "ts": _ts(2), "focus": 0.74, "stress": 0.32, "engagement": 0.6},
    ])))
    report = main._weekly_signal_report("student-1")
    assert report["averages"]["focus"] == 0.72     # still a ratio on the wire
    assert "average focus was 72%" in report["summary"]
    assert "0.72%" not in report["summary"]


def test_weekly_report_flags_days_it_could_not_retrieve(monkeypatch):
    """The row cap is per table, so cognitive can be truncated while face is
    not. Judging coverage from a single oldest-timestamp across both meant the
    older face rows held the cutoff back: no days were skipped, and the trimmed
    cognitive days came back as None -- shown to a parent as "no activity"
    rather than "not retrieved"."""
    # Both tables hold the full week, but only cognitive gets capped -- so it
    # reaches back 3 days while face still covers all 7.
    cog = [{"user_id": "student-1", "ts": _ts(d), "focus": 0.5, "stress": 0.4, "engagement": 0.5}
           for d in range(0, 7)]
    face = [{"user_id": "student-1", "ts": _ts(d), "attention": 0.8} for d in range(0, 7)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _signal_tables(cog, face), max_rows={"cognitive_signals": 3}))
    report = main._weekly_signal_report("student-1")

    assert report["truncated"] is True
    days = {d["date"]: d for d in report["daily"]}
    # Days beyond cognitive's reach are still reported (face has them) but are
    # explicitly marked as not retrieved rather than silently null.
    unretrieved = [d for d in report["daily"] if not d["cognitive_retrieved"]]
    assert unretrieved, "older days must be flagged, not silently nulled"
    for d in unretrieved:
        assert d["focus"] is None
        assert d["face_retrieved"] is True   # face data for that day is real
        assert d["attention"] is not None
    # The days cognitive does cover are not flagged.
    covered = days[_ts(0)[:10]]
    assert covered["cognitive_retrieved"] is True


def test_weekly_report_detects_truncation_from_count_not_row_length(monkeypatch):
    """PostgREST's db-max-rows can cap below _REPORT_ROW_CAP. A
    len(rows) >= _REPORT_ROW_CAP check never fires then, so data is trimmed
    with truncated=False and the guard is silently disabled."""
    cog = [{"user_id": "student-1", "ts": _ts(d % 7), "focus": 0.5} for d in range(50)]
    monkeypatch.setattr(main, "supabase",
                        _FakeSupabase(_signal_tables(cog), max_rows=10))
    report = main._weekly_signal_report("student-1")
    assert len(cog) < main._REPORT_ROW_CAP, "fixture must stay under our own cap"
    assert report["truncated"] is True
    assert report["sample_counts"]["cognitive"] == 10


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
    assert out == main._EMPTY_SUMMARY
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


def test_missing_class_returns_404_not_500():
    # .single() raises on zero rows, so without handling this surfaced as a 500.
    with pytest.raises(main.HTTPException) as exc:
        main._verify_class_owner("class-does-not-exist", "teacher-1")
    assert exc.value.status_code == 404


# ── leaderboard ──────────────────────────────────────────────────────────
#
# This endpoint reads through the service-role client, so RLS does not apply
# and nothing but the code below bounds what it hands back -- with names on it.

def _leaderboard_tables(n=5):
    # Scores stay three digits across the range used here: _Query.order sorts
    # by str(), so mixed-width numbers would rank 99 above 100 and the ordering
    # assertions below would be testing the fake rather than the endpoint.
    return {"user_stats": [
        {"user_id": f"student-{i}", "total_correct": 900 - i, "total_questions": 900,
         "current_streak": 1, "best_streak": 2}
        for i in range(n)
    ]}


def test_leaderboard_clamps_an_oversized_limit(monkeypatch):
    """?limit=999999 returned every user and their display_name to any signed-in
    caller -- a full directory of the platform, not a top-N board."""
    # More rows available than the cap, so a failure to clamp is visible.
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_leaderboard_tables(main._LEADERBOARD_MAX + 50)))
    monkeypatch.setattr(main, "get_user", lambda _r: STUDENT)
    monkeypatch.setattr(main, "_profile", lambda _uid: {"display_name": "Someone"})
    assert len(main.leaderboard(None, limit=999999)) == main._LEADERBOARD_MAX


def test_leaderboard_rejects_a_nonsense_limit(monkeypatch):
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_leaderboard_tables(5)))
    monkeypatch.setattr(main, "get_user", lambda _r: STUDENT)
    monkeypatch.setattr(main, "_profile", lambda _uid: {"display_name": "Someone"})
    assert len(main.leaderboard(None, limit=0)) == 1
    assert len(main.leaderboard(None, limit=-5)) == 1


def test_leaderboard_never_returns_user_ids(monkeypatch):
    """Returning user_id next to display_name handed every caller a
    UUID -> name map for the whole board, which is what made the open read on
    user_stats attributable to named students."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_leaderboard_tables(3)))
    monkeypatch.setattr(main, "get_user", lambda _r: STUDENT)
    monkeypatch.setattr(main, "_profile", lambda _uid: {"display_name": "Someone"})
    rows = main.leaderboard(None)
    assert rows, "fixture must produce rows"
    for row in rows:
        assert "user_id" not in row


def test_leaderboard_marks_the_callers_own_row(monkeypatch):
    """The page needs to know which row is the viewer's; that is the only
    reason it ever needed an id, so the server answers it directly."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_leaderboard_tables(3)))
    monkeypatch.setattr(main, "get_user", lambda _r: STUDENT)   # student-1
    monkeypatch.setattr(main, "_profile", lambda _uid: {"display_name": "Someone"})
    rows = main.leaderboard(None)
    mine = [r for r in rows if r["is_me"]]
    assert len(mine) == 1
    assert mine[0]["rank"] == 2   # student-1 is second by total_correct
