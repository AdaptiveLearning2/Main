#Ideally - this will be called from LLMTest2 to randomly select a topic. Then can call methods from other files for specific question generation. 
import os
from flask import Flask, jsonify, request
from flask_cors import CORS #pip install flask-cors
from supabase import create_client, Client #pip install supabase
from dotenv import load_dotenv   #pip install dotenv
from ollama import generate
import json
import random
from collections import deque
import concurrent.futures

from supabase_auth import datetime
import LLM_algebra_generation, LLM_ordering_generation, LLM_rationals_generation, LLM_mean_generation, LLM_median_generation
import LLM_mode_generation, LLM_probability_generation, LLM_geometry_generation, LLM_angle_relationship_generation, LLM_expressions_generation
#python -m flask --app LLM_topic_decider run

#connect with supabase 
load_dotenv() 
# url = os.getenv("VITE_SUPABASE_URL")
# key = os.getenv("VITE_SUPABASE_ANON_KEY")
SUPABASE_URL     = os.getenv("SUPABASE_URL")
SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase = create_client(SUPABASE_URL, SERVICE_ROLE_KEY)


#Possibly worthwhile to store history in supabase. 
#Possibly reset history per session. 

user_cache = {}
def get_user_performance(user_id):
    if user_id in user_cache:
        return user_cache[user_id]

    data = supabase.table("user_math_performance") \
        .select("correct_questions,attempted_questions, math_topics(topic_name)") \
        .eq("user_id", user_id) \
        .execute()
    user_cache[user_id] = data
    return data

#Store 40 questions globally, 10 per topic 
user_histories = {}

def get_user_history(user_id):
    if user_id not in user_histories:
        user_histories[user_id] = {
            "global": deque(maxlen=40), #stores last 40 questions regardless of topic, can use to ensure no repeats
            "geometry": deque(maxlen=10),
            "algebra": deque(maxlen=10),
            "expressions": deque(maxlen=10),
            "ordering": deque(maxlen=10),
            "rationals": deque(maxlen=10),
            "mean": deque(maxlen=10),
            "median": deque(maxlen=10),
            "mode": deque(maxlen=10),
            "probability": deque(maxlen=10),
            "angle_relationships": deque(maxlen=10)
        }
    return user_histories[user_id]


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

def add_question_to_supabase(question, difficulty):
    questions = supabase.table("questions").select("question_text").execute().data or []

    if any(q["question_text"] == question["question_text"] for q in questions):
        return False

    response = supabase.table("questions").insert({
        "subject" : question["question_topic"],
        "difficulty": difficulty,
        "question_text": question["question_text"],
        "options" : question["answer_options"],
        "correct_answer": question["correct_answer"],
        "created_at": str(datetime.now())
    }).execute()
    
    if response.data:
        return True
    else:
        print("Supabase insert error:", response.error)
        return False


#Possibility - can just select topic/difficulty manually if LLM generation is too slow. 
def calculate_topic_and_difficulty(user_id, grade):
    accuracy_response = get_user_performance(user_id)

    data = accuracy_response.data or []
    history = get_user_history(user_id)

    topic_scores = []

    for row in data:
        topic = row["math_topics"]["topic_name"]
        correct = row.get("correct_questions") or 0
        attempted = row.get("attempted_questions") or 0

        acc = correct / attempted if attempted > 0 else 0

        # Penalize repetition
        recent = [q["topic"] for q in history["global"]][-5:]
        repeat_penalty = recent.count(topic) * 0.1

        score = acc + repeat_penalty
        topic_scores.append((topic, score))

    # lowest score = worst topic
    topic = sorted(topic_scores, key=lambda x: x[1])[0][0]

    # difficulty
    if acc < 0.4:
        difficulty = "easy"
    elif acc < 0.7:
        difficulty = "medium"
    else:
        difficulty = "hard"

    return topic, difficulty


def parallel_topic_and_difficulty_calculation(topic_prompt, difficulty_prompt):
    with concurrent.futures.ThreadPoolExecutor() as executor:
        topic_future = executor.submit(generate, model="llama3.1:8b", prompt=topic_prompt, options={"temperature": 0.7, "top_p": 0.95, "top_k": 100})
        difficulty_future = executor.submit(generate, model="llama3.1:8b", prompt=difficulty_prompt, options={"temperature": 0.7, "top_p": 0.95, "top_k": 100})

        topic_response = topic_future.result()
        difficulty_response = difficulty_future.result()

        if not topic_response or not difficulty_response:
            print("No response received for topic or difficulty calculation.")
            return None, None

    return topic_response, difficulty_response


