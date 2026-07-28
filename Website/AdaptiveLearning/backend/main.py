from fastapi import FastAPI, Request, HTTPException, Path, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os, requests, random, string, threading
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from supabase import create_client

import LLM_topic_decider
import eeg_client
import eeg_poller

load_dotenv()

SUPABASE_URL     = os.getenv("SUPABASE_URL")
SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
BACKEND_PORT     = int(os.getenv("BACKEND_PORT", "8000"))

if not SUPABASE_URL or not SERVICE_ROLE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")

supabase = create_client(SUPABASE_URL, SERVICE_ROLE_KEY)

app = FastAPI(title="AdaptiveLearning API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── helpers ──────────────────────────────────────────────────────────────

def get_user(request: Request):
    token = request.headers.get("authorization", "").replace("Bearer ", "").strip()
    if not token:
        raise HTTPException(401, "Missing token")
    resp = requests.get(f"{SUPABASE_URL}/auth/v1/user", headers={
        "Authorization": f"Bearer {token}",
        "apikey": SERVICE_ROLE_KEY
    })
    if resp.status_code != 200:
        raise HTTPException(401, "Invalid token")
    return resp.json()

def rand_code(n=6):
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=n))

def _profile(uid: str) -> dict:
    try:
        p = supabase.table("profiles").select("*").eq("id", uid).single().execute()
        if p.data:
            return p.data
    except Exception:
        pass
    return {"id": uid, "display_name": "Student", "email": "", "role": "student", "grade_level": None}


# ─── biosignal reporting ─────────────────────────────────────────────────

# Cap on rows pulled per signal table for one report. A week of continuous
# recording exceeds this, so the query orders by ts DESC and the cap trims the
# OLDEST samples -- see _weekly_signal_report for why the day buckets are built
# from what actually came back rather than from the requested date range.
_REPORT_ROW_CAP = 5000


def _avg(values):
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 2)


def _utc_now() -> datetime:
    """Timezone-aware UTC.

    datetime.utcnow() is deprecated in 3.12 and returns a *naive* datetime,
    which compares wrongly against the timestamptz columns these queries filter
    on. The rest of this module still uses utcnow() in older endpoints; new
    code should use this instead of spreading the pattern further.
    """
    return datetime.now(timezone.utc)


def _iso_days_ago(days: int = 7) -> str:
    return (_utc_now() - timedelta(days=days)).isoformat()


def _topic_breakdown(student_id: str):
    try:
        rows = supabase.table("user_math_performance") \
            .select("*, math_topics(topic_name)") \
            .eq("user_id", student_id).execute().data or []
    except Exception as e:
        print(f"[topic_breakdown] {e}")
        rows = []
    out = []
    for r in rows:
        attempted = r.get("attempted_questions") or 0
        correct = r.get("correct_questions") or 0
        out.append({
            "topic_id": r.get("topic_id"),
            "topic_name": ((r.get("math_topics") or {}).get("topic_name") or "Unknown"),
            "attempted_questions": attempted,
            "correct_questions": correct,
            "accuracy": round((correct / attempted) * 100) if attempted else 0,
            "stress": r.get("stress"),
            "updated_at": r.get("updated_at"),
        })
    return out


def _signal_summary(student_id: str, days: int = 7) -> dict:
    """Just the headline averages, aggregated in Postgres.

    The full report pulls thousands of raw signal rows to compute a handful of
    numbers, which is fine for one student on a detail page and wasteful on a
    list that loads every visit. This returns the same headline figures without
    transferring any rows -- see the student_signal_summary migration.
    """
    try:
        res = supabase.rpc("student_signal_summary",
                           {"p_student_id": student_id, "p_days": days}).execute()
    except Exception as e:
        print(f"[signal_summary] {e}")
        return _EMPTY_SUMMARY.copy()
    rows = res.data or []
    row = rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else None)
    return _shape_summary(row)


_EMPTY_SUMMARY = {"focus": None, "stress": None, "engagement": None,
                  "face_attention": None, "sessions": 0,
                  "cognitive_samples": 0, "face_samples": 0}


def _shape_summary(row) -> dict:
    if not row:
        return _EMPTY_SUMMARY.copy()
    return {
        "focus": row.get("focus"),
        "stress": row.get("stress"),
        "engagement": row.get("engagement"),
        "face_attention": row.get("face_attention"),
        "sessions": row.get("sessions") or 0,
        # Surfaced rather than dropped: an average of None next to a sample
        # count of 0 means "nothing recorded", while None next to a nonzero
        # count would mean "recorded but unusable". The SQL counts non-NULL
        # measurements specifically so that distinction holds.
        "cognitive_samples": row.get("cognitive_samples") or 0,
        "face_samples": row.get("face_samples") or 0,
    }


def _signal_summaries(student_ids: list[str], days: int = 7) -> dict[str, dict]:
    """Headline averages for many students in one round-trip.

    The single-student RPC removes the row transfer but still costs one
    round-trip per child on a dashboard that loads every visit.
    """
    if not student_ids:
        return {}
    try:
        res = supabase.rpc("student_signal_summary_many",
                           {"p_student_ids": student_ids, "p_days": days}).execute()
    except Exception as e:
        print(f"[signal_summaries] {e}")
        return {}
    rows = res.data or []
    if isinstance(rows, dict):
        rows = [rows]
    return {str(r.get("student_id")): _shape_summary(r) for r in rows if r.get("student_id")}


