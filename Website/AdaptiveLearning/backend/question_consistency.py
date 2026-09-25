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


_OPERATION = frozenset("+-=*^")
_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")


def _atoms(text, letters):
    """Numbers, operators and the scored expression's own letters, as runs split by any other text."""
    letter = rf"|(?<![A-Za-z])[{re.escape(''.join(letters))}](?![A-Za-z])" if letters else ""
    pattern = re.compile(rf"\d+(?:\.\d+)?|[-+*/=^()]{letter}")
    text = text.replace("−", "-").replace("×", "*").replace("÷", "/").replace("·", "*")
    # "3²", "3^2" and "3**2" are one power.
    text = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+", lambda m: "^" + m.group().translate(_SUPERSCRIPT), text)
    text = text.replace("**", "^")
    runs, current, end = [], [], 0
    for m in pattern.finditer(text):
        if current and text[end:m.start()].strip():
            runs.append(current)
            current = []
        current.append(m.group())
        end = m.end()
    if current:
        runs.append(current)
    return runs


def _comparable(atoms):
    """Parentheses and `*` dropped: "3(x+2)" and "3*(x+2)" are the same display."""
    return [a for a in atoms if a not in "()*"]


def expression_mismatch(question_text, tokens):
    """Reason the expression shown differs from the scored `tokens`, or None.

    One displayed expression (an operation, not just a fraction bar) must match token for
    token; otherwise every scored number must appear in the text. Mixed numbers fail open.
    """
    if not isinstance(question_text, str) or not isinstance(tokens, list) or not tokens:
        return None
    scored_text = "".join(str(t) for t in tokens)
    if _MIXED_NUMBER.search(question_text) or _MIXED_NUMBER.search(scored_text):
        return None
    letters = set(re.findall(r"[a-z]", scored_text.lower()))
    scored = [a for run in _atoms(scored_text, letters) for a in run]
    shown = [run for run in _atoms(question_text, letters) if _OPERATION & set(run)
             and sum(a[0].isdigit() for a in run) >= 2]
    if len(shown) == 1:
        if _comparable(shown[0]) != _comparable(scored):
            return (f"the question shows {''.join(shown[0])} but "
                    f"{scored_text} is scored -- the student would be marked against "
                    f"an expression they were not given")
        return None
    in_text = [a for run in _atoms(question_text, set()) for a in run if a[0].isdigit()]
    if not in_text:
        return None          # numbers written in words: nothing to compare against
    missing = [n for n in scored if n[0].isdigit()]
    for n in in_text:
        if n in missing:
            missing.remove(n)
    if missing:
        return f"{missing} are scored but not in the question -- the student was not given them"


def label_pattern(label):
    """A label in the singular or plural it may be written in ("cherry", "cherries", "boxes")."""
    n = re.escape(str(label).strip().lower())
    stem = n[:-1] if n.endswith("y") else n[:-3] if n.endswith("ies") else None
    forms = [n, n + "s", n + "es"] + ([stem + "y", stem + "ies"] if stem else [])
    forms += [n[:-len(end)] for end in ("es", "s") if n.endswith(end) and len(n) > len(end)]
    return r"\b(?:" + "|".join(forms) + r")\b"


def counts_mismatch(question_text, counts):
    """Reason the counts in the text differ from the scored `counts`, or None.

    Each "<n> <up to two words> <label>" or "<label>[:] <n>" must be that label's count, and the
    text's numbers must be exactly the counts plus at most their total. No digits ("six red") fails open.
    """
    if not isinstance(question_text, str) or not isinstance(counts, dict) or not counts:
        return None
    for label, count in counts.items():
        name = label_pattern(label)
        # Both readings: in "blue 4 and green 2" green's "4 and green" is blue's, and its own is after.
        before = rf"\b(\d+)\s+(?:[A-Za-z-]+\s+){{0,2}}?{name}"
        after = rf"{name}\s*[:=]?\s*(\d+)\b"
        shown = {int(n) for p in (before, after) for n in re.findall(p, question_text, re.I)}
        if shown and count not in shown:
            return (f"the question gives {label!r} as {sorted(shown)} but {count} is "
                    f"scored -- the student would be marked against counts they were not given")
    # "If 1 marble is drawn" is the draw, not a count; "1 red is removed" stays one.
    text = _ONE_DRAWN.sub(" ", question_text)
    numbers = [int(n) for n in re.findall(r"(?<![\d.])\d+(?!\.?\d)", text)]
    if not numbers:
        return None
    extra = list(numbers)
    for count in counts.values():
        if count in extra:
            extra.remove(count)
    total = sum(counts.values())
    if extra and not (extra == [total] and _states_total(text, total)):
        return (f"the question also gives {extra}, which no scored count or a stated total "
                f"({total}) accounts for -- an item left out, or a count changed")
    return None


_DRAW_VERB = r"(?:drawn|picked|chosen|selected|pulled)\b"
_ONE_DRAWN = re.compile(rf"\b1\s+(?:[A-Za-z-]+\s+){{0,2}}?(?:is|are|was)\s+{_DRAW_VERB}"
                        rf"|\b(?:if|when)\s+1\s+(?:[A-Za-z-]+\s+){{0,2}}?{_DRAW_VERB}", re.I)


