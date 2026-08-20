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