def _weekly_signal_report(student_id: str, days: int = 7):
    """Aggregate a student's recent EEG and facial signals for reporting.

    Returns averages, highlights and per-day buckets. Callers must have already
    established that the requester may see this student.
    """
    since = _iso_days_ago(days)

    def _fetch(table: str, ts_col: str, limit: int) -> tuple[list, bool]:
        """Rows (newest first) plus whether the server withheld any.

        Truncation is detected from an exact count rather than from
        len(rows) >= limit. PostgREST applies its own db-max-rows ceiling
        (commonly 1000) on top of our .limit(), so a smaller server cap would
        silently trim the result while len(rows) never reached _REPORT_ROW_CAP
        -- leaving truncated=False and the whole guard disabled. Comparing
        against the count the server reports works whichever limit binds.
        """
        try:
            res = supabase.table(table).select("*", count="exact") \
                .eq("user_id", student_id).gte(ts_col, since) \
                .order(ts_col, desc=True).limit(limit).execute()
            rows = res.data or []
            total = getattr(res, "count", None)
            # count=None means the client/server didn't report one; fall back
            # to the length heuristic rather than claiming nothing was cut.
            was_cut = (total > len(rows)) if isinstance(total, int) else len(rows) >= limit
            return rows, was_cut
        except Exception as e:
            print(f"[weekly_report:{table}] {e}")
            return [], False

    cog, cog_cut = _fetch("cognitive_signals", "ts", _REPORT_ROW_CAP)
    face, face_cut = _fetch("face_signals", "ts", _REPORT_ROW_CAP)
    sessions, _ = _fetch("sessions", "started_at", 100)

    # The row cap trims oldest-first, so on a heavy week the earliest days come
    # back empty and would be reported as "no activity" rather than "not
    # retrieved".
    #
    # Tracked per table, because the cap is per table. Taking the oldest
    # timestamp across both and a single OR'd truncation flag got the mixed
    # case wrong: when cognitive was cut and face was not, the older face rows
    # held oldest_ts back, so no days were skipped and the trimmed cognitive
    # days rendered as None -- displayed to parents as "no activity", the exact
    # confusion this guard exists to prevent.
    def _oldest(rows: list, ts_col: str) -> str:
        return min([str(r.get(ts_col, "")) for r in rows if r.get(ts_col)], default="")

    truncated = cog_cut or face_cut
    cog_oldest_day = _oldest(cog, "ts")[:10]
    face_oldest_day = _oldest(face, "ts")[:10]

    latest_cognitive = cog[0] if cog else None
    latest_face = face[0] if face else None

    daily = []
    for i in range(days - 1, -1, -1):
        day = (_utc_now() - timedelta(days=i)).date().isoformat()
        # "We could not retrieve this day", judged per table.
        cog_missing = bool(cog_cut and cog_oldest_day and day < cog_oldest_day)
        face_missing = bool(face_cut and face_oldest_day and day < face_oldest_day)
        if cog_missing and face_missing:
            continue  # nothing retrievable for this day at all
        day_cog = [r for r in cog if str(r.get("ts", ""))[:10] == day]
        day_face = [r for r in face if str(r.get("ts", ""))[:10] == day]
        daily.append({
            "date": day,
            "focus": None if cog_missing else _avg([r.get("focus") for r in day_cog]),
            "stress": None if cog_missing else _avg([r.get("stress") for r in day_cog]),
            "engagement": None if cog_missing else _avg([r.get("engagement") for r in day_cog]),
            "attention": None if face_missing else _avg([r.get("attention") for r in day_face]),
            "sessions": len([r for r in sessions if str(r.get("started_at", ""))[:10] == day]),
            # False means "the cap stopped us fetching this", which a null
            # metric alone cannot distinguish from "nothing was recorded".
            "cognitive_retrieved": not cog_missing,
            "face_retrieved": not face_missing,
        })

    emotion_counts: dict[str, int] = {}
    for r in face:
        if r.get("emotion"):
            emotion_counts[r["emotion"]] = emotion_counts.get(r["emotion"], 0) + 1

    avg_focus = _avg([r.get("focus") for r in cog])
    avg_stress = _avg([r.get("stress") for r in cog])
    avg_attention = _avg([r.get("attention") for r in face])
    highest_stress = max([float(r["stress"]) for r in cog if r.get("stress") is not None], default=None)
    lowest_focus = min([float(r["focus"]) for r in cog if r.get("focus") is not None], default=None)

    # The averages are 0..1 ratios, as stored. Interpolating them straight into
    # a "%" sentence produced "average focus was 0.72%".
    def _as_pct(ratio):
        return round(float(ratio) * 100)

    bits = []
    if avg_focus is not None:
        bits.append(f"average focus was {_as_pct(avg_focus)}%")
    if avg_stress is not None:
        bits.append(f"average stress was {_as_pct(avg_stress)}%")
    if avg_attention is not None:
        bits.append(f"average face attention was {_as_pct(avg_attention)}%")
    summary = ("This week, " + ", ".join(bits) + ".") if bits else \
        "No EEG or facial recognition samples were recorded this week."

    return {
        "student_id": student_id,
        "days": days,
        "since": since,
        "truncated": truncated,
        "sample_counts": {"cognitive": len(cog), "face": len(face), "sessions": len(sessions)},
        "averages": {
            "focus": avg_focus,
            "stress": avg_stress,
            "engagement": _avg([r.get("engagement") for r in cog]),
            "face_attention": avg_attention,
            "identity_confidence": _avg([r.get("identity_confidence") for r in face]),
        },
        "highlights": {
            "highest_stress": round(highest_stress, 2) if highest_stress is not None else None,
            "lowest_focus": round(lowest_focus, 2) if lowest_focus is not None else None,
            "dominant_emotion": max(emotion_counts, key=emotion_counts.get) if emotion_counts else None,
        },
        "latest": {"cognitive": latest_cognitive, "face": latest_face},
        "daily": daily,
        "summary": summary,
    }


