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
) #treat 2x as 2*x for sympy parsing
import incorrect_solution_generation as inc_gen

# Enable implicit multiplication (2x → 2*x)
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
- There may be up to six operations.
- There can be up to two sets of parentheses.
- For simplify problems, only combine like terms (no equations).
- Ensure the final answer is a whole number when possible.
- Use ONLY double quotes for all strings.
- The JSON object must contain the keys "question_text", "question_topic", "scenario", and "variables".
- "variables" must be a list of strings.
- Do NOT include any characters outside the JSON object.

Return ONLY valid JSON with no text before or after the JSON object.
"""

solution = -1


#Potential improvements:
#Maybe can store previously generated question, feed into LLM to ensure next question is not the same.
#If solution is a fraction, at least one other generated response should be a fraction. 
def generate_expression_question(global_questions, prev_questions, difficulty, grade, max_retries=3):
    for attempt in range(max_retries):
        if attempt > 0:
            prompt = expr_prompt + "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."
        else:
            prompt = expr_prompt


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
        required_keys = ["scenario", "variables", "question_text"]
        if not all(k in question_data for k in required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", question_data)
            continue

        # If we reach here → SUCCESS
        break

    else:
        # All retries failed
        raise ValueError("Failed to generate valid JSON after retries")
    
    parts = question_data['variables']
    equation_stra = "".join(parts) #turn individual variables/operations into a single string to be parsed by sympy
    equation_stra = equation_stra.replace("−", "-")
    expr = parse_expr(equation_stra, transformations=transformations)

    scenario = question_data["scenario"]

    match scenario:
        case "evaluate" | "order_of_operations":
            solution = expr
        case "simplify":
            solution = sp.simplify(expr)
    
    #print("Solution:", solution)
    # solution = str(solution) if solution is not None else None

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
    #     - No fractions or expressions
    #     - Return JSON format: each array value of incorrect_answers should be a separate incorrect answer
    #     {{
    #     "incorrect_answers": ["x","x","x"]
    #     }}
    #     """

    #     if attempt > 0:
    #         incorrect_solution_prompt += "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."

    #     answer_response = None

    #     if solution is not None:
    #         answer_response = generate(model="llama3.1:8b",
    #                                 prompt=incorrect_solution_prompt,
    #                                 options = {"temperature": 0.4,
    #                                             "top_p": 0.9,
    #                                             "top_k": 40}) #slightly less randomness, 

    #     if answer_response is None:
    #         print("Answer generation failed")
    #         continue

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
    
    #NOT SURE IF NEED TO SPLIT, Going to try w/out for now to test. 
    # if scenario == "simplify":
    #     incorrect_answers = inc_gen.generate_incorrect_simplified_answers(float(solution)) if solution is not None else [] #for answers like 2x
    # else:
    #     incorrect_answers = inc_gen.generate_general_incorrect_answers(float(solution)) if solution is not None else []
    
    if scenario == "simplify":
        incorrect_answers = inc_gen.generate_symbolic_incorrect_answers(solution)
    else:
        if is_numeric(solution):
            incorrect_answers = inc_gen.generate_general_incorrect_answers(float(solution))
        else:
            incorrect_answers = []
    
    
    answers = [str(ans) for ans in incorrect_answers] + [str(solution)]
    random.shuffle(answers)

    #Build final JSON
    return {
        "question_text": question_data["question_text"],
        "question_topic": "expressions",
        "answer_options": answers,
        "correct_answer": str(normalize_answer(solution))
    }


#display on flask
# app= Flask(__name__)
# CORS(app)
# @app.route("/")
# def display_question():
#     return jsonify(generate_algebra_question())

