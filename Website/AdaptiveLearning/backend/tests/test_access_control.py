"""Access-control tests for the student/class data endpoints.

These endpoints all read through the service-role Supabase client, which
bypasses RLS -- so the checks in main.py are the only thing standing between a
caller and another student's data. Six of them shipped with no ownership check
at all, which is what these tests exist to prevent recurring.
"""
import inspect
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

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
    def __init__(self, tables, max_rows=None, rpc_results=None, rpc_raises=None):
        self._tables = tables
        # int applies to every table; dict is per table, which is what the
        # mixed-truncation case needs (the row cap is per table in reality).
        self._max_rows = max_rows
        self._rpc_results = rpc_results or {}
        # (name, params) -> Exception or None. Lets a test model a database
        # that has the function but not the signature being called, which is
        # what a deploy ahead of its migration actually looks like.
        self._rpc_raises = rpc_raises
        self.rpc_calls = []
        # Which tables were reached for, in order. Lets a test assert a query
        # was *not* made -- an empty result cannot distinguish "asked and got
        # nothing" from "never asked", which is exactly the difference the
        # facial-recognition opt-out has to make.
        self.table_calls = []

    def table(self, name):
        self.table_calls.append(name)
        cap = self._max_rows.get(name) if isinstance(self._max_rows, dict) else self._max_rows
        return _Query(self._tables.get(name, []), max_rows=cap)

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
        # supabase-py surfaces PostgREST errors by raising from execute().
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


@pytest.fixture(autouse=True)
def reset_strategy_rate_limit():
    """The limiter counts in module-level state, which outlives a test.

    Without this the strategy tests share one allowance and start failing on
    whichever of them happens to run eleventh.
    """
    main._strategy_hits.clear()
    main._strategy_sweep_at = 0.0
    yield
    main._strategy_hits.clear()
    main._strategy_sweep_at = 0.0


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


def _signal_tables(cog_rows, face_rows=None, session_rows=None):
    return {
        "cognitive_signals": cog_rows,
        "face_signals": face_rows if face_rows is not None else [],
        "sessions": session_rows or [],
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


def test_weekly_report_reports_session_truncation(monkeypatch):
    """sample_counts.sessions is rendered as the report's Sessions figure.

    Its truncation flag was discarded, so a student over the cap was shown a
    count that had silently stopped at it -- and with the signal tables under
    their own caps, `truncated` stayed False and nothing said otherwise.
    """
    sessions = [{"id": f"s{i}", "user_id": "student-1", "started_at": _ts(i % 7)}
                for i in range(40)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _signal_tables([], session_rows=sessions), max_rows={"sessions": 10}))
    report = main._weekly_signal_report("student-1")

    assert report["sample_counts"]["sessions"] == 10
    assert report["truncated"] is True


def test_weekly_report_keeps_a_day_whose_sessions_survived_the_cap(monkeypatch):
    """Sessions come from their own query under their own cap, so a day whose
    signal rows were trimmed can still have a session count retrieved intact.

    Dropping the day threw that away and reported the day as absent rather
    than partial.
    """
    cog = [{"user_id": "student-1", "ts": _ts(d), "focus": 0.5} for d in range(0, 7)]
    sessions = [{"id": f"s{d}", "user_id": "student-1", "started_at": _ts(d)}
                for d in range(0, 7)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _signal_tables(cog, session_rows=sessions), max_rows={"cognitive_signals": 3}))
    # Face reporting off, so the only thing that could have kept these days
    # before was the face query -- which is not running.
    report = main._weekly_signal_report("student-1", include_face=False)

    trimmed = [d for d in report["daily"] if d["cognitive_retrieved"] is False]
    assert trimmed, "days beyond cognitive's reach must still be reported"
    for d in trimmed:
        assert d["focus"] is None            # the signal genuinely was not read
        assert d["sessions_retrieved"] is True
        assert d["sessions"] == 1            # ...but the session count was


def test_weekly_report_nulls_a_day_whose_sessions_were_cut(monkeypatch):
    """A day the cap kept us from reading did not have zero sessions. Same
    distinction the signal metrics draw, so it gets the same shape."""
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
#
# The cap trims oldest-first, so the oldest day that came back is the day it
# cut through: part of it is here, the rest is not. That day used to be
# reported as retrieved, carrying a figure computed from whatever fraction
# survived -- indistinguishable from an exact one.

def test_weekly_report_withholds_the_day_the_cap_cut_into(monkeypatch):
    """Three readings a day for three days, and a cap of four.

    That keeps all of day 0 and one of day 1's three, so day 1 is the day the
    cap cut through. Averaging its single surviving reading and calling the
    result day 1's focus reads as a measurement of the whole day.
    """
    cog = [{"user_id": "student-1", "ts": _ts(d, hour=h),
            "focus": 0.5, "stress": 0.4, "engagement": 0.6}
           for d in range(0, 3) for h in (9, 12, 15)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _signal_tables(cog), max_rows={"cognitive_signals": 4}))
    report = main._weekly_signal_report("student-1", include_face=False)

    assert report["sample_counts"]["cognitive"] == 4, "fixture must actually be cut"
    days = {d["date"]: d for d in report["daily"]}
    whole, boundary, beyond = _ts(0)[:10], _ts(1)[:10], _ts(2)[:10]

    # The day entirely inside the cap is untouched.
    assert days[whole]["cognitive_retrieved"] is True
    assert days[whole]["focus"] == 0.5

    # The day it cut through is withheld -- but kept in the series, because
    # something was read for it.
    assert boundary in days, "a partly-read day must not be dropped as absent"
    assert days[boundary]["cognitive_retrieved"] is False
    assert days[boundary]["focus"] is None
    assert days[boundary]["stress"] is None
    assert days[boundary]["engagement"] is None

    # And the day it never reached is flagged the same way.
    assert days[beyond]["cognitive_retrieved"] is False
    assert days[beyond]["focus"] is None