# ─── question prefetch cache ──────────────────────────────────────────────
# Maintains a queue of up to QUEUE_SIZE pre-generated questions per user.
QUEUE_SIZE = 2
_prefetch_cache: dict[str, list] = {}   # user_id → list of questions
_prefetch_lock = threading.Lock()
_prefetch_active: dict[str, int] = {}   # user_id → count of in-flight workers

def _prefetch_worker(user_id: str, grade: str, bias: int, session_id: str | None):
    try:
        # Topic, difficulty, EEG state, and the manual Easier/Auto/Harder bias
        # are all resolved in this one call now -- no second "regenerate at
        # adjusted difficulty" LLM call needed, so each prefetched question
        # only ever costs one topic/difficulty decision + one generation call.
        question = LLM_topic_decider.LLM_single_prompt_topic_and_difficulty_decider(
            user_id, grade, session_id, bias
        )
        if question:
            with _prefetch_lock:
                _prefetch_cache.setdefault(user_id, []).append(question)
    except Exception as e:
        print(f"[prefetch] failed for {user_id[:8]}: {e}")
    finally:
        # Always decrement by exactly 1, whether this worker succeeded,
        # raised, or found nothing to generate -- a set-membership flag here
        # (rather than a real count) previously let the FIRST of several
        # concurrent workers clear the "in flight" state for ALL of them,
        # so _ensure_queue kept spawning more on top of ones still running.
        # That compounds every time a question is served (not just on rapid
        # clicks), eventually piling up far more concurrent Ollama calls than
        # QUEUE_SIZE ever intended, which is what made generation grind to a
        # halt after several questions even at a normal pace.
        with _prefetch_lock:
            _prefetch_active[user_id] = max(0, _prefetch_active.get(user_id, 0) - 1)

def _ensure_queue(user_id: str, grade: str, bias: int, session_id: str | None = None):
    """Spawn workers until the queue + in-flight workers reach QUEUE_SIZE."""
    with _prefetch_lock:
        queued   = len(_prefetch_cache.get(user_id, []))
        inflight = _prefetch_active.get(user_id, 0)
        needed   = QUEUE_SIZE - queued - inflight
        if needed <= 0:
            return
        _prefetch_active[user_id] = inflight + needed
    for _ in range(needed):
        threading.Thread(
            target=_prefetch_worker, args=(user_id, grade, bias, session_id), daemon=True
        ).start()

# ─── models ──────────────────────────────────────────────────────────────

class StartSessionRequest(BaseModel):
    title: str | None = None

class AnswerPayload(BaseModel):
    question_id:    str
    selected_index: int
    correct:        bool

class CreateClassRequest(BaseModel):
    name: str
    grade_level: str | None = None

class UpdateClassRequest(BaseModel):
    name: str | None = None
    grade_level: str | None = None

class JoinClassRequest(BaseModel):
    join_code: str

class LinkChildRequest(BaseModel):
    child_id: str

class UpdateProfileRequest(BaseModel):
    display_name: str | None = None
    grade_level:  str | None = None

class EegSessionRequest(BaseModel):
    session_id: str
    device_id: str | None = None


# ─── profiles ────────────────────────────────────────────────────────────

@app.get("/api/profile/me")
def get_my_profile(request: Request):
    user = get_user(request)
    return _profile(user["id"])

@app.put("/api/profile/me")
def update_my_profile(payload: UpdateProfileRequest, request: Request):
    user = get_user(request)
    fields = {k: v for k, v in payload.dict().items() if v is not None}
    if fields:
        fields["updated_at"] = datetime.utcnow().isoformat()
        supabase.table("profiles").update(fields).eq("id", user["id"]).execute()
    if payload.display_name is not None:
        try:
            supabase.auth.admin.update_user_by_id(
                user["id"],
                {"user_metadata": {**(user.get("user_metadata") or {}),
                                   "display_name": payload.display_name}},
            )
        except Exception as e:
            print("metadata sync failed:", e)
    return _profile(user["id"])


# ─── questions ───────────────────────────────────────────────────────────

@app.get("/api/questions")
def get_questions(limit: int = 100, subject: str | None = None, difficulty: str | None = None):
    q = supabase.table("questions").select("*").limit(limit)
    if subject:    q = q.eq("subject", subject)
    if difficulty: q = q.eq("difficulty", difficulty)
    res = q.execute()
    return res.data or []


# ─── llm generation ──────────────────────────────────────────────────────

@app.get("/api/generate-question")
def generate_question(
    user_id:    str        = Query(...),
    grade:      str | None = Query(None),
    class_id:   str | None = Query(None),
    bias:       int        = Query(0),
    session_id: str | None = Query(None),
):
    effective_grade = grade or "5th Grade"
    if class_id:
        cls = supabase.table("classes").select("grade_level").eq("id", class_id).single().execute()
        if cls.data and cls.data.get("grade_level"):
            effective_grade = cls.data["grade_level"]

    manual_bias = max(-1, min(1, int(bias or 0)))

    # Serve from prefetch queue if available. Topic, difficulty, this
    # session's recent EEG state, and manual bias are all resolved together
    # inside LLM_single_prompt_topic_and_difficulty_decider -- one topic/
    # difficulty decision + one generation call, whether this question came
    # from the queue (decided when it was prefetched) or is generated fresh
    # below (decided right now).
    with _prefetch_lock:
        queue    = _prefetch_cache.get(user_id, [])
        question = queue.pop(0) if queue else None

    if not question:
        print(f"[generate] cache miss for {user_id[:8]} — generating inline")
        question = LLM_topic_decider.LLM_single_prompt_topic_and_difficulty_decider(
            user_id, effective_grade, session_id, manual_bias
        )
        if not question:
            raise HTTPException(500, "Failed to generate question")
    else:
        print(f"[generate] cache hit for {user_id[:8]} — instant serve")

    question["effective_grade"] = effective_grade
    question["bias"]            = manual_bias
    # eeg_label / eeg_adjusted / difficulty were already set by the decider
    # above (or by the prefetch worker that generated this queued question).

    # Refill queue in background
    _ensure_queue(user_id, effective_grade, manual_bias, session_id)

    return question


