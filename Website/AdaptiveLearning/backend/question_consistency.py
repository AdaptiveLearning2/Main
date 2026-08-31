"""Does the question a student SEES describe the data that gets SCORED?

Every generator here returns two things that are supposed to agree: the
`question_text` shown to the student, and a structured field (`variables`,
`values`, `items`/`scenario`) the solver computes the answer from. Nothing
checked that they agreed, and an 8B model doesn't reliably keep them in step.

Measured 2026-08-19 on llama3.1:8b, 2 wrong answers in 12 generated:

  * mode -- shown "8, 4, 12, 16, 4, 14, 8, 10, 20, 4", answered [8, 4].
    4 occurs three times and 8 twice, so the only mode is 4; the scored
    `variables` were not the numbers on screen.
  * probability -- shown "...what is the probability of selecting an EDM
    band?" over 17+23+14+15 = 69 bands, answered 18/23. That is 54/69 --
    the COMPLEMENT. The text asked a positive question and the JSON said
    `scenario: not_probability_of`.

This is worse than a malformed question: the student sees something
answerable, answers it correctly, and is marked wrong. So both checks run
inside the existing retry loops and regenerate rather than patching.

**Both fail OPEN.** They return None whenever the text cannot be read
confidently, because a false rejection burns retries and is
indistinguishable from a model that cannot follow instructions -- the same
reasoning `grade_appropriateness` is built on. These catch a clear
contradiction; they are not a proof of agreement.
"""

import re
from fractions import Fraction

# A fraction is one token, not two numbers. `ordering` routinely scores "3/4"
# alongside "0.27", and those ARE comparable -- the values are parsed to floats
# and sorted on those, so a fraction and a decimal sit on one scale. Reading
# "3/4" as a bare 3 and 4 made this check inert on half the ordering questions
# sampled live (2026-08-21).
#
# (The parse moved into the bounded worker, so `solve_ordering` now receives
# numbers rather than calling `float(sympify(v))` itself. What matters here is
# unchanged: one token, one value.)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?(?:\s*/\s*\d+)?")

# "The numbers of hours were: 8, 4, 12" -- these prompts put the dataset
# after a colon, which is what makes locating it reliable enough to compare.
_LIST_AFTER_COLON = re.compile(
    rf":\s*({_NUMBER.pattern}(?:\s*,\s*{_NUMBER.pattern})+)")

# "1 1/2" is one value to a reader and two tokens to the regex above, so a
# text using mixed numbers cannot be tokenised confidently -- fail open
# rather than report the tokenisation as a dataset disagreement.
_MIXED_NUMBER = re.compile(r"\d+\s+\d+\s*/\s*\d+")

# Wording that makes a question ask for the complement. Bounded to whole
# words so "cannot" or a category called "Nothing" cannot trip it.
_NEGATION = re.compile(r"\b(not|isn't|is not|other than|neither)\b", re.I)


def _as_floats(values):
    """The values as floats, or None if any one of them is not a number.

    Compared by VALUE, not by token, because that is what the solvers do:
    a question showing 4/5 and scoring 0.8 agrees, and so does 32/40.
    """
    out = []
    for v in values:
        s = str(v).strip()
        if not _NUMBER.fullmatch(s):
            return None          # operators, labels, ranges -- not comparable
        s = s.replace(" ", "")
        try:
            out.append(float(Fraction(s)) if "/" in s else float(s))
        except (ValueError, ZeroDivisionError, OverflowError):
            # OverflowError is specific to the fraction path: float() of an
            # absurd decimal degrades to inf, but float() of a Fraction with
            # an oversized numerator raises. Nothing in this module may raise
            # -- it runs inside the generation retry loops.
            return None
    return out


# Why the dataset check did or did not reach a comparison. `dataset_mismatch`
# collapses all of these to a reason-or-None, which is the right shape for a
# caller that only wants to accept or retry -- but it makes "compared and
# agreed" indistinguishable from "never found anything to compare".
#
# CLAUDE.md's rule for this file is to measure how often a fail-open check
# *engages*, not just how often it fires, because a check that can no longer
# locate its input reports a perfect false-positive rate while doing nothing.
# That has already happened here once, with fractions in `ordering`. So the
# states are exposed from the one implementation rather than re-derived by
# whatever is measuring -- a second copy of these conditions would drift, and
# a drifted measurement is worse than none.
ENGAGED_AGREED = "engaged_agreed"
ENGAGED_MISMATCH = "engaged_mismatch"
INERT_NO_INPUT = "inert_no_input"
INERT_SCORED_NOT_COMPARABLE = "inert_scored_not_comparable"
INERT_MIXED_NUMBER = "inert_mixed_number"
INERT_NO_LIST_IN_TEXT = "inert_no_list_in_text"
INERT_SHOWN_NOT_COMPARABLE = "inert_shown_not_comparable"


def dataset_check(question_text, values):
    """(state, reason) for the dataset comparison -- see the states above.

    `reason` is non-None only for ENGAGED_MISMATCH; every other state means
    there is nothing to report to the caller.
    """
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

    `values` is the list the solver will actually compute over. Only the
    numbers after the last colon are compared, because a question sentence
    routinely contains numbers that are not data ("during a school year").
    When no such list is found, this returns None rather than guessing.
    """
    return dataset_check(question_text, values)[1]


def negation_mismatch(question_text, scenario):
    """Reason a probability question's wording disagrees with the scenario
    that will be solved, or None.

    `not_probability_of` computes 1 - p, so it is only correct for a
    question that actually asks for the complement. The two must agree in
    both directions: a negated question solved as `probability_of` is wrong
    by exactly the same amount.
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