def test_weekly_report_withholds_a_session_count_the_cap_cut_into(monkeypatch):
    """The clearest case for withholding a partial day.

    An average over a fraction of a day is at least a biased estimate of it. A
    count over a fraction of a day is simply wrong -- one third of the rows
    gives exactly one third of the sessions, with nothing to say it is a third.
    """
    sessions = [{"id": f"s{d}-{h}", "user_id": "student-1", "started_at": _ts(d, hour=h)}
                for d in range(0, 3) for h in (9, 12, 15)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _signal_tables([], session_rows=sessions), max_rows={"sessions": 4}))
    report = main._weekly_signal_report("student-1", include_face=False)

    days = {d["date"]: d for d in report["daily"]}
    whole, boundary = _ts(0)[:10], _ts(1)[:10]

    assert days[whole]["sessions_retrieved"] is True
    assert days[whole]["sessions"] == 3
    # Not 1, which is what a third of the day's rows counts to.
    assert days[boundary]["sessions_retrieved"] is False
    assert days[boundary]["sessions"] is None


def test_a_cap_landing_on_a_day_boundary_understates_rather_than_overstates(monkeypatch):
    """One reading a day and a cap of three: the cut falls exactly between two
    days, so the oldest day retrieved really is complete.

    Nothing available here can tell that apart from a day cut through the
    middle -- both leave an oldest retrieved row and some trimmed rows older
    than it, and distinguishing them means another query. So it resolves the
    conservative way, and this pins which way that is: a complete day reported
    as partial, never a partial day reported as complete. Deliberate, not an
    off-by-one to tidy up.
    """
    cog = [{"user_id": "student-1", "ts": _ts(d), "focus": 0.5} for d in range(0, 5)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _signal_tables(cog), max_rows={"cognitive_signals": 3}))
    report = main._weekly_signal_report("student-1", include_face=False)

    days = {d["date"]: d for d in report["daily"]}
    assert days[_ts(0)[:10]]["cognitive_retrieved"] is True
    assert days[_ts(2)[:10]]["cognitive_retrieved"] is False
    assert days[_ts(2)[:10]]["focus"] is None


# ── database behind the build ────────────────────────────────────────────

_PGRST202 = "PGRST202: Could not find the function public.student_signal_summary" \
            "(p_days, p_include_face, p_student_id) in the schema cache"


def _rejects_include_face(_name, params):
    """A database that has these functions but not the p_include_face form."""
    return RuntimeError(_PGRST202) if "p_include_face" in params else None


def test_weekly_report_counts_every_session_not_just_the_retrieved_rows(monkeypatch):
    """sample_counts is rows-retrieved throughout, and rendering its sessions
    figure as the report headline showed a heavy week as exactly the cap --
    while the parent dashboard, counting the same week in Postgres, showed the
    real number. sessions_recorded is the count the report is actually about.
    """
    sessions = [{"id": f"s{i}", "user_id": "student-1", "started_at": _ts(1, hour=i % 24)}
                for i in range(137)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_signal_tables([], [], sessions)))
    report = main._weekly_signal_report("student-1")
    assert report["sample_counts"]["sessions"] == main._SESSION_ROW_CAP   # rows we hold
    assert report["sessions_recorded"] == 137                             # sessions there were
    assert report["truncated"] is True


def test_weekly_report_session_count_falls_back_when_none_is_reported(monkeypatch):
    """A client or server that reports no exact count leaves the row count as
    the only figure available -- the same fallback the truncation check makes,
    rather than a null where a number belongs."""
    class _NoCountQuery(_Query):
        def select(self, *a, **kw):
            kw.pop("count", None)   # a server that answers without one
            return super().select(*a, **kw)

    class _NoCount(_FakeSupabase):
        def table(self, name):
            self.table_calls.append(name)
            return _NoCountQuery(self._tables.get(name, []))

    monkeypatch.setattr(main, "supabase", _NoCount(_signal_tables(
        [], [], [{"id": "s1", "user_id": "student-1", "started_at": _ts(1)}])))
    report = main._weekly_signal_report("student-1")
    assert report["sessions_recorded"] == 1


def test_signal_summary_survives_a_database_without_the_flag(monkeypatch):
    """p_include_face arrived with its own migration. Between deploying this
    code and applying it, every call carries an argument the database has no
    signature for -- and an unhandled one blanks the dashboard silently for the
    length of the window rather than erroring."""
    fake = _FakeSupabase({}, rpc_results={
        "student_signal_summary": [{"focus": 0.6, "stress": 0.3, "engagement": 0.5,
                                    "face_attention": 0.8, "sessions": 2,
                                    "cognitive_samples": 9, "face_samples": 4}],
    }, rpc_raises=_rejects_include_face)
    monkeypatch.setattr(main, "supabase", fake)

    out = main._signal_summary("student-1")
    assert out["focus"] == 0.6, "the retry must actually produce the summary"
    assert out["face_included"] is True
    # Tried the new signature first; only then fell back.
    assert "p_include_face" in fake.rpc_calls[0][1]
    assert "p_include_face" not in fake.rpc_calls[1][1]


def test_signal_summaries_survive_a_database_without_the_flag(monkeypatch):
    fake = _FakeSupabase({}, rpc_results={
        "student_signal_summary_many": [{"student_id": "student-1", "focus": 0.4,
                                         "sessions": 1, "cognitive_samples": 3}],
    }, rpc_raises=_rejects_include_face)
    monkeypatch.setattr(main, "supabase", fake)

    out = main._signal_summaries(["student-1"])
    assert out["student-1"]["focus"] == 0.4


def test_no_fallback_when_the_opt_out_is_on(monkeypatch):
    """The old signature has no way to be told to skip facial rows, so retrying
    against it would read exactly what the caller asked us not to. A blank tile
    is the right outcome; the UI already renders it as "Off"."""
    fake = _FakeSupabase({}, rpc_results={
        "student_signal_summary": [{"focus": 0.6, "face_attention": 0.8,
                                    "face_samples": 4}],
    }, rpc_raises=_rejects_include_face)
    monkeypatch.setattr(main, "supabase", fake)

    out = main._signal_summary("student-1", include_face=False)
    assert out["face_attention"] is None
    assert out["focus"] is None
    assert out["face_included"] is False
    assert len(fake.rpc_calls) == 1, "a retry here would read the facial rows"


