# 1. Get question from LLM. Response should include question text, topic, variables, operations.
# 2. Solve question using Python (potentially Wolfram Alpha API) to obtain correct answer.
# 3. Generate 4 unique answer options, including correct answer, using LLM.
# 4. Send question and answer options to frontend to display to user.

#Potential topic types. 

#Topics: 
#Ordering (e.g order numbers (including negatives and two digit decimal values) from least to greatest or vice versa). For least to greatest the question topic should be 'ordering-least', otherwise it is 'ordering-greatest'. 
#Rationals: (operations involving two fractions. Varaibles should be in the format '2/5' for a single fraction). The expected operation type should also be included.
#Operations: Variables should be in the format '2/5' for a single fraction, or 'x' for a variable. Additionally, the operations should be clearly defined from left to right using '+', '-', '*', '/' to indicate the operations. Both variables and operations will be realized left to right. For example, a question with variables '2', '3', '4', '+', '*' will be seen as 2 + 3 * 4, and the operations will be performed left to right, so it will be (2 + 3) * 4 = 20.
#Expressions: ex 'Simplify the expression 4(2x - 3).' The question should include the expression to be simplified, and the expected operation type should also be included. Variables should be in the format 'x' for a variable, and operations should be defined using '+', '-', '*', '/' to indicate the operations. The operations will be performed left to right, so for example, a question with variables '2x', '-3', '4', '*', will be seen as 2x - 3 * 4, and the operations will be performed left to right, so it will be (2x - 3) * 4 = 8x - 12.
#Algebra: ex 'Solve for x: 2x + 3 = 7.' The question should include the equation to be solved, and the expected operation type should also be included. Variables should be in the format 'x' for a variable, and operations should be defined using '+', '-', '*', '/' to indicate the operations. The operations will be performed left to right, so for example, a question with variables '2x', '+', '3', '=', '7', will be seen as 2x + 3 = 7, and the operations will be performed left to right, so it will be 2x = 4, and x = 2.
#Geometry: ex 'Find the area of a circle with a radius of 6 cm.' The question should include the geometric figure to be analyzed, and the expected operation type should also be included. Variables should be in the format '6 cm' for a variable, and operations should be defined using '+', '-', '*', '/' to indicate the operations. For example, a question with variables 'radius', '6 cm', 'area', '*', 'pi', will be seen as area = radius * radius * pi, and the operations will be performed left to right, so it will be area = 6 cm * 6 cm * pi = 36pi cm^2. Area, Perimeter, and Volume for simple shapes are potential questions. 
#Angle relationships: 'IF two angles are complementary and one angle measures 35 degrees, what is the measure of the other angle?' The question should include the angle relationship to be analyzed, and the expected operation type should also be included. Variables should be in the format '35 degrees' for a variable, and operations should be defined using '+', '-', '*', '/' to indicate the operations. For example, a question with variables 'angle1', '35 degrees', 'angle2', '+', 'complementary', will be seen as angle1 + angle2 = 90 degrees, and the operations will be performed left to right, so it will be 35 degrees + angle2 = 90 degrees, and angle2 = 55 degrees.
#Mean: Provide a short to medium sized integer or decimal variable dataset. 
#Median: Provide a short to medium sized integer or decimal variable dataset.
#Mode: Provide a short to medium sized integer or decimal variable dataset.
#Probability: ex 'A bag contains 5 red marbles, 3 blue marbles, and 2 green marbles. If a marble is drawn at random, what is the probability of drawing a red marble?' The question should include the probability scenario to be analyzed, and the expected operation type should also be included. Variables should be in the format '5 red marbles' for a variable, and operations should be defined using '+', '-', '*', '/' to indicate the operations. For example, a question with variables 'red marbles', '5', 'blue marbles', '3', 'green marbles', '2', 'total marbles', '+', 'probability of red marble', '/', 'total marbles', will be seen as probability of red marble = red marbles / total marbles, and the operations will be performed left to right, so it will be probability of red marble = 5 / (5 + 3 + 2) = 5/10 = 1/2.

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

def to_native(value): 
    if isinstance(value, Integer): 
        return int(value) 
    return value
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

algebra_prompt = f"""
You are to provide a Math question suitable for students. The response must be in JSON format. 
The Question Text, Question Topic, and Variables will be displayed. The Question Topic will be "algebra".

Algebra example: "Solve for x: 2x + 3 = 7." 
The question should include the equation to be solved. Variables must be formatted as strings such as "x", and operations must be 
represented using the symbols "+", "-", "*", "/". For example, the variables list ["2x", "+", "3", "=", "7"] represents the equation 2x + 3 = 7.

There may be up to three operations on the left-hand side, and up to two terms may contain the variable "x". 
Use a variety of integer values as long as the equation has a valid solution.

Return ONLY valid JSON with no text before or after the JSON object.

The JSON must follow this exact structure:

{{  "question_text": "Solve for x: 2x + 3 = 7.",
  "question_topic": "algebra",
  "variables": ["2x", "+", "3", "=", "7"]
}}

Rules:
- Use ONLY double quotes for all strings.
- The JSON object must contain the keys "question_text", "question_topic", and "variables".
- "variables" must be a list of strings.
- Do NOT include any characters outside the JSON object.
"""

solution = -1


#Potential improvements:
#Maybe can store previously generated question, feed into LLM to ensure next question is not the same.
#If solution is a fraction, at least one other generated response should be a fraction. 
def generate_algebra_question(global_questions, prev_questions, difficulty, grade, max_retries=3):
    for attempt in range(max_retries):
        if attempt > 0:
            prompt = algebra_prompt + "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."
        else:
            prompt = algebra_prompt
        
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
        required_keys = ["variables", "question_text"]
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
    x = symbols('x') #define variable for sympy
    left, right = equation_stra.split('=') #split equation into left and right parts
    left_expr = parse_expr(left, transformations=transformations)
    right_expr = parse_expr(right, transformations=transformations)
    equation = Eq(left_expr, right_expr) 
    solution = solve(equation, x) #solve for x



    #print("Solution:", solution)
    solution = str(solution[0]) if solution else None #solve returns a list, we want the first solution if it exists, otherwise None

    #
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
    #     - Unique numbers only. NUMBERS must be represented as strings. For example, "0.5" or "14" are valid representations.
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

    #     # If we reach here → SUCCESS
    #     break

    # else:
    #     # All retries failed
    #     raise ValueError("Failed to generate valid JSON after retries")

    # #combining generated incorrect responses with correct solution. 
    # incorrect_data = answer_data
    # answers = incorrect_data["incorrect_answers"] + [str(solution)]

    incorrect_answers = inc_gen.generate_general_incorrect_answers(solution)
    answers = [str(ans) for ans in incorrect_answers] + [str(solution)]

    random.shuffle(answers)

    #Build final JSON
    return {
        "question_text": question_data["question_text"],
        "question_topic": "algebra",
        "answer_options": answers,
        "correct_answer": solution
    }


#display on flask
# app= Flask(__name__)
# CORS(app)
# @app.route("/")
# def display_question():
#     return jsonify(generate_algebra_question())

