import os
from flask import Flask, jsonify, request
from flask_cors import CORS #pip install flask-cors
from supabase import create_client, Client #pip install supabase
from dotenv import load_dotenv   #pip install dotenv
import llm_client
import json
import random
from statistics import fmean
import signal_fusion
import grade_levels
from collections import deque

from supabase_auth import datetime
import LLM_algebra_generation, LLM_ordering_generation, LLM_rationals_generation, LLM_mean_generation, LLM_median_generation
import LLM_mode_generation, LLM_probability_generation, LLM_geometry_generation, LLM_angle_relationship_generation, LLM_expressions_generation
# python -m flask --app LLM_topic_decider run

load_dotenv()
SUPABASE_URL     = os.getenv("SUPABASE_URL")
SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase = create_client(SUPABASE_URL, SERVICE_ROLE_KEY)

ALL_TOPICS = [
    "geometry", "algebra", "expressions", "ordering", "rationals",
    "mean", "median", "mode", "probability", "angle_relationships",
]

# Enforces in code the same grade rule the prompts below only state in
# English ("Grades 1-3 should primarily see ordering, geometry, and
# expressions... Algebra and probability should only appear after grade 6"),
# because an 8B model does not reliably follow prose instructions. Keyed on
# the raw grade string rather than a _grade_band()-style band, since the rule
# splits between grade 5 and grade 6 -- finer than any four-band split.
def _allowed_topics(grade):
    # Reads the grade number via grade_levels instead of matching dropdown
    # strings exactly, since profiles.grade_level is free text. An unreadable
    # grade is treated as the youngest, so it can't fall through to the
    # fully-permissive branch.
    number = grade_levels.grade_number(grade)
    if number is None or number <= 3:
        return ["ordering", "geometry", "expressions"]
    if number <= 5:
        return [t for t in ALL_TOPICS if t not in ("algebra", "probability")]
    return list(ALL_TOPICS)  # 6th grade and up


def _safe_topic(topic, grade):
    """Returns topic if the student's grade may see it, otherwise a random
    allowed topic. Used both on the LLM's own pick and on the randomized
    fallback, so every topic is checked here before a question generates."""
    allowed = _allowed_topics(grade)
    return topic if topic in allowed else random.choice(allowed)


def get_user_performance(user_id):
    # Fetched fresh every call, not cached, so it reflects answers from the
    # student's current session too.
    return supabase.table("user_math_performance") \
        .select("correct_questions,attempted_questions, math_topics(topic_name)") \
        .eq("user_id", user_id) \
        .execute()


