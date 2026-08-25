# Generates a "median" question via LLM and computes the median with sympy.

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
import incorrect_solution_generation as inc_gen
import lesson_plan_context
import grade_levels
import grade_appropriateness
import question_consistency


def format_number(x):
    if isinstance(x, list):
        x = x[0]
    val = float(x.evalf()) if hasattr(x, "evalf") else float(sympify(x))
    if abs(val - int(val)) < 1e-9:
        return str(int(val))
    return f"{val:.2f}"

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
def is_number(val):
        try:
            sympify(val)
            return True
        except:
            return False
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
    vals = sorted([sympify(v) for v in values])
    n = len(vals)

    if n%2 == 1:
        return vals[n//2]
    else:
        return (vals[n//2 -1] + vals[n//2]) / 2

# Odd-length datasets: pick a distinct existing value as a distractor.
# Even-length: fall back to the general incorrect-answer generator.
def generate_incorrect_answers(solution, values):
    incorrect_answers = []
    
    vals = sorted([sympify(v) for v in values])
    n = len(vals)

    if n % 2 == 1:
        while len(incorrect_answers) < 3:
            index = random.randint(0, n-1) 
            if vals[index] != solution and vals[index] not in incorrect_answers:
                incorrect_answers.append(vals[index])
            else:
                continue
    else:
       incorrect_answers = inc_gen.generate_general_incorrect_answers(float(solution)) if solution is not None else []

    return incorrect_answers


def _grade_band(grade):
    # Delegated so ten copies of this cannot drift apart, and so an
    # unreadable grade ("Grade 1") lands in "early" rather than
    # "advanced" -- profiles.grade_level is free text. See grade_levels.
    return grade_levels.grade_band(grade)

# Odd-length datasets need no averaging (just pick the middle value);
# even-length ones require averaging the two middle values -- one genuine
# extra step, reserved for grades that have division. "median" isn't in
# LLM_topic_decider's grade-1-3 allowlist, so "early" here is
# defense-in-depth only.
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
    "advanced": {
        "easy":   "Use an ODD number of values (3-5 total).",
        "medium": "Use an ODD number of values (5-7 total).",
        "hard":   "Use an EVEN number of values (6-8 total), so finding the median requires averaging the two middle values.",
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
        response_text = llm_client.generate_text(prompt)

        raw = extract_json(response_text)

        if not raw:
            print(f"[Attempt {attempt+1}] No JSON found")
            print(response_text)
            continue

        # After the guard, not before it. `extract_json` returns None when the
        # response holds no JSON at all -- which is the case this retry loop
        # exists for -- and `.replace` on that raises AttributeError straight
        # out of the loop instead of retrying. The nine sibling generators all
        # check first; this one had the two lines the other way round.
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

        # Backstop on what the model actually produced, not just on what
        # the prompt asked for -- see grade_appropriateness.
        if grade_appropriateness.refuse(question_data.get("question_text"),
                                        "median", grade_band, difficulty,
                                        attempt + 1):
            continue

        # The student is shown question_text but scored on the field
        # above; nothing used to check they agree. See
        # question_consistency for the measured failure.
        inconsistent = question_consistency.dataset_mismatch(
            question_data.get("question_text"), question_data.get("variables"))
        if inconsistent:
            print(f"[Attempt {attempt+1}] Inconsistent question: {inconsistent}")
            continue
    
        parts = question_data['variables']

        if not all(is_number(v) for v in parts):
            print(f"[Attempt {attempt+1}] Non-numeric values detected:", parts)
            continue
        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    solution = median(parts)

    solution_float = float(solution.evalf()) if hasattr(solution, "evalf") else float(solution)
    incorrect_answers = generate_incorrect_answers(solution_float, parts)
    solution = format_number(solution)
    answers = [format_number(ans) for ans in incorrect_answers] + [solution]

    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "median",
        "answer_options": answers,
        "correct_answer": solution
    }

