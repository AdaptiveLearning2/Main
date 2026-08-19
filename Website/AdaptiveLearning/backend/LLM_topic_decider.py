#Ideally - this will be called from LLMTest2 to randomly select a topic. Then can call methods from other files for specific question generation.
import os
from flask import Flask, jsonify, request
from flask_cors import CORS #pip install flask-cors
from supabase import create_client, Client #pip install supabase
from dotenv import load_dotenv   #pip install dotenv
from ollama import generate
import json
import random
from statistics import fmean
import signal_fusion
import grade_levels
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

ALL_TOPICS = [
    "geometry", "algebra", "expressions", "ordering", "rationals",
    "mean", "median", "mode", "probability", "angle_relationships",
]

# Code-enforced version of the same rule both topic prompts below state in
# English ("Grades 1-3 should primarily see ordering, geometry, and
# expressions... Algebra and probability should only appear after grade 6").
# An 8B model does not reliably follow that -- the same reason difficulty is
# also clamped deterministically elsewhere in this file (see the EEG-bias
# comment in LLM_single_prompt_topic_and_difficulty_decider) -- and
# randomize_selection()'s fallback path used to pick uniformly across all 10
# topics with no grade awareness at all. Keyed on the raw grade string, not
# _grade_band()-style bands, because the rule itself draws its line between
# grade 5 and grade 6, finer than any of the generation files' four bands.
def _allowed_topics(grade):
    # Read numerically via grade_levels rather than by matching the exact
    # strings the dropdown emits. `profiles.grade_level` is free text, so
    # "Grade 1" used to miss every branch here and fall through to the
    # fully-permissive one -- a 1st grader eligible for algebra, which is
    # the single thing this function exists to prevent. An unreadable grade
    # is now treated as the youngest rather than the oldest.
    number = grade_levels.grade_number(grade)
    if number is None or number <= 3:
        return ["ordering", "geometry", "expressions"]
    if number <= 5:
        return [t for t in ALL_TOPICS if t not in ("algebra", "probability")]
    return list(ALL_TOPICS)  # 6th grade and up


def _safe_topic(topic, grade):
    """topic if the student's grade may see it, otherwise a random topic
    from what it may see. Applied to both the LLM's own selection (which the
    prompt asks for but doesn't enforce) and to randomize_selection()'s
    fallback, so this is the one place a topic is checked before a question
    ever generates."""
    allowed = _allowed_topics(grade)
    return topic if topic in allowed else random.choice(allowed)


#Possibly worthwhile to store history in supabase.
#Possibly reset history per session.

def get_user_performance(user_id):
    # Fetched fresh every call (not cached) so this reflects the student's
    # up-to-date accuracy, including anything they've answered so far in
    # their current session -- a stale cache here would defeat the point of
    # session-aware personalization.
    return supabase.table("user_math_performance") \
        .select("correct_questions,attempted_questions, math_topics(topic_name)") \
        .eq("user_id", user_id) \
        .execute()


# How many of the session's most recent answers/EEG samples to look at when
# gauging how the student is doing RIGHT NOW, as opposed to their all-time
# per-topic accuracy above.
SESSION_PERFORMANCE_WINDOW = 10
EEG_BIAS_WINDOW = 5
# The focus/calm/confidence thresholds live in `signal_fusion`, which is the
# only thing that reads them. A second copy here was inert -- the same
# two-implementations-one-dead shape this change deleted from the sidecar.

DIFFS = ["easy", "medium", "hard"]

def _shift_difficulty(current, bias):
    if current not in DIFFS:
        current = "medium"
    idx = max(0, min(len(DIFFS) - 1, DIFFS.index(current) + (bias or 0)))
    return DIFFS[idx]


def get_session_performance(session_id, limit=SESSION_PERFORMANCE_WINDOW):
    """Recent in-session accuracy -- how the student is doing in THIS
    session specifically, separate from their all-time per-topic accuracy."""
    if not session_id:
        return None
    try:
        rows = (
            supabase.table("session_answers")
            .select("correct")
            .eq("session_id", session_id)
            .order("answered_at", desc=True)
            .limit(limit)
            .execute()
        ).data or []
        if not rows:
            return None
        correct = sum(1 for r in rows if r.get("correct"))
        return {"answered": len(rows), "correct": correct, "accuracy": round(correct / len(rows), 3)}
    except Exception as e:
        print(f"[session_performance] {e}")
        return None


