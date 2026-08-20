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
from sympy.parsing.sympy_parser import (
    parse_expr,
    standard_transformations,
    implicit_multiplication_application
) # treat 2x as 2*x for sympy parsing
import incorrect_solution_generation as inc_gen
import lesson_plan_context
import grade_levels
import grade_appropriateness

transformations = (standard_transformations + (implicit_multiplication_application,))

def is_numeric(expr):   
    return len(expr.free_symbols) == 0

def normalize_answer(val):
    if isinstance(val, (sp.Integer, int)):
        return int(val)
    if isinstance(val, (sp.Float, float)):
        return float(val)
    if isinstance(val, sp.Rational):
        return float(val)  
    return str(val)

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

expr_prompt = f"""
You are to provide a Math question suitable for students. The response must be in JSON format. 
The Question Text, Question Topic, Scenario, and Variables will be displayed. The Question Topic will always be "expressions".

There are three possible scenarios:

Scenario 1: evaluate
"Solve 36/3+(8*2)-(15-7)+4"
The question should include a numerical expression to evaluate using the symbols "+", "-", "*", "/", "(", ")".

JSON for this scenario must follow this exact structure:
{{
  "question_text": "Solve 36/3+(8*2)-(15-7)+4.",
  "question_topic": "expressions",
  "scenario": "evaluate",
  "variables": ["36", "/", "3", "+", "(", "8", "*", "2", ")", "-", "(", "15", "-", "7", ")", "+", "4"]
}}

Scenario 2: order_of_operations
"Evaluate (4+6)*3-5"
The question should emphasize correct use of order of operations (parentheses, multiplication, division, addition, subtraction).

JSON for this scenario must follow this exact structure:
{{
  "question_text": "Evaluate (4+6)*3-5.",
  "question_topic": "expressions",
  "scenario": "order_of_operations",
  "variables": ["(", "4", "+", "6", ")", "*", "3", "-", "5"]
}}

Scenario 3: simplify
"Simplify 2x+3x"
The question should include a simple algebraic expression combining like terms. Use variable "x" only.

JSON for this scenario must follow this exact structure:
{{
  "question_text": "Simplify 2x+3x.",
  "question_topic": "expressions",
  "scenario": "simplify",
  "variables": ["2x", "+", "3x"]
}}

Rules:
- Select ONLY ONE scenario, Generate ONLY ONE question, return ONLY ONE JSON object.
- Use ONLY the symbols "+", "-", "*", "/", "(", ")" in expressions.
- Use ONLY integers (no decimals or fractions).
- The number of operations and parentheses allowed is given below under COMPLEXITY FOR THIS DIFFICULTY -- follow that, not a fixed count.
- For simplify problems, only combine like terms (no equations).
- Ensure the final answer is a whole number when possible.
- Use ONLY double quotes for all strings.
- The JSON object must contain the keys "question_text", "question_topic", "scenario", and "variables".
- "variables" must be a list of strings.
- Do NOT include any characters outside the JSON object.

Return ONLY valid JSON with no text before or after the JSON object.
"""

def _grade_band(grade):
    # Delegated so ten copies of this cannot drift apart, and so an
    # unreadable grade ("Grade 1") lands in "early" rather than
    # "advanced" -- profiles.grade_level is free text. See grade_levels.
    return grade_levels.grade_band(grade)

# Scenario 3 ("simplify", e.g. "2x + 3x") is algebraic notation -- combining
# like terms with a variable -- and used to be pickable via random.randint(1,3)
# for every grade with no gating at all, so a 1st grader could land on it.
# Withheld until "upper" (grades 7-8) regardless of difficulty, since
# pre-algebra notation isn't in reach before then. This means grade 6
# ("middle" band, though the topic-selection rule elsewhere in this codebase
# treats grade 6 as pre-algebra-ready) won't see "simplify" from this topic
# either -- a deliberate simplification rather than adding a fifth grade
# bucket just for this one scenario.
# Scenario 2 ("order_of_operations") is withheld from "early" as well, on top
# of scenario 3. Order of operations is CCSS 5.OA.1 -- a grade-5 concept --
# and the scenario is *defined* by mixing precedence levels, so it cannot be
# expressed within the early band's addition-and-subtraction-only rule. It
# was measurably harmful, not just conceptually wrong: see EARLY_BAND_EXAMPLE.
def _pick_scenario(grade_band):
    if grade_band == "early":
        return 1
    if grade_band == "middle":
        return random.randint(1, 2)
    return random.randint(1, 3)


