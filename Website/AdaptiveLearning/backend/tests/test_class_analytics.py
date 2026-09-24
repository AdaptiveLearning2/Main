"""Teacher analytics: reshaping, bucket filling and flags (arithmetic is in assert_signal_rls.sql)."""
import os
from datetime import datetime, timezone

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402
from tests.test_access_control import _FakeSupabase  # noqa: E402

TEACHER = {"id": "teacher-1"}
OTHER_TEACHER = {"id": "teacher-2"}
CLASS = "class-1"
ALICE, BOB = "student-1", "student-2"

# A Thursday, pinned so "the last 30 days" cannot drift with the wall clock.
NOW_UTC = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch):
    monkeypatch.setattr(main, "_utc_now", lambda: NOW_UTC)
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": main.WINDOW_OPEN, "starts_on": "2000-01-01",
        "ends_on": "2099-12-31", "timezone": "UTC"})


@pytest.fixture(autouse=True)
def _teacher_owns_the_class(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: TEACHER)


def _perf(user, topic_id, topic_name, attempted, correct):
    return {"user_id": user, "topic_id": topic_id,
            "attempted_questions": attempted, "correct_questions": correct,
            "math_topics": {"topic_name": topic_name}}


def _tables(perf=(), roster=(ALICE, BOB), owner="teacher-1"):
    return {
        "classes": [{"id": CLASS, "teacher_id": owner}],
        "class_memberships": [{"class_id": CLASS, "student_id": s,
                               "joined_at": "2026-01-01T00:00:00Z"} for s in roster],
        "profiles": [{"id": ALICE, "display_name": "Alice", "role": "student"},
                     {"id": BOB, "display_name": "Bob", "role": "student"}],
        "user_math_performance": list(perf),
        "signal_consent": [{"user_id": ALICE, "eeg_enabled": True,
                            "headband_optical_enabled": True, "camera_enabled": True}],
    }


def _fake(**kw):
    tables = kw.pop("tables", None) or _tables(**{k: kw.pop(k) for k in
                                                 ("perf", "roster", "owner")
                                                 if k in kw})
    return _FakeSupabase(tables, **kw)


# ─── the access check runs before any read ────────────────────────────────
# Per endpoint, not once against the helper: a handler that skipped it would pass that.

@pytest.mark.parametrize("call", [
    lambda: main.class_topic_heatmap(CLASS, None),
    lambda: main.class_accuracy_trend(CLASS, None),
    lambda: main.class_time_of_day(CLASS, None),
])
def test_a_non_owning_teacher_is_refused(monkeypatch, call):
    monkeypatch.setattr(main, "get_user", lambda _r: OTHER_TEACHER)
    monkeypatch.setattr(main, "supabase", _fake())
    with pytest.raises(main.HTTPException) as exc:
        call()
    assert exc.value.status_code == 403


@pytest.mark.parametrize("call", [
    lambda: main.class_topic_heatmap(CLASS, None),
    lambda: main.class_accuracy_trend(CLASS, None),
    lambda: main.class_time_of_day(CLASS, None),
])
def test_the_refusal_happens_before_the_roster_is_read(monkeypatch, call):
    """A 403 after reading the roster still leaks its size through timing."""
    fake = _fake()
    monkeypatch.setattr(main, "get_user", lambda _r: OTHER_TEACHER)
    monkeypatch.setattr(main, "supabase", fake)
    with pytest.raises(main.HTTPException):
        call()
    # The ownership read did happen, so this isn't passing on a handler that never reads.
    assert "classes" in fake.table_calls
    assert "class_memberships" not in fake.table_calls


# ─── topic heatmap ────────────────────────────────────────────────────────

def test_the_grid_rows_line_up_with_its_headings(monkeypatch):
    """Cells are a list aligned to `topics`, built in one pass."""
    monkeypatch.setattr(main, "supabase", _fake(perf=[
        _perf(ALICE, 1, "algebra", 10, 5),
        _perf(ALICE, 2, "geometry", 4, 4),
        _perf(BOB, 2, "geometry", 8, 2),
    ]))
    out = main.class_topic_heatmap(CLASS, None)
    names = [t["topic_name"] for t in out["topics"]]
    assert names == ["algebra", "geometry"]

    alice = next(s for s in out["students"] if s["user_id"] == ALICE)
    bob = next(s for s in out["students"] if s["user_id"] == BOB)
    assert len(alice["cells"]) == len(bob["cells"]) == len(names)
    assert alice["cells"][0]["accuracy"] == 0.5      # algebra
    assert alice["cells"][1]["accuracy"] == 1.0      # geometry
    assert bob["cells"][1]["accuracy"] == 0.25


