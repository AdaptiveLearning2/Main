"""The geometry solve path, with nothing heavy imported.

Lives apart from `LLM_geometry_generation` so `_solve_worker` can import it:
the generator module pulls in supabase, flask and dotenv at import, which a
subprocess spawned per question cannot afford, and none of that is needed to
turn a scenario and a dict of values into a number.

It runs in the worker rather than the request thread because `preprocess_variables`
applies `sympify` to the model's raw values and is unbounded:
`sympify("9**9**9")` never returns, and the spin holds the GIL inside CPython's
long-integer code, so no watchdog thread and no signal handler can stop it.
Only an external kill works. `SCENARIO_VARS` checks that the keys are present,
which says nothing about what the values are.
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
    x = symbols('x')
    solution = solve(Eq(simple_pi*x**2, area), x)
    return solution

def circle_circumference_missing_side(circ):
    x = symbols('x')
    solution = solve(Eq(2*simple_pi*x, circ), x)
    return solution


# The variables each scenario's solver indexes, derived from the dispatch below
# and pinned against it in tests/test_geometry_scenarios.py. A valid scenario
# carrying the wrong variables is a KeyError out of the dispatch -- a 500, not
# a retry -- and two hand-maintained lists is how that comes back.
SCENARIO_VARS = {
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
    """The scenario's numeric solution, or None if there is not one.

    None for every failure rather than a raise, so the caller's retry loop has
    a single thing to test.
    """
    try:
        vars = preprocess_variables(raw_vars)
        match (scenario):
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
                # Unreachable while the caller checks SOLVABLE_SCENARIOS first
                # and the tests pin the two lists equal -- but the extraction
                # made this a standalone unit, so that guarantee now lives in a
                # different file. Without the default, an unlisted scenario
                # leaves `solution` unbound and raises UnboundLocalError from a
                # line that reads like arithmetic.
                return None
        solution = normalize_solution(solution)
        value = float(solution)
    except Exception:
        return None
    if not math.isfinite(value):
        # `1e200` cubed is `1e600`, and `float()` of that is `inf`. There is no
        # question here, and no set of distractors around it.
        return None
    return value
