"""The geometry solve path, with nothing heavy imported.

Separate from the generator so `_solve_worker` can import it without supabase
or flask. Runs only in the worker: `sympify` on model values is unbounded.
"""

import math

import sympy as sp
from sympy import sqrt, symbols, Eq, solve, sympify, Rational


simple_pi = sympify(3.14)

def normalize_solution(sol):
    if isinstance(sol, list):
        return sol[0]
    return sol

def preprocess_variables(vars_dict):
    return {k : sympify(v) for k, v in vars_dict.items()}

# Area/Perimeter
def solve_triangle_perimeter(s1, s2, s3):
    return s1 + s2 + s3

def solve_triangle_area(base, height):
    return Rational(1/2) * base * height

def solve_rectangle_perimeter(l, w):
    return 2*l + 2*w

def solve_rectangle_area(l,w):
    return l * w

def solve_circle_circumference(r):
    return 2 * simple_pi * r

def solve_circle_area(r):
    return simple_pi * r * r

# Volume
def solve_rect_volume(l,w,h):
    return l*w*h

def solve_cube_volume(a):
    return a**3

def solve_cylinder_volume(r,h):
    return simple_pi * r**2 *h

def solve_pyramid_volume(b,h):
    return Rational(1/3) * b * h

def solve_sphere_volume(r):
    return Rational(4/3) * simple_pi * r**3

# Pythagorean Theorem
def solve_pythag(a, b):
    c = (a**2) + (b**2)
    return sqrt(c)

# Find missing side
def rect_area_missing_side(area, s1):
    x = symbols('x')
    solution = solve(Eq(x * s1, area), x)
    return solution

def rect_perimeter_missing_side(perim, s1):
    x = symbols('x')
    solution = solve(Eq((2*s1) + (2*x), perim), x)
    return solution

def triangle_area_missing_side(area, s1):
    x = symbols('x')
    solution = solve(Eq((1/2)*s1 *x, area), x)
    return solution

def traingle_perimeter_missing_side(perim, s1,s2):
    x = symbols('x')
    solution = solve(Eq(s1 + s2 + x, perim), x)
    return solution

def circle_area_missing_side(area):
    # positive=True: the only two-root solve, and `normalize_solution` takes the first.
    x = symbols('x', positive=True)
    solution = solve(Eq(simple_pi*x**2, area), x)
    return solution

def circle_circumference_missing_side(circ):
    x = symbols('x')
    solution = solve(Eq(2*simple_pi*x, circ), x)
    return solution


# Variables each scenario's solver indexes; pinned to the dispatch in
# tests/test_geometry_scenarios.py.
SCENARIO_VARS = {
    "rectangle_area_by_counting": ("columns", "rows",),
    "rectangle_area": ("length", "width",),
    "rectangle_perimeter": ("length", "width",),
    "triangle_area": ("base", "height",),
    "triangle_perimeter": ("s1", "s2", "s3",),
    "circle_area": ("radius",),
    "circle_circumference": ("radius",),
    "rect_volume": ("height", "length", "width",),
    "cylinder_volume": ("height", "radius",),
    "sphere_volume": ("radius",),
    "cube_volume": ("side",),
    "pyramid_volume": ("base_area", "height",),
    "pythagorean": ("a", "b",),
    "rect_area_missing_side": ("area", "known_side",),
    "rect_perimeter_missing_side": ("known_side", "perimeter",),
    "circle_area_missing_side": ("area",),
    "circle_circumference_missing_side": ("circumference",),
    "triangle_area_missing_side": ("area", "known_side",),
    "triangle_perimeter_missing_side": ("perimeter", "s1", "s2",),
}

SOLVABLE_SCENARIOS = frozenset(SCENARIO_VARS)


def solve_scenario(scenario, raw_vars):
    """`(value, reason)` -- the solution as a float, or None and why not.

    `reason` is None exactly when `value` is not.
    """
    if scenario not in SCENARIO_VARS:
        return None, f"no such scenario: {scenario!r}"
    missing = [k for k in SCENARIO_VARS[scenario]
               if k not in (raw_vars or {})]
    if missing:
        return None, f"{scenario} is missing variables: {missing}"
    try:
        vars = preprocess_variables(raw_vars)
        # Every value is a length, area or volume, so must be positive.
        given = [k for k, v in vars.items() if v.is_number and v <= 0]
        if given:
            return None, f"{scenario} was given non-positive {given}"
        match (scenario):
            case "rectangle_area_by_counting":
                # 2.G.2: same arithmetic as area; only the wording differs.
                solution = solve_rectangle_area(vars["rows"], vars["columns"])
            case "rectangle_area":
                solution = solve_rectangle_area(vars["length"], vars["width"])
            case "rectangle_perimeter":
                solution = solve_rectangle_perimeter(vars["length"], vars["width"])
            case "triangle_area":
                solution = solve_triangle_area(vars["base"], vars["height"])
            case "triangle_perimeter":
                solution = solve_triangle_perimeter(vars["s1"], vars["s2"], vars["s3"])
            case "circle_area":
                solution = solve_circle_area(vars["radius"])
            case "circle_circumference":
                solution = solve_circle_circumference(vars["radius"])
            case "rect_volume": 
                solution = solve_rect_volume(vars["length"], vars["width"], vars["height"])
            case "cylinder_volume":
                solution = solve_cylinder_volume(vars["radius"], vars["height"])
            case "sphere_volume":
                solution = solve_sphere_volume(vars["radius"])
            case "cube_volume":
                solution = solve_cube_volume(vars["side"])
            case "pyramid_volume":
                solution = solve_pyramid_volume(vars["base_area"], vars["height"])
            case "pythagorean":
                solution = solve_pythag(vars["a"], vars["b"])
            case "rect_area_missing_side" :
                solution = rect_area_missing_side(vars["area"], vars["known_side"])
            case "rect_perimeter_missing_side" :
                solution = rect_perimeter_missing_side(vars["perimeter"], vars["known_side"])
            case "circle_area_missing_side":
                solution = circle_area_missing_side(vars["area"])
            case "circle_circumference_missing_side":
                solution = circle_circumference_missing_side(vars["circumference"])
            case "triangle_area_missing_side" :
                solution = triangle_area_missing_side(vars["area"], vars["known_side"])
            case "triangle_perimeter_missing_side" :
                solution = traingle_perimeter_missing_side(vars["perimeter"], vars["s1"], vars["s2"])
            case _:
                # Unreachable while SCENARIO_VARS matches; avoids an unbound `solution`.
                return None, f"no branch for scenario {scenario!r}"
        solution = normalize_solution(solution)
        value = float(solution)
    except Exception as e:
        return None, f"could not solve {scenario}: {type(e).__name__}: {e}"
    if not math.isfinite(value):
        return None, f"{scenario} solved to a non-finite value"
    if value <= 0:
        # Positive inputs that don't fit (perimeter 10, known side 8) go negative.
        return None, f"{scenario} solved to {value}; a measure must be positive"
    # Triangle inequality: positive sides are not yet a triangle.
    sides = {"triangle_perimeter": lambda: [vars["s1"], vars["s2"], vars["s3"]],
             "triangle_perimeter_missing_side": lambda: [vars["s1"], vars["s2"], value],
             }.get(scenario)
    if sides:
        a, b, c = sorted(float(s) for s in sides())
        if a + b <= c:
            return None, (f"{scenario}: sides {a}, {b} and {c} do not make a "
                          f"triangle")
    return value, None
