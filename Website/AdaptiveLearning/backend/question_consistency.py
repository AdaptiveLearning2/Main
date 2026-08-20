"""Does the question a student SEES describe the data that gets SCORED?

Every generator here returns two things that are supposed to agree: the
`question_text` shown to the student, and a structured field (`variables`,
`values`, `items`/`scenario`) that the solver computes the answer from.
Nothing checked that they agreed, and an 8B model does not reliably keep
them in step. `LLM_mode_generation.py` even carried a stale note
contemplating this ("POSSIBLY: manually generate solution using numbers
from question_text").

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

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

# "The numbers of hours were: 8, 4, 12" -- these prompts put the dataset
# after a colon, which is what makes locating it reliable enough to compare.
_LIST_AFTER_COLON = re.compile(r":\s*(-?\d+(?:\.\d+)?(?:\s*,\s*-?\d+(?:\.\d+)?)+)")

# Wording that makes a question ask for the complement. Bounded to whole
# words so "cannot" or a category called "Nothing" cannot trip it.
_NEGATION = re.compile(r"\b(not|isn't|is not|other than|neither)\b", re.I)


def _as_floats(values):
    out = []
    for v in values:
        m = _NUMBER.fullmatch(str(v).strip())
        if not m:
            return None          # operators, fractions, labels -- not comparable
        out.append(float(m.group(0)))
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

    matches = _LIST_AFTER_COLON.findall(question_text)
    if not matches:
        return None
    shown = [float(n) for n in _NUMBER.findall(matches[-1])]
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
