"""Re-rendering old archives: expired or erased charts are never redrawn, and failed reads refuse."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "x")

import chart_archive  # noqa: E402

COG = [{"ts": "t", "focus": 0.5, "stress": 0.3}]
HEART = [{"ts": "t", "heart_rate_bpm": 70.0, "stress_category": "low", "trusted": True}]
ALL_PATHS = {"cognitive_timeline": "u/s/c.svg", "heart_rate": "u/s/h.svg",
             "stress_pie": "u/s/p.svg", "emotion_pie": None}


@pytest.fixture(autouse=True)
def _nothing_has_expired(monkeypatch):
    """No cutoff yet; the tests about the cutoff pass `expiry` themselves."""
    monkeypatch.setattr(chart_archive, "expiry_cutoff", lambda _client: (None, None))


def _session(sid, paths=ALL_PATHS, started_at="2026-06-10T14:00:00+00:00"):
    return {"id": sid, "user_id": "u1", "chart_paths": paths, "started_at": started_at}


def _fetch_for(table):
    def fetch(_client, session_id):
        cog, face, heart = table[session_id]
        return cog, face, heart
    return fetch


def _spy_archive(monkeypatch):
    calls = []

    def archive(_c, sid, uid, *, only=None, existing_paths=None):
        calls.append((sid, only, existing_paths))
        return {}

    monkeypatch.setattr(chart_archive, "archive_session", archive)
    return calls


def test_only_sessions_with_every_recorded_charts_rows_left_are_rerendered(monkeypatch):
    """Partial expiry: re-rendering would null the heart paths and orphan the last copy."""
    monkeypatch.setattr(chart_archive, "_fetch", _fetch_for({
        "s-live": (COG, [], HEART),
        "s-heart-expired": (COG, [], []),
        "s-all-expired": ([], [], []),
    }))
    calls = _spy_archive(monkeypatch)
    report = chart_archive.rearchive_sessions(
        object(), [_session("s-live"), _session("s-heart-expired"),
                   _session("s-all-expired"), _session("s-never", None)], dry_run=False)
    assert [c[0] for c in calls] == ["s-live"]
    assert report["rerendered"] == 1
    assert report["skipped_expired"] == 2
    assert report["skipped_unarchived"] == 1


def test_a_chart_an_erasure_nulled_stays_null_even_though_its_rows_exist(monkeypatch):
    """A camera erasure nulls both heart charts but leaves the headband's heart rows."""
    monkeypatch.setattr(chart_archive, "_fetch", _fetch_for({"s": (COG, [], HEART)}))
    calls = _spy_archive(monkeypatch)
    erased = {**ALL_PATHS, "heart_rate": None, "stress_pie": None}
    chart_archive.rearchive_sessions(object(), [_session("s", erased)], dry_run=False)
    assert calls == [("s", {"cognitive_timeline"}, erased)]


def test_archive_session_keeps_the_existing_entry_for_a_chart_outside_only(monkeypatch):
    uploaded = []

    class _Storage:
        def upload(self, path, file, file_options):
            uploaded.append(path)

    class _Client:
        class storage:
            @staticmethod
            def from_(_bucket):
                return _Storage()

        def table(self, _name):
            return self

        def update(self, payload):
            self.payload = payload
            return self

        def eq(self, *_a):
            return self

        # The erasure re-check: no erasures.
        def select(self, *_a):
            return self

        def gte(self, *_a):
            return self

        def execute(self):
            return type("R", (), {"data": []})()

    monkeypatch.setattr(chart_archive, "_fetch", lambda _c, _s: (COG, [], HEART))
    client = _Client()
    paths = chart_archive.archive_session(client, "s", "u1", only={"cognitive_timeline"},
                                          existing_paths={"heart_rate": None,
                                                          "stress_pie": None})
    assert uploaded == ["u1/s/cognitive_timeline.svg"]
    assert paths["heart_rate"] is None and paths["stress_pie"] is None


def test_a_dry_run_renders_nothing_and_names_what_it_would(monkeypatch):
    monkeypatch.setattr(chart_archive, "_fetch", _fetch_for({"s": (COG, [], HEART)}))
    monkeypatch.setattr(chart_archive, "archive_session",
                        lambda *_a, **_k: pytest.fail("rendered on a dry run"))
    report = chart_archive.rearchive_sessions(object(), [_session("s")])
    assert report["dry_run"] is True
    assert report["would_rerender"] == ["s"]


