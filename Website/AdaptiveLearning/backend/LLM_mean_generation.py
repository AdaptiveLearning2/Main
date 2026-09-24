# Generates a "mean" question via LLM and computes the average with sympy.

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
import incorrect_solution_generation as inc_gen
import answer_format
import lesson_plan_context
import safe_solve
import grade_levels
import ccss_standards
import grade_appropriateness
import question_consistency

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


def to_native(value): 
    if isinstance(value, Integer): 
        return int(value) 
    return value
mean_prompt = f"""
You are to provide a Math question suitable for students. The response must be in JSON format. 
The Question Text, Question Topic, and Variables will be displayed. The Question Topic will be "mean".

Mean example: "What is the mean of these values: 12, 15, 18, 21, 24" 
The question should include the listof values to be used when finding the solution. Each numeric value should be listed in the variables array.

Use a variety of integer values as long as the mean has a WHOLE number solution. To ensure this, the sum of the values should be divisible by the number of values. Dataset size and value magnitude are given below under COMPLEXITY FOR THIS DIFFICULTY -- follow that.

Return ONLY valid JSON with no text before or after the JSON object.

The JSON must follow this exact structure:

{{
  "question_text": "What is the mean of these values: 12, 15, 18, 21, 24",
  "question_topic": "mean",
  "variables": ["12","15","18","21","24"]
}}

Rules:
- Use ONLY double quotes for all strings.
- The JSON object must contain the keys "question_text", "question_topic", and "variables".
- "variables" must be a list of strings.
- No rationals or decimals allowed
- Do NOT include any text or characters outside the JSON object.
"""

solution = -1

def _grade_band(grade):
    # An unreadable grade ("Grade 1") lands in "early", not "advanced".
    return grade_levels.grade_band(grade)

# "early" is defense-in-depth: LLM_topic_decider withholds mean from grades 1-3.
COMPLEXITY_BY_GRADE = {
    "early": {
        "easy":   "Use 3 values, one or two-digit whole numbers under 20, that divide evenly (no remainder) for a whole-number average.",
        "medium": "Use 3-4 values, whole numbers under 30, that divide evenly for a whole-number average.",
        "hard":   "Use 4 values, whole numbers under 50, that divide evenly for a whole-number average.",
    },
    "middle": {
        "easy":   "Use 3-4 values, each a one or two-digit whole number. Use only positive whole numbers.",
        "medium": "Use 5-6 values, which may include two-digit or three-digit whole numbers. Use only positive whole numbers.",
        "hard":   "Use 6-7 values, which may include two-digit or three-digit whole numbers. Use only positive whole numbers.",
    },
    "upper": {
        "easy":   "Use 3-4 values, each a one or two-digit whole number.",
        "medium": "Use 5-6 values, which may include two-digit or three-digit whole numbers.",
        "hard":   "Use 7-8 values, which may include two-digit or three-digit whole numbers; negative whole numbers may be used (e.g. representing temperatures or scores relative to zero).",
    },
    # Grades 9+, capped at grade-8 content: a solver limit, not a prompt one.
    "advanced": {
        "easy":   "Use 4-5 values including at least one NEGATIVE number.",
        "medium": "Use 6-7 values including negatives and at least one value above 100.",
        "hard":   "Use 8-10 values including negatives and at least two three-digit values. The mean does NOT need to be a whole number.",
    },
}

def generate_mean_question(global_questions,prev_questions,difficulty,grade,max_retries=3):
    for attempt in range(max_retries):
        if attempt > 0:
            prompt = mean_prompt + "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."
        else:
            prompt = mean_prompt

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
        prompt = lesson_plan_context.append_lesson_context(prompt, "mean", grade_band)
        response_text = llm_client.generate_text(
            prompt, schema=question_schemas.dataset("mean"))

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

        # Backstop on what the model actually produced; see grade_appropriateness.
        if grade_appropriateness.refuse(question_data.get("question_text"),
                                        "mean", grade_band, difficulty,
                                        attempt + 1):
            continue

        # The student sees question_text but is scored on `variables`.
        inconsistent = question_consistency.dataset_mismatch(
            question_data.get("question_text"), question_data.get("variables"))
        if inconsistent:
            print(f"[Attempt {attempt+1}] Inconsistent question: {inconsistent}")
            continue

        # `sympify` on model values is unbounded, so it runs in the bounded worker.
        numbers = safe_solve.safe_sympify_values(question_data["variables"])
        if numbers is None:
            print(f"[Attempt {attempt+1}] Unusable variables:",
                  repr(question_data["variables"])[:80])
            continue

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    solution = sum(numbers) / len(numbers)

    incorrect_answers = inc_gen.generate_general_incorrect_answers(float(solution)) if solution is not None else []
    # One formatter for answer and distractors, or a whole mean shows as "6.0".
    solution = answer_format.format_value(solution) if solution is not None else None
    answers = [answer_format.format_value(ans) for ans in incorrect_answers] + [solution]

    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "mean",
        "ccss_standard": ccss_standards.ccss_for("mean", grade),
        "answer_options": answers,
        "correct_answer": solution
    }

