# 1. Get question from LLM. Response should include question text, topic, variables, operations.
# 2. Solve question using Python (potentially Wolfram Alpha API) to obtain correct answer.
# 3. Generate 4 unique answer options, including correct answer, using LLM.
# 4. Send question and answer options to frontend to display to user.

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
from sympy import symbols, Eq, solve, sympify, Integer, Rational
import incorrect_solution_generation as inc_gen


#current probability scenarions: probability_of, not_probability_of, dice, 
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
        return str(x)          # "3/4"
    if isinstance(x, sp.Integer):
        return int(x)          # 5
    if isinstance(x, sp.Float):
        return float(x)
    if isinstance(x, sp.Expr):
        return str(x)          # "5*x", "3+4"
    return str(x)

def solve_probability(scenario, items, target):
    if (scenario == "probability_of"):
        solution = solve_probability_of(items, target)
    elif (scenario == "not_probability_of"):
        solution = solve_not_probability_of(items, target)
    else: 
        solution = solve_dice(items, target)
    
    return solution


#need to check if these are right
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


#Potential improvements:
#Maybe can store previously generated question, feed into LLM to ensure next question is not the same.
#If solution is a fraction, at least one other generated response should be a fraction. 

#LLM seems to have poor randomization of scenarios, for now selecting randomized scenario for it.
def generate_probability_question(global_questions, prev_questions, difficulty, grade, max_retries=3):


    for attempt in range(max_retries):
        if attempt > 0:
            prompt = prob_prompt + "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."
        else:
            prompt = prob_prompt

        #randomize scenario selection to ensure variety in generated questions.
        scenario = random.randint(1,3)

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
        response = generate(
            model="llama3.1:8b",
            prompt=prompt,
            options={
                "temperature": 1.1, #more creativity
                "top_p": 0.95, #more diversity
                "top_k": 100 #broader token sampling.
            }
        )

        #print("RAW RESPONSE", response.response)
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
        required_keys = ["scenario", "question_text", "target"]
        if not all(k in question_data for k in required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", question_data)
            continue

        # If we reach here → SUCCESS
        break

    else:
        # All retries failed
        raise ValueError("Failed to generate valid JSON after retries")
    
    scenario = question_data["scenario"]
    target = question_data["target"]

    if scenario == "dice":
        sides = sympify(question_data["sides"])
        target = [sympify(t) for t in target]
        items = sides  

    else:
        items = {k: sympify(v) for k, v in question_data["items"].items()}
    
    if not items or not target:
        raise ValueError("Invalid items or target in question data")


    solution = solve_probability(scenario, items, target)

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
    #     - Unique numbers only. NUMBERS must be represented as strings. For example, "0.5" or "1/2" are valid representations.
    #     - Only numbers or simple numeric strings are allowed. Do NOT use brackets, fractions, or expressions.
    #     - Fractions or values with up to two decimal places are allowed as long as they are unique and not equivalent to other values.
    #     - Return JSON format: each array value of incorrect_answers should be a separate incorrect answer
    #     {{
    #     "incorrect_answers": ["x","x","x"]
    #     }}
    #     """

    #     if attempt > 0:
    #         incorrect_solution_prompt += "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."

    #     if (solution != None):
    #         answer_response = generate(model="llama3.1:8b",
    #                                 prompt=incorrect_solution_prompt,
    #                                 options = {"temperature": 0.4,
    #                                             "top_p": 0.9,
    #                                             "top_k": 40}) #slightly less randomness, 
    #     #print("RAW ANSWER", answer_response.response)
        
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

    #     # If we reach here → SUCCESS
    #     break

    # else:
    #     # All retries failed
    #     raise ValueError("Failed to generate valid JSON after retries")

    # #combining generated incorrect responses with correct solution. 
    # incorrect_data = answer_data
    # answers = incorrect_data["incorrect_answers"] + [str(solution)]
   
    incorrect_answers = inc_gen.generate_incorrect_rational(solution) if solution is not None else []
    solution = serialize_sympy(solution) if solution is not None else None
    answers = [serialize_sympy(ans) for ans in incorrect_answers] + [solution]

    random.shuffle(answers)

    #Build final JSON
    return {
        "question_text": question_data["question_text"],
        "question_topic": "probability",
        "answer_options": answers,
        "correct_answer": solution
    }


#display on flask
# app= Flask(__name__)
# CORS(app)
# @app.route("/")
# def display_question():
#     return jsonify(generate_algebra_question())