def test_a_topic_a_student_has_never_seen_is_null_not_zero(monkeypatch):
    """Zero means every attempt was wrong; a topic never served is not that."""
    monkeypatch.setattr(main, "supabase", _fake(perf=[
        _perf(ALICE, 1, "algebra", 10, 5),
        _perf(BOB, 2, "geometry", 8, 0),
    ]))
    out = main.class_topic_heatmap(CLASS, None)
    alice = next(s for s in out["students"] if s["user_id"] == ALICE)
    bob = next(s for s in out["students"] if s["user_id"] == BOB)
    # Alice has no geometry row at all.
    assert alice["cells"][1] is None
    # Bob answered geometry and got none right -- a real zero, kept as one.
    assert bob["cells"][1]["accuracy"] == 0.0


def test_a_row_that_exists_with_no_attempts_is_also_null(monkeypatch):
    """`record_topic_attempt` upserts, so a row can exist at zero attempts."""
    monkeypatch.setattr(main, "supabase", _fake(perf=[
        _perf(ALICE, 1, "algebra", 0, 0),
        _perf(BOB, 1, "algebra", 4, 3),
    ]))
    out = main.class_topic_heatmap(CLASS, None)
    alice = next(s for s in out["students"] if s["user_id"] == ALICE)
    assert alice["cells"][0] is None


def test_only_topics_the_class_has_been_served_appear(monkeypatch):
    """Topic identity comes from the rows, not from a read of `math_topics`."""
    fake = _fake(perf=[_perf(ALICE, 1, "algebra", 10, 5)])
    monkeypatch.setattr(main, "supabase", fake)
    out = main.class_topic_heatmap(CLASS, None)
    assert [t["topic_name"] for t in out["topics"]] == ["algebra"]
    assert "math_topics" not in fake.table_calls


def test_the_column_total_is_over_answers_not_a_mean_of_students(monkeypatch):
    monkeypatch.setattr(main, "supabase", _fake(perf=[
        _perf(ALICE, 1, "algebra", 100, 100),
        _perf(BOB, 1, "algebra", 4, 0),
    ]))
    out = main.class_topic_heatmap(CLASS, None)
    # 100/104, not the mean of 1.0 and 0.0.
    assert out["topics"][0]["accuracy"] == round(100 / 104, 4)


def test_a_failed_topic_read_is_not_an_empty_class(monkeypatch):
    monkeypatch.setattr(main, "supabase",
                        _fake(table_raises=["user_math_performance"]))
    out = main.class_topic_heatmap(CLASS, None)
    assert out["retrieved"] is False


def test_a_class_that_has_answered_nothing_reads_as_retrieved(monkeypatch):
    monkeypatch.setattr(main, "supabase", _fake(perf=[]))
    out = main.class_topic_heatmap(CLASS, None)
    assert out["retrieved"] is True
    assert out["topics"] == []


def test_the_low_sample_threshold_rides_on_the_payload(monkeypatch):
    """The figure is still returned; the threshold travels with it so the grid dims thin cells."""
    monkeypatch.setattr(main, "supabase", _fake(perf=[
        _perf(ALICE, 1, "algebra", 1, 1)]))
    out = main.class_topic_heatmap(CLASS, None)
    assert out["min_attempts"] == main._MIN_TOPIC_ATTEMPTS
    assert out["students"][0]["cells"][0] == {
        "attempted": 1, "correct": 1, "accuracy": 1.0}


# ─── accuracy trend ───────────────────────────────────────────────────────

def _buckets(*rows):
    return {"class_answer_buckets": [
        {"day": d, "hour": h, "attempted": a, "correct": c} for d, h, a, c in rows]}


def test_every_day_in_range_appears_including_the_empty_ones(monkeypatch):
    """A dropped day renders its neighbours as adjacent."""
    monkeypatch.setattr(main, "supabase", _fake(
        rpc_results=_buckets(("2026-06-11", 9, 10, 8))))
    out = main.class_accuracy_trend(CLASS, None, days=5)
    assert [d["day"] for d in out["days"]] == [
        "2026-06-07", "2026-06-08", "2026-06-09", "2026-06-10", "2026-06-11"]
    assert out["days_with_data"] == 1


def test_a_day_nobody_answered_is_null_accuracy_not_zero(monkeypatch):
    monkeypatch.setattr(main, "supabase", _fake(
        rpc_results=_buckets(("2026-06-11", 9, 4, 0))))
    out = main.class_accuracy_trend(CLASS, None, days=2)
    quiet, worked = out["days"]
    assert quiet["accuracy"] is None and quiet["attempted"] == 0
    # Answered four, got none right: a genuine zero.
    assert worked["accuracy"] == 0.0


