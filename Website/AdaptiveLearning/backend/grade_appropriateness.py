"""A code-level backstop on what a generated question contains; prompts alone don't bind an 8B model.

Deliberately narrow: only checks for algebraic variable notation (and early-band
`expressions` operators) reaching a band that must not see it, because those are
detectable with near-zero false positives. Magnitudes, negatives/decimals and
lesson-plan content are not checked. See docs/question-generation.md.
"""

import re

# Bands that must never see variable notation, per topic. `algebra` is absent (x is its content;
# `_allowed_topics` gates it by grade); `geometry` is early-only since later bands label sides.
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
    # The unknown here is a `?`, not an `x`; a `2x` reply has left the topic.
    "missing_number":      {"early", "middle"},
    "patterns":            {"early", "middle"},
    "graphs":              {"early", "middle"},
    "shape_fractions":     {"early", "middle"},
}

# Each avoids "x" as a multiplication sign ("6 x 4", "6x4"): a digit adjacent on either side
# means multiplication. Bounded to x/y/n so unit abbreviations can't trip them.
_VARIABLE_PATTERNS = [
    (re.compile(r"\d+[xyn]\b"),               "coefficient-variable notation"),
    (re.compile(r"solve for\s+[a-z]\b", re.I), "solve-for-a-variable phrasing"),
    (re.compile(r"\b[xyn]\s*="),               "variable on the left of an equation"),
    (re.compile(r"=\s*[xyn]\b"),               "variable on the right of an equation"),
    (re.compile(r"(?<!\d\s)(?<!\d)\b[xyn]\b(?!\s*\d)"),
     "a variable standing alone"),
]


def refuse(question_text, topic, grade_band, difficulty=None, attempt=None):
    """True -- having logged why -- if this question must be regenerated."""
    reason = find_violation(question_text, topic, grade_band, difficulty)
    if not reason:
        return False
    prefix = f"[Attempt {attempt}] " if attempt is not None else ""
    print(f"{prefix}Grade-inappropriate: {reason}")
    return True


def _operator_violation(question_text, difficulty):
    """Forbidden arithmetic operators in an early-band `expressions` question, or None.

    Only `hard` admits multiplication; no early difficulty admits division or parentheses.
    """
    if "(" in question_text or ")" in question_text:
        return "parentheses"
    if "/" in question_text or "÷" in question_text:
        return "division"
    if difficulty != "hard" and ("*" in question_text or "×" in question_text):
        return "multiplication"
    return None


def find_violation(question_text, topic, grade_band, difficulty=None):
    """Why `question_text` is wrong for this band, or None (also for a topic/band with no rule).

    `difficulty` is read only by the early `expressions` operator rule; absent means stricter."""
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