# How many recent in-session answers/EEG samples to look at, versus the
# all-time per-topic accuracy above.
SESSION_PERFORMANCE_WINDOW = 10
EEG_BIAS_WINDOW = 5
# The focus/calm/confidence thresholds live only in `signal_fusion`; don't
# duplicate them here.

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
    """Which signal channels the student permits.

    Reads the table directly instead of main's `_consent()` because main
    imports this module, so the reverse import would be circular. Fails
    closed: a read error reports every channel as revoked, so a database
    problem never records a signal the student refused.
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
        # Heart rate can come from either sensor, so this returns the permitted
        # sources, not a plain bool. Collapsing both flags into one boolean
        # would let a student who declined the camera have rppg-sourced rows
        # acted on anyway.
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

    `sources` restricts to sensors the student permitted, filtered in the
    query itself so a declined sensor's rows are never fetched at all.
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

    Reads the database instead of calling the sidecar, so it still works if
    that service is down. Averaging the last few rows also damps a 4 Hz
    signal so it doesn't flap between labels question to question.

    Returns a `FusedState`, or None with no session to read. The rule that
    combines channels lives in `signal_fusion` as plain functions, testable
    without a database or a model.
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
    # cognitive_signals.stress is 1.0 - calm, so this just inverts it back.
    # It is not an independent measurement, unlike heart_signals.stress_score.
    calm       = (1.0 - fmean(stress_vals)) if stress_vals else None
    confidence = fmean(engagement_vals) if engagement_vals else None

    eeg = signal_fusion.eeg_channel(focus, calm, confidence,
                                    revoked=not consent["eeg"])

    # Scoped to permitted sources in the query, so a declined sensor's rows
    # are never fetched.
    heart_rows = _latest("heart_signals", "stress_category, trusted, source",
                         session_id, sources=consent["heart"]) if consent["heart"] else []
    newest_heart = heart_rows[0] if heart_rows else {}
    heart = signal_fusion.heart_channel(
        newest_heart.get("stress_category"),
        newest_heart.get("trusted"),
        newest_heart.get("source"),
        revoked=not consent["heart"],
    )

    # Named columns, not `*`, so the confidence value this gate reads stays
    # unambiguous. See `signal_fusion.face_channel`.
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


# 40 questions globally, 10 per topic
user_histories = {}

def get_user_history(user_id):
    if user_id not in user_histories:
        user_histories[user_id] = {
            "global": deque(maxlen=40), # last 40 questions regardless of topic, used to avoid repeats
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
    """Store the question and return its id, or None if it could not be stored.

    Returns the id, not a bool, and a duplicate returns the existing row's id
    rather than False: the id is what `session_answers.question_id` refers
    to, and a duplicate is the ordinary case, not an error, since the
    generator reproduces a question sooner or later.
    """
    # Let the database find the duplicate instead of pulling the whole
    # questions table into Python on every generated question.
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
    """Store the question and set its id on it, in place. Returns the question.

    The id is what the page sends to `/api/sessions/{id}/answer`, so a
    question with no id never gets an answer recorded. Shared by both entry
    points below so the id-setting logic exists in one place, not two.
    """
    question["id"] = add_question_to_supabase(question, difficulty)
    if question["id"]:
        print("Question stored, id " + str(question["id"]))
    return question


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

    # How the student is doing right now in this session, as opposed to
    # all-time accuracy. Reads straight from the database, not a live sidecar
    # call, so this still works if the EEG service is unreachable.
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
        response_text = llm_client.generate_text(prompt)

        raw = extract_json(response_text)
        if not raw:
            print(f"[Attempt {attempt+1}] No JSON found")
            print(response_text)
            continue

        try:
            topic_data = json.loads(raw)
        except Exception as e:
                print(f"[Attempt {attempt+1}] JSON parse failed:", e)
                print(response_text)
                continue

        required_keys = ["topic", "difficulty"]
        if not all(k in topic_data for k in required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", topic_data)
            topic_data = None
            continue
        break

    if topic_data:
        topic = _safe_topic(topic_data["topic"], grade)
        difficulty = topic_data["difficulty"]
    else: #backup if generation failed.
        print("LLM selection generation failed, fallback to randomized selection")
        topic,difficulty = randomize_selection(accuracy_response, grade)

    # The LLM saw cognitive state and the manual control as context, but an 8B
    # model doesn't reliably follow that instruction (it has answered "medium"
    # for a clearly stressed student). So apply both again as a deterministic
    # shift here: stressed always eases off, focused only pushes harder if the
    # student left the control on Auto. Easing off overrides the manual
    # setting; pushing harder defers to it -- same asymmetry as signal_fusion.
    # `eeg_label` is the fused label across every consented channel, not EEG
    # alone.
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

    # Metadata for the frontend's "EEG eased/raised difficulty" badge, reusing
    # the session-scoped EEG read above.
    question["eeg_label"]    = eeg_label
    question["eeg_adjusted"] = bool(signal_state and signal_state.adjusted)
    # Which channel decided, and why (e.g. "heart elevated overriding
    # eeg-neutral"). Diagnostic only, and not meant for the student: this
    # carries raw internals like confidence numbers. Adaptive.jsx only uses
    # eeg_label and eeg_adjusted for the badge -- keep it that way. A child
    # reading their own confidence score isn't useful or kind.
    question["signal_reason"]   = signal_state.reason if signal_state else "no session"
    question["signal_channels"] = signal_state.channels if signal_state else {}
    question["difficulty"]   = difficulty

    return question




def randomize_selection(accuracy_response, grade):
    # Fallback for a failed LLM call, so it fires often enough to matter.
    # Draws only from the grade's allowed topics -- see _allowed_topics().
    topic = random.choice(_allowed_topics(grade))

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


app= Flask(__name__)
CORS(app)
@app.route("/")
def display_question():
    user_id = request.args.get("user_id")

    if not user_id:
        return jsonify({"error": "Missing user_id"}), 400

    response = LLM_topic_decider(user_id)
    return jsonify(response)