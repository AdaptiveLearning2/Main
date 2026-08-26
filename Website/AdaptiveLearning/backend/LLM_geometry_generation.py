# Generates area/perimeter/volume geometry questions via LLM and solves them with sympy.
import os
import re
import random
from supabase import create_client, Client #pip install supabase
from dotenv import load_dotenv   #pip install dotenv
import llm_client
import json
from flask import Flask, jsonify
from flask_cors import CORS #pip install flask-cors
import sympy as sp #pip install sympy
from sympy import sqrt, symbols, Eq, solve, sympify, Integer, Rational, pi
import incorrect_solution_generation as inc_gen
import lesson_plan_context
import grade_levels
import grade_appropriateness

# Scenarios: perimeter, area, volume, missing_side, pythagorean_theorem.
# pi is approximated as 3.14 for simplicity/consistency.
def extract_json(text):
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i+1]

    return None

simple_pi = sympify(3.14)

def normalize_solution(sol):
    if isinstance(sol, list):
        return sol[0]
    return sol

def serialize_sympy(x):
    if isinstance(x, sp.Rational):
        return str(x)
    if isinstance(x, sp.Integer):
        return int(x)
    if isinstance(x, sp.Float):
        return float(x)
    if isinstance(x, sp.Expr):
        return str(x)
    return str(x)

def format_two_decimals(x):
    if isinstance(x, list):
        x = x[0]
    val = float(x.evalf()) if hasattr(x, "evalf") else float(x)
    if val.is_integer():
        return str(int(val))
    else:
        return f"{val:.2f}"

def to_num(x):
    return sympify(x)

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

# The scenario is chosen in code by _pick_scenario before the prompt is
# built, so only that one block is sent. Sending all eighteen -- as this
# prompt did until the switch to a billed API made the waste visible -- put
# seventeen irrelevant worked examples in front of the model on every call,
# every one of them a phrasing template it could copy from ("...units. What
# is its...") for a question it was not being asked to write.
#
# Split into header/blocks/footer rather than one f-string for that reason.
# The blocks are plain strings, not f-strings: the original was an f-string
# with no interpolation, so its `{{`/`}}` were escapes rendering as single
# braces -- doubling them here would emit literal `{{` into the prompt.
GEOMETRY_HEADER = """
You are to provide a Math question suitable for students. The response must be in JSON format.
The Question Text, Question Topic, Scenario, Variables, and Target will be displayed. The Question Topic will always be "geometry".

Generate a question for the one scenario given below.

IMPORTANT:
- ALWAYS approximate pi as 3.14
- All numeric values must be simple (integers or one decimal max)
- Ensure the problem is solvable using the provided variables
"""

# The EASY/MEDIUM/HARD TOPICS listing that sat here is gone with the other
# seventeen blocks: it named scenarios the model can no longer see, and
# difficulty is not its decision -- _pick_scenario has already applied both
# the difficulty tier and the grade-band restriction by this point.

