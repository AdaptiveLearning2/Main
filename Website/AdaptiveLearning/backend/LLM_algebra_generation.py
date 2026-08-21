# Generates an algebra question via LLM, solves it with sympy, and builds answer options.

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
) # treat 2x as 2*x for sympy parsing
import incorrect_solution_generation as inc_gen
import lesson_plan_context
import grade_levels

transformations = (standard_transformations + (implicit_multiplication_application,))

def to_native(value): 
    if isinstance(value, Integer): 
        return int(value) 
    return value
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

algebra_prompt = f"""
You are to provide a Math question suitable for students. The response must be in JSON format. 
The Question Text, Question Topic, and Variables will be displayed. The Question Topic will be "algebra".

Algebra example: "Solve for x: 2x + 3 = 7." 
The question should include the equation to be solved. Variables must be formatted as strings such as "x", and operations must be 
represented using the symbols "+", "-", "*", "/". For example, the variables list ["2x", "+", "3", "=", "7"] represents the equation 2x + 3 = 7.

The number of steps needed to solve the equation is given below under COMPLEXITY FOR THIS DIFFICULTY -- follow that, not a fixed step count.
Use a variety of integer values as long as the equation has a valid solution.

Return ONLY valid JSON with no text before or after the JSON object.

The JSON must follow this exact structure:

{{  "question_text": "Solve for x: 2x + 3 = 7.",
  "question_topic": "algebra",
  "variables": ["2x", "+", "3", "=", "7"]
}}

Rules:
- Use ONLY double quotes for all strings.
- The JSON object must contain the keys "question_text", "question_topic", and "variables".
- "variables" must be a list of strings.
- Do NOT include any characters outside the JSON object.
"""

solution = -1

def _grade_band(grade):
    # Shared with the other generation files so they can't drift apart.
    # An unreadable grade like "Grade 1" falls back to "early", not "advanced".
    return grade_levels.grade_band(grade)

# "Early" (grades 1-3) students haven't been taught equation-solving
# notation, so no value of x is "easy" for them -- the question shape itself
# has to change, not just the numbers. LLM_topic_decider already keeps
# algebra away from grades below 6, so the "early"/"middle" tiers here exist
# only as a fallback in case that gate is bypassed.
COMPLEXITY_BY_GRADE = {
    "early": {
        "easy":   "Use a ONE-STEP equation with a coefficient of 1 and values under 10 (e.g. x + 2 = 5). Frame it as a missing-number fact, not formal algebra.",
        "medium": "Use a ONE-STEP equation with a coefficient of 1 and values under 20 (e.g. x + 7 = 15).",
        "hard":   "Use a ONE-STEP equation with a coefficient of 1 and values under 20, including subtraction (e.g. x - 6 = 9).",
    },
    "middle": {
        "easy":   "Use a ONE-STEP equation: a single operation applied to x (e.g. x + a = b or x - a = b). The coefficient of x must be 1. Keep constants under 50.",
        "medium": "Use a TWO-STEP equation (e.g. ax + b = c) with the coefficient of x greater than 1. Keep constants under 100.",
        "hard":   "Use a TWO-STEP equation (e.g. ax + b = c) with the coefficient of x greater than 1, constants under 100. Keep x on one side only.",
    },
    "upper": {
        "easy":   "Use a TWO-STEP equation (e.g. ax + b = c). Constants and coefficients between 1 and 200. Negative coefficients are allowed.",
        "medium": "Use up to three operations on the left-hand side. Constants and coefficients between 1 and 200. Negative coefficients are allowed.",
        "hard":   "Use up to three operations on the left-hand side, and the variable x may appear on both sides of the equation (e.g. ax + b = cx + d). Constants and coefficients between 1 and 200. Negative coefficients are allowed.",
    },
    "advanced": {
        "easy":   "Use a TWO-STEP equation (e.g. ax + b = c). No additional magnitude restriction beyond what's typical for the equation.",
        "medium": "Use up to three operations on the left-hand side. No additional magnitude restriction beyond what's typical for the equation.",
        "hard":   "Use up to three operations on the left-hand side, and the variable x may appear on both sides of the equation (e.g. ax + b = cx + d). No additional magnitude restriction beyond what's typical for the equation.",
    },
}

def generate_algebra_question(global_questions, prev_questions, difficulty, grade, max_retries=3):
    for attempt in range(max_retries):
        if attempt > 0:
            prompt = algebra_prompt + "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."
        else:
            prompt = algebra_prompt

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
        grade_band = _grade_band(grade)
        prompt += (
            f"\nCOMPLEXITY FOR THIS GRADE AND DIFFICULTY: "
            f"{COMPLEXITY_BY_GRADE[grade_band].get(difficulty, COMPLEXITY_BY_GRADE[grade_band]['medium'])}\n"
        )

        prompt = lesson_plan_context.append_lesson_context(prompt, "algebra", grade_band)

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

        required_keys = ["variables", "question_text"]
        if not all(k in question_data for k in required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", question_data)
            continue

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    parts = question_data['variables']
    equation_stra = "".join(parts) # turn individual variables/operations into a single string to be parsed by sympy
    x = symbols('x')
    left, right = equation_stra.split('=')
    left_expr = parse_expr(left, transformations=transformations)
    right_expr = parse_expr(right, transformations=transformations)
    equation = Eq(left_expr, right_expr)
    solution = solve(equation, x)

    solution = str(solution[0]) if solution else None # solve returns a list; take the first solution if any

    incorrect_answers = inc_gen.generate_general_incorrect_answers(solution)
    answers = [str(ans) for ans in incorrect_answers] + [str(solution)]

    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "algebra",
        "answer_options": answers,
        "correct_answer": solution
    }

