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

# A fraction is one token, not two numbers. `ordering` routinely scores
# "3/4" alongside "0.27" -- `solve_ordering` sorts on float(sympify(v)), so
# those ARE comparable, and reading them as a bare 3 and 4 made this check
# inert on half the ordering questions sampled live (2026-08-21).
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


def dataset_mismatch(question_text, values):
    """Reason the dataset in `question_text` differs from `values`, or None.

    `values` is the list the solver will actually compute over. Only the
    numbers after the last colon are compared, because a question sentence
    routinely contains numbers that are not data ("during a school year").
    When no such list is found, this returns None rather than guessing.
    """
    if not question_text or not values:
        return None
    scored = _as_floats(values)
    if scored is None or len(scored) < 2:
        return None

    if _MIXED_NUMBER.search(question_text):
        return None

    matches = _LIST_AFTER_COLON.findall(question_text)
    if not matches:
        return None
    shown = _as_floats(_NUMBER.findall(matches[-1]))
    if shown is None:
        return None
    if len(shown) < 2:
        return None

    if sorted(shown) != sorted(scored):
        return (f"the question shows {shown} but the answer is computed from "
                f"{scored} -- the student would be marked against data they "
                f"were not given")
    return None


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
