# Generates a "mode" question via LLM and computes the mode(s) with sympy/Counter.

from collections import Counter
import os
import re
import itertools
import random
from supabase import create_client, Client #pip install supabase
from dotenv import load_dotenv   #pip install dotenv
import llm_client
import json
from flask import Flask, jsonify
from flask_cors import CORS #pip install flask-cors
import sympy as sp #pip install sympy
from sympy import symbols, Eq, solve, sympify, Integer
import answer_format
import lesson_plan_context
import safe_solve
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
    """Through the shared formatter, like the distractors.

    `str()` here rendered the correct answer as `["4.0"]` beside distractors of
    `["10"]`, `["8"]`, `["2"]` -- the same tell `mean` had, one layer further
    on. Fixing the distractor pool alone left the answer as the odd one out,
    which is why the test asserts every option is in canonical form rather
    than checking any single path.
    """
    if isinstance(ans, list):
        return [answer_format.format_value(x) for x in ans]
    if isinstance(ans, sp.Basic):
        return answer_format.format_value(float(ans))
    return answer_format.format_value(ans)

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
    """`values` are already numbers -- parsed in the bounded worker.

    They used to be sympified here, which is the one unbounded step on this
    path: `sympify("9**9**9")` never returns and holds the GIL while it does
    not. Counting floats cannot hang.
    """
    vals = list(values)
    count = Counter(vals)
    max_count = max(count.values())

    return [key for key, value in count.items() if value == max_count]

def generate_incorrect_answers(solution, values):
    generated_answers = []

    # Both branches below drew randomly until they had three distinct answers,
    # with no bound. Whether three exist is a property of the dataset: a set
    # with only two non-modal values cannot supply them, and the nested loop in
    # the multi-mode branch could not even finish one answer when the dataset
    # held fewer distinct values than the mode has members. Enumerating instead
    # of sampling is what makes both terminate -- the candidate pool is small
    # and finite, so there is nothing to search for.
    # Formatted, not `str()`. The values are floats since the parse moved
    # into the worker, so `str()` rendered a dataset written "5, 5, 5, 7"
    # as options of "5.0" and "7.0" -- no tell, but no option matching
    # how the question reads.
    distinct = list(dict.fromkeys(answer_format.format_value(v) for v in values))

    # CASE 1: SINGLE MODE
    if not isinstance(solution, list):
        # Formatted through the same function as `distinct`, or the comparison
        # below never matches and the correct answer is offered as a
        # distractor. `str(5.0)` is "5.0" and the pool holds "5".
        solution = answer_format.format_value(solution)
        others = [v for v in distinct if v != solution]
        random.shuffle(others)
        generated_answers = others[:3]

    # CASE 2: MULTIPLE MODES
    else:
        # Same reason as CASE 1: `str` here, formatted values there.
        solution_set = {answer_format.format_value(v) for v in solution}
        size = len(solution)
        pool = list(distinct)
        random.shuffle(pool)
        # Every same-size subset of the dataset that is not the answer, up to
        # the three needed. `combinations` is finite by construction.
        for combo in itertools.combinations(pool, size):
            if set(combo) != solution_set:
                generated_answers.append(list(combo))
            if len(generated_answers) == 3:
                break

    if len(generated_answers) < 3:
        # A dataset too small to supply three wrong answers of the right shape.
        # Offsetting the mode's own values always can, and keeps the option the
        # same shape as the answer.
        offset = 1
        base = solution if isinstance(solution, list) else [solution]
        while len(generated_answers) < 3 and offset <= 8:
            values_ = [answer_format.format_value(float(v) + offset) for v in base]
            # Compared in the shape the list actually holds. It was built as a
            # list and compared against a list of strings in the single-mode
            # branch, so the check never matched and the filler produced
            # `['7.0', '6.0', '7.0']` -- the same Rational-against-strings
            # mistake `generate_incorrect_rational` had.
            candidate = values_ if isinstance(solution, list) else values_[0]
            if candidate not in generated_answers:
                generated_answers.append(candidate)
            offset += 1

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

# The solver can return more than one tied value (a bimodal dataset), so
# bimodal datasets are kept to "hard" difficulty at every grade -- spotting
# a tie takes an extra step over a single clear mode. "mode" isn't offered
# to grades 1-3 at all, so the "early" tier here is just a backup.
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
    # "advanced" is grades 9+. It used to be `upper` with the magnitude
    # clause deleted -- which reads to the model as no requirement rather
    # than a harder one, and an audit of 640 questions measured the result:
    # 83% of grade-9 questions were three or more grades below grade.
    #
    # The ceiling here is grade 8, not high school, and that is a solver
    # limit rather than a prompt one -- see the note above
    # COMPLEXITY_BY_GRADE in this file's module docstring region.
    "advanced": {
        "easy":   "Use 6-7 values with a SINGLE clear mode, including at least one NEGATIVE number.",
        "medium": "Use 9-10 values with a SINGLE mode appearing only ONE more time than the next most frequent value, including negatives.",
        "hard":   "Use 10-12 values which MAY be bimodal, including negatives and at least two values above 100.",
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

        # The student reads question_text but is scored against variables --
        # check the numbers in the text match the scored dataset.
        inconsistent = question_consistency.dataset_mismatch(
            question_data.get("question_text"), question_data.get("variables"))
        if inconsistent:
            print(f"[Attempt {attempt+1}] Inconsistent question: {inconsistent}")
            continue

        # Parsed in the bounded worker, inside the loop: `sympify` on the
        # model's values is the one unbounded step here, and counting floats
        # cannot hang.
        numbers = safe_solve.safe_sympify_values(question_data["variables"])
        if numbers is None:
            print(f"[Attempt {attempt+1}] Unusable variables:",
                  repr(question_data["variables"])[:80])
            continue

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    solution = mode(numbers)

    incorrect_answers = generate_incorrect_answers(solution, numbers)
    solution = serialize_answer(solution)
    answers = [serialize_answer(ans) for ans in incorrect_answers] + [solution]

    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "mode",
        "answer_options": answers,
        "correct_answer": solution
    }