def test_a_failed_read_is_counted_and_refuses_the_run_past_the_cap(monkeypatch):
    """A failed read looks like an expired session, so it must not be skipped."""
    def fetch(_c, _sid):
        raise RuntimeError("db down")

    monkeypatch.setattr(chart_archive, "_fetch", fetch)
    calls = _spy_archive(monkeypatch)
    rows = [_session(f"s{i}") for i in range(10)]
    report = chart_archive.rearchive_sessions(object(), rows, dry_run=False,
                                              max_read_failures=2)
    assert calls == []
    assert report["skipped_expired"] == 0
    # Refused *at* the cap, not only past it.
    assert report["read_failures"] == 2 and report["refused"]


def test_one_render_failure_does_not_stop_the_run(monkeypatch):
    monkeypatch.setattr(chart_archive, "_fetch", lambda _c, _s: (COG, [], HEART))
    calls = []

    def flaky(_c, sid, _u, **_k):
        calls.append(sid)
        if sid == "s-a":
            raise RuntimeError("storage down")
        return {}

    monkeypatch.setattr(chart_archive, "archive_session", flaky)
    report = chart_archive.rearchive_sessions(
        object(), [_session("s-a"), _session("s-b")], dry_run=False)
    assert calls == ["s-a", "s-b"]
    assert report["failed"] == 1 and report["rerendered"] == 1


def test_a_run_stops_at_the_rerender_cap_and_says_so(monkeypatch):
    monkeypatch.setattr(chart_archive, "_fetch", lambda _c, _s: (COG, [], HEART))
    calls = _spy_archive(monkeypatch)
    rows = [_session(f"s{i}") for i in range(5)]
    report = chart_archive.rearchive_sessions(object(), rows, dry_run=False, max_rerenders=2)
    assert len(calls) == 2 and report["hit_cap"] is True


def test_the_tool_takes_the_oldest_sessions_first_and_names_its_target():
    import inspect
    import rearchive_session_charts as tool
    src = inspect.getsource(tool.main)
    assert 'order("ended_at", desc=False)' in src
    assert "target:" in src and "netloc" in src


def test_a_capped_run_reports_its_cursor_and_the_tool_resumes_from_it(monkeypatch):
    monkeypatch.setattr(chart_archive, "_fetch", lambda _c, _s: (COG, [], HEART))
    _spy_archive(monkeypatch)
    rows = [{**_session(f"s{i}"), "ended_at": f"2026-09-0{i + 1}T10:00:00+00:00"} for i in range(4)]
    report = chart_archive.rearchive_sessions(object(), rows, dry_run=False, max_rerenders=2)
    assert report["hit_cap"] and report["last_ended_at"] == "2026-09-02T10:00:00+00:00"
    import inspect
    import rearchive_session_charts as tool
    src = inspect.getsource(tool.main)
    assert 'query.gt("ended_at", args.after)' in src and "--after" in src


def test_the_cursor_does_not_pass_a_session_whose_read_failed(monkeypatch):
    """Otherwise the resume skips it and it drops from the backfill for good."""
    def fetch(_c, sid):
        if sid == "s0":
            raise RuntimeError("db down")
        return COG, [], HEART

    monkeypatch.setattr(chart_archive, "_fetch", fetch)
    _spy_archive(monkeypatch)
    rows = [{**_session(f"s{i}"), "ended_at": f"2026-09-0{i + 1}T10:00:00+00:00"} for i in range(2)]
    report = chart_archive.rearchive_sessions(object(), rows, dry_run=False, max_read_failures=5)
    assert report["last_ended_at"] == "2026-09-02T10:00:00+00:00"
    only_failed = chart_archive.rearchive_sessions(object(), rows[:1], dry_run=False,
                                                   max_read_failures=5)
    assert only_failed["last_ended_at"] is None


