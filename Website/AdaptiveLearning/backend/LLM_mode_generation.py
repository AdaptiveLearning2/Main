# 1. Get question from LLM. Response should include question text, topic, variables, operations.
# 2. Solve question using Python (potentially Wolfram Alpha API) to obtain correct answer.
# 3. Generate 4 unique answer options, including correct answer, using LLM.
# 4. Send question and answer options to frontend to display to user.

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

#POSSIBLY: manually generate solution using numbers from question_text.
#This way I can count the # of modes present and ensure solution matches
#that amount w/ randomly selected numbers. 
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
    #always return list
    if isinstance(ans, list):
        return [str(x) for x in ans]
    return [str(ans)]

# The solver (mode()/generate_incorrect_answers() above) already supports
# multi-modal datasets (returns a list of tied values), but the prompt used
# to unconditionally forbid them ("SINGULAR most common value"), so that
# path never actually got exercised. Now bimodal datasets are allowed at
# hard difficulty specifically, alongside dataset size scaling.
DIFFICULTY_COMPLEXITY = {
    "easy":   "Use 5-6 values with a SINGLE clear mode -- the most frequent value should appear at least 2 more times than any other value.",
    "medium": "Use 7-9 values with a SINGLE mode, but the most frequent value should appear only ONE more time than the next most frequent value, requiring careful counting.",
    "hard":   "Use 8-10 values. The dataset MAY be bimodal (two values tied for most frequent) in addition to single-mode datasets.",
}

# Difficulty governs count/single-vs-multi-modal above; grade controls value
# magnitude and whether negatives appear.
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
    "early":    "Use whole numbers between 1 and 20, no negatives.",
    "middle":   "Use whole numbers between 1 and 100, no negatives.",
    "upper":    "Use whole numbers between 1 and 200; negative numbers may be used.",
    "advanced": "No additional restriction.",
}

#Potential improvements:
#Maybe can store previously generated question, feed into LLM to ensure next question is not the same.
#If solution is a fraction, at least one other generated response should be a fraction.
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
        prompt += (
            f"\nCOMPLEXITY FOR THIS DIFFICULTY: "
            f"{DIFFICULTY_COMPLEXITY.get(difficulty, DIFFICULTY_COMPLEXITY['medium'])}\n"
        )
        prompt += (
            f"\nMAGNITUDE FOR THIS GRADE LEVEL: "
            f"{GRADE_COMPLEXITY[_grade_band(grade)]}\n"
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
        required_keys = ["variables", "question_text"]
        if not all(k in question_data for k in required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", question_data)
            continue

        # If we reach here â†’ SUCCESS
        break

    else:
        # All retries failed
        raise ValueError("Failed to generate valid JSON after retries")
    
    parts = question_data['variables']
    solution = mode(parts)

    #solution = normalize_answer(solution_list)

    #print("Solution:", solution)
    # solution = str(solution) if solution else None

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
    #     - Unique, whole numbers numbers only. These numbers MUST come from the numeric values specified in the question that are not the solution.
    #     - NUMBERS must be represented as strings. For example, "0.5" or "1/2" are valid representations.
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
    # answers = incorrect_data["incorrect_answers"] + [str(solution)]
    
    incorrect_answers = generate_incorrect_answers(solution, parts)
    solution = serialize_answer(solution)
    answers = [serialize_answer(ans) for ans in incorrect_answers] + [solution]
    
    random.shuffle(answers)

    #Build final JSON
    return {
        "question_text": question_data["question_text"],
        "question_topic": "mode",
        "answer_options": answers,
        "correct_answer": solution
    }


#display on flask
# app= Flask(__name__)
# CORS(app)
# @app.route("/")
# def display_question():
#     return jsonify(generate_algebra_question())