SCENARIO_BLOCKS = {
    1: """SCENARIO 1: rectangle_area
Example:
"A rectangle has a length of 5 units and a width of 3 units. What is its area?"

JSON structure:
{
  "question_text": "A rectangle has a length of 5 units and a width of 3 units. What is its area?",
  "type": "geometry",
  "scenario": "rectangle_area",
  "variables": {
    "length": "5",
    "width": "3"
  }
}
""",

    2: """SCENARIO 2: rectangle_perimeter
Example:
"A rectangle has a length of 8 units and a width of 2 units. What is its perimeter?"

{
  "question_text": "A rectangle has a length of 8 units and a width of 2 units. What is its perimeter?",
  "type": "geometry",
  "scenario": "rectangle_perimeter",
  "variables": {
    "length": "8",
    "width": "2"
  }
}
""",

    3: """SCENARIO 3: triangle_area
Example:
"A triangle has a base of 6 units and a height of 4 units. What is its area?"

{
  "question_text": "A triangle has a base of 6 units and a height of 4 units. What is its area?",
  "type": "geometry",
  "scenario": "triangle_area",
  "variables": {
    "base": "6",
    "height": "4"
  }
}
""",

    4: """SCENARIO 4: triangle_perimeter
Example:
"A triangle has side lengths 3, 4, and 5 units. What is its perimeter?"

{
  "question_text": "A triangle has side lengths 3, 4, and 5 units. What is its perimeter?",
  "type": "geometry",
  "scenario": "triangle_perimeter",
  "variables": {
    "s1": "3",
    "s2": "4",
    "s3": "5"
  }
}
""",

    5: """SCENARIO 5: circle_area
Example:
"A circle has a radius of 7 units. What is its area?"

{
  "question_text": "A circle has a radius of 7 units. What is its area?",
  "type": "geometry",
  "scenario": "circle_area",
  "variables": {
    "radius": "7"
  }
}
""",

    6: """SCENARIO 6: circle_circumference
Example:
"A circle has a radius of 5 units. What is its circumference?"

{
  "question_text": "A circle has a radius of 5 units. What is its circumference?",
  "type": "geometry",
  "scenario": "circle_circumference",
  "variables": {
    "radius": "5"
  }
}
""",

    7: """SCENARIO 7: rectangular_prism_volume
Example:
"A rectangular prism has a length of 4, width of 3, and height of 2. What is its volume?"

{
  "question_text": "A rectangular prism has a length of 4, width of 3, and height of 2. What is its volume?",
  "type": "geometry",
  "scenario": "rect_volume",
  "variables": {
    "length": "4",
    "width": "3",
    "height": "2"
  }
}
""",

    8: """SCENARIO 8: cylinder_volume
Example:
"A cylinder has a radius of 3 and height of 5. What is its volume?"

{
  "question_text": "A cylinder has a radius of 3 and height of 5. What is its volume?",
  "type": "geometry",
  "scenario": "cylinder_volume",
  "variables": {
    "radius": "3",
    "height": "5"
  }
}
""",

    9: """SCENARIO 9: sphere_volume
Example:
"A sphere has a radius of 3. What is its volume?"

{
  "question_text": "A sphere has a radius of 3. What is its volume?",
  "type": "geometry",
  "scenario": "sphere_volume",
  "variables": {
    "radius": "3"
  }
}
""",

    10: """SCENARIO 10: pythagorean
Example:
"A right triangle has legs of 3 and 4 units. What is the hypotenuse?"

{
  "question_text": "A right triangle has legs of 3 and 4 units. What is the hypotenuse?",
  "type": "geometry",
  "scenario": "pythagorean",
  "variables": {
    "a": "3",
    "b": "4"
  }
}
""",

    11: """SCENARIO 11: rectangle_missing_side_area
Example:
"A rectangle has an area of 20 square units and a width of 4 units. What is the length?"

{
  "question_text": "A rectangle has an area of 20 square units and a width of 4 units. What is the length?",
  "type": "geometry",
  "scenario": "rect_area_missing_side",
  "variables": {
    "area": "20",
    "known_side": "4"
  }
}
""",

    12: """SCENARIO 12: rectangle_missing_side_perimeter
Example:
"A rectangle has a perimeter of 24 units and one side length of 5 units. What is the other side?"

{
  "question_text": "A rectangle has a perimeter of 24 units and one side length of 5 units. What is the other side?",
  "type": "geometry",
  "scenario": "rect_perimeter_missing_side",
  "variables": {
    "perimeter": "24",
    "known_side": "5"
  }
}
""",

    13: """SCENARIO 13: circle_missing_radius_area
Example:
"A circle has an area of 50.24 square units. What is the radius?"

{
  "question_text": "A circle has an area of 50.24 square units. What is the radius?",
  "type": "geometry",
  "scenario": "circle_area_missing_side",
  "variables": {
    "area": "50.24"
  }
}
""",

    14: """SCENARIO 14: triangle_missing_side_area
Example:
"A triangle has an area of 12 square units and a base of 6 units. What is the height?"

{
  "question_text": "A triangle has an area of 12 square units and a base of 6 units. What is the height?",
  "type": "geometry",
  "scenario": "triangle_area_missing_side",
  "variables": {
    "area": "12",
    "known_side": "6"
  }
}
""",

    15: """SCENARIO 15: triangle_missing_side_perimeter
Example:
"A triangle has a perimeter of 18 units. Two of its sides are 5 units and 7 units. What is the length of the third side?"

{
  "question_text": "A triangle has a perimeter of 18 units. Two of its sides are 5 units and 7 units. What is the length of the third side?",
  "type": "geometry",
  "scenario": "triangle_perimeter_missing_side",
  "variables": {
    "perimeter": "18",
    "s1": "5",
    "s2": "7"
  }
}
""",

    16: """SCENARIO 16: circle_missing_radius_circumference
Example:
"A circle has a circumference of 31.4 units. What is the radius?"

{
  "question_text": "A circle has a circumference of 31.4 units. What is the radius?",
  "type": "geometry",
  "scenario": "circle_circumference_missing_side",
  "variables": {
    "circumference": "31.4"
  }
}
""",

    17: """SCENARIO 17: cube_volume
Example:
"A cube has a side length of 4 units. What is its volume?"

{
  "question_text": "A cube has a side length of 4 units. What is its volume?",
  "type": "geometry",
  "scenario": "cube_volume",
  "variables": {
    "side": "4"
  }
}
""",

    18: """SCENARIO 18: pyramid_volume
Example:
"A pyramid has a base area of 30 square units and a height of 9 units. What is its volume?"

{
  "question_text": "A pyramid has a base area of 30 square units and a height of 9 units. What is its volume?",
  "type": "geometry",
  "scenario": "pyramid_volume",
  "variables": {
    "base_area": "30",
    "height": "9"
  }
}
""",
}

GEOMETRY_FOOTER = """
FINAL RULES:
- Generate ONLY ONE question, return ONLY ONE JSON object.
- Return ONLY valid JSON, with NO additional text or characters
- Do NOT include: explanations, markdown, backticks, extra text before or after JSON
- Use ONLY double quotes
- All keys must match EXACTLY as shown
"""


