# Generates area/perimeter/volume geometry questions via LLM and solves them with sympy.
import math
import os
import re
import random
from supabase import create_client, Client
from dotenv import load_dotenv
import llm_client
from llm_json import extract_json
import question_schemas
import question_figures
import json
from flask import Flask, jsonify
from flask_cors import CORS
import sympy as sp
from sympy import sqrt, symbols, Eq, solve, sympify, Integer, Rational, pi
import incorrect_solution_generation as inc_gen
import lesson_plan_context
import geometry_solvers
import safe_solve
import grade_levels
import ccss_standards
import scenario_tiers
import grade_appropriateness

# pi is approximated as 3.14.
# The solve path lives in `geometry_solvers`, which the bounded worker imports.
SCENARIO_VARS = geometry_solvers.SCENARIO_VARS
SOLVABLE_SCENARIOS = geometry_solvers.SOLVABLE_SCENARIOS


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


# Only the block _pick_scenario chose is sent; the others are phrasing templates
# the model would copy. Plain strings, not f-strings: don't double the braces.
GEOMETRY_HEADER = """
You are to provide a Math question suitable for students. The response must be in JSON format.
The Question Text, Question Topic, Scenario, Variables, and Target will be displayed. The Question Topic will always be "geometry".

Generate a question for the one scenario given below.

IMPORTANT:
- ALWAYS approximate pi as 3.14
- All numeric values must be simple (integers or one decimal max)
- Ensure the problem is solvable using the provided variables
"""

