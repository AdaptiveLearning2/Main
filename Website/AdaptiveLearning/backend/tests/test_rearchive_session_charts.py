"""Re-rendering archives written before a chart changed.

The guards are the point. A session whose per-sample rows have expired must
never be re-rendered, because the archive is then the last picture of it;
expiry is per channel, so the check is per chart; a chart an erasure nulled
must stay null; a failed read looks exactly like an expired session and so
refuses the run rather than skipping; and one run may only overwrite so many
live sessions' objects.
"""

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


def _session(sid, paths=ALL_PATHS):
    return {"id": sid, "user_id": "u1", "chart_paths": paths}


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
    """Partial expiry: heart rows gone, cognitive rows remain. Re-rendering
    would null the heart paths and orphan the objects, the last copy."""
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
    """A camera erasure removes both heart charts and leaves the headband's
    heart rows. Re-rendered from the rows, the erased pictures come back."""
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

        def execute(self):
            return None

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
    """A failed read is indistinguishable from an expired session, which is
    the skip case -- so it must not be skipped."""
    def fetch(_c, _sid):
        raise RuntimeError("db down")

    monkeypatch.setattr(chart_archive, "_fetch", fetch)
    calls = _spy_archive(monkeypatch)
    rows = [_session(f"s{i}") for i in range(10)]
    report = chart_archive.rearchive_sessions(object(), rows, dry_run=False,
                                              max_read_failures=2)
    assert calls == []
    assert report["skipped_expired"] == 0
    # Refused *at* the cap: five failing reads against a cap of five used to
    # return refused unset and exit 0, exactly what a run with no work does.
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
    """Recorded before the read, a session whose read failed was passed
    over by the resume and dropped from the backfill for good."""
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
