import os
from flask import Flask, jsonify, request
from flask_cors import CORS
from supabase import create_client, Client
from dotenv import load_dotenv
import llm_client
from llm_json import extract_json
import json
import random
from statistics import fmean
import signal_fusion
import grade_levels
import unicodedata
import datetime as _dt
from collections import deque

from supabase_auth import datetime
import LLM_algebra_generation, LLM_ordering_generation, LLM_rationals_generation, LLM_mean_generation, LLM_median_generation
import LLM_mode_generation, LLM_probability_generation, LLM_geometry_generation, LLM_angle_relationship_generation, LLM_expressions_generation
import LLM_missing_number_generation, LLM_patterns_generation, LLM_graphs_generation
import LLM_shape_fractions_generation
import LLM_quadratics_generation, LLM_functions_generation
import LLM_spread_generation
import LLM_kindergarten_generation
# python -m flask --app LLM_topic_decider run

load_dotenv()
SUPABASE_URL     = os.getenv("SUPABASE_URL")
SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase = create_client(SUPABASE_URL, SERVICE_ROLE_KEY)

ALL_TOPICS = [
    "geometry", "algebra", "expressions", "ordering", "rationals",
    "mean", "median", "mode", "probability", "angle_relationships",
    "missing_number", "patterns", "graphs", "shape_fractions",
    "quadratics", "functions", "spread",
    # Kindergarten; LLM_kindergarten_generation.TOPICS, and a test pins the two.
    "counting", "comparing_numbers", "add_and_subtract", "teen_numbers", "shapes",
]

# The grade at which each topic's core concept is introduced, by CCSS code.
# Enforced in code because the model does not reliably follow prose grade rules.
# Per topic, not grade brackets: a bracket must be remembered for every topic it excludes.
TOPIC_MIN_GRADE = {
    "ordering":            1,   # 1.NBT.3, comparing whole numbers
    "expressions":         1,   # 1.OA, add and subtract within 20
    # 1.G produces nothing a solver can score, so geometry starts at 2.G.2.
    "geometry":            2,   # 2.G.2; per-scenario floor in
                                # LLM_geometry_generation.SCENARIO_MIN_GRADE
    "rationals":           4,   # 4.NF.3, fractions with like denominators
    "mean":                6,   # 6.SP.5c
    "median":              6,   # 6.SP.5c
    "mode":                6,   # 6.SP.5c
    "algebra":             6,   # 6.EE.7, one-variable equations
    "angle_relationships": 7,   # 7.G.5, complementary and supplementary
    "probability":         7,   # 7.SP.5
    "missing_number":      1,   # 1.OA.8, the unknown in an equation
    "patterns":            1,   # 1.NBT.1 counting sequences, 2.NBT.2 skip counting
    "graphs":              1,   # 1.MD.4 read a graph, 2.MD.10 compare bars
    "shape_fractions":     1,   # 1.G.3, reading a fraction off a picture
    # High-school topics: every other topic tops out at grade 8. See `hs_solvers`.
    "quadratics":          9,   # A-REI.4b, solving a quadratic by factoring
    "functions":           9,   # F-IF.2 notation, F-BF.1c composition
    "spread":              9,   # S-ID.2, standard deviation only
    # Kindergarten has its own topics and sees no other (K.CC, K.OA, K.NBT, K.G).
    "counting":            0,
    "comparing_numbers":   0,
    "add_and_subtract":    0,
    "teen_numbers":        0,
    "shapes":              0,
}

# Grade past which a topic stops being worth serving; absent means no ceiling.
# These skills don't scale with bigger numbers, unlike the uncapped topics.
TOPIC_MAX_GRADE = {
    "missing_number":      3,   # 3.OA.4 unknown factor is the last of it
    "patterns":            5,   # 4.OA.5 and 5.OA.3 still generate patterns
    "graphs":              3,   # 3.MD.3 is the last bar-graph standard
    "shape_fractions":     3,   # 3.NF.1; 4.NF.3 is `rationals`
    "counting":            0,
    "comparing_numbers":   0,
    "add_and_subtract":    0,
    "teen_numbers":        0,
    "shapes":              0,
}


def _allowed_topics(grade):
    # profiles.grade_level is free text; an unreadable grade is `DEFAULT_GRADE`, not kindergarten.
    number = grade_levels.served_grade_number(grade)
    return [t for t in ALL_TOPICS
            if TOPIC_MIN_GRADE[t] <= number
            and number <= TOPIC_MAX_GRADE.get(t, number)]


