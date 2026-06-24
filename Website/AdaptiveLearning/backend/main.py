from fastapi import FastAPI, Request, HTTPException, Path, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os, requests, random, string
from datetime import datetime, timedelta
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

DIFFS = ["easy", "medium", "hard"]

def _shift_difficulty(current: str | None, bias: int) -> str:
    if current not in DIFFS:
        current = "medium"
    idx = max(0, min(len(DIFFS) - 1, DIFFS.index(current) + (bias or 0)))
    return DIFFS[idx]


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
    user_id:  str        = Query(...),
    grade:    str | None = Query(None),
    class_id: str | None = Query(None),
    bias:     int        = Query(0),
):
    effective_grade = grade or "5th Grade"
    if class_id:
        cls = supabase.table("classes").select("grade_level").eq("id", class_id).single().execute()
        if cls.data and cls.data.get("grade_level"):
            effective_grade = cls.data["grade_level"]

    bias = max(-1, min(1, int(bias or 0)))

    question = LLM_topic_decider.LLM_topic_and_difficulty_separate_decider(user_id, effective_grade)
    if not question:
        raise HTTPException(500, "Failed to generate question")

    if bias != 0:
        topic       = question.get("question_topic")
        cur_diff    = question.get("difficulty") or "medium"
        target_diff = _shift_difficulty(cur_diff, bias)
        if topic and target_diff != cur_diff:
            try:
                question = LLM_topic_decider.question_generation(
                    topic, target_diff, user_id, effective_grade
                )
                question["difficulty"] = target_diff
            except Exception as e:
                print("bias regeneration failed, returning original:", e)

    question["effective_grade"] = effective_grade
    question["bias"] = bias
    return question


# ─── sessions ────────────────────────────────────────────────────────────

@app.post("/api/sessions/start")
def start_session(payload: StartSessionRequest, request: Request):
    user = get_user(request)
    obj  = {
        "user_id":            user["id"],
        "title":              payload.title or "Practice Session",
        "started_at":         datetime.utcnow().isoformat(),
        "questions_answered": 0,
        "correct_answers":    0,
    }
    res = supabase.table("sessions").insert(obj).execute()
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
    get_user(request)
    res = supabase.table("user_stats").select("*").eq("user_id", student_id).execute()
    if not res.data:
        return {"total_questions": 0, "total_correct": 0, "current_streak": 0, "best_streak": 0}
    return res.data[0]

@app.get("/api/sessions/student/{student_id}")
def student_sessions(student_id: str, request: Request):
    get_user(request)
    res = supabase.table("sessions").select("*").eq("user_id", student_id).order("started_at", desc=True).limit(20).execute()
    return res.data or []

@app.get("/api/performance/student/{student_id}")
def student_performance(student_id: str, request: Request):
    get_user(request)
    res = supabase.table("user_math_performance") \
        .select("*, math_topics(topic_name)") \
        .eq("user_id", student_id).execute()
    return res.data or []


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

@app.get("/api/classes/{class_id}/students")
def class_students(class_id: str, request: Request):
    get_user(request)
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
    get_user(request)
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
    cls = supabase.table("classes").select("teacher_id").eq("id", class_id).single().execute()
    if not cls.data:
        raise HTTPException(404, "Class not found")
    if cls.data["teacher_id"] != user["id"] and user.get("user_metadata", {}).get("role") != "teacher":
        raise HTTPException(403, "Not your class")

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

@app.get("/api/eeg/health")
def eeg_health():
    """Tells the frontend whether the EEGResearch sidecar service is reachable."""
    alive = eeg_client.is_alive()
    if not alive:
        return {"available": False, "url": eeg_client.EEG_API_URL}
    return {"available": True, "url": eeg_client.EEG_API_URL,
            "muse": eeg_client.get_muse_status()}

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
    out = eeg_poller.start(supabase, user["id"], payload.session_id)
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
def eeg_status(request: Request):
    user = get_user(request)
    return {
        "service": eeg_client.is_alive(),
        "muse":    eeg_client.get_muse_status(),
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
        })
    return children


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=BACKEND_PORT, reload=True)