def test_a_broken_rpc_is_not_retried(monkeypatch):
    """Only a missing *signature* justifies the fallback. Retrying a function
    that exists and failed would double every error's cost."""
    fake = _FakeSupabase({}, rpc_raises=lambda *_a: RuntimeError("57014: statement timeout"))
    monkeypatch.setattr(main, "supabase", fake)

    out = main._signal_summary("student-1")
    # dominant_emotion is part of the single-student shape on every path,
    # including the ones that never reached a row.
    assert out == {**main._EMPTY_SUMMARY, "face_included": True, "dominant_emotion": None}
    assert len(fake.rpc_calls) == 1


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
    assert out == {**main._EMPTY_SUMMARY, "dominant_emotion": None}
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


# ── the opt-out on the headline summaries (parent dashboard) ─────────────

def test_signal_summaries_pass_the_opt_out_into_the_aggregate(monkeypatch):
    """The dashboard reads facial attention through this RPC.

    Nulling the field on the way out would leave the rows still being read; the
    aggregate takes the flag so no facial row is touched, which is the promise
    the weekly report and the teacher list already make.
    """
    fake = _FakeSupabase({}, rpc_results={"student_signal_summary_many": []})
    monkeypatch.setattr(main, "supabase", fake)
    main._signal_summaries(["student-1"], include_face=False)
    assert fake.rpc_calls[0][1]["p_include_face"] is False

    fake.rpc_calls.clear()
    main._signal_summaries(["student-1"])
    assert fake.rpc_calls[0][1]["p_include_face"] is True, "included unless asked otherwise"


def test_signal_summary_passes_the_opt_out_into_the_aggregate(monkeypatch):
    fake = _FakeSupabase({}, rpc_results={"student_signal_summary": []})
    monkeypatch.setattr(main, "supabase", fake)
    main._signal_summary("student-1", include_face=False)
    assert fake.rpc_calls[0][1]["p_include_face"] is False


def test_summary_marks_the_opt_out_rather_than_reporting_no_face_data(monkeypatch):
    """face_attention null beside face_samples 0 is exactly what a student the
    camera never saw looks like. face_included is what tells them apart."""
    fake = _FakeSupabase({}, rpc_results={"student_signal_summary_many": [
        {"student_id": "student-1", "focus": 0.6, "face_attention": None,
         "sessions": 2, "cognitive_samples": 10, "face_samples": 0},
    ]})
    monkeypatch.setattr(main, "supabase", fake)
    off = main._signal_summaries(["student-1"], include_face=False)
    assert off["student-1"]["face_included"] is False
    on = main._signal_summaries(["student-1"])
    assert on["student-1"]["face_included"] is True


def test_children_endpoint_threads_the_opt_out(monkeypatch):
    """A parent switching facial reporting off on a child's report and going
    back to the dashboard must not find facial attention on screen again."""
    tables = {**TABLES, "parent_child_links": [
        {"id": "l1", "parent_id": "parent-1", "child_id": "student-1",
         "created_at": "2026-07-01T00:00:00Z"},
    ]}
    fake = _FakeSupabase(tables, rpc_results={"student_signal_summary_many": []})
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "get_user", lambda _r: PARENT)
    children = main.my_children(None, include_face=False)
    assert fake.rpc_calls[0][1]["p_include_face"] is False
    # The fallback shape for a child the RPC returned no row for has to carry
    # the flag too, or the dashboard renders "N/A" where it should say "Off".
    assert all(c["signal_summary"]["face_included"] is False for c in children)
    assert children, "fixture should link at least one child to this parent"


# ── /api/students/{id}/signal-summary ────────────────────────────────────
#
# The teacher student list used to read cognitive_signals and face_signals
# straight from the browser under a 200-row cap. At the poller's 1 Hz default
# that cap binds after about three minutes, so tiles labelled "last 7d" were
# averaging the newest three minutes of one sitting, and the reading count sat
# pinned at exactly 200 while being presented as a count of the week. These
# cover the endpoint that replaced it.

_SUMMARY_ROW = {
    "focus": 0.7, "stress": 0.3, "engagement": 0.5, "face_attention": 0.8,
    "sessions": 4, "cognitive_samples": 51840, "face_samples": 51840,
    "dominant_emotion": "focused",
}


