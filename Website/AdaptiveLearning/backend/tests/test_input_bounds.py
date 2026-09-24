"""What a request body may contain, and which columns a write may reach."""

import os
from zoneinfo import available_timezones

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
    """Sidecar version skew under `forbid` would 422 the whole batch."""
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
                # `_row_or_404` unwraps single() to the row; a list here would fail in the double.
                self._single = True
                return self

            def update(self, obj):
                client.updates.append((table, obj))
                return self

            def execute(self):
                return type("R", (), {"data": row if self._single else [row]})()

        return _Q()


def _declared_fields(model):
    """Every field the model declares, which is what the handler must write."""
    return set(model.model_fields)


def _sample_value(field):
    """Something plausible of the field's own type, for the double below."""
    annotation = getattr(field.annotation, "__args__", (field.annotation,))
    for kind in annotation:
        if kind is bool:
            return True
        if kind is int:
            return 1
        if kind is str:
            return "Ada"
    return "Ada"


class _PayloadCarryingMore:
    """A payload carrying more than the model declares; `.dict()` would leak the extras.

    `__getattr__` answers None for anything not passed.
    """

    def __init__(self, **fields):
        self._fields = fields
        for key, value in fields.items():
            setattr(self, key, value)

    def __getattr__(self, _name):
        return None

    def dict(self):
        return dict(self._fields)


def _payload_for(model, **extra):
    """A double carrying every field `model` declares, plus `extra`."""
    declared = {name: _sample_value(field)
                for name, field in model.model_fields.items()}
    return _PayloadCarryingMore(**declared, **extra)


def test_the_payload_double_answers_for_a_field_it_was_not_given():
    """The double's own contract; the tests below never reach this fallback."""
    payload = _PayloadCarryingMore(display_name="Ada")
    assert payload.display_name == "Ada"
    assert payload.a_field_nobody_passed is None
    assert payload.dict() == {"display_name": "Ada"}


def test_a_profile_update_writes_only_the_columns_it_names(monkeypatch):
    client = _CapturingClient()
    monkeypatch.setattr(main, "get_user", lambda _r: STUDENT)
    monkeypatch.setattr(main, "supabase", client)
    monkeypatch.setattr(main, "_profile", lambda _uid: {"id": STUDENT["id"]})

    main.update_my_profile(
        _payload_for(main.UpdateProfileRequest,
                     role="admin", email="attacker@example.test"),
        None)

    written = [obj for table, obj in client.updates if table == "profiles"]
    assert written, "the profile update never reached the database"
    for obj in written:
        assert "role" not in obj, "a posted role reached profiles.role"
        assert "email" not in obj
        # Equality, not `<=`: a declared field missing from the handler is never stored.
        assert set(obj) - {"updated_at"} == _declared_fields(main.UpdateProfileRequest)


def test_a_class_update_writes_only_the_columns_it_names(monkeypatch):
    client = _CapturingClient()
    monkeypatch.setattr(main, "get_user", lambda _r: TEACHER)
    monkeypatch.setattr(main, "supabase", client)

    main.update_class(
        "class-1",
        _payload_for(main.UpdateClassRequest,
                     teacher_id="someone-else", join_code="AAAAAA"),
        None)

    written = [obj for table, obj in client.updates if table == "classes"]
    assert written, "the class update never reached the database"
    for obj in written:
        assert "teacher_id" not in obj, "a posted teacher_id reached classes"
        assert "join_code" not in obj
        assert set(obj) == _declared_fields(main.UpdateClassRequest)


def test_the_service_role_client_is_why_those_two_tests_exist():
    """The service-role client bypasses column grants, so the handler alone narrows columns."""
    assert main.SERVICE_ROLE_KEY is not None


# ─── section 6: free-text caps ───────────────────────────────────────────

UUID = "b7e1c2d3-4f56-7890-abcd-ef0123456789"
LONG_NAME = "Mrs Abernathy-Whitcombe's Thursday Group"

