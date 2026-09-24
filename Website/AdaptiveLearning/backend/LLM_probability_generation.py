# Generates a probability question via LLM and solves it with sympy.

import os
import re
import random
from supabase import create_client, Client
from dotenv import load_dotenv
import llm_client
import question_schemas
import json
from flask import Flask, jsonify
from flask_cors import CORS
import sympy as sp
from sympy import symbols, Eq, solve, sympify, Integer, Rational
import incorrect_solution_generation as inc_gen
import lesson_plan_context
import safe_solve
import grade_levels
import ccss_standards
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


def _whole(value):
    """`value` as an int if the parsed float is a whole number, else None."""
    return int(value) if isinstance(value, (int, float)) and float(value).is_integer() else None


def _scored_data(question_data):
    """(items, target) the solvers can score, or the reason the reply cannot be.

    Dice: `items` is the side count, `target` distinct faces in 1..sides. Bags: whole counts,
    and a target naming items that exist (case and spacing ignored), so no target scores 0.
    """
    target = question_data["target"]
    targets = target if isinstance(target, list) else [target]
    if not targets:
        return "no target"

    # Parsed in the bounded worker, since `sympify` on model text can hang.
    if question_data["scenario"] == "dice":
        parsed = safe_solve.safe_sympify_values([question_data["sides"], *targets])
        if parsed is None:
            return f"unusable dice values: {question_data['sides']!r}, {target!r}"
        sides, faces = _whole(parsed[0]), [_whole(f) for f in parsed[1:]]
        if sides is None or sides < 2:
            return f"a die needs a whole number of sides, not {question_data['sides']!r}"
        if any(f is None or not 1 <= f <= sides for f in faces) or len(set(faces)) != len(faces):
            return f"target faces {target!r} are not distinct faces of a {sides}-sided die"
        return sides, faces

    raw_items = question_data["items"]
    if not isinstance(raw_items, dict) or not raw_items:
        return "items is not a non-empty object"
    parsed = safe_solve.safe_sympify_values(list(raw_items.values()))
    counts = [_whole(c) for c in parsed] if parsed is not None else None
    if counts is None or any(c is None or c < 0 for c in counts) or sum(counts) == 0:
        return f"unusable item counts: {raw_items!r}"
    items = dict(zip(raw_items.keys(), counts))

    by_name = {str(k).strip().lower(): k for k in items}
    resolved = [by_name.get(str(t).strip().lower()) for t in targets]
    if None in resolved or len(set(resolved)) != len(resolved):
        return f"target {target!r} does not name distinct items of {list(items)!r}"
    return items, resolved if isinstance(target, list) else resolved[0]

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

# Scenario block numbers: 1 counting (easy), 3 dice condition (medium), 2 complement (hard).
DIFFICULTY_SCENARIOS = {
    "easy":   [1],
    "medium": [3],
    "hard":   [2],
}

# Prompt block number -> scenario name the reply must carry; the solver dispatches on the name.
_SCENARIO_NAMES = {
    1: "probability_of",
    2: "not_probability_of",
    3: "dice",
}


def _pick_scenario(difficulty):
    return random.choice(DIFFICULTY_SCENARIOS.get(difficulty, DIFFICULTY_SCENARIOS["medium"]))

# Difficulty picks the scenario; grade sizes the sample space.
def _grade_band(grade):
    # Shared so copies can't drift; profiles.grade_level is free text. See grade_levels.
    return grade_levels.grade_band(grade)

# Topic is gated to grade 6+, so "early"/"middle" are defence in depth only.
GRADE_COMPLEXITY = {
    "early":    "Keep the total number of items (or dice sides) small, no more than 10 total.",
    "middle":   "Total items may be up to 20.",
    "upper":    "Total items may be up to 50.",
    # Grades 9+: an empty restriction is not a harder one.
    "advanced": "Use total item counts between 20 and 60, chosen so the resulting probability does NOT reduce to a simple fraction like 1/2 or 1/3 -- the student should have to reduce it themselves.",
}


# The scenario is picked here because the LLM randomises poorly.
def generate_probability_question(global_questions, prev_questions, difficulty, grade, max_retries=3):
    for attempt in range(max_retries):
        if attempt > 0:
            prompt = prob_prompt + "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."
        else:
            prompt = prob_prompt

        scenario = _pick_scenario(difficulty)

        prompt += f"\nYOU must generate a question for scenario {scenario}."

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
        # Schema is None for the bag scenarios; see question_schemas.probability.
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

        # The prompt sends all three blocks, so the reply may answer a different one.
        if question_data["scenario"] != _SCENARIO_NAMES[scenario]:
            print(f"[Attempt {attempt+1}] Wrong scenario:",
                  question_data["scenario"])
            continue

        # Checked inside the loop: read after the for/else, a missing key is a 500, not a retry.
        # The schema covers only dice, and only on Claude.
        needed = "sides" if question_data["scenario"] == "dice" else "items"
        if needed not in question_data:
            print(f"[Attempt {attempt+1}] Missing {needed!r} for scenario",
                  question_data["scenario"])
            continue

        # Backstop on what the model produced, not what the prompt asked for.
        if grade_appropriateness.refuse(question_data.get("question_text"),
                                        "probability", grade_band, difficulty,
                                        attempt + 1):
            continue

        # `not_probability_of` scores 1 - p, so a positively-worded text would get the complement.
        inconsistent = question_consistency.negation_mismatch(
            question_data.get("question_text"), question_data.get("scenario"))
        if inconsistent:
            print(f"[Attempt {attempt+1}] Inconsistent question: {inconsistent}")
            continue

        scored = _scored_data(question_data)
        if isinstance(scored, str):
            print(f"[Attempt {attempt+1}] Unusable scored data: {scored}")
            continue
        items, target = scored

        if question_data["scenario"] != "dice":
            inconsistent = question_consistency.counts_mismatch(
                question_data.get("question_text"), items)
            if inconsistent:
                print(f"[Attempt {attempt+1}] Inconsistent question: {inconsistent}")
                continue

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    scenario = question_data["scenario"]

    solution = solve_probability(scenario, items, target)

    incorrect_answers = inc_gen.generate_incorrect_rational(solution) if solution is not None else []
    solution = serialize_sympy(solution) if solution is not None else None
    answers = [serialize_sympy(ans) for ans in incorrect_answers] + [solution]

    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "probability",
        "ccss_standard": ccss_standards.ccss_for("probability", grade, scenario),
        "answer_options": answers,
        "correct_answer": solution
    }

