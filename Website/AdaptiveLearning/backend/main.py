from fastapi import FastAPI, Request, HTTPException, Path, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import os, re, requests, random, string, threading, time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
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
# Sessions are far coarser than signal samples -- one row per sitting, not per
# reading -- so they get their own, much smaller cap.
_SESSION_ROW_CAP = 100


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


def _rpc_signature_missing(exc) -> bool:
    """Whether a failed RPC means *this signature* is absent, not that it broke.

    PostgREST answers PGRST202 when nothing matches the name and argument list;
    Postgres itself uses SQLSTATE 42883 (undefined_function).

    The fallback to matching the string form is deliberately loose -- these
    clients do not expose a code attribute consistently, and "42883" could in
    principle appear in unrelated error text. That is safe here because of where
    the answer is used: _summary_rpc only retries when include_face is True, and
    the retry just drops an argument whose new-signature default is also True.
    A false positive therefore costs one extra round-trip and returns the same
    rows. Do not reuse this to decide anything where a wrong True is not free.
    """
    code = getattr(exc, "code", None)
    if code in ("PGRST202", "42883"):
        return True
    text = str(exc)
    return "PGRST202" in text or "42883" in text


def _summary_rpc(name: str, params: dict, include_face: bool):
    """Call a summary RPC, tolerating a database that predates p_include_face.

    The parameter arrived with its own migration, so between deploying this
    code and applying that migration every call carries an argument the
    database has no signature for. Left alone the endpoint answers with empty
    summaries -- blank dashboards, no error -- for the length of the window.

    The retry is deliberately conditional on include_face. With the opt-out on
    there is no safe fallback: the old signature has no way to be told, so
    calling it would read exactly the facial rows the caller asked us not to.
    A blank tile is the correct outcome there, and the UI already renders it as
    "Off" rather than as a measurement.

    REMOVABLE once 20260801000000_signal_summary_include_face.sql is applied
    everywhere this code runs. It exists only for the window between deploying
    this build and running that migration, and after that the fallback branch is
    unreachable -- but it has to outlive the deploy it guards, so it cannot go in
    the same change that introduced it. Deleting it early re-opens exactly the
    blank-dashboard failure it was added for.
    """
    try:
        return supabase.rpc(name, {**params, "p_include_face": include_face}).execute()
    except Exception as e:
        if not (include_face and _rpc_signature_missing(e)):
            raise
        print(f"[{name}] p_include_face missing; database is behind this build")
        return supabase.rpc(name, params).execute()


def _signal_summary(student_id: str, days: int = 7, include_face: bool = True) -> dict:
    """Just the headline averages, aggregated in Postgres.

    The full report pulls thousands of raw signal rows to compute a handful of
    numbers, which is fine for one student on a detail page and wasteful on a
    list that loads every visit. This returns the same headline figures without
    transferring any rows -- see the student_signal_summary migration.

    include_face=False is passed down into the aggregate, so no facial row is
    read at all -- the same guarantee _weekly_signal_report makes, rather than
    a null applied on the way out.
    """
    try:
        res = _summary_rpc("student_signal_summary",
                           {"p_student_id": student_id, "p_days": days}, include_face)
    except Exception as e:
        print(f"[signal_summary] {e}")
        return _shape_summary(None, include_face)
    rows = res.data or []
    row = rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else None)
    return _shape_summary(row, include_face)


_EMPTY_SUMMARY = {"focus": None, "stress": None, "engagement": None,
                  "face_attention": None, "sessions": 0,
                  "cognitive_samples": 0, "face_samples": 0,
                  "face_included": True}


def _shape_summary(row, include_face: bool = True) -> dict:
    if not row:
        return {**_EMPTY_SUMMARY, "face_included": include_face}
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
        # With the opt-out on, face_attention is null and face_samples 0 --
        # identical to a student the camera never saw. Same distinction the
        # weekly report draws with its own face_included.
        "face_included": include_face,
    }


def _signal_summaries(student_ids: list[str], days: int = 7,
                      include_face: bool = True) -> dict[str, dict]:
    """Headline averages for many students in one round-trip.

    The single-student RPC removes the row transfer but still costs one
    round-trip per child on a dashboard that loads every visit.
    """
    if not student_ids:
        return {}
    try:
        res = _summary_rpc("student_signal_summary_many",
                           {"p_student_ids": student_ids, "p_days": days}, include_face)
    except Exception as e:
        print(f"[signal_summaries] {e}")
        return {}
    rows = res.data or []
    if isinstance(rows, dict):
        rows = [rows]
    return {str(r.get("student_id")): _shape_summary(r, include_face)
            for r in rows if r.get("student_id")}


