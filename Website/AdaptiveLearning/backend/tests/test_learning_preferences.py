"""The Preferences tab controls something now.

All three wrote `al_prefs` to localStorage and nothing read it back: difficulty
never reached the LLM decider, duration was enforced by nothing, and there was
no notification system for the notification toggle to switch. Decoration, the
same as the Connect buttons removed from the Devices tab.

What is asserted here is the half that can silently regress -- the endpoint's
bounds, the failed-read fallback, and the prewarm, which serves the *first*
questions of every session and so is exactly where a difficulty setting would
appear not to work.
"""

import os

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
    """The prewarm ran at a hardcoded 0.

    QUEUE_SIZE questions are generated before the student answers anything and
    served first, so a setting ignored here is a setting that does nothing for
    the opening of every session -- the part of a lesson most likely to be the
    only part.
    """
    _with_profile(monkeypatch, difficulty_bias=saved)

    main.start_session(main.StartSessionRequest(title=None), request=None)

    assert _stubbed == [saved]


def test_a_corrupt_saved_bias_cannot_shift_difficulty_off_the_end(_stubbed, monkeypatch):
    """`profiles` carries a FOR ALL own-row policy, so a student reaches this
    column through PostgREST directly. The CHECK constraint is the real guard;
    this is the second one, because `_shift_difficulty` clamps rather than
    raising -- a bias of 7 would read as "always hard" for ever, which is a
    wrong value that works."""
    _with_profile(monkeypatch, difficulty_bias=7)

    main.start_session(main.StartSessionRequest(title=None), request=None)

    assert _stubbed == [1]


def test_a_failed_profile_read_still_carries_usable_preferences():
    """`_profile` swallows its exception and returns a default, and the caller
    cannot tell that from a real profile. Without the preference keys the page
    gets `undefined` for three controls and renders them unset -- which reads as
    a student who turned everything off."""
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
    """Bounded here as well as by the CHECK, so the answer is a 422 naming the
    field rather than a 500 out of the client library."""
    with pytest.raises(ValidationError):
        main.UpdateProfileRequest(**{field: value})


@pytest.mark.parametrize("field,value", [
    ("difficulty_bias", 0),
    ("practice_reminders", False),
])
def test_the_falsy_settings_are_sent_rather_than_filtered_out(field, value):
    """`update_my_profile` drops fields that are None, which is right -- but 0 is
    the adaptive bias and False is reminders off, and both are choices. If they
    were filtered as falsy instead, neither could ever be saved: a student could
    turn reminders on and never off again."""
    payload = main.UpdateProfileRequest(**{field: value})
    fields = {k: v for k, v in payload.dict().items() if v is not None}

    assert fields == {field: value}