def test_hours_of_one_day_are_summed_into_that_day(monkeypatch):
    monkeypatch.setattr(main, "supabase", _fake(rpc_results=_buckets(
        ("2026-06-11", 9, 10, 5), ("2026-06-11", 14, 10, 9))))
    out = main.class_accuracy_trend(CLASS, None, days=1)
    assert out["days"][0]["attempted"] == 20
    assert out["days"][0]["accuracy"] == 0.7


def test_the_range_is_bounded(monkeypatch):
    monkeypatch.setattr(main, "supabase", _fake(rpc_results=_buckets()))
    out = main.class_accuracy_trend(CLASS, None, days=10_000)
    assert len(out["days"]) == main._CLASS_TREND_MAX_DAYS
    # And a nonsense low value cannot produce an empty chart with no days.
    assert len(main.class_accuracy_trend(CLASS, None, days=0)["days"]) == 1


def test_a_failed_read_is_not_a_quiet_month(monkeypatch):
    def _raises(name, _params):
        return RuntimeError("boom") if name == "class_answer_buckets" else None
    monkeypatch.setattr(main, "supabase", _fake(rpc_raises=_raises))
    out = main.class_accuracy_trend(CLASS, None, days=7)
    assert out["retrieved"] is False
    # Days are still emitted for the axis, so the flag must be read before drawing.
    assert len(out["days"]) == 7
    assert all(d["accuracy"] is None for d in out["days"])


def test_a_class_with_no_students_is_not_a_failed_read(monkeypatch):
    monkeypatch.setattr(main, "supabase", _fake(roster=()))
    out = main.class_accuracy_trend(CLASS, None, days=7)
    assert out["retrieved"] is True and out["student_count"] == 0


@pytest.mark.parametrize("call, key", [
    (lambda: main.class_accuracy_trend(CLASS, None, days=7), "days"),
    (lambda: main.class_time_of_day(CLASS, None, days=7), "cells"),
    (lambda: main.class_topic_heatmap(CLASS, None), "students"),
])
def test_a_failed_roster_read_is_not_a_class_with_no_students(monkeypatch,
                                                              call, key):
    """A failed roster read folds into `retrieved`, not an aggregate over nobody."""
    monkeypatch.setattr(main, "supabase",
                        _fake(table_raises=["class_memberships"]))
    out = call()
    assert out["retrieved"] is False
    assert not out[key] or all(
        s.get("attempted", 0) == 0 for s in out[key] if isinstance(s, dict))


def test_the_range_is_fixed_by_one_clock_read(monkeypatch):
    """A request straddling midnight must not query one range and bucket another.

    The clock advances on every call, so a second read cannot go unnoticed."""
    clock = iter([
        datetime(2026, 6, 11, 23, 59, 59, tzinfo=timezone.utc),   # the query
        datetime(2026, 6, 12, 0, 0, 1, tzinfo=timezone.utc),      # any re-read
        datetime(2026, 6, 12, 0, 0, 2, tzinfo=timezone.utc),
    ])
    monkeypatch.setattr(main, "_utc_now", lambda: next(clock))
    # The oldest day of a 3-day window ending 2026-06-11.
    monkeypatch.setattr(main, "supabase", _fake(
        rpc_results=_buckets(("2026-06-09", 9, 10, 8))))

    out = main.class_accuracy_trend(CLASS, None, days=3)
    assert [d["day"] for d in out["days"]] == [
        "2026-06-09", "2026-06-10", "2026-06-11"]
    assert out["days"][0]["attempted"] == 10
    assert out["attempted"] == 10


def test_the_window_is_asked_for_in_the_schools_timezone(monkeypatch):
    """Bucketing is in SQL, which would otherwise default to UTC."""
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": main.WINDOW_OPEN, "starts_on": "2000-01-01",
        "ends_on": "2099-12-31", "timezone": "America/Los_Angeles"})
    fake = _fake(rpc_results=_buckets())
    monkeypatch.setattr(main, "supabase", fake)
    main.class_accuracy_trend(CLASS, None, days=7)
    name, params = fake.rpc_calls[-1]
    assert name == "class_answer_buckets"
    assert params["p_timezone"] == "America/Los_Angeles"
    # Half-open, ending at tomorrow's local midnight so today is included.
    assert params["p_to"].startswith("2026-06-12T00:00:00")


# ─── time of day ──────────────────────────────────────────────────────────

