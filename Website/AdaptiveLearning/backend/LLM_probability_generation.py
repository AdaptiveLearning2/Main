# Generates a probability question via LLM and solves it with sympy.

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
import grade_levels
import grade_appropriateness
import question_consistency


# Scenarios: probability_of, not_probability_of, dice.
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

def solve_probability(scenario, items, target):
    if (scenario == "probability_of"):
        solution = solve_probability_of(items, target)
    elif (scenario == "not_probability_of"):
        solution = solve_not_probability_of(items, target)
    else: 
        solution = solve_dice(items, target)
    
    return solution


def solve_probability_of(items, target):
    total = sum(items.values())
    
    if isinstance(target, list):
        favorable = sum(items.get(t, 0) for t in target)
    else:
        favorable = items.get(target, 0)

    return Rational(favorable, total)

def solve_not_probability_of(items, target):
    total = sum(items.values())

    if isinstance(target, list):
        excluded = sum(items.get(t, 0) for t in target)
    else:
        excluded = items.get(target, 0)

    return Rational(total - excluded, total)

def solve_dice(sides, target):
    return Rational(len(target), sides)

prob_prompt = f"""
You are to provide a Math question suitable for students. The response must be in JSON format. 
The Question Text, Question Topic, Scenario, Items, and Target will be displayed. The Question Topic will always be "probability"
There will be three possible scenarios to select from. You must select only ONE scenario to generate a question and corresponding JSON response for.

Scenario 1: probability_of 
"A bag contains 6 red marbles, 4 blue marbles, and 2 green marbles. If one marble is drawn at random, what is the probability of drawing a red marble?"
JSON for this scenario must follow this exact structure: 
{{
  "question_text": "A bag contains 6 red marbles, 4 blue marbles, and 2 green marbles. If one marble is drawn at random, what is the probability of drawing a red marble?",
  "question_topic": "probability",
  "scenario": "probability_of",
  "items": {{
    "red": "6",
    "blue": "4",
    "green": "2"
  }},
  "target": "red"
}}

Scenario 2: not_probability_of 
"A bag contains 5 yellow marbles, 3 purple marbles, and 2 orange marbles. If one marble is drawn at random, what is the probability of NOT drawing a yellow marble?"
JSON for this scenario must follow this exact structure: 
{{
  "question_text": "A bag contains 5 yellow marbles, 3 purple marbles, and 2 orange marbles. If one marble is drawn at random, what is the probability of NOT drawing a yellow marble?",
  "question_topic": "probability",
  "scenario": "not_probability_of",
  "items": {{
    "yellow": "5",
    "purple": "3",
    "orange": "2"
  }},
  "target": "yellow"
}}

Scenario 3: dice 
"A standard six-sided die is rolled. What is the probability of rolling a number greater than 4?"
JSON for this scenario must follow this exact structure: 
{{
  "question_text": "A standard six-sided die is rolled. What is the probability of rolling a number greater than 4?",
  "question_topic": "probability",
  "scenario": "dice",
  "sides": "6",
  "target": ["5", "6"]
}}

Return ONLY valid JSON with no text before or after the JSON object.

The JSON must follow this exact structure:

Rules:
- Use ONLY double quotes for all strings.
- The JSON object must contain the keys "question_text", "question_topic", "scenario", "items" or "sides", and "target".
- "items" must be a list of strings.
- Do NOT include any characters outside the JSON object.
"""

solution = -1

# probability_of is direct counting (EASY). dice requires reasoning about a
# condition over a sample space, e.g. "greater than 4" (MEDIUM). not_probability_of
# additionally requires the complementary-probability concept -- one more
# conceptual step on top of probability_of (HARD).
DIFFICULTY_SCENARIOS = {
    "easy":   [1],
    "medium": [3],
    "hard":   [2],
}

# Block number -> the scenario name that block asks for. This prompt sends all
# three blocks and names the wanted one by number, so this map is the only
# thing tying "scenario 3" to the "dice" the reply must carry -- and the solver
# dispatches on the name.
_SCENARIO_NAMES = {
    1: "probability_of",
    2: "not_probability_of",
    3: "dice",
}


def _pick_scenario(difficulty):
    return random.choice(DIFFICULTY_SCENARIOS.get(difficulty, DIFFICULTY_SCENARIOS["medium"]))

