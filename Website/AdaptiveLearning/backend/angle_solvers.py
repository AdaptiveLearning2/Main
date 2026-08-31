"""The angle-relationship solve path, with nothing heavy imported.

Lives apart from `LLM_angle_relationship_generation` for the reason
`geometry_solvers` does: `_solve_worker` imports it, and the generator module
pulls in supabase, flask and dotenv at import.

It runs in the worker because `parse_expr` is applied to the model's raw
variable strings and is unbounded -- `parse_expr("9**9**9")` never returns, and
the spin holds the GIL inside CPython's long-integer code, so no watchdog
thread and no signal handler can stop it. Four of the five scenarios need only
a number, but `algebra_complementary` solves an equation in `x`, so the
variables stay sympy expressions and the whole solve belongs here rather than
just the parse.
"""

import math

import sympy as sp
from sympy.parsing.sympy_parser import (
    parse_expr,
    standard_transformations,
    implicit_multiplication_application,
)

transformations = standard_transformations + (implicit_multiplication_application,)


def preprocess_variables(raw_variables):
    """The model's variable strings as sympy expressions.

    Only ever called from inside this module's `solve_scenario`, and so only
    ever inside the worker: this is the unbounded step, and calling it from a
    request thread is the bug this module exists to fix. Exposed because the
    degenerate-figure tests build the parsed form directly.
    """
    return [parse_expr(str(v), transformations=transformations)
            for v in raw_variables]


def complementary_angle(a):
    return 90 - a


def supplementary_angle(a):
    return 180 - a


def linear_pair(a):
    return 180 - a


def triangle_missing_angle(a, b):
    return 180 - a - b


def solve_complementary(expr1, expr2):
    x = sp.symbols('x')
    equation = sp.Eq(expr1 + expr2, 90)
    result = sp.solve(equation, x)
    return result[0] if result else None


# How many variables each scenario indexes. Checked before the dispatch, so a
# reply with too few is a rejection rather than an IndexError -- the same class
# of failure `SCENARIO_VARS` covers for geometry.
SCENARIO_ARITY = {
    "complementary": 1,
    "supplementary": 1,
    "linear_pair": 1,
    "triangle_sum": 2,
    "algebra_complementary": 2,
}

SOLVABLE_SCENARIOS = frozenset(SCENARIO_ARITY)


# The angle total each scenario's given values must fall inside. Measured
# 2026-08-18: a triangle question with angles 75 and 105 was generated with the
# answer 0 -- correct arithmetic, and not a real triangle, since those two
# already use the full 180 degrees. `complementary` fails the same way at 90.
_SCENARIO_TOTAL = {
    "complementary":         90,
    "supplementary":         180,
    "linear_pair":           180,
    "triangle_sum":          180,
    "algebra_complementary": 90,
}


def invalid_reason(scenario, variables, solution):
    """Why this configuration is invalid, or None if it is fine.

    Lives here rather than in the generator because it needs the *parsed*
    expressions: `algebra_complementary` substitutes the solved `x` back into
    both angle expressions, which is impossible once only a float has crossed
    the process boundary. Wrong at every grade, so it is checked before the
    whole-number rule the caller applies.
    """
    if scenario == "algebra_complementary":
        # `solution` here is x, not an angle, and x itself has no bound --
        # check the two angle expressions evaluated at x instead.
        x = sp.symbols('x')
        try:
            angles = [float(expr.subs(x, solution)) for expr in variables]
        except (TypeError, ValueError):
            return None
        if any(a <= 0 or a >= 90 for a in angles):
            return (f"solving gives angles {angles} degrees; each must be "
                    f"strictly between 0 and 90 to be complementary")
        return None

    total = _SCENARIO_TOTAL.get(scenario)
    if total is None:
        return None
    try:
        given = [float(expr) for expr in variables]
    except (TypeError, ValueError):
        # Not numeric -- nothing to check here rather than a reason to refuse.
        return None
    for angle in given:
        if angle <= 0 or angle >= total:
            return (f"a given angle is {angle} degrees; it must be strictly "
                    f"between 0 and {total}")

    # The half that catches 75 + 105: both givens are individually legal and
    # together they use the entire total, so the answer is 0 -- correct
    # arithmetic and not a figure that exists.
    if solution <= 0:
        return (f"the answer is {solution} degrees -- the given angles already "
                f"use the whole {total}-degree total, so there is no such figure")
    if solution >= total:
        return (f"the answer is {solution} degrees, which leaves nothing for "
                f"the other angle(s)")
    return None


def solve_scenario(scenario, raw_variables):
    """The scenario's numeric answer as a float, or None if there is not one.

    None for every failure rather than a raise, so the caller's retry loop has
    one thing to test.
    """
    arity = SCENARIO_ARITY.get(scenario)
    if arity is None:
        return None
    if not isinstance(raw_variables, list) or len(raw_variables) < arity:
        return None
    try:
        variables = preprocess_variables(raw_variables)
        match scenario:
            case "complementary":
                solution = complementary_angle(variables[0])
            case "supplementary":
                solution = supplementary_angle(variables[0])
            case "linear_pair":
                solution = linear_pair(variables[0])
            case "triangle_sum":
                solution = triangle_missing_angle(variables[0], variables[1])
            case "algebra_complementary":
                solution = solve_complementary(variables[0], variables[1])
            case _:
                # Unreachable while SCENARIO_ARITY and this `match` agree, and
                # the tests pin them equal -- but this is a standalone unit, so
                # it holds that guarantee itself rather than borrowing the
                # caller's.
                return None
        if solution is None:
            return None
        # Checked here because it needs the parsed expressions, which do not
        # cross the process boundary.
        reason = invalid_reason(scenario, variables, solution)
        if reason:
            return None
        value = float(solution)
    except Exception:
        return None
    if not math.isfinite(value):
        return None
    return value
