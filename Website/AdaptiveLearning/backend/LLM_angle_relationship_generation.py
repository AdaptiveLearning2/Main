import os
import re
import random
from supabase import create_client, Client #pip install supabase
from dotenv import load_dotenv   #pip install dotenv
from ollama import chat, generate
from ollama import ChatResponse
import json
from flask import Flask, jsonify
from flask_cors import CORS #pip install flask-cors
import sympy as sp #pip install sympy
from sympy import symbols, Eq, solve, sympify, Integer
from sympy.parsing.sympy_parser import (
    parse_expr,
    standard_transformations,
    implicit_multiplication_application
)
import incorrect_solution_generation as inc_gen
import lesson_plan_context
import grade_levels
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

def preprocess_variables(vars):
    parsed = []
    for v in vars:
        parsed.append(parse_expr(v, transformations=transformations))
    return parsed


def complementary_angle(a):
    return 90 - a

def supplementary_angle(a):
    return 180 - a

def linear_pair(a):
    return 180 - a

def triangle_missing_angle(a,b):
    return 180 - a - b

def solve_complementary(expr1, expr2):
    x = sp.symbols('x')
    equation = sp.Eq(expr1 + expr2, 90)
    result= sp.solve(equation, x)
    return result[0] if result else None

angle_prompt = f"""
You are to provide a Math question suitable for students. The response must be in JSON format. 
The Question Text, Question Topic, Scenario, and Variables will be displayed. The Question Topic will always be "angle_relationships".
There will be four possible scenarios to select from. You must select only ONE scenario to generate a question and corresponding JSON response for.

Scenario 1: complementary
"Two angles are complementary. One angle is 35Â°. What is the other angle?"
JSON for this scenario must follow this exact structure:
{{
  "question_text": "Two angles are complementary. One angle is 35Â°. What is the other angle?",
  "question_topic": "angle_relationships",
  "scenario": "complementary",
  "variables": ["35"]
}}

Scenario 2: supplementary
"Two angles are supplementary. One angle is 135Â°. What is the other angle?"
JSON for this scenario must follow this exact structure:
{{
  "question_text": "Two angles are supplementary. One angle is 135Â°. What is the other angle?",
  "question_topic": "angle_relationships",
  "scenario": "supplementary",
  "variables": ["135"]
}}

Scenario 3: linear_pair
"Two angles form a straight line. One angle is 140Â°. What is the other angle?"
JSON for this scenario must follow this exact structure:
{{
  "question_text": "Two angles form a straight line. One angle is 140Â°. What is the other angle?",
  "question_topic": "angle_relationships",
  "scenario": "linear_pair",
  "variables": ["140"]
}}

Scenario 4: triangle_sum
"A triangle has angles 50Â° and 60Â°. What is the third angle?"
JSON for this scenario must follow this exact structure:
{{
  "question_text": "A triangle has angles 50Â° and 60Â°. What is the third angle?",
  "question_topic": "angle_relationships",
  "scenario": "triangle_sum",
  "variables": ["50", "60"]
}}

Scenario 5: algebra_complementary
"Two angles are complementary: (x + 10)Â° and (2x âˆ’ 20)Â°. Find x."
JSON for this scenario must follow this exact structure:
{{
  "question_text": "Two angles are complementary: (x + 10)Â° and (2x âˆ’ 20)Â°. Find x.",
  "question_topic": "angle_relationships",
  "scenario": "algebra_complementary",
  "variables": ["x + 10", "2x - 20"]
}}

Return ONLY valid JSON with no text before or after the JSON object.

The JSON must follow this exact structure:

Rules:
- Select ONLY ONE scenario, Generate ONLY ONE question, return ONLY ONE JSON object. 
- Use ONLY double quotes for all strings.
- The JSON object must contain the keys "question_text", "question_topic", "scenario", and "variables".
- "variables" must be a list of strings.
- Do NOT include any text or characters outside the JSON object.
"""

solution = -1

# complementary/supplementary/linear_pair are all a single subtraction from a
# known constant (EASY). triangle_sum needs two known values combined (MEDIUM).
# algebra_complementary requires setting up and solving an equation for x (HARD).
DIFFICULTY_SCENARIOS = {
    "easy":   [1, 2, 3],
    "medium": [4],
    "hard":   [5],
}

# Scenario 5 (algebra_complementary) needs setting up and solving an
# equation for x, so it's withheld from "early"/"middle" grades (1-6) here
# regardless of difficulty -- difficulty alone isn't a safe gate, since a
# struggling-topic or randomized "hard" pick could still reach it at any
# grade. Falls back to the medium tier's non-algebraic scenarios instead.
def _pick_scenario(difficulty, grade_band):
    candidates = DIFFICULTY_SCENARIOS.get(difficulty, DIFFICULTY_SCENARIOS["medium"])
    if grade_band in ("early", "middle"):
        candidates = [s for s in candidates if s != 5] or DIFFICULTY_SCENARIOS["medium"]
    return random.choice(candidates)

def _grade_band(grade):
    # Shared with the other generation files so they can't drift apart.
    # An unreadable grade like "Grade 1" falls back to "early", not "advanced".
    return grade_levels.grade_band(grade)

GRADE_COMPLEXITY = {
    "early":    "Use angle measures that are whole numbers between 10 and 80, in multiples of 5 for easy mental math.",
    "middle":   "Use angle measures that are whole numbers between 5 and 170.",
    "upper":    "No additional restriction on angle measures.",
    "advanced": "No additional restriction on angle measures.",
}