def _safe_topic(topic, grade):
    """`topic` if the student's grade may see it, otherwise a random allowed topic."""
    allowed = _allowed_topics(grade)
    return topic if topic in allowed else random.choice(allowed)


def get_user_performance(user_id):
    # Not cached, so it includes this session's answers.
    return supabase.table("user_math_performance") \
        .select("correct_questions,attempted_questions, math_topics(topic_name)") \
        .eq("user_id", user_id) \
        .execute()


# Recent in-session answers / EEG samples considered.
SESSION_PERFORMANCE_WINDOW = 10
EEG_BIAS_WINDOW = 5
# A reading older than this (s) no longer steers difficulty; equals main._LIVE_WINDOW_SEC, pinned.
SIGNAL_MAX_AGE_SEC = 90
# Focus/calm/confidence thresholds live only in `signal_fusion`.

DIFFS = ["easy", "medium", "hard"]

# A run of correct answers can push difficulty up without a "focused" reading:
# at least MIN_ANSWERS in the window, at or above this accuracy.
PERFORMANCE_PUSH_ACCURACY = 0.7
PERFORMANCE_PUSH_MIN_ANSWERS = 3
# ...and the newest this-many answers must be right; the aggregate can't tell rising from falling.
PERFORMANCE_PUSH_RECENT_CORRECT = 2


def _shift_difficulty(current, bias):
    if current not in DIFFS:
        current = "medium"
    idx = max(0, min(len(DIFFS) - 1, DIFFS.index(current) + (bias or 0)))
    return DIFFS[idx]


def _decide_bias(eeg_label, session_perf, manual_bias=0, increase_withheld=False):
    """The deterministic shift applied on top of the model's difficulty.

    Easing off wins, pushing harder defers: "stressed" always eases; a push needs Auto and
    a "focused" reading or a correct run, and is held by a manual setting, `increase_withheld`
    (the facial veto), or recent misses. Pure, so testable without a model or database.
    """
    if eeg_label == "stressed":
        return -1
    if manual_bias:
        return manual_bias
    if increase_withheld:
        return 0
    # Recent misses veto a push from either source: correctness has no quality gate.
    if _recent_falling(session_perf):
        return 0
    if eeg_label == "focused":
        return 1
    if (session_perf
            and (session_perf.get("answered") or 0) >= PERFORMANCE_PUSH_MIN_ANSWERS
            and (session_perf.get("accuracy") or 0) >= PERFORMANCE_PUSH_ACCURACY
            and _recent_all_correct(session_perf.get("recent"))):
        return 1
    return 0


def _recent_falling(session_perf):
    """Whether a miss is among the newest PERFORMANCE_PUSH_RECENT_CORRECT answers.

    Not `not _recent_all_correct`: no answers yet means no opinion, not "falling".
    """
    recent = (session_perf or {}).get("recent") or []
    return any(not r for r in recent[:PERFORMANCE_PUSH_RECENT_CORRECT])


def _recent_all_correct(recent):
    """Whether the newest PERFORMANCE_PUSH_RECENT_CORRECT answers were right.

    `recent` is newest first; absent fails closed (no push).
    """
    if not recent or len(recent) < PERFORMANCE_PUSH_RECENT_CORRECT:
        return False
    return all(recent[:PERFORMANCE_PUSH_RECENT_CORRECT])


def get_session_performance(session_id, limit=SESSION_PERFORMANCE_WINDOW):
    """Recent accuracy in this session, separate from all-time per-topic accuracy."""
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
        # `recent` (newest first) keeps the order the aggregate throws away.
        return {"answered": len(rows), "correct": correct,
                "accuracy": round(correct / len(rows), 3),
                "recent": [bool(r.get("correct")) for r in rows]}
    except Exception as e:
        print(f"[session_performance] {e}")
        return None


def _consent_flags(user_id):
    """Which signal channels the student permits. Fails closed: a read error revokes all.

    Reads the table directly because main imports this module (circular otherwise).
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
            # No row means the same as a row of falses.
            return {"eeg": False, "heart": [], "face": False}
        r = rows[0]
        # Permitted heart sources, not a bool: a declined camera must exclude rppg rows.
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

    Only rows from the last SIGNAL_MAX_AGE_SEC, so a sensor that stopped reporting stops
    steering. `sources` filters in the query, so a declined sensor's rows are never fetched.
    """
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=SIGNAL_MAX_AGE_SEC)
    try:
        q = (
            supabase.table(table)
            .select(columns)
            .eq("session_id", session_id)
            .gte("ts", cutoff.isoformat())
        )
        if sources is not None:
            q = q.in_("source", sources)
        return (q.order("ts", desc=True).limit(limit).execute()).data or []
    except Exception as e:
        print(f"[{table}] {e}")
        return []