def _summary_fake(monkeypatch, viewer, row=None):
    fake = _FakeSupabase(TABLES, rpc_results={
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
    """The same relationship the "cog: teacher read" RLS policy encodes, which
    is what the browser-client read this replaced was leaning on."""
    _summary_fake(monkeypatch, TEACHER)
    out = main.student_signal_summary("student-1", None)
    assert out["focus"] == 0.7


def test_signal_summary_endpoint_rejects_a_teacher_of_a_different_class(monkeypatch):
    _summary_fake(monkeypatch, OTHER_TEACHER)
    with pytest.raises(main.HTTPException) as exc:
        main.student_signal_summary("student-1", None)
    assert exc.value.status_code == 403


def test_signal_summary_endpoint_allows_a_linked_parent(monkeypatch):
    """Role-neutral path, per CLAUDE.md: gated on the relationship, so it is
    not the teacher list's private endpoint just because that is its caller."""
    _summary_fake(monkeypatch, PARENT)
    assert main.student_signal_summary("student-1", None)["focus"] == 0.7


def test_signal_summary_endpoint_counts_the_whole_window_not_a_row_cap(monkeypatch):
    """The figure a teacher sees is Postgres's count over the window.

    51840 is seven days at 1 Hz across a few sittings -- a number the 200-row
    browser read this replaced could not have produced, and the reason it could
    not is the bug: it would have reported 200 and called it the week.
    """
    _summary_fake(monkeypatch, TEACHER)
    out = main.student_signal_summary("student-1", None)
    assert out["cognitive_samples"] == 51840
    assert out["face_samples"] == 51840


def test_signal_summary_endpoint_threads_the_opt_out(monkeypatch):
    fake = _summary_fake(monkeypatch, TEACHER)
    main.student_signal_summary("student-1", None, include_face=False)
    assert fake.rpc_calls[0][1]["p_include_face"] is False

    fake.rpc_calls.clear()
    main.student_signal_summary("student-1", None)
    assert fake.rpc_calls[0][1]["p_include_face"] is True, "included unless asked otherwise"


def test_signal_summary_endpoint_clamps_the_day_range(monkeypatch):
    """Same bounds as the weekly report, so a caller cannot ask for an
    unbounded scan by putting a large number in the query string."""
    fake = _summary_fake(monkeypatch, TEACHER)
    main.student_signal_summary("student-1", None, days=9999)
    assert fake.rpc_calls[0][1]["p_days"] == 30

    fake.rpc_calls.clear()
    main.student_signal_summary("student-1", None, days=0)
    assert fake.rpc_calls[0][1]["p_days"] == 1


def test_signal_summary_carries_the_dominant_emotion(monkeypatch):
    """Computed in the aggregate rather than by counting emotions client-side,
    for the same reason the averages are: a capped row read only ever saw the
    newest few minutes of them."""
    _summary_fake(monkeypatch, TEACHER)
    assert main.student_signal_summary("student-1", None)["dominant_emotion"] == "focused"


def test_signal_summary_withholds_the_dominant_emotion_when_the_opt_out_is_on(monkeypatch):
    """emotion is a facial reading. A stale value surviving the opt-out would
    put facial data back on screen with the switch reading "off"."""
    _summary_fake(monkeypatch, TEACHER)
    out = main.student_signal_summary("student-1", None, include_face=False)
    assert out["dominant_emotion"] is None
    assert out["face_included"] is False


def test_batch_summaries_carry_no_dominant_emotion(monkeypatch):
    """Only the single-student RPC computes it. Adding it to the shared shape
    would report an always-null "no emotion recorded" for every child on the
    parent dashboard, for a figure that page never asks for or renders."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase({}, rpc_results={
        "student_signal_summary_many": [
            {"student_id": "student-1", "focus": 0.4, "sessions": 1, "cognitive_samples": 3},
        ],
    }))
    assert "dominant_emotion" not in main._signal_summaries(["student-1"])["student-1"]


# ── where the facial-recognition opt-out reaches, and where it does not ──
#
# The control covers the reporting surfaces, each of which renders the switch.
# Live monitoring and session review deliberately sit outside it: both are
# teacher-only views built around whether the camera is currently working, and
# neither renders the switch, so honouring it there would silently change a
# page the control is absent from.
#
# That boundary is documented in frontend/src/lib/facePref.js and at both call
# sites. These two tests are what make moving it fail loudly rather than
# leaving the note quietly wrong -- the same reason the parent dashboard's
# hasSignalSummary carries a test for the tile it deliberately omits.

def test_every_reporting_endpoint_takes_the_opt_out():
    for fn in (main.student_weekly_report, main.student_signal_summary, main.my_children):
        assert "include_face" in inspect.signature(fn).parameters, (
            f"{fn.__name__} renders facial data on a surface that shows the switch"
        )


def test_the_opt_out_deliberately_does_not_reach_live_or_session_review():
    """Asserting an absence, on purpose.

    If either of these grows an include_face, the scope note in facePref.js has
    become wrong and the switch needs to appear on the page as well -- so this
    should fail and send whoever added it to that note, rather than letting the
    two halves drift apart silently.
    """
    for fn in (main.class_live, main.session_signals):
        assert "include_face" not in inspect.signature(fn).parameters, (
            f"{fn.__name__} now honours the opt-out; update the scope note in "
            "frontend/src/lib/facePref.js and render the switch on that page"
        )


def test_missing_class_returns_404_not_500():
    # .single() raises on zero rows, so without handling this surfaced as a 500.
    with pytest.raises(main.HTTPException) as exc:
        main._verify_class_owner("class-does-not-exist", "teacher-1")
    assert exc.value.status_code == 404


# ── facial-recognition opt-out ───────────────────────────────────────────

def test_report_without_face_never_queries_face_signals(monkeypatch):
    """The opt-out has to mean "not read", not "read and hidden".

    A parent switching facial reporting off is making a statement about what
    gets looked at, so asserting on the nulled output alone would pass even if
    the rows were still being fetched.
    """
    fake = _FakeSupabase(_signal_tables(
        [{"user_id": "student-1", "ts": _ts(1), "focus": 0.7}],
        [{"user_id": "student-1", "ts": _ts(1), "attention": 0.9, "emotion": "happy"}],
    ))
    monkeypatch.setattr(main, "supabase", fake)
    report = main._weekly_signal_report("student-1", include_face=False)

    assert "face_signals" not in fake.table_calls
    assert report["face_included"] is False
    assert report["averages"]["face_attention"] is None
    assert report["highlights"]["dominant_emotion"] is None
    assert report["sample_counts"]["face"] == 0
    # Cognitive is unaffected.
    assert report["averages"]["focus"] == 0.7


def test_report_without_face_marks_days_not_applicable_rather_than_unretrieved(monkeypatch):
    """face_retrieved=False means "the cap stopped us". With face reporting
    off nothing was requested, so False would report a retrieval failure that
    never happened -- and the UI counts `=== false` to warn about gaps."""
    fake = _FakeSupabase(_signal_tables(
        [{"user_id": "student-1", "ts": _ts(1), "focus": 0.7}]))
    monkeypatch.setattr(main, "supabase", fake)
    report = main._weekly_signal_report("student-1", include_face=False)
    assert report["daily"], "days should still be reported"
    assert all(d["face_retrieved"] is None for d in report["daily"])


def test_report_without_face_does_not_claim_facial_data_was_absent(monkeypatch):
    """Saying "no facial recognition samples were recorded" reports an absence
    that was never measured."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_signal_tables([])))
    off = main._weekly_signal_report("student-1", include_face=False)
    on = main._weekly_signal_report("student-1", include_face=True)
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


def test_strategy_basis_aggregates_instead_of_reading_signal_rows(monkeypatch):
    """The rules and the prompt use six numbers between them.

    Reading them out of _weekly_signal_report transferred up to _REPORT_ROW_CAP
    rows from each signal table to arrive at them, on the endpoint that is also
    the heaviest thing a click can trigger. Asserted on the queries rather than
    the output: identical numbers can be reached either way, and which is what
    this is about.
    """
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
    out = main.student_learning_strategies("student-1", None, main.LearningStrategyRequest())

    assert "cognitive_signals" not in fake.table_calls
    assert "face_signals" not in fake.table_calls
    assert [name for name, _ in fake.rpc_calls] == ["student_signal_summary"]
    assert out["basis"]["averages"]["focus"] == 0.7


def test_strategy_basis_counts_sessions_in_postgres(monkeypatch):
    """The aggregate's session count is exact.

    The report's figure is a row count under _SESSION_ROW_CAP, so a busy week
    reached the model as "practice sessions recorded: 100" however many there
    really were.
    """
    fake = _FakeSupabase(
        {**TABLES, **_strategy_tables()},
        rpc_results={"student_signal_summary": [{"sessions": 137}]},
    )
    monkeypatch.setattr(main, "supabase", fake)
    basis = main._strategy_basis("student-1", 7, True)
    assert basis["sample_counts"]["sessions"] == 137


def test_strategy_basis_drops_identity_confidence(monkeypatch):
    """basis.averages is the response contract. identity_confidence is a
    face-recognition confidence score this endpoint was never about, and it was
    in there only because the report's whole averages dict was passed along."""
    fake = _FakeSupabase(
        {**TABLES, **_strategy_tables()},
        rpc_results={"student_signal_summary": [{"focus": 0.7}]},
    )
    monkeypatch.setattr(main, "supabase", fake)
    basis = main._strategy_basis("student-1", 7, True)
    assert set(basis["averages"]) == {"focus", "stress", "engagement", "face_attention"}


def test_strategy_basis_threads_the_opt_out_into_the_aggregate(monkeypatch):
    """Same guarantee the other surfaces make: with the switch off, no facial
    row is read here either."""
    fake = _FakeSupabase({**TABLES, **_strategy_tables()},
                         rpc_results={"student_signal_summary": []})
    monkeypatch.setattr(main, "supabase", fake)
    main._strategy_basis("student-1", 7, False)
    assert fake.rpc_calls[0][1]["p_include_face"] is False
    assert "face_signals" not in fake.table_calls


def test_learning_strategies_rejects_a_viewer_with_no_relationship(monkeypatch):
    """Same gate as every other student-data endpoint: the relationship, not a
    role claim. The service-role client bypasses RLS, so this check is the only
    thing in the way."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase({**TABLES, **_strategy_tables()}))
    monkeypatch.setattr(main, "get_user", lambda _r: STRANGER)
    with pytest.raises(main.HTTPException) as exc:
        main.student_learning_strategies("student-1", None, main.LearningStrategyRequest())
    assert exc.value.status_code == 403


def test_learning_strategies_allows_a_linked_parent(monkeypatch):
    monkeypatch.setattr(main, "supabase", _FakeSupabase({**TABLES, **_strategy_tables()}))
    monkeypatch.setattr(main, "get_user", lambda _r: PARENT)
    out = main.student_learning_strategies("student-1", None, main.LearningStrategyRequest())
    assert out["student_id"] == "student-1"
    # Four with no signal data: the face-attention rule is conditional on a low
    # reading, so it does not fire here. The cap is five, not a quota.
    assert len(out["strategies"]) == 4
    assert out["source"] == "rule-based"


def test_weakest_topic_ignores_topics_with_no_attempts():
    """_topic_breakdown reports an unattempted topic at 0%, which would always
    win -- sending a parent to revise a topic never given to their child."""
    topics = [
        {"topic_name": "algebra", "attempted_questions": 0, "accuracy": 0},
        {"topic_name": "geometry", "attempted_questions": 10, "accuracy": 40},
        {"topic_name": "mean", "attempted_questions": 5, "accuracy": 80},
    ]
    assert main._weakest_topic(topics)["topic_name"] == "geometry"
    assert main._weakest_topic([]) is None
    assert main._weakest_topic([{"topic_name": "x", "attempted_questions": 0, "accuracy": 0}]) is None


def test_weakest_topic_summary_carries_only_the_named_fields():
    """The _topic_breakdown row also holds topic_id, a stress reading and
    updated_at. Returning it whole made all of those part of this endpoint's
    response contract by accident."""
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


def test_rule_based_strategies_ignore_face_attention_when_reporting_is_off():
    averages = {"face_attention": 0.2, "focus": 0.7, "stress": 0.3}
    off = main._rule_based_strategies({"averages": averages, "face_included": False}, [])
    on = main._rule_based_strategies({"averages": averages, "face_included": True}, [])
    assert any("attention drifts" in s for s in on)
    assert not any("attention drifts" in s for s in off)


def test_strategy_prompt_carries_no_identifying_data():
    """The report holds the student id and raw `latest` rows. The model needs
    the shape of the week, not a record identifying a child."""
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


# Three well-formed items. Prefixing the clinical cases with these makes the
# safety filter the reason they are rejected -- written as one-liners they were
# also failing the "fewer than three items" check, so the assertions would have
# held even with the clinical filter removed entirely.
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


def test_validated_strategies_rejects_clinical_language_outside_the_list():
    """Checked against the whole reply, not just the lines that survive parsing.

    An unmarked preamble is dropped, so a diagnosis there would never reach a
    parent -- but a model that volunteered one is not following the prompt, and
    the items it happened to format correctly have not earned more trust.
    """
    assert main._validated_strategies(
        f"Note: these results suggest dyslexia.\n{_THREE_SAFE}") is None


def test_validated_strategies_drops_an_unmarked_preamble():
    """Every non-empty line used to count as a strategy, so a lead-in became
    numbered advice a parent was handed and the model never wrote."""
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
    """No markers means nothing was parsed as an item, so there is no reply to
    accept -- the rule-based list stands."""
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
    """"1. a" is well-formed: three marked items, none over the ceiling, no
    clinical vocabulary. Without a floor it passed every check and reached a
    parent labelled model-refined."""
    assert main._validated_strategies("1. a\n2. b\n3. c") is None
    assert main._validated_strategies(
        f"{_THREE_SAFE}\n4. Practice more") is None, "one stub rejects the whole reply"


def test_validated_strategies_accepts_ordinary_advice_at_the_floor():
    """The floor has to clear real one-liners, or it just disables the model."""
    out = main._validated_strategies(_THREE_SAFE)
    assert out and len(out) == 3


def test_validated_strategies_unwraps_markdown_emphasis():
    """Nothing between here and the parent renders markdown, so asterisks would
    arrive as literal punctuation. The whole-item case matters most: the bold
    marker sits in front of the number, where the list pattern would eat it as
    a bullet and leave the digits behind as text."""
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
    """Underscores are only emphasis at a word boundary.

    Matched anywhere, the unwrapping treated two topics named the way the
    tables store them as one emphasis span and deleted both underscores:
    "angle_relationships and mean_median" reached a parent as
    "anglerelationships and meanmedian" -- garbled words that passed every
    other check, the line being well formed and the right length.

    Both snake_case terms belong on one line. The delimiters have to pair up
    for the old pattern to match at all, so a line carrying only one underscore
    holds nothing.
    """
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
    """Asterisks are only emphasis at a word boundary, same as underscores.

    "*" is the multiplication sign, and this is a maths app. Matched anywhere,
    the unwrapping treated two products in one line as one emphasis span and
    deleted both asterisks: "practise 7*8 and 9*6" reached a parent as
    "practise 78 and 96" -- a garbled line that passed every other check, being
    well formed and the right length. Exactly the failure snake_case topic
    names hit, and at least as reachable: the model is being asked for maths
    practice advice.

    Both products belong on one line. The delimiters have to pair up for the
    old pattern to match at all, so a line carrying only one asterisk holds
    nothing.
    """
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
    """A leading "* " is a bullet, not emphasis -- unwrapping must not eat it."""
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


def test_learning_strategies_skips_the_model_when_not_enabled(monkeypatch):
    """Default deployment has no local Ollama. The endpoint must answer without
    opening a socket, not fail or hang."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase({**TABLES, **_strategy_tables()}))
    monkeypatch.setattr(main, "get_user", lambda _r: PARENT)
    monkeypatch.setattr(main, "STRATEGY_LLM_ENABLED", False)
    called = []
    monkeypatch.setattr(main, "_llm_strategies", lambda *_a: called.append(1))
    out = main.student_learning_strategies("student-1", None, main.LearningStrategyRequest())
    assert called == []
    assert out["source"] == "rule-based"


def test_learning_strategies_keeps_the_safe_list_when_the_model_is_rejected(monkeypatch):
    """The whole point of the validation: rejected output must not reach a
    parent, and the response must say the model was tried and discarded."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase({**TABLES, **_strategy_tables()}))
    monkeypatch.setattr(main, "get_user", lambda _r: PARENT)
    monkeypatch.setattr(main, "STRATEGY_LLM_ENABLED", True)
    monkeypatch.setattr(main, "_llm_strategies", lambda *_a: None)
    out = main.student_learning_strategies("student-1", None, main.LearningStrategyRequest())
    baseline = main._rule_based_strategies(
        main._strategy_basis("student-1", 7, True), [])
    assert out["strategies"] == baseline
    assert out["source"] == "rule-based (model output rejected)"


def test_learning_strategies_uses_validated_model_output(monkeypatch):
    monkeypatch.setattr(main, "supabase", _FakeSupabase({**TABLES, **_strategy_tables()}))
    monkeypatch.setattr(main, "get_user", lambda _r: PARENT)
    monkeypatch.setattr(main, "STRATEGY_LLM_ENABLED", True)
    refined = ["Review fractions for ten minutes", "Take a short break between sets"]
    monkeypatch.setattr(main, "_llm_strategies", lambda *_a: refined)
    out = main.student_learning_strategies("student-1", None, main.LearningStrategyRequest())
    assert out["strategies"] == refined
    assert out["source"] == "model-refined"


def _fake_ollama(monkeypatch, generate):
    """Install a stand-in ollama module exposing a Client with `generate`."""
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
    """Any failure reaching the model is a fallback, not a 500."""
    def _boom(**_k):
        raise ConnectionError("connection refused")
    _fake_ollama(monkeypatch, _boom)
    assert main._llm_strategies("prompt") is None


def test_llm_strategies_bounds_the_call_with_a_timeout(monkeypatch):
    """A server that accepts the connection and then stalls never raises, so
    without a deadline on the client this endpoint holds a worker thread open
    instead of falling back to the answer it guarantees."""
    client = _fake_ollama(monkeypatch, lambda **_k: {"response": _THREE_SAFE})
    monkeypatch.setattr(main, "STRATEGY_LLM_TIMEOUT", 7.5)
    assert main._llm_strategies("prompt") == [
        "Review fractions for ten minutes",
        "Take a short break between sets",
        "Ask which problem felt hardest",
    ]
    assert client.last_kwargs.get("timeout") == 7.5


def test_llm_call_is_abandoned_once_it_outlives_the_deadline(monkeypatch):
    """The client-side timeout is per operation, not for the call as a whole.

    A server that keeps resetting it -- dribbling a byte inside every read
    window -- holds the request open indefinitely while every individual
    deadline is honoured. What the caller waits for has to be bounded
    separately, or the guarantee is only that no single read stalls.
    """
    monkeypatch.setattr(main, "supabase", _FakeSupabase({**TABLES, **_strategy_tables()}))
    monkeypatch.setattr(main, "get_user", lambda _r: PARENT)
    monkeypatch.setattr(main, "STRATEGY_LLM_ENABLED", True)
    monkeypatch.setattr(main, "STRATEGY_LLM_TIMEOUT", 0.05)

    released = threading.Event()

    def _hang(*_a):
        # Bounded so the pool thread cannot outlive the suite, but far longer
        # than the deadline under test.
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
    """max_workers bounds the threads, not the queue behind them.

    With every worker stuck on a stalled server, a submission that the caller
    later gives up on still sits in the pool's work queue and runs once a worker
    frees up. Left uncancelled, a sustained outage accumulates a backlog of
    prompts nobody is waiting for and then generates every one of them against
    the recovered server.
    """
    released = threading.Event()
    started = []

    def _work(prompt):
        started.append(prompt)
        released.wait(timeout=10)
        return None

    # A dedicated single-worker pool, so occupying it cannot disturb the
    # module-level one the rest of the suite shares.
    pool = ThreadPoolExecutor(max_workers=1)
    original_pool = main._STRATEGY_LLM_POOL
    original_llm = main._llm_strategies
    original_timeout = main.STRATEGY_LLM_TIMEOUT
    main._STRATEGY_LLM_POOL = pool
    main._llm_strategies = _work
    main.STRATEGY_LLM_TIMEOUT = 0.05
    try:
        blocker = pool.submit(main._llm_strategies, "occupying")
        while not started:            # let it actually reach the worker, so
            time.sleep(0.01)          # the next submission has to queue
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


def test_pool_refusing_the_work_falls_back_rather_than_raising():
    """submit() itself raises on a shut-down pool. That has to degrade to the
    rule-based answer like every other model failure, not surface as a 500."""
    pool = ThreadPoolExecutor(max_workers=1)
    pool.shutdown(wait=True)
    original_pool = main._STRATEGY_LLM_POOL
    main._STRATEGY_LLM_POOL = pool
    try:
        assert main._llm_strategies_bounded("prompt") is None
    finally:
        main._STRATEGY_LLM_POOL = original_pool


# ── numeric configuration ────────────────────────────────────────────────

def test_bad_numeric_env_falls_back_instead_of_killing_the_process(monkeypatch):
    """These are read at import, so a typo would raise before the app object
    exists -- taking down every endpoint over a tuning parameter that belongs to
    one optional feature."""
    monkeypatch.setenv("STRATEGY_RATE_LIMIT", "ten")
    assert main._env_number("STRATEGY_RATE_LIMIT", 10, int) == 10


def test_unset_and_empty_numeric_env_use_the_default(monkeypatch):
    monkeypatch.delenv("STRATEGY_RATE_WINDOW", raising=False)
    assert main._env_number("STRATEGY_RATE_WINDOW", 60.0, float) == 60.0
    # An exported-but-empty variable is the shape a shell leaves behind, and
    # float("") raises just as loudly as float("abc").
    monkeypatch.setenv("STRATEGY_RATE_WINDOW", "   ")
    assert main._env_number("STRATEGY_RATE_WINDOW", 60.0, float) == 60.0


def test_valid_numeric_env_is_still_honoured(monkeypatch):
    """The fallback must not swallow a setting that was configured correctly."""
    monkeypatch.setenv("STRATEGY_LLM_TIMEOUT", "2.5")
    assert main._env_number("STRATEGY_LLM_TIMEOUT", 20.0, float) == 2.5


@pytest.mark.parametrize("raw", ["0", "-1"])
def test_out_of_range_numeric_env_is_clamped_not_honoured(raw, monkeypatch):
    """A number is not automatically a usable setting.

    Each of these parses, so the ValueError fallback never fires, and each
    disables its feature in whichever direction the parameter points: a rate
    limit of zero makes `len(hits) >= limit` true on the first request and 429s
    every caller; a window of zero leaves every hit already expired, so nothing
    is counted and the ceiling silently is not there; a timeout of zero has the
    wait expire before the model can answer, so the pass is off while
    STRATEGY_LLM_ENABLED still says it is on.
    """
    monkeypatch.setenv("STRATEGY_RATE_LIMIT", raw)
    assert main._env_number("STRATEGY_RATE_LIMIT", 10, int, minimum=1) == 1


def test_clamping_is_to_the_minimum_not_back_to_the_default(monkeypatch):
    """A deployer who wrote a small number was asking for a small number, so
    the nearest usable value is a better answer than the shipped one."""
    monkeypatch.setenv("STRATEGY_RATE_WINDOW", "0.25")
    assert main._env_number("STRATEGY_RATE_WINDOW", 60.0, float, minimum=1.0) == 1.0


def test_in_range_values_are_untouched_by_the_floor(monkeypatch):
    monkeypatch.setenv("STRATEGY_LLM_TIMEOUT", "2.5")
    assert main._env_number("STRATEGY_LLM_TIMEOUT", 20.0, float, minimum=1.0) == 2.5
    # No minimum given: unchanged behaviour for callers that do not want one.
    monkeypatch.setenv("STRATEGY_RATE_LIMIT", "0")
    assert main._env_number("STRATEGY_RATE_LIMIT", 10, int) == 0


def test_the_shipped_settings_carry_a_floor():
    """The floors are only worth anything if the real settings ask for them."""
    assert main._STRATEGY_RATE_LIMIT >= 1
    assert main._STRATEGY_RATE_WINDOW >= 1.0


@pytest.mark.parametrize("raw", ["inf", "-inf", "nan", "Infinity", "NaN"])
def test_non_finite_numeric_env_falls_back_to_the_default(raw, monkeypatch):
    """The third class of unusable value, which neither guard above catches.

    float() accepts all of these, so the ValueError fallback never fires. The
    floor does not catch inf or nan either: inf is above every minimum, and
    every comparison against nan is False, so `value < minimum` is False for
    both.

    -inf is the one case the floor *would* have caught, and it is listed here
    to pin that it deliberately no longer does. Clamping it to the minimum
    treats it as a deployer asking for a small number; it is not a magnitude at
    all, so it belongs with its siblings on the fallback rather than being
    rounded into a plausible-looking setting.
    """
    monkeypatch.setenv("STRATEGY_LLM_TIMEOUT", raw)
    assert main._env_number("STRATEGY_LLM_TIMEOUT", 20.0, float, minimum=1.0) == 20.0


def test_non_finite_rate_window_would_otherwise_turn_a_429_into_a_500():
    """Why the fallback is the right answer for inf rather than clamping.

    With an infinite window no recorded hit ever expires, so a caller past the
    limit stays past it -- and the Retry-After calculation on that path computes
    int(inf), which raises OverflowError. The rate limiter answers 500 instead
    of 429, permanently, from a value that parses cleanly.
    """
    with pytest.raises(OverflowError):
        int(float("inf") - 1.0)


def test_non_finite_timeout_would_otherwise_disable_the_model_pass(monkeypatch):
    """And why nan is not a timeout either: the wait expires immediately, so the
    model pass is off for good while STRATEGY_LLM_ENABLED still says it is on,
    with every reply coming back "rule-based (model output rejected)"."""
    from concurrent.futures import Future
    with pytest.raises(TimeoutError):
        Future().result(timeout=float("nan"))


def test_a_finite_value_below_the_floor_still_clamps(monkeypatch):
    """The non-finite check runs before the floor, so it must not shadow it."""
    monkeypatch.setenv("STRATEGY_LLM_TIMEOUT", "0")
    assert main._env_number("STRATEGY_LLM_TIMEOUT", 20.0, float, minimum=1.0) == 1.0
    assert main.STRATEGY_LLM_TIMEOUT >= 1.0


# ── strategy rate limit ──────────────────────────────────────────────────

def _strategies_as(viewer, monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: viewer)
    return main.student_learning_strategies(
        "student-1", None, main.LearningStrategyRequest())


def test_learning_strategies_rate_limits_a_repeating_caller(monkeypatch):
    """It is the heaviest endpoint a click can trigger -- two capped signal
    reads, a topic breakdown, and optionally a model call -- behind a button
    that can be pressed as fast as a parent likes."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase({**TABLES, **_strategy_tables()}))
    monkeypatch.setattr(main, "_STRATEGY_RATE_LIMIT", 3)

    for _ in range(3):
        assert _strategies_as(PARENT, monkeypatch)["student_id"] == "student-1"

    with pytest.raises(main.HTTPException) as exc:
        _strategies_as(PARENT, monkeypatch)
    assert exc.value.status_code == 429
    # Without it the client is told to back off but not for how long.
    assert int(exc.value.headers["Retry-After"]) >= 1


def test_learning_strategies_rate_limit_is_per_caller(monkeypatch):
    """Counted per viewer, not globally: one parent exhausting their allowance
    must not lock every other parent out of the endpoint."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase({**TABLES, **_strategy_tables()}))
    monkeypatch.setattr(main, "_STRATEGY_RATE_LIMIT", 1)

    _strategies_as(PARENT, monkeypatch)
    with pytest.raises(main.HTTPException):
        _strategies_as(PARENT, monkeypatch)

    # A teacher of student-1's class, with their own untouched allowance.
    assert _strategies_as(TEACHER, monkeypatch)["student_id"] == "student-1"


def test_learning_strategies_checks_access_before_the_rate_limit(monkeypatch):
    """A caller with no relationship gets 403, not 429. The limit protects the
    work below it; masking the access decision would make an unauthorised
    caller's result depend on how often they had asked."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase({**TABLES, **_strategy_tables()}))
    monkeypatch.setattr(main, "_STRATEGY_RATE_LIMIT", 1)

    for _ in range(3):
        with pytest.raises(main.HTTPException) as exc:
            _strategies_as(STRANGER, monkeypatch)
        assert exc.value.status_code == 403


def test_rate_limit_sweep_reclaims_callers_whose_window_has_passed(monkeypatch):
    """The dict grows with everyone who has ever used the endpoint."""
    monkeypatch.setattr(main, "_STRATEGY_SWEEP_ABOVE", 2)
    monkeypatch.setattr(main, "_STRATEGY_RATE_WINDOW", 0.0)   # every hit already expired
    for i in range(4):
        main._rate_limit_strategies(f"user-{i}")
    # The sweep runs on the call that crosses the threshold, so the caller
    # being served is the one left behind.
    assert len(main._strategy_hits) == 1


def test_rate_limit_sweep_does_not_run_on_every_request(monkeypatch):
    """Past the size threshold with that many *active* callers there is nothing
    to reclaim, and a size-only trigger rescanned the whole dict on every
    request -- under the lock -- to discover that each time."""
    monkeypatch.setattr(main, "_STRATEGY_SWEEP_ABOVE", 2)
    scans = []
    real_items = dict.items
    monkeypatch.setattr(main, "_strategy_hits",
                        type("_Counted", (dict,), {
                            "items": lambda self: (scans.append(1), real_items(self))[1],
                        })())
    for i in range(6):
        main._rate_limit_strategies(f"user-{i}")
    assert len(scans) == 1, f"swept {len(scans)} times for 6 requests"


def test_learning_strategies_clamps_the_day_range(monkeypatch):
    fake = _FakeSupabase({**TABLES, **_strategy_tables()})
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "get_user", lambda _r: PARENT)
    assert main.student_learning_strategies(
        "student-1", None, main.LearningStrategyRequest(days=999))["basis"]["days"] == 30
    assert main.student_learning_strategies(
        "student-1", None, main.LearningStrategyRequest(days=0))["basis"]["days"] == 1
