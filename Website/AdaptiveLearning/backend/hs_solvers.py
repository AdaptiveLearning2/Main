"""Exact solvers for the high-school topics, and the equations they render.

No model-supplied value reaches this file (generators pick the integers), so
it needs no sympy or subprocess. Every solver returns `(value, reason)` and
refuses (`None`) anything it cannot score exactly. `render_*` is the only
writer of an equation; generators require `question_text` to contain it verbatim.
"""

import math

# Largest answer worth asking; also bounds f(g(x)), which squares its input.
MAX_ABS_RESULT = 10 ** 6

# Also bounds the composition: a cubic inner function would cube the magnitude.
MAX_DEGREE = 2


# --- rendering ------------------------------------------------------------
# `question_text` must contain these outputs verbatim. Negative coefficients
# render as " - 5x", never " + -5x".


def _first_term(coefficient, suffix):
    """The leading term, which carries its own sign and no spaces."""
    if coefficient == 1 and suffix:
        return suffix
    if coefficient == -1 and suffix:
        return f"-{suffix}"
    return f"{coefficient}{suffix}"


def _later_term(coefficient, suffix):
    """A following term as " + 3x" or " - 3x", or "" when it vanishes."""
    if coefficient == 0:
        return ""
    sign = "-" if coefficient < 0 else "+"
    magnitude = abs(coefficient)
    body = suffix if magnitude == 1 and suffix else f"{magnitude}{suffix}"
    return f" {sign} {body}"


def render_quadratic(a, b, c):
    """`ax^2 + bx + c = 0` as a student would write it (`x^2`, matching the prompt)."""
    return (_first_term(a, "x^2") + _later_term(b, "x") + _later_term(c, "")
            + " = 0")


def render_polynomial(coefficients):
    """A polynomial in descending powers: `[3, -2, 1]` -> `3x^2 - 2x + 1`."""
    degree = len(coefficients) - 1
    parts = []
    for index, coefficient in enumerate(coefficients):
        power = degree - index
        suffix = "" if power == 0 else "x" if power == 1 else f"x^{power}"
        if not parts:
            if coefficient == 0 and power > 0:
                # A leading zero is not a term; don't render "0x^2".
                continue
            parts.append(_first_term(coefficient, suffix))
        else:
            parts.append(_later_term(coefficient, suffix))
    return "".join(parts) if parts else "0"


# --- quadratics (A-REI.4b) ------------------------------------------------


def solve_quadratic(a, b, c, target):
    """The requested root of `ax^2 + bx + c = 0`, or `(None, reason)`.

    Integer roots only, so the answer and distractors are whole numbers.
    `target` is "larger"/"smaller"; repeated or non-real roots are refused.
    """
    if target not in ("larger", "smaller"):
        return None, f"unknown target {target!r}"
    if a == 0:
        return None, "not a quadratic: the x^2 coefficient is 0"

    discriminant = b * b - 4 * a * c
    if discriminant < 0:
        return None, "no real roots"
    if discriminant == 0:
        return None, "a repeated root: 'larger' and 'smaller' are the same"

    root = _exact_isqrt(discriminant)
    if root is None:
        return None, f"irrational roots: {discriminant} is not a perfect square"

    numerators = (-b + root, -b - root)
    denominator = 2 * a
    values = []
    for numerator in numerators:
        if numerator % denominator != 0:
            return None, "roots are not whole numbers"
        values.append(numerator // denominator)

    smaller, larger = sorted(values)
    value = larger if target == "larger" else smaller
    if abs(value) > MAX_ABS_RESULT:
        return None, f"root {value} is too large to ask about"
    return value, None


def other_root(a, b, c, target):
    """The root that was *not* asked for (a distractor), or None."""
    opposite = "smaller" if target == "larger" else "larger"
    value, _ = solve_quadratic(a, b, c, opposite)
    return value


def _exact_isqrt(value):
    """The integer square root of a perfect square, or None.

    `math.isqrt` floors; squaring back is what rejects non-squares.
    """
    if value < 0:
        return None
    import math
    root = math.isqrt(value)
    return root if root * root == value else None


# --- functions (F-IF.2, F-BF.1c) -----------------------------------------


def evaluate_polynomial(coefficients, x):
    """`f(x)` for a polynomial in descending powers, or `(None, reason)`.

    Horner's method, so intermediates stay the size of the answer.
    """
    if not coefficients:
        return None, "no coefficients"
    if len(coefficients) - 1 > MAX_DEGREE:
        return None, f"degree above {MAX_DEGREE}"
    total = 0
    for coefficient in coefficients:
        total = total * x + coefficient
        if abs(total) > MAX_ABS_RESULT:
            return None, "the value is too large to ask about"
    return total, None


def solve_composition(outer, inner, x):
    """`f(g(x))`, or `(None, reason)`. An oversized inner value is refused first."""
    middle, reason = evaluate_polynomial(inner, x)
    if middle is None:
        return None, f"inner function: {reason}"
    return evaluate_polynomial(outer, middle)


# --- spread (S-ID.2) ------------------------------------------------------


def population_sd(values):
    """The population standard deviation, or `(None, reason)` if not exact.

    Divides by n, not n-1, so the question wording must say "population".
    The variance must be a perfect square; `_choose_dataset` builds data that way.
    """
    if not values or len(values) < 2:
        return None, "a spread needs at least two values"
    total = sum(values)
    if total % len(values):
        return None, "the mean is not a whole number"
    mean = total // len(values)
    squares = sum((v - mean) ** 2 for v in values)
    if squares == 0:
        return None, "every value is the same, so the spread is zero"
    if squares % len(values):
        return None, "the variance is not a whole number"
    variance = squares // len(values)
    root = _exact_isqrt(variance)
    if root is None:
        return None, f"irrational: a variance of {variance} is not a square"
    return root, None


def population_variance(values):
    """The variance (the forgot-the-square-root distractor), or None where the
    standard deviation is not exact."""
    sd, _reason = population_sd(values)
    return None if sd is None else sd * sd