def get_session_signal_state(session_id, user_id=None):
    """This session's fused EEG + heart + facial state, or None with no session.

    Reads the database, not the sidecar, so it works when that is down; averaging
    damps label flapping. The fusion rule lives in `signal_fusion`.
    """
    if not session_id:
        return None

    consent = _consent_flags(user_id)

    eeg_rows = _latest("cognitive_signals", "focus, stress, raw",
                       session_id, EEG_BIAS_WINDOW) if consent["eeg"] else []
    focus_vals  = [r["focus"]  for r in eeg_rows if r.get("focus")  is not None]
    stress_vals = [r["stress"] for r in eeg_rows if r.get("stress") is not None]
    # Signal quality is `raw.confidence` (no column). `raw` is unvalidated client JSON,
    # so only a real number in 0..1 counts (bool is an int to isinstance).
    confidence_vals = [
        r["raw"]["confidence"] for r in eeg_rows
        if isinstance(r.get("raw"), dict)
        and isinstance(r["raw"].get("confidence"), (int, float))
        and not isinstance(r["raw"].get("confidence"), bool)
        and 0.0 <= r["raw"]["confidence"] <= 1.0
    ]

    focus      = fmean(focus_vals)      if focus_vals      else None
    # cognitive_signals.stress is 1.0 - calm; not an independent measurement.
    calm       = (1.0 - fmean(stress_vals)) if stress_vals else None
    confidence = fmean(confidence_vals) if confidence_vals else None

    # Calm source (the stressed line differs by source); a missing key means "sdk".
    # Type-checked before use as a set element: a posted dict or list is unhashable.
    def _calm_source_of(r: dict) -> str | None:
        raw = r.get("raw")
        s = raw.get("calm_source") if isinstance(raw, dict) else None
        if s is None:
            return "sdk"
        return s if isinstance(s, str) else None
    sources = {_calm_source_of(r) for r in eeg_rows if r.get("stress") is not None}
    sources = {s for s in sources if s in signal_fusion.EEG_STRESSED_CALM_MAX_BY_SOURCE}
    if len(sources) > 1:
        # Mixed sources put calm on two scales: withdraw calm only, not focus/confidence.
        calm = None
    calm_source = next(iter(sources)) if len(sources) == 1 else "sdk"

    eeg = signal_fusion.eeg_channel(focus, calm, confidence,
                                    revoked=not consent["eeg"],
                                    calm_source=calm_source)

    heart_rows = _latest("heart_signals", "stress_category, trusted, source",
                         session_id, sources=consent["heart"]) if consent["heart"] else []
    newest_heart = heart_rows[0] if heart_rows else {}
    heart = signal_fusion.heart_channel(
        newest_heart.get("stress_category"),
        newest_heart.get("trusted"),
        newest_heart.get("source"),
        revoked=not consent["heart"],
    )

    # Named columns, so the confidence this gate reads is unambiguous.
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

# Replayed model text is newline-joined into prompts, so a newline in it lands in
# instruction position. Flattened and bounded, not refused: only repeat avoidance is at stake.
_HISTORY_TEXT_MAX = 300   # chars per entry; a guess, not a measurement
# Line breaks beyond `\n` (Zl, Zp) and bidi overrides (Cf), as `validated_grade` refuses.
_LINE_BREAKING = ("Cc", "Cf", "Zl", "Zp")


def _prompt_safe_text(value) -> str:
    """One bounded single line, for a value on its way into a prompt."""
    if not isinstance(value, str):
        return ""
    flattened = "".join(
        " " if unicodedata.category(ch) in _LINE_BREAKING else ch
        for ch in value)
    return " ".join(flattened.split())[:_HISTORY_TEXT_MAX]


def _prompt_safe_history(entries):
    """The repeat-avoidance list, every text flattened and bounded; same shape the generators read."""
    safe = []
    for entry in entries or ():
        text = _prompt_safe_text((entry or {}).get("text"))
        if text:
            safe.append({**entry, "text": text})
    return safe