def _states_total(text, total):
    """True if `total` reads as the whole bag ("a bag of 12", "12 marbles in a bag: ...", "12 in total")."""
    before = rf"\b(?:of|contains|holds|has|with)\s+{total}\b"
    in_total = rf"\b{total}\s+(?:[A-Za-z-]+\s+){{0,2}}?(?:in\s+(?:all|total)|altogether)\b"
    # The list may lead with its label: "12 marbles in a bag: red 6, ..." or "...: red: 6, ...".
    before_list = rf"\b{total}\s+(?:[A-Za-z-]+\s+){{0,4}}?[A-Za-z-]+\s*:\s*(?:[A-Za-z-]+[:=]?\s+){{0,2}}\d"
    return any(re.search(p, text, re.I) for p in (before, in_total, before_list))


# Where a question asks: after the last of these is the item or event it is about.
_ASKS = re.compile(r"\b(?:probability|chances?|likely|likelihood)\b", re.I)


def odds_mismatch(question_text):
    """Reason the question asks for odds, which a probability answer does not give, or None."""
    if isinstance(question_text, str) and re.search(r"\bodds\b", question_text, re.I):
        return "the question asks for odds, but a probability is scored"
    return None


def _question_part(text):
    asks = list(_ASKS.finditer(text))
    return text[asks[-1].end():] if asks else None


def target_mismatch(question_text, labels, targets):
    """Reason the items the question asks about are not the scored `targets`, or None.

    Read after the last "probability", "chance", "likely" or "odds": the labels named there
    must be exactly the targets.
    """
    if not isinstance(question_text, str):
        return None
    question = _question_part(question_text)
    if question is None:
        return None
    named = {label for label in labels if re.search(label_pattern(label), question, re.I)}
    if named and named != set(targets):
        return f"the question asks about {sorted(named)} but {sorted(targets)} is scored"
    return None


_SIDE_WORDS = {"four": 4, "six": 6, "eight": 8, "ten": 10, "twelve": 12, "twenty": 20}
_COMPARISONS = [
    (r"\b(?:greater|more|higher|larger|bigger)\s+than\s+(\d+)", lambda f, n: f > n),
    (r"\b(?:less|fewer|lower|smaller)\s+than\s+(\d+)", lambda f, n: f < n),
    (r"\bat\s+least\s+(\d+)|\b(\d+)\s+or\s+(?:more|higher|greater|above)\b", lambda f, n: f >= n),
    (r"\bat\s+most\s+(\d+)|\b(\d+)\s+or\s+(?:less|lower|fewer|below)\b", lambda f, n: f <= n),
    (r"\bmultiple\s+of\s+(\d+)", lambda f, n: n > 0 and f % n == 0),
]
_PARITY = {"even": lambda f: f % 2 == 0, "odd": lambda f: f % 2 == 1,
           "prime": lambda f: f > 1 and all(f % d for d in range(2, int(f ** 0.5) + 1))}
# "neither a 1 nor a 6" is one negation of the faces it lists.
_NEGATED = re.compile(r"\bnot\b|n['’]t\b|\bcannot\b|\bother\s+than\b|\bexcept\b"
                      r"|\b(?:anything|everything|all)\s+but\b|\bneither\b", re.I)


def _sides_in_text(text):
    m = re.search(r"\b(\d+|" + "|".join(_SIDE_WORDS) + r")[\s-]*(?:sided|faced)\b", text, re.I)
    if m:
        word = m.group(1).lower()
        return int(word) if word.isdigit() else _SIDE_WORDS[word]
    return 6 if re.search(r"\bstandard\s+die\b|\bnumber\s+cube\b", text, re.I) else None


def _event_faces(question, sides):
    """The faces one recognised event names, its complement under one negation, or None."""
    negations = len(_NEGATED.findall(question))
    if negations > 1:
        return None
    question = _NEGATED.sub(" ", question)
    question = re.sub(r"\b\d+[\s-]*(?:sided|faced)\b", " ", question, flags=re.I)
    found = []
    for pattern, test in _COMPARISONS:
        for m in re.finditer(pattern, question, re.I):
            n = int(next(g for g in m.groups() if g))
            found.append({f for f in range(1, sides + 1) if test(f, n)})
    found += [{f for f in range(1, sides + 1) if test(f)} for word, test in _PARITY.items()
              if re.search(rf"\b{word}\b", question, re.I)]
    if not found:
        found = [{int(n) for n in re.findall(r"\b\d+\b", question)}] if re.search(r"\d", question) else []
    if len(found) != 1 or not found[0]:
        return None
    return set(range(1, sides + 1)) - found[0] if negations else found[0]


def dice_mismatch(question_text, sides, faces):
    """Reason the die or the event the text describes is not the scored one, or None.

    Sides from "six-sided", "8-sided", "standard die"; the event after the question word from one
    comparison, even/odd/prime or listed faces, negated at most once. Anything else fails open.
    """
    if not isinstance(question_text, str) or not isinstance(sides, int) or not 1 < sides <= 1000:
        return None          # the events are enumerated face by face
    shown_sides = _sides_in_text(question_text)
    if shown_sides is not None and shown_sides != sides:
        return f"the question shows a {shown_sides}-sided die but {sides} sides are scored"
    question = _question_part(question_text)
    if question is None:
        return None
    event = _event_faces(question, sides)
    if event is not None and event != set(faces):
        return f"the question's event is faces {sorted(event)} but {sorted(faces)} is scored"
    return None


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