#Change: use two separate prompt to try to improve speed and get more "adaptive" results. 
#Prompt 1: Take in user performance data/recent history, select topic. 
#Prompt 2: Select difficulty. 

#PROBABLY: need to provide previous and current user performance data so LLM can see differences.  
#Also should provide last topic selection? not sure since history should cover that. 
def LLM_topic_and_difficulty_separate_decider(user_id, grade): 
    accuracy_response = get_user_performance(user_id)

    json_response = accuracy_response.data or []

    history = get_user_history(user_id)
    recent_global = list(history["global"])[-5:]


    topic_prompt = f"""
        You are a function that returns ONLY valid JSON.

        DO NOT include explanations, reasoning, code, markdown, symbols, or extra text.

        INPUT:
        Student Performance = {json_response}
        Recent Question History = {recent_global}
        Student Grade Level = {grade}

        TASK: Select a math topic from the list below. You will be provided with the user's performance data and recent question history. 
        
        There are three selection methods you can utilize for topic selection: 
        1) Improvement: If the user has shown improvement in a topic, you may select that topic to reinforce learning. 
        2) Struggle: If the user is struggling in a topic, you may select that topic to provide additional practice.
        3) Random Variation: You may select a random topic to provide variety. 
        
        GUIDANCE: 
        Random variation should be used initially to introduce the user to a variety of topics. As the user accumulates performance data, you should rely more heavily on improvement and struggle-based selection.
        Struggle should be weighted more heavily than improvement. However, if a user continues to answer a "struggle" topic incorrectly, another topic should be selected for at least the next 5 topic selections.

        Grade Level Impact: 
        The student's grade level should influnce topic selection. 
        For example, if a student is in an earlier grade, you may want to prioritize foundational topics like "ordering" or "geometry" over more advanced topics like "probability".
        Algebra and probability should only appear after grade 6. Grades 1-3 should primarily see ordering, geometry, and expressions. Grades 4-5 can see all topics except probability and algebra. Grade 6+ can see all topics.


        TOPICS:
        geometry, algebra, expressions, ordering, rationals, mean, median, mode, probability, angle_relationships

        RULES (CRITICAL):
        - Use ONLY the provided performance data. NULL attempted_questions values indicate that the topic has not generated yet.
        - Do NOT create, assume, or infer any missing values
        - Do NOT fabricate tables, examples, or additional data
        - Do NOT modify or reinterpret the input data
        - If correct_questions OR attempted_questions is 0 or null → accuracy = 0
        - If data is missing → accuracy = 0
        - Do NOT explain your reasoning
        - Do NOT output calculations
        - Output ONLY JSON

        OUTPUT FORMAT (STRICT):
        Return ONLY this JSON. No extra text.

        {{
            "topic": "one_of_the_topics",
            "selection_method": "improvement_or_struggle_or_random"
        }}
    """
    difficulty_prompt = f"""
        You are a function that returns ONLY valid JSON.

        DO NOT include explanations, reasoning, code, markdown, symbols, or extra text.

        TASK: Select a difficulty level for the next question based on the user's performance data, grade level, and recent question history.

        INPUT:
        Student Performance = {json_response}
        Recent Question History = {recent_global}
        Student Grade Level = {grade}

        DIFFICULTY RULES:
        Grade Level Impact:
        Grade's 1-4 should primarily receive "easy" questions, with occasional "medium" questions for variety. Grades 5-6 can receive a mix of "easy" and "medium" questions, with rare "hard" questions. Grade 7+ can receive a balanced mix of "easy", "medium", and "hard" questions.
        Accuracy Impact:
        Performance should heavily influence difficulty selection. Randomness can be used OCCASIONALLY at higher grade levels to test the student's knowledge.
        - accuracy < 40% → "easy"
        - 40%–70% → "medium"
        - > 70% → "hard"

        RULES (CRITICAL):
        - Use ONLY the provided performance data. NULL attempted_questions values indicate that the topic has not generated yet.
        - Do NOT create, assume, or infer any missing values
        - Do NOT fabricate tables, examples, or additional data
        - Do NOT modify or reinterpret the input data
        - If correct_questions OR attempted_questions is 0 or null → accuracy = 0
        - If data is missing → accuracy = 0
        - Do NOT explain your reasoning
        - Do NOT output calculations
        - Select a difficulty within the select grade level's capabilities. 
        - Output ONLY JSON


        OUTPUT FORMAT (STRICT):
        Return ONLY this JSON. No extra text.

        {{
            "difficulty": "easy_or_medium_or_hard"
        }}
    """



    for attempt in range(2): 
        topic_response, difficulty_response = parallel_topic_and_difficulty_calculation(topic_prompt, difficulty_prompt)

        raw_topic = extract_json(topic_response.response)
        raw_difficulty = extract_json(difficulty_response.response)
        if not raw_topic or not raw_difficulty:
            print(f"[Attempt {attempt+1}] No JSON found")
            print(topic_response.response)
            print(difficulty_response.response)
            continue
        

        try:
            topic_data = json.loads(raw_topic)
            difficulty_data = json.loads(raw_difficulty)
        except Exception as e:
                print(f"[Attempt {attempt+1}] JSON parse failed:", e)
                print(topic_response.response)
                print(difficulty_response.response)
                continue

        # Validate required keys
        topic_required_keys = ["topic", "selection_method"]
        difficulty_required_keys = ["difficulty"]
        if not all(k in topic_data for k in topic_required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", topic_data)
            continue

        if not all(k in difficulty_data for k in difficulty_required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", difficulty_data)
            continue

        # If we reach here → SUCCESS
        break

    if (topic_data):
        #WILL add check later to default to randomized selection if LLM topic selection fails. 
        topic = topic_data["topic"]
        difficulty = difficulty_data["difficulty"]
        
    else:
        print("LLM selection generation failed, fallback to randomized selection")
        topic,difficulty = randomize_selection(accuracy_response)
    
    print(f"Selected topic: {topic} (selection method: {topic_data.get('selection_method', 'N/A')}) at difficulty: {difficulty}")
    question = question_generation(topic, difficulty, user_id, grade)
    print(question)


    if (add_question_to_supabase(question, difficulty)):
        print("Question added to supabase successfully")


    return question


#Theres probably a cleaner way to do this - have a list of topics and then loop through to find the right one, rather than hardcoding every option. But this works for now.
def question_generation(topic, difficulty, user_id, grade):
    history = get_user_history(user_id)
    recent_global = list(history["global"])[-5:]
    recent_topic  = list(history[topic])[-5:] if topic in history else []
    print(f"topic: {topic} difficulty: {difficulty}")
    match topic:
        case "ordering":
            response = LLM_ordering_generation.generate_ordering_question(recent_global, recent_topic,
                difficulty=difficulty, grade=grade)
            history["global"].append({
                    "text": response["question_text"],
                    "topic": "ordering"})
            history["ordering"].append({
                    "text": response["question_text"],
                    "topic": "ordering"}) 

        case "geometry":
            response = LLM_geometry_generation.generate_geometry_question(recent_global, recent_topic,
                difficulty=difficulty, grade=grade)
            history["global"].append({
                    "text": response["question_text"],
                    "topic": "geometry"})
            history["geometry"].append({
                    "text": response["question_text"],
                    "topic": "geometry"})
        case "algebra":
            response = LLM_algebra_generation.generate_algebra_question(recent_global, recent_topic,
                difficulty=difficulty, grade=grade)
            history["global"].append({
                    "text": response["question_text"],
                    "topic": "algebra"})
            history["algebra"].append({
                    "text": response["question_text"],
                    "topic": "algebra"})
        case "expressions":
            response = LLM_expressions_generation.generate_expression_question(recent_global, recent_topic,
                difficulty=difficulty, grade=grade)
            history["global"].append({
                    "text": response["question_text"],
                    "topic": "expressions"})
            history["expressions"].append({
                    "text": response["question_text"],
                    "topic": "expressions"})
        case "rationals":
            response = LLM_rationals_generation.generate_rational_question(recent_global, recent_topic,
                difficulty=difficulty, grade=grade)
            history["global"].append({
                    "text": response["question_text"],
                    "topic": "rationals"})
            history["rationals"].append({
                    "text": response["question_text"],
                    "topic": "rationals"})
        case "mean":
            response = LLM_mean_generation.generate_mean_question(recent_global, recent_topic,
                difficulty=difficulty, grade=grade)
            history["global"].append({
                    "text": response["question_text"],
                    "topic": "mean"})
            history["mean"].append({
                    "text": response["question_text"],
                    "topic": "mean"})
        case "median":
            response = LLM_median_generation.generate_median_question(recent_global, recent_topic,
                difficulty=difficulty, grade=grade)
            history["global"].append({
                    "text": response["question_text"],
                    "topic": "median"})
            history["median"].append({
                    "text": response["question_text"],
                    "topic": "median"})
        case "mode":
            response = LLM_mode_generation.generate_mode_question(recent_global, recent_topic,
                difficulty=difficulty, grade=grade)
            history["global"].append({
                    "text": response["question_text"],
                    "topic": "mode"})
            history["mode"].append({
                    "text": response["question_text"],
                    "topic": "mode"})
        case "probability":
            response = LLM_probability_generation.generate_probability_question(recent_global, recent_topic,
                difficulty=difficulty, grade=grade)
            history["global"].append({
                    "text": response["question_text"],
                    "topic": "probability"})
            history["probability"].append({
                    "text": response["question_text"],
                    "topic": "probability"})
        case "angle_relationships":
            response = LLM_angle_relationship_generation.generate_angle_relationship_question(recent_global, recent_topic,
                difficulty=difficulty, grade=grade)
            history["global"].append({
                    "text": response["question_text"],
                    "topic": "angle_relationships"})
            history["angle_relationships"].append({
                    "text": response["question_text"],
                    "topic": "angle_relationships"})
    return response

def LLM_single_prompt_topic_and_difficulty_decider(user_id, grade):
    accuracy_response = get_user_performance(user_id)
    
    json_response = accuracy_response.data or []
    
    history = get_user_history(user_id)
    recent_global = list(history["global"])[-10:]
    
    prompt = f"""
        You are a function that returns ONLY valid JSON.

        DO NOT include explanations, reasoning, code, markdown, symbols, or extra text.

        INPUT:
        Student Performance = {json_response}
        Recent Question History = {recent_global}
        Student Grade Level = {grade}

        TASK:
        Select a math topic and difficulty level.

        TOPICS:
        geometry, algebra, expressions, ordering, rationals, mean, median, mode, probability, angle_relationships

        DIFFICULTY LEVELS:
        easy, medium, hard

        TOPIC SELECTION RULES (STRICT):
        - DO NOT select a topic that appears in the last 3 questions
        - If a topic appears 2+ times in recent history, it MUST NOT be selected
        - If a topic has been answered incorrectly 3+ times consecutively, DO NOT select it for the next 5 questions
        - Topics with NULL attempted_questions MUST be prioritized (unless restricted above)
        - Over any 5 consecutive questions, at least 3 different topics must appear
        - If multiple valid topics exist, randomly select among them
        - If no valid topics remain, select the least recently used topic

        PERFORMANCE RULES:
        - Use ONLY provided data
        - If correct_questions OR attempted_questions is 0 or null → accuracy = 0

        DIFFICULTY RULES:
        - accuracy < 40% → easy
        - 40%–70% → medium
        - > 70% → hard

        GRADE RULES:
        - Grades 1–4 → mostly easy
        - Grades 5–6 → easy/medium mix
        - Grades 7+ → balanced mix of all difficulties

        OUTPUT FORMAT (STRICT):
        {{
            "topic": "one_of_the_topics",
            "difficulty": "easy_or_medium_or_hard"
        }}
        """
    
    for attempt in range(3):
        response = generate(model="llama3.1:8b", prompt=prompt,
            options={"temperature": 1.1, "top_p": 0.95, "top_k": 100})
    
        raw = extract_json(response.response)
        if not raw:
            print(f"[Attempt {attempt+1}] No JSON found")
            print(response.response)
            continue
    
        try:
            topic_data = json.loads(raw)
        except Exception as e:
                print(f"[Attempt {attempt+1}] JSON parse failed:", e)
                print(response.response)
                continue
    
        # Validate required keys
        required_keys = ["topic", "difficulty"]
        if not all(k in topic_data for k in required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", topic_data)
            continue
        # If we reach here → SUCCESS
        break

    if (topic_data):
        topic = topic_data["topic"]
        difficulty = topic_data["difficulty"]
    else: #backup if generation failed. 
        print("LLM selection generation failed, fallback to randomized selection")
        topic,difficulty = randomize_selection(accuracy_response)
    
    # topic = "probability" #TESTING
    question = question_generation(topic, difficulty, user_id, grade)
    print(question)

    if (add_question_to_supabase(question, difficulty)):
        print("Question added to supabase successfully")

    return question




def randomize_selection(accuracy_response):
    num = random.randint(0, 9)
    match num:
        case 0:
            topic = "ordering"
        case 1:
            topic = "rationals"
        case 2:
            topic = "expressions"
        case 3:
            topic = "algebra"
        case 4:
            topic = "geometry"
        case 5:
            topic = "angle_relationships"
        case 6:
            topic = "mean"
        case 7:
            topic = "median"
        case 8: 
            topic = "mode"
        case 9:
            topic = "probability"
        
    #get accuracy from supabase, select difficulty level accordingly 
    for row in accuracy_response.data or []:
        if row.get("math_topics", {}).get("topic_name") == topic:
            correct = row.get("correct_questions") or 0
            attempted = row.get("attempted_questions") or 0
            break 

    if attempted == 0:
        accuracy = 0
    else:
        accuracy = correct / attempted

    if accuracy < 0.4:
        difficulty = "easy"
    elif accuracy < 0.7:
        difficulty = "medium"
    else:
        difficulty = "hard"
    
    return topic, difficulty


#select from 11 math topics
#POSSIBLY can have LLM select topic first given things like stress/accuracy

#TO-DO: Implement LLM-based topic selection, provide (optional) accuracy/stress values from frontend. 
# def randomize_question():
#     num = random.randint(0, 9) #get int from 0-9 inclusive
#     print("num", num)
#     match num:
#         case 0:
#             # Ordering, need to call generate question 
#             response = LLM_ordering_generation.generate_ordering_question(history["global"], history["ordering"])
#             history["global"].append({
#                     "text": response["question_text"],
#                     "topic": "ordering"})
#             history["ordering"].append({
#                     "text": response["question_text"],
#                     "topic": "ordering"}) 

#         case 1:
#             # Rationals
#             response = LLM_rationals_generation.generate_rational_question(history["global"], history["rationals"])
#             history["global"].append({
#                     "text": response["question_text"],
#                     "topic": "rationals"})
#             history["rationals"].append({
#                     "text": response["question_text"],
#                     "topic": "rationals"})
#         case 2: 
#             #expressions
#             response = LLM_expressions_generation.generate_expression_question(history["global"], history["expressions"])
#             history["global"].append({
#                     "text": response["question_text"],
#                     "topic": "expressions"})
#             history["expressions"].append({
#                     "text": response["question_text"],
#                     "topic": "expressions"})
#         case 3: 
#             #algebra
#             response = LLM_algebra_generation.generate_algebra_question(history["global"], history["algebra"])
#             history["global"].append({
#                     "text": response["question_text"],
#                     "topic": "algebra"})
#             history["algebra"].append({
#                     "text": response["question_text"],
#                     "topic": "algebra"})
#         case 4: 
#             #geometry
#             response = LLM_geometry_generation.generate_geometry_question(history["global"], history["geometry"])
#             history["global"].append({
#                     "text": response["question_text"],
#                     "topic": "geometry"})
#             history["geometry"].append({
#                     "text": response["question_text"],
#                     "topic": "geometry"})
#         case 5:
#             #angle relationship
#             response = LLM_angle_relationship_generation.generate_angle_relationship_question(history["global"], history["angle_relationships"])
#             history["global"].append({
#                     "text": response["question_text"],
#                     "topic": "angle_relationships"})
#             history["angle_relationships"].append({
#                     "text": response["question_text"],
#                     "topic": "angle_relationships"})
#         case 6:
#             #mean
#             response = LLM_mean_generation.generate_mean_question(history["global"], history["mean"])
#             history["global"].append({
#                     "text": response["question_text"],
#                     "topic": "mean"})
#             history["mean"].append({
#                     "text": response["question_text"],
#                     "topic": "mean"})
#         case 7: 
#             #median
#             response = LLM_median_generation.generate_median_question(history["global"], history["median"])
#             history["global"].append({
#                     "text": response["question_text"],
#                     "topic": "median"})
#             history["median"].append({
#                     "text": response["question_text"],
#                     "topic": "median"})
#         case 8:
#             #mode
#             response = LLM_mode_generation.generate_mode_question(history["global"], history["mode"])
#             history["global"].append({
#                     "text": response["question_text"],
#                     "topic": "mode"})
#             history["mode"].append({
#                     "text": response["question_text"],
#                     "topic": "mode"})
#         case 9:
#             #probability
#             response = LLM_probability_generation.generate_probability_question(history["global"], history["probability"])
#             history["global"].append({
#                     "text": response["question_text"],
#                     "topic": "probability"})
#             history["probability"].append({
#                     "text": response["question_text"],
#                     "topic": "probability"})

#     print(response)
#     return response




app= Flask(__name__)
CORS(app)
@app.route("/")
def display_question():
    user_id = request.args.get("user_id")

    if not user_id:
        return jsonify({"error": "Missing user_id"}), 400

    response = LLM_topic_decider(user_id)
    return jsonify(response)

#New display_question function, request id from frontend then calls LLM_topic_decider