SCENARIO_BLOCKS = {
    19: """SCENARIO 19: rectangle_area_by_counting
Example:
"A rectangle is split into 3 rows of 4 same-size squares. How many squares is that in total?"

This scenario is for the youngest students. Ask ONLY how many squares fill the
rectangle -- do NOT use the words "area", "multiply" or "units squared", and do
NOT ask for a formula. Keep both numbers between 2 and 6.

{
  "question_text": "A rectangle is split into 3 rows of 4 same-size squares. How many squares is that in total?",
  "type": "geometry",
  "scenario": "rectangle_area_by_counting",
  "variables": {
    "rows": "3",
    "columns": "4"
  }
}
""",

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


# Block number -> scenario name; cross-checked against the blocks by
# `test_the_names_match_the_blocks_they_send`.
_SCENARIO_NAMES = {
    19: "rectangle_area_by_counting",
    1: "rectangle_area",
    2: "rectangle_perimeter",
    3: "triangle_area",
    4: "triangle_perimeter",
    5: "circle_area",
    6: "circle_circumference",
    7: "rect_volume",
    8: "cylinder_volume",
    9: "sphere_volume",
    10: "pythagorean",
    11: "rect_area_missing_side",
    12: "rect_perimeter_missing_side",
    13: "circle_area_missing_side",
    14: "triangle_area_missing_side",
    15: "triangle_perimeter_missing_side",
    16: "circle_circumference_missing_side",
    17: "cube_volume",
    18: "pyramid_volume",
}


def _solve_scenario(scenario, raw_vars, attempt):
    """The scenario's numeric solution, or None to retry.

    Runs in the bounded worker: `sympify` on `{"side": "9**9**9"}` holds the GIL
    and never returns.
    """
    value = safe_solve.safe_solve_geometry(scenario, raw_vars)
    if value is None:
        print(f"[Attempt {attempt}] Could not solve {scenario} from "
              f"{raw_vars!r:.60}")
    return value


def _geometry_prompt(scenario):
    """Header + the selected scenario's block + footer.

    KeyError on an unknown scenario: it means the scenario tables have drifted.
    """
    # Name and keys restated as a rule: the example alone let the model blend scenarios.
    name = _SCENARIO_NAMES[scenario]
    required = ", ".join(f'"{key}"' for key in SCENARIO_VARS[name])
    return (GEOMETRY_HEADER + "\n" + SCENARIO_BLOCKS[scenario] + GEOMETRY_FOOTER
            + f'- "scenario" MUST be exactly "{name}"\n'
            + f'- "variables" MUST contain exactly these keys: {required}\n'
            + "- Do NOT mix keys from another scenario; the value of each key "
              "must match what its name says it is\n")


solution = -1

# Grade each scenario's formula is introduced, by CCSS code. A scenario missing
# here fails `tests/test_early_band_geometry.py`.
SCENARIO_MIN_GRADE = {
    "rectangle_area_by_counting":        2,   # 2.G.2; the only one below grade 3
    "rectangle_area":                    3,   # 3.MD.7
    "rectangle_perimeter":               3,   # 3.MD.8
    "triangle_perimeter":                3,   # 3.MD.8
    "rect_area_missing_side":            4,   # 4.MD.3, unknown side from area
    "rect_perimeter_missing_side":       4,   # 4.MD.3
    "triangle_perimeter_missing_side":   4,   # 4.MD.3
    "rect_volume":                       5,   # 5.MD.5
    "cube_volume":                       5,   # 5.MD.5
    "triangle_area":                     6,   # 6.G.1
    "triangle_area_missing_side":        6,   # 6.G.1 inverted
    "circle_area":                       7,   # 7.G.4
    "circle_circumference":              7,   # 7.G.4
    "circle_area_missing_side":          7,   # 7.G.4 inverted
    "circle_circumference_missing_side": 7,   # 7.G.4 inverted
    "pythagorean":                       8,   # 8.G.7
    "cylinder_volume":                   8,   # 8.G.9
    "sphere_volume":                     8,   # 8.G.9
    "pyramid_volume":                    9,   # HS G-GMD.3; not in 8.G.9
}

# Top grade per band; used only when a band name is passed instead of a grade.
_BAND_CEILING = {"early": 3, "middle": 6, "upper": 8, "advanced": 13}


def _band_scenarios(grade):
    """Scenario numbers whose formula this student has reached.

    Takes a number, a string like "4th Grade", or a band name (its ceiling).
    """
    if grade in _BAND_CEILING:
        ceiling = _BAND_CEILING[grade]
    else:
        ceiling = grade_levels.served_grade_number(grade)
    allowed = {number_ for number_, name in _SCENARIO_NAMES.items()
               if SCENARIO_MIN_GRADE[name] <= ceiling}
    if allowed:
        return allowed
    # Below the easiest scenario: the easiest ones, since an empty set makes
    # `random.choice` raise.
    floor = min(SCENARIO_MIN_GRADE.values())
    return {number_ for number_, name in _SCENARIO_NAMES.items()
            if SCENARIO_MIN_GRADE[name] <= floor}


# How hard each scenario is to *do*, independent of the grade that teaches it.
SCENARIO_DIFFICULTY = {
    "rectangle_area_by_counting":        1,   # count the squares
    "rectangle_area":                    2,   # one multiplication
    "rectangle_perimeter":               2,
    "triangle_perimeter":                2,   # add three
    "triangle_area":                     3,   # multiply then halve
    "circle_circumference":              3,   # 2 pi r
    "triangle_perimeter_missing_side":   3,   # invert by subtracting
    "circle_area":                       4,   # pi r squared
    "rect_volume":                       4,   # multiply three
    "cube_volume":                       4,
    "rect_area_missing_side":            4,   # invert by dividing
    "rect_perimeter_missing_side":       4,
    "pyramid_volume":                    5,   # a third of base times height
    "cylinder_volume":                   5,   # pi r squared h
    "pythagorean":                       5,   # squares and a root
    "triangle_area_missing_side":        5,   # invert, with the halving
    "circle_circumference_missing_side": 5,   # invert, with pi
    "sphere_volume":                     6,   # four thirds pi r cubed
    "circle_area_missing_side":          6,   # invert, with pi and a root
}


def _pick_scenario(difficulty, grade):
    """A scenario for this difficulty, chosen from what this grade can see.

    Ranked and sliced after the grade filter, so `hard` never ends up easier
    than `medium` when the filter removes the hardest scenarios.
    """
    allowed = _band_scenarios(grade)
    return random.choice(scenario_tiers.pick(
        difficulty, allowed,
        lambda number: SCENARIO_DIFFICULTY[_SCENARIO_NAMES[number]]))

# Difficulty picks the scenario; the band scales the measurements.
def _grade_band(grade):
    # An unreadable grade ("Grade 1") lands in "early", not "advanced".
    return grade_levels.grade_band(grade)

GRADE_COMPLEXITY = {
    "early":    "Keep all given measurements (lengths, radii, etc.) between 1 and 12.",
    "middle":   "Measurements may range from 1 to 30.",
    "upper":    "Measurements may range from 1 to 100.",
    # Grades 9+: an empty restriction reads to the model as none, not as harder.
    "advanced": "Use two-digit measurements, and include one value with a decimal place (e.g. 12.5) so the arithmetic does not stay whole-number.",
}


def generate_geometry_question(global_questions, prev_questions, difficulty, grade,max_retries=3):
    for attempt in range(max_retries):
        grade_band = _grade_band(grade)
        # The grade, not the band: the band rounds a 4th grader up to grade 6.
        scenario = _pick_scenario(difficulty, grade)

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
        response_text = llm_client.generate_text(
            prompt, schema=question_schemas.geometry(_SCENARIO_NAMES[scenario]))

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

        # An unknown scenario name is a retry, not a 500 from the solver.
        if question_data["scenario"] not in SOLVABLE_SCENARIOS:
            print(f"[Attempt {attempt+1}] Unknown scenario:",
                  question_data["scenario"])
            continue

        # Gate the scenario returned, not just the block sent: the model can switch.
        if question_data["scenario"] not in {
                _SCENARIO_NAMES[n] for n in _band_scenarios(grade)}:
            print(f"[Attempt {attempt+1}] Scenario above this grade:",
                  question_data["scenario"])
            continue

        # Missing variables would KeyError in the solver; retry instead.
        missing = [k for k in SCENARIO_VARS[question_data["scenario"]]
                   if k not in (question_data.get("variables") or {})]
        if missing:
            print(f"[Attempt {attempt+1}] Scenario "
                  f"{question_data['scenario']} missing variables: {missing}")
            continue

        # Solved inside the loop, so every solve failure is another attempt.
        solution_float = _solve_scenario(question_data["scenario"],
                                         question_data["variables"],
                                         attempt + 1)
        if solution_float is None:
            continue

        # Backstop on what the model actually produced; see grade_appropriateness.
        if grade_appropriateness.refuse(question_data.get("question_text"),
                                        "geometry", grade_band, difficulty,
                                        attempt + 1):
            continue

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    solution = format_two_decimals(solution_float)
    incorrect_answers = inc_gen.generate_general_incorrect_answers(solution_float)
    # Every option a string, like the solution: `Adaptive.jsx` compares with a
    # type-sensitive JSON.stringify.
    answers = [format_two_decimals(float(ans)) for ans in incorrect_answers] \
        + [solution]

    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "geometry",
        "ccss_standard": ccss_standards.ccss_for(
            "geometry", grade, question_data["scenario"]),
        # From the same `variables` the solver used; None for most scenarios.
        "figure": question_figures.figure_for(question_data["scenario"],
                                              question_data["variables"]),
        "answer_options": answers,
        "correct_answer": solution
    }