def _weekly_signal_report(student_id: str, days: int = 7, include_face: bool = True):
    """Aggregate a student's recent EEG and facial signals for reporting.

    Returns averages, highlights and per-day buckets. Callers must have already
    established that the requester may see this student.

    include_face=False skips the face_signals query outright rather than
    fetching and discarding: the point of the opt-out is that facial data isn't
    read at all, and it also drops the heaviest of the three queries. Every
    face-derived field then comes back None, and `face_included` tells the
    caller that means "not requested" rather than "nothing recorded".
    """
    since = _iso_days_ago(days)

    def _fetch(table: str, ts_col: str, limit: int) -> tuple[list, bool, int | None]:
        """Rows (newest first), whether the server withheld any, and the total.

        Truncation is detected from an exact count rather than from
        len(rows) >= limit. PostgREST applies its own db-max-rows ceiling
        (commonly 1000) on top of our .limit(), so a smaller server cap would
        silently trim the result while len(rows) never reached _REPORT_ROW_CAP
        -- leaving truncated=False and the whole guard disabled. Comparing
        against the count the server reports works whichever limit binds.

        That count is worth returning as well as comparing: for sessions it is
        the figure the report is actually about, and it is exact whether or not
        the cap bound. None means the server reported no count.
        """
        try:
            res = supabase.table(table).select("*", count="exact") \
                .eq("user_id", student_id).gte(ts_col, since) \
                .order(ts_col, desc=True).limit(limit).execute()
            rows = res.data or []
            total = getattr(res, "count", None)
            if not isinstance(total, int):
                total = None
            # total=None means the client/server didn't report one; fall back
            # to the length heuristic rather than claiming nothing was cut.
            was_cut = (total > len(rows)) if total is not None else len(rows) >= limit
            return rows, was_cut, total
        except Exception as e:
            print(f"[weekly_report:{table}] {e}")
            return [], False, None

    cog, cog_cut, _ = _fetch("cognitive_signals", "ts", _REPORT_ROW_CAP)
    face, face_cut, _ = _fetch("face_signals", "ts", _REPORT_ROW_CAP) if include_face else ([], False, None)
    # Truncation kept, not discarded. A student over the cap was shown a count
    # that silently stopped at it -- and with the other two tables under their
    # own cap, `truncated` stayed False and nothing said so.
    sessions, ses_cut, ses_total = _fetch("sessions", "started_at", _SESSION_ROW_CAP)

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

    truncated = cog_cut or face_cut or ses_cut
    cog_oldest_day = _oldest(cog, "ts")[:10]
    face_oldest_day = _oldest(face, "ts")[:10]
    ses_oldest_day = _oldest(sessions, "started_at")[:10]

    latest_cognitive = cog[0] if cog else None
    latest_face = face[0] if face else None

    daily = []
    for i in range(days - 1, -1, -1):
        day = (_utc_now() - timedelta(days=i)).date().isoformat()
        # "We could not retrieve this day", judged per table.
        cog_missing = bool(cog_cut and cog_oldest_day and day < cog_oldest_day)
        face_missing = bool(face_cut and face_oldest_day and day < face_oldest_day)
        ses_missing = bool(ses_cut and ses_oldest_day and day < ses_oldest_day)
        # Skip only when nothing we actually asked for could be retrieved. With
        # face reporting off there is no face request to fail, so the day hinges
        # on the other two -- otherwise an always-False face_missing would keep
        # days that hold no retrievable data at all.
        #
        # Sessions count here too: they come from their own query under its own
        # cap, so a day whose signals were trimmed can still have a session
        # count that was retrieved intact. Dropping the day threw that away and
        # reported the day as absent rather than partial.
        if cog_missing and (face_missing or not include_face) and ses_missing:
            continue
        day_cog = [r for r in cog if str(r.get("ts", ""))[:10] == day]
        day_face = [r for r in face if str(r.get("ts", ""))[:10] == day]
        daily.append({
            "date": day,
            "focus": None if cog_missing else _avg([r.get("focus") for r in day_cog]),
            "stress": None if cog_missing else _avg([r.get("stress") for r in day_cog]),
            "engagement": None if cog_missing else _avg([r.get("engagement") for r in day_cog]),
            "attention": None if face_missing else _avg([r.get("attention") for r in day_face]),
            # None rather than 0, on the same reasoning as the metrics above: a
            # day the cap kept us from reading did not have zero sessions, and
            # `sessions_retrieved` is what tells the two apart.
            "sessions": None if ses_missing else
                        len([r for r in sessions if str(r.get("started_at", ""))[:10] == day]),
            # False means "the cap stopped us fetching this", which a null
            # metric alone cannot distinguish from "nothing was recorded".
            # None means "not requested" -- face reporting is off, so there was
            # no retrieval to succeed or fail, and the consumers that count
            # `=== false` must not treat the opt-out as a retrieval failure.
            "cognitive_retrieved": not cog_missing,
            "face_retrieved": (not face_missing) if include_face else None,
            "sessions_retrieved": not ses_missing,
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
    if bits:
        summary = "This week, " + ", ".join(bits) + "."
    elif include_face:
        summary = "No EEG or facial recognition samples were recorded this week."
    else:
        # Naming facial recognition here would report an absence that was never
        # measured, since the caller opted out of reading it.
        summary = "No EEG samples were recorded this week."

    return {
        "student_id": student_id,
        "days": days,
        "since": since,
        "truncated": truncated,
        # Distinguishes "facial reporting is switched off for this view" from
        # "the camera recorded nothing". Both leave every face field null.
        "face_included": include_face,
        "sample_counts": {"cognitive": len(cog), "face": len(face), "sessions": len(sessions)},
        # How many sessions there *were*, as opposed to how many rows came back
        # under _SESSION_ROW_CAP. sample_counts is rows-retrieved throughout, so
        # rendering its sessions figure as the report's headline showed a heavy
        # week as exactly the cap -- while the parent dashboard, which counts in
        # Postgres, showed the real number for the same child and week. Falls
        # back to the row count when the server reported no exact count, which
        # is the same fallback the truncation check makes.
        "sessions_recorded": ses_total if ses_total is not None else len(sessions),
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
def student_weekly_report(student_id: str, request: Request, days: int = 7, include_face: bool = True):
    """Aggregated EEG/facial signals for a student over the last `days`.

    Role-neutral path: teachers and parents both read this for the students
    they're entitled to see, so namespacing it under /api/teacher/ would be
    misleading. Access is decided by relationship, not by role name.

    include_face=false omits facial-recognition data from the report entirely;
    see _weekly_signal_report.
    """
    _verify_can_view_student(get_user(request), student_id)
    p = _profile(student_id)
    return {
        "student_name": p.get("display_name") or p.get("email") or "Student",
        **_weekly_signal_report(student_id, max(1, min(days, 30)), include_face=include_face),
    }


@app.get("/api/students/{student_id}/topic-breakdown")
def student_topic_breakdown(student_id: str, request: Request):
    _verify_can_view_student(get_user(request), student_id)
    return _topic_breakdown(student_id)


# ─── at-home learning strategies ─────────────────────────────────────────

def _env_number(name: str, default, cast):
    """A numeric setting from the environment, falling back on a bad value.

    These are read at import, so a typo in a deployment's environment would
    otherwise raise ValueError before the app object exists -- taking down every
    endpoint over a tuning parameter for one optional feature. Falling back to
    the shipped default keeps the process up, and the log line is what says the
    setting is not the one that was configured.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        print(f"[config] {name}={raw!r} is not a number; using {default}")
        return default


# The model pass is opt-in. Off, the endpoint answers from the deterministic
# rules below and never opens a socket -- which is what CI, and any deployment
# without a local Ollama, should do. Enabling it changes only whether the
# rule-based answer gets a chance to be replaced.
STRATEGY_LLM_ENABLED = os.getenv("STRATEGY_LLM_ENABLED", "false").strip().lower() in ("1", "true", "yes")
STRATEGY_LLM_MODEL   = os.getenv("STRATEGY_LLM_MODEL", "llama3.1:8b")
# Wall-clock budget for the whole model call. A hung Ollama server is not an
# exception, so without an explicit timeout the endpoint would block one of a
# small pool of worker threads indefinitely instead of falling back to the
# rule-based answer the caller is guaranteed.
STRATEGY_LLM_TIMEOUT = _env_number("STRATEGY_LLM_TIMEOUT", 20.0, float)

# The model call runs here rather than on the request's own thread so the
# deadline can actually be enforced.
#
# Handing the timeout to the client is not enough on its own: httpx applies it
# per operation (connect, then each read), not to the call as a whole, so a
# server that dribbles a byte inside every window keeps the request alive well
# past STRATEGY_LLM_TIMEOUT. Waiting on a future instead bounds what the caller
# experiences, whatever the transport does.
#
# The client-side timeout stays on for the other half of the problem: once the
# wait is abandoned the worker is still in there, and the per-operation deadline
# is what eventually frees it. max_workers caps how many can pile up; past that,
# submissions queue and time out on the same deadline, which degrades to the
# rule-based answer rather than to unbounded threads.
#
# max_workers bounds the threads but not the queue behind them, so an abandoned
# wait also cancels its future -- see _llm_strategies_bounded. Without that, a
# stalled server turns every timed-out request into a work item that still runs
# later, and the backlog is unbounded even though the thread count is not.
_STRATEGY_LLM_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="strategy-llm")

# Per-caller ceiling on the endpoint. It is the heaviest thing a parent can
# trigger by clicking a button -- an aggregate over both signal tables, a topic
# breakdown, and optionally a model call -- and the button is repeatable at
# whatever rate they can click. The model call dominates, which is why the
# limit stayed after _strategy_basis dropped the row transfer.
#
# In-process, so with multiple uvicorn workers the effective ceiling is this
# many per worker. That is deliberate: the point is to blunt one caller looping
# on the button, and a shared counter would mean a cache or a table to keep it
# in. Move it to one if the ceiling ever needs to be exact.
_STRATEGY_RATE_LIMIT  = _env_number("STRATEGY_RATE_LIMIT", 10, int)
_STRATEGY_RATE_WINDOW = _env_number("STRATEGY_RATE_WINDOW", 60.0, float)
_strategy_hits: dict[str, list[float]] = {}
_strategy_hits_lock = threading.Lock()
# When the sweep below last ran. Size alone was not a sufficient trigger: past
# the threshold with that many *active* callers, every request scanned the
# whole dict and deleted nothing, holding the lock to do it. Pairing size with
# an interval keeps the sweep proportional to time rather than to traffic.
_strategy_sweep_at = 0.0
_STRATEGY_SWEEP_EVERY = 60.0
_STRATEGY_SWEEP_ABOVE = 1024


def _rate_limit_strategies(user_id: str):
    """Raise 429 if this caller has already had its allowance this window.

    Timed on monotonic(), not wall-clock: a clock adjustment would otherwise
    either wipe the window or extend it arbitrarily.
    """
    global _strategy_sweep_at
    now = time.monotonic()
    with _strategy_hits_lock:
        # The dict is keyed by caller, so it grows with everyone who has ever
        # used the endpoint. Sweeping it only once it is large keeps the common
        # path a single lookup rather than a scan of every known caller, and
        # only once per interval keeps it that way when the dict is large
        # because the callers are real -- where a size-only trigger scanned
        # everything on every request and freed nothing.
        if (len(_strategy_hits) > _STRATEGY_SWEEP_ABOVE
                and now - _strategy_sweep_at >= _STRATEGY_SWEEP_EVERY):
            _strategy_sweep_at = now
            for uid in [u for u, ts in _strategy_hits.items()
                        if all(now - t >= _STRATEGY_RATE_WINDOW for t in ts)]:
                del _strategy_hits[uid]

        hits = [t for t in _strategy_hits.get(user_id, ()) if now - t < _STRATEGY_RATE_WINDOW]
        if len(hits) >= _STRATEGY_RATE_LIMIT:
            _strategy_hits[user_id] = hits
            # Measured from the oldest hit still counted -- that is the one
            # whose expiry frees a slot.
            retry_after = max(1, int(_STRATEGY_RATE_WINDOW - (now - min(hits))) + 1)
            raise HTTPException(
                429,
                "Too many strategy requests. Try again shortly.",
                headers={"Retry-After": str(retry_after)},
            )
        hits.append(now)
        _strategy_hits[user_id] = hits

_STRATEGY_COUNT = 5
_STRATEGY_MAX_CHARS = 320
# A floor as well as a ceiling. Without one, a truncated or degenerate reply
# ("1. a\n2. b\n3. c") passed every check and was served to a parent as
# model-refined advice. Nothing useful to someone helping a child with maths
# fits in fewer characters than this, and the rule-based list is always there
# to fall back to.
_STRATEGY_MIN_CHARS = 25

# Clinical vocabulary that must not reach a parent from this endpoint. These
# are learning-state indicators, not measurements of health, and a local model
# asked for study tips will still occasionally volunteer a diagnosis. Output
# containing any of these is discarded wholesale rather than edited: a sentence
# that needed a word removed to be safe is not a sentence to hand a parent.
_CLINICAL_TERMS = re.compile(
    r"\b(diagnos\w*|disorder\w*|disabilit\w*|adhd|autis\w*|dyslex\w*|dyscalcul\w*|"
    r"depress(?:ion|ive)|anxiet\w*|anxious|medicat\w*|meds|prescri\w*|psychiatr\w*|"
    r"psycholog\w*|counsel\w*|clinical\w*|symptom\w*|disease\w*|syndrome\w*|"
    r"patient\w*|therap(?:y|ist|ies)|treatment\w*|neurolog\w*|"
    r"cognitive impairment|special (?:needs|education\w*)|iep)\b",
    re.IGNORECASE,
)

# Leading "1.", "2)", "-", "*", "•" from a numbered or bulleted model reply.
_LIST_MARKER = re.compile(r"^\s*(?:\d+\s*[\).:]|[-*•])\s*")

# Markdown emphasis a model wraps an item in ("1. **Keep sessions short**").
# Stripped rather than left alone: nothing renders markdown between here and
# the parent, so the asterisks would reach them as literal punctuation.
#
# Split by delimiter, because the two need different rules. Underscores are
# only emphasis at a word boundary: matched anywhere, and with the pattern
# deleting its delimiters rather than spacing them, an item naming topics in
# the form the tables store them came out mangled -- "review
# angle_relationships and mean_median" became "review anglerelationships and
# meanmedian", a garbled word served to a parent past every other check.
# CommonMark draws the boundary in the same place and for the same reason, so
# "_problem felt hardest_" is still unwrapped while snake_case is left alone.
#
# Asterisks need no such guard: they do not occur inside words.
_MD_ASTERISK = re.compile(r"(\*{1,3})(?=\S)(.+?)(?<=\S)\1")
_MD_UNDERSCORE = re.compile(r"(?<!\w)(_{1,3})(?=\S)(.+?)(?<=\S)\1(?!\w)")


def _strip_emphasis(line: str) -> str:
    return _MD_UNDERSCORE.sub(r"\2", _MD_ASTERISK.sub(r"\2", line))


def _weakest_topic(topics: list[dict]):
    """Lowest-accuracy topic the student has actually attempted.

    Topics with no attempts are excluded: _topic_breakdown reports them at 0%,
    which would otherwise always win and send a parent to revise a topic their
    child has never been given.
    """
    attempted = [t for t in topics if (t.get("attempted_questions") or 0) > 0]
    if not attempted:
        return None
    return min(attempted, key=lambda t: t.get("accuracy") or 0)


def _weakest_topic_summary(topics: list[dict]) -> dict | None:
    """Just the fields the strategies response is about."""
    weakest = _weakest_topic(topics)
    if not weakest:
        return None
    return {
        "topic_name": weakest.get("topic_name"),
        "accuracy": weakest.get("accuracy"),
        "attempted_questions": weakest.get("attempted_questions"),
    }


def _strategy_basis(student_id: str, days: int, include_face: bool) -> dict:
    """The slice of a weekly report this endpoint actually reads.

    Built from the aggregate RPC rather than _weekly_signal_report. Between
    them _rule_based_strategies and _strategy_prompt use six numbers, and the
    full report transfers up to _REPORT_ROW_CAP rows from each signal table
    plus the sessions query to arrive at them -- the same waste _signal_summary
    was added to remove from the parent dashboard, on the endpoint that is also
    the heaviest thing a click can trigger.

    Shaped like a report because the two consumers read report keys, and both
    are also called directly on real reports by the weekly-report tests.

    Two differences from the report's own figures, both improvements:
    sessions is Postgres's count rather than a row count capped at
    _SESSION_ROW_CAP, and averages carries no identity_confidence -- a
    face-recognition confidence score that this response was never about and
    that only reached `basis` because the whole averages dict was passed along.

    include_face is threaded down into the aggregate, so with the opt-out on no
    facial row is read here either.
    """
    summary = _signal_summary(student_id, days, include_face=include_face)
    return {
        "days": days,
        "face_included": summary["face_included"],
        "averages": {
            "focus": summary["focus"],
            "stress": summary["stress"],
            "engagement": summary["engagement"],
            "face_attention": summary["face_attention"],
        },
        "sample_counts": {"sessions": summary["sessions"]},
    }


def _rule_based_strategies(report: dict, topics: list[dict]) -> list[str]:
    """Deterministic at-home strategies derived from the weekly report.

    Always computed, and always what the endpoint falls back to. Thresholds are
    on the 0..1 ratios the signal tables store.
    """
    averages = report.get("averages") or {}
    strategies = []

    weakest = _weakest_topic(topics)
    if weakest:
        label = str(weakest.get("topic_name") or "the weakest topic").replace("_", " ")
        strategies.append(
            f"Spend 10-15 minutes on {label} before new material — it is currently "
            f"the lowest-scoring topic at {weakest.get('accuracy')}%."
        )
    else:
        strategies.append(
            "Start with a short review and ask your child to explain one solved "
            "problem out loud, which shows where their understanding actually stops."
        )

    stress = averages.get("stress")
    if stress is not None and float(stress) >= 0.65:
        strategies.append(
            "Break practice into shorter blocks with a two-minute pause between "
            "them — stress indicators ran high this week."
        )
    else:
        strategies.append(
            "Keep practice to 15-20 minute blocks, each followed by quick feedback "
            "on what went well."
        )

    focus = averages.get("focus")
    if focus is not None and float(focus) < 0.45:
        strategies.append(
            "Clear the workspace of phones and second screens, and set one small "
            "goal per block — focus indicators were low this week."
        )
    else:
        strategies.append(
            "Keep the study setup and time of day consistent, since the current "
            "routine is holding up."
        )

    attention = averages.get("face_attention")
    if report.get("face_included") and attention is not None and float(attention) < 0.5:
        strategies.append(
            "Try working problems on paper together or tying them to a real "
            "situation when attention drifts."
        )

    strategies.append(
        "Close each session by asking which problem felt hardest and what helped "
        "most — it makes the next session easier to plan."
    )
    return strategies[:_STRATEGY_COUNT]


def _strategy_prompt(report: dict, topics: list[dict], baseline: list[str]) -> str:
    """Prompt text built from aggregates only.

    Deliberately excludes the student id, name, and the raw `latest` rows the
    report carries: the model needs the shape of the week, not a record that
    identifies a child.
    """
    averages = report.get("averages") or {}

    def _pct(v):
        return "unavailable" if v is None else f"{round(float(v) * 100)}%"

    weakest = _weakest_topic(topics)
    topic_line = (
        f"{str(weakest.get('topic_name')).replace('_', ' ')} at {weakest.get('accuracy')}%"
        if weakest else "no attempted topics yet"
    )
    face_line = (
        f"average facial attention {_pct(averages.get('face_attention'))}"
        if report.get("face_included") else "facial reporting is switched off"
    )
    return (
        "You are helping a parent support their child's maths practice at home.\n"
        "Use only the weekly summary below. These are classroom learning "
        "indicators, not medical measurements — do not diagnose, do not name any "
        "condition, and do not give medical advice.\n"
        f"Return exactly {_STRATEGY_COUNT} short, practical, at-home strategies as "
        "a numbered list. One sentence each, no preamble.\n\n"
        f"Weekly summary (last {report.get('days', 7)} days):\n"
        f"- average focus {_pct(averages.get('focus'))}\n"
        f"- average stress {_pct(averages.get('stress'))}\n"
        f"- average engagement {_pct(averages.get('engagement'))}\n"
        f"- {face_line}\n"
        f"- weakest attempted topic: {topic_line}\n"
        f"- practice sessions recorded: {(report.get('sample_counts') or {}).get('sessions', 0)}\n\n"
        "For reference, here is a safe baseline answer:\n"
        + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(baseline))
    )


def _parse_strategy_lines(raw: str) -> list[str]:
    """The list items of a model reply, in order.

    Only lines that actually carried a list marker count. The prompt asks for a
    numbered list and says no preamble, but nothing makes the model obey: taking
    every non-empty line turned a lead-in like "Here are five strategies for
    your child:" into strategy #1, handing a parent a numbered instruction the
    model never wrote. Unmarked trailing chatter goes the same way, and a
    strategy wrapped across two lines keeps only its marked first line rather
    than splitting into two half-sentences.

    Emphasis is unwrapped before the marker is stripped, not after: a model that
    bolds the whole item ("**1. Keep sessions short**") puts an asterisk in
    front of the number, which the marker pattern would otherwise consume as a
    bullet and leave the digits behind as text.
    """
    lines = []
    for line in (raw or "").splitlines():
        cleaned, marked = _LIST_MARKER.subn("", _strip_emphasis(line))
        cleaned = cleaned.strip()
        if marked and cleaned:
            lines.append(cleaned)
    return lines


def _validated_strategies(raw: str) -> list[str] | None:
    """Model output, or None if it fails any check.

    Returning None means the caller keeps the deterministic list. There is no
    partial acceptance: a reply that breaks one rule has shown it is not
    following the prompt, and the rest of it has not earned more trust.
    """
    # Judged on the whole reply, not just the lines that survive parsing. A
    # model that volunteered a diagnosis in a preamble is not one whose
    # remaining items have earned trust for being well punctuated -- and
    # checking post-parse would let exactly that text set the framing while
    # passing the filter.
    if _CLINICAL_TERMS.search(raw or ""):
        return None
    lines = _parse_strategy_lines(raw)
    if len(lines) < 3:
        return None
    # Bounded at both ends. The ceiling catches a model that ran on; the floor
    # catches one that produced list scaffolding with nothing in it -- "1. a"
    # is well-formed, survives every other check, and is not advice.
    if any(not _STRATEGY_MIN_CHARS <= len(line) <= _STRATEGY_MAX_CHARS for line in lines):
        return None
    return lines[:_STRATEGY_COUNT]


def _llm_strategies(prompt: str) -> list[str] | None:
    """One local-model attempt, or None on any failure.

    ollama is imported here rather than at module scope so this module stays
    importable — and the endpoint stays usable on the rule-based path — in an
    environment where the package is missing. requirements.txt pins it, so that
    is a slimmed-down or partially-installed deployment rather than the normal
    case; the routine failure this guards is the *server* being absent, which
    surfaces as an exception from the call below.

    Goes through an explicit Client so the call carries STRATEGY_LLM_TIMEOUT.
    The module-level ollama.generate() has no deadline, and a server that
    accepts the connection and then stalls never raises — so the promise that
    any failure is a fallback rather than a 500 only holds with it attached.
    """
    try:
        from ollama import Client
        resp = Client(timeout=STRATEGY_LLM_TIMEOUT).generate(
            model=STRATEGY_LLM_MODEL,
            prompt=prompt,
            options={"temperature": 0.4},
        )
        raw = resp.get("response") if isinstance(resp, dict) else getattr(resp, "response", "")
    except Exception as e:
        print(f"[learning_strategies:llm] {e}")
        return None
    return _validated_strategies(raw or "")


def _llm_strategies_bounded(prompt: str) -> list[str] | None:
    """_llm_strategies under a deadline the caller actually feels.

    See _STRATEGY_LLM_POOL: the client-side timeout is per operation, so this
    is what holds when the transport keeps resetting it. Abandoning the wait
    leaves a *started* worker running -- there is no way to interrupt a blocking
    socket read -- but the caller is no longer behind it, and the answer they
    get is the rule-based one that was always the fallback.
    """
    future = None
    try:
        # Inside the try: submit() itself raises if the pool is shutting down,
        # which the handler below turns into the rule-based fallback.
        future = _STRATEGY_LLM_POOL.submit(_llm_strategies, prompt)
        return future.result(timeout=STRATEGY_LLM_TIMEOUT)
    except FutureTimeoutError:
        # Cancel, rather than just dropping the reference. Bounding the workers
        # does not bound the queue behind them: with both busy on a stalled
        # server, every further submission sits in the pool's work queue and
        # still runs once a worker frees up. A sustained outage would otherwise
        # accumulate a backlog of prompts nobody is waiting for any more, and
        # then generate every one of them against the recovered server.
        #
        # Returns False for a task already running, which genuinely cannot be
        # stopped -- but True for one that never started, which is exactly the
        # case that piles up.
        future.cancel()
        print(f"[learning_strategies:llm] abandoned after {STRATEGY_LLM_TIMEOUT}s")
        return None
    except Exception as e:
        # _llm_strategies swallows its own failures, so reaching here means the
        # pool itself refused the work (shutting down, for instance).
        print(f"[learning_strategies:llm] {e}")
        return None


class LearningStrategyRequest(BaseModel):
    include_face: bool = True
    days: int = 7


@app.post("/api/students/{student_id}/learning-strategies")
def student_learning_strategies(student_id: str, request: Request, payload: LearningStrategyRequest):
    """At-home practice strategies derived from a student's weekly report.

    Role-neutral, like the weekly report it reads: gated on the viewer's
    relationship to the student, not on a role claim.

    Always answers. The deterministic rules produce the response unless the
    optional model pass is enabled *and* its output passes _validated_strategies,
    and `source` says which of those happened.

    Reads its signal figures through _strategy_basis, which aggregates in
    Postgres -- the full weekly report pulls thousands of rows to produce the
    handful of numbers the rules and the prompt use.

    Rate limited per caller. The access check runs first so a caller with no
    relationship to the student still gets 403 rather than having the answer
    masked by a 429; the limit then guards the expensive part below.
    """
    viewer = get_user(request)
    _verify_can_view_student(viewer, student_id)
    _rate_limit_strategies(viewer["id"])

    days = max(1, min(payload.days, 30))
    report = _strategy_basis(student_id, days, payload.include_face)
    topics = _topic_breakdown(student_id)

    strategies = _rule_based_strategies(report, topics)
    source = "rule-based"

    if STRATEGY_LLM_ENABLED:
        refined = _llm_strategies_bounded(_strategy_prompt(report, topics, strategies))
        if refined:
            strategies, source = refined, "model-refined"
        else:
            # Named distinctly from the plain rule-based case: "the model was
            # asked and its answer was not usable" is worth seeing in the UI.
            source = "rule-based (model output rejected)"

    return {
        "student_id": student_id,
        "generated_at": _utc_now().isoformat(),
        "strategies": strategies,
        "source": source,
        "basis": {
            "days": days,
            "face_included": payload.include_face,
            "averages": report.get("averages") or {},
            # Named fields rather than the whole _topic_breakdown row, which
            # also carries topic_id, a stress reading and updated_at — none of
            # which this response is about, and all of which would become part
            # of its contract by accident.
            "weakest_topic": _weakest_topic_summary(topics),
        },
    }


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
    user = get_user(request)
    device_id = (body or {}).get("device_id") or eeg_client.DEFAULT_DEVICE_ID
    # A station with a live poller is that poller's owner's in-progress
    # session -- another user rescanning/reconnecting it (or, on the
    # disconnect handler below, killing it outright) is per-victim griefing,
    # not just an unwanted side effect. See can_use_device's docstring for
    # the unclaimed-station pairing window this doesn't (and isn't meant to)
    # close.
    if not eeg_poller.can_use_device(user["id"], device_id):
        raise HTTPException(403, "Station in use by another user")
    if not eeg_client.is_alive():
        raise HTTPException(503, "EEG service not running on port 8001")
    try:
        return eeg_client.muse_refresh(device_id)
    except Exception as e:
        raise HTTPException(502, f"Bridge error: {e}")

@app.post("/api/eeg/muse/connect")
def eeg_muse_connect(request: Request, body: dict = Body(...)):
    """Connect to a specific Muse headband by name."""
    user = get_user(request)
    name = (body.get("name") or "").strip()
    device_id = body.get("device_id") or eeg_client.DEFAULT_DEVICE_ID
    if not name:
        raise HTTPException(400, "Device name required")
    # See eeg_muse_refresh above.
    if not eeg_poller.can_use_device(user["id"], device_id):
        raise HTTPException(403, "Station in use by another user")
    if not eeg_client.is_alive():
        raise HTTPException(503, "EEG service not running on port 8001")
    try:
        return eeg_client.muse_connect(name, device_id)
    except Exception as e:
        raise HTTPException(502, f"Bridge error: {e}")

@app.post("/api/eeg/muse/disconnect")
def eeg_muse_disconnect(request: Request, body: dict = Body(default={})):
    """Tell the native bridge to disconnect from the current headband."""
    user = get_user(request)
    device_id = (body or {}).get("device_id") or eeg_client.DEFAULT_DEVICE_ID
    # See eeg_muse_refresh above -- this is the handler where the
    # unguarded gap mattered most (a stranger disconnecting someone else's
    # live session), so it's guarded the same way for consistency.
    if not eeg_poller.can_use_device(user["id"], device_id):
        raise HTTPException(403, "Station in use by another user")
    if not eeg_client.is_alive():
        raise HTTPException(503, "EEG service not running on port 8001")
    try:
        return eeg_client.muse_disconnect(device_id)
    except Exception as e:
        raise HTTPException(502, f"Bridge error: {e}")

@app.get("/api/eeg/devices")
def eeg_devices(request: Request):
    """List the sidecar's registered devices (stations), for the frontend picker.

    Enumeration-only, gated on being logged in (not can_use_device): it
    returns device_id/kind/running/connection_state_name for every station,
    with no biometric values or per-user ownership -- the picker needs the
    full list to choose from, and "station X is in use" is the extent of
    what leaks. Considered non-sensitive.
    """
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
def my_children(request: Request, include_face: bool = True):
    """A parent's linked children with their headline signal averages.

    include_face=false carries the facial-recognition opt-out down into the
    aggregate, so the dashboard honours the same control as the child's report.
    Without it, switching facial reporting off on a report and navigating back
    put facial attention straight back on screen.
    """
    user = get_user(request)
    links = supabase.table("parent_child_links").select("child_id, created_at") \
        .eq("parent_id", user["id"]).execute()
    # One round-trip for every child's signal averages, rather than one per
    # child inside the loop below. Note the rest of this loop is still a query
    # per child (stats, sessions, performance, profile); this fixes the query
    # added here, not the endpoint's overall shape.
    summaries = _signal_summaries([lnk["child_id"] for lnk in (links.data or [])],
                                  include_face=include_face)
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
            "signal_summary": summaries.get(str(cid)) or _shape_summary(None, include_face),
        })
    return children


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=BACKEND_PORT, reload=True)