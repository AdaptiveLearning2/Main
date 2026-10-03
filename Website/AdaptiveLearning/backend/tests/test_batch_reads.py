"""Rosters read once for the class or the family, never once per student."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402
from test_access_control import _FakeSupabase  # noqa: E402

TEACHER = {"id": "teacher-1"}


def _class_tables(sessions):
    return {
        "classes": [{"id": "class-1", "teacher_id": "teacher-1"}],
        "class_memberships": [{"class_id": "class-1", "student_id": "kid-a"},
                              {"class_id": "class-1", "student_id": "kid-b"}],
        "profiles": [{"id": "kid-a", "display_name": "Ada"}, {"id": "kid-b", "display_name": "Ben"}],
        "sessions": sessions,
    }


def _session(sid, uid, started, ended="2026-09-01T11:00:00+00:00"):
    return {"id": sid, "user_id": uid, "started_at": started, "ended_at": ended,
            "class_id": "class-1", "title": "t", "questions_answered": 1, "correct_answers": 1,
            "chart_paths": {"focus": "x"}}


def test_a_class_sessions_page_is_one_read_for_every_student(monkeypatch):
    """The page made one request per student (each six reads); this is one sessions call."""
    fake = _FakeSupabase(_class_tables([
        _session("s1", "kid-a", "2026-09-01T09:00:00+00:00"),
        _session("s2", "kid-a", "2026-09-02T09:00:00+00:00"),
        _session("s3", "kid-b", "2026-09-01T10:00:00+00:00"),
    ]))
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "get_user", lambda _r: TEACHER)

    out = main.class_sessions("class-1", None)

    calls = [p for name, p in fake.rpc_calls if name == "recent_sessions_for_users"]
    assert calls == [{"p_user_ids": ["kid-a", "kid-b"], "p_limit": main._RECENT_SESSIONS}]
    assert out["students"] == [{"user_id": "kid-a", "name": "Ada"},
                               {"user_id": "kid-b", "name": "Ben"}]
    assert [s["id"] for s in out["sessions"]["kid-a"]] == ["s2", "s1"], "newest first"
    # Named columns only: `chart_paths` is a storage path nothing renders.
    assert "chart_paths" not in out["sessions"]["kid-a"][0]
    assert out["sessions"]["kid-a"][0]["idle"] is False


def test_a_failed_class_sessions_read_is_unknown_not_empty(monkeypatch):
    """`None` per student: an empty list would say the class ran no sessions."""
    def boom(name, _params):
        return RuntimeError("down") if name == "recent_sessions_for_users" else None
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_class_tables([]), rpc_raises=boom))
    monkeypatch.setattr(main, "get_user", lambda _r: TEACHER)

    out = main.class_sessions("class-1", None)

    assert out["sessions"] == {"kid-a": None, "kid-b": None}


def test_another_teachers_class_sessions_are_refused(monkeypatch):
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_class_tables([])))
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "teacher-2"})
    with pytest.raises(main.HTTPException) as exc:
        main.class_sessions("class-1", None)
    assert exc.value.status_code == 403


def test_the_parent_dashboard_reads_every_childs_consent_and_sessions_once(monkeypatch):
    """Per child, these were one consent read and one sessions read each."""
    fake = _FakeSupabase({
        "parent_child_links": [
            {"parent_id": "parent-1", "child_id": c, "created_at": "2026-01-01"}
            for c in ("kid-a", "kid-b", "kid-c")],
        "user_stats": [], "user_math_performance": [], "signal_consent": [],
        "sessions": [_session("s1", "kid-b", "2026-09-01T09:00:00+00:00")],
    })
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "parent-1"})

    children = main.my_children(None)

    assert fake.table_calls.count("signal_consent") == 1
    assert [n for n, _ in fake.rpc_calls].count("recent_sessions_for_users") == 1
    # The one table read left is the stats' open-session read; per child it was one more each.
    assert fake.table_calls.count("sessions") == 1
    assert [s["id"] for s in next(c for c in children if c["user_id"] == "kid-b")["sessions"]] == ["s1"]