def get_user_history(user_id):
    if user_id not in user_histories:
        # From ALL_TOPICS: a missing topic key would silently lose repeat avoidance.
        user_histories[user_id] = {
            "global": deque(maxlen=40),
            **{topic: deque(maxlen=10) for topic in ALL_TOPICS},
        }
    return user_histories[user_id]



# Rows the duplicate check reads; a match beyond it is just stored again.
_DEDUPE_CANDIDATES = 50


def _answer_value(answer):
    """An answer as the generator returned it; list answers are stored as JSON text."""
    if isinstance(answer, str) and answer.startswith("["):
        try:
            return json.loads(answer)
        except ValueError:
            pass
    return answer


def add_question_to_supabase(question, difficulty):
    """Store the question and return its id, or None if it could not be stored.

    A duplicate returns the existing row's id (what `session_answers.question_id` needs)
    and replaces the question's `answer_options` in place with the stored row's.
    """
    # Same question = same text, CCSS code (grade-dependent), answer and figure.
    # Options are not compared: serving the stored ones keeps a recorded index valid.
    # List answers and figures are compared in Python; a missed match costs one extra row.
    answer = question["correct_answer"]
    code = question.get("ccss_standard")
    lookup = supabase.table("questions") \
        .select("id, options, correct_answer, figure") \
        .eq("question_text", question["question_text"])
    lookup = lookup.is_("ccss_standard", "null") if code is None \
        else lookup.eq("ccss_standard", code)
    if isinstance(answer, str):
        lookup = lookup.eq("correct_answer", answer)
    existing = lookup.limit(_DEDUPE_CANDIDATES).execute()

    for row in existing.data or ():
        options = row.get("options")
        if (_answer_value(row.get("correct_answer")) == _answer_value(answer)
                and row.get("figure") == question.get("figure")
                # The page marks an answer by finding it among the options.
                and isinstance(options, list) and answer in options):
            question["answer_options"] = list(options)
            return row["id"]

    response = supabase.table("questions").insert({
        "subject" : question["question_topic"],
        # NULL when there is none; a duplicate is served from this row, figure included.
        "figure": question.get("figure"),
        "ccss_standard": question.get("ccss_standard"),
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

    Without an id, `/api/sessions/{id}/answer` cannot record an answer to it.
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
    # The one dispatch point to every generator: past here `grade` is a canonical label,
    # so no caller text reaches a prompt. See grade_levels.grade_for_prompt.
    grade = grade_levels.grade_for_prompt(grade)
    history = get_user_history(user_id)
    recent_global = _prompt_safe_history(list(history["global"])[-5:])
    recent_topic  = _prompt_safe_history(
        list(history[topic])[-5:] if topic in history else [])
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

        case "missing_number":
            response = LLM_missing_number_generation.generate_missing_number_question(recent_global, recent_topic,
                difficulty=difficulty, grade=grade)
            history["global"].append({
                    "text": response["question_text"],
                    "topic": "missing_number"})
            history["missing_number"].append({
                    "text": response["question_text"],
                    "topic": "missing_number"})

        case "patterns":
            response = LLM_patterns_generation.generate_patterns_question(recent_global, recent_topic,
                difficulty=difficulty, grade=grade)
            history["global"].append({
                    "text": response["question_text"],
                    "topic": "patterns"})
            history["patterns"].append({
                    "text": response["question_text"],
                    "topic": "patterns"})

        case "graphs":
            response = LLM_graphs_generation.generate_graphs_question(recent_global, recent_topic,
                difficulty=difficulty, grade=grade)
            history["global"].append({
                    "text": response["question_text"],
                    "topic": "graphs"})
            history["graphs"].append({
                    "text": response["question_text"],
                    "topic": "graphs"})

        case "shape_fractions":
            response = LLM_shape_fractions_generation.generate_shape_fractions_question(recent_global, recent_topic,
                difficulty=difficulty, grade=grade)
            history["global"].append({
                    "text": response["question_text"],
                    "topic": "shape_fractions"})
            history["shape_fractions"].append({
                    "text": response["question_text"],
                    "topic": "shape_fractions"})

        case "quadratics":
            response = LLM_quadratics_generation.generate_quadratics_question(recent_global, recent_topic,
                difficulty=difficulty, grade=grade)
            history["global"].append({
                    "text": response["question_text"],
                    "topic": "quadratics"})
            history["quadratics"].append({
                    "text": response["question_text"],
                    "topic": "quadratics"})

        case "functions":
            response = LLM_functions_generation.generate_functions_question(recent_global, recent_topic,
                difficulty=difficulty, grade=grade)
            history["global"].append({
                    "text": response["question_text"],
                    "topic": "functions"})
            history["functions"].append({
                    "text": response["question_text"],
                    "topic": "functions"})

        case "spread":
            response = LLM_spread_generation.generate_spread_question(recent_global, recent_topic,
                difficulty=difficulty, grade=grade)
            history["global"].append({
                    "text": response["question_text"],
                    "topic": "spread"})
            history["spread"].append({
                    "text": response["question_text"],
                    "topic": "spread"})

        case "counting" | "comparing_numbers" | "add_and_subtract" | "teen_numbers" | "shapes":
            response = LLM_kindergarten_generation.generate_kindergarten_question(
                recent_global, recent_topic, difficulty=difficulty, grade=grade, topic=topic)
            history["global"].append({
                    "text": response["question_text"],
                    "topic": topic})
            history[topic].append({
                    "text": response["question_text"],
                    "topic": topic})

        case _:
            # Names the unwired topic instead of an UnboundLocalError on `response`.
            raise ValueError(f"no generator wired for topic {topic!r}")
    return response

def LLM_single_prompt_topic_and_difficulty_decider(user_id, grade, session_id=None, manual_bias=0):
    # `grade` is caller text headed for the prompt; canonicalise it as question_generation does.
    grade = grade_levels.grade_for_prompt(grade)
    accuracy_response = get_user_performance(user_id)

    json_response = accuracy_response.data or []

    history = get_user_history(user_id)
    # Flattened even though repr of a list escapes newlines today; one `join` would change that.
    recent_global = _prompt_safe_history(list(history["global"])[-10:])

    # From the database, not the sidecar, so this works when EEG is unreachable.
    session_perf = get_session_performance(session_id)
    signal_state = get_session_signal_state(session_id, user_id)
    eeg_label    = signal_state.label if signal_state else "no_eeg"
    # Only what the grade may see: a pick outside it is replaced at random by `_safe_topic`.
    topic_list   = ", ".join(_allowed_topics(grade))

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
        {topic_list}

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
        - Kindergarten and grades 1–4 → mostly easy
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
        difficulty = str(topic_data["difficulty"]).strip().lower()
        if difficulty not in DIFFS:
            difficulty = _difficulty_from_accuracy(accuracy_response, topic)
    else: #backup if generation failed.
        print("LLM selection generation failed, fallback to randomized selection")
        topic,difficulty = randomize_selection(accuracy_response, grade)

    # Re-applied deterministically because the model doesn't reliably follow them.
    # `eeg_label` is the fused label across every consented channel, not EEG alone.
    effective_bias = _decide_bias(
        eeg_label, session_perf, manual_bias,
        increase_withheld=bool(getattr(signal_state, "increase_withheld", False)))
    if effective_bias:
        difficulty = _shift_difficulty(difficulty, effective_bias)

    question = question_generation(topic, difficulty, user_id, grade)
    print(question)

    _attach_stored_id(question, difficulty)

    # For the frontend's "EEG eased/raised difficulty" badge.
    question["eeg_label"]    = eeg_label
    question["eeg_adjusted"] = bool(signal_state and signal_state.adjusted)
    # Diagnostic only, never shown to the student: carries raw internals like confidence.
    question["signal_reason"]   = signal_state.reason if signal_state else "no session"
    question["signal_channels"] = signal_state.channels if signal_state else {}
    question["difficulty"]   = difficulty

    return question




def randomize_selection(accuracy_response, grade):
    # Fallback for a failed LLM call; draws only from the grade's allowed topics.
    topic = random.choice(_allowed_topics(grade))
    return topic, _difficulty_from_accuracy(accuracy_response, topic)


def _difficulty_from_accuracy(accuracy_response, topic):
    """The prompt's accuracy rule for `topic`; a topic never attempted counts as 0%."""
    correct = attempted = 0
    for row in accuracy_response.data or []:
        if (row.get("math_topics") or {}).get("topic_name") == topic:
            correct = row.get("correct_questions") or 0
            attempted = row.get("attempted_questions") or 0
            break

    accuracy = correct / attempted if attempted else 0
    if accuracy < 0.4:
        return "easy"
    if accuracy < 0.7:
        return "medium"
    return "hard"


app= Flask(__name__)
CORS(app)
@app.route("/")
def display_question():
    user_id = request.args.get("user_id")

    if not user_id:
        return jsonify({"error": "Missing user_id"}), 400

    response = LLM_topic_decider(user_id)
    return jsonify(response)