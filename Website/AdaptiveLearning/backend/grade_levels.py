"""One reading of a grade string, shared by everything that gates on it.

`profiles.grade_level` is free text, not constrained to the frontend
dropdown's exact strings ("1st grade", "2nd grade", ...), so any code
matching those strings and falling through to the most permissive branch on
anything else is a hole: "Grade 1" could become eligible for algebra, or get
advanced content in whatever topic was chosen.

Two properties this file exists to hold:

- **A grade is read numerically where it can be**, so "Grade 1", "1st
  Grade", "grade 1" and "1" are one grade, not one grade plus three unknowns.
- **An unreadable grade is treated as the youngest, not the oldest.** Same
  asymmetry as `signal_fusion`: withholding a topic from a student who could
  have handled it costs one easy question, while serving algebra to a 1st
  grader is the failure the gate exists to prevent. `_grade_band` inherits
  this -- an unreadable grade gets "early" content, not "advanced".
"""

import re
import unicodedata

# Labels with no digit in them. Kindergarten sits below 1st grade; the two
# post-8th labels are what the frontend's dropdown offers above "8th grade".
_NAMED_GRADES = {
    "kindergarten": 0,
    "pre-k":        0,
    "prek":         0,
    "highschool":   9,
    "high school":  9,
    "college":      13,
    "university":   13,
}

# A grade number outside this range is not a school grade, so a stray number
# in the string ("2026 cohort") does not silently become grade 2026.
_MIN_GRADE, _MAX_GRADE = 0, 13


def grade_number(grade):
    """The numeric school grade in `grade`, or None if it cannot be read.

    None is the signal to treat the student as the youngest, not to guess.
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
    """The four-band bucket the generation files scale content by.

    1-3 early, 4-6 middle, 7-8 upper, above that advanced. An unreadable
    grade lands in "early" -- see the module docstring for why.
    """
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
#
# `grade` reaches nineteen f-string prompts as a raw string -- the topic
# decider's `Student Grade Level = {grade}` and one `a {grade} student` line
# in each of the seventeen generators. Every one of those strings is
# client-supplied: `PUT /api/profile/me` and the two class endpoints write it,
# `POST /api/practice-sessions/start` takes it directly, and
# `GET /api/generate-question?grade=` hands it to the decider without so much
# as a length cap. A value carrying newlines can close the line it sits on and
# open an instruction of its own, in a prompt whose whole job is to be
# followed.
#
# Escaping that string is the weaker answer, and it is not what this does. A
# grade is not free text: the only thing any consumer wants from it is the
# number `grade_number` already reads. So the prompt is handed a label
# *rebuilt from that number*, and injection stops being something to filter
# for -- it is unrepresentable, because nothing the caller wrote survives.
#
# The labels round-trip: `grade_number(CANONICAL_GRADE_LABELS[n]) == n` for
# every n, which is what makes this substitution behaviour-preserving. Every
# other consumer -- `_allowed_topics`, `grade_band`, each generator's
# `GRADE_OVERRIDES` -- keys on the number, never on the string, so passing the
# canonical label down changes nothing but what the model reads. A test pins
# the round-trip; break it and the grade gates move.
#
# Two dropdown labels are relabelled on the way through: "Highschool" becomes
# "9th Grade" and "College" stays "College", since both already collapse to
# one number for every gate. The prompt's own GRADE RULES are written in
# numbers ("Grades 7+"), so the numeric label is the one it can act on.
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

# What the prompt is told when the grade cannot be read. Not a guessed grade:
# an unreadable one is already treated as the youngest by `grade_band`, and
# asserting "1st Grade" in the prompt would be a claim nobody made. Echoing
# the unreadable string back is the option this exists to refuse -- it is
# exactly the value that could not mean anything, and the only one that could
# carry a payload.
UNKNOWN_GRADE_LABEL = "unspecified"


def grade_for_prompt(grade):
    """The only form of `grade` that may be interpolated into a prompt.

    One of `CANONICAL_GRADE_LABELS`' fourteen values, or
    `UNKNOWN_GRADE_LABEL`. Nothing the caller wrote reaches the string.
    """
    return CANONICAL_GRADE_LABELS.get(grade_number(grade), UNKNOWN_GRADE_LABEL)


# ─── the edge check, layer 1 ─────────────────────────────────────────────
#
# `grade_for_prompt` is what makes the prompt safe, and it holds on its own --
# but it holds at the *last* step, and a value that reaches it has already
# been stored, echoed onto a teacher's class list and a student's profile
# badge, and read back by whatever is written next. So the edge refuses what
# the system cannot read at all: a grade `grade_number` returns None for means
# nothing to any gate here, and a 422 naming the field beats storing it and
# quietly treating that student as the youngest for the rest of the year.
#
# The cap is not the security property -- forty characters is plenty of room
# for a sentence -- it bounds what gets stored and displayed. The layer that
# makes an injection unrepresentable is `grade_for_prompt`; this one keeps the
# column honest.
GRADE_MAX_LENGTH = 40


def validated_grade(value):
    """`value` unchanged if it is a grade this system can read, else raise.

    None and "" pass through as None -- clearing the field is not a bad grade.
    Raises ValueError, so a Pydantic `field_validator` surfaces it as a 422
    naming the field.
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
    # A grade is one line. Without this, "5th Grade\r\nOUTPUT FORMAT..." is
    # seventeen characters and reads as grade 5, so it cleared both the cap
    # and the number check -- the exact shape this is written against, and
    # the reason a length cap is not a substitute for a structural one. The
    # categories are control (Cc), format (Cf, which carries the
    # right-to-left overrides), and the line and paragraph separators (Zl,
    # Zp) that `\n` is not the only spelling of.
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") for ch in text):
        raise ValueError("grade must be a single line of plain text")
    if grade_number(text) is None:
        raise ValueError(
            "grade is not a grade level this system recognises "
            "(for example '5th Grade', 'Kindergarten' or 'College')")
    return text
