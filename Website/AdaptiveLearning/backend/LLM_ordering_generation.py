# Generates ordering questions via LLM and calculates the result.
import os
import re
import ast
import itertools
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
import lesson_plan_context
import safe_solve
import grade_levels
import ccss_standards
import grade_appropriateness
import question_consistency
from sympy.parsing.sympy_parser import (
    parse_expr,
    standard_transformations,
    implicit_multiplication_application
) # treat 2x as 2*x for sympy parsing

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

ordering_prompt = f"""
You are to provide a Math question suitable for students. The response must be in JSON format. 
The Question Text, Question Topic, and Variables will be displayed. The Question Topic will be "ordering".

Ordering example question: "Order from least to greatest: 3/6, 0.6, 2/3, 0.75". The question text should display the direction
(least_to_greatest or greatest_to_least) as well as every value to be ordered. There should not be equivalent values, for example 0.5 and 1/2 should not
both be values for a single question. All values should be numeric. Values may go to two decimal places.
The number of values and which value types to use (whole numbers, decimals, fractions, negatives) are given below under COMPLEXITY FOR THIS DIFFICULTY -- follow that.
Return ONLY valid JSON with no text before or after the JSON object.

The JSON must follow this exact structure:

{{
  "question_text": "Order from least to greatest: 3/6, 0.6, 2/3, 0.75",
  "question_topic": "ordering",
  "direction": "least_to_greatest", 
  "values": ["3/4", "0.6", "2/3", "0.75"]
}}

Rules:
- Use ONLY double quotes for all strings.
- The JSON object must contain the keys "question_text", "question_topic", "direction", and "values".
- "values" must be a list of strings.
- Do NOT include any characters outside the JSON object.
"""

def solve_ordering(values, numbers, direction="least_to_greatest"):
    """`values` sorted by the matching entry in `numbers`, returned as the model's own strings.

    `numbers` is parsed by the bounded worker, since `sympify` on model text can hang.
    """
    normalized = list(zip(values, numbers))

    sorted_vals = sorted(normalized, 
                         key=lambda x:x[1],
                         reverse=(direction=="greatest_to_least"))

    return [v[0] for v in sorted_vals]

def shuffle_incorrect_answers(solution):
    """Up to three wrong orderings, enumerated rather than sampled so it always terminates."""
    wrong = [list(p) for p in itertools.permutations(solution)
             if list(p) != solution]
    random.shuffle(wrong)
    return wrong[:3]

def _grade_band(grade):
    # Shared so copies can't drift; profiles.grade_level is free text. See grade_levels.
    return grade_levels.grade_band(grade)

# Difficulty changes the concept (decimals, fractions, negatives), not just magnitude,
# so each grade band has its own tiers.
COMPLEXITY_BY_GRADE = {
    "early": {
        "easy":   "Use 3 values, whole numbers below 20 only. No decimals, no fractions, no negatives.",
        "medium": "Use 4 values, whole numbers below 50 only. No decimals, no fractions, no negatives.",
        "hard":   "Use 4-5 values, whole numbers below 100 only. No decimals, no fractions, no negatives.",
    },
    "middle": {
        "easy":   "Use 3-4 values. Use ONLY whole numbers or simple one-decimal-place values (e.g. 4, 7, 2.5). No fractions. Keep magnitude below 100.",
        "medium": "Use 4-5 values. Include a mix of decimals (up to two decimal places) and simple fractions (e.g. 1/2, 3/4). Keep magnitude below 100.",
        "hard":   "Use 5-6 values. Include a mix of decimals (up to two decimal places) and simple fractions. Keep magnitude below 100.",
    },
    "upper": {
        "easy":   "Use 3-4 values. Use ONLY whole numbers or simple one-decimal-place values. No fractions. Magnitude up to 200.",
        "medium": "Use 4-5 values. Include a mix of decimals (up to two decimal places) and simple fractions. Magnitude up to 200.",
        "hard":   "Use 5-6 values. Include a mix of decimals (up to two decimal places), fractions, and at least one negative value. Magnitude up to 200.",
    },
    # Grades 9+, but content tops out at grade 8: a solver limit, not a prompt one.
    "advanced": {
        "easy":   "Use 4-5 values mixing whole numbers and decimals, including at least one NEGATIVE value.",
        "medium": "Use 5-6 values mixing decimals to two places, simple fractions, and at least one negative.",
        "hard":   "Use 6 values mixing decimals to two places, fractions with UNLIKE denominators, and at least TWO negatives. At least two of the values must be within 0.1 of each other, so they cannot be ordered at a glance.",
    },
}

def generate_ordering_question(global_questions, prev_questions,difficulty, grade, max_retries=3):
    for attempt in range(max_retries):
        if attempt > 0:
            prompt = ordering_prompt + "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."
        else:
            prompt = ordering_prompt


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
        prompt = lesson_plan_context.append_lesson_context(prompt, "ordering", grade_band)
        response_text = llm_client.generate_text(
            prompt, schema=question_schemas.ordering())

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

        required_keys = ["values", "question_text", "direction"]
        if not all(k in question_data for k in required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", question_data)
            continue

        # Backstop on what the model produced, not what the prompt asked for.
        if grade_appropriateness.refuse(question_data.get("question_text"),
                                        "ordering", grade_band, difficulty,
                                        attempt + 1):
            continue

        # The student reads question_text but is scored against values.
        inconsistent = question_consistency.dataset_mismatch(
            question_data.get("question_text"), question_data.get("values"))
        if inconsistent:
            print(f"[Attempt {attempt+1}] Inconsistent question: {inconsistent}")
            continue

        # Two values have only one wrong order, so one distractor at most.
        if len(question_data.get("values") or []) < 3:
            print(f"[Attempt {attempt+1}] Too few values to order:",
                  repr(question_data.get("values"))[:60])
            continue

        # Parsed in the bounded worker, so an unbounded value is a retry, not a hang.
        numbers = safe_solve.safe_sympify_values(question_data["values"])
        if numbers is None:
            print(f"[Attempt {attempt+1}] Unusable values:",
                  repr(question_data["values"])[:80])
            continue

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    solution = solve_ordering(question_data["values"], numbers,
                              question_data["direction"])

    incorrect_answers = shuffle_incorrect_answers(solution)
    answers = incorrect_answers + [solution]
    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "ordering",
        "ccss_standard": ccss_standards.ccss_for("ordering", grade),
        "answer_options": answers,
        "correct_answer": solution
    }
