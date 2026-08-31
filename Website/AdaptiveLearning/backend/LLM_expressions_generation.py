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

# Only the selected scenario's block is sent -- see the note in
# LLM_geometry_generation.py. _pick_scenario has already applied the
# grade-band restriction (scenarios 2 and 3 are withheld from "early"), so
# the others were worked examples for questions the model must not write.
#
# This narrows the early-band contradiction CLAUDE.md documents but does NOT
# resolve it: scenario 1's own example is `36/3+(8*2)-(15-7)+4`, which is
# exactly the parenthesis-heavy older-student shape that beat the textual
# rule. EARLY_BAND_EXAMPLE below is still what actually fixes that and must
# stay -- three contradicting examples became one, not none.
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


def _expr_prompt(scenario):
    """Header + the one selected scenario's block + footer. KeyError on an
    unknown scenario, for the reason _geometry_prompt documents."""
    return EXPR_HEADER + "\n" + SCENARIO_BLOCKS[scenario] + EXPR_FOOTER


def _grade_band(grade):
    # Shared with the other generation files so they can't drift apart.
    # An unreadable grade like "Grade 1" falls back to "early", not "advanced".
    return grade_levels.grade_band(grade)

# Scenario 3 ("simplify", e.g. "2x + 3x") uses algebraic notation, so it's
# withheld until "upper" (grades 7-8) regardless of difficulty -- pre-algebra
# notation isn't in reach before then. Grade 6 ("middle" band) also misses out
# on it, even though the topic-selection rule elsewhere treats grade 6 as
# pre-algebra-ready; that's a deliberate simplification rather than adding a
# fifth grade bucket for one scenario.
# Scenario 2 ("order_of_operations") is withheld from "early" too. It's a
# grade-5 concept (CCSS 5.OA.1) defined by mixing precedence levels, which
# can't be expressed within the early band's addition-and-subtraction-only
# rule -- see EARLY_BAND_EXAMPLE for what happened when it wasn't withheld.
def _pick_scenario(grade_band):
    if grade_band == "early":
        return 1
    if grade_band == "middle":
        return random.randint(1, 2)
    return random.randint(1, 3)


# The scenario examples in SCENARIO_BLOCKS above are all written for older
# students (e.g. "36/3+(8*2)-(15-7)+4"), and a few-shot example beats a text
# rule. Measured on llama3.1:8b with the lesson plans seeded (2026-08-18,
# grade 1 / easy): 2 of 8 questions came back with parentheses despite
# COMPLEXITY_BY_GRADE forbidding them. So the early band gets its own worked
# example below, and `grade_appropriateness` catches whatever still slips through.
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

# Which operations are available changes by grade, not just how many of them
# or how big the numbers are -- multiplication, division, and parentheses
# should only appear once a grade has actually been taught them.
# Two grades inside the "middle" band have not met a concept the band's
# tiers use. The band spans 4-6 and its tiers are written for its ceiling,
# so a 4th grader was offered parentheses on 6 of 10 measured questions --
# order of operations is 5.OA.1.
#
# Appended to the prompt rather than folded into COMPLEXITY_BY_GRADE,
# because the table is keyed by band and this is keyed by grade; giving
# the table a thirteenth column to express one rule would make every
# other topic's table wrong by omission. Prompt-level, so it can leak --
# `grade_appropriateness` is where a code-level check would go if it does.
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
    # "advanced" is grades 9+. It used to be `upper` with the magnitude
    # clause deleted -- which reads to the model as no requirement rather
    # than a harder one, and an audit of 640 questions measured the result:
    # 83% of grade-9 questions were three or more grades below grade.
    #
    # The ceiling here is grade 8, not high school, and that is a solver
    # limit rather than a prompt one -- see the note above
    # COMPLEXITY_BY_GRADE in this file's module docstring region.
    "advanced": {
        "easy":   "Use 3-4 operations including at least TWO negative integers (e.g. -15 + 6 - (-8)). No parentheses.",
        "medium": "Use 4-5 operations with one set of parentheses and at least one integer exponent such as 2**3. For a simplify question instead, use at least three like terms with one negative coefficient.",
        "hard":   "Use 5-6 operations with TWO levels of nested parentheses and negative integers (e.g. ((8-3)*2 - 7)*2 + 18/3). For a simplify question instead, use four or more like terms including negative coefficients. Do NOT raise a variable to a power.",
    },
}

solution = -1


def generate_expression_question(global_questions, prev_questions, difficulty, grade, max_retries=3):
    for attempt in range(max_retries):
        # randomize scenario selection (within what this grade band may see)
        # to ensure variety; the prompt is built around the result
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
        override = GRADE_OVERRIDES.get(grade_levels.grade_number(grade))
        if override:
            prompt += "\nGRADE-SPECIFIC RULE: " + override + "\n"
        prompt = lesson_plan_context.append_lesson_context(prompt, "expressions", grade_band)
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

        # Solved inside the loop, so an expression that cannot be solved is a
        # retry rather than a failed question -- the same move CLAUDE.md
        # records for `angle_relationships`, and for the same reason: whether
        # the answer is usable is a property of the solved value, not of the
        # text, so it cannot be checked before solving.
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

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    scenario = question_data["scenario"]

    # `solved` was produced inside the loop above, in a subprocess with a hard
    # time bound, because the operand comes from the model and `parse_expr`
    # evaluates eagerly: `9**9**9` never returns -- a number with ~370 million
    # digits -- and no in-process timeout can stop it, since a thread cannot be
    # killed and a signal is not delivered while the interpreter is inside a
    # long integer computation. Measured here: generation span at 100% CPU for
    # 28 minutes before being killed by hand. On the inline path -- every
    # question, with QUESTION_QUEUE_SIZE at 0 -- that is a request holding one
    # of anyio's ~40 threadpool slots until the process restarts.
    # `GENERATION_LLM_TIMEOUT` bounded the model call; nothing bounded what was
    # done with the answer.
    #
    # Re-parsing here is safe where the original was not: the worker's result
    # is length-capped, so this is a short string rather than whatever the
    # model asked for.
    solution = sp.sympify(solved)

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