def test_days_collapse_onto_weekday_and_hour(monkeypatch):
    """2026-06-08 and 2026-06-01 are both Mondays."""
    monkeypatch.setattr(main, "supabase", _fake(rpc_results=_buckets(
        ("2026-06-08", 9, 10, 5), ("2026-06-01", 9, 10, 9),
        ("2026-06-09", 9, 4, 4))))
    out = main.class_time_of_day(CLASS, None, days=30)
    monday_9 = next(c for c in out["cells"] if c["weekday"] == 0 and c["hour"] == 9)
    assert monday_9["attempted"] == 20 and monday_9["accuracy"] == 0.7
    tuesday_9 = next(c for c in out["cells"] if c["weekday"] == 1)
    assert tuesday_9["attempted"] == 4


def test_only_hours_the_class_has_worked_in_are_emitted(monkeypatch):
    monkeypatch.setattr(main, "supabase", _fake(rpc_results=_buckets(
        ("2026-06-08", 9, 10, 5), ("2026-06-09", 14, 10, 5))))
    out = main.class_time_of_day(CLASS, None, days=30)
    assert out["hours"] == [9, 14]
    assert len(out["cells"]) == 2


def test_an_impossible_hour_is_dropped_rather_than_plotted(monkeypatch):
    monkeypatch.setattr(main, "supabase", _fake(rpc_results=_buckets(
        ("2026-06-08", 99, 10, 5), ("2026-06-08", 9, 4, 2))))
    out = main.class_time_of_day(CLASS, None, days=30)
    assert out["hours"] == [9]


def test_a_failed_read_is_not_an_empty_timetable(monkeypatch):
    def _raises(name, _params):
        return RuntimeError("boom") if name == "class_answer_buckets" else None
    monkeypatch.setattr(main, "supabase", _fake(rpc_raises=_raises))
    out = main.class_time_of_day(CLASS, None, days=30)
    assert out["retrieved"] is False and out["cells"] == []


# ─── last active ──────────────────────────────────────────────────────────

def test_the_roster_reads_last_active_once_for_everyone(monkeypatch):
    """One RPC call for the roster, never one per student."""
    fake = _fake(rpc_results={"last_active_for_users": [
        {"user_id": ALICE, "last_active": "2026-06-11T09:00:00Z"}]})
    monkeypatch.setattr(main, "supabase", fake)
    out = main.class_students(CLASS, None)
    assert [c[0] for c in fake.rpc_calls].count("last_active_for_users") == 1

    alice = next(s for s in out if s["user_id"] == ALICE)
    bob = next(s for s in out if s["user_id"] == BOB)
    assert alice["last_active"] == "2026-06-11T09:00:00Z"
    # Never started a session: a real fact, not a failure.
    assert bob["last_active"] is None
    assert bob["last_active_retrieved"] is True


def test_a_failed_last_active_read_is_not_a_roster_of_idle_students(monkeypatch):
    """Reporting a failed read as nobody active is both wrong and actionable."""
    def _raises(name, _params):
        return RuntimeError("boom") if name == "last_active_for_users" else None
    monkeypatch.setattr(main, "supabase", _fake(rpc_raises=_raises))
    out = main.class_students(CLASS, None)
    assert all(s["last_active"] is None for s in out)
    assert all(s["last_active_retrieved"] is False for s in out)


# ─── focus against accuracy ───────────────────────────────────────────────

def _focus(n, r, buckets=()):
    return {"focus_accuracy_for_user": {"n": n, "r": r, "buckets": list(buckets)}}


@pytest.fixture
def _viewer_may_see_alice(monkeypatch):
    monkeypatch.setattr(main, "_can_view_student", lambda _v, _s: True)


def test_a_correlation_over_too_few_answers_is_withheld(monkeypatch,
                                                        _viewer_may_see_alice):
    """r has no visible denominator, and `corr()` answers from two pairs."""
    monkeypatch.setattr(main, "supabase", _fake(rpc_results=_focus(8, 0.91)))
    out = main.student_focus_accuracy(ALICE, None)
    assert out["correlation"] is None
    assert out["sufficient"] is False
    # The count lets the surface say why it is absent.
    assert out["pairs"] == 8
    assert out["min_pairs"] == main._FOCUS_MIN_PAIRS


def test_a_correlation_over_enough_answers_is_reported(monkeypatch,
                                                       _viewer_may_see_alice):
    monkeypatch.setattr(main, "supabase",
                        _fake(rpc_results=_focus(400, 0.4231987)))
    out = main.student_focus_accuracy(ALICE, None)
    assert out["correlation"] == 0.4232
    assert out["sufficient"] is True