# Through 5th grade, answers must be whole degrees; from 6th grade, a
# decimal answer is ordinary mathematics, not a defect.
#
# Keyed on the raw grade number, not _grade_band(), because the cutoff falls
# between grade 5 and grade 6 while the "middle" band spans 4-6. A band-based
# check would either force whole numbers on a 6th grader or allow decimals
# for a 4th grader.
#
# An unreadable grade defaults to requiring whole numbers, matching
# grade_levels' rule that an unknown student is treated as the youngest.
# Highschool and College still parse as grade 9 and 13, so they're unaffected.
def _requires_whole_number_solution(grade):
    number = grade_levels.grade_number(grade)
    return number is None or number <= 5


# What each scenario's angles must add up to. Every angle in the question --
# given or asked for -- has to be strictly between 0 and this total.
_SCENARIO_TOTAL = {
    "complementary":         90,
    "supplementary":         180,
    "linear_pair":           180,
    "triangle_sum":          180,
    "algebra_complementary": 90,
}


def _invalid_angle_reason(scenario, variables, solution):
    """Why this angle configuration is invalid, or None if it's fine.

    Measured 2026-08-18: a triangle question with angles 75 and 105 was
    generated with the answer 0 -- correct arithmetic, but not a real
    triangle, since 75 and 105 already use up the full 180 degrees.
    `complementary` has the same failure at 90. Keyed on each scenario's
    total (see _SCENARIO_TOTAL) so both are caught by one check.

    This is wrong at every grade, so it's checked unconditionally, before
    the whole-number rule below.

    Takes the already-parsed `variables` so sympy only parses once per
    attempt, instead of again inside `_solve_scenario`.
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

    if solution <= 0:
        return (f"the answer is {solution} degrees -- the given angles already "
                f"use the whole {total}-degree total, so there is no such figure")
    if solution >= total:
        return (f"the answer is {solution} degrees, which leaves nothing for "
                f"the other angle(s)")
    return None


def _solve_scenario(scenario, variables):
    """The numeric answer for one parsed question, or None for an
    unrecognised scenario.

    Split out so the solve happens inside the retry loop: whether the
    answer is a whole number can only be checked once the scenario has
    actually been solved, and a question that fails that check needs to be
    regenerated, not patched after the fact.
    """
    match scenario:
        case "complementary":
            return complementary_angle(variables[0])
        case "supplementary":
            return supplementary_angle(variables[0])
        case "linear_pair":
            return linear_pair(variables[0])
        case "triangle_sum":
            return triangle_missing_angle(variables[0], variables[1])
        case "algebra_complementary":
            return solve_complementary(variables[0], variables[1])
    return None


def generate_angle_relationship_question(global_questions,prev_questions, difficulty, grade, max_retries=3):
    for attempt in range(max_retries):
        if attempt > 0:
            prompt = angle_prompt + "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."
        else:
            prompt = angle_prompt

        # select a scenario from the tier matching this question's difficulty and grade
        grade_band = _grade_band(grade)
        scenario = _pick_scenario(difficulty, grade_band)

        prompt += f"\nYOU must generate a question for scenario {scenario}."
        print(scenario)


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
            # Asking costs nothing and saves retries; the check after the
            # solve is what actually enforces it.
            prompt += (
                "\nThe ANSWER must be a whole number of degrees. Choose the "
                "given angle measures so the result has no decimal part.\n"
            )
        prompt = lesson_plan_context.append_lesson_context(prompt, "angle_relationships", grade_band)

        response = generate(
            model="llama3.1:8b",
            prompt=prompt,
            options={
                "temperature": 1.1,
                "top_p": 0.95,
                "top_k": 100
            }
        )

        raw = extract_json(response.response)

        if not raw:
            print(f"[Attempt {attempt+1}] No JSON found")
            print(response.response)
            continue

        try:
            question_data = json.loads(raw)
        except Exception as e:
            print(f"[Attempt {attempt+1}] JSON parse failed:", e)
            print(response.response)
            continue

        required_keys = ["scenario", "variables", "question_text"]
        if not all(k in question_data for k in required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", question_data)
            continue

        # Backstop on what the model actually produced, not just on what
        # the prompt asked for -- see grade_appropriateness.
        if grade_appropriateness.refuse(question_data.get("question_text"),
                                        "angle_relationships", grade_band, difficulty,
                                        attempt + 1):
            continue

        # Solved here, inside the loop, so a question with a grade-inappropriate
        # answer can be regenerated. The prompt asks for whole-number answers
        # but isn't reliably obeyed -- measured 2026-08-18:
        # (5x+15)+(3x-20)=90 came back as 11.875 -- so it's checked in code.
        scenario_name = question_data.get("scenario")
        try:
            # Parsed once and handed to both, rather than each re-parsing
            # the same strings through sympy on every attempt.
            variables = preprocess_variables(question_data["variables"])
            solution = _solve_scenario(scenario_name, variables)
        except Exception as e:
            print(f"[Attempt {attempt+1}] Could not solve: {e}")
            continue

        if solution is None:
            print(f"[Attempt {attempt+1}] Unrecognised scenario:", scenario_name)
            continue

        solution = normalize_solution(solution)

        # Checked before the grade rule: a degenerate figure is wrong for
        # every student, not just a young one.
        invalid = _invalid_angle_reason(scenario_name, variables, solution)
        if invalid:
            print(f"[Attempt {attempt+1}] Invalid angle question: {invalid}")
            continue

        if _requires_whole_number_solution(grade) and not float(solution).is_integer():
            # A decimal answer is fine from 6th grade; before that it is
            # arithmetic the student has not met, and rounding it for display
            # would make the correct answer disagree with a correct
            # calculation.
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
        "answer_options": answers,
        "correct_answer": solution
    }

