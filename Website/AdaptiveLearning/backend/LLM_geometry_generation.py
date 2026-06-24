#area, perimeter - circle, triangle, rectangle. (possibly add rhombus, trapezoid, parallelogram)
#volume - cylinder, sphere, rectangle, cube, pyramid

#need to specify questions do not involve angles 
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
from sympy import sqrt, symbols, Eq, solve, sympify, Integer, Rational, pi
import incorrect_solution_generation as inc_gen

#current geometry scenarions: perimeter, area, volume, missing_side, pythagorean_theorem
#APPROXIMATING 3.14 for pi for simplicity/consistancy
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

simple_pi = sympify(3.14)

def normalize_solution(sol):
    if isinstance(sol, list):
        return sol[0]   # or pick valid one
    return sol

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

def format_two_decimals(x):
    if isinstance(x, list):
        x = x[0]
    val = float(x.evalf()) if hasattr(x, "evalf") else float(x)
    # check if it's effectively an integer
    if val.is_integer():
        return str(int(val))   # "5"
    else:
        return f"{val:.2f}"    # "3.50"

#Helpers
def to_num(x):
    return sympify(x)

def preprocess_variables(vars_dict):
    return {k : sympify(v) for k, v in vars_dict.items()}

#Area/Perimeter
def solve_triangle_perimeter(s1, s2, s3):
    return s1 + s2 + s3

def solve_triangle_area(base, height):
    return Rational(1/2) * base * height

def solve_rectangle_perimeter(l, w):
    return 2*l + 2*w

def solve_rectangle_area(l,w):
    return l * w

def solve_circle_circumference(r):
    return 2 * simple_pi * r

def solve_circle_area(r):
    return simple_pi * r * r

#Volume
def solve_rect_volume(l,w,h):
    return l*w*h

def solve_cube_volume(a):
    return a**3

def solve_cylinder_volume(r,h):
    return simple_pi * r**2 *h

def solve_pyramid_volume(b,h):
    return Rational(1/3) * b * h

def solve_sphere_volume(r):
    return Rational(4/3) * simple_pi * r**3

#Pythagorean Theorem
def solve_pythag(a, b):
    c = (a**2) + (b**2)
    return sqrt(c)

#Find missing side 
def rect_area_missing_side(area, s1):
    x = symbols('x')
    solution = solve(Eq(x * s1, area), x)
    return solution

def rect_perimeter_missing_side(perim, s1):
    x = symbols('x')
    solution = solve(Eq((2*s1) + (2*x), perim), x)
    return solution

def triangle_area_missing_side(area, s1):
    x = symbols('x')
    solution = solve(Eq((1/2)*s1 *x, area), x)
    return solution

def traingle_perimeter_missing_side(perim, s1,s2):
    x = symbols('x')
    solution = solve(Eq(s1 + s2 + x, perim), x)
    return solution

def circle_area_missing_side(area):
    x = symbols('x')
    solution = solve(Eq(simple_pi*x**2, area), x)
    return solution

def circle_circumference_missing_side(circ):
    x = symbols('x')
    solution = solve(Eq(2*simple_pi*x, circ), x)
    return solution

#IDEA: Randomize scenario, have a separate prompt for each. May be easier for LLM to handle