def test_the_cursor_does_not_pass_a_session_whose_render_failed(monkeypatch):
    """The cursor advances only after the archive call succeeds."""
    monkeypatch.setattr(chart_archive, "_fetch", lambda _c, _s: (COG, [], HEART))

    def flaky(_c, sid, _u, **_k):
        if sid == "s1":
            raise RuntimeError("storage down")
        return {}

    monkeypatch.setattr(chart_archive, "archive_session", flaky)
    rows = [{**_session(f"s{i}"), "ended_at": f"2026-09-0{i + 1}T10:00:00+00:00"} for i in range(2)]
    report = chart_archive.rearchive_sessions(object(), rows, dry_run=False)
    assert report["failed"] == 1 and report["rerendered"] == 1
    assert report["last_ended_at"] == "2026-09-01T10:00:00+00:00"


# ── the expiry cutoff, not "any rows left", decides what may be re-read ─────

def _sydney(cutoff="2026-06-15"):
    from datetime import date
    from zoneinfo import ZoneInfo
    return date.fromisoformat(cutoff), ZoneInfo("Australia/Sydney")


def test_a_session_begun_on_an_expired_day_is_not_rerendered_even_with_rows_left(monkeypatch):
    """Expiry deletes per student-day in capped batches, so surviving rows prove nothing."""
    monkeypatch.setattr(chart_archive, "_fetch", _fetch_for({
        "s-expired-day": (COG, [], HEART), "s-after": (COG, [], HEART)}))
    calls = _spy_archive(monkeypatch)
    report = chart_archive.rearchive_sessions(object(), [
        # 20:00 UTC on the 14th is the morning of the 15th in Sydney: on the cutoff.
        _session("s-expired-day", started_at="2026-06-14T20:00:00+00:00"),
        # 15:00 UTC on the 15th is the 16th there: after it, though UTC says the 15th.
        _session("s-after", started_at="2026-06-15T15:00:00+00:00"),
    ], dry_run=False, expiry=_sydney())
    assert [c[0] for c in calls] == ["s-after"]
    assert report["skipped_expired"] == 1


def test_a_run_that_cannot_read_the_cutoff_refuses_rather_than_assuming_none(monkeypatch):
    def unreadable(_client):
        raise RuntimeError("rpc unavailable")

    monkeypatch.setattr(chart_archive, "expiry_cutoff", unreadable)
    calls = _spy_archive(monkeypatch)
    report = chart_archive.rearchive_sessions(object(), [_session("s")], dry_run=False)
    assert calls == []
    assert "cutoff" in report["refused"]


def test_an_unreadable_start_counts_as_expired():
    assert chart_archive.touches_expired_day("not a time", _sydney())
    assert chart_archive.touches_expired_day(None, _sydney())
    assert not chart_archive.touches_expired_day("not a time", (None, None))


class _CutoffClient:
    def __init__(self, cutoff, timezone):
        self._cutoff, self._timezone = cutoff, timezone

    def rpc(self, name, _params):
        assert name == "expired_signal_cutoff"
        return type("Q", (), {"execute": lambda _s: type("R", (), {"data": self._cutoff})()})()

    def table(self, name):
        assert name == "retention_window"
        rows = [{"timezone": self._timezone}]
        q = type("Q", (), {})()
        q.select = q.limit = lambda *_a, **_k: q
        q.execute = lambda: type("R", (), {"data": rows})()
        return q


@pytest.mark.parametrize("cutoff, tz, expected", [
    ("2026-06-15", "Australia/Sydney", "2026-06-15 Australia/Sydney"),
    (None, "Australia/Sydney", "none"),        # nothing has expired yet
    ("2026-06-15", None, "none"),               # expire_signal_rows deletes nothing then
])
def test_the_cutoff_is_read_with_the_schools_timezone(cutoff, tz, expected, monkeypatch):
    monkeypatch.undo()          # the real reader, not the autouse stand-in
    got = chart_archive.expiry_cutoff(_CutoffClient(cutoff, tz))
    assert (f"{got[0]} {got[1]}" if got[0] else "none") == expected


def test_a_misspelt_timezone_is_a_failed_read_not_an_empty_cutoff(monkeypatch):
    monkeypatch.undo()
    with pytest.raises(Exception):
        chart_archive.expiry_cutoff(_CutoffClient("2026-06-15", "Australia/Sidney"))
