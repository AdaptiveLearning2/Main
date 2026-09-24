"""Does the question a student SEES describe the data that gets SCORED?

Compares `question_text` against the structured field the solver uses, inside the retry
loops, so a mismatch regenerates. Both checks fail OPEN: None whenever the text cannot be
read confidently, since a false rejection burns retries. They catch clear contradictions only.
"""

import re
from fractions import Fraction

# A fraction is one token, not two numbers ("3/4" is compared with "0.27" by value).
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:\s*/\s*\d+)?")

# The prompts put the dataset after a colon ("were: 8, 4, 12").
_LIST_AFTER_COLON = re.compile(
    rf":\s*({_NUMBER.pattern}(?:\s*,\s*{_NUMBER.pattern})+)")

# "1 1/2" is two tokens to `_NUMBER`, so mixed numbers fail open.
_MIXED_NUMBER = re.compile(r"\d+\s+\d+\s*/\s*\d+")

# Complement wording; whole words, so "cannot" or a category "Nothing" cannot trip it.
_NEGATION = re.compile(r"\b(not|isn't|is not|other than|neither)\b", re.I)


def _as_floats(values):
    """The values as floats (compared by value, as the solvers do), or None if any is not a number."""
    out = []
    for v in values:
        s = str(v).strip()
        if not _NUMBER.fullmatch(s):
            return None          # operators, labels, ranges -- not comparable
        s = s.replace(" ", "")
        try:
            out.append(float(Fraction(s)) if "/" in s else float(s))
        except (ValueError, ZeroDivisionError, OverflowError):
            # float() of a huge Fraction raises OverflowError; nothing here may raise.
            return None
    return out


# Why the dataset check did or did not reach a comparison, so measurement can tell
# "compared and agreed" from "found nothing to compare" (an inert check looks perfect).
ENGAGED_AGREED = "engaged_agreed"
ENGAGED_MISMATCH = "engaged_mismatch"
INERT_NO_INPUT = "inert_no_input"
INERT_SCORED_NOT_COMPARABLE = "inert_scored_not_comparable"
INERT_MIXED_NUMBER = "inert_mixed_number"
INERT_NO_LIST_IN_TEXT = "inert_no_list_in_text"
INERT_SHOWN_NOT_COMPARABLE = "inert_shown_not_comparable"


def dataset_check(question_text, values):
    """(state, reason) for the dataset comparison; `reason` is non-None only for ENGAGED_MISMATCH."""
    if not question_text or not values:
        return INERT_NO_INPUT, None
    scored = _as_floats(values)
    if scored is None or len(scored) < 2:
        return INERT_SCORED_NOT_COMPARABLE, None

    if _MIXED_NUMBER.search(question_text):
        return INERT_MIXED_NUMBER, None

    matches = _LIST_AFTER_COLON.findall(question_text)
    if not matches:
        return INERT_NO_LIST_IN_TEXT, None
    shown = _as_floats(_NUMBER.findall(matches[-1]))
    if shown is None or len(shown) < 2:
        return INERT_SHOWN_NOT_COMPARABLE, None

    if sorted(shown) != sorted(scored):
        return ENGAGED_MISMATCH, (
            f"the question shows {shown} but the answer is computed from "
            f"{scored} -- the student would be marked against data they "
            f"were not given")
    return ENGAGED_AGREED, None


def dataset_mismatch(question_text, values):
    """Reason the dataset in `question_text` differs from `values`, or None.

    Only the list after the last colon is compared; no such list returns None.
    """
    return dataset_check(question_text, values)[1]


def negation_mismatch(question_text, scenario):
    """Reason a probability question's wording disagrees with its scenario, or None.

    `not_probability_of` scores 1 - p, so text and scenario must agree on negation both ways.
    """
    if not question_text or scenario not in ("probability_of", "not_probability_of"):
        return None
    negated_text = bool(_NEGATION.search(question_text))
    negated_scenario = scenario == "not_probability_of"
    if negated_text == negated_scenario:
        return None
    if negated_scenario:
        return ("the question asks for a plain probability but the scenario is "
                "'not_probability_of', so the complement would be scored")
    return ("the question asks for the complement but the scenario is "
            "'probability_of', so the wrong side would be scored")