def _geometry_prompt(scenario):
    """Header + the one selected scenario's block + footer.

    KeyError rather than a default block on an unknown scenario: every caller
    gets its number from _pick_scenario, which draws from DIFFICULTY_SCENARIOS,
    so an unknown one means those two tables have drifted apart -- which should
    fail loudly here rather than silently generate a rectangle-area question
    the solver will then score against whatever scenario it was asked for.
    """
    return GEOMETRY_HEADER + "\n" + SCENARIO_BLOCKS[scenario] + GEOMETRY_FOOTER


solution = -1

# Maps each difficulty tier to the scenario numbers under EASY/MEDIUM/HARD
# TOPICS in the prompt above. Scenarios 14 and 15 (triangle missing-side
# area/perimeter) aren't listed there by name but belong to the same
# "solve for a missing side" family as the other MEDIUM scenarios.
DIFFICULTY_SCENARIOS = {
    "easy":   [1, 2, 3, 4, 5, 6],
    "medium": [10, 11, 12, 13, 14, 15, 16],
    "hard":   [7, 8, 9, 17, 18],
}

# Circle, 3D volume, and pythagorean-theorem scenarios need formulas grades
# 1-3 haven't reached, so "early" band is restricted to flat rectangle/
# triangle area and perimeter -- roughly where grade-3 geometry standards
# land. Grades 1-2 in that band get the ceiling of what this topic can offer
# them, since bands are coarser than a single grade.
EARLY_BAND_SCENARIOS = {1, 2, 3, 4}

def _pick_scenario(difficulty, grade_band):
    candidates = DIFFICULTY_SCENARIOS.get(difficulty, DIFFICULTY_SCENARIOS["medium"])
    if grade_band == "early":
        candidates = [s for s in candidates if s in EARLY_BAND_SCENARIOS] or sorted(EARLY_BAND_SCENARIOS)
    return random.choice(candidates)

# Difficulty governs which scenario gets picked above; grade controls the
# magnitude of the given measurements (side lengths, radii, etc.) within
# whatever scenario gets chosen.
def _grade_band(grade):
    # Delegated so ten copies of this cannot drift apart, and so an
    # unreadable grade ("Grade 1") lands in "early" rather than
    # "advanced" -- profiles.grade_level is free text. See grade_levels.
    return grade_levels.grade_band(grade)

GRADE_COMPLEXITY = {
    "early":    "Keep all given measurements (lengths, radii, etc.) between 1 and 12.",
    "middle":   "Measurements may range from 1 to 30.",
    "upper":    "Measurements may range from 1 to 100.",
    "advanced": "No additional restriction.",
}
# Kept separate from EARLY_BAND_SCENARIOS above rather than folded into one
# dict: this scales a number, that gates which formulas are even in play,
# and conflating them would make either change look like it needs the other.


def generate_geometry_question(global_questions, prev_questions, difficulty, grade,max_retries=3):
    for attempt in range(max_retries):
        # Scenario first: the prompt is now built around it rather than
        # listing every scenario and naming one at the end.
        grade_band = _grade_band(grade)
        scenario = _pick_scenario(difficulty, grade_band)

        prompt = _geometry_prompt(scenario)
        if attempt > 0:
            prompt += "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."

        prompt += (
            "\nPreviously generated questions:\n"
            + "\n".join(q["text"] for q in prev_questions)
            + "\n\nRecent global questions:\n"
            + "\n".join(q["text"] for q in global_questions)
            + "\n\nDO NOT generate a question matching any of the above. Use different wording and numerical values."
        )
        prompt += (
            f"\nGenerate a question of this topic that a {grade} student would consider to be of {difficulty} difficulty.\n"
        )
        prompt += (
            f"\nMAGNITUDE FOR THIS GRADE LEVEL: "
            f"{GRADE_COMPLEXITY[grade_band]}\n"
        )
        prompt = lesson_plan_context.append_lesson_context(prompt, "geometry", grade_band)
        response_text = llm_client.generate_text(prompt)

        print(response_text)

        raw = extract_json(response_text)

        if not raw:
            print(f"[Attempt {attempt+1}] No JSON found")
            print(response_text)
            continue

        try:
            question_data = json.loads(raw)
        except Exception as e:
            print(f"[Attempt {attempt+1}] JSON parse failed:", e)
            print(response_text)
            continue

        required_keys = ["scenario", "variables", "question_text"]
        if not all(k in question_data for k in required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", question_data)
            continue

        # Backstop on what the model actually produced, not just on what
        # the prompt asked for -- see grade_appropriateness.
        if grade_appropriateness.refuse(question_data.get("question_text"),
                                        "geometry", grade_band, difficulty,
                                        attempt + 1):
            continue

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    scenario = question_data["scenario"]
    vars = question_data["variables"]
    vars = preprocess_variables(vars)

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

    solution = normalize_solution(solution)
    solution_float = float(solution)
    solution = format_two_decimals(solution)
    incorrect_answers = inc_gen.generate_general_incorrect_answers(solution_float)
    answers = [round(float(ans), 2) for ans in incorrect_answers] + [solution]

    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "geometry",
        "answer_options": answers,
        "correct_answer": solution
    }

