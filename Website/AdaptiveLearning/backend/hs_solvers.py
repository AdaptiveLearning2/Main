"""Exact solvers for the two high-school topics, and the equations they render.

Grades 9-12 had no content of their own. Every concept the other topics can
*score* tops out at grade 8 -- `algebra` is one linear equation with one
solution (8.EE.7b), `probability` a single event (7.SP.5), `mean`/`median`/
`mode` one statistic over a listed dataset (6.SP.5c) -- so an audit of 640
generated questions found 81% of grade-9 questions three or more grades below
grade, and stating a harder requirement in the prompt moved it by two points.
Harder numbers inside 8.EE.7b are still 8.EE.7b. Closing that needs solvers,
which is what this is.

Three properties, and the first is why this file is not sympy:

  * **Integer arithmetic only, so nothing here can hang.** `safe_solve` exists
    because `sympify("9**9**9")` never returns and the spin holds the GIL, so
    only an external kill stops it. There is no parser here to feed: every
    input is matched against `_INT` before `int()` touches it, and every
    operation is one arithmetic step on bounded integers. Same reasoning as
    `missing_number` and `patterns`, which also skip the bounded subprocess.
  * **Refuse rather than guess.** Every function answers `(value, reason)` and
    returns `None` for anything it cannot score exactly -- an irrational root,
    a repeated root where "the larger" means nothing, a result too large to be
    a sensible question. The caller is inside a retry loop, so a refusal costs
    one attempt. A wrong answer costs a student a question they answered
    correctly, which this codebase treats as the worst outcome available.
  * **The equation shown is rendered from the coefficients being scored.**
    `render_*` is the only thing that writes an equation, and the generators
    require the model's `question_text` to contain its output verbatim. That
    makes "the text says one equation and the solver scored another" -- the
    failure `question_consistency` exists for, and the one this topic is most
    exposed to, since the question *is* the equation -- unrepresentable rather
    than merely unlikely. Same direction as `question_figures`: derive the
    presentation from the scored data, never let the model write both.

One module for two topics rather than the per-topic split `geometry_solvers`
and `angle_solvers` use. Those are separate because `_solve_worker` imports
them and must not pull in supabase and flask with them; nothing here runs in a
subprocess, and the two topics share every helper below this line.
"""

import math
import re

# Bounded by inspection, which is what lets `int()` run on model output with no
# subprocess around it: at most four digits and an optional sign.
_INT = re.compile(r"^-?\d{1,4}$")

# A result past this is not a question anyone would ask, and for `functions` it
# is also the bound on the arithmetic: f(g(x)) over two quadratics squares its
# input, so an unbounded composition reaches 10^16 from coefficients that each
# look reasonable. Refusing is right either way -- "what is f(g(7))" answered
# 48,271,009 is not a question about function composition, it is a question
# about whether the student has a calculator.
MAX_ABS_RESULT = 10 ** 6

# Degree 2 is the whole of what these topics ask for, and it bounds the
# composition above: a cubic inner function would cube the magnitude.
MAX_DEGREE = 2


def parse_int(raw):
    """The integer in `raw`, or None if it is not one this file will accept.

    `int()` alone would take "999999999999" and " 12 " and "+3"; the point of
    the regex is that everything downstream can assume a bounded magnitude
    without re-checking.
    """
    if isinstance(raw, bool):        # bool is an int subclass; not a coefficient
        return None
    if isinstance(raw, int):
        return raw if abs(raw) <= 9999 else None
    if not isinstance(raw, str) or not _INT.match(raw):
        return None
    return int(raw)


def parse_int_list(raws, max_length):
    """A list of integers, or None if any entry is not one.

    All-or-nothing: a coefficient list with one unusable entry is an unusable
    polynomial, and taking the readable ones would silently change its degree.
    """
    if not isinstance(raws, list) or not 1 <= len(raws) <= max_length:
        return None
    out = []
    for raw in raws:
        value = parse_int(raw)
        if value is None:
            return None
        out.append(value)
    return out


# --- rendering ------------------------------------------------------------
#
# The generators require `question_text` to contain exactly what these return,
# so a change here changes what the model is asked to reproduce. Signs are the
# whole difficulty: "x^2 + -5x + 6 = 0" is what naive formatting produces and
# is not how anyone writes a quadratic, so a student would reasonably read it
# as a typo and the model would reasonably "correct" it -- costing a retry on
# every question whose middle coefficient is negative.


def _first_term(coefficient, suffix):
    """The leading term, which carries its own sign and no spaces."""
    if coefficient == 1 and suffix:
        return suffix
    if coefficient == -1 and suffix:
        return f"-{suffix}"
    return f"{coefficient}{suffix}"


