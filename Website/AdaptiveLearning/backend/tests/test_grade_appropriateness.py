"""The backstop on what a generated question contains, and the four ways a
lesson-plan cell can decline to contribute anything.

Both halves exist because a prompt instruction is not an enforcement
mechanism against an 8B model -- the same reason `_safe_topic` and
`_pick_scenario` exist. What is asserted here is narrow on purpose: the
detector's value is entirely in its false-positive rate, since a check that
rejects good questions burns retries and is indistinguishable from a model
that cannot follow instructions. So the "must not flag" half of this file is
the load-bearing half, and `6 x 4` is the case it exists for.
"""

import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import grade_appropriateness as ga  # noqa: E402
import lesson_plan_context as lpc  # noqa: E402


# --- the detector: what it must catch ------------------------------------

@pytest.mark.parametrize("text,topic,band", [
    # The two documented leaks this was written for.
    ("Simplify 2x + 3x.", "expressions", "early"),
    ("Simplify 2x + 3x.", "expressions", "middle"),
    ("Two angles are complementary: (x + 10) and (2x - 20). Find x.",
     "angle_relationships", "middle"),
    # Variable notation reaching a topic that never legitimately uses it.
    ("If x = 5, order these values.", "ordering", "early"),
    ("Solve for n: the mean is 12.", "mean", "middle"),
])
def test_variable_notation_is_refused_where_the_band_forbids_it(text, topic, band):
    assert ga.find_violation(text, topic, band) is not None


# --- the detector: what it must NOT catch --------------------------------

@pytest.mark.parametrize("text,topic,band", [
    # "x" as a multiplication sign, spaced and unspaced. This is the reason
    # the patterns are anchored the way they are: a naive /x/ would reject
    # every early-band multiplication question ever generated.
    ("What is 6 x 4?", "expressions", "early"),
    ("What is 6x4?", "expressions", "early"),
    # Ordinary arithmetic that happens to contain the letters x, y or n.
    ("Find the next number: 2, 4, 6, ?", "ordering", "early"),
    ("There are 5 boxes and 3 more. How many?", "expressions", "early"),
    ("A rectangle is 3 yards by 4 yards. What is its area?", "geometry", "early"),
    ("Evaluate (4+6)*3-5.", "expressions", "middle"),
    # Bands that are allowed the notation.
    ("Simplify 2x + 3x.", "expressions", "upper"),
    ("A right triangle has legs a and b. Find c.", "geometry", "upper"),
])
def test_ordinary_questions_are_not_refused(text, topic, band):
    assert ga.find_violation(text, topic, band) is None


def test_algebra_is_exempt_at_every_band():
    """algebra's own early-band content is one-step equations with x, framed
    as a missing-number fact. A blanket rule would reject every question the
    topic exists to ask, so the topic is absent from FORBIDDEN_BANDS -- and
    the gate that protects grades 1-5 from algebra is `_allowed_topics`,
    which stops the topic being chosen at all."""
    assert "algebra" not in ga.FORBIDDEN_BANDS
    for band in ("early", "middle", "upper", "advanced"):
        assert ga.find_violation("Solve for x: x + 2 = 5.", "algebra", band) is None


# --- early-band expressions: forbidden operators -------------------------
#
# Measured, not hypothetical. Against the seeded lesson plans on
# llama3.1:8b (2026-08-18, grade 1 / easy), 2 of 8 generated questions came
# back with parentheses -- "Solve 5 + (2 - 1)." -- and a separate run gave
# "Evaluate (3+2)*4-1.", both while the prompt in the same call forbade
# them. The scenario examples in expr_prompt are written for older students
# and the model followed their shape over the constraint.

@pytest.mark.parametrize("text,difficulty", [
    ("Solve 5 + (2 - 1).",   "easy"),    # the real observed failure
    ("Evaluate (3+2)*4-1.",  "easy"),    # the other real observed failure
    ("What is 8 / 2 + 1?",   "easy"),    # division, never allowed early
    ("What is 8 / 2 + 1?",   "hard"),    # not even at hard
    ("What is 3 * 4 + 1?",   "easy"),    # multiplication above easy's rule
    ("What is 3 * 4 + 1?",   "medium"),
    ("What is 3 * 4 + 1?",   None),      # absent difficulty reads as strict
])
def test_forbidden_operators_are_refused_for_early_expressions(text, difficulty):
    assert ga.find_violation(text, "expressions", "early", difficulty) is not None