# ─── sessions ────────────────────────────────────────────────────────────

@app.post("/api/sessions/start")
def start_session(payload: StartSessionRequest, request: Request):
    user = get_user(request)

    # Close any previous open sessions for user before opening a new one.
    # without this a student who closes the tab without actually ending the session
    # leaves a stale open session behind.

    stale_open = supabase.table("sessions").select("id") \
        .eq("user_id", user["id"]).is_("ended_at", "null").execute().data or []
    for s in stale_open:
        eeg_poller.stop(s["id"])
        supabase.table("sessions").update(
            {"ended_at": _utc_now().isoformat()}
        ).eq("id", s["id"]).execute()


    obj  = {
        "user_id":            user["id"],
        "title":              payload.title or "Practice Session",
        "started_at":         _utc_now().isoformat(),
        "questions_answered": 0,
        "correct_answers":    0,
    }
    res = supabase.table("sessions").insert(obj).execute()

    # Pre-warm question queue in background while student sees the setup screen
    profile = _profile(user["id"])
    grade   = profile.get("grade_level") or "5th Grade"
    _ensure_queue(user["id"], grade, 0, res.data[0]["id"])

    return res.data[0]

@app.post("/api/sessions/{session_id}/answer")
def record_answer(session_id: str = Path(...), payload: AnswerPayload = Body(...), request: Request = None):
    user = get_user(request)
    supabase.table("session_answers").insert({
        "session_id":     session_id,
        "user_id":        user["id"],
        "question_id":    payload.question_id,
        "selected_index": payload.selected_index,
        "correct":        payload.correct,
        "answered_at":    datetime.utcnow().isoformat(),
    }).execute()
    sess = supabase.table("sessions").select("*").eq("id", session_id).single().execute()
    if not sess.data:
        raise HTTPException(404, "Session not found")
    cur = sess.data
    supabase.table("sessions").update({
        "questions_answered": (cur.get("questions_answered") or 0) + 1,
        "correct_answers":    (cur.get("correct_answers") or 0) + (1 if payload.correct else 0),
    }).eq("id", session_id).execute()
    return {"ok": True}

@app.post("/api/sessions/{session_id}/end")
def end_session(session_id: str = Path(...), request: Request = None):
    user = get_user(request)
    eeg_poller.stop(session_id)  # auto-stop EEG poller
    sess = supabase.table("sessions").select("*").eq("id", session_id).single().execute()
    if not sess.data:
        raise HTTPException(404, "Session not found")
    data = sess.data
    supabase.table("sessions").update({"ended_at": datetime.utcnow().isoformat()}).eq("id", session_id).execute()

    total_q = data.get("questions_answered") or 0
    correct = data.get("correct_answers")    or 0

    existing = supabase.table("user_stats").select("*").eq("user_id", user["id"]).execute()
    if existing.data:
        s = existing.data[0]
        supabase.table("user_stats").update({
            "total_questions": (s.get("total_questions") or 0) + total_q,
            "total_correct":   (s.get("total_correct")   or 0) + correct,
            "last_session_at": datetime.utcnow().isoformat(),
            "updated_at":      datetime.utcnow().isoformat(),
        }).eq("user_id", user["id"]).execute()
    else:
        supabase.table("user_stats").insert({
            "user_id":          user["id"],
            "total_questions":  total_q,
            "total_correct":    correct,
            "current_streak":   0,
            "best_streak":      0,
            "last_session_at":  datetime.utcnow().isoformat(),
        }).execute()
    return {"ok": True}

@app.get("/api/sessions")
def list_sessions(request: Request):
    user = get_user(request)
    res  = supabase.table("sessions").select("*").eq("user_id", user["id"]).order("started_at", desc=True).execute()
    return res.data or []


# ─── stats ───────────────────────────────────────────────────────────────

@app.get("/api/stats/me")
def my_stats(request: Request):
    user = get_user(request)
    res  = supabase.table("user_stats").select("*").eq("user_id", user["id"]).execute()
    if not res.data:
        return {"total_questions": 0, "total_correct": 0, "current_streak": 0, "best_streak": 0}
    return res.data[0]

@app.get("/api/stats/student/{student_id}")
def student_stats(student_id: str, request: Request):
    _verify_can_view_student(get_user(request), student_id)
    res = supabase.table("user_stats").select("*").eq("user_id", student_id).execute()
    if not res.data:
        return {"total_questions": 0, "total_correct": 0, "current_streak": 0, "best_streak": 0}
    return res.data[0]

@app.get("/api/sessions/student/{student_id}")
def student_sessions(student_id: str, request: Request):
    _verify_can_view_student(get_user(request), student_id)
    res = supabase.table("sessions").select("*").eq("user_id", student_id).order("started_at", desc=True).limit(20).execute()
    return res.data or []

@app.get("/api/performance/student/{student_id}")
def student_performance(student_id: str, request: Request):
    _verify_can_view_student(get_user(request), student_id)
    res = supabase.table("user_math_performance") \
        .select("*, math_topics(topic_name)") \
        .eq("user_id", student_id).execute()
    return res.data or []


@app.get("/api/students/{student_id}/weekly-report")
def student_weekly_report(student_id: str, request: Request, days: int = 7):
    """Aggregated EEG/facial signals for a student over the last `days`.

    Role-neutral path: teachers and parents both read this for the students
    they're entitled to see, so namespacing it under /api/teacher/ would be
    misleading. Access is decided by relationship, not by role name.
    """
    _verify_can_view_student(get_user(request), student_id)
    p = _profile(student_id)
    return {
        "student_name": p.get("display_name") or p.get("email") or "Student",
        **_weekly_signal_report(student_id, max(1, min(days, 30))),
    }