geometry_prompt = f"""
You are to provide a Math question suitable for students. The response must be in JSON format. 
The Question Text, Question Topic, Scenario, Variables, and Target will be displayed. The Question Topic will always be "geometry".

There are multiple possible scenarios to select from. You must select only ONE scenario to generate a question and corresponding JSON response for.

IMPORTANT:
- ALWAYS approximate pi as 3.14
- All numeric values must be simple (integers or one decimal max)
- Ensure the problem is solvable using the provided variables

EASY TOPICS: rectangle_area, rectangle_perimeter, triangle_area, triangle_perimeter, circle_area, circle_circumference
MEDIUM TOPICS: rectangle_missing_side_area, rectangle_missing_side_perimeter, circle_missing_radius, pythagorean_theorem
HARD TOPICS: rectangular_prism_volume, cylinder_volume, sphere_volume, cube_volume, pyramid_volume

SCENARIO 1: rectangle_area
Example:
"A rectangle has a length of 5 units and a width of 3 units. What is its area?"

JSON structure:
{{
  "question_text": "A rectangle has a length of 5 units and a width of 3 units. What is its area?",
  "type": "geometry",
  "scenario": "rectangle_area",
  "variables": {{
    "length": "5",
    "width": "3"
  }}
}}

SCENARIO 2: rectangle_perimeter
Example:
"A rectangle has a length of 8 units and a width of 2 units. What is its perimeter?"

{{
  "question_text": "A rectangle has a length of 8 units and a width of 2 units. What is its perimeter?",
  "type": "geometry",
  "scenario": "rectangle_perimeter",
  "variables": {{
    "length": "8",
    "width": "2"
  }}
}}

SCENARIO 3: triangle_area
Example:
"A triangle has a base of 6 units and a height of 4 units. What is its area?"

{{
  "question_text": "A triangle has a base of 6 units and a height of 4 units. What is its area?",
  "type": "geometry",
  "scenario": "triangle_area",
  "variables": {{
    "base": "6",
    "height": "4"
  }}
}}

SCENARIO 4: triangle_perimeter
Example:
"A triangle has side lengths 3, 4, and 5 units. What is its perimeter?"

{{
  "question_text": "A triangle has side lengths 3, 4, and 5 units. What is its perimeter?",
  "type": "geometry",
  "scenario": "triangle_perimeter",
  "variables": {{
    "s1": "3",
    "s2": "4",
    "s3": "5"
  }}
}}

SCENARIO 5: circle_area
Example:
"A circle has a radius of 7 units. What is its area?"

{{
  "question_text": "A circle has a radius of 7 units. What is its area?",
  "type": "geometry",
  "scenario": "circle_area",
  "variables": {{
    "radius": "7"
  }}
}}

SCENARIO 6: circle_circumference
Example:
"A circle has a radius of 5 units. What is its circumference?"

{{
  "question_text": "A circle has a radius of 5 units. What is its circumference?",
  "type": "geometry",
  "scenario": "circle_circumference",
  "variables": {{
    "radius": "5"
  }}
}}



SCENARIO 7: rectangular_prism_volume
Example:
"A rectangular prism has a length of 4, width of 3, and height of 2. What is its volume?"

{{
  "question_text": "A rectangular prism has a length of 4, width of 3, and height of 2. What is its volume?",
  "type": "geometry",
  "scenario": "rect_volume",
  "variables": {{
    "length": "4",
    "width": "3",
    "height": "2"
  }}
}}

SCENARIO 8: cylinder_volume
Example:
"A cylinder has a radius of 3 and height of 5. What is its volume?"

{{
  "question_text": "A cylinder has a radius of 3 and height of 5. What is its volume?",
  "type": "geometry",
  "scenario": "cylinder_volume",
  "variables": {{
    "radius": "3",
    "height": "5"
  }}
}}

SCENARIO 9: sphere_volume
Example:
"A sphere has a radius of 3. What is its volume?"

{{
  "question_text": "A sphere has a radius of 3. What is its volume?",
  "type": "geometry",
  "scenario": "sphere_volume",
  "variables": {{
    "radius": "3"
  }}
}}

SCENARIO 10: pythagorean
Example:
"A right triangle has legs of 3 and 4 units. What is the hypotenuse?"

{{
  "question_text": "A right triangle has legs of 3 and 4 units. What is the hypotenuse?",
  "type": "geometry",
  "scenario": "pythagorean",
  "variables": {{
    "a": "3",
    "b": "4"
  }}
}}

SCENARIO 11: rectangle_missing_side_area
Example:
"A rectangle has an area of 20 square units and a width of 4 units. What is the length?"

{{
  "question_text": "A rectangle has an area of 20 square units and a width of 4 units. What is the length?",
  "type": "geometry",
  "scenario": "rect_area_missing_side",
  "variables": {{
    "area": "20",
    "known_side": "4"
  }}
}}

SCENARIO 12: rectangle_missing_side_perimeter
Example:
"A rectangle has a perimeter of 24 units and one side length of 5 units. What is the other side?"

{{
  "question_text": "A rectangle has a perimeter of 24 units and one side length of 5 units. What is the other side?",
  "type": "geometry",
  "scenario": "rect_perimeter_missing_side",
  "variables": {{
    "perimeter": "24",
    "known_side": "5"
  }}
}}

SCENARIO 13: circle_missing_radius_area
Example:
"A circle has an area of 50.24 square units. What is the radius?"

{{
  "question_text": "A circle has an area of 50.24 square units. What is the radius?",
  "type": "geometry",
  "scenario": "circle_area_missing_side",
  "variables": {{
    "area": "50.24"
  }}
}}

SCENARIO 14: triangle_missing_side_area
Example:
"A triangle has an area of 12 square units and a base of 6 units. What is the height?"

{{
  "question_text": "A triangle has an area of 12 square units and a base of 6 units. What is the height?",
  "type": "geometry",
  "scenario": "triangle_area_missing_side",
  "variables": {{
    "area": "12",
    "known_side": "6"
  }}
}}

SCENARIO 15: triangle_missing_side_perimeter
Example:
"A triangle has a perimeter of 18 units. Two of its sides are 5 units and 7 units. What is the length of the third side?"

{{
  "question_text": "A triangle has a perimeter of 18 units. Two of its sides are 5 units and 7 units. What is the length of the third side?",
  "type": "geometry",
  "scenario": "triangle_perimeter_missing_side",
  "variables": {{
    "perimeter": "18",
    "s1": "5",
    "s2": "7"
  }}
}}

SCENARIO 16: circle_missing_radius_circumference
Example:
"A circle has a circumference of 31.4 units. What is the radius?"

{{
  "question_text": "A circle has a circumference of 31.4 units. What is the radius?",
  "type": "geometry",
  "scenario": "circle_circumference_missing_side",
  "variables": {{
    "circumference": "31.4"
  }}
}}

SCENARIO 17: cube_volume
Example:
"A cube has a side length of 4 units. What is its volume?"

{{
  "question_text": "A cube has a side length of 4 units. What is its volume?",
  "type": "geometry",
  "scenario": "cube_volume",
  "variables": {{
    "side": "4"
  }}
}}

SCENARIO 18: pyramid_volume
Example:
"A pyramid has a base area of 30 square units and a height of 9 units. What is its volume?"

{{
  "question_text": "A pyramid has a base area of 30 square units and a height of 9 units. What is its volume?",
  "type": "geometry",
  "scenario": "pyramid_volume",
  "variables": {{
    "base_area": "30",
    "height": "9"
  }}
}}

FINAL RULES:
- Select ONLY ONE scenario, Generate ONLY ONE question, return ONLY ONE JSON object. 
- Return ONLY valid JSON, with NO additional text or characters
- Do NOT include: explanations, markdown, backticks, extra text before or after JSON
- Use ONLY double quotes
- All keys must match EXACTLY as shown
"""

