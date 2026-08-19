"""A code-level backstop on what a generated question actually contains.

Every other grade rule in this codebase moved from "soft prompt instruction"
to "code-level check" because an 8B model does not reliably comply with the
soft version -- topic selection to `_safe_topic`, scenario selection to
`_pick_scenario(difficulty, grade_band)`. `COMPLEXITY_BY_GRADE` and the
lesson-plan text spliced in by `lesson_plan_context` are the two that are
still only prompt-level, and the lesson-plan splice has no backstop at all:
it is plain text appended to a prompt, and nothing downstream checks whether
the model honoured it. This module is that check.

Deliberately narrow. It tests for ONE thing -- algebraic variable notation
reaching a band that must not see it -- because that is the failure the two
known leaks produced (`expressions`' "simplify" scenario, and
`angle_relationships`' solve-for-x scenario), and because it is the one
property that can be detected in generated text with near-zero false
positives. Checks with a real false-positive rate are worse than no check:
they burn retries, and a question rejected for a bad reason is
indistinguishable from a model that cannot follow instructions.

Notably NOT checked, and on purpose:

- **Negative numbers / decimals / fraction notation in `ordering` early.**
  The band's objectives forbid them, but "-" is also a hyphen and a range
  separator ("3-4 values"), and "." is a full stop. Detecting these means
  guessing at intent, and the magnitude rules are already stated per-cell in
  `COMPLEXITY_BY_GRADE`.
- **Magnitude ceilings at all** -- "numbers 1-9" for early/easy, "under 20"
  for early/medium, and so on. Nothing here checks them, so a grade-1
  question asking for 847 + 312 passes. Unlike the operator rule, this is
  not one bound per topic: the ceiling varies per band AND per difficulty
  tier, so a check would have to read `COMPLEXITY_BY_GRADE` back out of
  prose that is written for a model rather than for a parser. Extracting
  every integer and comparing it to a ceiling also mis-fires on the
  numbers that are not operands -- a dataset's item count, a year, "3
  apples". Named here rather than left implicit, because "the output is
  checked" should not be read as "the output is fully checked".
- **Whether the question reflects the lesson-plan text's *content*.** That
  needs a model to judge, which puts an unbounded LLM call on the hot
  question-generation path -- the exact cost the deterministic checks in
  this codebase exist to avoid.

So this narrows the blast radius of a bad lesson plan; it does not verify
that a good one was followed. Seeding `lesson_plans` still needs its effect
confirmed by reading generated output.
"""

import re

# Which bands must never see algebraic variable notation, per topic.
#
# `algebra` is absent deliberately: its own early-band content is one-step
# equations with x ("x + 2 = 5", framed as a missing-number fact), so x is
# legitimate there at every band and a blanket check would reject every
# question the topic exists to ask. The grade gate that matters for algebra
# is `LLM_topic_decider._allowed_topics`, which keeps the topic away from
# grades 1-5 entirely.
#
# `geometry` is early-only: `upper`/`advanced` scenarios legitimately label
# triangle sides a, b, c for the Pythagorean theorem, and `middle` covers
# missing-side problems that may name an unknown.
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
}

# Each pattern is chosen to avoid the one false positive that actually
# occurs in this domain: "x" as a multiplication sign ("6 x 4", "6x4").
#
# - `\d+[xyn]\b` requires the letter to be attached to a digit AND followed
#   by a word boundary, so "2x" matches and "6x4" does not (the 4 kills the
#   boundary). "6 x 4" does not match either -- the space breaks `\d+[xyn]`.
# - "solve for <letter>" is unambiguous phrasing; no arithmetic question
#   uses it.
# - A single letter on either side of "=" is an equation with an unknown.
#   Bounded to x/y/n so a unit abbreviation cannot trip it.
#
# The bare-variable pattern is the one that needs the lookarounds. A
# coefficient of 1 is written without a digit ("Find x if...", "What is x
# plus 5?"), so `\d+[xyn]\b` never sees it and neither does the "=" pair
# when the question has no equals sign. Matching a lone letter is what
# risks the multiplication sign, so it is bounded on both sides: a digit
# immediately before ("6 x 4") or after ("x 4") means multiplication, not
# an unknown.
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

    The whole check at a call site, because the find-it/log-it/`continue`
    block was copied into nine generation files and nine copies of a rule
    drift. Returns a bool so the caller stays one `if ...: continue`.
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

    This is the one magnitude-adjacent rule that IS checkable, and it is
    checked because it was measurably broken: every scenario example in
    `expr_prompt` is written for older students, and on llama3.1:8b a
    few-shot example beat the textual constraint 2 times in 8 (2026-08-18,
    grade 1 / easy -- "Solve 5 + (2 - 1).", "Evaluate (3+2)*4-1.").

    Unlike negatives and decimals -- where "-" is also a hyphen and "." a
    full stop, so detection would be guesswork -- these characters have
    exactly one meaning inside a generated arithmetic expression. The
    prompt also constrains the whole topic to "+", "-", "*", "/", "(", ")",
    so there is no third reading available.

    Difficulty-aware because the early band's own rules are: `hard` admits
    multiplication facts up to 5x5, `easy`/`medium` do not, and none of the
    three admits division or parentheses.
    """
    if "(" in question_text or ")" in question_text:
        return "parentheses"
    if "/" in question_text or "÷" in question_text:
        return "division"
    if difficulty != "hard" and ("*" in question_text or "×" in question_text):
        return "multiplication"
    return None


def find_violation(question_text, topic, grade_band, difficulty=None):
    """Describe why `question_text` is wrong for this band, or None if it is
    fine. None is also the answer for any topic/band with no rule -- absence
    of a rule is not a violation.

    `difficulty` is optional so a caller with no opinion still gets the
    band-level checks; only the early-band `expressions` operator rule reads
    it, and it errs toward the stricter reading when it is absent."""
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