@app.get("/api/students/{student_id}/topic-breakdown")
def student_topic_breakdown(student_id: str, request: Request):
    _verify_can_view_student(get_user(request), student_id)
    return _topic_breakdown(student_id)


# ─── leaderboard ─────────────────────────────────────────────────────────

@app.get("/api/leaderboard")
def leaderboard(request: Request, limit: int = 20):
    get_user(request)
    res = supabase.table("user_stats") \
        .select("user_id, total_correct, total_questions, current_streak, best_streak") \
        .order("total_correct", desc=True).limit(limit).execute()
    rows = res.data or []
    enriched = []
    for i, row in enumerate(rows):
        p = _profile(row["user_id"])
        enriched.append({**row, "display_name": p.get("display_name") or "Student", "rank": i + 1})
    return enriched


# ─── classes ────────────────────────────────────────────────────────────

@app.post("/api/classes")
def create_class(payload: CreateClassRequest, request: Request):
    user = get_user(request)
    if user.get("user_metadata", {}).get("role") != "teacher":
        raise HTTPException(403, "Only teachers can create classes")
    code = rand_code()
    for _ in range(5):
        existing = supabase.table("classes").select("id").eq("join_code", code).execute()
        if not existing.data:
            break
        code = rand_code()
    res = supabase.table("classes").insert({
        "teacher_id":  user["id"],
        "name":        payload.name,
        "grade_level": payload.grade_level,
        "join_code":   code,
    }).execute()
    return res.data[0]

@app.put("/api/classes/{class_id}")
def update_class(class_id: str, payload: UpdateClassRequest, request: Request):
    user = get_user(request)
    cls = supabase.table("classes").select("*").eq("id", class_id).single().execute()
    if not cls.data:
        raise HTTPException(404, "Class not found")
    if cls.data["teacher_id"] != user["id"]:
        raise HTTPException(403, "Not your class")
    fields = {k: v for k, v in payload.dict().items() if v is not None}
    if fields:
        supabase.table("classes").update(fields).eq("id", class_id).execute()
    res = supabase.table("classes").select("*").eq("id", class_id).single().execute()
    return res.data

@app.get("/api/classes")
def my_classes(request: Request):
    user = get_user(request)
    role = user.get("user_metadata", {}).get("role", "student")
    if role == "teacher":
        res = supabase.table("classes").select("*, class_memberships(count)").eq("teacher_id", user["id"]).execute()
    else:
        memberships = supabase.table("class_memberships").select("class_id").eq("student_id", user["id"]).execute()
        ids = [m["class_id"] for m in (memberships.data or [])]
        if not ids:
            return []
        res = supabase.table("classes").select("*").in_("id", ids).execute()
    return res.data or []

@app.post("/api/classes/join")
def join_class(payload: JoinClassRequest, request: Request):
    user = get_user(request)
    cls  = supabase.table("classes").select("*").eq("join_code", payload.join_code.upper()).execute()
    if not cls.data:
        raise HTTPException(404, "Class not found — check the code")
    class_id = cls.data[0]["id"]
    already = supabase.table("class_memberships").select("id") \
        .eq("class_id", class_id).eq("student_id", user["id"]).execute()
    if already.data:
        raise HTTPException(409, "Already in this class")
    supabase.table("class_memberships").insert({
        "class_id":  class_id,
        "student_id": user["id"],
    }).execute()
    return cls.data[0]

def _verify_class_owner(class_id: str, user_id: str):
    """Only the teacher who owns a class may read its roster or live data.

    These endpoints query through the service-role client, which bypasses RLS,
    so the database will not stop a caller on its own -- the check has to happen
    here or not at all.
    """
    # .single() raises rather than returning empty when the row is missing, so
    # a bogus id has to be caught here or it surfaces as a 500 instead of a 404.
    try:
        cls = supabase.table("classes").select("teacher_id").eq("id", class_id).single().execute()
    except Exception:
        raise HTTPException(404, "Class not found")
    if not cls.data:
        raise HTTPException(404, "Class not found")
    if cls.data["teacher_id"] != user_id:
        raise HTTPException(403, "Not your class")


def _can_view_student(viewer: dict, student_id: str) -> bool:
    """Whether `viewer` is allowed to see this student's data.

    Three legitimate relationships: the student themselves, a teacher of a class
    the student is enrolled in, or a linked parent. Everything reachable through
    this check is queried with the service-role client, so RLS is not a backstop.
    """
    uid = viewer["id"]
    if uid == student_id:
        return True

    # Teacher of any class this student belongs to.
    try:
        classes = supabase.table("classes").select("id").eq("teacher_id", uid).execute().data or []
        class_ids = [c["id"] for c in classes]
        if class_ids:
            member = supabase.table("class_memberships").select("id") \
                .in_("class_id", class_ids).eq("student_id", student_id).limit(1).execute().data or []
            if member:
                return True
    except Exception as e:
        print(f"[can_view_student:teacher] {e}")

    # Linked parent.
    try:
        link = supabase.table("parent_child_links").select("id") \
            .eq("parent_id", uid).eq("child_id", student_id).limit(1).execute().data or []
        if link:
            return True
    except Exception as e:
        print(f"[can_view_student:parent] {e}")

    return False


def _verify_can_view_student(viewer: dict, student_id: str):
    if not _can_view_student(viewer, student_id):
        raise HTTPException(403, "You do not have access to this student")