# Each field gets a realistic value; caps differ per field.
CAPPED = [
    ("StartSessionRequest",   "title",        {},                       LONG_NAME),
    ("CreateClassRequest",    "name",         {},                       LONG_NAME),
    ("UpdateClassRequest",    "name",         {},                       LONG_NAME),
    ("UpdateProfileRequest",  "display_name", {},                       LONG_NAME),
    ("JoinClassRequest",      "join_code",    {},                       "ABC123"),
    ("LinkChildRequest",      "link_code",    {},                       "ABCD2345"),
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
    model = getattr(main, model_name)
    model(**extra, **{field: ordinary})


def test_the_timezone_cap_clears_every_name_it_has_to_accept():
    """Against the installed zone database; the cap has deliberate headroom."""
    names = available_timezones()
    assert names, "no zone database installed; this test cannot mean anything"
    longest = max(names, key=len)
    assert len(longest) <= main._TIMEZONE_MAX, (
        f"{longest!r} is {len(longest)} characters and the cap is "
        f"{main._TIMEZONE_MAX}")


@pytest.mark.parametrize("model_name,field", [
    ("LearningStrategyRequest", "days"),
    ("ChartSummaryRequest",     "days"),
    ("ChartSummaryRequest",     "weeks"),
])
def test_a_range_parameter_is_clamped_by_its_handler_and_not_refused_here(model_name, field):
    """Clamped in the handler, so a `Field(ge=, le=)` bound would turn the clamp into a 422."""
    model = getattr(main, model_name)
    for extreme in (0, -1, 999_999):
        model(**{field: extreme})


@pytest.mark.parametrize("asked,expected_days,expected_weeks", [
    (999_999, 30, main._TREND_MAX_WEEKS),
    (0,       1,  2),
    (-5,      1,  2),
])
def test_the_chart_summary_clamps_both_of_its_ranges(monkeypatch, asked,
                                                     expected_days, expected_weeks):
    """Recorded at `_chart_summary_basis`, where the clamped values are handed on."""
    seen = {}
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "viewer-1"})
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a: None)
    monkeypatch.setattr(main, "_rate_limit_chart_summary", lambda *_a: None)
    # Every key the response builder reads, or the double fails as a KeyError.
    stub_basis = {"face_included": True, "signals_retrieved": True,
                  "trend_retrieved": True, "stats_retrieved": True,
                  "topics_retrieved": True, "consent_retrieved": True,
                  "averages": {}, "trend": {}, "academic": {}, "topics": []}

    def _basis(sid, days, weeks, include_face):
        seen.update(days=days, weeks=weeks)
        return dict(stub_basis)

    monkeypatch.setattr(main, "_chart_summary_basis", _basis)
    monkeypatch.setattr(main, "_rule_based_chart_summary", lambda _b: [])
    monkeypatch.setattr(main, "_feature_flags",
                        lambda: {"chart_summary_llm_enabled": {"enabled": False}})

    answer = main.student_chart_summary(
        "student-1", None,
        main.ChartSummaryRequest(days=asked, weeks=asked))

    assert seen == {"days": expected_days, "weeks": expected_weeks}
    # And on the way out: `basis` tells the caller what range it got.
    assert answer["basis"]["days"] == expected_days
    assert answer["basis"]["weeks"] == expected_weeks


def test_the_acknowledgement_map_is_bounded():
    """The body cap bounds the request, not the number of keys the handler loops over."""
    main.ConsentNoticeAck(through={str(n): "2026-01-01" for n in range(200)})
    with pytest.raises(ValidationError):
        main.ConsentNoticeAck(through={str(n): "2026-01-01" for n in range(201)})


def test_the_topic_list_is_bounded():
    ok = {"mode": "test", "difficulty": "easy"}
    main.StartPracticeSessionRequest(**ok, topics=["ordering"] * 64)
    with pytest.raises(ValidationError):
        main.StartPracticeSessionRequest(**ok, topics=["ordering"] * 65)
