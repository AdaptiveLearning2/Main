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
from sympy import symbols, Eq, solve, sympify, Integer
from sympy.parsing.sympy_parser import (
    parse_expr,
    standard_transformations,
    implicit_multiplication_application
)
import incorrect_solution_generation as inc_gen
# The solve path -- the five solvers, the parse and the degenerate-figure
# check -- lives in `angle_solvers`, which the bounded worker imports.
import lesson_plan_context
import angle_solvers
import safe_solve
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

# Only the selected scenario's block is sent -- see the same note in
# LLM_geometry_generation.py. _pick_scenario has already applied difficulty
# and the grade-band restriction by the time this is built, so the other
# scenarios were worked examples for questions the model was not being asked
# to write. Plain strings, not f-strings: the original was an f-string with
# no interpolation, so its `{{`/`}}` were escapes for single braces.
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
    """Header + the one selected scenario's block + footer. KeyError on an
    unknown scenario, for the reason _geometry_prompt documents."""
    return ANGLE_HEADER + "\n" + SCENARIO_BLOCKS[scenario] + ANGLE_FOOTER


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


def generate_angle_relationship_question(global_questions,prev_questions, difficulty, grade, max_retries=3):
    for attempt in range(max_retries):
        # select a scenario from the tier matching this question's difficulty
        # and grade; the prompt is built around it rather than listing all five
        grade_band = _grade_band(grade)
        scenario = _pick_scenario(difficulty, grade_band)

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
            # Asking costs nothing and saves retries; the check after the
            # solve is what actually enforces it.
            prompt += (
                "\nThe ANSWER must be a whole number of degrees. Choose the "
                "given angle measures so the result has no decimal part.\n"
            )
        prompt = lesson_plan_context.append_lesson_context(prompt, "angle_relationships", grade_band)

        response_text = llm_client.generate_text(prompt)

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
                                        "angle_relationships", grade_band, difficulty,
                                        attempt + 1):
            continue

        # Solved here, inside the loop, so a question with a grade-inappropriate
        # answer can be regenerated. The prompt asks for whole-number answers
        # but isn't reliably obeyed -- measured 2026-08-18:
        # (5x+15)+(3x-20)=90 came back as 11.875 -- so it's checked in code.
        scenario_name = question_data.get("scenario")
        # Parsed and solved in the bounded worker. `parse_expr` on the model's
        # variable strings is the unbounded step -- `parse_expr("9**9**9")`
        # never returns, and holds the GIL while it does not, so no watchdog
        # thread can stop it. The degenerate-figure check goes with it because
        # it needs the parsed expressions: `algebra_complementary` substitutes
        # the solved `x` back into both angle expressions, which is impossible
        # once only a float has crossed the process boundary.
        solution = safe_solve.safe_solve_angle(scenario_name,
                                               question_data["variables"])
        if solution is None:
            print(f"[Attempt {attempt+1}] Unsolvable or invalid "
                  f"{scenario_name} question:",
                  repr(question_data["variables"])[:60])
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

