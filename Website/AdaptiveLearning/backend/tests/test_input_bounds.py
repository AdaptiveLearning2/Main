"""What a request body may contain, and which columns a write may reach.

Sections 5 and 6 of the security plan, together because they are two halves of
one question. A model decides which keys survive parsing; a handler decides
which columns a write touches. Getting only the first right is what made this
worth doing:

`update_my_profile` and `update_class` built their database update out of
`payload.dict()` wholesale. Both write through the **service-role** client,
which bypasses RLS *and* the column grants `20260824010000` revoked from
`anon`/`authenticated` -- so the migration that makes `profiles.role`
non-client-writable does not reach those statements. The only thing stopping a
posted `role` from landing in the column was that the model happened not to
declare the field. True today, and one field away from being false.

So the tests here are of two kinds, and the second kind is the one with teeth:

- that the models refuse what they do not declare, and bound their free text;
- that the handlers write named columns *even when handed a payload carrying
  more than they declare* -- which is the future state the model guard exists
  to prevent, simulated, because a test against today's model cannot tell a
  named-column write from `payload.dict()`. Both produce the same five keys.
"""

import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402
from pydantic import BaseModel, ValidationError  # noqa: E402

import main  # noqa: E402

STUDENT = {"id": "student-1", "user_metadata": {}}
TEACHER = {"id": "teacher-1", "user_metadata": {}}


# The six models on the sidecar's ingest path, exempt from `extra="forbid"` by
# name rather than by a rule that could quietly widen.
INGEST_MODELS = {
    "CognitiveSample", "CognitiveBatch",
    "FaceSample", "FaceBatch",
    "HeartSample", "HeartBatch",
}


def _request_models():
    return {
        name: obj for name, obj in vars(main).items()
        if isinstance(obj, type) and issubclass(obj, BaseModel)
        and obj.__module__ == "main" and obj is not main.StrictModel
    }


# ─── section 5: what a model accepts ─────────────────────────────────────

def test_every_request_model_forbids_what_it_does_not_declare():
    lenient = sorted(
        name for name, model in _request_models().items()
        if model.model_config.get("extra") != "forbid"
    )
    assert lenient == sorted(INGEST_MODELS), (
        "a model outside the ingest path accepts undeclared fields; inherit "
        "StrictModel, or add it to INGEST_MODELS with the reason")


def test_the_exempt_models_are_the_ones_a_sidecar_posts_to():
    """The exemption is a real trade, so it is named rather than derived.

    A sidecar runs on a student's laptop and updates on its own schedule, so a
    field it gained before this backend did is ordinary version skew. Under
    `forbid` that skew 422s the *whole batch*, losing every valid sample
    travelling with it -- which is why `CognitiveBatch.samples` is already
    `list[Any]` validated per sample. The cost of staying lenient is a column
    that reads "not measured" forever, and a different test already covers it.
    """
    for name in INGEST_MODELS:
        assert name in _request_models(), f"{name} no longer exists"


@pytest.mark.parametrize("name", sorted(INGEST_MODELS & {"FaceSample", "HeartSample"}))
def test_a_sidecar_field_this_backend_does_not_know_is_dropped_not_refused(name):
    model = getattr(main, name)
    base = {"source": "muse_optics"} if name == "HeartSample" else {}
    built = model(**base, a_field_from_a_newer_sidecar=1.0)
    assert not hasattr(built, "a_field_from_a_newer_sidecar")


def test_a_student_cannot_post_a_role_to_their_own_profile():
    with pytest.raises(ValidationError):
        main.UpdateProfileRequest(display_name="S", role="admin")


def test_a_teacher_cannot_post_a_teacher_id_to_a_class():
    with pytest.raises(ValidationError):
        main.UpdateClassRequest(name="4B", teacher_id=TEACHER["id"])


# ─── section 5: which columns a write reaches ────────────────────────────

class _CapturingClient:
    """Records every `update()` payload, by table."""

    def __init__(self):
        self.updates = []

    def table(self, name):
        client, table = self, name
        row = {"id": "row-1", "teacher_id": TEACHER["id"],
               "name": "4B", "grade_level": "5th Grade"}

        class _Q:
            def __init__(self):
                self._single = False

            def select(self, *_a, **_k):  return self
            def eq(self, *_a):            return self

            def single(self):
                # `_row_or_404` unwraps a single() read to the row itself, so
                # a fake that always hands back a list makes the handler
                # subscript a list with a column name -- a failure in the
                # double, dressed as one in the code.
                self._single = True
                return self

            def update(self, obj):
                client.updates.append((table, obj))
                return self

            def execute(self):
                return type("R", (), {"data": row if self._single else [row]})()

        return _Q()


class _PayloadCarryingMore:
    """A payload with a field the handler's model does not declare.

    This is the future the model guard exists to prevent, and the only way to
    tell a named-column write from `payload.dict()`: against today's model the
    two produce identical keys, so a test using the real model passes either
    way. `.dict()` is provided precisely so the old implementation would work
    and would leak.
    """

    def __init__(self, **fields):
        self._fields = fields
        for key, value in fields.items():
            setattr(self, key, value)

    def dict(self):
        return dict(self._fields)


