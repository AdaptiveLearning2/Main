from fastapi import FastAPI, Request, HTTPException, Path, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import os, math, re, requests, random, string, threading, time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from supabase import create_client
from typing import NamedTuple

import LLM_topic_decider
import eeg_client
import signal_mapping
import eeg_poller

load_dotenv()

SUPABASE_URL     = os.getenv("SUPABASE_URL")
SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
BACKEND_PORT     = int(os.getenv("BACKEND_PORT", "8000"))

if not SUPABASE_URL or not SERVICE_ROLE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")

supabase = create_client(SUPABASE_URL, SERVICE_ROLE_KEY)

@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Process-lifetime hooks. Everything before the yield is startup.

    A lifespan handler rather than @app.on_event, which FastAPI deprecated --
    and which has to be attached to the app object, so it would have pulled
    this handler up here anyway. The names below resolve when the handler runs,
    which lets each one stay defined beside the state it owns.
    """
    yield
    # Pollers first: they are daemon threads that print on the way out, so
    # leaving them to interpreter teardown risks a fatal stdout-lock abort on
    # an otherwise clean shutdown. See eeg_poller.stop_all.
    try:
        eeg_poller.stop_all()
    finally:
        # In a finally: a poller that somehow raises on the way out must not
        # take the pool's shutdown down with it. Both are cleanup, and neither
        # gets a second chance after this.
        _shutdown_strategy_pool()


app = FastAPI(title="AdaptiveLearning API", lifespan=_lifespan)

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


class ReportChannels(NamedTuple):
    """Which optional channels a report may read, and whether we know.

    Named rather than a bare tuple because the first version returned
    `(heart, emotion)` while its parameters read `(want_emotion, want_heart)`,
    and one call site unpacked it with `*reversed(...)`. That silently swapped
    the flags: a student who allowed the headband and declined the camera had
    `face_signals` read for them. Fields cannot be transposed by accident.

    `consent_retrieved` is carried for the same reason `_consent` carries it --
    "nobody consented" and "we could not find out" both yield False here, and
    the reporting rules require a surface to tell them apart before it says a
    channel was not requested.
    """
    heart: bool
    emotion: bool
    consent_retrieved: bool


def _reportable_channels(student_id: str, want_emotion: bool = True,
                         want_heart: bool = True) -> ReportChannels:
    """Which optional channels a report may read: consent AND what was asked for.

    Two different controls, composed rather than conflated. **Consent** decides
    whether a channel was ever recorded and is not the viewer's to override.
    The **request** flag is a viewer-side preference -- a teacher choosing not to
    look at facial data on a page -- documented at the top of
    `frontend/src/lib/facePref.js`. Either one being false means no read.

    Consent is authoritative and cannot be widened by a caller, which is why it
    is resolved here rather than trusted from a query parameter. A revoked
    channel has no rows to read anyway; gating the read as well means a stale
    row from before a withdrawal cannot surface in a report.

    Fails closed, like `_consent` itself: an unreadable consent row reports
    nothing rather than everything.
    """
    consent = _consent(student_id)
    heart = bool(consent.get("headband_optical_enabled")) or bool(consent.get("camera_enabled"))
    emotion = bool(consent.get("camera_enabled"))
    return ReportChannels(heart=want_heart and heart,
                          emotion=want_emotion and emotion,
                          consent_retrieved=bool(consent.get("retrieved")))


def _summary_rpc(name: str, params: dict, include_heart: bool, include_emotion: bool):
    """Call a summary RPC with the facial opt-out threaded in.

    This used to retry without p_include_face when the database had no matching
    signature, covering the window between deploying the code and applying
    20260801000000_signal_summary_include_face.sql. That migration is applied,
    so the branch was unreachable and is gone (#48).

    A schema mismatch is therefore an error again, which is the point: while the
    fallback existed, a database missing p_include_face for any *other* reason
    -- a bad rollback, an environment provisioned from an old dump -- degraded
    to a silently wrong answer instead of failing.
    """
    return supabase.rpc(name, {**params,
                               "p_include_heart": include_heart,
                               "p_include_emotion": include_emotion}).execute()


def _signal_summary(student_id: str, days: int = 7, include_heart: bool = True,
                    include_emotion: bool = True,
                    consent_retrieved: bool = True) -> dict:
    """Just the headline averages, aggregated in Postgres.

    The full report pulls thousands of raw signal rows to compute a handful of
    numbers, which is fine for one student on a detail page and wasteful on a
    list that loads every visit. This returns the same headline figures without
    transferring any rows -- see the student_signal_summary migration.

    Both flags are passed down into the aggregate, so a declined channel has no
    row read at all -- the same guarantee _weekly_signal_report makes, rather
    than a null applied on the way out.

    Carries dominant_emotion, which _signal_summaries does not: only the
    single-student RPC computes it, because only the surfaces that read one
    student at a time render it.
    """
    row = None
    retrieved = True
    try:
        res = _summary_rpc("student_signal_summary",
                           {"p_student_id": student_id, "p_days": days},
                           include_heart, include_emotion)
    except Exception as e:
        print(f"[signal_summary] {e}")
        # The figures below are about to become defaults rather than
        # measurements, and nothing downstream could tell the difference: the
        # endpoint answers 200 either way, so a caller saw zero samples and a
        # null average -- the same shape as a student who recorded nothing.
        # Surfaces then reported an absence in data that failed to load.
        retrieved = False
    else:
        rows = res.data or []
        row = rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else None)
    summary = _shape_summary(row, include_heart, include_emotion, retrieved,
                             consent_retrieved)
    # Set here rather than in _shape_summary, which is shared with the batch
    # RPC: putting it there would add an always-null dominant_emotion to every
    # child on the parent dashboard, reporting "no emotion recorded" for a
    # figure that was never asked for. Explicitly None with the opt-out on for
    # the same reason the SQL yields NULL there -- and on the deploy-window
    # fallback path, where the old signature has no such column at all.
    summary["dominant_emotion"] = (row or {}).get("dominant_emotion") if include_emotion else None
    return summary


_EMPTY_SUMMARY = {"consent_retrieved": True,
                  "focus": None, "stress": None, "engagement": None,
                  "face_attention": None, "heart_rate_bpm": None,
                  "rmssd_ms": None, "sessions": 0,
                  "cognitive_samples": 0, "face_samples": 0, "heart_samples": 0,
                  # `face_included` is kept alongside the two new flags rather
                  # than replaced. Existing consumers branch on it, and removing
                  # it would turn "the field is gone" into a falsy value at
                  # every one of them -- silently reporting a channel as
                  # excluded. It now means the emotion channel specifically.
                  "face_included": True, "emotion_included": True,
                  "heart_included": True, "retrieved": True}


def _shape_summary(row, include_heart: bool = True, include_emotion: bool = True,
                   retrieved: bool = True, consent_retrieved: bool = True) -> dict:
    """The summary payload.

    `retrieved` is the third thing a caller has to be able to tell apart, after
    "nothing was recorded" and "facial data was not requested": the aggregate
    query itself failed. Both helpers below swallow that exception so one
    broken read does not blank a dashboard -- but the payload they return is
    all defaults, and answered with a 200, so without this flag a zero sample
    count and a null average were indistinguishable from a quiet week. Every
    surface that renders "no data" has to consult it before saying so.

    True by default: it is the answer for every path that actually reached the
    database, including one that legitimately came back with no rows.
    """
    if not row:
        return {**_EMPTY_SUMMARY, "face_included": include_emotion,
                "emotion_included": include_emotion, "heart_included": include_heart,
                "retrieved": retrieved, "consent_retrieved": consent_retrieved}
    return {
        "focus": row.get("focus"),
        "stress": row.get("stress"),
        "engagement": row.get("engagement"),
        "face_attention": row.get("face_attention"),
        # Absolute units, unlike every other figure here, which are 0..1 ratios.
        # `toPct()` on the frontend must not be applied to them.
        "heart_rate_bpm": row.get("heart_rate_bpm"),
        "rmssd_ms": row.get("rmssd_ms"),
        "sessions": row.get("sessions") or 0,
        # Surfaced rather than dropped: an average of None next to a sample
        # count of 0 means "nothing recorded", while None next to a nonzero
        # count would mean "recorded but unusable". The SQL counts non-NULL
        # measurements specifically so that distinction holds.
        "cognitive_samples": row.get("cognitive_samples") or 0,
        "face_samples": row.get("face_samples") or 0,
        "heart_samples": row.get("heart_samples") or 0,
        # With a channel excluded its average is null and its count 0 --
        # identical to a student that sensor never read. Same distinction the
        # weekly report draws with its own flags.
        # Kept as an alias for emotion_included, and narrower than its name:
        # it means the *emotion* channel, not "anything facial". Consumers
        # branch on it, so removing it would turn "field absent" into a falsy
        # value at each of them. Deprecated -- read emotion_included in new
        # code, and do not fold a third channel into this one.
        "face_included": include_emotion,
        "emotion_included": include_emotion,
        "heart_included": include_heart,
        # A row got here, so the read succeeded by construction. Carried
        # anyway rather than hardcoded True, so the field is present on every
        # payload and a consumer never has to treat "absent" as a third state.
        "retrieved": retrieved,
        # `retrieved` is about the aggregate query; this is about the consent
        # read that decided which channels it could ask for. Both False means
        # the channel flags are "we could not find out" rather than "declined",
        # and the parent dashboard is the surface that renders that difference.
        "consent_retrieved": consent_retrieved,
    }


def _signal_summaries(student_ids: list[str], days: int = 7,
                      include_heart: bool = True,
                      include_emotion: bool = True) -> dict[str, dict] | None:
    """Headline averages for many students in one round-trip.

    The single-student RPC removes the row transfer but still costs one
    round-trip per child on a dashboard that loads every visit.

    None means the read failed, as opposed to {} for a call that succeeded and
    had nothing to return. The caller needs the difference: it fills in a
    default summary for any child missing from the result, and that default has
    to say whether it stands in for a failed query or for a child the aggregate
    genuinely reported nothing about. Returning {} for both had the dashboard
    tell a parent their child had recorded nothing whenever the RPC broke.
    """
    if not student_ids:
        return {}
    try:
        res = _summary_rpc("student_signal_summary_many",
                           {"p_student_ids": student_ids, "p_days": days},
                           include_heart, include_emotion)
    except Exception as e:
        print(f"[signal_summaries] {e}")
        return None
    rows = res.data or []
    if isinstance(rows, dict):
        rows = [rows]
    return {str(r.get("student_id")): _shape_summary(r, include_heart, include_emotion)
            for r in rows if r.get("student_id")}


def _weekly_signal_report(student_id: str, days: int = 7, include_heart: bool = True,
                          include_emotion: bool = True,
                          consent_retrieved: bool = True):
    """Aggregate a student's recent EEG and facial signals for reporting.

    Returns averages, highlights and per-day buckets. Callers must have already
    established that the requester may see this student.

    A false flag skips that channel's query outright rather than fetching and
    discarding: the point is that the data isn't read at all, and it also drops
    the heaviest of the queries. Every field from that channel then comes back
    None, and `heart_included` / `emotion_included` tell the caller that means
    "not requested" rather than "nothing recorded".
    """
    since = _iso_days_ago(days)

    def _fetch(table: str, ts_col: str, limit: int) -> tuple[list, bool, int | None, bool]:
        """Rows (newest first), whether the server withheld any, the total, and
        whether the read happened at all.

        Truncation is detected from an exact count rather than from
        len(rows) >= limit. PostgREST applies its own db-max-rows ceiling
        (commonly 1000) on top of our .limit(), so a smaller server cap would
        silently trim the result while len(rows) never reached _REPORT_ROW_CAP
        -- leaving truncated=False and the whole guard disabled. Comparing
        against the count the server reports works whichever limit binds.

        That count is worth returning as well as comparing: for sessions it is
        the figure the report is actually about, and it is exact whether or not
        the cap bound. None means the server reported no count.

        The last element is the same distinction the summary payloads draw with
        `retrieved`, reached the other way. This function swallows its exception
        so one broken table does not blank a report -- but the empty list it
        returns is indistinguishable from a student who recorded nothing, and
        every figure downstream is then a default presented as a measurement.
        The caller has to be told which it is holding.
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
            return rows, was_cut, total, True
        except Exception as e:
            print(f"[weekly_report:{table}] {e}")
            return [], False, None, False

    cog, cog_cut, _, cog_ok = _fetch("cognitive_signals", "ts", _REPORT_ROW_CAP)
    # ok=True with the opt-out on: nothing failed, there was simply nothing to
    # ask for. `face_included` is what says the query never ran, and conflating
    # the two would have the opt-out reported as a broken read.
    face, face_cut, _, face_ok = _fetch("face_signals", "ts", _REPORT_ROW_CAP) if include_emotion \
        else ([], False, None, True)
    heart, heart_cut, _, heart_ok = _fetch("heart_signals", "ts", _REPORT_ROW_CAP) if include_heart \
        else ([], False, None, True)
    # Truncation kept, not discarded. A student over the cap was shown a count
    # that silently stopped at it -- and with the other two tables under their
    # own cap, `truncated` stayed False and nothing said so.
    sessions, ses_cut, ses_total, ses_ok = _fetch("sessions", "started_at", _SESSION_ROW_CAP)

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

    truncated = cog_cut or face_cut or heart_cut or ses_cut
    cog_oldest_day = _oldest(cog, "ts")[:10]
    face_oldest_day = _oldest(face, "ts")[:10]
    ses_oldest_day = _oldest(sessions, "started_at")[:10]
    heart_oldest_day = _oldest(heart, "ts")[:10]

    latest_cognitive = cog[0] if cog else None
    latest_face = face[0] if face else None
    # Newest *trusted* reading, matching every other heart figure in this
    # payload. An untrusted latest would be the one number here not subject to
    # the quality gate, and it is the one rendered largest.
    latest_heart = next((r for r in heart if r.get("trusted") is True), None)

    # Three states per table per day, not two. The cap trims oldest-first, so
    # the oldest day that came back is the day it cut *into*: part of that day
    # is here and the rest is not.
    #
    # Calling that day retrieved published a figure computed from whatever
    # fraction survived -- a biased average, and for sessions a flat undercount
    # -- with nothing to distinguish it from an exact one. Calling it absent
    # would throw the day away instead. It is neither, so the value is withheld
    # and the retrieved flag says so, while the day stays in the series because
    # something was read for it.
    #
    # A cap that happened to stop exactly on a day boundary leaves an oldest
    # day that really is complete, and that is not distinguishable from here
    # without another query. It resolves the conservative way: a complete day
    # understated as partial, rather than a partial day published as complete.
    #
    # A failed query is the fourth state and the flattest of them: the table was
    # not read for any day in the range, so every day is missing rather than
    # merely trimmed. It has to be judged here rather than left to the cap
    # logic, which sees only an empty result and a truncation flag of False --
    # and would therefore call every day complete and publish the resulting
    # empty averages and zero session counts as measurements.
    def _coverage(ok: bool, cut: bool, oldest_day: str, day: str) -> tuple[bool, bool]:
        """(nothing was retrieved for this day, this day is complete)."""
        if not ok:
            return True, False          # the read failed; no day was covered
        if not cut:
            return False, True          # nothing was trimmed, so every day is whole
        if not oldest_day:
            # Trimmed, and nothing came back to say how far it reached -- a
            # server cap of zero, or rows carrying no usable timestamp. Folded
            # into "nothing was trimmed" before, which called every day whole
            # and published the resulting empty averages as measurements, on
            # the one input that says the least about what was retrieved.
            return True, False
        if day < oldest_day:
            return True, False          # the cap stopped before this day entirely
        return False, day > oldest_day  # == oldest_day is the day it cut into

    daily = []
    for i in range(days - 1, -1, -1):
        day = (_utc_now() - timedelta(days=i)).date().isoformat()
        cog_missing, cog_whole = _coverage(cog_ok, cog_cut, cog_oldest_day, day)
        face_missing, face_whole = _coverage(face_ok, face_cut, face_oldest_day, day)
        ses_missing, ses_whole = _coverage(ses_ok, ses_cut, ses_oldest_day, day)
        # Per day, like the three above. `heart_ok and not heart_cut` was
        # table-wide: one heart read over the row cap marked *every* day
        # unretrieved, so the chart drew nothing even for days that came back
        # complete. The cap binds per table, so the coverage has to be per day.
        heart_missing, heart_whole = _coverage(heart_ok, heart_cut, heart_oldest_day, day)
        # Skip only when nothing we actually asked for could be retrieved. With
        # face reporting off there is no face request to fail, so the day hinges
        # on the other two -- otherwise an always-False face_missing would keep
        # days that hold no retrievable data at all.
        #
        # Sessions count here too: they come from their own query under its own
        # cap, so a day whose signals were trimmed can still have a session
        # count that was retrieved intact. Dropping the day threw that away and
        # reported the day as absent rather than partial.
        # Heart included, or a day whose cognitive and session reads failed but
        # whose heart read succeeded is dropped from `daily` entirely -- and the
        # heart data that *was* retrieved never reaches the chart.
        if (cog_missing and (face_missing or not include_emotion)
                and (heart_missing or not include_heart) and ses_missing):
            continue
        day_cog = [r for r in cog if str(r.get("ts", ""))[:10] == day]
        day_face = [r for r in face if str(r.get("ts", ""))[:10] == day]
        # Trusted only, matching the week's averages. A day whose every sample
        # was rejected is then a null beside a `heart_retrieved` of True --
        # "measured, unusable" -- rather than a gap that reads as sensor-off.
        day_heart = [r for r in heart
                     if str(r.get("ts", ""))[:10] == day and r.get("trusted") is True]

        daily.append({
            "date": day,
            # Withheld unless the day is whole. A partly-retrieved day averages
            # only the fraction that survived the cap, which reads exactly like
            # a measurement of the whole day.
            "focus": _avg([r.get("focus") for r in day_cog]) if cog_whole else None,
            "stress": _avg([r.get("stress") for r in day_cog]) if cog_whole else None,
            "engagement": _avg([r.get("engagement") for r in day_cog]) if cog_whole else None,
            "attention": _avg([r.get("attention") for r in day_face]) if face_whole else None,
            # None rather than 0, on the same reasoning as the metrics above: a
            # day the cap kept us from reading did not have zero sessions, and
            # `sessions_retrieved` is what tells the two apart. A count is the
            # clearest case for withholding a partial day -- half a day's rows
            # give exactly half the sessions, with no hint that it is half.
            "sessions": len([r for r in sessions if str(r.get("started_at", ""))[:10] == day])
                        if ses_whole else None,
            # **Absolute units**, unlike every other series here, which are 0..1
            # ratios rendered as percentages. A consumer applying the same
            # scaling to these would draw a 72 bpm day at 7200% -- so they are
            # named for the unit and the frontend gives them their own axis.
            "heart_rate_bpm": _avg([r.get("heart_rate_bpm") for r in day_heart])
                              if heart_whole else None,
            "rmssd_ms": _avg([r.get("rmssd_ms") for r in day_heart])
                        if heart_whole else None,
            # False means "we did not fetch this day in full", which a null
            # metric alone cannot distinguish from "nothing was recorded". It
            # covers the days the cap never reached, the single day it cut
            # into, and every day of a table whose query failed outright.
            # None means "not requested" -- face reporting is off, so there was
            # no retrieval to succeed or fail, and the consumers that count
            # `=== false` must not treat the opt-out as a retrieval failure.
            "cognitive_retrieved": cog_whole,
            "face_retrieved": face_whole if include_emotion else None,
            "heart_retrieved": heart_whole if include_heart else None,
            "sessions_retrieved": ses_whole,
        })

    # Only trusted heart samples are averaged. An untrusted one carries a rate;
    # it is just not one worth putting in front of a parent, and the same rule
    # runs in the SQL aggregate so the two surfaces cannot disagree.
    heart_rates = [r["heart_rate_bpm"] for r in heart
                   if r.get("heart_rate_bpm") is not None and r.get("trusted") is True]
    rmssd_values = [r["rmssd_ms"] for r in heart
                    if r.get("rmssd_ms") is not None and r.get("trusted") is True]
    # Which sensor produced them, so a reader can tell a headband week from a
    # camera week -- accuracy differs materially between sources, and after
    # Phase 4 only the headband is validated at all.
    # Trusted rows only, matching the averages. Listing a source whose every
    # sample was rejected renders as "the headband was on and measured nothing"
    # beside a null average -- the "measured, unusable" state claiming to be a
    # working sensor.
    heart_sources = sorted({r["source"] for r in heart
                            if r.get("source") and r.get("trusted") is True})

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
    else:
        # An absence is only assertable about a table that was actually read.
        # A failed query leaves exactly the empty result a quiet week does, so
        # without splitting these the sentence reported "nothing was recorded"
        # on the strength of a query that never returned -- the same mistake
        # the opt-out branch below was written to avoid, reached by a different
        # route. Each table lands in one list or the other, and only the read
        # ones get an absence claimed about them.
        measured, unread = [], []
        (measured if cog_ok else unread).append("EEG")
        # Naming facial recognition at all would report on something that was
        # never measured, since the caller opted out of reading it.
        if include_emotion:
            (measured if face_ok else unread).append("facial recognition")
        if include_heart:
            (measured if heart_ok else unread).append("heart rate")

        def _join(items: list[str], conjunction: str) -> str:
            # "a, b or c" rather than "a or b or c", which reads badly once a
            # third channel exists. Takes the conjunction because the two
            # sentences want different ones -- "no X or Y were recorded" against
            # "X and Y could not be loaded" -- and having the logic twice meant
            # the second copy did not get the comma fix.
            if len(items) <= 1:
                return "".join(items)
            return ", ".join(items[:-1]) + f" {conjunction} " + items[-1]

        def _sentence_case(text: str) -> str:
            # Not .capitalize(), which lowercases the rest and turns "EEG" into
            # "Eeg".
            return text[:1].upper() + text[1:]

        parts = []
        if measured:
            parts.append(f"No {_join(measured, 'or')} samples were recorded this week.")
        if unread:
            parts.append(_sentence_case(
                f"{_join(unread, 'and')} data could not be loaded."))
        summary = " ".join(parts)

    return {
        "student_id": student_id,
        "days": days,
        "since": since,
        "truncated": truncated,
        # Distinguishes "facial reporting is switched off for this view" from
        # "the camera recorded nothing". Both leave every face field null.
        #
        # `face_included` is kept as an alias for `emotion_included` and is
        # narrower than its name: it means the *emotion* channel, not "anything
        # facial". Consumers branch on it, so removing it would turn "field
        # absent" into a falsy value at each of them. Deprecated -- read
        # emotion_included in new code, and do not fold a third channel into it.
        "face_included": include_emotion,
        "emotion_included": include_emotion,
        "heart_included": include_heart,
        # False means the two flags above are "we could not find out", not "the
        # student declined". Both suppress every optional channel and only one
        # is a fault; a surface saying "not requested" about the first would be
        # reporting a database outage as a preference.
        "consent_retrieved": consent_retrieved,
        # The tally, not just its argmax. Nothing on the frontend could render a
        # pie before this: emotion reached it only as a scalar `dominant_emotion`,
        # so a distribution had to be recomputed from raw rows or not shown. The
        # loop above already counts these to pick the dominant one.
        "emotion_distribution": (dict(sorted(emotion_counts.items(),
                                             key=lambda kv: (-kv[1], kv[0])))
                                 if include_emotion else None),
        "heart_sources": heart_sources if include_heart else None,
        # Which of the three reads actually happened. Everything else in this
        # payload is a figure computed from whatever came back, and a query that
        # failed contributes the same empty rows as a student who recorded
        # nothing -- so a null average, a zero sample count and an absent day
        # are all ambiguous without this. Per table, because the reads fail
        # independently and one broken table should not retract the other two.
        #
        # face is None with the opt-out on, matching the per-day
        # `face_retrieved`: there was no retrieval to succeed or fail, and a
        # consumer checking `is False` must not read the opt-out as a failure.
        "retrieved": {
            "cognitive": cog_ok,
            "face": face_ok if include_emotion else None,
            "heart": heart_ok if include_heart else None,
            "sessions": ses_ok,
        },
        "sample_counts": {"cognitive": len(cog), "face": len(face),
                          # Rows retrieved, not rows averaged. A week of nothing
                          # but untrusted samples is then a nonzero count beside
                          # a null average -- "measured, unusable" -- rather than
                          # indistinguishable from a week the sensor was off.
                          "heart": len(heart), "sessions": len(sessions)},
        # How many sessions there *were*, as opposed to how many rows came back
        # under _SESSION_ROW_CAP. sample_counts is rows-retrieved throughout, so
        # rendering its sessions figure as the report's headline showed a heavy
        # week as exactly the cap -- while the parent dashboard, which counts in
        # Postgres, showed the real number for the same child and week. Falls
        # back to the row count when the server reported no exact count, which
        # is the same fallback the truncation check makes.
        #
        # None when the read failed, rather than the len() of the empty list it
        # returned: "0 sessions this week" is a claim, and there is nothing
        # behind it on that path. `retrieved.sessions` says which it is, and the
        # panel renders the null as a dash.
        "sessions_recorded": (ses_total if ses_total is not None else len(sessions)) if ses_ok else None,
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
            "heart_rate_bpm": (sum(heart_rates) / len(heart_rates)) if heart_rates else None,
            "rmssd_ms": (sum(rmssd_values) / len(rmssd_values)) if rmssd_values else None,
        },
        # `heart` present only when the channel was read. Absent rather than
        # null, so a snapshot cannot show an empty heart row that reads as a
        # sensor recording nothing -- the same rule the tiles follow.
        "latest": {"cognitive": latest_cognitive, "face": latest_face,
                   **({"heart": latest_heart} if include_heart else {})},
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

    include_face=false omits facial-recognition data from the report entirely.
    It is a viewer preference and narrows only: stored consent decides what may
    be read at all, and a caller cannot widen past it -- see
    `_reportable_channels`. The parameter keeps its name because the frontend
    control and its localStorage key still use it;
    see _weekly_signal_report.
    """
    _verify_can_view_student(get_user(request), student_id)
    p = _profile(student_id)
    channels = _reportable_channels(student_id, include_face)
    return {
        "student_name": p.get("display_name") or p.get("email") or "Student",
        **_weekly_signal_report(student_id, max(1, min(days, 30)),
                                include_heart=channels.heart,
                                include_emotion=channels.emotion,
                                consent_retrieved=channels.consent_retrieved),
    }


@app.get("/api/students/{student_id}/signal-summary")
def student_signal_summary(student_id: str, request: Request, days: int = 7, include_face: bool = True):
    """Headline signal averages for a student, aggregated in Postgres.

    Role-neutral, like the weekly report: gated on the viewer's relationship to
    the student rather than on a role claim.

    Exists because the teacher student list needs these four averages and
    cannot get them honestly from the browser client. It used to read
    cognitive_signals and face_signals directly under a 200-row cap. At the
    poller's default 1 Hz that cap binds after about three minutes, so the
    tiles labelled "last 7d" were in fact averaging the newest three minutes of
    a single sitting -- and the sample counts, pinned at exactly 200, were
    presented as a count of the week. A teacher and a parent looking at the
    same student that week saw different numbers, which is precisely what
    matching the window was meant to prevent.

    Raising the cap is not the fix: seven days at 1 Hz is upwards of half a
    million rows per student. The aggregate answers in one round-trip and
    transfers no rows, which is what _signal_summary was added for.

    The summary RPC is granted to service_role only, so this has to be a
    server-side endpoint rather than an rpc() call from the browser. That gives
    up nothing: _can_view_student's teacher branch -- owns a class the student
    is a member of -- is the same relationship the "cog: teacher read" and
    "face: teacher read" RLS policies encode, so the scope is unchanged.
    """
    _verify_can_view_student(get_user(request), student_id)
    channels = _reportable_channels(student_id, include_face)
    return _signal_summary(student_id, max(1, min(days, 30)),
                           include_heart=channels.heart,
                           include_emotion=channels.emotion,
                           consent_retrieved=channels.consent_retrieved)


@app.get("/api/students/{student_id}/topic-breakdown")
def student_topic_breakdown(student_id: str, request: Request):
    _verify_can_view_student(get_user(request), student_id)
    return _topic_breakdown(student_id)


# ─── at-home learning strategies ─────────────────────────────────────────

def _env_number(name: str, default, cast, minimum=None):
    """A numeric setting from the environment, falling back on a bad value.

    These are read at import, so a typo in a deployment's environment would
    otherwise raise ValueError before the app object exists -- taking down every
    endpoint over a tuning parameter for one optional feature. Falling back to
    the shipped default keeps the process up, and the log line is what says the
    setting is not the one that was configured.

    `minimum` extends that to values that parse but are not usable. A number is
    not automatically a setting: every caller here has a floor below which the
    value does not tune the feature but disables or breaks it, quietly and in
    whichever direction the parameter happens to point -- see the call sites.
    Clamping rather than falling back to the default, because a deployer who
    wrote a small number was asking for a small number, and the nearest usable
    one is closer to that than the shipped value is.

    The non-finite check is separate from both, and falls back rather than
    clamping. "inf" and "nan" parse cleanly under float() and pass a `minimum`
    comparison -- inf because it is above every floor, nan because every
    comparison against it is False -- so neither of the guards above sees them,
    and they are not magnitudes there is a nearest usable value to clamp to.
    They reach the call sites as settings that break rather than tune:
    STRATEGY_RATE_WINDOW=inf makes the 429 path compute int(inf) and raise
    OverflowError, turning a rate limit into a 500, and STRATEGY_LLM_TIMEOUT=nan
    makes future.result() time out instantly, switching the model pass off for
    good while STRATEGY_LLM_ENABLED still says it is on. int() rejects both at
    the cast, so this only bites the float callers.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = cast(raw)
    except (TypeError, ValueError):
        print(f"[config] {name}={raw!r} is not a number; using {default}")
        return default
    if not math.isfinite(value):
        print(f"[config] {name}={raw!r} is not a finite number; using {default}")
        return default
    if minimum is not None and value < minimum:
        print(f"[config] {name}={raw!r} is below the usable minimum; using {minimum}")
        return minimum
    return value


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
#
# Floored: at zero or below, future.result() times out before the model can
# answer at all, so the pass is permanently switched off while STRATEGY_LLM_
# ENABLED still says it is on -- and the only symptom is every reply being
# "rule-based (model output rejected)".
STRATEGY_LLM_TIMEOUT = _env_number("STRATEGY_LLM_TIMEOUT", 20.0, float, minimum=1.0)

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
#
# None until the first model call. Built on demand rather than at import
# because the model pass is opt-in and off by default: CI, and any deployment
# without a local Ollama, would otherwise carry an executor nothing ever
# submits to. Tests substitute this global directly, so _strategy_pool hands
# back whatever is already here rather than insisting on building it itself.
_STRATEGY_LLM_POOL: ThreadPoolExecutor | None = None
_strategy_pool_lock = threading.Lock()


def _strategy_pool() -> ThreadPoolExecutor:
    """The model-call pool, created on first use.

    Locked, because two requests can reach a cold pool at once and the loser of
    that race would otherwise hand back an executor that is not the one in the
    global -- leaving its worker outside the max_workers ceiling and outside
    the shutdown below.
    """
    global _STRATEGY_LLM_POOL
    with _strategy_pool_lock:
        if _STRATEGY_LLM_POOL is None:
            _STRATEGY_LLM_POOL = ThreadPoolExecutor(max_workers=2,
                                                    thread_name_prefix="strategy-llm")
        return _STRATEGY_LLM_POOL


def _shutdown_strategy_pool():
    """Drop the queue on the way out. Called from _lifespan.

    wait=False and cancel_futures=True rather than a clean join: the whole
    point of the pool is that a worker can be stuck in a socket read against a
    stalled Ollama for as long as the client timeout allows, and nobody is
    waiting on that answer by the time the process is going down. Cancelling
    discards the queued work; the running worker cannot be interrupted, and the
    interpreter's own atexit join is what eventually collects it.

    The global goes back to None so a pool shut down here is never handed to a
    later caller -- submit() on a shut-down executor raises, which
    _llm_strategies_bounded degrades to the rule-based answer, but a reload in
    the same process should get a working pool rather than that fallback.
    """
    global _STRATEGY_LLM_POOL
    with _strategy_pool_lock:
        pool, _STRATEGY_LLM_POOL = _STRATEGY_LLM_POOL, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


# How many requests may be *waiting* on the model at once, process-wide.
#
# Distinct from max_workers, which bounds the threads doing the generating.
# This bounds the callers blocked on the result -- and those are the scarce
# resource, because this endpoint is a sync def. FastAPI runs sync endpoints on
# anyio's threadpool (40 slots by default), so every request sitting in
# future.result() holds one of those slots for up to STRATEGY_LLM_TIMEOUT
# seconds. The per-caller rate limit does not help here: it is per user id, so
# ~40 distinct parents clicking Generate against a stalled Ollama could occupy
# the entire threadpool for 20s at a time -- taking down every other sync
# endpoint in the app, none of which has anything to do with this feature.
#
# Past the cap the model pass is skipped rather than queued. That is not a
# degradation the caller has to be protected from: the rule-based list is the
# guaranteed answer and always the fallback, so the cost of being over the cap
# is generic advice instead of tuned advice, which is exactly what a rejected
# or timed-out model reply already costs.
#
# Floored at 1: at zero or below the semaphore never admits anyone, so the
# model pass would be permanently off while STRATEGY_LLM_ENABLED says it is on
# -- the silent-disable failure _env_number's minimum exists to prevent.
_STRATEGY_LLM_MAX_WAITERS = _env_number("STRATEGY_LLM_MAX_WAITERS", 4, int, minimum=1)
_strategy_llm_waiters = threading.BoundedSemaphore(_STRATEGY_LLM_MAX_WAITERS)


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
#
# Both floored, and they fail in opposite directions. A limit of zero or less
# makes `len(hits) >= limit` true on the first request, so every caller gets
# 429 and the feature is bricked over a tuning parameter -- the failure mode
# _env_number exists to prevent for a typo, reached by a value that parses. A
# window of zero or less makes every recorded hit already expired, so nothing
# is ever counted and the ceiling silently is not there. Neither is a way to
# turn the limiter off: there is no such setting, and if one is ever wanted it
# should be explicit rather than an out-of-range number.
_STRATEGY_RATE_LIMIT  = _env_number("STRATEGY_RATE_LIMIT", 10, int, minimum=1)

# Ingestion is the trust boundary, so it is bounded on both axes.
#
# The local sidecar POSTs these with the student's own bearer token, which means
# the client is not trusted: a compromised or merely buggy process on a
# student's laptop must not be able to flood the table. `_verify_session_owner`
# answers "whose session" and the consent check answers "may this be recorded";
# neither bounds volume, and volume is its own denial-of-service.
# The two bounds multiply, and the product is the number worth knowing: at the
# defaults a student may post 120 batches of 500 a minute, so **60,000 samples
# per minute**. Against a 1 Hz producer that is roughly a thousandfold of
# headroom, which is deliberate -- the limit is sized to stop a runaway loop or
# a hostile client, not to police a working sensor, and a legitimate backlog
# flush after a dropped connection has to fit through it. Tighten the batch size
# before the rate if that ever needs revisiting: a smaller batch costs a
# well-behaved client nothing but round trips.
_INGEST_MAX_BATCH   = _env_number("INGEST_MAX_BATCH", 500, int, minimum=1)
_INGEST_RATE_LIMIT  = _env_number("INGEST_RATE_LIMIT", 120, int, minimum=1)
_INGEST_RATE_WINDOW = _env_number("INGEST_RATE_WINDOW", 60.0, float, minimum=1.0)

_ingest_hits: dict[str, list[float]] = {}
_ingest_hits_lock = threading.Lock()
# Same sweep as the strategy limiter, and needed more here: entries are
# otherwise pruned only on that caller's *next* request, so every student who
# posts once and stops leaves a list behind for the process lifetime. Ingest is
# the higher-volume endpoint, so it accumulates callers fastest -- this was the
# one place without eviction, which is the wrong way round.
_ingest_sweep_at = time.monotonic()
_INGEST_SWEEP_EVERY = 60.0
_INGEST_SWEEP_ABOVE = 1024

# Which heart sources each consent flag permits. One flag per *sensor*, so a
# student who allowed the headband and declined the camera has consented to
# muse_optics and muse_ppg and not to rppg. Rejecting per source rather than
# per channel is the entire reason heart_signals carries `source` at all.
_HEART_SOURCES_BY_CONSENT = {
    "headband_optical_enabled": ("muse_optics", "muse_ppg"),
    "camera_enabled":           ("rppg",),
}

_STRATEGY_RATE_WINDOW = _env_number("STRATEGY_RATE_WINDOW", 60.0, float, minimum=1.0)
_strategy_hits: dict[str, list[float]] = {}
_strategy_hits_lock = threading.Lock()
# When the sweep below last ran. Size alone was not a sufficient trigger: past
# the threshold with that many *active* callers, every request scanned the
# whole dict and deleted nothing, holding the lock to do it. Pairing size with
# an interval keeps the sweep proportional to time rather than to traffic.
#
# Seeded from the clock the comparison uses, NOT from 0.0. time.monotonic()'s
# reference point is undefined -- on Linux it is boot time -- so 0.0 is not a
# "never swept" sentinel, it is a claim that the last sweep happened at boot.
# On a host less than _STRATEGY_SWEEP_EVERY seconds old, `now - 0.0 >=
# _STRATEGY_SWEEP_EVERY` is False, and the sweep is suppressed until the
# machine has been up for the interval -- the reclaim silently not running
# during exactly the window a fresh container spends starting up.
#
# It also made the sweep tests depend on the runner's uptime rather than on
# anything they assert: green on a workstation up for days, red on a fresh CI
# runner where monotonic() had only reached ~56s. Seeding here makes the
# interval mean "since the last sweep, or since this process started", which is
# what it was always meant to mean, on any host.
_strategy_sweep_at = time.monotonic()
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
#
# Each term is stemmed only as far as its clinical sense reaches. The filter
# rejects the whole reply, so an over-broad stem does not merely trim a word --
# it silently switches the model pass off for every reply containing an
# ordinary one, and the only symptom is `source` permanently reading
# "rule-based (model output rejected)". Over-blocking and a genuinely unsafe
# model are indistinguishable from outside, which is why the stems below are
# written narrowly rather than defensively:
#
#   - "patient" is matched only in the forms that are unambiguously the *noun*:
#     the plural, and the singular behind a determiner. `patient\w*` caught the
#     adjective and "patiently"; even `patients?` still catches the adjective in
#     "be patient when they get stuck" -- which is about as likely a sentence as
#     exists in advice on helping a child with maths. The adjective is the
#     common reading here and the noun is the clinical one, so the pattern has
#     to tell them apart rather than stem across both. ("patience" was never
#     caught -- it has no "t" after "patien" -- but "patiently" and the bare
#     adjective both were.)
#
#     What this gives up: a bare predicative noun ("they are not patient" in
#     the clinical sense) is not caught. That reading is vanishingly rare in
#     study advice, and a reply actually framing a child as a medical patient
#     will almost certainly trip one of the other terms in the same sentence.
#     Over-blocking every "be patient" is the worse trade, because it is silent.
#   - `meds` and `treatment\w*` are kept as they were -- unlike the adjective
#     "patient", both carry the clinical sense in every reading that fits this
#     prompt, so there is no ordinary use here for a narrower stem to protect.
_CLINICAL_TERMS = re.compile(
    r"\b(diagnos\w*|disorder\w*|disabilit\w*|adhd|autis\w*|dyslex\w*|dyscalcul\w*|"
    r"depress(?:ion|ive)|anxiet\w*|anxious|medicat\w*|meds|prescri\w*|psychiatr\w*|"
    r"psycholog\w*|counsel\w*|clinical\w*|symptom\w*|disease\w*|syndrome\w*|"
    r"patients|(?:a|an|the|any|your|their)\s+patient|"
    r"therap(?:y|ist|ies)|treatment\w*|neurolog\w*|"
    r"cognitive impairment|special (?:needs|education\w*)|iep)\b",
    re.IGNORECASE,
)

# Leading "1.", "2)", "-", "*", "•" from a numbered or bulleted model reply.
_LIST_MARKER = re.compile(r"^\s*(?:\d+\s*[\).:]|[-*•])\s*")

# Markdown emphasis a model wraps an item in ("1. **Keep sessions short**").
# Stripped rather than left alone: nothing renders markdown between here and
# the parent, so the asterisks would reach them as literal punctuation.
#
# Both delimiters need a word-boundary guard, for the same reason: the pattern
# deletes its delimiters rather than spacing them, so a pair that was never
# emphasis in the first place fuses the text around it into a garbled word --
# well formed, the right length, and past every other check on its way to a
# parent.
#
# Underscores: an item naming topics in the form the tables store them came out
# as "review anglerelationships and meanmedian" from "review
# angle_relationships and mean_median".
#
# Asterisks: not "they do not occur inside words" -- they do not occur inside
# *words*, but this is a maths app and "*" is the multiplication sign. Two
# products in one line pair up exactly as two snake_case terms did: "practise
# 7*8 and 9*6" became "practise 78 and 96". CommonMark does allow intraword "*"
# emphasis, so the unguarded pattern was spec-faithful -- but spec-fidelity is
# not what this is for, and a model writing arithmetic here is at least as
# likely as one writing two topic ids.
#
# Genuine emphasis is unaffected in both cases: the delimiters of "**Keep
# sessions short**" and "_problem felt hardest_" are flanked by whitespace or
# the line ends, and a bolded whole item ("**2. Take a break**") still unwraps
# before the list marker is read.
_MD_ASTERISK = re.compile(r"(?<![\w*])(\*{1,3})(?=\S)(.+?)(?<=\S)\1(?![\w*])")
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
    channels = _reportable_channels(student_id, include_face)
    summary = _signal_summary(student_id, days, include_heart=channels.heart,
                              include_emotion=channels.emotion,
                              consent_retrieved=channels.consent_retrieved)
    return {
        "days": days,
        "face_included": summary["face_included"],
        # Carried through to `basis` in the response. A failed aggregate leaves
        # every average None, which the rules below already read as "no signal
        # to act on" -- so the advice degrades to the generic list rather than
        # being tuned to zeros, which is the right outcome. What was missing is
        # saying so: without this, a caller comparing `basis.averages` against
        # the week could not tell advice built from a quiet week from advice
        # built from a query that never ran.
        #
        # Named signals_retrieved, NOT `retrieved`, even though that is the key
        # the summary payload uses. This dict is deliberately shaped like a
        # weekly report -- _rule_based_strategies and _strategy_prompt read
        # report keys and are called on both -- and a real report's `retrieved`
        # is a dict of three per-table booleans, not one. Two shapes under one
        # key across two payloads with shared consumers is a wrong answer
        # waiting for the first caller to write
        # `report.get("retrieved", {}).get("cognitive")`. The response field
        # below has always been called signals_retrieved; this just stops the
        # collision existing internally as well.
        "signals_retrieved": summary["retrieved"],
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


def _llm_strategies(prompt: str, timeout: float | None = None) -> list[str] | None:
    """One local-model attempt, or None on any failure.

    ollama is imported here rather than at module scope so this module stays
    importable — and the endpoint stays usable on the rule-based path — in an
    environment where the package is missing. requirements.txt pins it, so that
    is a slimmed-down or partially-installed deployment rather than the normal
    case; the routine failure this guards is the *server* being absent, which
    surfaces as an exception from the call below.

    Goes through an explicit Client so the call carries a timeout at all. The
    module-level ollama.generate() has no deadline, and a server that accepts
    the connection and then stalls never raises — so the promise that any
    failure is a fallback rather than a 500 only holds with one attached.

    `timeout` is what remains of the caller's budget, which is not the same as
    the budget itself once a submission has queued: the work item was created
    when the caller started waiting, but it starts running whenever a worker
    frees up. Charging it the full STRATEGY_LLM_TIMEOUT from there let it hold
    a worker for nearly twice the setting, against a deadline the caller had
    already given up on. Defaults to the full budget for a direct call, which
    is not queued behind anything.
    """
    try:
        from ollama import Client
        resp = Client(timeout=STRATEGY_LLM_TIMEOUT if timeout is None else timeout).generate(
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
    """_llm_strategies under a deadline the caller actually feels, if admitted.

    Admission first. See _STRATEGY_LLM_MAX_WAITERS: this endpoint is a sync
    def, so a caller blocked on the model is holding one of anyio's threadpool
    slots, and those are shared with every other sync endpoint in the app.
    Bounding the workers and bounding the pool's queue -- both of which the
    deadline logic below already does -- do not bound the *waiters*, and the
    waiters are what the rest of the app contends with.
    """
    # Non-blocking: a caller who cannot get in must not queue on the semaphore
    # too, which would reintroduce the wait this cap exists to prevent -- just
    # one lock deeper, and without a deadline on it.
    if not _strategy_llm_waiters.acquire(blocking=False):
        # Not an error, and deliberately not raised to the caller: the
        # rule-based list is the guaranteed answer, so being over the cap costs
        # generic advice rather than tuned advice. `source` reports it as
        # "rule-based (model output rejected)" alongside a rejected reply,
        # which is the same thing from the reader's point of view -- the model
        # did not produce a usable answer for this request.
        print(f"[learning_strategies:llm] at capacity "
              f"({_STRATEGY_LLM_MAX_WAITERS} in flight); using the rule-based answer")
        return None
    try:
        return _llm_strategies_admitted(prompt)
    finally:
        _strategy_llm_waiters.release()


def _llm_strategies_admitted(prompt: str) -> list[str] | None:
    """The wait itself, once _llm_strategies_bounded has admitted the caller.

    Split out so the semaphore's release is a plain finally around a single
    call rather than wrapping every return path here.

    See _STRATEGY_LLM_POOL: the client-side timeout is per operation, so this
    is what holds when the transport keeps resetting it. Abandoning the wait
    leaves a *started* worker running -- there is no way to interrupt a blocking
    socket read -- but the caller is no longer behind it, and the answer they
    get is the rule-based one that was always the fallback.

    One deadline, shared by the wait and the work. The two used to be the same
    *duration* measured from different moments: a submission that queued behind
    a busy worker spent most of the caller's budget waiting, then started with
    a fresh STRATEGY_LLM_TIMEOUT of its own -- so the pool could stay saturated
    for close to twice the configured setting, generating against a deadline
    nobody was waiting on any more.
    """
    deadline = time.monotonic() + STRATEGY_LLM_TIMEOUT

    def _run():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # Started after the caller gave up. Nothing this could return would
            # be used, so it does not open a socket at all -- the cancel below
            # catches the queued items, and this catches the one that had
            # already been handed to a worker.
            return None
        return _llm_strategies(prompt, remaining)

    future = None
    try:
        # Inside the try: submit() itself raises if the pool is shutting down,
        # which the handler below turns into the rule-based fallback.
        future = _strategy_pool().submit(_run)
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
            # False means the averages beside it are defaults, not a quiet
            # week, so these strategies are the generic list rather than one
            # tuned to the student. Same distinction the summary endpoint
            # makes; stated here because this response is what a parent acts
            # on.
            "signals_retrieved": report.get("signals_retrieved", True),
            "averages": report.get("averages") or {},
            # Named fields rather than the whole _topic_breakdown row, which
            # also carries topic_id, a stress reading and updated_at — none of
            # which this response is about, and all of which would become part
            # of its contract by accident.
            "weakest_topic": _weakest_topic_summary(topics),
        },
    }


# ─── leaderboard ─────────────────────────────────────────────────────────

_LEADERBOARD_MAX = 100


@app.get("/api/leaderboard")
def leaderboard(request: Request, limit: int = 20):
    """Top students by correct answers.

    This reads through the service-role client, so RLS does not apply and the
    caller's `limit` is the only thing bounding how much of the user base comes
    back -- with names attached. Unclamped, /api/leaderboard?limit=999999 was a
    full directory of every user and their activity, for any signed-in caller.

    user_id is resolved but never returned. The page only needs to know which
    row is the viewer's own, and handing out a UUID -> display_name map for
    everyone on the board is what turned the (separately fixed) open read on
    user_stats into something that could be tied back to named students.
    """
    user = get_user(request)
    res = supabase.table("user_stats") \
        .select("user_id, total_correct, total_questions, current_streak, best_streak") \
        .order("total_correct", desc=True).limit(max(1, min(limit, _LEADERBOARD_MAX))).execute()
    rows = res.data or []
    enriched = []
    for i, row in enumerate(rows):
        uid = row.pop("user_id", None)
        p = _profile(uid)
        enriched.append({
            **row,
            "display_name": p.get("display_name") or "Student",
            "rank": i + 1,
            "is_me": uid == user["id"],
        })
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

@app.get("/api/classes/{class_id}")
def get_class(class_id: str, request: Request):
    """One class, for the pages that need its name and join code.

    Owner-only, like the roster and live endpoints next to it -- this reads
    through the service-role client, so _verify_class_owner is the whole check.
    """
    user = get_user(request)
    _verify_class_owner(class_id, user["id"])
    # Named columns, not "*": a column added to classes later should not start
    # reaching the browser because this line never mentioned it. Costs a second
    # read of the row the helper already fetched -- the helper is the shared
    # rule for who may see a class, and re-deriving it inline to save a
    # round-trip is how the class_live guard drifted.
    res = supabase.table("classes") \
        .select("id, name, join_code, grade_level") \
        .eq("id", class_id).single().execute()
    return res.data

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


# ─── consent: what may be recorded, per student ───────────────────────────
#
# Three channels, named for the sensor rather than the signal derived from it.
# `camera` covers expression AND the rPPG heart-rate fallback: one device, one
# decision, so a heart-rate failover can never quietly open a webcam the student
# declined.
#
# Everything is off until a linked parent turns it on, and this is the only
# write path -- signal_consent has no insert/update RLS policy for anyone, so
# the anon key in the frontend bundle cannot reach it through PostgREST. The
# rules below are therefore the enforcement, not a convenience layer over it.

CONSENT_CHANNELS = ("eeg", "headband_optical", "camera")

_CONSENT_DENIED = {
    **{f"{c}_enabled": False for c in CONSENT_CHANNELS},
    **{f"{c}_revoked_at": None for c in CONSENT_CHANNELS},
    **{f"{c}_revoked_by": None for c in CONSENT_CHANNELS},
    "updated_by": None,
    "updated_at": None,
    "parent_enabled_at": None,
    "student_ack_at": None,
}


def _parse_ts(value) -> datetime | None:
    """Parse a PostgREST timestamp, tolerating the trailing-Z spelling.

    Comparing these as strings happens to work for the formats in play, but only
    while both sides stay normalized -- a Z-suffixed value and a +00:00 one
    denote the same instant and sort differently. The comparison here decides
    whether a student is shown a notice about their own consent, so it is worth
    not resting on that.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        # These values are database-generated, so this should not fire. Logged
        # because of which way it fails: an unparseable timestamp suppresses
        # needs_student_ack, and the student is then not told a parent turned a
        # sensor back on. Silence is the wrong direction here.
        print(f"[consent:parse_ts] unparseable timestamp {value!r}")
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _consent(student_id: str) -> dict:
    """Consent flags for a student. Absent row and failed read both deny.

    Failing closed is the whole point: the reporting helpers elsewhere in this
    module swallow their exceptions and answer with an empty payload, which is
    the right call for a dashboard. It is the wrong call here -- defaulting to
    "enabled" on a failed read would record data the student declined, and the
    failure would be invisible because recording looks identical either way.

    `retrieved` distinguishes the two denials for callers that need to say why:
    "nothing is recorded because nobody consented" and "we could not find out"
    are not the same sentence to put in front of a parent.
    """
    try:
        rows = supabase.table("signal_consent").select("*") \
            .eq("user_id", student_id).limit(1).execute().data or []
    except Exception as e:
        print(f"[consent:read] {student_id}: {e}")
        return {**_CONSENT_DENIED, "retrieved": False, "exists": False}
    if not rows:
        # `exists` is not `retrieved`: no row and a failed read both deny, but
        # only the first one means a write should insert rather than update.
        return {**_CONSENT_DENIED, "retrieved": True, "exists": False}
    return {**_CONSENT_DENIED, **rows[0], "retrieved": True, "exists": True}


def _IS_DUPLICATE_KEY(exc: Exception) -> bool:
    """Whether a write failed because the row already existed.

    supabase-py surfaces PostgREST errors as an APIError whose shape has moved
    between versions, so this checks the SQLSTATE (23505) and the message rather
    than depending on a particular attribute being present.
    """
    code = getattr(exc, "code", None) or (
        exc.args[0].get("code") if exc.args and isinstance(exc.args[0], dict) else None
    )
    if str(code) == "23505":
        return True
    return "duplicate key" in str(exc).lower()


def _is_linked_parent(viewer_id: str, student_id: str) -> bool:
    try:
        link = supabase.table("parent_child_links").select("id") \
            .eq("parent_id", viewer_id).eq("child_id", student_id).limit(1).execute().data or []
        return bool(link)
    except Exception as e:
        print(f"[consent:parent_link] {e}")
        return False


def _consent_actor(viewer: dict, student_id: str) -> str:
    """Who is writing: the student themselves, or a linked parent.

    Deliberately narrower than _verify_can_view_student, which also admits
    teachers. A teacher can see that a channel is off -- they need to, or a
    blank tile reads as a broken query -- but consent over a child's body is not
    theirs to change. Reading and writing are different relationships here, so
    this does not reuse that helper.
    """
    if viewer["id"] == student_id:
        return "student"
    if _is_linked_parent(viewer["id"], student_id):
        return "parent"
    raise HTTPException(403, "Only the student or a linked parent can change consent")


def _shape_consent(row: dict, student_id: str) -> dict:
    """Per-channel payload: enabled, when it was revoked, and by which role.

    `revoked_by` is a role, never an identity. A teacher needs to know a
    decision was made and roughly by whom -- "student opted out" reads very
    differently from "parent opted out" when you are looking at a blank tile --
    but which guardian made it is none of their business.

    The role is derived per channel from that channel's own revoker, not from
    the row's `updated_by`. The row has one `updated_by` and the channels are
    revoked independently, so a student withdrawing the camera followed by a
    parent enabling eeg would otherwise report the parent as having withdrawn
    the camera.
    """
    channels = {}
    for c in CONSENT_CHANNELS:
        enabled = bool(row.get(f"{c}_enabled"))
        revoker = row.get(f"{c}_revoked_by")
        channels[c] = {
            "enabled": enabled,
            "revoked_at": row.get(f"{c}_revoked_at"),
            # Only meaningful while the channel is off; a role attached to an
            # enabled channel would read as "turned on by", which it is not.
            "revoked_by": (
                None if enabled or not revoker
                else ("student" if revoker == student_id else "parent")
            ),
        }

    enabled_at = _parse_ts(row.get("parent_enabled_at"))
    ack_at = _parse_ts(row.get("student_ack_at"))
    return {
        "student_id": student_id,
        "channels": channels,
        "retrieved": row.get("retrieved", True),
        "updated_at": row.get("updated_at"),
        # A parent turning something back ON has to be visible to the student:
        # discovering a resumed sensor by noticing data reappear is a surprise,
        # not consent. A parent turning one OFF raises nothing -- the student
        # loses nothing they had, and can see the state in settings.
        "needs_student_ack": bool(
            enabled_at and (ack_at is None or ack_at < enabled_at)
        ),
    }


class ConsentUpdate(BaseModel):
    eeg_enabled:              bool | None = None
    headband_optical_enabled: bool | None = None
    camera_enabled:           bool | None = None


@app.get("/api/consent/{student_id}")
def get_consent(student_id: str, request: Request):
    user = get_user(request)
    _verify_can_view_student(user, student_id)
    return _shape_consent(_consent(student_id), student_id)


@app.put("/api/consent/{student_id}")
def update_consent(student_id: str, payload: ConsentUpdate, request: Request):
    user = get_user(request)
    actor = _consent_actor(user, student_id)

    current = _consent(student_id)
    if not current["retrieved"]:
        # Writing blind would mean deciding the student's current state from a
        # read that failed. A 503 is recoverable; a wrongly-enabled channel is
        # not.
        raise HTTPException(503, "Could not read current consent; not changing it")

    now = _utc_now().isoformat()
    fields: dict = {}
    guards: dict = {}
    re_enabled = False
    for c in CONSENT_CHANNELS:
        requested = getattr(payload, f"{c}_enabled")
        if requested is None:
            continue
        was = bool(current[f"{c}_enabled"])
        if requested == was:
            continue

        # The asymmetry. A student may withdraw at any time and that decision
        # stands until a parent revisits it; a student cannot re-enable, or the
        # parent's control would be nominal.
        if requested and actor == "student":
            raise HTTPException(
                403,
                f"You can turn {c} off, but only a parent can turn it back on",
            )

        fields[f"{c}_enabled"] = requested
        fields[f"{c}_revoked_at"] = None if requested else now
        fields[f"{c}_revoked_by"] = None if requested else user["id"]
        # The state this decision was made against, asserted on the write below.
        guards[f"{c}_enabled"] = was
        if requested and actor == "parent":
            re_enabled = True

    if not fields:
        # No-op. Deliberately does not restamp updated_by/updated_at: a parent
        # re-saving unchanged settings would otherwise raise a notice at the
        # student about a change that did not happen.
        return _shape_consent(current, student_id)

    fields["updated_by"] = user["id"]
    fields["updated_at"] = now
    if re_enabled:
        fields["parent_enabled_at"] = now

    try:
        if not current["exists"]:
            # No row yet. Insert rather than upsert so a row created by a
            # concurrent request collides instead of being silently overwritten
            # with a decision made against a state that no longer holds.
            try:
                supabase.table("signal_consent") \
                    .insert({"user_id": student_id, **fields}).execute()
            except Exception as e:
                # Same race as the conditional update below, so it gets the same
                # answer. Without this the two paths report one lost race as a
                # 500 and the other as a 409, and a client cannot tell that
                # "reload and try again" is the right response to both.
                if _IS_DUPLICATE_KEY(e):
                    raise HTTPException(
                        409, "Consent changed while you were editing it; reload and try again"
                    )
                raise
            return _shape_consent(_consent(student_id), student_id)

        # Conditional on every flag this call decided against. Read-then-write
        # is not atomic, and the two racing writes here are a student's
        # withdrawal and a parent's re-enable landing on the same channel -- so
        # losing the race silently would mean recording against a refusal. If
        # the state moved underneath us the update matches nothing, and the
        # caller is told to look again rather than being told it worked.
        q = supabase.table("signal_consent").update(fields).eq("user_id", student_id)
        for col, was in guards.items():
            q = q.eq(col, was)
        written = q.execute().data or []
    except HTTPException:
        # The 409 raised for a lost insert race above. Without this the outer
        # handler turns it back into a 500, which is the bug this whole branch
        # exists to remove.
        raise
    except Exception as e:
        print(f"[consent:write] {student_id}: {e}")
        raise HTTPException(500, "Could not save consent")

    if not written:
        raise HTTPException(409, "Consent changed while you were editing it; reload and try again")

    return _shape_consent(_consent(student_id), student_id)


@app.post("/api/consent/ack")
def ack_consent(request: Request):
    """Student dismisses the notice that a parent turned a channel back on."""
    user = get_user(request)
    try:
        written = supabase.table("signal_consent") \
            .update({"student_ack_at": _utc_now().isoformat()}) \
            .eq("user_id", user["id"]).execute().data or []
    except Exception as e:
        print(f"[consent:ack] {user['id']}: {e}")
        raise HTTPException(500, "Could not acknowledge")
    # A student with no consent row has nothing to acknowledge. Reporting
    # success for a write that matched nothing would leave a client believing a
    # notice was dismissed that is still there.
    if not written:
        raise HTTPException(404, "No consent record to acknowledge")
    return {"ok": True}


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
    # Two confidences, named for what each answers. `identity_confidence` is how
    # sure we are whose face this is; `emotion_confidence` is how sure we are of
    # the expression. The fusion rule reads only the second, and read the first
    # by mistake once -- see the migration comment that predicted it.
    identity_confidence: float | None = None
    emotion_confidence:  float | None = None
    emotion_trusted:     bool  | None = None
    raw:                 dict  | None = None

class FaceBatch(BaseModel):
    session_id: str
    samples:    list[FaceSample] = Field(max_length=_INGEST_MAX_BATCH)


class HeartSample(BaseModel):
    """One derived heart reading, from whichever sensor produced it.

    `source` is required and constrained in the database to
    muse_optics | muse_ppg | rppg, because consent is per *sensor* and a row
    that cannot say which sensor produced it cannot be consent-checked. After
    Phase 4 nothing writes `rppg` -- camera heart rate failed ECG validation --
    but the column stays honest about what the schema permits.
    """
    ts:              str | None = None
    source:          str
    heart_rate_bpm:  float | None = None
    rmssd_ms:        float | None = None
    sqi:             float | None = None
    stress_score:    float | None = None
    stress_category: str   | None = None
    trusted:         bool  | None = None
    raw:             dict  | None = None


class HeartBatch(BaseModel):
    session_id: str
    samples:    list[HeartSample] = Field(max_length=_INGEST_MAX_BATCH)

def _rate_limit_ingest(user_id: str):
    """Raise 429 once a caller has spent its allowance for the window.

    Monotonic, like `_rate_limit_strategies`, so a clock adjustment cannot wipe
    or extend the window. Kept separate from that limiter rather than shared:
    the budgets are different by two orders of magnitude, and one dict would
    make a student's steady 1 Hz ingest compete with their own occasional
    strategy request.
    """
    global _ingest_sweep_at
    now = time.monotonic()
    with _ingest_hits_lock:
        # Only once the dict is large, and only once per interval: a size-only
        # trigger scans every known caller on every request and frees nothing
        # when the dict is large because the callers are real.
        if (len(_ingest_hits) > _INGEST_SWEEP_ABOVE
                and now - _ingest_sweep_at >= _INGEST_SWEEP_EVERY):
            _ingest_sweep_at = now
            for uid in [u for u, ts in _ingest_hits.items()
                        if all(now - t >= _INGEST_RATE_WINDOW for t in ts)]:
                del _ingest_hits[uid]

        hits = [t for t in _ingest_hits.get(user_id, ())
                if now - t < _INGEST_RATE_WINDOW]
        if len(hits) >= _INGEST_RATE_LIMIT:
            _ingest_hits[user_id] = hits
            retry_after = max(1, int(_INGEST_RATE_WINDOW - (now - min(hits))) + 1)
            raise HTTPException(429, "Too many ingest batches. Slow down.",
                                headers={"Retry-After": str(retry_after)})
        hits.append(now)
        _ingest_hits[user_id] = hits


def _permitted_heart_sources(consent: dict) -> set[str]:
    """The sources this student's consent covers. Empty means record nothing."""
    allowed: set[str] = set()
    for flag, sources in _HEART_SOURCES_BY_CONSENT.items():
        if consent.get(flag):
            allowed.update(sources)
    return allowed


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
    # Accepted in either mode -- rejecting it under `pull` would break mixed
    # local dev -- but warned about when this session is *actually* being
    # double-written: a live poller for it is writing the same table from the
    # same sidecar, so every EEG sample lands twice. Valid rows, wrong counts,
    # no error, and no dedupe key to catch it the way `heart_signals` has.
    #
    # The condition is a live poller for this session, not `INGEST_MODE ==
    # "pull"`. The mode was only a proxy, and a wrong one in both directions: it
    # fired on the hand-posted dev batch the openness exists for, and -- with a
    # once-per-process flag -- that benign first post spent the warning, so a
    # genuine double-write later in the same process was never reported.
    #
    # Per session rather than per process: the sessions are the thing at risk,
    # there are few of them, and this still logs once each rather than at the
    # sample rate.
    # One call, one lock: checking liveness and claiming the warning separately
    # let two concurrent batches both pass the check and log twice. It also puts
    # the eviction next to `stop()`, which is the only thing that can bound it.
    if eeg_poller.claim_double_write_warning(payload.session_id):
        print(f"[ingest] session {payload.session_id[:8]} is being written by both "
              f"the poller and /api/signals/cognitive. Every EEG sample is landing "
              f"twice and cognitive_signals has no dedupe key to catch it.",
              flush=True)
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
    # Before the session lookup, not after: the limiter needs only the caller's
    # id, and running it second meant a flooding client still cost one `sessions`
    # query per request -- the exact cost the limit exists to avoid.
    _rate_limit_ingest(user["id"])
    _verify_session_owner(payload.session_id, user["id"])

    # Last line of defence. The sidecar already gates on consent, but a stale one
    # that kept sending after a student withdrew would otherwise keep recording,
    # and the withdrawal would look respected from every surface that reads.
    # `_consent` fails closed, so an unreadable consent row records nothing.
    consent = _consent(user["id"])
    if not consent.get("camera_enabled"):
        return {"ok": True, "inserted": 0, "dropped": len(payload.samples),
                "reason": ("consent unavailable" if not consent.get("retrieved")
                           else "camera not consented")}

    # Through the shared mapper, not inline. Two copies of this had already
    # drifted -- the mapper dropped gaze_x/gaze_y that this endpoint wrote --
    # which is exactly the divergence the module was extracted to prevent, and
    # it went unnoticed because nothing called it.
    rows = [r for r in (
        signal_mapping.map_face_to_face_signal(
            {"timestamp": s.ts or _utc_now().isoformat(),
             "face": {"emotion": s.emotion, "attention": s.attention,
                      "gaze_x": s.gaze_x, "gaze_y": s.gaze_y,
                      "identity_confidence": s.identity_confidence,
                      "emotion_confidence": s.emotion_confidence,
                      "trusted": s.emotion_trusted},
             "raw": s.raw},
            payload.session_id, user["id"])
        for s in payload.samples
    ) if r is not None]
    # Insert, not upsert: `face_signals` has no dedupe key yet. See
    # 20260809120000 for why that is deferred rather than undecided, and the
    # query to run before adding one.
    if rows: supabase.table("face_signals").insert(rows).execute()
    return {"ok": True, "inserted": len(rows)}


@app.post("/api/signals/heart")
def ingest_heart(payload: HeartBatch, request: Request):
    """Derived heart readings, from whichever sensor produced them.

    Consent is checked **per sample**, against the sensor named in `source`,
    because one channel can arrive from two sensors under two separate
    permissions. Samples from a declined sensor are dropped and counted rather
    than failing the batch: a mixed batch is a legitimate thing for a client to
    send, and rejecting the whole thing would take the consented samples with it.

    The count comes back so a caller can tell "recorded nothing" from "sent
    nothing" -- the same distinction the reporting rules exist to preserve, on
    the write side.
    """
    user = get_user(request)
    # Before the session lookup, not after: the limiter needs only the caller's
    # id, and running it second meant a flooding client still cost one `sessions`
    # query per request -- the exact cost the limit exists to avoid.
    _rate_limit_ingest(user["id"])
    _verify_session_owner(payload.session_id, user["id"])

    consent = _consent(user["id"])
    allowed = _permitted_heart_sources(consent)
    kept = [s for s in payload.samples if s.source in allowed]
    dropped = len(payload.samples) - len(kept)

    # Three outcomes, not two. "every sensor was declined" and "we could not
    # find out" both record nothing, and only one of them is a fault worth
    # chasing -- the same distinction `_consent` carries `retrieved` for, and
    # the same one the reporting rules insist on. Without it a consent table
    # that is down looks exactly like a student who said no.
    reason = None
    if not allowed:
        reason = ("consent unavailable" if not consent.get("retrieved")
                  else "no consented heart sensor")

    rows = [r for r in (
        signal_mapping.map_heart_to_heart_signal(
            {"timestamp": s.ts or _utc_now().isoformat(),
             "heart": {"source": s.source, "bpm": s.heart_rate_bpm,
                       "rmssd_ms": s.rmssd_ms, "sqi": s.sqi,
                       "stress_score": s.stress_score,
                       "stress_category": s.stress_category,
                       "trusted": s.trusted},
             "raw": s.raw},
            payload.session_id, user["id"])
        for s in kept
    ) if r is not None]

    written = 0
    if rows:
        # ON CONFLICT DO NOTHING against (session_id, source, ts). A retried
        # batch is then idempotent instead of doubling every average it touches
        # -- a failure with no symptom except a wrong number.
        #
        # `inserted` counts what the database actually wrote, not what was sent.
        # Taking it from `len(rows)` reported a replay as having inserted its
        # whole batch while inserting nothing, which is a worse lie than the
        # double-count this key exists to prevent: it tells a retrying client
        # its retry worked.
        resp = supabase.table("heart_signals").upsert(
            rows, on_conflict="session_id,source,ts", ignore_duplicates=True
        ).execute()
        # Depends on PostgREST returning a representation, which postgrest-py
        # requests by default. Under `return=minimal` this would report
        # inserted: 0, duplicates: N on every *successful* write -- the same
        # silent miscount the count exists to fix, inverted. The fake client in
        # the tests cannot catch that, so it is written down here: if the
        # default is ever changed, this arithmetic has to change with it.
        written = len(resp.data or [])
    return {"ok": True, "inserted": written, "dropped": dropped,
            "duplicates": len(rows) - written, "reason": reason}

@app.get("/api/signals/session/{session_id}")
def session_signals(session_id: str, request: Request, since: str | None = None):
    # This returns raw EEG and facial-emotion samples for a session, so resolve
    # whose session it is and apply the same access rule as the student-scoped
    # endpoints rather than trusting any authenticated caller.
    #
    # No include_face, deliberately. The facial-recognition opt-out covers the
    # reporting surfaces -- the weekly report, the signal summaries and the
    # children endpoint, all of which render the switch -- and session review,
    # this endpoint's only caller, does not. See the scope note in
    # frontend/src/lib/facePref.js; adding the parameter here without putting
    # the switch on that page would make the control silently change a view it
    # is absent from.
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
    """Live signals for the students of a class the caller owns.

    No include_face, deliberately, for the reason session_signals gives above:
    the facial-recognition opt-out covers the reporting surfaces, which render
    the switch, and the live monitor does not. It is built around whether the
    camera is currently working -- the attention gauge, the current emotion and
    a camera-on badge -- which a reporting-window preference has no sensible
    reading on. See the scope note in frontend/src/lib/facePref.js.
    """
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
    # The fourth and last endpoint in this family to learn the mode. /start,
    # /status and /health each needed it for the same reason, found in three
    # separate rounds; this one is dev-only behind VITE_EEG_DEBUG so nothing a
    # student sees, but leaving it is how it gets found a fourth time.
    if eeg_poller.INGEST_MODE == "push":
        return {"available": None, "ingest_mode": "push", "devices": []}
    if not eeg_client.is_alive():
        return {"available": False, "ingest_mode": "pull", "devices": []}
    return {"available": True, "ingest_mode": "pull",
            "devices": eeg_client.list_devices()}

@app.get("/api/eeg/debug")
def eeg_debug(request: Request, device_id: str = eeg_client.DEFAULT_DEVICE_ID):
    """Raw EEG snapshot for local development — returns the full state from EEGResearch."""
    user = get_user(request)
    # The last of the five in this family to learn the mode. /start, /status,
    # /health and /devices each needed it for the same reason, found across
    # separate rounds; this one is dev-only behind VITE_EEG_DEBUG so nothing a
    # student sees, but leaving it is how it gets found again.
    if eeg_poller.INGEST_MODE == "push":
        return {"available": None, "ingest_mode": "push"}
    if not eeg_client.is_alive():
        return {"available": False, "ingest_mode": "pull"}
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
    """Tells the frontend whether the EEGResearch sidecar service is reachable.

    Under push ingestion there is nothing to be reachable *from here*, and this
    is the poll that runs from page load -- the status one is gated on a session
    existing. Reporting a flat `available: False` put "EEG service not reachable
    on port 8001. Make sure the EEGResearch backend is running." on the first
    screen a student sees, which is the sentence the mode check was added to
    stop showing. Fixing it at /start and /status alone just moved it here.
    """
    if eeg_poller.INGEST_MODE == "push":
        # `available` is None rather than False for the same reason as /status:
        # "not probed in this deployment" is not "probed and down".
        return {"available": None, "ingest_mode": "push", "url": None}
    alive = eeg_client.is_alive()
    if not alive:
        return {"available": False, "ingest_mode": "pull",
                "url": eeg_client.EEG_API_URL}
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
    # Answered before the liveness check, deliberately. Under push ingestion
    # this backend never talks to a sidecar, so "EEG service is not running on
    # port 8001" would be both true and completely misleading -- it reads as a
    # broken service when the deployment simply does not work that way.
    if eeg_poller.INGEST_MODE == "push":
        raise HTTPException(
            409,
            "This deployment uses push ingestion: the sidecar on the student's "
            "own device posts to /api/signals/* and this endpoint does not "
            "start a poller. Nothing is wrong with the headband.",
        )
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
    # Under push ingestion this backend has no route to a sidecar, so
    # `is_alive()` is false forever -- and the frontend polls this every 3
    # seconds and renders it as "EEG service is down". The student would get the
    # carefully-worded 409 from /start once and then a continuous contradiction
    # of it. Same argument as checking the mode before the liveness probe there;
    # /start was simply the only place it got applied.
    push = eeg_poller.INGEST_MODE == "push"
    return {
        # None, not False: "we do not probe a sidecar in this deployment" is not
        # the same claim as "we probed and it is down", and a consumer that
        # branches on falsiness would render both identically.
        "service": None if push else eeg_client.is_alive(),
        # Carried so a client can say *why* rather than inferring it from a
        # null, which is the distinction the reporting rules exist to keep.
        "ingest_mode": eeg_poller.INGEST_MODE,
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

    Stored consent is resolved per child on top of that, so one sibling's
    refusal cannot suppress another's data and no child's declined channel is
    read because a sibling permitted it.
    """
    user = get_user(request)
    links = supabase.table("parent_child_links").select("child_id, created_at") \
        .eq("parent_id", user["id"]).execute()
    # One round-trip for every child's signal averages, rather than one per
    # child inside the loop below. Note the rest of this loop is still a query
    # per child (stats, sessions, performance, profile); this fixes the query
    # added here, not the endpoint's overall shape.
    # Grouped by consent, because consent is per child and the batch RPC takes
    # one flag pair for the whole call. Passing a single pair would mean either
    # reading a channel a child declined, or hiding one a sibling permitted --
    # and the first of those is the one that matters.
    #
    # At most four groups exist (heart x emotion) and in practice one, since
    # siblings are usually configured the same way, so this is normally still a
    # single round-trip. Correctness first: a fifth query is cheaper than
    # reading a channel a child refused.
    # Resolved once per child and reused below. This endpoint is already a
    # query per child; reading consent twice for each would make it N+2 for
    # nothing.
    child_ids = [lnk["child_id"] for lnk in (links.data or [])]
    channels_by_child = {cid: _reportable_channels(cid, include_face)
                         for cid in child_ids}
    # Keyed on the flags alone, not the whole ReportChannels. `consent_retrieved`
    # does not change what the RPC is asked for, so including it would split two
    # children with identical flags into separate round-trips and break the
    # "at most four groups" bound this relies on.
    by_channels: dict[tuple[bool, bool], list[str]] = {}
    for cid, ch in channels_by_child.items():
        by_channels.setdefault((ch.heart, ch.emotion), []).append(cid)

    summaries: dict | None = {}
    for (heart_flag, emotion_flag), group in by_channels.items():
        part = _signal_summaries(group, include_heart=heart_flag,
                                 include_emotion=emotion_flag)
        if part is None:
            # One failed group fails the whole call, discarding groups that
            # succeeded. Deliberate, and the conservative choice rather than an
            # oversight: `summaries_retrieved` is one flag for the endpoint, so
            # a partial result would have to report the succeeded children as
            # retrieved and the failed ones as empty -- and "empty" is exactly
            # the word that must not stand in for "we could not read it". Every
            # child falling back to an explicitly-unretrieved payload is the
            # honest answer until the flag is per child.
            summaries = None
            break
        summaries.update(part)
    # None is the failed read; {} is a read that returned nothing. Both leave
    # every child falling back below, and the fallback has to say which one it
    # is standing in for -- otherwise a broken RPC reaches a parent as "your
    # child recorded nothing this week".
    summaries_retrieved = summaries is not None
    summaries = summaries or {}
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
            # Grouping keys on the flags alone, so a child whose consent read
            # *failed* shares a group with one who genuinely declined both
            # channels -- same RPC call, different meaning. The RPC path cannot
            # know which, so it is stamped per child here.
            "signal_summary": {**summaries[str(cid)],
                               "consent_retrieved": channels_by_child[cid].consent_retrieved}
                              if str(cid) in summaries
                              else _shape_summary(None,
                                                channels_by_child[cid].heart,
                                                channels_by_child[cid].emotion,
                                                summaries_retrieved,
                                                channels_by_child[cid].consent_retrieved),
        })
    return children


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=BACKEND_PORT, reload=True)