@app.get("/api/classes/{class_id}/students")
def class_students(class_id: str, request: Request):
    user = get_user(request)
    _verify_class_owner(class_id, user["id"])
    memberships = supabase.table("class_memberships").select("student_id, joined_at") \
        .eq("class_id", class_id).execute()
    students = []
    for m in (memberships.data or []):
        sid = m["student_id"]
        stats_res = supabase.table("user_stats").select("*").eq("user_id", sid).execute()
        stats = stats_res.data[0] if stats_res.data else {"total_questions": 0, "total_correct": 0, "current_streak": 0}
        p = _profile(sid)
        students.append({
            "user_id":   sid,
            "name":      p.get("display_name") or "Student",
            "email":     p.get("email") or "",
            "joined_at": m["joined_at"],
            **stats,
        })
    return students


# ─── biosignals: cognitive (headband) + face recognition ──────────────────

class CognitiveSample(BaseModel):
    ts:         str | None = None
    focus:      float | None = None
    stress:     float | None = None
    engagement: float | None = None
    alpha:      float | None = None
    beta:       float | None = None
    theta:      float | None = None
    delta:      float | None = None
    gamma:      float | None = None
    raw:        dict  | None = None

class CognitiveBatch(BaseModel):
    session_id: str
    samples:    list[CognitiveSample]

class FaceSample(BaseModel):
    ts:                  str | None = None
    emotion:             str   | None = None
    attention:           float | None = None
    gaze_x:              float | None = None
    gaze_y:              float | None = None
    identity_confidence: float | None = None
    raw:                 dict  | None = None

class FaceBatch(BaseModel):
    session_id: str
    samples:    list[FaceSample]

def _verify_session_owner(session_id: str, user_id: str):
    sess = supabase.table("sessions").select("user_id").eq("id", session_id).single().execute()
    if not sess.data:
        raise HTTPException(404, "Session not found")
    if sess.data["user_id"] != user_id:
        raise HTTPException(403, "Not your session")

@app.post("/api/signals/cognitive")
def ingest_cognitive(payload: CognitiveBatch, request: Request):
    user = get_user(request)
    _verify_session_owner(payload.session_id, user["id"])
    rows = [{
        "session_id": payload.session_id,
        "user_id":    user["id"],
        "ts":         s.ts or datetime.utcnow().isoformat(),
        "focus":      s.focus, "stress": s.stress, "engagement": s.engagement,
        "alpha":      s.alpha, "beta":   s.beta,   "theta":      s.theta,
        "delta":      s.delta, "gamma":  s.gamma,  "raw":        s.raw,
    } for s in payload.samples]
    if rows: supabase.table("cognitive_signals").insert(rows).execute()
    return {"ok": True, "inserted": len(rows)}

@app.post("/api/signals/face")
def ingest_face(payload: FaceBatch, request: Request):
    user = get_user(request)
    _verify_session_owner(payload.session_id, user["id"])
    rows = [{
        "session_id":          payload.session_id,
        "user_id":             user["id"],
        "ts":                  s.ts or datetime.utcnow().isoformat(),
        "emotion":             s.emotion,
        "attention":           s.attention,
        "gaze_x":              s.gaze_x,
        "gaze_y":              s.gaze_y,
        "identity_confidence": s.identity_confidence,
        "raw":                 s.raw,
    } for s in payload.samples]
    if rows: supabase.table("face_signals").insert(rows).execute()
    return {"ok": True, "inserted": len(rows)}

@app.get("/api/signals/session/{session_id}")
def session_signals(session_id: str, request: Request, since: str | None = None):
    # This returns raw EEG and facial-emotion samples for a session, so resolve
    # whose session it is and apply the same access rule as the student-scoped
    # endpoints rather than trusting any authenticated caller.
    user = get_user(request)
    try:
        sess = supabase.table("sessions").select("user_id").eq("id", session_id).single().execute()
    except Exception:
        raise HTTPException(404, "Session not found")
    if not sess.data:
        raise HTTPException(404, "Session not found")
    _verify_can_view_student(user, sess.data["user_id"])

    cog = supabase.table("cognitive_signals").select("*").eq("session_id", session_id)
    fac = supabase.table("face_signals").select("*").eq("session_id", session_id)
    if since:
        cog = cog.gt("ts", since); fac = fac.gt("ts", since)
    cog_data = cog.order("ts").limit(20000).execute().data or []
    fac_data = fac.order("ts").limit(20000).execute().data or []
    answers  = supabase.table("session_answers").select("*").eq("session_id", session_id).order("answered_at").execute().data or []
    return {"cognitive": cog_data, "face": fac_data, "answers": answers}


# ─── live monitoring (only show truly active sessions) ───────────────────

