"""A code-level backstop on what a generated question actually contains.

Prompt instructions alone aren't enough: an 8B model doesn't reliably comply.
`COMPLEXITY_BY_GRADE` and the lesson-plan text spliced in by
`lesson_plan_context` are still only prompt-level and have no backstop --
nothing downstream checks whether the model honoured them. This module is
that check.

Deliberately narrow. It tests for ONE thing -- algebraic variable notation
reaching a band that must not see it -- because that's the failure two known
leaks produced, and the one property detectable in generated text with
near-zero false positives. A check with real false positives is worse than
no check: it burns retries, and looks identical to a model that just can't
follow instructions.

Notably NOT checked, and on purpose:

- **Negative numbers / decimals / fraction notation in `ordering` early.**
  "-" is also a hyphen and a range separator, and "." is a full stop, so
  detecting these means guessing at intent.
- **Magnitude ceilings** -- "numbers 1-9" for early/easy, and so on. A
  grade-1 question asking for 847 + 312 passes. The ceiling varies per band
  *and* difficulty, and extracting every integer would mis-fire on numbers
  that aren't operands (a dataset's item count, a year, "3 apples").
- **Whether the question reflects the lesson-plan text's *content*.** That
  needs a model to judge, which puts an unbounded LLM call on the hot
  generation path.

So this narrows the blast radius of a bad lesson plan; it doesn't verify a
good one was followed. Seeding `lesson_plans` still needs its effect
confirmed by reading generated output.
"""

import re

# Which bands must never see algebraic variable notation, per topic.
#
# `algebra` is absent deliberately: its own early-band content is one-step
# equations with x, so x is legitimate there at every band. The grade gate
# that matters for algebra is `LLM_topic_decider._allowed_topics`, which
# keeps the topic away from grades 1-5 entirely.
#
# `geometry` is early-only: `upper`/`advanced` scenarios legitimately label
# triangle sides a, b, c, and `middle` may name an unknown side.
FORBIDDEN_BANDS = {
    "expressions":         {"early", "middle"},
    "angle_relationships": {"early", "middle"},
    "geometry":            {"early"},
    "ordering":            {"early", "middle"},
    "rationals":           {"early", "middle"},
    "mean":                {"early", "middle"},
    "median":              {"early", "middle"},
    "mode":                {"early", "middle"},
    "probability":         {"early", "middle"},
    # Both are elementary topics whose whole point is that the unknown is a
    # `?` and not an `x`. `missing_number` is one notation away from `algebra`
    # (6.EE.7), so a reply that reaches for `2x` has left the topic.
    "missing_number":      {"early", "middle"},
    "patterns":            {"early", "middle"},
}

# Each pattern avoids the one false positive that actually occurs here: "x"
# as a multiplication sign ("6 x 4", "6x4").
#
# - `\d+[xyn]\b` requires the letter attached to a digit AND followed by a
#   word boundary, so "2x" matches and "6x4" doesn't (the 4 kills the
#   boundary); "6 x 4" doesn't match either since the space breaks it.
# - "solve for <letter>" is unambiguous phrasing.
# - A single letter on either side of "=" is an equation with an unknown.
#   Bounded to x/y/n so a unit abbreviation can't trip it.
#
# The bare-variable pattern needs the lookarounds because a coefficient of 1
# is written without a digit ("Find x if..."), so the other patterns miss
# it. Matching a lone letter risks the multiplication sign, so it's bounded
# on both sides: a digit immediately before or after means multiplication.
_VARIABLE_PATTERNS = [
    (re.compile(r"\d+[xyn]\b"),               "coefficient-variable notation"),
    (re.compile(r"solve for\s+[a-z]\b", re.I), "solve-for-a-variable phrasing"),
    (re.compile(r"\b[xyn]\s*="),               "variable on the left of an equation"),
    (re.compile(r"=\s*[xyn]\b"),               "variable on the right of an equation"),
    (re.compile(r"(?<!\d\s)(?<!\d)\b[xyn]\b(?!\s*\d)"),
     "a variable standing alone"),
]


def refuse(question_text, topic, grade_band, difficulty=None, attempt=None):
    """True -- having logged why -- if this question must be regenerated.

    The whole check lives here so it isn't copied into nine generation files.
    Returns a bool so the caller stays one `if ...: continue`.
    """
    reason = find_violation(question_text, topic, grade_band, difficulty)
    if not reason:
        return False
    prefix = f"[Attempt {attempt}] " if attempt is not None else ""
    print(f"{prefix}Grade-inappropriate: {reason}")
    return True


def _operator_violation(question_text, difficulty):
    """Forbidden arithmetic operators in an early-band `expressions`
    question, or None.

    Checked because it was measurably broken: every scenario example in
    `expr_prompt` is written for older students, and on llama3.1:8b a
    few-shot example beat the textual constraint 2 times in 8 (2026-08-18,
    grade 1 / easy).

    Unlike negatives and decimals, where "-" is also a hyphen and "." a full
    stop, these characters have exactly one meaning in a generated
    expression, so detection here isn't guesswork.

    Difficulty-aware because the early band's own rules are: `hard` admits
    multiplication facts up to 5x5, `easy`/`medium` don't, and none admits
    division or parentheses.
    """
    if "(" in question_text or ")" in question_text:
        return "parentheses"
    if "/" in question_text or "÷" in question_text:
        return "division"
    if difficulty != "hard" and ("*" in question_text or "×" in question_text):
        return "multiplication"
    return None


def find_violation(question_text, topic, grade_band, difficulty=None):
    """Describe why `question_text` is wrong for this band, or None if it's
    fine. None is also the answer for any topic/band with no rule -- absence
    of a rule isn't a violation.

    `difficulty` is optional so a caller with no opinion still gets the
    band-level checks; only the early-band `expressions` operator rule reads
    it, and errs toward the stricter reading when absent."""
    if not question_text:
        return None

    if topic == "expressions" and grade_band == "early":
        found = _operator_violation(question_text, difficulty)
        if found:
            return (f"{found} is not appropriate for the early band of topic "
                    f"'expressions' at {difficulty or 'unspecified'} difficulty")

    if grade_band not in FORBIDDEN_BANDS.get(topic, ()):
        return None

    for pattern, description in _VARIABLE_PATTERNS:
        match = pattern.search(question_text)
        if match:
            return (f"{description} ({match.group(0)!r}) is not appropriate "
                    f"for the {grade_band} band of topic {topic!r}")
    return None
