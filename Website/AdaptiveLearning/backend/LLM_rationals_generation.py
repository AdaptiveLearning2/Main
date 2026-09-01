# Generates a rational-number arithmetic question via LLM and solves it with sympy.

import os
import re
import random
from supabase import create_client, Client #pip install supabase
from dotenv import load_dotenv   #pip install dotenv
import llm_client
import question_schemas
import json
from flask import Flask, jsonify
from flask_cors import CORS #pip install flask-cors
import sympy as sp #pip install sympy
from sympy import symbols, Eq, solve, sympify, Integer, Rational
import incorrect_solution_generation as inc_gen
import lesson_plan_context
import safe_solve
import token_join
import grade_levels
import grade_appropriateness


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

def serialize_sympy(obj):
    if isinstance(obj, sp.Rational):
        return str(obj)
    if isinstance(obj, sp.Integer):
        return int(obj)
    if isinstance(obj, sp.Float):
        return float(obj)
    if isinstance(obj, sp.Expr):
        return str(obj)
    return str(obj)

rat_prompt = f"""
You are to provide a Math question suitable for students. The response must be in JSON format. 
The Question Text, Question Topic, and Variables will be displayed. The Question Topic will be "rationals".

Rationals example: "Solve 4/5 - 1/10" 
The question should include the rational expression to be solved. Variables must be formatted as strings such as "x", and operations must be 
represented using the symbols "+", "-", "*", "/". For example, the variables list ["4/5", "-", "1/10"] represents the question 4/5 - 1/10.

The number of operations and denominator complexity are given below under COMPLEXITY FOR THIS DIFFICULTY -- follow that, not a fixed count. Each numerical value should be a fraction displayed in JSON in the format "a/b". Do not attempt to format mixed numbers.
Use a variety of rational values as long as the equation has a valid solution.

Return ONLY valid JSON with no text before or after the JSON object.

The JSON must follow this exact structure:

{{
  "question_text": "Solve 4/5 - 1/10",
  "question_topic": "rationals",
  "variables": ["4/5", "-", "1/10"]
}}

Rules:
- Use ONLY double quotes for all strings.
- The JSON object must contain the keys "question_text", "question_topic", and "variables".
- "variables" must be a list of strings.
- Do NOT include any characters outside the JSON object.
"""

solution = -1

def _grade_band(grade):
    # Delegated so ten copies of this cannot drift apart, and so an
    # unreadable grade ("Grade 1") lands in "early" rather than
    # "advanced" -- profiles.grade_level is free text. See grade_levels.
    return grade_levels.grade_band(grade)

# Grade-band-first. "rationals" isn't in LLM_topic_decider's grade-1-3
# allowlist (fractions are typically a grade-3+ concept, fraction
# *operations* grade-4/5+), so "early" here is defense-in-depth only --
# kept to same-denominator halves/thirds/fourths framed as parts of a whole,
# not the denominator/operation-count scaling middle band onward uses.
# See the note in LLM_expressions_generation.GRADE_OVERRIDES. The same
# band-ceiling effect gave a 4th grader unlike denominators on 7 of 10
# measured questions; 4.NF.3 is like denominators, 5.NF.1 is unlike.
GRADE_OVERRIDES = {
    4: "This student is in GRADE 4. Every fraction must share the SAME denominator -- adding fractions with unlike denominators is a grade-5 standard (5.NF.1).",
}


COMPLEXITY_BY_GRADE = {
    "early": {
        "easy":   "Use a SINGLE addition between two simple fractions sharing the same denominator (halves, thirds, or fourths only, e.g. 1/4 + 2/4).",
        "medium": "Use a SINGLE addition or subtraction between two simple fractions sharing the same denominator (halves, thirds, fourths, or eighths).",
        "hard":   "Use a SINGLE addition or subtraction between two simple fractions sharing the same denominator, denominators up to 10.",
    },
    "middle": {
        "easy":   "Use a SINGLE operation between two fractions that already share the same denominator (e.g. 3/8 + 2/8). Use only positive fractions.",
        "medium": "Use TWO operations between fractions with different denominators. Use only positive fractions.",
        "hard":   "Use up to THREE operations between fractions with different denominators, using larger denominators (e.g. sevenths, ninths, elevenths). Use only positive fractions.",
    },
    "upper": {
        "easy":   "Use a SINGLE operation between two fractions that already share the same denominator. Negative fractions are allowed.",
        "medium": "Use TWO operations between fractions with different denominators. Negative fractions are allowed.",
        "hard":   "Use up to THREE operations between fractions with different denominators, using larger denominators. Negative fractions are allowed.",
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
        "easy":   "Use TWO fractions with DIFFERENT denominators (e.g. 2/3 + 1/4).",
        "medium": "Use THREE fractions with different denominators, at least one of them NEGATIVE (e.g. -3/4 + 5/6 - 1/3).",
        "hard":   "Use THREE or FOUR fractions with different denominators up to 12, including at least TWO negatives and both addition and subtraction (e.g. 7/12 - (-5/8) + 1/3 - 3/4).",
    },
}

def generate_rational_question(global_questions, prev_questions,difficulty, grade, max_retries=3):
    for attempt in range(max_retries):
        if attempt > 0:
            prompt = rat_prompt + "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."
        else:
            prompt = rat_prompt

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
        override = GRADE_OVERRIDES.get(grade_levels.grade_number(grade))
        if override:
            prompt += "\nGRADE-SPECIFIC RULE: " + override + "\n"
        prompt = lesson_plan_context.append_lesson_context(prompt, "rationals", grade_band)
        response_text = llm_client.generate_text(
            prompt, schema=question_schemas.token_list("rationals"))

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
                                        "rationals", grade_band, difficulty,
                                        attempt + 1):
            continue

        # Through the bounded worker, like algebra and expressions. `sympify`
        # on the model's expression is the unbounded step -- `sympify("9**9**9")`
        # never returns -- and this topic joins tokens the model wrote, so the
        # operand is entirely its choice. `evaluate` returns the canonical form,
        # which for this topic is the fraction a student is shown ("5/6").
        #
        # Inside the loop, and this was the last generator where it was not.
        # Below the `for/else` both of these were a 500 on attempt 1: tokens
        # that will not join, and a division by zero, which the worker now
        # reports as unusable rather than answering "zoo". Neither says the
        # next reply will be bad, so both are worth a retry.
        equation_str = token_join.join_tokens(question_data['variables'])
        if equation_str is None:
            print(f"[Attempt {attempt+1}] Unusable variables:",
                  repr(question_data['variables'])[:80])
            continue
        solved = safe_solve.safe_solve(equation_str, "evaluate")
        if solved is None:
            print(f"[Attempt {attempt+1}] Could not solve:",
                  repr(equation_str)[:80])
            continue

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    solution = sympify(solved)

    incorrect_answers = inc_gen.generate_incorrect_rational(solution) if solution is not None else []
    solution = serialize_sympy(solution) if solution is not None else None
    answers = [serialize_sympy(ans) for ans in incorrect_answers] + [solution]

    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        # The topic this generator is, not the label the model chose. It
        # reaches `questions.subject`, which `record_topic_attempt` joins
        # against `math_topics.topic_name` -- so a wrong value here is not a
        # cosmetic label, it credits the student's work to another topic in
        # `user_math_performance`, which is what the adaptive engine reads to
        # decide what to serve next.
        #
        # Measured 3 of 3 against Haiku: this stored "algebra" every time,
        # because the prompt said so in two places. The other nine generators
        # have always hardcoded their own name; this was the only one that
        # did not.
        "question_topic": "rationals",
        "answer_options": answers,
        "correct_answer": solution
    }
