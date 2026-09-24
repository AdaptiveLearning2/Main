import os
import re
import random
from supabase import create_client, Client
from dotenv import load_dotenv
import llm_client
import question_schemas
import json
from flask import Flask, jsonify
from flask_cors import CORS
import sympy as sp
from sympy import symbols, Eq, solve, sympify, Integer
from sympy.parsing.sympy_parser import (
    parse_expr,
    standard_transformations,
    implicit_multiplication_application
)
import incorrect_solution_generation as inc_gen
# The solve path lives in `angle_solvers`, which the bounded worker imports.
import lesson_plan_context
import angle_solvers
import safe_solve
import grade_levels
import ccss_standards
import scenario_tiers
import grade_appropriateness

transformations = (standard_transformations + (implicit_multiplication_application,))

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

def normalize_solution(sol):
    if isinstance(sol, list):
        sol = sol[0]

    if isinstance(sol, (sp.Integer, sp.Float)):
        return float(sol)

    if isinstance(sol, sp.Expr):
        return float(sol.evalf())

    return float(sol)

def format_answer(x):
    if x is None:
        return None
    x = float(x)
    if x.is_integer():
        return str(int(x))
    return f"{round(x, 2)}"

# Only the selected scenario's block is sent. Plain strings, not f-strings, so
# braces are literal.
ANGLE_HEADER = """
You are to provide a Math question suitable for students. The response must be in JSON format.
The Question Text, Question Topic, Scenario, and Variables will be displayed. The Question Topic will always be "angle_relationships".
Generate a question for the one scenario given below.
"""

SCENARIO_BLOCKS = {
    1: """Scenario 1: complementary
"Two angles are complementary. One angle is 35Â°. What is the other angle?"
JSON for this scenario must follow this exact structure:
{
  "question_text": "Two angles are complementary. One angle is 35Â°. What is the other angle?",
  "question_topic": "angle_relationships",
  "scenario": "complementary",
  "variables": ["35"]
}
""",

    2: """Scenario 2: supplementary
"Two angles are supplementary. One angle is 135Â°. What is the other angle?"
JSON for this scenario must follow this exact structure:
{
  "question_text": "Two angles are supplementary. One angle is 135Â°. What is the other angle?",
  "question_topic": "angle_relationships",
  "scenario": "supplementary",
  "variables": ["135"]
}
""",

    3: """Scenario 3: linear_pair
"Two angles form a straight line. One angle is 140Â°. What is the other angle?"
JSON for this scenario must follow this exact structure:
{
  "question_text": "Two angles form a straight line. One angle is 140Â°. What is the other angle?",
  "question_topic": "angle_relationships",
  "scenario": "linear_pair",
  "variables": ["140"]
}
""",

    4: """Scenario 4: triangle_sum
"A triangle has angles 50Â° and 60Â°. What is the third angle?"
JSON for this scenario must follow this exact structure:
{
  "question_text": "A triangle has angles 50Â° and 60Â°. What is the third angle?",
  "question_topic": "angle_relationships",
  "scenario": "triangle_sum",
  "variables": ["50", "60"]
}
""",

    5: """Scenario 5: algebra_complementary
"Two angles are complementary: (x + 10)Â° and (2x âˆ’ 20)Â°. Find x."
JSON for this scenario must follow this exact structure:
{
  "question_text": "Two angles are complementary: (x + 10)Â° and (2x âˆ’ 20)Â°. Find x.",
  "question_topic": "angle_relationships",
  "scenario": "algebra_complementary",
  "variables": ["x + 10", "2x - 20"]
}
""",

}

ANGLE_FOOTER = """
Return ONLY valid JSON with no text before or after the JSON object.

Rules:
- Generate ONLY ONE question, return ONLY ONE JSON object.
- Use ONLY double quotes for all strings.
- The JSON object must contain the keys "question_text", "question_topic", "scenario", and "variables".
- "variables" must be a list of strings.
- Do NOT include any text or characters outside the JSON object.
"""


def _angle_prompt(scenario):
    """Header + the selected scenario's block + footer. KeyError on an unknown scenario."""
    return ANGLE_HEADER + "\n" + SCENARIO_BLOCKS[scenario] + ANGLE_FOOTER


solution = -1

# Block number -> scenario name; cross-checked against the blocks by
# `test_the_names_match_the_blocks_they_send`.
_SCENARIO_NAMES = {
    1: "complementary",
    2: "supplementary",
    3: "linear_pair",
    4: "triangle_sum",
    5: "algebra_complementary",
}

# Grade each scenario is introduced. Per scenario, because triangle_sum (8.G.5)
# arrives a grade after the rest of the topic (7.G.5).
SCENARIO_MIN_GRADE = {
    "complementary":         7,   # 7.G.5
    "supplementary":         7,   # 7.G.5
    "linear_pair":           7,   # 7.G.5
    "triangle_sum":          8,   # 8.G.5, angle sum of a triangle
    "algebra_complementary": 7,   # 7.G.5 with 7.EE.4, solve for x
}


