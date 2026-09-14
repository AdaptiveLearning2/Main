"""Re-rendering archives written before a chart changed.

The guard is the point: a session whose per-sample rows have expired must
never be re-rendered, because the archive is then the last picture of it
and re-running the archiver would upload empty charts over it.
"""

from __future__ import annotations

import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "x")

import chart_archive  # noqa: E402

LIVE = {"id": "s-live", "user_id": "u1", "chart_paths": {"cognitive_timeline": "u1/s-live/c.svg"}}
EXPIRED = {"id": "s-expired", "user_id": "u1", "chart_paths": {"cognitive_timeline": "u1/s-expired/c.svg"}}
NEVER = {"id": "s-never", "user_id": "u1", "chart_paths": None}


def _fake_fetch(_client, session_id):
    if session_id == "s-live":
        return ([{"ts": "t", "focus": 0.5, "stress": 0.3}], [], [])
    return ([], [], [])


def test_only_sessions_with_rows_left_are_rerendered(monkeypatch):
    monkeypatch.setattr(chart_archive, "_fetch", _fake_fetch)
    archived = []
    monkeypatch.setattr(chart_archive, "archive_session",
                        lambda _c, sid, uid: archived.append((sid, uid)) or {})
    report = chart_archive.rearchive_sessions(object(), [LIVE, EXPIRED, NEVER], dry_run=False)
    assert archived == [("s-live", "u1")]
    assert report["rerendered"] == 1
    assert report["skipped_expired"] == 1
    assert report["skipped_unarchived"] == 1


def test_a_dry_run_renders_nothing_and_names_what_it_would(monkeypatch):
    monkeypatch.setattr(chart_archive, "_fetch", _fake_fetch)
    monkeypatch.setattr(chart_archive, "archive_session",
                        lambda *_a: (_ for _ in ()).throw(AssertionError("rendered on a dry run")))
    report = chart_archive.rearchive_sessions(object(), [LIVE, EXPIRED])
    assert report["dry_run"] is True
    assert report["would_rerender"] == ["s-live"]
    assert report["rerendered"] == 0


def test_one_failure_does_not_stop_the_run(monkeypatch):
    monkeypatch.setattr(chart_archive, "_fetch", lambda _c, _s: ([{"ts": "t"}], [], []))
    calls = []

    def flaky(_c, sid, _u):
        calls.append(sid)
        if sid == "s-a":
            raise RuntimeError("storage down")
        return {}

    monkeypatch.setattr(chart_archive, "archive_session", flaky)
    rows = [{"id": "s-a", "user_id": "u", "chart_paths": {"x": "p"}},
            {"id": "s-b", "user_id": "u", "chart_paths": {"x": "p"}}]
    report = chart_archive.rearchive_sessions(object(), rows, dry_run=False)
    assert calls == ["s-a", "s-b"]
    assert report["failed"] == 1 and report["rerendered"] == 1
