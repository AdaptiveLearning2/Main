"""One reading of the free-text `profiles.grade_level`, shared by everything that gates on it.

A grade is read numerically ("Grade 1", "1st Grade" and "1" are one grade), and an
unreadable grade is treated as grade 1, never the oldest; kindergarten (0) must be named.
"""

import re
import unicodedata

# Labels with no digit in them.
_NAMED_GRADES = {
    "kindergarten": 0,
    "pre-k":        0,
    "prek":         0,
    "highschool":   9,
    "high school":  9,
    "college":      13,
    "university":   13,
}

# Outside this range a number is not a grade ("2026 cohort").
_MIN_GRADE, _MAX_GRADE = 0, 13

# What a student or class with no grade is served, everywhere: the same grade 1 as an unreadable one.
DEFAULT_GRADE = "1st Grade"


def grade_number(grade):
    """The numeric school grade in `grade`, or None if it cannot be read.

    None is the signal to treat the student as grade 1, not to guess.
    """
    text = (grade or "").strip().lower()
    if not text:
        return None

    for label, number in _NAMED_GRADES.items():
        if label in text:
            return number

    match = re.search(r"\d+", text)
    if not match:
        return None

    number = int(match.group(0))
    return number if _MIN_GRADE <= number <= _MAX_GRADE else None


def grade_band(grade):
    """The four-band bucket the generation files scale content by; unreadable is "early"."""
    number = grade_number(grade)
    if number is None:
        return "early"
    if number <= 3:
        return "early"
    if number <= 6:
        return "middle"
    if number <= 8:
        return "upper"
    return "advanced"


# ─── the canonical form a model prompt is allowed to see ──────────────────
# Prompts get a label rebuilt from the number, so no client-written text reaches them.
# Must round-trip: grade_number(CANONICAL_GRADE_LABELS[n]) == n (a test pins it).
CANONICAL_GRADE_LABELS = {
    0:  "Kindergarten",
    1:  "1st Grade",
    2:  "2nd Grade",
    3:  "3rd Grade",
    4:  "4th Grade",
    5:  "5th Grade",
    6:  "6th Grade",
    7:  "7th Grade",
    8:  "8th Grade",
    9:  "9th Grade",
    10: "10th Grade",
    11: "11th Grade",
    12: "12th Grade",
    13: "College",
}

# Never a guessed grade, and never the unreadable input echoed back.
UNKNOWN_GRADE_LABEL = "unspecified"


def grade_for_prompt(grade):
    """The only form of `grade` that may be interpolated into a prompt."""
    return CANONICAL_GRADE_LABELS.get(grade_number(grade), UNKNOWN_GRADE_LABEL)


# ─── the edge check, layer 1 ─────────────────────────────────────────────
# Refuse unreadable grades at write time (422). The cap bounds storage/display;
# `grade_for_prompt` is the injection defence.
GRADE_MAX_LENGTH = 40


def validated_grade(value):
    """`value` unchanged if it is a grade this system can read, else raise.

    None and "" return None (clearing the field). Raises ValueError, a 422 via `field_validator`.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("grade must be text")

    text = value.strip()
    if not text:
        return None
    if len(text) > GRADE_MAX_LENGTH:
        raise ValueError(
            f"grade is too long (max {GRADE_MAX_LENGTH} characters)")
    # One line only: "5th Grade\r\n..." passes the cap and reads as grade 5. Cf covers
    # right-to-left overrides; Zl/Zp are the other line breaks.
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in text):
        raise ValueError("grade must be a single line of plain text")
    if grade_number(text) is None:
        raise ValueError(
            "grade is not a grade level this system recognises "
            "(for example '5th Grade', 'Kindergarten' or 'College')")
    return text