# Every scenario example in expr_prompt above is written for older students --
# scenario 1's is "36/3+(8*2)-(15-7)+4" -- and a few-shot example beats a
# textual constraint. Measured on llama3.1:8b against the seeded lesson plans
# (2026-08-18, grade 1 / easy): 2 of 8 questions came back with parentheses,
# and a separate run produced "Evaluate (3+2)*4-1.", despite
# COMPLEXITY_BY_GRADE forbidding both in the same prompt. So the early band
# gets a worked example in the shape it is actually allowed, and
# `grade_appropriateness` rejects the ones that still slip through.
EARLY_BAND_EXAMPLE = """
EXAMPLE OF A CORRECT QUESTION FOR THIS GRADE LEVEL -- follow this shape, NOT
the scenario examples above, which are written for much older students:
{
  "question_text": "What is 7 + 8 - 4?",
  "question_topic": "expressions",
  "scenario": "evaluate",
  "variables": ["7", "+", "8", "-", "4"]
}
The question_text must contain ONLY digits, "+", "-", and "?" -- no "*", no
"/", and no parentheses of any kind.
"""

# Grade-band-first: which OPERATIONS are even available changes by grade,
# not just how many of them or how big the numbers are. Multiplication/
# division and parentheses used to be available at every grade the moment
# difficulty ticked up to "medium", regardless of whether that grade has
# been taught multiplication yet.
COMPLEXITY_BY_GRADE = {
    "early": {
        "easy":   "Use 2 operations total, ADDITION AND SUBTRACTION ONLY. Do NOT use multiplication, division, or parentheses. Numbers 1-9.",
        "medium": "Use 2-3 operations total, ADDITION AND SUBTRACTION ONLY. Do NOT use multiplication, division, or parentheses. Numbers 1-20.",
        "hard":   "Use 2-3 operations total. Multiplication facts up to 5x5 may be included alongside addition/subtraction. Do NOT use division or parentheses. Numbers 1-20.",
    },
    "middle": {
        "easy":   "Use 2-3 operations total. Do NOT use any parentheses. Numbers up to two digits (1-50).",
        "medium": "Use 3-4 operations total. You may use up to one set of parentheses. Numbers up to two digits (1-50).",
        "hard":   "Use 5-6 operations total. You may use up to two sets of parentheses. Numbers up to two digits (1-50).",
    },
    "upper": {
        "easy":   "Use 2-3 operations total. Do NOT use any parentheses. Numbers may be up to three digits (1-200).",
        "medium": "Use 3-4 operations total. You may use up to one set of parentheses. Numbers may be up to three digits (1-200).",
        "hard":   "Use 5-6 operations total. You may use up to two sets of parentheses. Numbers may be up to three digits (1-200).",
    },
    "advanced": {
        "easy":   "Use 2-3 operations total. Do NOT use any parentheses.",
        "medium": "Use 3-4 operations total. You may use up to one set of parentheses.",
        "hard":   "Use 5-6 operations total. You may use up to two sets of parentheses.",
    },
}

solution = -1


def generate_expression_question(global_questions, prev_questions, difficulty, grade, max_retries=3):
    for attempt in range(max_retries):
        if attempt > 0:
            prompt = expr_prompt + "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."
        else:
            prompt = expr_prompt

        # randomize scenario selection (within what this grade band may see) to ensure variety
        grade_band = _grade_band(grade)
        scenario = _pick_scenario(grade_band)

        prompt += f"\nYOU must generate a question for scenario {scenario}."
        print(scenario)

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
            f"\nCOMPLEXITY FOR THIS GRADE AND DIFFICULTY: "
            f"{COMPLEXITY_BY_GRADE[grade_band].get(difficulty, COMPLEXITY_BY_GRADE[grade_band]['medium'])}\n"
        )
        if grade_band == "early":
            prompt += EARLY_BAND_EXAMPLE
        prompt = lesson_plan_context.append_lesson_context(prompt, "expressions", grade_band)
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

        required_keys = ["scenario", "variables", "question_text"]
        if not all(k in question_data for k in required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", question_data)
            continue

        # Backstop on what the model actually produced, not just on what
        # the prompt asked for -- see grade_appropriateness.
        if grade_appropriateness.refuse(question_data.get("question_text"),
                                        "expressions", grade_band, difficulty,
                                        attempt + 1):
            continue

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    parts = question_data['variables']
    equation_stra = "".join(parts) # turn individual variables/operations into a single string to be parsed by sympy
    equation_stra = equation_stra.replace("âˆ’", "-")
    expr = parse_expr(equation_stra, transformations=transformations)

    scenario = question_data["scenario"]

    match scenario:
        case "evaluate" | "order_of_operations":
            solution = expr
        case "simplify":
            solution = sp.simplify(expr)

    if scenario == "simplify":
        incorrect_answers = inc_gen.generate_symbolic_incorrect_answers(solution)
    else:
        if is_numeric(solution):
            incorrect_answers = inc_gen.generate_general_incorrect_answers(float(solution))
        else:
            incorrect_answers = []
    
    
    answers = [str(ans) for ans in incorrect_answers] + [str(solution)]
    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "expressions",
        "answer_options": answers,
        "correct_answer": str(normalize_answer(solution))
    }

