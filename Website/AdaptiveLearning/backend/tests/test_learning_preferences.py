"""Learning preferences: endpoint bounds, the failed-read fallback, and the session prewarm."""

import os
from datetime import datetime, timedelta

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402
from pydantic import ValidationError  # noqa: E402

import main  # noqa: E402

STUDENT = {"id": "student-1", "user_metadata": {}}


class _Client:
    """Just enough Supabase for `start_session` to reach the prewarm."""

    def __init__(self):
        self.inserted = []

    def table(self, name):
        client = self
        table = name

        class _Q:
            def select(self, *_a, **_k):  return self
            def eq(self, *_a):            return self
            def is_(self, *_a):           return self
            def order(self, *_a, **_k):   return self
            def limit(self, *_a):         return self
            def update(self, *_a):        return self
            def single(self):             return self

            def insert(self, obj):
                client.inserted.append((table, obj))
                self._insert = obj
                return self

            def execute(self):
                if getattr(self, "_insert", None) is not None:
                    return type("R", (), {"data": [{"id": "session-1", **self._insert}]})()
                return type("R", (), {"data": []})()

        return _Q()


@pytest.fixture
def _stubbed(monkeypatch):
    """A student with a saved preference, and a prewarm that only records."""
    calls = []
    monkeypatch.setattr(main, "get_user", lambda _r: STUDENT)
    monkeypatch.setattr(main, "supabase", _Client())
    monkeypatch.setattr(main, "_ensure_queue",
                        lambda uid, grade, bias, sid=None: calls.append(bias))
    monkeypatch.setattr(main, "_rollup_session_days", lambda *_a, **_k: None)
    monkeypatch.setattr(main, "_discard_if_nothing_recorded", lambda *_a, **_k: False)
    return calls


def _with_profile(monkeypatch, **fields):
    base = {"id": STUDENT["id"], "display_name": "S", "email": "", "role": "student",
            "grade_level": "5th Grade", "difficulty_bias": 0,
            "session_duration_minutes": 15, "practice_reminders": True}
    monkeypatch.setattr(main, "_profile", lambda _uid: {**base, **fields})


@pytest.mark.parametrize("saved", [-1, 0, 1])
def test_a_session_prewarms_at_the_students_own_difficulty(_stubbed, monkeypatch, saved):
    """QUEUE_SIZE is not pinned: `_ensure_queue` is stubbed, so it is never read."""
    _with_profile(monkeypatch, difficulty_bias=saved)

    main.start_session(main.StartSessionRequest(title=None), request=None)

    assert _stubbed == [saved]


def test_a_student_with_no_grade_is_prewarmed_at_the_grade_the_topic_list_shows(_stubbed, monkeypatch):
    _with_profile(monkeypatch, grade_level=None)
    grades = []
    monkeypatch.setattr(main, "_ensure_queue", lambda uid, grade, bias, sid=None: grades.append(grade))

    main.start_session(main.StartSessionRequest(title=None), request=None)

    assert main.list_topics(grade=grades[0]) == main.list_topics(grade=None)


def test_a_corrupt_saved_bias_cannot_shift_difficulty_off_the_end(_stubbed, monkeypatch):
    """Second guard behind the CHECK: `_shift_difficulty` clamps rather than raising."""
    _with_profile(monkeypatch, difficulty_bias=7)

    main.start_session(main.StartSessionRequest(title=None), request=None)

    assert _stubbed == [1]


def test_a_failed_profile_read_still_carries_usable_preferences():
    """Missing keys would render as a student who turned everything off."""
    fallback = main._profile("nobody")

    assert fallback["difficulty_bias"] == 0
    assert fallback["session_duration_minutes"] == 15
    assert fallback["practice_reminders"] is True


@pytest.mark.parametrize("field,value", [
    ("difficulty_bias", 2),
    ("difficulty_bias", -2),
    ("session_duration_minutes", 0),
    ("session_duration_minutes", 10_000),
])
def test_the_endpoint_refuses_values_the_column_would_refuse(field, value):
    """A 422 naming the field, rather than a 500 from the database CHECK."""
    with pytest.raises(ValidationError):
        main.UpdateProfileRequest(**{field: value})


_STORED = {"id": STUDENT["id"], "display_name": "S", "role": "student", "difficulty_bias": 0,
           "practice_reminders": True, "updated_at": None, "avatar_path": "not for the page"}


class _ProfileTable:
    """`profiles` for `update_my_profile`: an update answers with the row as stored, as PostgREST does.

    `row=None` is an account with no profile row; `reads_fail` fails every read that is not an update.
    """

    def __init__(self, row=_STORED, reads_fail=False):
        self.row = dict(row) if row is not None else None
        self.written, self.reads = [], 0
        self._reads_fail = reads_fail

    def table(self, _name):
        client = self

        class _Q:
            def update(self, obj):
                client.written.append(dict(obj))
                self._update = obj
                return self

            def select(self, *_a, **_k): return self
            def eq(self, *_a):           return self
            def limit(self, *_a):        return self

            def execute(self):
                if getattr(self, "_update", None) is not None:
                    if client.row is not None:
                        client.row.update(self._update)
                    return type("R", (), {"data": [dict(client.row)] if client.row else []})()
                client.reads += 1
                if client._reads_fail:
                    raise RuntimeError("profiles read failed")
                return type("R", (), {"data": [dict(client.row)] if client.row else []})()

        return _Q()


def _save(monkeypatch, client, **fields):
    monkeypatch.setattr(main, "get_user", lambda _r: STUDENT)
    monkeypatch.setattr(main, "supabase", client)
    return main.update_my_profile(main.UpdateProfileRequest(**fields), None)


@pytest.mark.parametrize("field,value", [
    ("difficulty_bias", 0),
    ("practice_reminders", False),
])
def test_the_falsy_settings_are_sent_rather_than_filtered_out(field, value, monkeypatch):
    """0 and False are real choices; driven through the handler, not a restated filter."""
    client = _ProfileTable()
    _save(monkeypatch, client, **{field: value})
    assert [{k: v for k, v in w.items() if k != "updated_at"} for w in client.written] == [{field: value}]


def test_a_profile_edit_stamps_updated_at_in_utc_with_its_offset(monkeypatch):
    client = _ProfileTable()
    _save(monkeypatch, client, practice_reminders=True)
    stamps = [datetime.fromisoformat(w["updated_at"]).utcoffset() for w in client.written]
    assert stamps == [timedelta(0)], client.written


def test_a_save_answers_with_the_row_as_stored_in_its_named_columns(monkeypatch):
    saved = _save(monkeypatch, _ProfileTable(), practice_reminders=False)
    assert saved["practice_reminders"] is False
    assert "avatar_path" not in saved and "updated_at" not in saved


def test_a_save_reads_nothing_after_its_update(monkeypatch):
    """A read failing after a landed save would make the page restore the old value and save it back."""
    client = _ProfileTable(reads_fail=True)
    assert _save(monkeypatch, client, practice_reminders=False)["practice_reminders"] is False
    assert client.reads == 0


def test_a_save_for_an_account_with_no_profile_row_is_a_404(monkeypatch):
    with pytest.raises(main.HTTPException) as exc:
        _save(monkeypatch, _ProfileTable(row=None), practice_reminders=False)
    assert exc.value.status_code == 404
