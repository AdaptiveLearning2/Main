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
import grade_appropriateness
 
# Enable implicit multiplication (2x â†’ 2*x)
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
    # sympy solve() often returns lists
    if isinstance(sol, list):
        sol = sol[0]

    # sympy types â†’ python scalar
    if isinstance(sol, (sp.Integer, sp.Float)):
        return float(sol)

    if isinstance(sol, sp.Expr):
        return float(sol.evalf())

    return float(sol)

def format_answer(x):
    if x is None:
        return None
    x = float(x)
    # check if it's basically an integer
    if x.is_integer():
        return str(int(x))
    # otherwise keep decimals clean
    return f"{round(x, 2)}"

def preprocess_variables(vars):
    parsed = []
    for v in vars:
        parsed.append(parse_expr(v, transformations=transformations))
    return parsed


def complementary_angle(a):
    return 90 - a

def supplementary_angle(a): #POSSIBLY will change since its the same calculation as linear
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

# Scenario 5 (algebra_complementary) requires setting up and solving an
# equation for x -- it was gated to "hard" difficulty only, but nothing kept
# a grade-1 student marked "hard" from landing on it: difficulty and grade
# are independent inputs, so a struggling-topic or randomized "hard" pick
# could reach it regardless of grade. Withheld from "early"/"middle" bands
# (grades 1-6) here, with a fallback to the medium tier's non-algebraic
# scenarios; "angle_relationships" isn't in LLM_topic_decider's grade-1-3
# allowlist either, so "early" reaching this function at all is already
# defense-in-depth.
def _pick_scenario(difficulty, grade_band):
    candidates = DIFFICULTY_SCENARIOS.get(difficulty, DIFFICULTY_SCENARIOS["medium"])
    if grade_band in ("early", "middle"):
        candidates = [s for s in candidates if s != 5] or DIFFICULTY_SCENARIOS["medium"]
    return random.choice(candidates)

def _grade_band(grade):
    g = (grade or "").strip().lower()
    if g in {"1st grade", "2nd grade", "3rd grade"}:
        return "early"
    if g in {"4th grade", "5th grade", "6th grade"}:
        return "middle"
    if g in {"7th grade", "8th grade"}:
        return "upper"
    return "advanced"

GRADE_COMPLEXITY = {
    "early":    "Use angle measures that are whole numbers between 10 and 80, in multiples of 5 for easy mental math.",
    "middle":   "Use angle measures that are whole numbers between 5 and 170.",
    "upper":    "No additional restriction on angle measures.",
    "advanced": "No additional restriction on angle measures.",
}

# Through 5th grade the ANSWER must come out to a whole number of degrees;
# from 6th, a decimal answer is ordinary mathematics rather than a defect.
#
# Keyed on the raw grade string, NOT on _grade_band(), because the line falls
# between grade 5 and grade 6 while the "middle" band spans 4, 5 AND 6 -- the
# same reason LLM_topic_decider._allowed_topics is grade-keyed rather than
# band-keyed. Using the band here would either impose whole numbers on a 6th
# grader or allow decimals for a 4th grader; there is no band boundary in the
# right place.
#
# An unrecognised grade (Highschool, College, anything else) falls through to
# False, matching _grade_band()'s own "advanced" default: the constraint is a
# scaffold for younger students, so the safe direction when the grade is
# unknown is to leave the mathematics alone rather than to constrain it.
WHOLE_NUMBER_SOLUTION_GRADES = {
    "1st grade", "2nd grade", "3rd grade", "4th grade", "5th grade",
}


def _requires_whole_number_solution(grade):
    return (grade or "").strip().lower() in WHOLE_NUMBER_SOLUTION_GRADES


def _solve_scenario(question_data):
    """The numeric answer for one parsed question, or None for a scenario
    this file does not recognise.

    Split out of the body below so the solve can happen INSIDE the retry
    loop: whether the answer is a whole number is a property of the solved
    value, not of the question text, so it cannot be checked until the
    scenario has been evaluated -- and a question that fails that check has
    to be regenerated rather than patched.
    """
    variables = preprocess_variables(question_data["variables"])
    match question_data["scenario"]:
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


