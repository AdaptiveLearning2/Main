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
 
# Enable implicit multiplication (2x → 2*x)
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

    # sympy types → python scalar
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
"Two angles are complementary. One angle is 35°. What is the other angle?"
JSON for this scenario must follow this exact structure:
{{
  "question_text": "Two angles are complementary. One angle is 35°. What is the other angle?",
  "question_topic": "angle_relationships",
  "scenario": "complementary",
  "variables": ["35"]
}}

Scenario 2: supplementary
"Two angles are supplementary. One angle is 135°. What is the other angle?"
JSON for this scenario must follow this exact structure:
{{
  "question_text": "Two angles are supplementary. One angle is 135°. What is the other angle?",
  "question_topic": "angle_relationships",
  "scenario": "supplementary",
  "variables": ["135"]
}}

Scenario 3: linear_pair
"Two angles form a straight line. One angle is 140°. What is the other angle?"
JSON for this scenario must follow this exact structure:
{{
  "question_text": "Two angles form a straight line. One angle is 140°. What is the other angle?",
  "question_topic": "angle_relationships",
  "scenario": "linear_pair",
  "variables": ["140"]
}}

Scenario 4: triangle_sum
"A triangle has angles 50° and 60°. What is the third angle?"
JSON for this scenario must follow this exact structure:
{{
  "question_text": "A triangle has angles 50° and 60°. What is the third angle?",
  "question_topic": "angle_relationships",
  "scenario": "triangle_sum",
  "variables": ["50", "60"]
}}

Scenario 5: algebra_complementary
"Two angles are complementary: (x + 10)° and (2x − 20)°. Find x."
JSON for this scenario must follow this exact structure:
{{
  "question_text": "Two angles are complementary: (x + 10)° and (2x − 20)°. Find x.",
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


#Potential improvements:
#Maybe can store previously generated question, feed into LLM to ensure next question is not the same.
#If solution is a fraction, at least one other generated response should be a fraction. 
def generate_angle_relationship_question(global_questions,prev_questions, difficulty, grade, max_retries=3):
    for attempt in range(max_retries):
        if attempt > 0:
            prompt = angle_prompt + "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."
        else:
            prompt = angle_prompt


        #randomize scenario selection to ensure variety in generated questions.
        scenario = random.randint(1,5)

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

        # If we reach here → SUCCESS
        break

    else:
        # All retries failed
        raise ValueError("Failed to generate valid JSON after retries")

    scenario = question_data["scenario"]
    vars = question_data["variables"]
    vars = preprocess_variables(vars)

    match scenario: 
        case "complementary":
           solution = complementary_angle(vars[0])
        case "supplementary":
           solution = supplementary_angle(vars[0])
        case "linear_pair":
            solution = linear_pair(vars[0])
        case "triangle_sum":
           solution = triangle_missing_angle(vars[0], vars[1])
        case "algebra_complementary":
            solution = solve_complementary(vars[0], vars[1])
    
    
    solution = normalize_solution(solution)
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

    #     # If we reach here → SUCCESS
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

