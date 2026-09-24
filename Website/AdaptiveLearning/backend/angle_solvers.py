"""The angle-relationship solve path, with nothing heavy imported.

Separate from the generator so `_solve_worker` can import it without supabase
or flask. Runs only in the worker: `parse_expr` on model strings is unbounded.
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

    Unbounded: call only inside the worker (tests call it directly).
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


# Variables each scenario indexes; checked first so too few is a rejection, not an IndexError.
SCENARIO_ARITY = {
    "complementary": 1,
    "supplementary": 1,
    "linear_pair": 1,
    "triangle_sum": 2,
    "algebra_complementary": 2,
}

SOLVABLE_SCENARIOS = frozenset(SCENARIO_ARITY)


# Degrees each scenario's angles sum to; givens and answer must lie strictly inside.
_SCENARIO_TOTAL = {
    "complementary":         90,
    "supplementary":         180,
    "linear_pair":           180,
    "triangle_sum":          180,
    "algebra_complementary": 90,
}


def invalid_reason(scenario, variables, solution):
    """Why this configuration is invalid, or None if it is fine.

    Here, not in the generator, because it needs the parsed expressions.
    """
    if scenario == "algebra_complementary":
        # `solution` is x, not an angle; check both angles evaluated at x.
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

    # Givens legal alone can still use the whole total (75 + 105), leaving 0.
    if solution <= 0:
        return (f"the answer is {solution} degrees -- the given angles already "
                f"use the whole {total}-degree total, so there is no such figure")
    if solution >= total:
        return (f"the answer is {solution} degrees, which leaves nothing for "
                f"the other angle(s)")
    return None


def solve_scenario(scenario, raw_variables):
    """`(value, reason)` -- the answer as a float, or None and why not.

    `reason` is None exactly when `value` is not.
    """
    arity = SCENARIO_ARITY.get(scenario)
    if arity is None:
        return None, f"no such scenario: {scenario!r}"
    if not isinstance(raw_variables, list) or len(raw_variables) < arity:
        return None, (f"{scenario} needs {arity} variable(s), got "
                      f"{raw_variables!r:.60}")
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
                # Unreachable while SCENARIO_ARITY matches this `match`.
                return None, f"no branch for scenario {scenario!r}"
        if solution is None:
            return None, f"{scenario} has no solution"
        reason = invalid_reason(scenario, variables, solution)
        if reason:
            return None, reason
        value = float(solution)
    except Exception as e:
        return None, f"could not solve {scenario}: {type(e).__name__}: {e}"
    if not math.isfinite(value):
        return None, f"{scenario} solved to a non-finite value"
    return value, None