# Difficulty governs which scenario gets picked above; grade controls the
# size of the sample space (total items or dice sides) within whatever
# scenario gets chosen.
def _grade_band(grade):
    # Delegated so ten copies of this cannot drift apart, and so an
    # unreadable grade ("Grade 1") lands in "early" rather than
    # "advanced" -- profiles.grade_level is free text. See grade_levels.
    return grade_levels.grade_band(grade)

# "probability" isn't reachable before grade 6 at all --
# LLM_topic_decider._safe_topic() withholds it from every grade below that
# (see its docstring) -- so "early" and "middle" here are defense-in-depth
# only, not primary content.
GRADE_COMPLEXITY = {
    "early":    "Keep the total number of items (or dice sides) small, no more than 10 total.",
    "middle":   "Total items may be up to 20.",
    "upper":    "Total items may be up to 50.",
    # Grades 9+. See the note in LLM_geometry_generation: an empty
    # restriction is not a harder one.
    "advanced": "Use total item counts between 20 and 60, chosen so the resulting probability does NOT reduce to a simple fraction like 1/2 or 1/3 -- the student should have to reduce it themselves.",
}


# The LLM has poor randomization of scenarios on its own, so the scenario is picked here instead.
def generate_probability_question(global_questions, prev_questions, difficulty, grade, max_retries=3):
    for attempt in range(max_retries):
        if attempt > 0:
            prompt = prob_prompt + "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."
        else:
            prompt = prob_prompt

        scenario = _pick_scenario(difficulty)

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
        grade_band = _grade_band(grade)
        prompt += (
            f"\nMAGNITUDE FOR THIS GRADE LEVEL: "
            f"{GRADE_COMPLEXITY[grade_band]}\n"
        )
        prompt = lesson_plan_context.append_lesson_context(prompt, "probability", grade_band)
        # `None` for the two bag scenarios, whose `items` object cannot be
        # expressed -- see question_schemas.probability.
        response_text = llm_client.generate_text(
            prompt, schema=question_schemas.probability(_SCENARIO_NAMES[scenario]))

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

        required_keys = ["scenario", "question_text", "target"]
        if not all(k in question_data for k in required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", question_data)
            continue

        # Backstop on what the model actually produced, not just on what
        # the prompt asked for -- see grade_appropriateness.
        if grade_appropriateness.refuse(question_data.get("question_text"),
                                        "probability", grade_band, difficulty,
                                        attempt + 1):
            continue

        # `not_probability_of` scores 1 - p, so a positively-worded question
        # tagged with it is answered with the complement. Measured: a text
        # asking for P(EDM) over 69 bands was answered 18/23, i.e. 54/69.
        inconsistent = question_consistency.negation_mismatch(
            question_data.get("question_text"), question_data.get("scenario"))
        if inconsistent:
            print(f"[Attempt {attempt+1}] Inconsistent question: {inconsistent}")
            continue

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    scenario = question_data["scenario"]
    target = question_data["target"]

    # Parsed in the bounded worker. `sympify` on the model's counts is the one
    # unbounded step here -- `sympify("9**9**9")` never returns, and holds the
    # GIL while it does not -- and the probability arithmetic afterwards is
    # ordinary division. Whole numbers throughout, so the floats come back
    # exact; `solve_probability` builds the Rational from them.
    if scenario == "dice":
        parsed = safe_solve.safe_sympify_values([question_data["sides"], *target])
        if parsed is None:
            raise ValueError(f"unusable dice values: {question_data['sides']!r}")
        items = parsed[0]
        target = parsed[1:]
    else:
        raw_items = question_data["items"]
        if not isinstance(raw_items, dict) or not raw_items:
            raise ValueError("Invalid items in question data")
        counts = safe_solve.safe_sympify_values(list(raw_items.values()))
        if counts is None:
            raise ValueError(f"unusable item counts: {raw_items!r}")
        items = dict(zip(raw_items.keys(), counts))

    if not items or not target:
        raise ValueError("Invalid items or target in question data")


    solution = solve_probability(scenario, items, target)

    incorrect_answers = inc_gen.generate_incorrect_rational(solution) if solution is not None else []
    solution = serialize_sympy(solution) if solution is not None else None
    answers = [serialize_sympy(ans) for ans in incorrect_answers] + [solution]

    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "probability",
        "answer_options": answers,
        "correct_answer": solution
    }

