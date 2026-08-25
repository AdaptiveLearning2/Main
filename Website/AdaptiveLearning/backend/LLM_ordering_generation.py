# Generates ordering questions via LLM and calculates the result.
import os
import re
import ast
import random
from supabase import create_client, Client #pip install supabase
from dotenv import load_dotenv   #pip install dotenv
import llm_client
import json
from flask import Flask, jsonify
from flask_cors import CORS #pip install flask-cors
import sympy as sp #pip install sympy
from sympy import symbols, Eq, solve, sympify, Integer
import lesson_plan_context
import grade_levels
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
def normalize(value):
    try: 
        return float(sympify(value))
    except: 
        raise ValueError(f"Invalid value: {value}")
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

def solve_ordering(values, direction="least_to_greatest"):
    normalized = [(v, normalize(v)) for v in values] # treat strings from JSON as floats

    sorted_vals = sorted(normalized, 
                         key=lambda x:x[1],
                         reverse=(direction=="greatest_to_least"))

    return [v[0] for v in sorted_vals]

def shuffle_incorrect_answers(solution):
    incorrect_answers = set()

    while len(incorrect_answers) < 3:
        shuffled = solution.copy()
        random.shuffle(shuffled)

        if shuffled == solution:
            shuffled = solution.copy()
            shuffled[0], shuffled[1] = shuffled[1], shuffled[0]
        
        incorrect_answers.add(tuple(shuffled))

    return [list(ans) for ans in incorrect_answers]

def _grade_band(grade):
    # Delegated so ten copies of this cannot drift apart, and so an
    # unreadable grade ("Grade 1") lands in "early" rather than
    # "advanced" -- profiles.grade_level is free text. See grade_levels.
    return grade_levels.grade_band(grade)

# Difficulty changes which math concept is used, not just how big the
# numbers are. Decimals are roughly a grade-4+ concept and fractions
# grade-3+, so each grade band defines its own easy/medium/hard tiers --
# otherwise scaling magnitude alone could put fraction comparisons in
# front of a 1st grader just because that grade allows bigger numbers.
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
    "advanced": {
        "easy":   "Use 3-4 values. Use ONLY whole numbers or simple one-decimal-place values. No fractions.",
        "medium": "Use 4-5 values. Include a mix of decimals (up to two decimal places) and simple fractions.",
        "hard":   "Use 5-6 values. Include a mix of decimals (up to two decimal places), fractions, and at least one negative value.",
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

        required_keys = ["values", "question_text", "direction"]
        if not all(k in question_data for k in required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", question_data)
            continue

        # Backstop on what the model actually produced, not just on what
        # the prompt asked for -- see grade_appropriateness.
        if grade_appropriateness.refuse(question_data.get("question_text"),
                                        "ordering", grade_band, difficulty,
                                        attempt + 1):
            continue

        # The student reads question_text but is scored against values --
        # check the numbers in the text match the scored dataset.
        inconsistent = question_consistency.dataset_mismatch(
            question_data.get("question_text"), question_data.get("values"))
        if inconsistent:
            print(f"[Attempt {attempt+1}] Inconsistent question: {inconsistent}")
            continue

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    if question_data:
        solution = solve_ordering(question_data["values"], question_data["direction"])

    incorrect_answers = shuffle_incorrect_answers(solution)
    answers = incorrect_answers + [solution]
    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "ordering",
        "answer_options": answers,
        "correct_answer": solution
    }