def _later_term(coefficient, suffix):
    """A following term as " + 3x" or " - 3x", or "" when it vanishes.

    A zero coefficient renders as nothing rather than "+ 0x": the term is
    absent from the equation the student sees, and the solver is reading the
    coefficient rather than the string, so there is nothing to keep them
    honest about.
    """
    if coefficient == 0:
        return ""
    sign = "-" if coefficient < 0 else "+"
    magnitude = abs(coefficient)
    body = suffix if magnitude == 1 and suffix else f"{magnitude}{suffix}"
    return f" {sign} {body}"


def render_quadratic(a, b, c):
    """`ax^2 + bx + c = 0` as a student would write it.

    `x^2` rather than `x²` or `x**2`: the first is what a keyboard produces and
    what the prompt's example shows, and the substring check means all three
    would otherwise be a retry apiece.
    """
    return (_first_term(a, "x^2") + _later_term(b, "x") + _later_term(c, "")
            + " = 0")


def render_polynomial(coefficients):
    """A polynomial in descending powers: `[3, -2, 1]` -> `3x^2 - 2x + 1`.

    A bare constant (`[7]`) renders as `7`, which is a legitimate if dull
    function; the generators' own tiers are what keep it from being asked.
    """
    degree = len(coefficients) - 1
    parts = []
    for index, coefficient in enumerate(coefficients):
        power = degree - index
        suffix = "" if power == 0 else "x" if power == 1 else f"x^{power}"
        if not parts:
            if coefficient == 0 and power > 0:
                # A leading zero is not a term; the polynomial is lower-degree
                # than its list suggests. Skip it rather than render "0x^2".
                continue
            parts.append(_first_term(coefficient, suffix))
        else:
            parts.append(_later_term(coefficient, suffix))
    return "".join(parts) if parts else "0"


# --- quadratics (A-REI.4b) ------------------------------------------------


def solve_quadratic(a, b, c, target):
    """The requested root of `ax^2 + bx + c = 0`, or `(None, reason)`.

    Restricted to quadratics that **factor over the integers**, which is
    A-REI.4b's core ("solve quadratic equations by inspection, taking square
    roots, completing the square, the quadratic formula and factoring") and is
    what keeps the answer and its distractors whole numbers. The alternatives
    were both worse: an irrational root renders as a decimal that the correct
    option matches only to whatever precision the formatter chose, and a
    rational one puts a fraction among integer distractors, which is a tell.

    `target` names which root, and the two refusals below are why it has to:

      * a **repeated** root (discriminant 0) makes "the larger solution" a
        question with no answer -- both roots are the same number, so a student
        who reads carefully has nothing to choose between.
      * **no real roots** (negative discriminant) cannot be scored at all here.

    Neither is a defect in the model's reply so much as a quadratic this topic
    cannot ask about, which is why both cost a retry rather than raising.
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
    """The root that was *not* asked for, or None.

    The best distractor this topic has: a student who solves correctly and
    then reads "larger" as "smaller" lands exactly here, which is the mistake
    worth putting in front of them. Distractors that are one away from the
    answer test arithmetic; this one tests whether they read the question.
    """
    opposite = "smaller" if target == "larger" else "larger"
    value, _ = solve_quadratic(a, b, c, opposite)
    return value


def _exact_isqrt(value):
    """The integer square root of a perfect square, or None.

    `math.isqrt` floors, so it answers 3 for 10 as readily as for 9 -- the
    squaring back is what separates the two, and without it every discriminant
    would look like a perfect square and every irrational root would be
    served rounded.
    """
    if value < 0:
        return None
    import math
    root = math.isqrt(value)
    return root if root * root == value else None


# --- functions (F-IF.2, F-BF.1c) -----------------------------------------


def evaluate_polynomial(coefficients, x):
    """`f(x)` for a polynomial in descending powers, or `(None, reason)`.

    Horner's method, so the intermediate values stay the size of the answer
    rather than of `x**degree` -- which matters because the bound below is
    checked on the result, and an intermediate that overflowed the bound on
    its way to a small answer would be refused for no reason a reader could
    see.
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


def is_constant_polynomial(coefficients):
    """True if this is the same value for every x, e.g. `[0, 0, 7]`.

    A constant is a perfectly good function and a useless question: "if
    f(x) = 7, what is f(4)" asks nothing about function notation, which is the
    whole of what this topic is for. `render_polynomial` skips leading zeros,
    so the reply also *looks* fine on screen -- the tell is only in the
    coefficient list, which is where this can be caught.
    """
    return all(coefficient == 0 for coefficient in coefficients[:-1])


def solve_composition(outer, inner, x):
    """`f(g(x))`, or `(None, reason)`.

    Composition is the part of this topic that is unambiguously high school:
    evaluating a rule at a value is 8.F.2, and it is *function notation* that
    F-IF.2 introduces and `f(g(x))` (F-BF.1c) that has no grade-8 equivalent.
    Both halves go through the same bound, so an inner value that is already
    too large is refused before it is squared.
    """
    middle, reason = evaluate_polynomial(inner, x)
    if middle is None:
        return None, f"inner function: {reason}"
    return evaluate_polynomial(outer, middle)