#Potential improvements:
#Maybe can store previously generated question, feed into LLM to ensure next question is not the same.
#If solution is a fraction, at least one other generated response should be a fraction.
def generate_angle_relationship_question(global_questions,prev_questions, difficulty, grade, max_retries=3):
    for attempt in range(max_retries):
        if attempt > 0:
            prompt = angle_prompt + "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."
        else:
            prompt = angle_prompt


        #select a scenario from the tier matching this question's difficulty and grade.
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
                "temperature": 1.1, #more creativity
                "top_p": 0.95, #more diversity
                "top_k": 100 #broader token sampling.
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

        # Validate required keys
        required_keys = ["scenario", "variables", "question_text"]
        if not all(k in question_data for k in required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", question_data)
            continue

        # Backstop on what the model actually produced, not just on what the
        # prompt asked for -- see grade_appropriateness for why the prompt
        # alone isn't trusted here.
        violation = grade_appropriateness.find_violation(
            question_data.get("question_text"), "angle_relationships", grade_band)
        if violation:
            print(f"[Attempt {attempt+1}] Grade-inappropriate: {violation}")
            continue

        # Solved here rather than after the loop so a question whose ANSWER
        # is wrong for the grade can be regenerated. The prompt asks for
        # whole-number answers at these grades and is not reliably obeyed --
        # measured 2026-08-18, (5x+15)+(3x-20)=90 gives 11.875 -- which is
        # the same prompt-is-not-enforcement problem as everywhere else in
        # this codebase, so it gets the same treatment.
        try:
            solution = _solve_scenario(question_data)
        except Exception as e:
            print(f"[Attempt {attempt+1}] Could not solve: {e}")
            continue

        if solution is None:
            print(f"[Attempt {attempt+1}] Unrecognised scenario:",
                  question_data.get("scenario"))
            continue

        solution = normalize_solution(solution)

        if _requires_whole_number_solution(grade) and not float(solution).is_integer():
            # A decimal answer is fine from 6th grade; before that it is
            # arithmetic the student has not met, and rounding it for display
            # would make the correct answer disagree with a correct
            # calculation.
            print(f"[Attempt {attempt+1}] Non-whole-number answer "
                  f"({solution}) for a {grade} student")
            continue

        # If we reach here â†’ SUCCESS
        break

    else:
        # All retries failed
        raise ValueError("Failed to generate valid JSON after retries")

    solution = format_answer(solution)
    # solution = str(solution) if solution is not None else None

    # for attempt in range(max_retries):
    #     incorrect_solution_prompt = f"""
    #     Generate three incorrect numerical answer options for a math problem.
    #     Question:
    #     {question_data["question_text"]}
    #     Correct Answer:
    #     {solution}

    #     Rules:
    #     - NO additional text, characters, or symbols should accompany this response. Response should strictly include JSON formatted data.
    #     - The answers must NOT equal or simplify to {solution}
    #     - Unique numbers only. NUMBERS must be represented as strings. For example, "0.5" or "14" are valid representations.
    #     - Only numbers or simple numeric strings are allowed. Do NOT use brackets, fractions, or expressions.
    #     - No fractions or expressions
    #     - Return JSON format: each array value of incorrect_answers should be a separate incorrect answer
    #     {{
    #     "incorrect_answers": ["x","x","x"]
    #     }}
    #     """

    #     if (solution != None):
    #         answer_response = generate(model="llama3.1:8b",
    #                                 prompt=incorrect_solution_prompt,
    #                                 options = {"temperature": 0.4,
    #                                             "top_p": 0.9,
    #                                             "top_k": 40}) #slightly less randomness, 
    #     if attempt > 0:
    #         incorrect_solution_prompt += "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."

    #     raw = extract_json(answer_response.response)

    #     if not raw:
    #         print(f"[Attempt {attempt+1}] No JSON found")
    #         print(answer_response.response)
    #         continue

    #     try:
    #         answer_data = json.loads(raw)
    #     except Exception as e:
    #         print(f"[Attempt {attempt+1}] JSON parse failed:", e)
    #         print(answer_response.response)
    #         continue

    #     # Validate required keys
    #     required_keys = ["incorrect_answers"]
    #     if not all(k in answer_data for k in required_keys):
    #         print(f"[Attempt {attempt+1}] Missing keys:", answer_data)
    #         continue

    #     # If we reach here â†’ SUCCESS
    #     break

    # else:
    #     # All retries failed
    #     raise ValueError("Failed to generate valid JSON after retries")

    # #combining generated incorrect responses with correct solution. 
    # incorrect_data = answer_data
    #answers = incorrect_data["incorrect_answers"] + [str(solution)]
    solution_float = float(solution) if solution is not None else None
    incorrect_answers = inc_gen.generate_general_incorrect_answers(solution_float) if solution_float is not None else []
    answers = [str(ans) for ans in incorrect_answers] + [str(solution)]
    random.shuffle(answers)

    #Build final JSON
    return {
        "question_text": question_data["question_text"],
        "question_topic": "angle_relationships",
        "answer_options": answers,
        "correct_answer": solution
    }


#display on flask
# app= Flask(__name__)
# CORS(app)
# @app.route("/")
# def display_question():
#     return jsonify(generate_algebra_question())

