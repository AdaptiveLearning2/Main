# Generates a "mode" question via LLM and computes the mode(s) with sympy/Counter.

from collections import Counter
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
import lesson_plan_context
import grade_levels
import grade_appropriateness
import question_consistency

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

def serialize_answer(ans):
    if isinstance(ans, list):
        return [str(x) for x in ans]
    if isinstance(ans, sp.Basic):
        return str(ans)
    return str(ans)

mode_prompt = f"""
You are to provide a Math question suitable for students. The response must be in JSON format. 
The Question Text, Question Topic, and Variables will be displayed. The Question Topic will be "mode".

mode example: "A teacher recorded the number of books students finished during a reading challenge. The numbers of books read by the students were: 3, 5, 2, 5, 4, 6, 5, 3. What is the mode of this dataset?" 

The question should include the list of values to be used when finding the solution. Each numeric value should be listed in the variables array.

Use a variety of integer values as long as the mode has a WHOLE number solution. Dataset size, and whether more than one value may tie for
most frequent, are given below under COMPLEXITY FOR THIS DIFFICULTY -- follow that.

Return ONLY valid JSON with no text before or after the JSON object.

The JSON must follow this exact structure:

{{
  "question_text": "A teacher recorded the number of books students finished during a reading challenge. The numbers of books read by the students were: 3, 5, 2, 5, 4, 6, 5, 3. What is the mode of this dataset?",
  "question_topic": "mode",
  "variables": ["3,"5","2","5","4","6","5", "2"]
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

def mode(values):
    vals = [sympify(v) for v in values]
    count = Counter(vals)
    max_count = max(count.values())

    return [key for key, value in count.items() if value == max_count]

def generate_incorrect_answers(solution, values):
    generated_answers = []

    # CASE 1: SINGLE MODE
    if not isinstance(solution, list):
        solution = str(solution)

        while len(generated_answers) < 3:
            incorrect_answer = str(random.choice(values))

            if (
                incorrect_answer != solution and
                incorrect_answer not in generated_answers
            ):
                generated_answers.append(incorrect_answer)

    # CASE 2: MULTIPLE MODES
    else:
        solution_set = set(map(str, solution))

        while len(generated_answers) < 3:
            generated = []
            seen = set()

            while len(generated) < len(solution):
                candidate = str(random.choice(values))

                if candidate not in seen:
                    seen.add(candidate)
                    generated.append(candidate)

            if (
                set(generated) != solution_set and
                generated not in generated_answers
            ):
                generated_answers.append(generated)

    return generated_answers

def normalize_answer(ans):
    if isinstance(ans, list):
        return [str(x) for x in ans]
    return [str(ans)]

def _grade_band(grade):
    # Delegated so ten copies of this cannot drift apart, and so an
    # unreadable grade ("Grade 1") lands in "early" rather than
    # "advanced" -- profiles.grade_level is free text. See grade_levels.
    return grade_levels.grade_band(grade)

# Grade-band-first. The solver (mode()/generate_incorrect_answers() above)
# supports multi-modal datasets (returns a list of tied values); bimodal
# datasets are reserved for "hard" difficulty everywhere, since spotting a
# tie is a genuine extra step over a single clear mode. "mode" isn't in
# LLM_topic_decider's grade-1-3 allowlist, so "early" is defense-in-depth
# only.
COMPLEXITY_BY_GRADE = {
    "early": {
        "easy":   "Use 4-5 values, whole numbers below 20, with a SINGLE clear mode appearing at least 2 more times than any other value.",
        "medium": "Use 5-6 values, whole numbers below 20, with a SINGLE clear mode.",
        "hard":   "Use 6-7 values, whole numbers below 30, with a SINGLE mode that appears only ONE more time than the next most frequent value.",
    },
    "middle": {
        "easy":   "Use 5-6 values with a SINGLE clear mode -- the most frequent value should appear at least 2 more times than any other value. Whole numbers between 1 and 100, no negatives.",
        "medium": "Use 7-9 values with a SINGLE mode, but the most frequent value should appear only ONE more time than the next most frequent value, requiring careful counting. Whole numbers between 1 and 100, no negatives.",
        "hard":   "Use 8-10 values. The dataset MAY be bimodal (two values tied for most frequent) in addition to single-mode datasets. Whole numbers between 1 and 100, no negatives.",
    },
    "upper": {
        "easy":   "Use 5-6 values with a SINGLE clear mode. Whole numbers between 1 and 200; negative numbers may be used.",
        "medium": "Use 7-9 values with a SINGLE mode, requiring careful counting. Whole numbers between 1 and 200; negative numbers may be used.",
        "hard":   "Use 8-10 values. The dataset MAY be bimodal. Whole numbers between 1 and 200; negative numbers may be used.",
    },
    "advanced": {
        "easy":   "Use 5-6 values with a SINGLE clear mode -- the most frequent value should appear at least 2 more times than any other value.",
        "medium": "Use 7-9 values with a SINGLE mode, but the most frequent value should appear only ONE more time than the next most frequent value, requiring careful counting.",
        "hard":   "Use 8-10 values. The dataset MAY be bimodal (two values tied for most frequent) in addition to single-mode datasets.",
    },
}

def generate_mode_question(global_questions, prev_questions,difficulty, grade, max_retries=3):
    for attempt in range(max_retries):
        if attempt > 0:
            prompt = mode_prompt + "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."
        else:
            prompt = mode_prompt

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
        prompt = lesson_plan_context.append_lesson_context(prompt, "mode", grade_band)
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

        # Backstop on what the model actually produced, not just on what
        # the prompt asked for -- see grade_appropriateness.
        if grade_appropriateness.refuse(question_data.get("question_text"),
                                        "mode", grade_band, difficulty,
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

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    parts = question_data['variables']
    solution = mode(parts)

    incorrect_answers = generate_incorrect_answers(solution, parts)
    solution = serialize_answer(solution)
    answers = [serialize_answer(ans) for ans in incorrect_answers] + [solution]

    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "mode",
        "answer_options": answers,
        "correct_answer": solution
    }