@pytest.mark.parametrize("text,difficulty", [
    ("What is 7 + 8 - 4?",  "easy"),
    ("What is 9 minus 3 plus 2?", "easy"),
    ("Solve 14 + 9 - 3.",   "medium"),
    # early/hard admits multiplication facts, so this one is legitimate.
    ("What is 3 * 4 + 1?",  "hard"),
])
def test_permitted_early_expressions_are_not_refused(text, difficulty):
    assert ga.find_violation(text, "expressions", "early", difficulty) is None


@pytest.mark.parametrize("band", ["middle", "upper", "advanced"])
def test_older_bands_keep_their_operators(band):
    """The operator rule is early-band only. Parentheses and multiplication
    are the whole point of `order_of_operations`, which middle band upward
    is meant to see -- a rule that leaked upward would reject every
    question that scenario exists to ask."""
    assert ga.find_violation("Solve (2+15)*8-(49-23)+19.",
                             "expressions", band, "hard") is None


def test_a_topic_with_no_rule_is_never_a_violation():
    assert ga.find_violation("Simplify 2x + 3x.", "not_a_real_topic", "early") is None


def test_empty_question_text_is_not_a_violation():
    """A missing question_text is a JSON problem, caught by the key
    validation above this check -- not something to report as a grade
    violation, which would send a reader looking in the wrong place."""
    for empty in (None, ""):
        assert ga.find_violation(empty, "expressions", "early") is None


# --- the four ways a cell contributes nothing ----------------------------

class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, data, raises):
        self._data, self._raises = data, raises

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        if self._raises:
            raise RuntimeError("connection refused")
        return _FakeResponse(self._data)


class _FakeClient:
    def __init__(self, data, raises=False):
        self._data, self._raises = data, raises

    def table(self, *a, **k):
        return _FakeQuery(self._data, self._raises)


@pytest.fixture(autouse=True)
def _clear_lesson_plan_cache():
    """The module caches for 30s, so a test reading a different fake client
    would otherwise get the previous test's answer."""
    lpc._cache.clear()
    yield
    lpc._cache.clear()


@pytest.mark.parametrize("client,expected_reason,expects_text", [
    (_FakeClient([{"objectives": "Compare whole numbers.", "notes": None}]),
     lpc.FOUND, True),
    (_FakeClient([]),                                     lpc.NO_ROW, False),
    (_FakeClient([{"objectives": "", "notes": None}]),    lpc.BLANK_ROW, False),
    (_FakeClient(None, raises=True),                      lpc.READ_FAILED, False),
    (None,                                                lpc.NO_CREDENTIALS, False),
])
def test_every_way_of_contributing_nothing_is_named_separately(
        monkeypatch, client, expected_reason, expects_text):
    """An unseeded cell, a half-finished edit, an outage and a misconfigured
    process all degrade to the same prompt -- and all used to be the same
    silent None. They are operationally different problems, so they get
    different names."""
    monkeypatch.setattr(lpc, "_get_client", lambda: client)

    text = lpc.get_lesson_context("ordering", "early")
    lpc._cache.clear()
    reason = lpc.lookup_reason("ordering", "early")

    assert reason == expected_reason
    assert (text is not None) is expects_text


@pytest.mark.parametrize("client", [
    _FakeClient(None, raises=True),
    None,
])
def test_a_transient_failure_is_not_cached(monkeypatch, client):
    """Caching an outage would keep answering None for the TTL after the
    database came back, and caching absent credentials would outlive
    credentials that are loaded later in the process's life."""
    monkeypatch.setattr(lpc, "_get_client", lambda: client)
    lpc.get_lesson_context("ordering", "early")
    assert ("ordering", "early") not in lpc._cache


def test_lesson_plan_text_is_clamped(monkeypatch):
    """Dashboard-authored text still reaches a prompt, so it is bounded like
    every other prompt input here."""
    monkeypatch.setattr(lpc, "_get_client",
                        lambda: _FakeClient([{"objectives": "x" * 9000, "notes": None}]))
    assert len(lpc.get_lesson_context("ordering", "early")) == lpc._MAX_CONTEXT_CHARS


def test_a_failed_lookup_leaves_the_prompt_untouched(monkeypatch):
    """The whole module fails open: grounding is an enrichment, never a gate,
    so nothing it does may block a question from being generated."""
    monkeypatch.setattr(lpc, "_get_client", lambda: _FakeClient([]))
    prompt = "original prompt"
    assert lpc.append_lesson_context(prompt, "ordering", "early") == prompt