def test_a_null_correlation_survives_a_sufficient_sample(monkeypatch,
                                                         _viewer_may_see_alice):
    """`corr()` is null with no variance, which differs from too few pairs."""
    monkeypatch.setattr(main, "supabase", _fake(rpc_results=_focus(400, None)))
    out = main.student_focus_accuracy(ALICE, None)
    assert out["correlation"] is None
    assert out["sufficient"] is True


def test_buckets_carry_their_own_sample_sizes(monkeypatch, _viewer_may_see_alice):
    """Buckets show their own denominators, so they're returned below the threshold."""
    monkeypatch.setattr(main, "supabase", _fake(rpc_results=_focus(9, 0.5, [
        {"bucket": 1, "focus_low": 0.0, "focus_high": 0.2, "answered": 4, "correct": 1},
        {"bucket": 5, "focus_low": 0.8, "focus_high": 1.0, "answered": 5, "correct": 5},
    ])))
    out = main.student_focus_accuracy(ALICE, None)
    assert out["correlation"] is None
    assert [b["accuracy"] for b in out["buckets"]] == [0.25, 1.0]
    assert [b["answered"] for b in out["buckets"]] == [4, 5]


def test_an_empty_bucket_is_null_accuracy(monkeypatch, _viewer_may_see_alice):
    monkeypatch.setattr(main, "supabase", _fake(rpc_results=_focus(0, None, [
        {"bucket": 1, "focus_low": 0.0, "focus_high": 0.2, "answered": 0, "correct": 0},
    ])))
    out = main.student_focus_accuracy(ALICE, None)
    assert out["buckets"][0]["accuracy"] is None


def test_without_eeg_consent_the_join_is_never_run(monkeypatch,
                                                   _viewer_may_see_alice):
    """Assert on the call, not the payload, which is the same either way."""
    tables = _tables()
    tables["signal_consent"] = [{"user_id": ALICE, "eeg_enabled": False,
                                 "headband_optical_enabled": True,
                                 "camera_enabled": True}]
    fake = _FakeSupabase(tables, rpc_results=_focus(400, 0.5))
    monkeypatch.setattr(main, "supabase", fake)
    out = main.student_focus_accuracy(ALICE, None)
    assert "focus_accuracy_for_user" not in [c[0] for c in fake.rpc_calls]
    assert out["eeg_enabled"] is False
    assert out["correlation"] is None
    # Not a failed read: the read succeeded in saying this student declined.
    assert out["retrieved"] is True


def test_a_consented_student_is_asked_for(monkeypatch, _viewer_may_see_alice):
    """The positive half of the test above."""
    fake = _fake(rpc_results=_focus(400, 0.5))
    monkeypatch.setattr(main, "supabase", fake)
    main.student_focus_accuracy(ALICE, None)
    assert "focus_accuracy_for_user" in [c[0] for c in fake.rpc_calls]


def test_a_failed_read_is_not_a_student_with_no_readings(monkeypatch,
                                                         _viewer_may_see_alice):
    def _raises(name, _params):
        return RuntimeError("boom") if name == "focus_accuracy_for_user" else None
    monkeypatch.setattr(main, "supabase", _fake(rpc_raises=_raises))
    out = main.student_focus_accuracy(ALICE, None)
    assert out["retrieved"] is False
    assert out["buckets"] == [] and out["pairs"] == 0


def test_the_payload_shape_is_the_same_on_every_branch(monkeypatch,
                                                       _viewer_may_see_alice):
    """An optional key eventually becomes a fourth state."""
    def _raises(name, _params):
        return RuntimeError("boom") if name == "focus_accuracy_for_user" else None

    declined = _tables()
    declined["signal_consent"] = [{"user_id": ALICE, "eeg_enabled": False,
                                   "headband_optical_enabled": True,
                                   "camera_enabled": True}]

    shapes = []
    for fake in (_fake(rpc_results=_focus(400, 0.5)),
                 _fake(rpc_results=_focus(0, None)),
                 _fake(rpc_raises=_raises),
                 _FakeSupabase(declined)):
        monkeypatch.setattr(main, "supabase", fake)
        shapes.append(set(main.student_focus_accuracy(ALICE, None)))
    assert all(s == shapes[0] for s in shapes)


def test_a_viewer_with_no_relationship_is_refused(monkeypatch):
    monkeypatch.setattr(main, "_can_view_student", lambda _v, _s: False)
    monkeypatch.setattr(main, "supabase", _fake())
    with pytest.raises(main.HTTPException) as exc:
        main.student_focus_accuracy(ALICE, None)
    assert exc.value.status_code == 403
