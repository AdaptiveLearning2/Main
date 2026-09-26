"""user_stats' streaks are computed at each credit and decay on read; they were written as 0 forever."""
import os
from datetime import datetime, timezone

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402

UTC = timezone.utc


def _at(day, hour=12):
    return datetime(2026, 9, day, hour, tzinfo=UTC).isoformat()


@pytest.mark.parametrize("stats,started,expected", [
    ({}, _at(10), (1, 1)),                                                      # first session
    ({"current_streak": 3, "best_streak": 3, "last_session_at": _at(9)}, _at(10), (4, 4)),
    ({"current_streak": 3, "best_streak": 5, "last_session_at": _at(10, 8)}, _at(10), (3, 5)),
    ({"current_streak": 3, "best_streak": 5, "last_session_at": _at(7)}, _at(10), (1, 5)),
    # A row credited before streaks were computed: a same-day session starts the streak at 1.
    ({"current_streak": 0, "best_streak": 0, "last_session_at": _at(10, 8)}, _at(10), (1, 1)),
])
def test_a_credit_moves_the_streak_by_school_day(stats, started, expected):
    out = main._streak_update(stats, started, UTC)
    assert (out["current_streak"], out["best_streak"]) == expected
    assert out["last_session_at"] == started


@pytest.mark.parametrize("last,day,expected", [
    (11, 14, 4),        # Friday then Monday: only the weekend between
    (11, 12, 4),        # Friday then Saturday: a weekend session counts
    (12, 14, 4),        # Saturday then Monday
    (10, 14, 1),        # Thursday then Monday: Friday was missed
], ids=["fri-mon", "fri-sat", "sat-mon", "thu-mon"])
def test_a_weekend_never_breaks_a_streak(last, day, expected):
    """Every class read 'Avg streak 0' on a Monday morning when the streak counted calendar days."""
    stats = {"current_streak": 3, "best_streak": 3, "last_session_at": _at(last)}
    assert main._streak_update(stats, _at(day), UTC)["current_streak"] == expected


@pytest.mark.parametrize("last,today,current", [(11, 14, 3), (10, 14, 0), (11, 15, 0)],
                         ids=["fri, read monday", "thu, read monday", "fri, read tuesday"])
def test_the_read_decays_only_over_a_missed_weekday(monkeypatch, last, today, current):
    monkeypatch.setattr(main, "_utc_now", lambda: datetime(2026, 9, today, 8, tzinfo=UTC))
    stats = {"current_streak": 3, "best_streak": 3, "last_session_at": _at(last)}
    assert main._live_streak(stats, UTC)["current_streak"] == current


def test_an_older_session_closed_late_changes_no_streak():
    """The sweep closes an abandoned Monday session after Tuesday's was credited."""
    stats = {"current_streak": 2, "best_streak": 2, "last_session_at": _at(10)}
    assert main._streak_update(stats, _at(9), UTC) == {}


def test_the_day_is_the_schools_not_utcs():
    """23:30 on the 9th in Los Angeles is the 10th in UTC; the streak day is the 9th."""
    from zoneinfo import ZoneInfo
    la = ZoneInfo("America/Los_Angeles")
    stats = {"current_streak": 1, "best_streak": 1, "last_session_at": _at(9, 16)}   # 09:00 on the 9th, LA
    late = datetime(2026, 9, 10, 6, 30, tzinfo=UTC).isoformat()                    # 23:30 on the 9th, LA
    assert main._streak_update(stats, late, la)["current_streak"] == 1


@pytest.mark.parametrize("last,current", [(_at(10), 4), (_at(9), 4), (_at(8), 0), (None, 0)])
def test_a_streak_reads_zero_once_a_whole_day_is_missed(monkeypatch, last, current):
    monkeypatch.setattr(main, "_utc_now", lambda: datetime(2026, 9, 10, 15, tzinfo=UTC))
    stats = {"current_streak": 4, "best_streak": 6, "last_session_at": last}
    out = main._live_streak(stats, UTC)
    assert out["current_streak"] == current and out["best_streak"] == 6


class _Stats:
    """`user_stats` holding one row; records what a credit writes."""

    def __init__(self, row):
        self.row, self.written = row, None

    def table(self, name):
        db = self

        class Q:
            def select(self, *_a):
                return self

            def eq(self, *_a):
                return self

            def update(self, patch):
                db.written = patch
                return self

            def insert(self, row):
                db.written = row
                return self

            def execute(self):
                return type("R", (), {"data": [db.row] if db.row else []})()
        assert name == "user_stats"
        return Q()


@pytest.mark.parametrize("row", [None, {"user_id": "u1", "total_questions": 5, "total_correct": 3,
                                        "current_streak": 2, "best_streak": 2,
                                        "last_session_at": _at(9)}],
                         ids=["first credit inserts", "next day updates"])
def test_a_credit_writes_the_streak(monkeypatch, row):
    db = _Stats(row)
    monkeypatch.setattr(main, "supabase", db)
    monkeypatch.setattr(main, "_school_timezone", lambda: UTC)
    main._credit_session_to_user_stats("u1", 4, 2, _at(10))
    assert db.written["current_streak"] == (3 if row else 1)
    assert db.written["best_streak"] == (3 if row else 1)
    assert db.written["last_session_at"] == _at(10)
