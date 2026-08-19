"""One reading of a grade string, shared by everything that gates on it.

Every grade rule here used to match the exact strings the frontend dropdown
happens to emit ("1st grade", "2nd grade", ...) and fall through to the
*most permissive* branch for anything else. Nothing in the schema enforces
that format -- `profiles.grade_level` is free text -- so "Grade 1" reached
`_allowed_topics`'s final `return ALL_TOPICS` and a 1st grader became
eligible for algebra, and reached `_grade_band`'s final `return "advanced"`
and got advanced content in whatever topic was chosen.

Two properties this file exists to hold:

- **A grade is read numerically where it can be**, so "Grade 1", "1st
  Grade", "grade 1" and "1" are one grade rather than one grade and three
  unknowns.
- **An unreadable grade is treated as the youngest, not the oldest.** The
  asymmetry is the same one `signal_fusion` documents: withholding a topic
  from a student who could have handled it costs one easy question, while
  serving algebra to a 1st grader is the failure the gate exists to
  prevent. `_grade_band` inherits it -- an unreadable grade gets "early"
  content, not "advanced".

That is a deliberate reversal of what the old fall-through did, and it is
why this is one function rather than ten copies.
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

    Bands are unchanged -- 1-3 early, 4-6 middle, 7-8 upper, above that
    advanced -- but an unreadable grade now lands in "early" rather than
    "advanced". See the module docstring for why that direction.
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
