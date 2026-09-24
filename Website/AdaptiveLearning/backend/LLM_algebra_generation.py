# Generates an algebra question via LLM, solves it with sympy, and builds answer options.

import os
import re
import random
from supabase import create_client, Client
from dotenv import load_dotenv
import llm_client
from llm_json import extract_json
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
) # treat 2x as 2*x for sympy parsing
import incorrect_solution_generation as inc_gen
import lesson_plan_context
import safe_solve
import token_join
import grade_levels
import ccss_standards

transformations = (standard_transformations + (implicit_multiplication_application,))

def to_native(value): 
    if isinstance(value, Integer): 
        return int(value) 
    return value
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

def _solve_equation(variables, attempt):
    """The equation's single solution as a string, or None to retry.

    None for: no solution, more than one (a quadratic would mark a correct root
    wrong), a non-numeric solution, a malformed equation, or non-scalar tokens.
    """
    equation_str = token_join.join_tokens(variables)
    if equation_str is None:
        print(f"[Attempt {attempt}] Unusable variables: {variables!r:.80}")
        return None
    # Bounded subprocess: `9**9**9` spins holding the GIL, so only a kill stops it.
    # Split, parse, solve and the one-solution check all run in the worker.
    solved = safe_solve.safe_solve(equation_str, "equation")
    if solved is None:
        print(f"[Attempt {attempt}] Unsolvable equation: {equation_str[:80]!r}")
        return None
    return solved


def _grade_band(grade):
    # An unreadable grade like "Grade 1" falls back to "early", not "advanced".
    return grade_levels.grade_band(grade)

# LLM_topic_decider keeps algebra away from grades below 6; "early"/"middle"
# are a fallback if that gate is bypassed.
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
    # Grades 9+, capped at grade-8 content: a solver limit, not a prompt one.
    "advanced": {
        "easy":   "Use a TWO-STEP equation with at least one NEGATIVE constant (e.g. 5x - 12 = 33). Coefficients and constants between 2 and 60.",
        "medium": "Distribute over one set of parentheses (e.g. 4(x - 3) + 2x = 26) and include at least one negative constant. Coefficients between 2 and 40.",
        "hard":   "Put x on BOTH sides AND distribute over at least one set of parentheses (e.g. 5(x - 2) + 3x = 2(x + 6) - 4). Use at least one negative or fractional coefficient. The equation must still have EXACTLY ONE solution -- no quadratics.",
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

        response_text = llm_client.generate_text(
            prompt, schema=question_schemas.token_list("algebra"))

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

        required_keys = ["variables", "question_text"]
        if not all(k in question_data for k in required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", question_data)
            continue

        # Solved inside the loop, so an unscorable equation is a retry, not a 500.
        solution = _solve_equation(question_data["variables"], attempt + 1)
        if solution is None:
            continue

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    # The worker answers exactly ("3/2"); decimal distractors would leave it the only fraction.
    if "/" in str(solution):
        incorrect_answers = inc_gen.generate_incorrect_rational(solution)
    else:
        incorrect_answers = inc_gen.generate_general_incorrect_answers(solution)
    answers = [str(ans) for ans in incorrect_answers] + [str(solution)]

    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "algebra",
        "ccss_standard": ccss_standards.ccss_for("algebra", grade),
        "answer_options": answers,
        "correct_answer": solution
    }