def test_a_profile_update_writes_only_the_columns_it_names(monkeypatch):
    client = _CapturingClient()
    monkeypatch.setattr(main, "get_user", lambda _r: STUDENT)
    monkeypatch.setattr(main, "supabase", client)
    monkeypatch.setattr(main, "_profile", lambda _uid: {"id": STUDENT["id"]})

    main.update_my_profile(
        _PayloadCarryingMore(
            display_name="Ada", grade_level="5th Grade", difficulty_bias=1,
            session_duration_minutes=15, practice_reminders=True,
            role="admin", email="attacker@example.test"),
        None)

    written = [obj for table, obj in client.updates if table == "profiles"]
    assert written, "the profile update never reached the database"
    for obj in written:
        assert "role" not in obj, "a posted role reached profiles.role"
        assert "email" not in obj
        assert set(obj) <= {"display_name", "grade_level", "difficulty_bias",
                            "session_duration_minutes", "practice_reminders",
                            "updated_at"}


def test_a_class_update_writes_only_the_columns_it_names(monkeypatch):
    client = _CapturingClient()
    monkeypatch.setattr(main, "get_user", lambda _r: TEACHER)
    monkeypatch.setattr(main, "supabase", client)

    main.update_class(
        "class-1",
        _PayloadCarryingMore(name="4B", grade_level="5th Grade",
                             teacher_id="someone-else", join_code="AAAAAA"),
        None)

    written = [obj for table, obj in client.updates if table == "classes"]
    assert written, "the class update never reached the database"
    for obj in written:
        assert "teacher_id" not in obj, "a posted teacher_id reached classes"
        assert "join_code" not in obj
        assert set(obj) <= {"name", "grade_level"}


def test_the_service_role_client_is_why_those_two_tests_exist():
    """Stated once, so the reasoning is not only in a comment.

    `20260824010000` revokes UPDATE on `profiles.role` from the client roles.
    Every write in this app goes through the service-role client, which is not
    one of those roles -- so that migration constrains PostgREST and not this
    backend, and the handler is the only thing narrowing the column set.
    """
    assert main.SERVICE_ROLE_KEY is not None


# ─── section 6: free-text caps ───────────────────────────────────────────

UUID = "b7e1c2d3-4f56-7890-abcd-ef0123456789"
LONG_NAME = "Mrs Abernathy-Whitcombe's Thursday Group"

# The real value each field carries matters: a generic sample long enough to
# exercise a 100-character name is *over* the 32 a join code or a channel gets,
# so one shared string would fail the caps that are doing their job.
CAPPED = [
    ("StartSessionRequest",   "title",        {},                       LONG_NAME),
    ("CreateClassRequest",    "name",         {},                       LONG_NAME),
    ("UpdateClassRequest",    "name",         {},                       LONG_NAME),
    ("UpdateProfileRequest",  "display_name", {},                       LONG_NAME),
    ("JoinClassRequest",      "join_code",    {},                       "ABC123"),
    ("LinkChildRequest",      "child_id",     {},                       UUID),
    ("AnswerPayload",         "question_id",  {"selected_index": 0, "correct": True}, UUID),
    ("PracticeAnswerPayload", "question_id",  {"selected_index": 0, "correct": True}, UUID),
    ("PracticeViewPayload",   "question_id",  {},                       UUID),
    ("EegSessionRequest",     "session_id",   {},                       UUID),
    ("ErasureRequest",        "channel",      {},                       "headband_optical"),
    ("RetentionWindowUpdate", "timezone",     {"enforced": False},
     "America/Argentina/ComodRivadavia"),
]


@pytest.mark.parametrize("model_name,field,extra,ordinary", CAPPED)
def test_a_free_text_field_is_bounded(model_name, field, extra, ordinary):
    model = getattr(main, model_name)
    with pytest.raises(ValidationError):
        model(**extra, **{field: "x" * 5000})


@pytest.mark.parametrize("model_name,field,extra,ordinary", CAPPED)
def test_an_ordinary_value_still_fits(model_name, field, extra, ordinary):
    """A cap that refuses real input is a broken endpoint, not a bound.

    The timezone case is the tightest of these on purpose: the longest name in
    the IANA database is 32 characters, which is the cap, so this fails if the
    bound is ever tightened by one.
    """
    model = getattr(main, model_name)
    model(**extra, **{field: ordinary})


@pytest.mark.parametrize("model_name,field", [
    ("LearningStrategyRequest", "days"),
    ("ChartSummaryRequest",     "days"),
    ("ChartSummaryRequest",     "weeks"),
])
def test_a_range_parameter_is_clamped_by_its_handler_and_not_refused_here(model_name, field):
    """A decision, recorded where reversing it means editing a test.

    The obvious §6 move is `Field(ge=..., le=...)` on each of these, and it is
    wrong: all three are already clamped in their handlers
    (`max(1, min(payload.days, 30))`), which is this codebase's convention for
    a caller-supplied range, and `test_learning_strategies_clamps_the_day_range`
    pins it at 999 -> 30 and 0 -> 1. Adding a field bound turns that documented
    clamp into a 422 for the same input: two bounds over one number, the
    stricter winning silently. Caught by that test, which is the only reason
    this is a comment rather than a regression.
    """
    model = getattr(main, model_name)
    for extreme in (0, -1, 999_999):
        model(**{field: extreme})


def test_the_acknowledgement_map_is_bounded():
    """The one field a client may grow freely. The body cap bounds the request;
    nothing else bounded the number of keys the handler loops over."""
    main.ConsentNoticeAck(through={str(n): "2026-01-01" for n in range(200)})
    with pytest.raises(ValidationError):
        main.ConsentNoticeAck(through={str(n): "2026-01-01" for n in range(201)})


def test_the_topic_list_is_bounded():
    ok = {"mode": "test", "difficulty": "easy"}
    main.StartPracticeSessionRequest(**ok, topics=["ordering"] * 64)
    with pytest.raises(ValidationError):
        main.StartPracticeSessionRequest(**ok, topics=["ordering"] * 65)