@app.get("/api/teacher/classes/{class_id}/live")
def class_live(class_id: str, request: Request):
    user = get_user(request)
    # Was: `owner != user AND role != "teacher"` -- which only rejected callers
    # who were neither, so ANY teacher could read ANY class's live signals.
    _verify_class_owner(class_id, user["id"])

    LIVE_WINDOW_SEC = 90
    STALE_AFTER_SEC = 600
    now = datetime.utcnow()
    live_cutoff  = (now - timedelta(seconds=LIVE_WINDOW_SEC)).isoformat()
    stale_cutoff = (now - timedelta(seconds=STALE_AFTER_SEC)).isoformat()

    members = supabase.table("class_memberships").select("student_id").eq("class_id", class_id).execute().data or []
    out = []
    for m in members:
        sid = m["student_id"]
        p = _profile(sid)

        open_sessions = supabase.table("sessions").select("*") \
            .eq("user_id", sid).is_("ended_at", "null") \
            .order("started_at", desc=True).limit(1).execute().data or []

        active = None
        latest_cog = latest_face = None

        if open_sessions:
            sess = open_sessions[0]
            sid2 = sess["id"]
            c = supabase.table("cognitive_signals").select("*") \
                .eq("session_id", sid2).order("ts", desc=True).limit(1).execute().data
            f = supabase.table("face_signals").select("*") \
                .eq("session_id", sid2).order("ts", desc=True).limit(1).execute().data
            a = supabase.table("session_answers").select("answered_at") \
                .eq("session_id", sid2).order("answered_at", desc=True).limit(1).execute().data

            latest_cog  = c[0] if c else None
            latest_face = f[0] if f else None

            candidates = []
            if latest_cog and latest_cog.get("ts"):       candidates.append(latest_cog["ts"])
            if latest_face and latest_face.get("ts"):     candidates.append(latest_face["ts"])
            if a and a[0].get("answered_at"):             candidates.append(a[0]["answered_at"])
            if sess.get("started_at"):                    candidates.append(sess["started_at"])
            last_activity = max(candidates) if candidates else sess.get("started_at")

            if last_activity and last_activity < stale_cutoff:
                supabase.table("sessions").update({
                    "ended_at": now.isoformat()
                }).eq("id", sid2).execute()
                eeg_poller.stop(sid2)
                active = None; latest_cog = None; latest_face = None
            elif last_activity and last_activity >= live_cutoff:
                active = sess

        out.append({
            "user_id":          sid,
            "name":             p.get("display_name") or "Student",
            "email":            p.get("email") or "",
            "active_session":   active,
            "latest_cognitive": latest_cog,
            "latest_face":      latest_face,
        })
    return out


# ─── EEG sidecar integration ─────────────────────────���───────────────────

@app.post("/api/eeg/muse/refresh")
def eeg_muse_refresh(request: Request, body: dict = Body(default={})):
    """Trigger a Bluetooth scan for nearby Muse headbands."""
    get_user(request)
    # TODO(#33): not guarded by eeg_poller.can_use_device -- any logged-in user
    # can rescan/reconnect/disconnect another user's claimed station by
    # device_id. Not a regression (the pre-device-registry single shared
    # stream was equally pokeable), but worth closing now that stations are
    # individually targetable. Tracked as a fast-follow, not fixed here.
    device_id = (body or {}).get("device_id") or eeg_client.DEFAULT_DEVICE_ID
    if not eeg_client.is_alive():
        raise HTTPException(503, "EEG service not running on port 8001")
    try:
        return eeg_client.muse_refresh(device_id)
    except Exception as e:
        raise HTTPException(502, f"Bridge error: {e}")

@app.post("/api/eeg/muse/connect")
def eeg_muse_connect(request: Request, body: dict = Body(...)):
    """Connect to a specific Muse headband by name."""
    get_user(request)
    # TODO(#33): see eeg_muse_refresh above -- not guarded by can_use_device.
    name = (body.get("name") or "").strip()
    device_id = body.get("device_id") or eeg_client.DEFAULT_DEVICE_ID
    if not name:
        raise HTTPException(400, "Device name required")
    if not eeg_client.is_alive():
        raise HTTPException(503, "EEG service not running on port 8001")
    try:
        return eeg_client.muse_connect(name, device_id)
    except Exception as e:
        raise HTTPException(502, f"Bridge error: {e}")

@app.post("/api/eeg/muse/disconnect")
def eeg_muse_disconnect(request: Request, body: dict = Body(default={})):
    """Tell the native bridge to disconnect from the current headband."""
    get_user(request)
    # TODO(#33): see eeg_muse_refresh above -- not guarded by can_use_device.
    # Worth noting this one specifically enables griefing (disconnecting
    # someone else's live session), not just an unwanted rescan/reconnect.
    device_id = (body or {}).get("device_id") or eeg_client.DEFAULT_DEVICE_ID
    if not eeg_client.is_alive():
        raise HTTPException(503, "EEG service not running on port 8001")
    try:
        return eeg_client.muse_disconnect(device_id)
    except Exception as e:
        raise HTTPException(502, f"Bridge error: {e}")

@app.get("/api/eeg/devices")
def eeg_devices(request: Request):
    """List the sidecar's registered devices (stations), for the frontend picker."""
    get_user(request)
    if not eeg_client.is_alive():
        return {"available": False, "devices": []}
    return {"available": True, "devices": eeg_client.list_devices()}

@app.get("/api/eeg/debug")
def eeg_debug(request: Request, device_id: str = eeg_client.DEFAULT_DEVICE_ID):
    """Raw EEG snapshot for local development — returns the full state from EEGResearch."""
    user = get_user(request)
    if not eeg_client.is_alive():
        return {"available": False}
    # A station with a live poller is that poller's owner's in-progress
    # biometric data, not shared classroom data -- don't let another user
    # read it just because they know the device_id.
    if not eeg_poller.can_use_device(user["id"], device_id):
        return {"available": False, "reason": "in_use_by_other"}
    # Same as eeg_health: a missing/misconfigured token makes get_state and
    # get_muse_status raise (by design), which would otherwise surface here as a
    # bare 500. Report it instead so the two EEG endpoints behave alike.
    try:
        snapshot = eeg_client.get_state(device_id, timeout=1.5)
        muse     = eeg_client.get_muse_status(device_id)
    except RuntimeError as e:
        return {"available": False, "error": str(e)}
    return {"available": True, "snapshot": snapshot, "muse": muse}