def _grade_scenarios(grade):
    """Scenario numbers whose relationship this student has reached.

    Below the lowest minimum (including an unreadable grade), the grade-7 set.
    """
    number = grade_levels.grade_number(grade)
    if number is None:
        number = 1
    allowed = {n for n, name in _SCENARIO_NAMES.items()
               if SCENARIO_MIN_GRADE[name] <= number}
    if allowed:
        return allowed
    floor = min(SCENARIO_MIN_GRADE.values())
    return {n for n, name in _SCENARIO_NAMES.items()
            if SCENARIO_MIN_GRADE[name] <= floor}


# Difficulty, not grade: triangle_sum is later (8.G.5) but easier than algebra_complementary.
SCENARIO_DIFFICULTY = {
    "complementary":         1,   # subtract from 90
    "supplementary":         1,   # subtract from 180
    "linear_pair":           1,
    "triangle_sum":          2,   # subtract two from 180
    "algebra_complementary": 3,   # set up an equation and solve for x
}


def _pick_scenario(difficulty, grade):
    """A scenario for this difficulty, chosen from what this grade can see.

    Ranked and sliced after the grade filter, so `hard` never ends up easier
    than `medium` when the filter removes the hardest scenarios.
    """
    allowed = _grade_scenarios(grade)
    return random.choice(scenario_tiers.pick(
        difficulty, allowed,
        lambda number: SCENARIO_DIFFICULTY[_SCENARIO_NAMES[number]]))

def _grade_band(grade):
    # An unreadable grade like "Grade 1" falls back to "early", not "advanced".
    return grade_levels.grade_band(grade)

GRADE_COMPLEXITY = {
    "early":    "Use angle measures that are whole numbers between 10 and 80, in multiples of 5 for easy mental math.",
    "middle":   "Use angle measures that are whole numbers between 5 and 170.",
    "upper":    "No additional restriction on angle measures.",
    # Grades 9+: an empty restriction is not a harder one.
    "advanced": "Use angle measures that are whole numbers NOT divisible by 5 (e.g. 37, 112, 143), so the arithmetic cannot be done by inspection. For the algebraic scenario use coefficients between 2 and 9.",
}

# Whole degrees through grade 5 (and for an unreadable grade). Keyed on the grade
# number, not the band, because the "middle" band spans 4-6.
def _requires_whole_number_solution(grade):
    number = grade_levels.grade_number(grade)
    return number is None or number <= 5


def generate_angle_relationship_question(global_questions,prev_questions, difficulty, grade, max_retries=3):
    for attempt in range(max_retries):
        grade_band = _grade_band(grade)
        # The grade, not the band: triangle_sum is 8.G.5, the rest 7.G.5.
        scenario = _pick_scenario(difficulty, grade)

        prompt = _angle_prompt(scenario)
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
        if _requires_whole_number_solution(grade):
            # Saves retries; the check after the solve enforces it.
            prompt += (
                "\nThe ANSWER must be a whole number of degrees. Choose the "
                "given angle measures so the result has no decimal part.\n"
            )
        prompt = lesson_plan_context.append_lesson_context(prompt, "angle_relationships", grade_band)

        response_text = llm_client.generate_text(
            prompt, schema=question_schemas.angles(_SCENARIO_NAMES[scenario]))

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

        # Gate the scenario the model returned, not the one asked for: it can differ.
        if question_data["scenario"] not in {
                _SCENARIO_NAMES[n] for n in _grade_scenarios(grade)}:
            print(f"[Attempt {attempt+1}] Scenario above this grade:",
                  question_data["scenario"])
            continue

        # Backstop on what the model actually produced; see grade_appropriateness.
        if grade_appropriateness.refuse(question_data.get("question_text"),
                                        "angle_relationships", grade_band, difficulty,
                                        attempt + 1):
            continue

        # Solved inside the loop, so a grade-inappropriate answer is regenerated.
        scenario_name = question_data.get("scenario")
        # Bounded worker: `parse_expr("9**9**9")` holds the GIL. The degenerate-figure
        # check runs there too, since it needs the parsed expressions.
        solution = safe_solve.safe_solve_angle(scenario_name,
                                               question_data["variables"])
        if solution is None:
            print(f"[Attempt {attempt+1}] Unsolvable or invalid "
                  f"{scenario_name} question:",
                  repr(question_data["variables"])[:60])
            continue

        if _requires_whole_number_solution(grade) and not float(solution).is_integer():
            # Retry rather than round: a rounded answer disagrees with a correct calculation.
            print(f"[Attempt {attempt+1}] Non-whole-number answer "
                  f"({solution}) for a {grade} student")
            continue

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    solution = format_answer(solution)

    solution_float = float(solution) if solution is not None else None
    incorrect_answers = inc_gen.generate_general_incorrect_answers(solution_float) if solution_float is not None else []
    answers = [str(ans) for ans in incorrect_answers] + [str(solution)]
    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "angle_relationships",
        "ccss_standard": ccss_standards.ccss_for(
            "angle_relationships", grade, question_data.get("scenario")),
        "answer_options": answers,
        "correct_answer": solution
    }