def _consent_flags(user_id):
    """Which channels the student permits, so an absence can name its cause.

    Read here rather than through main's `_consent()` because main imports this
    module; the reverse import would be a cycle. Fails **closed** in the same
    direction as that helper -- on a read error every channel reads as revoked,
    so a database problem suppresses signals rather than acting on ones the
    student may have refused.
    """
    if not user_id:
        return {"eeg": False, "heart": [], "face": False}
    try:
        rows = (
            supabase.table("signal_consent")
            .select("eeg_enabled, headband_optical_enabled, camera_enabled")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        ).data or []
        if not rows:
            # No row means the same as a row of falses. Nothing is recorded for
            # a student nobody has configured.
            return {"eeg": False, "heart": [], "face": False}
        r = rows[0]
        # One flag per *sensor*, and the heart channel can arrive from either --
        # so this returns the permitted **sources**, not a boolean. ORing the two
        # flags and then never looking at `source` again would let a student who
        # allowed the headband and declined the camera have rppg-sourced rows
        # acted on. Latent today (nothing writes rppg) and therefore exactly the
        # kind of thing that gets missed later.
        heart_sources = []
        if r.get("headband_optical_enabled"):
            heart_sources += ["muse_optics", "muse_ppg"]
        if r.get("camera_enabled"):
            heart_sources += ["rppg"]
        return {
            "eeg":   bool(r.get("eeg_enabled")),
            "heart": heart_sources,
            "face":  bool(r.get("camera_enabled")),
        }
    except Exception as e:
        print(f"[signal_consent] {e}")
        return {"eeg": False, "heart": [], "face": False}


def _latest(table, columns, session_id, limit=1, sources=None):
    """This session's most recent row(s) from a signals table, newest first.

    `limit` defaults to 1 because only the newest row is ever read for the heart
    and facial channels. `sources` restricts to the sensors the student
    permitted, applied in the query rather than after it -- an unfetched row
    cannot be acted on by a later change.
    """
    try:
        q = (
            supabase.table(table)
            .select(columns)
            .eq("session_id", session_id)
        )
        if sources is not None:
            q = q.in_("source", sources)
        return (q.order("ts", desc=True).limit(limit).execute()).data or []
    except Exception as e:
        print(f"[{table}] {e}")
        return []


def get_session_signal_state(session_id, user_id=None):
    """This session's fused EEG + heart + facial state.

    Reads the database rather than calling the EEGResearch sidecar, so it works
    when that service is down and reflects a short rolling window instead of one
    instantaneous reading. The window is also the damping: averaging the last
    few rows, once per question, is what stops a 4 Hz label from flapping, which
    is why there is no cooldown here.

    Returns a `FusedState`, or None when there is no session to read. The
    asymmetric rule that combines the channels lives in `signal_fusion` as pure
    functions, so it can be tested without a database or a model.
    """
    if not session_id:
        return None

    consent = _consent_flags(user_id)

    eeg_rows = _latest("cognitive_signals", "focus, stress, engagement",
                       session_id, EEG_BIAS_WINDOW) if consent["eeg"] else []
    focus_vals      = [r["focus"]      for r in eeg_rows if r.get("focus")      is not None]
    stress_vals     = [r["stress"]     for r in eeg_rows if r.get("stress")     is not None]
    engagement_vals = [r["engagement"] for r in eeg_rows if r.get("engagement") is not None]

    focus      = fmean(focus_vals)      if focus_vals      else None
    # `stress` in cognitive_signals is `1.0 - calm`, written by eeg_client. It
    # is the calm score inverted and renamed, not a second measurement -- unlike
    # heart_signals.stress_score, which is derived against the session's own
    # baseline. Two columns, two meanings, and only one of them measures stress.
    calm       = (1.0 - fmean(stress_vals)) if stress_vals else None
    confidence = fmean(engagement_vals) if engagement_vals else None

    eeg = signal_fusion.eeg_channel(focus, calm, confidence,
                                    revoked=not consent["eeg"])

    # Scoped to the sources the student actually permitted, in the query. A row
    # from a declined sensor is never fetched, so no later edit can act on one.
    heart_rows = _latest("heart_signals", "stress_category, trusted, source",
                         session_id, sources=consent["heart"]) if consent["heart"] else []
    newest_heart = heart_rows[0] if heart_rows else {}
    heart = signal_fusion.heart_channel(
        newest_heart.get("stress_category"),
        newest_heart.get("trusted"),
        newest_heart.get("source"),
        revoked=not consent["heart"],
    )

    # Selected by explicit name rather than `*`, which is what keeps the
    # confidence this gate reads unambiguous. See `signal_fusion.face_channel`.
    face_rows = _latest("face_signals", "emotion, emotion_confidence, emotion_trusted",
                        session_id) if consent["face"] else []
    newest_face = face_rows[0] if face_rows else {}
    face = signal_fusion.face_channel(
        newest_face.get("emotion"),
        newest_face.get("emotion_confidence"),
        newest_face.get("emotion_trusted"),
        revoked=not consent["face"],
    )

    return signal_fusion.fuse(
        eeg, heart, face,
        focus=round(focus, 3) if focus is not None else None,
        calm=round(calm, 3) if calm is not None else None,
        confidence=round(confidence, 3) if confidence is not None else None,
    )


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
    """Store the question and return **its id**, or None if it could not be stored.

    Returned rather than a bool, and the duplicate branch returns the existing
    id rather than False, because the id is what an answer refers to. Without
    one the adaptive page had nothing to put in `session_answers.question_id`,
    which is why it never recorded an answer at all: every question a student
    answered there was counted in that browser's localStorage and nowhere else,
    so `user_stats`, `session_answers` and every report downstream of them read
    zero however long the child practised.

    A duplicate is the ordinary case rather than an error -- the generator
    reproduces a question sooner or later -- and answering one is exactly as
    real as answering a novel one.
    """
    # Let the database check for the duplicate instead of pulling every row in
    # the (unboundedly growing) questions table into Python on every single
    # generated question.
    existing = supabase.table("questions") \
        .select("id") \
        .eq("question_text", question["question_text"]) \
        .limit(1) \
        .execute()

    if existing.data:
        return existing.data[0]["id"]

    response = supabase.table("questions").insert({
        "subject" : question["question_topic"],
        "difficulty": difficulty,
        "question_text": question["question_text"],
        "options" : question["answer_options"],
        "correct_answer": question["correct_answer"],
        "created_at": str(datetime.now())
    }).execute()

    if response.data:
        return response.data[0]["id"]
    else:
        print("Supabase insert error:", getattr(response, "error", None))
        return None


