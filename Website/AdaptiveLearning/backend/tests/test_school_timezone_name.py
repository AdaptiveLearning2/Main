"""A mistyped school timezone reaches every RPC as UTC, as `_school_timezone` degrades, never raw."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402


class _Rpc:
    def __init__(self):
        self.params = []

    def rpc(self, name, params):
        self.params.append((name, params))
        return type("E", (), {"execute": lambda s: type("R", (), {"data": []})()})()


@pytest.fixture
def rpc(monkeypatch):
    r = _Rpc()
    monkeypatch.setattr(main, "supabase", r)
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": main.WINDOW_UNREADABLE, "timezone": "America/New_Yrok"})
    return r


@pytest.mark.parametrize("call", [
    lambda: main._summary_rpc("student_signal_summary", {"p_student_id": "s", "p_days": 7}, True, True),
    lambda: main._class_signal_totals(["s"], 7, True, True),
    lambda: main._class_signal_trend(["s"], 7, True, True),
], ids=["summary", "cohort totals", "cohort trend"])
def test_a_mistyped_zone_is_sent_as_utc(rpc, call):
    """Postgres raises on an unknown zone, so every summary came back unretrieved."""
    call()
    assert rpc.params and rpc.params[0][1]["p_timezone"] == "UTC"


def test_a_real_zone_is_sent_by_name(rpc, monkeypatch):
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": main.WINDOW_OPEN, "timezone": "America/New_York"})
    main._summary_rpc("student_signal_summary", {"p_student_id": "s", "p_days": 7}, True, True)
    assert rpc.params[0][1]["p_timezone"] == "America/New_York"


def test_the_last_resort_utc_has_a_name():
    """`timezone.utc` is `_school_timezone`'s fallback without tzdata, and has no `.key`."""
    from datetime import timezone
    assert main._tz_name(timezone.utc) == "UTC"
