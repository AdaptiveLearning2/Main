import os
import re
import random
from supabase import create_client, Client
from dotenv import load_dotenv
import llm_client
from llm_json import extract_json
import json
from flask import Flask, jsonify
from flask_cors import CORS
import sympy as sp
from sympy import symbols, Eq, solve, sympify, Integer
from sympy.parsing.sympy_parser import (
    parse_expr,
    standard_transformations,
    implicit_multiplication_application
) # treat 2x as 2*x for sympy parsing
import incorrect_solution_generation as inc_gen
import lesson_plan_context
import safe_solve
import token_join
import grade_levels
import ccss_standards
import grade_appropriateness
import question_schemas
import answer_format

transformations = (standard_transformations + (implicit_multiplication_application,))

def is_numeric(expr):   
    return len(expr.free_symbols) == 0

def answer_text(val):
    """The one string a solved value is shown as.

    Used for the option *and* `correct_answer`: the page marks by string equality.
    """
    if is_numeric(val):
        return answer_format.format_value(float(val))
    return str(val)

# Only the selected scenario's block is sent. Scenario 1's example is still an
# older-student shape, so EARLY_BAND_EXAMPLE below must stay.
EXPR_HEADER = """
You are to provide a Math question suitable for students. The response must be in JSON format.
The Question Text, Question Topic, Scenario, and Variables will be displayed. The Question Topic will always be "expressions".

Generate a question for the one scenario given below.
"""

SCENARIO_BLOCKS = {
    1: """Scenario 1: evaluate
"Solve 36/3+(8*2)-(15-7)+4"
The question should include a numerical expression to evaluate using the symbols "+", "-", "*", "/", "(", ")".

JSON for this scenario must follow this exact structure:
{
  "question_text": "Solve 36/3+(8*2)-(15-7)+4.",
  "question_topic": "expressions",
  "scenario": "evaluate",
  "variables": ["36", "/", "3", "+", "(", "8", "*", "2", ")", "-", "(", "15", "-", "7", ")", "+", "4"]
}
""",

    2: """Scenario 2: order_of_operations
"Evaluate (4+6)*3-5"
The question should emphasize correct use of order of operations (parentheses, multiplication, division, addition, subtraction).

JSON for this scenario must follow this exact structure:
{
  "question_text": "Evaluate (4+6)*3-5.",
  "question_topic": "expressions",
  "scenario": "order_of_operations",
  "variables": ["(", "4", "+", "6", ")", "*", "3", "-", "5"]
}
""",

    3: """Scenario 3: simplify
"Simplify 2x+3x"
The question should include a simple algebraic expression combining like terms. Use variable "x" only.

JSON for this scenario must follow this exact structure:
{
  "question_text": "Simplify 2x+3x.",
  "question_topic": "expressions",
  "scenario": "simplify",
  "variables": ["2x", "+", "3x"]
}
""",

}

EXPR_FOOTER = """
Rules:
- Generate ONLY ONE question, return ONLY ONE JSON object.
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


# Block number -> scenario name; cross-checked against the blocks by a test,
# since `safe_solve` dispatches on the name.
_SCENARIO_NAMES = {
    1: "evaluate",
    2: "order_of_operations",
    3: "simplify",
}


def _expr_prompt(scenario):
    """Header + the selected scenario's block + footer. KeyError on an unknown scenario."""
    return EXPR_HEADER + "\n" + SCENARIO_BLOCKS[scenario] + EXPR_FOOTER


def _grade_band(grade):
    # An unreadable grade like "Grade 1" falls back to "early", not "advanced".
    return grade_levels.grade_band(grade)

# simplify (algebraic notation) waits for "upper"; order_of_operations (5.OA.1)
# is withheld from "early", whose tiers are addition and subtraction only.
def _pick_scenario(grade_band):
    if grade_band == "early":
        return 1
    if grade_band == "middle":
        return random.randint(1, 2)
    return random.randint(1, 3)