def _attach_stored_id(question, difficulty):
    """Store the question and put its id on it, in place. Returns the question.

    The id is what an answer refers to. Without it the page has nothing to send
    to `/api/sessions/{id}/answer`, so nothing generated on the adaptive path is
    ever recorded -- which is the bug this line was added to fix.

    Both entry points here need it, and each had its own copy of these five
    lines. That is precisely the shape that lets one path keep the id while the
    other quietly stops setting it, with no error anywhere and the same symptom
    as before: a student practising and the database reading zero.
    """
    question["id"] = add_question_to_supabase(question, difficulty)
    if question["id"]:
        print("Question stored, id " + str(question["id"]))
    return question


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
        topic_future = executor.submit(generate, model="llama3.2:3b", prompt=topic_prompt, options={"temperature": 0.7, "top_p": 0.95, "top_k": 100})
        difficulty_future = executor.submit(generate, model="llama3.2:3b", prompt=difficulty_prompt, options={"temperature": 0.7, "top_p": 0.95, "top_k": 100})

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



    topic_data = None
    difficulty_data = None
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
                topic_data = None
                difficulty_data = None
                continue

        # Validate required keys
        topic_required_keys = ["topic", "selection_method"]
        difficulty_required_keys = ["difficulty"]
        if not all(k in topic_data for k in topic_required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", topic_data)
            topic_data = None
            continue

        if not all(k in difficulty_data for k in difficulty_required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", difficulty_data)
            difficulty_data = None
            continue

        # If we reach here → SUCCESS
        break

    if topic_data and difficulty_data:
        topic = _safe_topic(topic_data["topic"], grade)
        difficulty = difficulty_data["difficulty"]

    else:
        print("LLM selection generation failed, fallback to randomized selection")
        topic,difficulty = randomize_selection(accuracy_response, grade)
    
    print(f"Selected topic: {topic} (selection method: {topic_data.get('selection_method', 'N/A')}) at difficulty: {difficulty}")
    question = question_generation(topic, difficulty, user_id, grade)
    print(question)


    _attach_stored_id(question, difficulty)


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

def LLM_single_prompt_topic_and_difficulty_decider(user_id, grade, session_id=None, manual_bias=0):
    accuracy_response = get_user_performance(user_id)

    json_response = accuracy_response.data or []

    history = get_user_history(user_id)
    recent_global = list(history["global"])[-10:]

    # Session-scoped signals: how the student is doing and feeling RIGHT NOW
    # in their current session, as opposed to their all-time accuracy above.
    # Both read straight from the database (cognitive_signals / heart_signals /
    # face_signals / session_answers) rather than a live sidecar call, so this
    # works even if the EEG service is unreachable.
    session_perf = get_session_performance(session_id)
    signal_state = get_session_signal_state(session_id, user_id)
    eeg_label    = signal_state.label if signal_state else "no_eeg"

    prompt = f"""
        You are a function that returns ONLY valid JSON.

        DO NOT include explanations, reasoning, code, markdown, symbols, or extra text.

        INPUT:
        Student Performance (all-time, per topic) = {json_response}
        Recent Question History = {recent_global}
        Student Grade Level = {grade}
        This Session's Recent Accuracy = {session_perf if session_perf else "no answers yet this session"}
        Student's Current Cognitive State (from sensors) = {eeg_label}

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
        - This Session's Recent Accuracy reflects how the student is doing RIGHT NOW and should be
          weighted more heavily than all-time accuracy when the two disagree

        DIFFICULTY RULES:
        - accuracy < 40% → easy
        - 40%–70% → medium
        - > 70% → hard
        - If Student's Current Cognitive State is "stressed", prefer easier difficulty regardless of accuracy
        - If Student's Current Cognitive State is "focused" and accuracy supports it, prefer harder difficulty
        - If Student's Current Cognitive State is "no_eeg" or "insufficient_signal", ignore it and use accuracy alone

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

    topic_data = None
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
            topic_data = None
            continue
        # If we reach here → SUCCESS
        break

    if topic_data:
        topic = _safe_topic(topic_data["topic"], grade)
        difficulty = topic_data["difficulty"]
    else: #backup if generation failed.
        print("LLM selection generation failed, fallback to randomized selection")
        topic,difficulty = randomize_selection(accuracy_response, grade)

    # EEG state and the manual Easier/Auto/Harder control were both given to
    # the LLM above as context for topic/difficulty selection, but an 8B model
    # doesn't reliably follow soft prompt instructions (verified: it has
    # returned "medium" for a clearly stressed student despite being told to
    # prefer easier). Difficulty needs to be a dependable safety behavior, not
    # a probabilistic one, so apply both as a deterministic shift on top of
    # whatever the LLM picked -- same rules as the old live-sidecar bias:
    # stressed always eases off, focused only pushes harder when the student
    # left the control on Auto, and this never costs a second LLM call.
    #
    # `eeg_label` is now the *fused* label across every consented channel, not
    # EEG alone -- see signal_fusion for the asymmetric rule behind it. The
    # asymmetry below is the one that was already here and it is preserved
    # exactly: easing off overrides a manual setting, pushing harder defers to
    # it. Fusion widens what can trigger the ease-off; it does not loosen this.
    effective_bias = manual_bias
    if eeg_label == "stressed":
        effective_bias = -1
    elif eeg_label == "focused" and manual_bias == 0:
        effective_bias = 1
    if effective_bias:
        difficulty = _shift_difficulty(difficulty, effective_bias)

    question = question_generation(topic, difficulty, user_id, grade)
    print(question)

    _attach_stored_id(question, difficulty)

    # Metadata for the frontend's "EEG eased/raised difficulty" badge -- reuses
    # the same session-scoped EEG read above rather than a second lookup.
    question["eeg_label"]    = eeg_label
    question["eeg_adjusted"] = bool(signal_state and signal_state.adjusted)
    # Which channel decided, and why. Names the source on an override
    # ("heart elevated (muse_optics) overriding eeg-neutral") so a reading that
    # contradicts the headband can be explained rather than just observed.
    #
    # **Diagnostic, and not for the student.** These carry raw internals
    # ("eeg confidence 0.31 below 0.45") and this payload goes to the student's
    # browser. Nothing renders them today -- checked: `Adaptive.jsx:556` uses
    # only `eeg_label` and `eeg_adjusted` for the eased/raised badge. Keep it
    # that way; a teacher-facing surface is where these belong, and a child
    # reading their own confidence scores is neither useful nor kind.
    question["signal_reason"]   = signal_state.reason if signal_state else "no session"
    question["signal_channels"] = signal_state.channels if signal_state else {}
    question["difficulty"]   = difficulty

    return question




def randomize_selection(accuracy_response, grade):
    # Was a uniform pick across all 10 topics with no grade awareness at all
    # -- this is the fallback path for a failed LLM call, so it fires often
    # enough to matter, and used to be the most direct way a 1st grader
    # landed on "algebra". _allowed_topics() is the single source of truth
    # for the grade rule; see its docstring.
    topic = random.choice(_allowed_topics(grade))

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