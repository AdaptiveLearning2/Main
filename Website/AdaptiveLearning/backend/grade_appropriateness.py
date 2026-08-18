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
_VARIABLE_PATTERNS = [
    (re.compile(r"\d+[xyn]\b"),               "coefficient-variable notation"),
    (re.compile(r"solve for\s+[a-z]\b", re.I), "solve-for-a-variable phrasing"),
    (re.compile(r"\b[xyn]\s*="),               "variable on the left of an equation"),
    (re.compile(r"=\s*[xyn]\b"),               "variable on the right of an equation"),
]


def find_violation(question_text, topic, grade_band):
    """Describe why `question_text` is wrong for this band, or None if it is
    fine. None is also the answer for any topic/band with no rule -- absence
    of a rule is not a violation."""
    if grade_band not in FORBIDDEN_BANDS.get(topic, ()):
        return None
    if not question_text:
        return None

    for pattern, description in _VARIABLE_PATTERNS:
        match = pattern.search(question_text)
        if match:
            return (f"{description} ({match.group(0)!r}) is not appropriate "
                    f"for the {grade_band} band of topic {topic!r}")
    return None
