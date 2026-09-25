# Generates a "median" question via LLM and computes the median with sympy.

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
import lesson_plan_context
import safe_solve
import grade_levels
import ccss_standards
import grade_appropriateness
import question_consistency


def format_number(x):
    """`x` is already a float; the `sympify` fallback never sees model text."""
    if isinstance(x, list):
        x = x[0]
    val = float(x.evalf()) if hasattr(x, "evalf") else float(sympify(x))
    if abs(val - int(val)) < 1e-9:
        return str(int(val))
    return f"{val:.2f}"


median_prompt = f"""
You are to provide a Math question suitable for students. The response must be in JSON format. 
The Question Text, Question Topic, and Variables will be displayed. The Question Topic will be "median".

Median example: "A group of students recorded the number of minutes they spent studying each day for a week. Their times (in minutes) were: 32, 45, 28, 40, 35, 50, 30. What is the median study time?" 

The question should include the list of values to be used when finding the solution. Each numeric value should be listed in the variables array.

Use a variety of integer values as long as the median has a WHOLE number solution. This can be accomplished either by
1. Ensuring an odd number of values are in the array.
2. If there are an even number of values in the array, the sum of the middle two values should be evenly divisble by two.

Dataset size, and whether to use an odd or even count, are given below under COMPLEXITY FOR THIS DIFFICULTY -- follow that.


Return ONLY valid JSON with no text before or after the JSON object.

The JSON must follow this exact structure:

{{
  "question_text": "A group of students recorded the number of minutes they spent studying each day for a week. Their times (in minutes) were: 32, 45, 28, 40, 35, 50, 30. What is the median study time?",
  "question_topic": "median",
  "variables": ["32","45","28","40","35","50","30"]
}}

Rules:
- "question_text" must be a SINGLE LINE string, any newline characters inside the string is invalid.
- Use ONLY double quotes for all strings.
- ALL values in "variables" MUST be numeric strings (e.g., "12", "45")
- DO NOT use words like "red", "blue", or any non-numeric values
- If any value is not a number, the response is invalid
- The JSON object must contain the keys "question_text", "question_topic", and "variables".
- "variables" must be a list of strings.
- No rationals or decimals allowed
- Do NOT include any text or characters outside the JSON object.
"""

solution = -1

def median(values):
    """`values` are already numbers, parsed in the bounded worker."""
    vals = sorted(values)
    n = len(vals)

    if n%2 == 1:
        return vals[n//2]
    else:
        return (vals[n//2 -1] + vals[n//2]) / 2

# Odd-length datasets: pick a distinct existing value as a distractor.
# Even-length: fall back to the general incorrect-answer generator.
def generate_incorrect_answers(solution, values):
    incorrect_answers = []
    
    vals = sorted(values)
    n = len(vals)

    if n % 2 == 1:
        # No loop: `[5, 7, 9]` has only two non-median values.
        others = [v for v in dict.fromkeys(vals) if v != solution]
        random.shuffle(others)
        incorrect_answers = others[:3]

    if len(incorrect_answers) < 3:
        # Bounded and always returns three.
        incorrect_answers = (inc_gen.generate_general_incorrect_answers(float(solution))
                             if solution is not None else [])

    return incorrect_answers


def _grade_band(grade):
    # An unreadable grade ("Grade 1") lands in "early", not "advanced".
    return grade_levels.grade_band(grade)

# Even-length datasets add an averaging step, reserved for hard tiers. "early"
# is defense-in-depth: LLM_topic_decider withholds median from grades 1-3.
COMPLEXITY_BY_GRADE = {
    "early": {
        "easy":   "Use an ODD number of values (3 total), whole numbers below 20, so the median is simply the middle value once sorted.",
        "medium": "Use an ODD number of values (3-5 total), whole numbers below 30.",
        "hard":   "Use an ODD number of values (5 total), whole numbers below 50.",
    },
    "middle": {
        "easy":   "Use an ODD number of values (3-5 total), so the median is simply the middle value with no averaging needed. Whole numbers between 1 and 200, no negatives.",
        "medium": "Use an ODD number of values (5-7 total). Whole numbers between 1 and 200, no negatives.",
        "hard":   "Use an EVEN number of values (6-8 total), so finding the median requires averaging the two middle values. Whole numbers between 1 and 200, no negatives.",
    },
    "upper": {
        "easy":   "Use an ODD number of values (3-5 total). Whole numbers between 1 and 500; negative numbers may be used.",
        "medium": "Use an ODD number of values (5-7 total). Whole numbers between 1 and 500; negative numbers may be used.",
        "hard":   "Use an EVEN number of values (6-8 total), so finding the median requires averaging the two middle values. Whole numbers between 1 and 500; negative numbers may be used.",
    },
    # Grades 9+, capped at grade-8 content: a solver limit, not a prompt one.
    "advanced": {
        "easy":   "Use an ODD number of values (5-7 total) including at least two NEGATIVE numbers.",
        "medium": "Use an ODD number of values (7-9 total) including negatives and at least one value above 100.",
        "hard":   "Use an EVEN number of values (8-10 total) including negatives, so the median is the average of the two middle values and may be a decimal.",
    },
}

def generate_median_question(global_questions, prev_questions,difficulty,grade, max_retries=3):
    for attempt in range(max_retries):
        if attempt > 0:
            prompt = median_prompt + "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."
        else:
            prompt = median_prompt

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
        prompt = lesson_plan_context.append_lesson_context(prompt, "median", grade_band)
        response_text = llm_client.generate_text(
            prompt, schema=question_schemas.dataset("median"))

        raw = extract_json(response_text)

        if not raw:
            print(f"[Attempt {attempt+1}] No JSON found")
            print(response_text)
            continue

        # After the None guard, or `.replace` raises instead of retrying.
        raw = raw.replace("\n", " ")

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
                                        "median", grade_band, difficulty,
                                        attempt + 1):
            continue

        # The student sees question_text but is scored on `variables`.
        inconsistent = question_consistency.dataset_mismatch(
            question_data.get("question_text"), question_data.get("variables"))
        if inconsistent:
            print(f"[Attempt {attempt+1}] Inconsistent question: {inconsistent}")
            continue
    
        # The bounded worker refuses anything not a finite number; nothing after parses.
        numbers = safe_solve.safe_sympify_values(question_data['variables'])
        if numbers is None:
            print(f"[Attempt {attempt+1}] Unusable variables:",
                  repr(question_data['variables'])[:80])
            continue
        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    solution = median(numbers)

    solution_float = float(solution)
    incorrect_answers = generate_incorrect_answers(solution_float, numbers)
    solution = format_number(solution)
    answers = [format_number(ans) for ans in incorrect_answers] + [solution]

    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "median",
        "ccss_standard": ccss_standards.ccss_for("median", grade),
        "answer_options": answers,
        "correct_answer": solution
    }