@app.get("/api/eeg/health")
def eeg_health():
    """Tells the frontend whether the EEGResearch sidecar service is reachable."""
    alive = eeg_client.is_alive()
    if not alive:
        return {"available": False, "url": eeg_client.EEG_API_URL}
    # is_alive() hits the sidecar's unauthenticated /healthz, so a reachable
    # sidecar tells us nothing about auth. get_muse_status() needs the learner
    # token and raises (by design -- see eeg_client.get_state) when it is
    # missing or misconfigured. That is a configuration error, not an outage,
    # but this is a health check: it must report the problem, not 500 on it.
    # Return unavailable with the reason so the frontend degrades exactly as it
    # does for an outage while a developer sees the cause instead of a bare
    # stack trace in the logs.
    try:
        muse = eeg_client.get_muse_status()
    except RuntimeError as e:
        return {"available": False, "url": eeg_client.EEG_API_URL, "error": str(e)}
    return {"available": True, "url": eeg_client.EEG_API_URL, "muse": muse}

@app.post("/api/eeg/start")
def eeg_start(payload: EegSessionRequest, request: Request):
    user = get_user(request)
    sess = supabase.table("sessions").select("user_id, ended_at") \
        .eq("id", payload.session_id).single().execute()
    if not sess.data:
        raise HTTPException(404, "Session not found")
    if sess.data["user_id"] != user["id"]:
        raise HTTPException(403, "Not your session")
    if sess.data.get("ended_at"):
        raise HTTPException(400, "Session already ended")
    if not eeg_client.is_alive():
        raise HTTPException(503, "EEG service is not running on port 8001")
    device_id = payload.device_id or eeg_client.DEFAULT_DEVICE_ID
    # Without this, an unknown/typo'd device_id spawns a poller that dies on
    # the sidecar's 404 -- but eeg_poller.start() has already returned
    # running: True by then, so the user sees "connected" and silently gets
    # no data. known_ids empty (list_devices() unreachable/erroring even
    # though is_alive() just succeeded) falls back to the old permissive
    # behavior rather than blocking a legitimate start on a transient glitch.
    known_ids = {d.get("device_id") for d in eeg_client.list_devices()}
    if known_ids and device_id not in known_ids:
        raise HTTPException(404, f"Unknown device_id: {device_id!r}")
    try:
        out = eeg_poller.start(supabase, user["id"], payload.session_id, device_id)
    except eeg_poller.DeviceClaimedError:
        raise HTTPException(
            409,
            "This headband is already recording for another user. "
            "Ask them to disconnect before pairing here.",
        )
    return {"ok": True, **out}

@app.post("/api/eeg/stop")
def eeg_stop(payload: EegSessionRequest, request: Request):
    user = get_user(request)
    sess = supabase.table("sessions").select("user_id") \
        .eq("id", payload.session_id).single().execute()
    if not sess.data or sess.data["user_id"] != user["id"]:
        raise HTTPException(403, "Not your session")
    return {"ok": True, **eeg_poller.stop(payload.session_id)}

@app.get("/api/eeg/status")
def eeg_status(request: Request, device_id: str = eeg_client.DEFAULT_DEVICE_ID):
    user = get_user(request)
    # Blank only the muse block, not the whole response -- the caller's own
    # poller status (below) is always theirs to see regardless of device_id.
    muse = (
        eeg_client.get_muse_status(device_id)
        if eeg_poller.can_use_device(user["id"], device_id)
        else {"available": False, "reason": "in_use_by_other"}
    )
    return {
        "service": eeg_client.is_alive(),
        "muse":    muse,
        "poller":  eeg_poller.status(user["id"]),
    }


# ─── parent endpoints ────────────────────────────────────────────────────

@app.post("/api/parent/link-child")
def link_child(payload: LinkChildRequest, request: Request):
    user = get_user(request)
    if user.get("user_metadata", {}).get("role") != "parent":
        raise HTTPException(403, "Only parents can link children")
    p = _profile(payload.child_id)
    if not p or p.get("role") != "student":
        raise HTTPException(404, "Child account not found or not a student")
    already = supabase.table("parent_child_links").select("id") \
        .eq("parent_id", user["id"]).eq("child_id", payload.child_id).execute()
    if already.data:
        raise HTTPException(409, "Already linked to this child")
    supabase.table("parent_child_links").insert({
        "parent_id": user["id"],
        "child_id":  payload.child_id,
    }).execute()
    return {"ok": True, "child_id": payload.child_id, "child_name": p.get("display_name") or "Student"}

@app.get("/api/parent/children")
def my_children(request: Request):
    user = get_user(request)
    links = supabase.table("parent_child_links").select("child_id, created_at") \
        .eq("parent_id", user["id"]).execute()
    # One round-trip for every child's signal averages, rather than one per
    # child inside the loop below. Note the rest of this loop is still a query
    # per child (stats, sessions, performance, profile); this fixes the query
    # added here, not the endpoint's overall shape.
    summaries = _signal_summaries([lnk["child_id"] for lnk in (links.data or [])])
    children = []
    for lnk in (links.data or []):
        cid = lnk["child_id"]
        stats_res = supabase.table("user_stats").select("*").eq("user_id", cid).execute()
        stats = stats_res.data[0] if stats_res.data else {"total_questions": 0, "total_correct": 0, "current_streak": 0, "best_streak": 0}
        sess_res = supabase.table("sessions").select("*").eq("user_id", cid).order("started_at", desc=True).limit(5).execute()
        perf_res = supabase.table("user_math_performance").select("*, math_topics(topic_name)").eq("user_id", cid).execute()
        p = _profile(cid)
        children.append({
            "user_id":     cid,
            "name":        p.get("display_name") or "Student",
            "email":       p.get("email") or "",
            "linked_at":   lnk["created_at"],
            "stats":       stats,
            "sessions":    sess_res.data or [],
            "performance": perf_res.data or [],
            # Headline signal averages only. Deliberately not the full weekly
            # report: that pulls thousands of raw rows per child, and this runs
            # on a dashboard that loads every visit.
            "signal_summary": summaries.get(str(cid)) or _EMPTY_SUMMARY.copy(),
        })
    return children


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=BACKEND_PORT, reload=True)