solution = -1


#Potential improvements:
#Maybe can store previously generated question, feed into LLM to ensure next question is not the same.
#If solution is a fraction, at least one other generated response should be a fraction. 
def generate_geometry_question(global_questions, prev_questions, difficulty, grade,max_retries=3):
    for attempt in range(max_retries):
        if attempt > 0:
            prompt = geometry_prompt + "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."
        else:
            prompt = geometry_prompt

        #randomize scenario selection to ensure variety in generated questions.
        scenario = random.randint(1,18)

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

        # print("RAW RESPONSE:")
        print(response.response)

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
    
    scenario = question_data["scenario"]
    vars = question_data["variables"]
    vars = preprocess_variables(vars)

    match (scenario):
        case "rectangle_area":
            solution = solve_rectangle_area(vars["length"], vars["width"])
        case "rectangle_perimeter":
            solution = solve_rectangle_perimeter(vars["length"], vars["width"])
        case "triangle_area":
            solution = solve_triangle_area(vars["base"], vars["height"])
        case "triangle_perimeter":
            solution = solve_triangle_perimeter(vars["s1"], vars["s2"], vars["s3"])
        case "circle_area":
            solution = solve_circle_area(vars["radius"])
        case "circle_circumference":
            solution = solve_circle_circumference(vars["radius"])
        case "rect_volume": 
            solution = solve_rect_volume(vars["length"], vars["width"], vars["height"])
        case "cylinder_volume":
            solution = solve_cylinder_volume(vars["radius"], vars["height"])
        case "sphere_volume":
            solution = solve_sphere_volume(vars["radius"])
        case "cube_volume":
            solution = solve_cube_volume(vars["side"])
        case "pyramid_volume":
            solution = solve_pyramid_volume(vars["base_area"], vars["height"])
        case "pythagorean":
            solution = solve_pythag(vars["a"], vars["b"])
        case "rect_area_missing_side" :
            solution = rect_area_missing_side(vars["area"], vars["known_side"])
        case "rect_perimeter_missing_side" :
            solution = rect_perimeter_missing_side(vars["perimeter"], vars["known_side"])
        case "circle_area_missing_side":
            solution = circle_area_missing_side(vars["area"])
        case "circle_circumference_missing_side":
            solution = circle_circumference_missing_side(vars["circumference"])
        case "triangle_area_missing_side" :
            solution = triangle_area_missing_side(vars["area"], vars["known_side"])
        case "triangle_perimeter_missing_side" :
            solution = traingle_perimeter_missing_side(vars["perimeter"], vars["s1"], vars["s2"])

    # solution = str(solution) if solution else None


    # for attempt in range(max_retries):
    #     incorrect_solution_prompt = f"""
    #     Generate three incorrect numerical answer options for a math problem.
    #     Question:
    #     {question_data["question_text"]}
    #     Correct Answer:
    #     {solution}

    #     Rules:
    #     - NO additional text, characters, or symbols should be placed before or after the JSON response. Response should strictly include JSON formatted data.
    #     - The answers must NOT equal or simplify to {solution}
    #     - Unique numbers only. NUMBERS must be represented as strings. For example, "0.5" or "1/2" are valid representations.
    #     - Only numbers or simple numeric strings are allowed. Do NOT use brackets, fractions, or expressions.
    #     - Fractions or values with up to two decimal places are allowed as long as they are unique and not equivalent to other values.
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
        
        
    #     # print("RAW RESPONSE:")
    #     print(answer_response.response)
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
    
    # incorrect_answers = inc_gen.generate_general_incorrect_answers(float(solution)) if solution is not None else []
    # solution = format_two_decimals(solution) if solution is not None else None
    # solution = serialize_sympy(solution)
    # answers = [serialize_sympy(ans) for ans in incorrect_answers] + [solution]
    
    # normalize correct answer 
    solution = normalize_solution(solution)
    solution_float = float(solution)
    solution = format_two_decimals(solution)
    # generate incorrect answers using float
    incorrect_answers = inc_gen.generate_general_incorrect_answers(solution_float)
    # ensure everything is JSON-safe strings or floats
    answers = [round(float(ans), 2) for ans in incorrect_answers] + [solution]


    random.shuffle(answers)

    #Build final JSON
    return {
        "question_text": question_data["question_text"],
        "question_topic": "geometry",
        "answer_options": answers,
        "correct_answer": solution
    }


#display on flask
# app= Flask(__name__)
# CORS(app)
# @app.route("/")
# def display_question():
#     return jsonify(generate_algebra_question())