# The scenario examples are written for older students, and a few-shot example
# beats a text rule, so the early band gets its own.
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

# Per-grade rules inside a band: the "middle" tiers are written for grade 6, and
# grade 4 has not met parentheses (5.OA.1). Prompt-level only, so it can leak.
GRADE_OVERRIDES = {
    4: "This student is in GRADE 4. Do NOT use parentheses of any kind -- order of operations is a grade-5 standard (5.OA.1).",
}


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
    # Grades 9+, capped at grade-8 content: a solver limit, not a prompt one.
    "advanced": {
        "easy":   "Use 3-4 operations including at least TWO negative integers (e.g. -15 + 6 - (-8)). No parentheses.",
        "medium": "Use 4-5 operations with one set of parentheses and at least one integer exponent such as 2**3. For a simplify question instead, use at least three like terms with one negative coefficient.",
        "hard":   "Use 5-6 operations with TWO levels of nested parentheses and negative integers (e.g. ((8-3)*2 - 7)*2 + 18/3). For a simplify question instead, use four or more like terms including negative coefficients. Do NOT raise a variable to a power.",
    },
}

solution = -1


def generate_expression_question(global_questions, prev_questions, difficulty, grade, max_retries=3):
    for attempt in range(max_retries):
        grade_band = _grade_band(grade)
        scenario = _pick_scenario(grade_band)

        prompt = _expr_prompt(scenario)
        if attempt > 0:
            prompt += "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."

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
        override = GRADE_OVERRIDES.get(grade_levels.served_grade_number(grade))
        if override:
            prompt += "\nGRADE-SPECIFIC RULE: " + override + "\n"
        prompt = lesson_plan_context.append_lesson_context(prompt, "expressions", grade_band)
        response_text = llm_client.generate_text(
            prompt, schema=question_schemas.expressions(_SCENARIO_NAMES[scenario]))
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

        required_keys = ["scenario", "variables", "question_text"]
        if not all(k in question_data for k in required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", question_data)
            continue

        # Check the scenario returned: an off-name reply solves as evaluate and
        # gets the grade-1 CCSS code. Only Ollama can produce one.
        if question_data["scenario"] != _SCENARIO_NAMES[scenario]:
            print(f"[Attempt {attempt+1}] Wrong scenario:",
                  question_data["scenario"])
            continue

        # Backstop on what the model actually produced; see grade_appropriateness.
        if grade_appropriateness.refuse(question_data.get("question_text"),
                                        "expressions", grade_band, difficulty,
                                        attempt + 1):
            continue

        # Solved inside the loop, so an unsolvable expression is a retry.
        equation_stra = token_join.join_tokens(question_data["variables"])
        if equation_stra is None:
            print(f"[Attempt {attempt+1}] Unusable variables:",
                  repr(question_data["variables"])[:80])
            continue
        solved = safe_solve.safe_solve(equation_stra, question_data["scenario"])
        if solved is None:
            print(f"[Attempt {attempt+1}] Unsolvable or unbounded expression:",
                  equation_stra[:80])
            continue

        # Safe to re-parse: `solved` came from the bounded worker and is length-capped.
        solution = sp.sympify(solved)
        # Only simplify answers in x; a variable left in an evaluation had no distractors.
        if question_data["scenario"] != "simplify" and not is_numeric(solution):
            print(f"[Attempt {attempt+1}] Evaluation left a variable:", str(solution)[:80])
            continue

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    scenario = question_data["scenario"]

    if scenario == "simplify":
        incorrect_answers = inc_gen.generate_symbolic_incorrect_answers(solution)
    else:
        incorrect_answers = inc_gen.generate_general_incorrect_answers(float(solution))

    
    correct = answer_text(solution)
    answers = [str(ans) for ans in incorrect_answers] + [correct]
    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "expressions",
        "ccss_standard": ccss_standards.ccss_for("expressions", grade, scenario),
        "answer_options": answers,
        "correct_answer": correct
    }

