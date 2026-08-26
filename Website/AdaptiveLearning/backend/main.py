from fastapi import FastAPI, Request, HTTPException, Path, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
import os, math, re, requests, random, string, threading, time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import asynccontextmanager
from datetime import datetime, date, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from supabase import create_client
from typing import NamedTuple

import LLM_topic_decider
import chart_archive
import eeg_client
import signal_mapping
import eeg_poller
import llm_client

load_dotenv()

def _env_number(name: str, default, cast, minimum=None):
    """Read a numeric setting from the environment, falling back on a bad value.

    These run at import time, so a typo would otherwise crash the whole app
    before it starts, over one optional feature's tuning knob. Falls back to
    the default instead, with a log line saying so.

    `minimum` is a floor: some settings don't just tune a feature below a
    certain value, they break it. Clamps to the minimum rather than the
    default, since a small value was still an intentional ask for something
    small.

    Non-finite values ("inf", "nan") parse fine but aren't real magnitudes, so
    they fall back to the default instead of being clamped -- inf passes any
    minimum check, nan fails every comparison. Only affects the float callers;
    int() rejects both at the cast.
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


SUPABASE_URL     = os.getenv("SUPABASE_URL")
SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
BACKEND_PORT     = _env_number("BACKEND_PORT", 8000, int, minimum=1)

if not SUPABASE_URL or not SERVICE_ROLE_KEY:
    raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY")

supabase = create_client(SUPABASE_URL, SERVICE_ROLE_KEY)

@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Process-lifetime hooks. Everything before the yield is startup."""
    start_stale_sweeper()
    yield
    # Pollers first: they're daemon threads that print on the way out, and
    # leaving them to interpreter teardown risks a fatal stdout-lock crash.
    try:
        eeg_poller.stop_all()
    finally:
        # The sweeper is the same kind of thread and needs the same join, for
        # the same reason -- see `stop_stale_sweeper`.
        try:
            stop_stale_sweeper()
        finally:
            # Nested finally so each shutdown step runs even if an earlier one
            # raises.
            try:
                _shutdown_strategy_pool()
            finally:
                try:
                    _shutdown_admin_live_pool()
                finally:
                    try:
                        _shutdown_prefetch_pool()
                    finally:
                        chart_archive.shutdown_pool()


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


def _unique_ids(values) -> list:
    """The ids worth querying for: in order, without blanks or repeats."""
    return [v for v in dict.fromkeys(values) if v]


def _group_by_user(rows) -> dict[str, list]:
    """Rows bucketed by `user_id`, order within a bucket preserved.

    Order matters for `_open_sessions_many`: it asks the database for
    newest-first and its caller takes `[0]`.
    """
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r.get("user_id"), []).append(r)
    return grouped


def _row_or_404(query, what: str) -> dict:
    """Run a `.single()` lookup and answer 404 when the row is not there.

    `.single()` raises `APIError(PGRST116)` on zero rows instead of returning
    empty, so this catches that and turns it into a proper 404 rather than a
    500 with a stack trace. The empty-data check below the try is kept as a
    backstop in case the client library ever stops raising here.

    Don't use this for a lookup where "absent" is a legitimate answer and
    should fall back instead of failing (see `generate_question`'s class read).
    """
    try:
        res = query.single().execute()
    except HTTPException:
        raise
    except Exception:                                          # noqa: BLE001
        raise HTTPException(404, f"{what} not found")
    if not res.data:
        raise HTTPException(404, f"{what} not found")
    return res.data

# The four values `profiles.role` may hold. `admin` is the only one nobody can
# choose at sign-up -- `handle_new_user` whitelists the other three.
ADMIN_ROLE = "admin"
SELF_SERVICE_ROLES = ("student", "teacher", "parent")


def _role(uid: str) -> str:
    """A caller's role, from `profiles` -- never from `user_metadata`.

    `user_metadata.role` is client-writable (`supabase.auth.updateUser`), so a
    student could self-elevate to teacher if we trusted it. `profiles.role`
    can't be edited by the client (UPDATE/INSERT revoked), so it's the source
    of truth. Falls back to 'student' -- the least-privileged role -- on a
    failed read, so a database blip can never grant access.
    """
    return (_profile(uid) or {}).get("role") or "student"


def _placeholder_profile(uid: str) -> dict:
    """What a caller gets when a profile cannot be read, or has no row.

    The caller can't tell this from a real profile, so the preference fields
    are filled with the same values a new account gets -- leaving them out
    would render as "the student turned this off" instead of "read failed".
    Shared with `_profiles_many` so single and batch lookups agree.
    """
    return {"id": uid, "display_name": "Student", "email": "", "role": "student",
            "grade_level": None, "difficulty_bias": 0,
            "session_duration_minutes": 15, "practice_reminders": True}


def _profile(uid: str) -> dict:
    try:
        p = supabase.table("profiles").select("*").eq("id", uid).single().execute()
        if p.data:
            return p.data
    except Exception:
        pass
    return _placeholder_profile(uid)


def _profiles_many(uids) -> dict[str, dict]:
    """`_profile` for a roster, in one query rather than one per student.

    Same fallback as `_profile`, per missing student: an absent row and a
    failed read both become the placeholder, since a blank name reads as bad
    data rather than a failed read either way.
    """
    ids = _unique_ids(uids)
    if not ids:
        return {}
    rows = []
    try:
        rows = supabase.table("profiles").select("*").in_("id", ids).execute().data or []
    except Exception as e:                                     # noqa: BLE001
        print(f"[profiles] could not batch-read {len(ids)} profiles: {e}")
    found = {r["id"]: r for r in rows if r.get("id")}
    return {uid: found.get(uid) or _placeholder_profile(uid) for uid in ids}


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
    """Timezone-aware UTC. `datetime.utcnow()` is deprecated and naive, which
    compares wrongly against the timestamptz columns these queries filter on.
    Use this instead in new code."""
    return datetime.now(timezone.utc)


# ── the school-year retention window ────────────────────────────────────────
#
# Every state a caller can be in. `open` is the only one that records. Five
# states rather than a boolean because "not started yet", "year is over" and
# "no year configured" are different things to tell a parent, and collapsing
# them to "off" reads as consent being ignored. `unreadable` is its own state
# because that one is our fault, not a fact about the school.
WINDOW_OPEN = "open"
# Also records, but for a different reason than WINDOW_OPEN: "inside a real
# year" and "not gating on a year at all" look the same from the recording
# side but are different facts worth keeping apart.
WINDOW_NOT_ENFORCED = "not_enforced"
WINDOW_BEFORE = "before_year"
WINDOW_AFTER = "after_year"
WINDOW_UNCONFIGURED = "unconfigured"
WINDOW_UNREADABLE = "unreadable"

class _WindowMeaning(NamedTuple):
    """What a window state means, in the three places that need to know."""
    records: bool
    # Shown to a person. Lowercase since ingest endpoints embed it in a
    # `reason` field beside "eeg not consented" and similar.
    reason: str | None
    # Rendered by the frontend, which picks its own copy from the key.
    stopped_reason: str | None


# One table for all three facts about each state, so adding a state can't miss
# one of them. `test_every_window_state_has_a_meaning` enforces it.
_WINDOW_STATES = {
    WINDOW_OPEN: _WindowMeaning(True, None, None),
    WINDOW_NOT_ENFORCED: _WindowMeaning(True, None, None),
    WINDOW_BEFORE: _WindowMeaning(
        False, "recording has not started for this school year",
        "school_year_not_started"),
    WINDOW_AFTER: _WindowMeaning(
        False, "the school year has ended", "school_year_ended"),
    WINDOW_UNCONFIGURED: _WindowMeaning(
        False, "no school year is configured, so nothing is recorded",
        "school_year_unconfigured"),
    WINDOW_UNREADABLE: _WindowMeaning(
        False, "could not check the school year, so nothing was recorded",
        "school_year_unknown"),
}

# Derived, not maintained by hand. A state missing from the table above denies
# by default, the same fail-closed direction as everything else here.
_WINDOW_DENIED = {k for k, v in _WINDOW_STATES.items() if not v.records}


# How long a successful window read is cached. The window is one row shared by
# every student and edited twice a year, so caching it saves a round trip per
# ingest request.
#
# Unlike `_consent`, which may never be cached: a withdrawal has to take effect
# mid-lesson, so caching it would keep recording against a refusal. The window
# only moves twice a year, so the worst case here is recording briefly past
# local midnight on the last day of the year -- bounded and harmless, unlike a
# stale consent answer.
_RETENTION_TTL_SECONDS = 30.0
_retention_cached: tuple[float, dict] | None = None
_retention_lock = threading.Lock()


def _retention_cache_clear() -> None:
    """Drop the cached window. For tests, and for anything that edits the row."""
    global _retention_cached
    with _retention_lock:
        _retention_cached = None


def _retention_window() -> dict:
    """The configured school year, and today's position in it.

    Fails closed like `_consent`: an unreadable or unconfigured window records
    nothing, since an unset date isn't an open-ended licence.

    Returns `state` plus the raw dates, so a caller can say *when* recording
    starts or stopped, not just that it isn't happening.

    Compares in the school's own timezone, not UTC -- "the last day of school"
    should end at local midnight, not somewhere in the afternoon UTC.
    """
    global _retention_cached
    now = time.monotonic()
    with _retention_lock:
        cached = _retention_cached
    if cached and now < cached[0]:
        # Only the row is cached, not the verdict -- the state depends on
        # today's date, so it's recomputed on every call.
        return _resolve_window(cached[1])

    try:
        rows = supabase.table("retention_window").select("*").limit(1).execute().data or []
    except Exception as e:
        print(f"[retention:read] {e}")
        # Not cached: caching a failure would keep denying for the TTL even
        # after the database recovers.
        return {"state": WINDOW_UNREADABLE, "starts_on": None, "ends_on": None,
                "timezone": None}
    if not rows:
        return {"state": WINDOW_UNCONFIGURED, "starts_on": None, "ends_on": None,
                "timezone": None}

    with _retention_lock:
        _retention_cached = (now + _RETENTION_TTL_SECONDS, rows[0])
    return _resolve_window(rows[0])


def _resolve_window(row: dict) -> dict:
    """Today's position in a window row. Split out so the cache stores the
    stable row, not the verdict, which depends on today's date."""
    name = row.get("timezone") or "UTC"
    try:
        tz = ZoneInfo(name)
    except Exception:
        # A typo'd timezone denies rather than silently falling back to UTC,
        # which would shift every boundary by hours while looking fine.
        print(f"[retention:tz] unknown timezone {name!r} -- recording denied")
        return {"state": WINDOW_UNREADABLE, "starts_on": row.get("starts_on"),
                "ends_on": row.get("ends_on"), "timezone": name}

    today = _utc_now().astimezone(tz).date()
    starts, ends = row.get("starts_on"), row.get("ends_on")

    # Checked before the dates: an unenforced row has no term dates to check
    # (they're nullable for exactly this case), so reading them first would
    # wrongly deny it as unparseable.
    #
    # `is False`, not falsiness: a row predating this column, or one missing
    # it, must not read as "not enforcing". Only an explicit false disables the
    # gate.
    if row.get("enforced") is False:
        return {"state": WINDOW_NOT_ENFORCED, "starts_on": starts,
                "ends_on": ends, "timezone": name}

    try:
        starts_d = date.fromisoformat(str(starts))
        ends_d = date.fromisoformat(str(ends))
    except (TypeError, ValueError):
        if starts is None and ends is None:
            # Enforcing with no dates set is a half-finished edit, not "record
            # forever" -- that's what `enforced = false` means. Deny it the
            # same way an absent row denies.
            print("[retention:dates] enforced with no dates set -- recording denied")
            return {"state": WINDOW_UNCONFIGURED, "starts_on": None,
                    "ends_on": None, "timezone": name}
        print(f"[retention:dates] unparseable window {starts!r}..{ends!r}")
        return {"state": WINDOW_UNREADABLE, "starts_on": starts, "ends_on": ends,
                "timezone": name}

    if today < starts_d:
        state = WINDOW_BEFORE
    elif today > ends_d:
        # Inclusive of ends_on: the last day of school is still a school day.
        state = WINDOW_AFTER
    else:
        state = WINDOW_OPEN
    return {"state": state, "starts_on": starts, "ends_on": ends, "timezone": name}


# ─── feature flags ───────────────────────────────────────────────────────
#
# Global runtime switches, edited from the admin dashboard instead of `.env` +
# redeploy. A key absent from the table still has a value -- the one the
# system had before the table existed -- so introducing a flag, or a failed
# read, never changes behaviour by itself.
#
# This map is also the whitelist: an unknown key in the table is ignored, and
# a write to an unknown key is refused rather than silently creating a dead
# switch.
_FEATURE_FLAG_DEFAULTS = {
    "strategy_llm_enabled": False,
    "recording_eeg_enabled": True,
    "recording_heart_enabled": True,
    "recording_camera_enabled": True,
    "consent_enforcement_enabled": True,
}

# Named rather than repeated as a literal at its three call sites, since this
# is the one flag that must never be left off for long.
CONSENT_ENFORCEMENT_FLAG = "consent_enforcement_enabled"

# Same TTL and reasoning as `_RETENTION_TTL_SECONDS`: global config, not a
# per-student decision, so bounded staleness is harmless. The bypass expiry
# itself is checked against the clock on every read, not cached as a verdict,
# so turning enforcement back on always takes effect within the TTL.
_FEATURE_FLAGS_TTL_SECONDS = 30.0
_feature_flags_cached: tuple[float, dict] | None = None
_feature_flags_lock = threading.Lock()


def _feature_flags_cache_clear() -> None:
    """Drop the cached flags. For tests, and for anything that writes a flag."""
    global _feature_flags_cached
    with _feature_flags_lock:
        _feature_flags_cached = None


def _feature_flags() -> dict:
    """Every known flag, as `{key: {"enabled": bool, "bypass_until": str|None}}`.

    Fails to the declared defaults on a read error, never to off or on --
    an unreadable table isn't a reconfiguration. This matters most for
    `consent_enforcement_enabled`, which defaults True: a database outage must
    never be the reason consent stops being enforced.

    Unknown keys in the table are dropped, so the result always has exactly
    the keys in `_FEATURE_FLAG_DEFAULTS`.
    """
    global _feature_flags_cached
    now = time.monotonic()
    with _feature_flags_lock:
        cached = _feature_flags_cached
    if cached and now < cached[0]:
        return cached[1]

    flags = {k: {"enabled": v, "bypass_until": None}
             for k, v in _FEATURE_FLAG_DEFAULTS.items()}
    try:
        rows = supabase.table("feature_flags").select("*").execute().data or []
    except Exception as e:
        print(f"[flags:read] {e}")
        # Not cached, same reasoning as `_retention_window`: a transient blip
        # shouldn't keep answering with defaults after the database recovers.
        return flags

    for row in rows:
        key = row.get("key")
        if key in flags:
            flags[key] = {"enabled": bool(row.get("enabled")),
                          "bypass_until": row.get("bypass_until")}

    with _feature_flags_lock:
        _feature_flags_cached = (now + _FEATURE_FLAGS_TTL_SECONDS, flags)
    return flags


def _consent_enforcement_active(flags: dict | None = None) -> bool:
    """Whether per-student consent gates recording right now.

    True unless there's a live, unexpired bypass. Expiry is checked against the
    clock on every read rather than by a job flipping the row back, so a
    missed cron run can't leave enforcement off forever. A bypass with no
    `bypass_until` counts as already expired -- an unbounded bypass is exactly
    what this guards against.
    """
    flags = flags if flags is not None else _feature_flags()
    flag = flags.get(CONSENT_ENFORCEMENT_FLAG) or {}
    if flag.get("enabled", True):
        return True
    until = _parse_ts(flag.get("bypass_until"))
    if until is None:
        return True
    return _utc_now() >= until


def _school_timezone() -> tzinfo:
    """The school's zone, for bucketing report days. Defaults to UTC.

    Opposite default from `_retention_window` on purpose: there, a bad zone
    denies recording (wrong boundary = data collected nobody agreed to). Here
    it just shifts a chart's day boundary by a few hours, and denying would
    blank the whole dashboard -- a bigger harm than a slightly wrong bucket.

    Shares `_retention_window`'s cache, so a timezone fix can take up to
    `_RETENTION_TTL_SECONDS` to show up.

    Falls back to `timezone.utc`, not `ZoneInfo("UTC")` -- `ZoneInfo` needs a
    timezone database Windows doesn't ship, so without `tzdata` installed even
    the fallback would raise. CI runs on Linux, where this gap doesn't show up,
    so it's easy to miss.
    """
    try:
        return ZoneInfo(_retention_window().get("timezone") or "UTC")
    except Exception:
        try:
            return ZoneInfo("UTC")
        except Exception:
            return timezone.utc


def _school_date(ts, tz: tzinfo) -> date | None:
    """The calendar day `ts` falls on *at the school*. None if unparseable.

    Not `str(ts)[:10]` -- PostgREST returns UTC, and slicing the string buckets
    by UTC midnight, which is mid-afternoon the previous day in Los Angeles.
    A late lesson would land on the wrong day of a parent's chart.

    Returns a `date` because the rollup writer does arithmetic on it;
    `_school_day` below gives the same day as a string, for bucketing. One
    conversion feeding both keeps the report and the rollup from disagreeing
    about which day a reading belongs to.
    """
    parsed = _parse_ts(ts)
    return None if parsed is None else parsed.astimezone(tz).date()


def _school_day(ts, tz: tzinfo) -> str:
    """`_school_date` as YYYY-MM-DD, for bucketing.

    Empty string for anything unparseable -- no day matches that, so a bad
    timestamp drops out rather than silently joining a bucket.
    """
    resolved = _school_date(ts, tz)
    return "" if resolved is None else resolved.isoformat()


def _credit_session_to_user_stats(user_id: str, total_q: int, correct: int) -> None:
    """Add one closed session's answers to the student's lifetime totals.

    Shared by every close path (`/end`, the stale-session sweep, `class_live`)
    so none of them can close a session without crediting it.

    Never raises: runs alongside the writes that close a session, and a
    failure here must not cost the student their session record.
    """
    if not total_q:
        # Skip rather than write a zero-row, so a student who's never answered
        # anything has no `user_stats` row -- which `/api/stats/*` already
        # treats as "no data yet".
        return
    try:
        existing = supabase.table("user_stats").select("*").eq("user_id", user_id).execute()
        now = _utc_now().isoformat()
        if existing.data:
            s = existing.data[0]
            supabase.table("user_stats").update({
                "total_questions": (s.get("total_questions") or 0) + total_q,
                "total_correct":   (s.get("total_correct")   or 0) + correct,
                "last_session_at": now,
                "updated_at":      now,
            }).eq("user_id", user_id).execute()
        else:
            supabase.table("user_stats").insert({
                "user_id":          user_id,
                "total_questions":  total_q,
                "total_correct":    correct,
                "current_streak":   0,
                "best_streak":      0,
                "last_session_at":  now,
            }).execute()
    except Exception as e:                                     # noqa: BLE001
        print(f"[stats] could not credit {total_q} answers for {user_id[:8]}: {e}")


def _discard_if_nothing_recorded(session_id: str, questions,
                                 answers_counted: bool = False) -> bool:
    """Delete a session that answered nothing and recorded nothing. True if gone.

    Pressing Connect Headband creates a session, so every failed pairing
    attempt leaves an empty row behind that would otherwise clutter History.
    A row with nothing on either side of it isn't academic history worth
    keeping.

    Deletes on absence, so it's guarded like `sweep_orphan_charts`: a failed
    read keeps the session rather than risk deleting one that had data. Any of
    the four tables having a row is enough to keep it.

    Checks `session_answers` directly rather than trusting the counter:
    `record_answer` writes the answer row and bumps `questions_answered` in two
    separate statements, so the counter can lag behind real answers. Trusting
    it could delete an answered session that merely looked empty.
    """
    if questions:
        return False
    # Skip the session_answers re-check only when the caller just counted it
    # from the database and got zero -- a cache fallback still leaves the
    # check in place.
    tables = ("cognitive_signals", "face_signals", "heart_signals") if answers_counted \
        else ("session_answers", "cognitive_signals", "face_signals", "heart_signals")
    for table in tables:
        try:
            rows = supabase.table(table).select("session_id") \
                .eq("session_id", session_id).limit(1).execute().data or []
        except Exception as e:                                 # noqa: BLE001
            print(f"[session:discard] could not check {table} for {session_id}: {e}")
            return False
        if rows:
            return False
    try:
        supabase.table("sessions").delete().eq("id", session_id).execute()
    except Exception as e:                                     # noqa: BLE001
        print(f"[session:discard] could not delete {session_id}: {e}")
        return False
    return True


def _rollup_session_days(user_id: str, started_at, ended_at) -> None:
    """Recompute the daily rollup for the school days this session touched.

    Called as a session closes, rather than at expiry -- otherwise the job that
    deletes raw data would also be the first to read the summary of it.

    Never raises: a failure here shouldn't cost the student their session
    record or stats update. The writer is idempotent, so the next close on
    that day repairs it; the delete job refuses to remove a day with no
    rollup, which backstops a persistently broken write.

    Usually one day; a session spanning local midnight touches two.
    """
    tz = _school_timezone()
    try:
        now = _utc_now()
        day = _school_date(started_at, tz) or _school_date(now, tz)
        end_day = _school_date(ended_at, tz) or _school_date(now, tz)
        # Guard against bad timestamps: a corrupt started_at years back would
        # step a day at a time all the way to today, and started_at after
        # ended_at (clock skew, bad write) would silently loop zero times.
        # Both fall back to rolling up just the closing day.
        span = (end_day - day).days
        if span < 0 or span > 7:
            print(f"[rollup] {user_id[:8]}: implausible span {day}..{end_day} "
                  f"({span}d), rolling up the closing day only")
            day = end_day
        failures = 0
        while day <= end_day:
            # try/except per day, not around the whole loop -- a failure on
            # day one of a two-day session must not skip day two, which is the
            # closing day this call exists to roll up.
            try:
                supabase.rpc("rollup_signal_day", {
                    "p_user_id": user_id,
                    "p_day": day.isoformat(),
                    "p_timezone": tz.key,
                }).execute()
            except Exception as e:
                failures += 1
                print(f"[rollup] {user_id[:8]} {day}: {e}")
            day += timedelta(days=1)
        if failures:
            print(f"[rollup] {user_id[:8]}: {failures} day(s) not rolled up")
    except Exception as e:
        # Covers the date arithmetic and timezone read, shared across every
        # day and not something a per-day retry can fix.
        print(f"[rollup] {user_id[:8]}: {e}")


def _claim_session_close(session_id: str, ended_at: str) -> bool:
    """Stamp `ended_at` on a session that has none yet. True if this caller won.

    Three sites can close a session, and two racing closes on the same session
    must not both run -- both would credit `user_stats` with the session's
    cumulative counts, double-counting every answer.

    The conditional update is the claim: `is_("ended_at", "null")` matches at
    most one row, and Postgres serialises the two writers, so exactly one wins.

    An empty result means zero rows matched -- postgrest-py returns the updated
    row by default, so nothing here needs a second SELECT to confirm it.
    `test_postgrest_update_returns_the_updated_row` pins that assumption, since
    a client-library change here would make every close silently look lost.

    Never raises: a session that can't be stamped must not take its caller down.
    """
    try:
        claimed = supabase.table("sessions").update({"ended_at": ended_at}) \
            .eq("id", session_id).is_("ended_at", "null").execute().data or []
    except Exception as e:                                     # noqa: BLE001
        print(f"[session:close] could not stamp {session_id}: {e}")
        return False
    return bool(claimed)


def _answer_counts(session_id: str, session: dict) -> tuple[int, int, bool]:
    """What a closing session actually answered -- the rows, not just the counter.

    `sessions.questions_answered` is a denormalised cache, written in a
    separate statement from the answer row, so it can lag behind the truth.
    Counting from `session_answer_counts` (a single SQL query, no row cap)
    avoids crediting a stale low number.

    Returns `(questions, correct, counted)`. `counted` says whether the numbers
    came from the rows or fell back to the stored counter -- a *trusted* zero
    means `session_answers` really is empty, so the discard check doesn't need
    to ask again.

    Falls back to the stored counter on a failed read, or whenever the counted
    rows come back *fewer* than the counter -- crediting less than a previous
    reading would lose a student's work.
    """
    stored_q = session.get("questions_answered") or 0
    stored_c = session.get("correct_answers") or 0
    try:
        res = supabase.rpc("session_answer_counts",
                           {"p_session_id": session_id}).execute()
    except Exception as e:                                     # noqa: BLE001
        if "PGRST202" in str(e):
            print(f"[session:close] session_answer_counts is missing from the "
                  f"database -- apply 20260826000000; crediting the stored "
                  f"counter until then: {e}")
        else:
            print(f"[session:close] could not recount answers for {session_id}: {e}")
        return stored_q, stored_c, False
    rows = res.data or []
    if isinstance(rows, dict):
        rows = [rows]
    if not rows:
        return stored_q, stored_c, False
    counted_q = rows[0].get("total") or 0
    if counted_q < stored_q:
        return stored_q, stored_c, False
    return counted_q, rows[0].get("correct") or 0, True


# ─── session alerts ───────────────────────────────────────────────────────
#
# Operational facts about a session, for the teacher who owns the class. Never
# a claim about the student: `signal_fusion` produces a "stressed" label and
# it is deliberately not routed here, for the reason the `attention` tiles
# were removed -- a timestamped event reads as objective, and that inference
# is not validated on children. See `20260901000000_session_alerts.sql`.

# Which close site is running. `_close_session` cannot tell, and the
# difference is the entire content of `session_auto_closed`.
CLOSED_BY_STUDENT = "student"
CLOSED_BY_SWEEP = "stale_sweep"

# How long a session may stay open before it is treated as abandoned rather
# than in progress.
#
# Deliberately far longer than any real lesson: `session_duration_minutes` caps
# out at an hour, and this is the threshold past which a session is closed *for*
# a student who is not there. Closing one that is genuinely in progress would
# discard the question they are part way through answering, so the cost is
# asymmetric and this errs long.
#
# **This is an age, not an idleness.** A session open for two hours with a
# student answering throughout is not stale by this measure, and that is
# accepted: `class_live` is the surface that computes real last-activity from
# signal rows and answers, and it keeps its own much tighter `_STALE_AFTER_SEC`.
# What this one exists for is the session nobody has touched since June.
_SESSION_ABANDONED_AFTER_SEC = _env_number(
    "SESSION_ABANDONED_AFTER_HOURS", 6.0, float, minimum=1.0) * 3600

# How often the background sweeper looks. Zero disables it entirely, which is
# what tests and any deployment preferring the on-demand sweeps should use.
_STALE_SWEEP_INTERVAL_SEC = _env_number(
    "STALE_SWEEP_INTERVAL_SECONDS", 900.0, float, minimum=0.0)

# Rows per pass. A backlog is worked through over several passes rather than in
# one long transaction -- each close runs the full sequence, including a chart
# render and four storage writes.
_STALE_SWEEP_BATCH = 50

ALERT_SESSION_AUTO_CLOSED = "session_auto_closed"
ALERT_SIGNALS_MISSING = "signals_missing"


def _session_had_signals(session_id: str) -> bool | None:
    """Whether any cognitive row reached this session. None if unreadable.

    Three states, as everywhere else: rows arrived, none did, or the question
    could not be answered. The last must not raise a `signals_missing` alert
    -- accusing a deployment of a silent recording failure on the strength of
    a failed count is the same class of error as reporting a failed read as a
    quiet week.

    Only `cognitive_signals` is consulted. It is the channel the alert is
    about: the headband is the primary sensor, the camera is opt-in and
    emotion-only, and a session with a working headband and no camera is not
    a fault.
    """
    try:
        res = supabase.table("cognitive_signals").select("id") \
            .eq("session_id", session_id).limit(1).execute()
        return bool(res.data)
    except Exception as e:                                     # noqa: BLE001
        print(f"[alerts] could not check signals for {session_id}: {e}")
        return None


def _recording_was_expected(user_id: str) -> bool | None:
    """Whether EEG *should* have been recording. None when that cannot be told.

    Three states, deliberately the same shape as `_session_had_signals` beside
    it, because `signals_missing` is the conjunction of the two and either one
    being unknown makes the alert a guess.

    **A try/except is not enough here, which is the whole reason this is a
    function.** `_consent()` catches its own read failure and returns a
    fail-closed dict rather than raising, and `_may_record` spreads that dict
    straight through -- so an unreadable `signal_consent` arrives as
    `record_eeg: False` with nothing thrown. Read as a bool that is
    indistinguishable from a student who declined the headband, and it is the
    likelier failure of the two.

    `_retention_window()` has exactly the same shape: `WINDOW_UNREADABLE`
    denies, so a failed read of the school year also lands as `record_eeg:
    False` without raising.

    Both already carry the flag that tells them apart -- `retrieved` and
    `window_state` -- so this reads them rather than inferring from the
    composed answer. A genuinely declined channel, a closed year and a
    disabled recording flag all still return False: nothing was supposed to
    arrive in any of those, and alerting would train a teacher to ignore the
    feed.
    """
    try:
        gate = _may_record(user_id)
    except Exception as e:                                     # noqa: BLE001
        print(f"[alerts] could not read recording state for {user_id[:8]}: {e}")
        return None
    # `is False`, not falsiness: a payload predating the flag has no opinion
    # and must not be read as a failed read.
    if gate.get("retrieved") is False:
        return None
    if gate.get("window_state") == WINDOW_UNREADABLE:
        return None
    return bool(gate.get("record_eeg"))


def _raise_session_alerts(user_id: str, session: dict,
                          closed_by: str, answered: int) -> None:
    """Emit whatever operational alerts this close earned.

    **Never raises.** It runs after the writes that matter -- the credit, the
    rollup -- and a failure to record an alert must not turn a completed
    session into a failed close. Same rule as `_rollup_session_days` and
    `_record_topic_attempt`.

    Both kinds are deduped by a unique index on `(session_id, kind)`, so a
    replayed close cannot double-report. That is a backstop rather than the
    primary guard, which is `_claim_session_close`.
    """
    sid = session.get("id")
    if not sid:
        return
    alerts = []

    if closed_by == CLOSED_BY_SWEEP:
        # The student did not end this. It timed out and was closed for them,
        # which means they closed the tab, lost the network, or walked away
        # mid-lesson -- and their work was credited, so it is worth knowing.
        # `detail` deliberately carries no timestamps. They are columns on the
        # session this alert already points at, so copying them here would be a
        # second source of truth for the same fact -- and writing the end-stamp
        # key as a literal would make `conftest.close_sites()` read this
        # function as a fourth close site. That guard finds closers by scanning
        # for exactly that hand-written key, so it is worth not blunting; note
        # it also means this comment cannot spell the key out.
        alerts.append({
            "kind": ALERT_SESSION_AUTO_CLOSED,
            "detail": {"questions_answered": answered},
        })

    expected = _recording_was_expected(user_id)
    if expected is None:
        # Unknown, not "no". Logged because this is the one branch with nothing
        # to show for it -- no alert, and a real recording outage during a
        # database blip would otherwise pass in silence.
        print(f"[alerts] cannot tell whether recording was expected for "
              f"{user_id[:8]}; withholding {ALERT_SIGNALS_MISSING}")
    elif expected:
        had = _session_had_signals(sid)
        # `is False`, never falsiness: None means the count failed, and that
        # is not evidence of a silent recording failure.
        if had is False:
            alerts.append({
                "kind": ALERT_SIGNALS_MISSING,
                "detail": {"questions_answered": answered, "channel": "eeg"},
            })

    if not alerts:
        return
    try:
        supabase.table("session_alerts").upsert(
            [{"user_id": user_id, "session_id": sid, **a} for a in alerts],
            on_conflict="session_id,kind", ignore_duplicates=True,
        ).execute()
    except Exception as e:                                     # noqa: BLE001
        print(f"[alerts] could not record {len(alerts)} alert(s) for {sid}: {e}")


def _close_session(user_id: str, session: dict, ended_at: str,
                   closed_by: str = CLOSED_BY_STUDENT) -> dict:
    """Everything a session close does, including stamping `ended_at`.

    Three endpoints close sessions (`/end`, the stale sweep, `class_live`), and
    they all go through this one function so the sequence can't drift between
    them. Order matters: discard runs first, since a rollup and archive of an
    about-to-be-deleted session is wasted work.

    The stamp happens first and is the claim to do everything else -- two
    closes racing on the same session must not both run the full sequence.

    Stopping the poller stays at each call site, since the ids differ, but it
    must happen *before* this call -- `test_every_close_site_stops_the_poller_first`
    pins that ordering.

    `closed_by` says which of the three sites this is, because the function
    cannot tell and the difference is the whole content of one alert: a
    session the student ended is ordinary, one the stale sweep ended is a
    lesson that stopped without them. It defaults to the student so a new call
    site has to opt *in* to raising an alert rather than out of it -- a
    wrongly-raised alert is worse than a missing one on a surface whose value
    is that every row means something happened.
    """
    sid = session["id"]
    if not _claim_session_close(sid, ended_at):
        # Someone else already stamped it -- doing this again would double-
        # count the session's answers in the lifetime totals.
        return {"discarded": False, "already_closed": True}

    total_q, correct, counted = _answer_counts(sid, session)

    # counted=True means the zero came from the database, so the discard check
    # doesn't need to ask session_answers the same question again.
    if _discard_if_nothing_recorded(sid, total_q, answers_counted=counted):
        return {"discarded": True}

    _credit_session_to_user_stats(user_id, total_q, correct)
    _rollup_session_days(user_id, session.get("started_at"), ended_at)
    # After the discard, and deliberately: an alert about a session that is
    # about to stop existing would be deleted by the cascade a moment later,
    # and an empty session is not an operational fault worth a teacher's
    # attention. Never raises -- see `_raise_session_alerts`.
    _raise_session_alerts(user_id, session, closed_by, total_q)
    # Off the request path and last: reads three tables and hits object
    # storage four times, not worth holding a request open for.
    chart_archive.schedule(supabase, sid, user_id)
    return {"discarded": False}


# ─── the background sweep for abandoned sessions ──────────────────────────
#
# Both existing sweeps are *on demand*: `start_session` collects a student's
# strays when they next start one, and `class_live` collects a class's when a
# teacher opens the live monitor. A student who simply never comes back is
# collected by neither, so their sessions sit open indefinitely -- seen in
# production as rows still marked live two months after they were started.
#
# It runs in the backend rather than as a `pg_cron` job on purpose. Closing a
# session is not a matter of stamping `ended_at`: it credits the lifetime
# totals, writes the daily rollup, archives four charts to storage and raises
# the alerts, none of which SQL can do. A cron job that stamped the column
# would be a fourth close site that silently skipped all of it -- exactly what
# `conftest.close_sites()` exists to catch.

_stale_sweep_stop = threading.Event()
_stale_sweep_thread: threading.Thread | None = None


def _sweep_abandoned_sessions(limit: int = _STALE_SWEEP_BATCH) -> dict:
    """Close sessions left open past `_SESSION_ABANDONED_AFTER_SEC`.

    Returns counts rather than raising, so the caller (a loop) can log and
    carry on. One student's broken session must not stop the sweep reaching
    everyone else's, so each close is guarded individually.
    """
    cutoff = (_utc_now() - timedelta(seconds=_SESSION_ABANDONED_AFTER_SEC)).isoformat()
    try:
        rows = (supabase.table("sessions")
                .select("id, user_id, started_at, questions_answered, correct_answers")
                .is_("ended_at", "null").lt("started_at", cutoff)
                .order("started_at").limit(limit).execute().data or [])
    except Exception as e:                                     # noqa: BLE001
        print(f"[stale_sweep] could not list abandoned sessions: {e}")
        return {"found": 0, "closed": 0, "discarded": 0, "failed": 0,
                "retrieved": False}

    closed = discarded = failed = 0
    for s in rows:
        uid = s.get("user_id")
        if not uid:
            continue
        try:
            # The poller first, and for the same reason every other close site
            # does it first: a tick landing after the discard check has looked
            # leaves a row pointing at a session that was just deleted.
            eeg_poller.stop(s["id"], uid)
            # Marked as a sweep, so an abandoned session with real work in it
            # raises `session_auto_closed` like the other two sweeps do.
            out = _close_session(uid, s, _utc_now().isoformat(),
                                 closed_by=CLOSED_BY_SWEEP)
            if out.get("discarded"):
                discarded += 1
            else:
                closed += 1
        except Exception as e:                                 # noqa: BLE001
            failed += 1
            print(f"[stale_sweep] could not close {s.get('id')}: {e}")
    if rows:
        print(f"[stale_sweep] {len(rows)} abandoned: {closed} closed, "
              f"{discarded} discarded, {failed} failed")
    return {"found": len(rows), "closed": closed, "discarded": discarded,
            "failed": failed, "retrieved": True}


def _stale_sweep_loop() -> None:
    """Sweep on an interval until asked to stop.

    Waits on the stop event rather than sleeping, so shutdown is immediate
    rather than up to an interval away -- the same rule the poller threads
    follow, and the reason this thread can be joined rather than abandoned to
    interpreter teardown.
    """
    while not _stale_sweep_stop.wait(_STALE_SWEEP_INTERVAL_SEC):
        try:
            _sweep_abandoned_sessions()
        except Exception as e:                                 # noqa: BLE001
            # The loop outliving one bad pass is the whole point of a sweeper.
            print(f"[stale_sweep] pass failed: {e}")


def start_stale_sweeper() -> bool:
    """Start the background sweep, unless it is switched off or already running.

    Safe to run in several processes at once: `_claim_session_close` is an
    atomic conditional update, so two workers racing on one session means one
    of them does the work and the other sees it was already closed.
    """
    global _stale_sweep_thread
    if _STALE_SWEEP_INTERVAL_SEC <= 0:
        print("[stale_sweep] disabled (STALE_SWEEP_INTERVAL_SECONDS=0)")
        return False
    if _stale_sweep_thread and _stale_sweep_thread.is_alive():
        return False
    _stale_sweep_stop.clear()
    _stale_sweep_thread = threading.Thread(
        target=_stale_sweep_loop, name="stale-sweep", daemon=True)
    _stale_sweep_thread.start()
    return True


def stop_stale_sweeper(timeout: float = 5.0) -> None:
    """Ask the sweep to finish and wait for it.

    Joined, not just signalled: this thread prints, and a print landing during
    interpreter shutdown while the stdout lock is held is a fatal
    `_enter_buffered_busy` abort -- exit code 134 after every test passed,
    which reads as unrelated flake. Same rule as `eeg_poller.stop_all`.
    """
    global _stale_sweep_thread
    _stale_sweep_stop.set()
    thread, _stale_sweep_thread = _stale_sweep_thread, None
    if thread and thread.is_alive():
        thread.join(timeout=timeout)


def _may_record(student_id: str) -> dict:
    """Consent **and** the retention window, which are different questions.

    Not folded into `_consent`: that helper answers "what did this family
    agree to", and is read by reporting surfaces and the consent screen, none
    of which should change their answer just because the school year ended.
    So the window is composed with consent here instead, at the recording
    sites, with `window_state` explaining why nothing records when consent
    alone would allow it.
    """
    flags = _feature_flags()
    # A live bypass substitutes a fully-consenting answer, but every other
    # gate (window, per-channel switches) still applies. `consent_bypassed`
    # rides along so a caller reporting *why* something recorded doesn't claim
    # the student actually agreed.
    enforced = _consent_enforcement_active(flags)
    consent = _consent(student_id) if enforced else {
        **_CONSENT_ENABLED_ALL, "retrieved": True, "exists": False}
    window = _retention_window()
    recording = window["state"] not in _WINDOW_DENIED
    return {**consent,
            "window_state": window["state"],
            "window_starts_on": window["starts_on"],
            "window_ends_on": window["ends_on"],
            "consent_bypassed": not enforced,
            # The per-channel flags a recording caller should read -- ANDed
            # with consent, never ORed, so a flag can withhold recording but
            # never grant it against a student's refusal.
            "record_eeg": (recording and flags["recording_eeg_enabled"]["enabled"]
                           and bool(consent.get("eeg_enabled"))),
            "record_headband_optical": (
                recording and flags["recording_heart_enabled"]["enabled"]
                and bool(consent.get("headband_optical_enabled"))),
            "record_camera": (recording and flags["recording_camera_enabled"]["enabled"]
                              and bool(consent.get("camera_enabled")))}


def _as_sentence(text: str) -> str:
    """A reason string as a standalone sentence.

    Reasons are written lowercase and unpunctuated for embedding in a `reason`
    field, but an HTTP error detail needs a capital and a full stop.
    """
    if not text:
        return text
    return text[0].upper() + text[1:] + ("" if text.endswith((".", "!", "?")) else ".")


# An unknown state has nothing to report -- the denial itself already comes
# from `_WINDOW_DENIED`.
_NO_MEANING = _WindowMeaning(False, None, None)


def _window_meaning(state: str) -> _WindowMeaning:
    """What a window state means. One accessor; callers pick a field.

    The window reason wins over the consent one: if the year hasn't started,
    nothing is recording, and reporting "eeg not consented" would wrongly send
    a parent to the consent screen.
    """
    return _WINDOW_STATES.get(state) or _NO_MEANING


def _not_recording_reason(gate: dict, declined: str,
                          unavailable: str = "consent unavailable") -> str:
    """Why this channel is not recording. Window first, then consent.

    Callers pass their own full sentences rather than fragments to compose --
    the sites word them differently ("eeg not consented" vs "no consented
    heart sensor"), so building it here avoided nonsense concatenations.
    """
    window = _window_meaning(gate.get("window_state")).reason
    if window:
        return window
    if not gate.get("retrieved"):
        return unavailable
    return declined


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
    """Which optional channels a report may read, and whether consent was readable.

    A named tuple, not a plain one, so fields can't get swapped by position
    (heart vs. emotion) the way they once did by accident.

    `consent_retrieved` tells "nobody consented" apart from "we couldn't read
    consent" -- callers need both before deciding a channel was declined.

    The `*_revoked_at` timestamps let a surface say *when* a channel was
    switched off, so it can show "Off since 3 August" instead of a blank tile
    that looks like a bug.
    """
    heart: bool
    emotion: bool
    consent_retrieved: bool
    # Defaulted so existing call sites keep working without threading these through.
    heart_revoked_at: str | None = None
    emotion_revoked_at: str | None = None
    # EEG is not a read filter like the two above -- there's no p_include_cognitive
    # on the summary RPCs, so the cognitive channel is always read, and withdrawal
    # keeps past data rather than hiding it. This lets a tile say "Off since <date>"
    # instead of "No sensor" for a channel that used to record.
    eeg: bool = True
    eeg_revoked_at: str | None = None


def _reportable_channels(student_id: str, want_emotion: bool = True,
                         want_heart: bool = True) -> ReportChannels:
    """Which optional channels a report may read: consent AND what was asked for.

    Consent decides what was ever recorded and is not the viewer's to override.

    `want_*` is a leftover viewer-side narrowing no client sends any more (the
    old frontend switch it served is retired; today's replacement, the
    teacher's "Hide sensor data" toggle, is client-side and changes no
    request). Kept because it's cheap and lets a caller ask for less than
    consent allows -- it is not a privacy boundary.

    Consent is resolved here, not trusted from a query parameter, so a
    revoked channel's rows are never read even if stale ones exist. Fails
    closed like `_consent`: an unreadable consent row reports nothing.
    """
    consent = _consent(student_id)
    heart = bool(consent.get("headband_optical_enabled")) or bool(consent.get("camera_enabled"))
    emotion = bool(consent.get("camera_enabled"))
    # Heart can come from either sensor, so it's off only when both are, and the
    # honest revoked date is the later of the two -- when it actually stopped.
    heart_revoked = None
    if not heart:
        stamps = [consent.get("headband_optical_revoked_at"),
                  consent.get("camera_revoked_at")]
        stamps = [t for t in stamps if t]
        # Compared as instants via `_parse_ts`, not as text, so a differently
        # formatted timestamp can't sort wrong. An unparseable stamp sorts last.
        heart_revoked = max(
            stamps,
            key=lambda t: _parse_ts(t) or datetime.min.replace(tzinfo=timezone.utc),
            default=None)
    eeg = bool(consent.get("eeg_enabled"))
    return ReportChannels(heart=want_heart and heart,
                          emotion=want_emotion and emotion,
                          consent_retrieved=bool(consent.get("retrieved")),
                          heart_revoked_at=heart_revoked,
                          emotion_revoked_at=None if emotion else consent.get("camera_revoked_at"),
                          # No `want_eeg`: the cognitive channel is always read, so
                          # this just reports consent. Null while on, so a tile can't
                          # show a revocation date next to live data.
                          eeg=eeg,
                          eeg_revoked_at=None if eeg else consent.get("eeg_revoked_at"))


def _summary_rpc(name: str, params: dict, include_heart: bool, include_emotion: bool):
    """Call a summary RPC with the facial opt-out threaded in.

    Deliberately not wrapped in a try/except: a database missing p_include_face
    (bad rollback, stale environment) should error loudly, not silently return
    a wrong answer.
    """
    return supabase.rpc(name, {**params,
                               "p_include_heart": include_heart,
                               "p_include_emotion": include_emotion,
                               # School timezone, not UTC, so this matches how
                               # `_weekly_signal_report` buckets "this week".
                               "p_timezone": _retention_window().get("timezone") or "UTC"}).execute()


def _signal_summary(student_id: str, days: int = 7, include_heart: bool = True,
                    include_emotion: bool = True,
                    consent_retrieved: bool = True,
                    emotion_revoked_at: str | None = None,
                    heart_revoked_at: str | None = None,
                    eeg_enabled: bool = True,
                    eeg_revoked_at: str | None = None) -> dict:
    """Just the headline averages, aggregated in Postgres.

    Cheaper than the full report for a list that loads every visit: it
    aggregates in the database instead of pulling thousands of raw rows.

    Both flags are passed into the aggregate so a declined channel is never
    read, matching what `_weekly_signal_report` guarantees.

    Carries `dominant_emotion`, which `_signal_summaries` does not -- only
    this single-student RPC computes it.
    """
    row = None
    retrieved = True
    try:
        res = _summary_rpc("student_signal_summary",
                           {"p_student_id": student_id, "p_days": days},
                           include_heart, include_emotion)
    except Exception as e:
        print(f"[signal_summary] {e}")
        # This endpoint still answers 200, so a failed read must not look like
        # a student who recorded nothing -- `retrieved=False` is what tells them apart.
        retrieved = False
    else:
        rows = res.data or []
        row = rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else None)
    summary = _shape_summary(row, include_heart, include_emotion, retrieved,
                             consent_retrieved,
                             emotion_revoked_at=emotion_revoked_at,
                             heart_revoked_at=heart_revoked_at,
                             eeg_enabled=eeg_enabled,
                             eeg_revoked_at=eeg_revoked_at)
    # Set here, not in `_shape_summary`, which the batch RPC also uses -- adding
    # it there would put an always-null `dominant_emotion` on every child in a
    # batch, claiming "no emotion" for a field never requested.
    summary["dominant_emotion"] = (row or {}).get("dominant_emotion") if include_emotion else None
    return summary


_EMPTY_SUMMARY = {"consent_retrieved": True,
                  "focus": None, "stress": None, "engagement": None,
                  "face_attention": None, "heart_rate_bpm": None,
                  "rmssd_ms": None, "sessions": 0,
                  "cognitive_samples": 0, "face_samples": 0, "heart_samples": 0,
                  # `face_included` is kept alongside the newer flags because
                  # existing consumers branch on it; removing it would read as
                  # "channel excluded" everywhere it's checked. Now means emotion
                  # specifically.
                  "face_included": True, "emotion_included": True,
                  "heart_included": True, "retrieved": True,
                  # True: an absent field means an old payload from before this
                  # existed, and defaulting to off would tell every old reader
                  # the headband was switched off -- a decision nobody
                  # made. Same reasoning as `face_included`'s fallback.
                  "eeg_enabled": True, "eeg_revoked_at": None}


def _shape_summary(row, include_heart: bool = True, include_emotion: bool = True,
                   retrieved: bool = True, consent_retrieved: bool = True,
                   emotion_revoked_at: str | None = None,
                   heart_revoked_at: str | None = None,
                   eeg_enabled: bool = True,
                   eeg_revoked_at: str | None = None) -> dict:
    """The summary payload.

    `retrieved` tells a third state apart: "nothing recorded", "not requested",
    or the aggregate query itself failed. Callers of this still answer 200 with
    all-default fields on a failed read, so without this flag a zero count and
    a failed read look identical. Any surface showing "no data" must check it.

    Defaults True, since that's correct for every path that actually reached
    the database, including a legitimate empty result.
    """
    if not row:
        return {**_EMPTY_SUMMARY, "face_included": include_emotion,
                "emotion_included": include_emotion, "heart_included": include_heart,
                "retrieved": retrieved, "consent_retrieved": consent_retrieved,
                "emotion_revoked_at": emotion_revoked_at,
                "heart_revoked_at": heart_revoked_at,
                "eeg_enabled": eeg_enabled, "eeg_revoked_at": eeg_revoked_at}
    return {
        "focus": row.get("focus"),
        "stress": row.get("stress"),
        "engagement": row.get("engagement"),
        "face_attention": row.get("face_attention"),
        # Absolute units, unlike every other figure here (0..1 ratios) --
        # the frontend's `toPct()` must not be applied to them.
        "heart_rate_bpm": row.get("heart_rate_bpm"),
        "rmssd_ms": row.get("rmssd_ms"),
        "sessions": row.get("sessions") or 0,
        # A None average next to a 0 count means "nothing recorded"; a None
        # average next to a nonzero count means "recorded but unusable".
        "cognitive_samples": row.get("cognitive_samples") or 0,
        "face_samples": row.get("face_samples") or 0,
        "heart_samples": row.get("heart_samples") or 0,
        # Kept as an alias for emotion_included and means the emotion channel
        # specifically, not "anything facial". Deprecated -- read
        # emotion_included in new code.
        "face_included": include_emotion,
        "emotion_included": include_emotion,
        "heart_included": include_heart,
        # A row got here, so the read succeeded. Still carried explicitly so
        # this field is present on every payload.
        "retrieved": retrieved,
        # `retrieved` is about the aggregate query; this is about the consent
        # read that decided which channels could be asked for. Both False means
        # "we couldn't find out", not "declined".
        "consent_retrieved": consent_retrieved,
        # When, not just whether. "Off since 3 August" reads very differently
        # from a blank tile. Null while the channel is on.
        "emotion_revoked_at": emotion_revoked_at,
        "heart_revoked_at": heart_revoked_at,
        # Consent, not an inclusion flag. The cognitive channel has no
        # p_include_* param and is always read; withdrawal keeps past data, so
        # a tile should say "Off since <date>", not "No sensor".
        "eeg_enabled": eeg_enabled,
        "eeg_revoked_at": eeg_revoked_at,
    }


def _signal_summaries(student_ids: list[str], days: int = 7,
                      include_heart: bool = True,
                      include_emotion: bool = True,
                      channels_by_student: dict | None = None) -> dict[str, dict] | None:
    """Headline averages for many students in one round-trip, instead of one per child.

    None means the read failed; {} means it succeeded with nothing to return.
    Callers need the difference -- they fill in a default summary for any
    missing child, and that default must say whether it stands in for a failed
    query or a genuinely quiet child.

    `channels_by_student` carries the per-child consent fields the batch RPC
    can't: it groups children by flag pair, so it has no way to return a
    per-child revocation date or `consent_retrieved`. Pass the `ReportChannels`
    map and this stamps them onto each row. Omitted, the defaults stand.
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
    out = {}
    for r in rows:
        sid = r.get("student_id")
        if not sid:
            continue
        ch = (channels_by_student or {}).get(sid) or (channels_by_student or {}).get(str(sid))
        out[str(sid)] = _shape_summary(
            r, include_heart, include_emotion,
            consent_retrieved=ch.consent_retrieved if ch else True,
            emotion_revoked_at=ch.emotion_revoked_at if ch else None,
            heart_revoked_at=ch.heart_revoked_at if ch else None,
            eeg_enabled=ch.eeg if ch else True,
            eeg_revoked_at=ch.eeg_revoked_at if ch else None)
    return out


# How many weeks a trend may span. Bounded like every other report window --
# the read is one query whatever the range, but the payload is per week and a
# caller asking for 500 would build a chart nobody can read.
_TREND_MAX_WEEKS = 26


def _week_start(day: date) -> date:
    """The Monday of `day`'s week.

    Weeks start on Monday rather than Sunday because the buckets are school
    weeks and `day` is already a school day resolved in the school's own
    timezone -- see `_school_day`. Using the raw date is safe here for exactly
    that reason: the timezone was applied when the rollup row was written.
    """
    return day - timedelta(days=day.weekday())


def _signal_trend(student_id: str, weeks: int = 8, include_heart: bool = True,
                  include_emotion: bool = True,
                  consent_retrieved: bool = True,
                  emotion_revoked_at: str | None = None,
                  heart_revoked_at: str | None = None):
    """Week-over-week averages, read from the rollup and nothing else.

    Deliberately not built on `_weekly_signal_report`. That one reads the
    per-sample tables under a row cap, which is right for a week and wrong for
    six months: the cap trims oldest-first, so the early weeks of a long range
    would come back empty and read as a quiet term. `signal_daily_rollup` has
    one row per student per day per channel, so a half-year is a few hundred
    rows and no cap is needed.

    It is also the only source that outlives `expire_signal_rows`. A trend is
    the surface most likely to be read *after* a school year ends, which is
    precisely when the raw rows are gone.

    Weighted by `trusted_sample_count`, and that is not a preference -- it is
    what the stored averages were computed over. `rollup_signal_day` writes
    `avg(focus)` for cognitive and `avg(...) FILTER (WHERE trusted)` for heart;
    Postgres `avg()` skips nulls, so both denominators are the trusted count.
    Weighting by `sample_count` would divide by rows the average never saw.

    Three of the five averages carry an approximation, and it is the same one:
    the rollup stores a single count per channel, so any column whose nulls do
    not follow that count's is weighted slightly wrongly.

      * `avg_rmssd_ms` -- roughly one trusted window in five is gated out of
        RMSSD (see CLAUDE.md on `rmssd_rejected_by`) while the heart count
        counts trusted rows.
      * `avg_stress` and `avg_engagement` -- `trusted_sample_count` for the
        cognitive channel is `count(*) FILTER (WHERE focus IS NOT NULL)`, and
        `map_eeg_to_cognitive` derives the three from `focus_score`,
        `calm_score` and `confidence` independently. Only `contact_poor` nulls
        all three together; an ordinary row can carry focus without calm.

    `avg_focus` and `avg_heart_rate_bpm` are exact. In every case the error is
    between days, never within one, and correcting it needs a per-column count
    the schema does not have and a backfill that deleted rows cannot supply.
    """
    tz = _school_timezone()
    school_today = _utc_now().astimezone(tz).date()
    # Whole weeks back from the Monday of the current week, so the range always
    # starts on a week boundary. Counting back `weeks * 7` days from today
    # would put a part-week at each end and make the first bar a fraction of
    # the others while looking like a full one.
    first_monday = _week_start(school_today) - timedelta(weeks=weeks - 1)

    # A declined channel is not read, rather than read and discarded. One
    # filtered query, not three -- an earlier version of this took "one query
    # or one per channel" as the only options and let consent filter the
    # aggregation instead, which is the exact shape CLAUDE.md's rule about the
    # facial opt-out warns against: never fall back to a query that reads what
    # the caller opted out of.
    channels = ["cognitive"]
    if include_heart:
        channels.append("heart")
    if include_emotion:
        channels.append("emotion")

    rows: list = []
    retrieved = True
    try:
        rows = (supabase.table("signal_daily_rollup").select("*")
                .eq("user_id", student_id)
                .in_("channel", channels)
                .gte("day", first_monday.isoformat())
                .lte("day", school_today.isoformat())
                .execute().data or [])
    except Exception as e:                                     # noqa: BLE001
        # Same three-state rule as every other reporting helper: an empty list
        # is also what a genuinely quiet term looks like, so the flag is the
        # only thing that tells them apart.
        print(f"[signal_trend] {student_id}: {e}")
        retrieved = False

    # Every week in range, including the empty ones. A week with no rows has to
    # appear as a gap in the series rather than be dropped, or a fortnight off
    # school renders as the weeks either side sitting next to each other.
    buckets: dict[date, dict] = {}
    for i in range(weeks):
        monday = first_monday + timedelta(weeks=i)
        buckets[monday] = {
            "week_start": monday.isoformat(),
            "sums": {k: [0.0, 0] for k in ("focus", "stress", "engagement",
                                           "heart_rate_bpm", "rmssd_ms")},
            "cognitive_samples": 0,
            "heart_samples": 0,
            "emotion_samples": 0,
            "days_with_data": set(),
            "heart_sources": set(),
            "emotion_counts": {},
        }

    COLUMNS = {
        "cognitive": (("focus", "avg_focus"), ("stress", "avg_stress"),
                      ("engagement", "avg_engagement")),
        "heart": (("heart_rate_bpm", "avg_heart_rate_bpm"),
                  ("rmssd_ms", "avg_rmssd_ms")),
    }

    for r in rows:
        channel = r.get("channel")
        # Belt and braces against a row the filter above should never have
        # returned. Cheap, and the alternative is trusting a query string to
        # enforce a consent decision.
        if channel not in channels:
            continue
        try:
            day = date.fromisoformat(str(r.get("day")))
        except (TypeError, ValueError):
            continue
        b = buckets.get(_week_start(day))
        if b is None:
            continue

        n = r.get("trusted_sample_count") or 0
        b["days_with_data"].add(day.isoformat())
        if channel == "cognitive":
            b["cognitive_samples"] += n
        elif channel == "heart":
            b["heart_samples"] += n
            for s in (r.get("heart_sources") or []):
                b["heart_sources"].add(s)
        elif channel == "emotion":
            b["emotion_samples"] += n
            for label, count in (r.get("emotion_counts") or {}).items():
                b["emotion_counts"][label] = b["emotion_counts"].get(label, 0) + count

        for key, column in COLUMNS.get(channel, ()):
            value = r.get(column)
            # A null average is a day the channel recorded nothing usable, not
            # a zero. Contributing 0 would drag the week down by exactly the
            # days that measured nothing.
            if isinstance(value, (int, float)) and n > 0:
                b["sums"][key][0] += float(value) * n
                b["sums"][key][1] += n

    def _mean(pair):
        total, n = pair
        return round(total / n, 4) if n else None

    out = []
    for monday in sorted(buckets):
        b = buckets[monday]
        out.append({
            "week_start": b["week_start"],
            **{k: _mean(v) for k, v in b["sums"].items()},
            "cognitive_samples": b["cognitive_samples"],
            "heart_samples": b["heart_samples"],
            "emotion_samples": b["emotion_samples"],
            "days_with_data": len(b["days_with_data"]),
            # Sorted so a week's sources are stable between reads -- this rides
            # on a chart caption, and an order that shuffles reads as a change.
            "heart_sources": sorted(b["heart_sources"]),
            "emotion_distribution": b["emotion_counts"],
        })

    return {
        "weeks": out,
        "retrieved": retrieved,
        "heart_included": include_heart,
        "emotion_included": include_emotion,
        "consent_retrieved": consent_retrieved,
        "emotion_revoked_at": emotion_revoked_at,
        "heart_revoked_at": heart_revoked_at,
        "timezone": str(tz),
    }


def _weekly_signal_report(student_id: str, days: int = 7, include_heart: bool = True,
                          include_emotion: bool = True,
                          consent_retrieved: bool = True,
                          emotion_revoked_at: str | None = None,
                          heart_revoked_at: str | None = None):
    """Aggregate a student's recent EEG and facial signals for reporting.

    Returns averages, highlights and per-day buckets. Callers must have already
    established that the requester may see this student.

    A false flag skips that channel's query outright rather than fetching and
    discarding: the point is that the data isn't read at all, and it also drops
    the heaviest of the queries. Every field from that channel then comes back
    None, and `heart_included` / `emotion_included` tell the caller that means
    "not requested" rather than "nothing recorded".
    """
    tz = _school_timezone()
    # Midnight at the start of the earliest school day in range, in UTC for the
    # query. Using `now - days` in UTC instead would quietly clip the first few
    # hours of the oldest day wherever the school is behind UTC.
    school_today = _utc_now().astimezone(tz).date()
    # `datetime.min.time()`, not `time.min`: `time` here is the stdlib module
    # (imported for monotonic clocks), so `time.min` doesn't exist.
    since = datetime.combine(school_today - timedelta(days=days - 1),
                             datetime.min.time(),
                             tzinfo=tz).astimezone(timezone.utc).isoformat()

    def _fetch(table: str, ts_col: str, limit: int) -> tuple[list, bool, int | None, bool]:
        """Rows (newest first), whether the server withheld any, the total, and
        whether the read happened at all.

        Truncation is detected from the exact count, not `len(rows) >= limit`:
        PostgREST's own row ceiling can trim below our `.limit()` and leave
        `len(rows)` short of it, which would hide the truncation.

        The count is also useful on its own -- for sessions it's the real
        figure the report is about, regardless of whether the cap bound.

        The last element tells a failed read apart from a table that is simply
        empty; both otherwise return the same empty list.
        """
        try:
            res = supabase.table(table).select("*", count="exact") \
                .eq("user_id", student_id).gte(ts_col, since) \
                .order(ts_col, desc=True).limit(limit).execute()
            rows = res.data or []
            total = getattr(res, "count", None)
            if not isinstance(total, int):
                total = None
            # No reported count: fall back to the length heuristic rather than
            # claiming nothing was cut.
            was_cut = (total > len(rows)) if total is not None else len(rows) >= limit
            return rows, was_cut, total, True
        except Exception as e:
            print(f"[weekly_report:{table}] {e}")
            return [], False, None, False

    cog, cog_cut, _, cog_ok = _fetch("cognitive_signals", "ts", _REPORT_ROW_CAP)
    # ok=True with the opt-out on: nothing failed, there was just nothing asked
    # for. `face_included` says the query never ran.
    face, face_cut, _, face_ok = _fetch("face_signals", "ts", _REPORT_ROW_CAP) if include_emotion \
        else ([], False, None, True)
    heart, heart_cut, _, heart_ok = _fetch("heart_signals", "ts", _REPORT_ROW_CAP) if include_heart \
        else ([], False, None, True)
    sessions, ses_cut, ses_total, ses_ok = _fetch("sessions", "started_at", _SESSION_ROW_CAP)

    # The cap trims oldest-first, so the earliest days of a heavy week come
    # back empty and would read as "no activity" instead of "not retrieved".
    # Tracked per table -- combining into one OR'd flag lets rows from an
    # uncut table mask a cut one and hide which days were actually trimmed.
    def _oldest(rows: list, ts_col: str) -> str:
        return min([str(r.get(ts_col, "")) for r in rows if r.get(ts_col)], default="")

    truncated = cog_cut or face_cut or heart_cut or ses_cut
    # Converted to school days because `_coverage` compares them as strings,
    # which is only chronological within one calendar.
    cog_oldest_day = _school_day(_oldest(cog, "ts"), tz)
    face_oldest_day = _school_day(_oldest(face, "ts"), tz)
    ses_oldest_day = _school_day(_oldest(sessions, "started_at"), tz)
    heart_oldest_day = _school_day(_oldest(heart, "ts"), tz)

    # Bucketed once instead of per day: re-parsing every row's timestamp for
    # each of `days` iterations is wasted work at these row caps.
    def _by_school_day(rows: list, ts_col: str) -> dict:
        out: dict[str, list] = {}
        for r in rows:
            out.setdefault(_school_day(r.get(ts_col), tz), []).append(r)
        return out

    # The rollup covers days whose per-sample rows have been deleted -- one
    # query for the whole range, keyed on (day, channel). Its own `retrieved`
    # flag matters because once the delete job runs, the rollup *is* the
    # history: a failed read here means the week is unreadable, not empty.
    #
    # These totals feed the week's averages so they stay consistent with
    # `daily`, which already falls back to the rollup per day -- otherwise the
    # chart and the headline numbers would disagree once old rows are deleted.
    #
    # (sum, n) per metric, not a mean of daily means: days differ in sample
    # count, and averaging averages would weight a 4-sample day the same as a
    # 4000-sample one.
    rolled_totals: dict[str, list] = {k: [0.0, 0] for k in
                                      ("focus", "stress", "engagement",
                                       "heart_rate_bpm", "rmssd_ms")}
    rolled_emotions: dict[str, int] = {}
    rolled_sources: set[str] = set()

    rollup_by: dict[tuple[str, str], dict] = {}
    rollup_ok = True
    try:
        for r in (supabase.table("signal_daily_rollup").select("*")
                  .eq("user_id", student_id)
                  .gte("day", (school_today - timedelta(days=days - 1)).isoformat())
                  .lte("day", school_today.isoformat())
                  .execute().data or []):
            rollup_by[(str(r.get("day")), r.get("channel"))] = r
    except Exception as e:
        print(f"[weekly_report:rollup] {student_id}: {e}")
        rollup_ok = False

    cog_by_day = _by_school_day(cog, "ts")
    face_by_day = _by_school_day(face, "ts")
    heart_by_day = _by_school_day(heart, "ts")
    sessions_by_day = _by_school_day(sessions, "started_at")

    # Newest row that actually produced a measurement, not just the newest row:
    # cognitive and face rows can have their measurement columns nulled (poor
    # electrode contact, a face window FER+ refused) while the session
    # timeline still needs the row. Taking the newest row regardless made a
    # "Most recent readings" panel show nothing while the weekly average beside
    # it showed real data.
    #
    # Falls back to the newest row when none carries a measurement, so a
    # channel with only unusable windows still shows "Calibrating" rather than
    # "No sensor".
    latest_cognitive = next((r for r in cog if r.get("focus") is not None), None) \
        or (cog[0] if cog else None)
    latest_face = next((r for r in face if r.get("emotion") is not None), None) \
        or (face[0] if face else None)
    # Trusted only, matching every other heart figure in this payload.
    latest_heart = next((r for r in heart if r.get("trusted") is True), None)

    # Four states per table per day: ok+whole, ok+partial (the cap cut into
    # this day), ok+missing (the cap stopped before this day), and failed
    # (every day missing, not just the trimmed ones). A partial day is withheld
    # rather than averaged from a fraction, which would silently bias it, or
    # dropped, which would lose the days that did come back complete.
    #
    # A cap that happens to land exactly on a day boundary looks the same as a
    # partial day from here, so it's treated as partial too -- understating a
    # complete day rather than risking publishing a partial one as whole.
    def _coverage(ok: bool, cut: bool, oldest_day: str, day: str) -> tuple[bool, bool]:
        """(nothing was retrieved for this day, this day is complete)."""
        if not ok:
            return True, False          # the read failed; no day was covered
        if not cut:
            return False, True          # nothing was trimmed, so every day is whole
        if not oldest_day:
            # Trimmed with nothing to say how far it reached (zero cap, or rows
            # with no usable timestamp) -- treat as missing rather than whole.
            return True, False
        if day < oldest_day:
            return True, False          # the cap stopped before this day entirely
        return False, day > oldest_day  # == oldest_day is the day it cut into

    daily = []
    for i in range(days - 1, -1, -1):
        day = (school_today - timedelta(days=i)).isoformat()
        cog_missing, cog_whole = _coverage(cog_ok, cog_cut, cog_oldest_day, day)
        face_missing, face_whole = _coverage(face_ok, face_cut, face_oldest_day, day)
        ses_missing, ses_whole = _coverage(ses_ok, ses_cut, ses_oldest_day, day)
        # Per day, not per table: `heart_ok and not heart_cut` used to mark
        # every day unretrieved whenever the table as a whole hit its cap.
        heart_missing, heart_whole = _coverage(heart_ok, heart_cut, heart_oldest_day, day)
        # Skip the day only when nothing asked for could be retrieved at all.
        # Sessions have their own query and cap, so a day with trimmed signals
        # can still have an intact session count -- dropping it would lose that.
        if (cog_missing and (face_missing or not include_emotion)
                and (heart_missing or not include_heart) and ses_missing):
            continue
        # Raw rows where they exist, the rollup where they don't. Decided by
        # what's actually present rather than by comparing against the
        # retention window, so this can't drift out of sync with a second copy
        # of the boundary math. A rollup also wins over a partial raw day,
        # since it's a complete summary and the raw read is not.
        def _rolled(channel, raw_rows, whole, read_ok):
            """The rollup row to use for this day, or None to use the raw rows.

            Only used when the raw query actually ran (`read_ok`) -- a rollup
            can be stale, since it's written when a session closes and today's
            lags the one in progress, so a failed raw read must not silently
            fall back to old numbers marked as current.
            """
            if not read_ok:
                return None
            row = rollup_by.get((day, channel))
            return row if row is not None and (not raw_rows or not whole) else None

        day_cog = cog_by_day.get(day, [])
        day_face = face_by_day.get(day, [])
        cog_roll = _rolled("cognitive", day_cog, cog_whole, cog_ok)
        face_roll = (_rolled("emotion", day_face, face_whole, face_ok)
                     if include_emotion else None)
        # Trusted only, matching the week's averages, so a day where every
        # sample was rejected reads as "measured, unusable" rather than
        # sensor-off.
        day_heart = [r for r in heart_by_day.get(day, []) if r.get("trusted") is True]
        heart_roll = (_rolled("heart", heart_by_day.get(day, []), heart_whole, heart_ok)
                      if include_heart else None)

        daily.append({
            "date": day,
            # Withheld unless the day is whole, since a partial average from
            # the cap's fraction would look like a measurement of the whole
            # day. `.get` throughout so a missing rollup column can't 500 the
            # report.
            "focus": (cog_roll.get("avg_focus") if cog_roll else
                      _avg([r.get("focus") for r in day_cog]) if cog_whole else None),
            "stress": (cog_roll.get("avg_stress") if cog_roll else
                       _avg([r.get("stress") for r in day_cog]) if cog_whole else None),
            "engagement": (cog_roll.get("avg_engagement") if cog_roll else
                           _avg([r.get("engagement") for r in day_cog]) if cog_whole else None),
            "attention": _avg([r.get("attention") for r in day_face]) if face_whole else None,
            # None, not 0: a day the cap couldn't reach didn't have zero
            # sessions. `sessions_retrieved` tells the two apart.
            "sessions": len(sessions_by_day.get(day, []))
                        if ses_whole else None,
            # Absolute units, unlike every other series here (0..1 ratios).
            # Applying the same percentage scaling would draw 72 bpm as 7200%.
            "heart_rate_bpm": (heart_roll.get("avg_heart_rate_bpm") if heart_roll else
                               _avg([r.get("heart_rate_bpm") for r in day_heart])
                               if heart_whole else None),
            "rmssd_ms": (heart_roll.get("avg_rmssd_ms") if heart_roll else
                         _avg([r.get("rmssd_ms") for r in day_heart])
                         if heart_whole else None),
            # False = "not fully fetched" (cap never reached it, cap cut into
            # it, or the query failed). None = "not requested" (face reporting
            # off) -- consumers checking `=== false` must not treat the
            # opt-out as a failure. A rollup-sourced day is always fully
            # retrieved, even where the raw read was capped or gone.
            "cognitive_retrieved": True if cog_roll else cog_whole,
            "face_retrieved": (None if not include_emotion else
                               True if face_roll else face_whole),
            "heart_retrieved": (None if not include_heart else
                                True if heart_roll else heart_whole),
            "sessions_retrieved": ses_whole,

            # Reduced fidelity, named: a rollup average and a raw average
            # answer the same question at different precision, so a chart
            # mixing them without saying so invites a bad comparison.
            "cognitive_from_rollup": bool(cog_roll),
            "face_from_rollup": bool(face_roll),
            "heart_from_rollup": bool(heart_roll),

            # How much is behind each figure, uniformly: raw days count rows,
            # rollup days carry the count from when the rows still existed.
            # This keeps a thin day visibly thin after the detail is gone --
            # without it four samples and four thousand look identical.
            "cognitive_samples": (cog_roll.get("sample_count") or 0) if cog_roll else len(day_cog),
            # Emotion rows, not face rows: `face_signals` has two producers, so
            # a gaze-only row is a real face row with no emotion in it. The
            # rollup's emotion `sample_count` is narrowed to match, or this
            # number would mean something different depending on whether the
            # day has been rolled up yet.
            "face_samples": ((face_roll.get("sample_count") or 0) if face_roll
                             else sum(1 for r in day_face
                                      if r.get("emotion") is not None)),
            "heart_samples": (heart_roll.get("sample_count") or 0) if heart_roll else len(day_heart),
        })

        # Weighted by `trusted_sample_count` -- rows that produced a usable
        # measurement, not rows that merely existed. `rmssd_ms` is an
        # approximation here: it's null on roughly one accepted window in
        # five, so its true weight is a bit lower than the count used.
        if cog_roll:
            n = cog_roll.get("trusted_sample_count") or 0
            for key, col in (("focus", "avg_focus"), ("stress", "avg_stress"),
                             ("engagement", "avg_engagement")):
                value = cog_roll.get(col)
                if value is not None and n:
                    rolled_totals[key][0] += float(value) * n
                    rolled_totals[key][1] += n
        if heart_roll:
            n = heart_roll.get("trusted_sample_count") or 0
            for key, col in (("heart_rate_bpm", "avg_heart_rate_bpm"),
                             ("rmssd_ms", "avg_rmssd_ms")):
                value = heart_roll.get(col)
                if value is not None and n:
                    rolled_totals[key][0] += float(value) * n
                    rolled_totals[key][1] += n
            rolled_sources.update(heart_roll.get("heart_sources") or ())
        if face_roll:
            for label, count in (face_roll.get("emotion_counts") or {}).items():
                rolled_emotions[label] = rolled_emotions.get(label, 0) + int(count)

    # Only trusted heart samples are averaged -- an untrusted one has a rate,
    # just not one worth showing a parent. The SQL aggregate applies the same rule.
    def _week(key, raw_values):
        """The week's mean over raw samples and summarised days combined.

        Both contribute their true sum and count, so a week that is half raw
        rows and half rollup (as happens right after the delete job first
        runs) still gets one honest mean.
        """
        total, n = rolled_totals[key]
        nums = [float(v) for v in raw_values if v is not None]
        total += sum(nums)
        n += len(nums)
        return (total / n) if n else None

    heart_rates = [r["heart_rate_bpm"] for r in heart
                   if r.get("heart_rate_bpm") is not None and r.get("trusted") is True]
    rmssd_values = [r["rmssd_ms"] for r in heart
                    if r.get("rmssd_ms") is not None and r.get("trusted") is True]
    # Which sensor produced these readings, so a reader can tell a headband
    # week from a camera week -- accuracy differs materially, and only the
    # headband is validated at all. Trusted rows only, matching the averages.
    # Summarised days are included too: once raw rows are gone, the rollup's
    # `heart_sources` is the only record that the sensor changed mid-week.
    heart_sources = sorted({r["source"] for r in heart
                            if r.get("source") and r.get("trusted") is True}
                           | rolled_sources)

    # Seeded from the rollup, then raw rows counted on top. The rollup stores
    # the full distribution (not just the winner) so this stays answerable
    # after the detail rows are deleted.
    emotion_counts: dict[str, int] = dict(rolled_emotions)
    for r in face:
        if r.get("emotion"):
            emotion_counts[r["emotion"]] = emotion_counts.get(r["emotion"], 0) + 1

    def _round2(value):
        return None if value is None else round(value, 2)

    avg_focus = _round2(_week("focus", [r.get("focus") for r in cog]))
    avg_stress = _round2(_week("stress", [r.get("stress") for r in cog]))
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
    # No attention sentence: `face_signals.attention` has no producer, so this
    # would put an unmeasured percentage in front of a parent. The average is
    # still computed and returned in the payload; only the sentence is gone.
    if bits:
        summary = "This week, " + ", ".join(bits) + "."
    else:
        # A failed query returns the same empty result as a quiet week, so
        # "nothing was recorded" can only be claimed for a table that actually
        # read successfully -- split into measured vs. unread here.
        measured, unread = [], []
        (measured if cog_ok else unread).append("EEG")
        # Skip facial recognition entirely when the caller opted out -- it was
        # never measured, so nothing should be claimed about it.
        if include_emotion:
            (measured if face_ok else unread).append("facial recognition")
        if include_heart:
            (measured if heart_ok else unread).append("heart rate")

        def _join(items: list[str], conjunction: str) -> str:
            # "a, b or c" rather than "a or b or c" once there are 3+ items.
            if len(items) <= 1:
                return "".join(items)
            return ", ".join(items[:-1]) + f" {conjunction} " + items[-1]

        parts = []
        if measured:
            parts.append(f"No {_join(measured, 'or')} samples were recorded this week.")
        if unread:
            parts.append(_as_sentence(
                f"{_join(unread, 'and')} data could not be loaded."))
        summary = " ".join(parts)

    return {
        "student_id": student_id,
        "days": days,
        "since": since,
        "truncated": truncated,
        # Distinguishes "facial reporting is switched off for this view" from
        # "the camera recorded nothing" -- both leave every face field null.
        #
        # `face_included` is kept as an alias for `emotion_included`, narrower
        # than its name (means the emotion channel only). Deprecated -- read
        # emotion_included in new code.
        "face_included": include_emotion,
        "emotion_included": include_emotion,
        "heart_included": include_heart,
        # False means the two flags above are "we couldn't find out", not "the
        # student declined" -- a surface must not report a database outage as
        # a preference.
        "consent_retrieved": consent_retrieved,
        "emotion_revoked_at": emotion_revoked_at,
        "heart_revoked_at": heart_revoked_at,
        # The full tally, not just the winner, so the frontend can render a pie
        # chart instead of only a single dominant_emotion label.
        "emotion_distribution": (dict(sorted(emotion_counts.items(),
                                             key=lambda kv: (-kv[1], kv[0])))
                                 if include_emotion else None),
        "heart_sources": heart_sources if include_heart else None,
        # Which of the three reads actually happened. A failed query returns
        # the same empty rows as a student who recorded nothing, so a null
        # average or zero count is ambiguous without this. Per table, since
        # the reads fail independently.
        #
        # face is None with the opt-out on, matching per-day `face_retrieved`:
        # there was no retrieval to succeed or fail.
        "retrieved": {
            "cognitive": cog_ok,
            "face": face_ok if include_emotion else None,
            "heart": heart_ok if include_heart else None,
            "sessions": ses_ok,
            "rollup": rollup_ok,
        },
        "sample_counts": {"cognitive": len(cog), "face": len(face),
                          # Rows retrieved, not rows averaged -- a week of only
                          # untrusted samples is a nonzero count beside a null
                          # average ("measured, unusable"), not sensor-off.
                          "heart": len(heart), "sessions": len(sessions)},
        # The real session total, as opposed to the row count under
        # _SESSION_ROW_CAP -- a heavy week otherwise showed exactly the cap as
        # its headline. Falls back to the row count if the server reported no
        # exact count.
        #
        # None when the read failed, not len() of the empty list it returned:
        # "0 sessions" is a claim, and there's nothing behind it on that path.
        "sessions_recorded": (ses_total if ses_total is not None else len(sessions)) if ses_ok else None,
        "averages": {
            "focus": avg_focus,
            "stress": avg_stress,
            "engagement": _round2(_week("engagement",
                                        [r.get("engagement") for r in cog])),
            "face_attention": avg_attention,
        },
        "highlights": {
            "highest_stress": round(highest_stress, 2) if highest_stress is not None else None,
            "lowest_focus": round(lowest_focus, 2) if lowest_focus is not None else None,
            "dominant_emotion": max(emotion_counts, key=emotion_counts.get) if emotion_counts else None,
            "heart_rate_bpm": _week("heart_rate_bpm", heart_rates),
            "rmssd_ms": _week("rmssd_ms", rmssd_values),
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

# Generation used to spawn a bare daemon thread per queued question, which was
# survivable only while the model was a local Ollama nobody paid per call: the
# process-wide peak was QUEUE_SIZE times however many children pressed start at
# once. The pool makes that a number someone chose, and it is sized to
# `GENERATION_MAX_CONCURRENCY` so threads and in-flight model calls line up --
# a larger pool would only buy workers that block on `llm_client`'s semaphore.
#
# Built on first use and reset on shutdown, same shape as `_strategy_pool`.
_PREFETCH_POOL: ThreadPoolExecutor | None = None
_prefetch_pool_lock = threading.Lock()


def _prefetch_pool() -> ThreadPoolExecutor:
    global _PREFETCH_POOL
    with _prefetch_pool_lock:
        if _PREFETCH_POOL is None:
            _PREFETCH_POOL = ThreadPoolExecutor(
                max_workers=llm_client.GENERATION_MAX_CONCURRENCY,
                thread_name_prefix="prefetch")
        return _PREFETCH_POOL


def _shutdown_prefetch_pool():
    """Drop queued prefetch on the way out. Called from _lifespan.

    Nothing is waiting on a prefetched question once the process is stopping,
    and a worker may be blocked in a socket read against a stalled model -- so
    cancel rather than join, exactly as `_shutdown_strategy_pool` does.
    """
    global _PREFETCH_POOL
    with _prefetch_pool_lock:
        pool, _PREFETCH_POOL = _PREFETCH_POOL, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


# Per-student ceiling on generations over time.
#
# `_prefetch_active` bounds *concurrency* per user, which is not the same
# quantity: a student answering quickly for an hour never exceeds two in
# flight and still generates hundreds of questions. That was free against a
# local model and is not against a metered one.
#
# Generous on purpose -- 60/min is far above what answering questions can
# actually consume (each answer triggers at most one refill), so it blunts a
# client looping on Generate without ever meeting a child working quickly.
_GENERATION_RATE_LIMIT  = _env_number("GENERATION_RATE_LIMIT", 60, int, minimum=1)
_GENERATION_RATE_WINDOW = _env_number("GENERATION_RATE_WINDOW", 60.0, float, minimum=1.0)
_generation_hits: dict[str, list[float]] = {}
_generation_hits_lock = threading.Lock()
_generation_sweep_at = time.monotonic()


def _claim_generation_slot(user_id: str) -> bool:
    """Count one generation against this student's window, or refuse.

    Returns a bool rather than raising: the prefetch worker has no request to
    fail, and skipping a refill there is invisible -- the queue simply stays
    short and the next question is generated inline. The endpoint turns a False
    into a 429 itself.
    """
    global _generation_sweep_at
    now = time.monotonic()
    with _generation_hits_lock:
        # Sweep only when the dict is large and at most once an interval, so
        # the common path stays one lookup. Same shape as the strategy limiter.
        if (len(_generation_hits) > _STRATEGY_SWEEP_ABOVE
                and now - _generation_sweep_at >= _STRATEGY_SWEEP_EVERY):
            _generation_sweep_at = now
            for uid in [u for u, ts in _generation_hits.items()
                        if all(now - t >= _GENERATION_RATE_WINDOW for t in ts)]:
                del _generation_hits[uid]

        hits = [t for t in _generation_hits.get(user_id, ())
                if now - t < _GENERATION_RATE_WINDOW]
        if len(hits) >= _GENERATION_RATE_LIMIT:
            _generation_hits[user_id] = hits
            return False
        hits.append(now)
        _generation_hits[user_id] = hits
        return True


def _prefetch_worker(user_id: str, grade: str, bias: int, session_id: str | None):
    try:
        if not _claim_generation_slot(user_id):
            print(f"[prefetch] rate limit reached for {user_id[:8]}; not refilling")
            return
        # Topic, difficulty, EEG state, and the manual bias are all resolved in
        # this one call -- one decision + one generation call per question.
        question = LLM_topic_decider.LLM_single_prompt_topic_and_difficulty_decider(
            user_id, grade, session_id, bias
        )
        if question:
            with _prefetch_lock:
                _prefetch_cache.setdefault(user_id, []).append(question)
    except Exception as e:
        print(f"[prefetch] failed for {user_id[:8]}: {e}")
    finally:
        # Decrement by exactly 1 regardless of outcome. Using a count here
        # (not a set-membership flag) matters: a flag would let the first of
        # several concurrent workers clear "in flight" for all of them, so
        # `_ensure_queue` kept spawning more on top of ones still running,
        # piling up far more concurrent Ollama calls than QUEUE_SIZE intends.
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
        try:
            _prefetch_pool().submit(_prefetch_worker, user_id, grade, bias, session_id)
        except Exception as e:                                 # noqa: BLE001
            # The lock is released by here, so `_lifespan` can shut the pool
            # between the fetch and the submit and `submit` raises RuntimeError.
            #
            # Rolling the counter back is the load-bearing half, and it is not
            # only about shutdown. `_prefetch_worker` owns the decrement in its
            # `finally`, so a worker that never starts never runs one: the
            # student's in-flight count stays permanently inflated, `needed`
            # is <= 0 from then on, and their queue never refills again.
            #
            # Swallowed rather than raised because prefetch is best-effort and
            # this runs *after* the caller already has its question -- letting
            # it out turns a served response into a 500 over a refill.
            with _prefetch_lock:
                _prefetch_active[user_id] = max(0, _prefetch_active.get(user_id, 0) - 1)
            print(f"[prefetch] could not queue for {user_id[:8]}: {e}")

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
    # Learning preferences. Bounded here as well as by the database's CHECK
    # constraints, since a 422 names the field but a constraint violation
    # just surfaces as a 500.
    #
    # `ge`/`le`, not a literal set, since bias is a shift applied by
    # `_shift_difficulty` -- the range is what matters, not how many values
    # DIFFS happens to have.
    difficulty_bias:          int | None = Field(None, ge=-1, le=1)
    session_duration_minutes: int | None = Field(None, ge=5, le=180)
    practice_reminders:       bool | None = None

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
    # Newest first, so "Recent Questions" on the teacher dashboard is actually
    # chronological rather than whatever order Postgres happens to return.
    q = supabase.table("questions").select("*").order("created_at", desc=True).limit(limit)
    if subject:    q = q.eq("subject", subject)
    if difficulty: q = q.eq("difficulty", difficulty)
    res = q.execute()
    return res.data or []


@app.get("/api/questions/count")
def count_questions(subject: str | None = None, difficulty: str | None = None):
    """How many questions exist, without transferring them.

    `count="exact"` makes PostgREST return the count in the Content-Range
    header with no rows -- avoids fetching a megabyte of question text just to
    show one number, and avoids the old bug where the count silently stopped
    growing once the bank passed the page limit.
    """
    try:
        q = supabase.table("questions").select("id", count="exact").limit(1)
        if subject:    q = q.eq("subject", subject)
        if difficulty: q = q.eq("difficulty", difficulty)
        res = q.execute()
        return {"total": res.count if res.count is not None else 0, "retrieved": True}
    except Exception as e:                                     # noqa: BLE001
        # Same three-state rule as the reporting helpers: a failed count must
        # not render as a question bank with nothing in it.
        print(f"[questions] could not count: {e}")
        return {"total": None, "retrieved": False}


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
        # Own catch, not `_row_or_404`: an unknown class just falls back to
        # `effective_grade` rather than failing the request, since this only
        # refines a default.
        try:
            cls = supabase.table("classes").select("grade_level") \
                .eq("id", class_id).single().execute()
        except Exception as e:                                 # noqa: BLE001
            print(f"[question] could not read class {class_id}: {e}")
            cls = None
        if cls and cls.data and cls.data.get("grade_level"):
            effective_grade = cls.data["grade_level"]

    manual_bias = max(-1, min(1, int(bias or 0)))

    # Serve from the prefetch queue if available, else generate now -- both
    # paths resolve topic, difficulty, EEG state and bias in one call.
    with _prefetch_lock:
        queue    = _prefetch_cache.get(user_id, [])
        question = queue.pop(0) if queue else None

    if not question:
        print(f"[generate] cache miss for {user_id[:8]} -- generating inline")
        if not _claim_generation_slot(user_id):
            raise HTTPException(
                429, "Too many questions requested. Try again shortly.",
                headers={"Retry-After": str(max(1, int(_GENERATION_RATE_WINDOW)))},
            )
        try:
            question = LLM_topic_decider.LLM_single_prompt_topic_and_difficulty_decider(
                user_id, effective_grade, session_id, manual_bias
            )
        except llm_client.GenerationUnavailable as e:
            # 503, not 500: a ceiling was reached, which is a decision this
            # deployment made rather than something that broke. Refusing is
            # deliberate -- quietly serving a question from somewhere else
            # would change what the student is asked with nothing saying so.
            print(f"[generate] refused for {user_id[:8]}: {e}")
            raise HTTPException(503, "Question generation is temporarily unavailable.")
        if not question:
            raise HTTPException(500, "Failed to generate question")
    else:
        print(f"[generate] cache hit for {user_id[:8]} -- instant serve")

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

    # Close any session the student left open (e.g. closed the tab without
    # ending it), so it doesn't sit open forever.
    #
    # Select `started_at`, `questions_answered`, `correct_answers` explicitly:
    # a missing column reads back as None, and `_discard_if_nothing_recorded`
    # and the lifetime-credit step below would treat that as "nothing done"
    # or "zero correct" for a session that actually has real work in it.
    stale_open = supabase.table("sessions") \
        .select("id, started_at, questions_answered, correct_answers") \
        .eq("user_id", user["id"]).is_("ended_at", "null").execute().data or []
    for s in stale_open:
        # Also releases any pre-claim EEG reservation left behind by a
        # scan/connect that never reached /start.
        eeg_poller.stop(s["id"], user["id"])
        stale_ended = _utc_now().isoformat()
        # This is often the only close a session gets, so it must still run
        # the rollup, lifetime credit, and chart archive like a normal close.
        # Marked as the sweep: the student did not end this one, and that is
        # exactly what `session_auto_closed` reports.
        _close_session(user["id"], s, stale_ended, closed_by=CLOSED_BY_SWEEP)


    obj  = {
        "user_id":            user["id"],
        "title":              payload.title or "Practice Session",
        "started_at":         _utc_now().isoformat(),
        "questions_answered": 0,
        "correct_answers":    0,
    }
    res = supabase.table("sessions").insert(obj).execute()

    # Pre-warm the question queue while the student sees the setup screen, at
    # their own difficulty bias -- not 0, or the first questions served would
    # ignore the Easier/Auto/Harder setting the page already shows them.
    profile = _profile(user["id"])
    grade   = profile.get("grade_level") or "5th Grade"
    bias    = max(-1, min(1, int(profile.get("difficulty_bias") or 0)))
    _ensure_queue(user["id"], grade, bias, res.data[0]["id"])

    return res.data[0]

@app.post("/api/sessions/{session_id}/answer")
def record_answer(session_id: str = Path(...), payload: AnswerPayload = Body(...), request: Request = None):
    user = get_user(request)
    # Check ownership before writing anything, or any signed-in student could
    # post answers into a session id they merely knew, moving another child's
    # counters and, at close, crediting the questions to the wrong student.
    _session_or_403(session_id, user["id"])
    supabase.table("session_answers").insert({
        "session_id":     session_id,
        "user_id":        user["id"],
        "question_id":    payload.question_id,
        "selected_index": payload.selected_index,
        "correct":        payload.correct,
        "answered_at":    datetime.utcnow().isoformat(),
    }).execute()
    # Incremented in the database, not read-then-written here, or two answers
    # landing together could both read the same count and one increment would
    # be lost.
    #
    # Never raises: the answer row is already written and is the real record.
    # `questions_answered` is just a live cache; `_answer_counts` recomputes it
    # at close, so losing this bump only delays a live number, not the answer.
    try:
        supabase.rpc("bump_session_counters", {
            "p_session_id": session_id,
            "p_correct":    bool(payload.correct),
        }).execute()
    except Exception as e:                                     # noqa: BLE001
        if "PGRST202" in str(e):
            print(f"[answer] bump_session_counters is missing from the database "
                  f"-- apply 20260826000000; live counters will not move: {e}")
        else:
            print(f"[answer] could not bump counters for {session_id}: {e}")
    # Hand the attributed topic straight back so the page can update its Topic
    # Accuracy panel without re-reading the whole performance table. `None`
    # means nothing was attributed, not an error.
    topic = _record_topic_attempt(user["id"], payload.question_id, payload.correct)
    return {"ok": True, "topic": topic}


def _record_topic_attempt(user_id: str, question_id: str, correct: bool) -> str | None:
    """Add one attempt to the student's per-topic record.

    The topic comes from the **question row**, never from the caller -- a
    client could otherwise credit the wrong subject for its answer, and this
    table is what the adaptive engine reads to choose the next question.

    Never raises: it runs after the answer row is already saved, and a topic
    lookup failing must not make that answer look lost.

    One statement in the database (`record_topic_attempt`), so two answers
    landing at once can't read-modify-write the same counts and drop one.

    Returns the topic **name**, or `None` for an unknown question, a topic
    with no `math_topics` row, or any failure -- so the page can update its
    Topic Accuracy panel without re-reading the whole table.
    """
    try:
        res = supabase.rpc("record_topic_attempt", {
            "p_user_id":     user_id,
            "p_question_id": question_id,
            "p_correct":     bool(correct),
        }).execute()
        topic = getattr(res, "data", None)
        return topic if isinstance(topic, str) else None
    except Exception as e:                                     # noqa: BLE001
        # PGRST202 means the migration hasn't been applied -- every answer
        # will fail this way until it is, unlike other errors which are
        # one-off and retry on the next answer.
        if "PGRST202" in str(e):
            print(f"[answer] record_topic_attempt is missing from the database -- "
                  f"apply 20260825000000; no topic attribution until then: {e}")
        else:
            print(f"[answer] could not record topic attempt for {user_id[:8]}: {e}")
        return None

@app.post("/api/sessions/{session_id}/end")
def end_session(session_id: str = Path(...), request: Request = None):
    user = get_user(request)
    # Check ownership before stopping the poller, or a student who merely knew
    # another student's session id could end their lesson mid-way.
    data = _session_or_403(session_id, user["id"], "*")
    # Also releases user["id"]'s pre-claim reservation, if any.
    eeg_poller.stop(session_id, user["id"])  # auto-stop EEG poller
    # May already be closed (by the stale sweep or a teacher's Live view). This
    # is just a cheap early-out, not the real guard -- the read and the write
    # below are two separate statements, so a close can still land in between.
    # `_close_session`'s conditional stamp is the actual guard; without one
    # here too, a session closed twice would credit its cumulative counts
    # twice, inflating the accuracy a parent reads. Not an error: the student
    # did nothing wrong.
    if data.get("ended_at"):
        return {"ok": True, "already_closed": True}
    # Use `_utc_now`, not naive `datetime.utcnow`: the rollup converts this
    # stamp to a school day and needs a timezone to convert from. Reuse the
    # same value below rather than reading the clock again, or a session that
    # crosses local midnight could roll up the wrong day.
    ended = _utc_now().isoformat()
    result = _close_session(user["id"], data, ended)
    if result.get("already_closed"):
        return {"ok": True, "already_closed": True}
    return {"ok": True, **({"discarded": True} if result["discarded"] else {})}

@app.get("/api/sessions")
def list_sessions(request: Request):
    user = get_user(request)
    res  = supabase.table("sessions").select("*").eq("user_id", user["id"]).order("started_at", desc=True).execute()
    return res.data or []


# ─── stats ───────────────────────────────────────────────────────────────

def _topic_performance_rows(student_ids) -> tuple[list, bool]:
    """The raw per-topic rows for several students, and whether the read worked.

    Split out from `_topic_performance_many` because the two callers need
    different things from a failure. A Topic Accuracy panel can degrade to
    empty -- "no attempts" is what an empty panel already means there. A
    heatmap cannot: an all-blank grid and a grid nobody could fetch look
    identical, which is the three-state rule every reporting helper here
    carries. So the flag is returned rather than swallowed, and the helper
    below drops it for the callers that genuinely do not need it.
    """
    ids = _unique_ids(student_ids)
    if not ids:
        return [], True
    try:
        rows = supabase.table("user_math_performance") \
            .select("*, math_topics(topic_name)").in_("user_id", ids).execute().data or []
    except Exception as e:                                     # noqa: BLE001
        print(f"[perf] could not batch-read topic performance for {len(ids)}: {e}")
        return [], False
    return rows, True


def _topic_performance_many(student_ids) -> dict[str, list]:
    """Per-topic performance for several students, in one query rather than N.

    Fails to an empty map -- the caller renders a Topic Accuracy panel from
    it, and an empty panel already means "no attempts", so this degrades
    safely.
    """
    rows, _ = _topic_performance_rows(student_ids)
    return _group_by_user(rows)


def _open_sessions_many(student_ids) -> dict[str, list]:
    """Every open session per student, newest first, in one query rather than N.

    A student normally has at most one open session -- `start_session` sweeps
    stray ones on return. Errors are deliberately not caught here: the live
    monitor is the caller, and turning a failed read into an empty map would
    make every student look idle to a teacher deciding who needs help.
    """
    ids = _unique_ids(student_ids)
    if not ids:
        return {}
    rows = supabase.table("sessions").select("*") \
        .in_("user_id", ids).is_("ended_at", "null") \
        .order("started_at", desc=True).execute().data or []
    return _group_by_user(rows)


def _stats_including_open_session(student_id: str) -> dict:
    """Lifetime totals, plus whatever the student has answered *so far today*.

    `user_stats` only updates when a session closes, so a lesson in progress
    would otherwise read as "0 questions" until it ends. This adds the open
    session's live counts (`ended_at is null`, so it never double-counts one
    already closed).

    `retrieved` tells "answered nothing" apart from "the read failed" -- both
    would otherwise show as four zeros, which reads as a real (if bad) academic
    record to a parent. It's only False if the *lifetime* read fails; a failed
    open-session read just skips the live delta and keeps the stored totals.

    See the batch version below for the actual logic -- this just calls it
    with a list of one.
    """
    return _stats_including_open_session_many([student_id])[student_id]


def _stats_including_open_session_many(student_ids: list[str]) -> dict[str, dict]:
    """The figures described above, for a roster, in two queries rather than 2N.

    Same `retrieved` flag as the single-student version, resolved per student.
    A failed lifetime read marks every student unretrieved rather than
    reporting a roster of zeros, since the batch can't tell which rows it
    would have got.

    Always returns one entry per id passed in, even though blanks and repeats
    are dropped before querying -- the single-student wrapper indexes the
    result directly, so a missing key would be a KeyError.
    """
    if not student_ids:
        return {}
    base: dict[str, dict] = {
        sid: {"total_questions": 0, "total_correct": 0, "current_streak": 0,
              "best_streak": 0, "retrieved": True}
        for sid in student_ids
    }
    lookup = _unique_ids(student_ids)
    if not lookup:
        return base
    try:
        rows = supabase.table("user_stats").select("*") \
            .in_("user_id", lookup).execute().data or []
        for r in rows:
            if r.get("user_id") in base:
                base[r["user_id"]] = {**r, "retrieved": True}
    except Exception as e:                                     # noqa: BLE001
        print(f"[stats] could not batch-read user_stats for {len(student_ids)}: {e}")
        return {sid: {**v, "retrieved": False} for sid, v in base.items()}
    try:
        open_rows = supabase.table("sessions") \
            .select("user_id, questions_answered, correct_answers") \
            .in_("user_id", lookup).is_("ended_at", "null").execute().data or []
    except Exception as e:                                     # noqa: BLE001
        # Stored totals are still valid on their own; this only ever adds to them.
        print(f"[stats] could not batch-read open sessions: {e}")
        return base
    for r in open_rows:
        sid = r.get("user_id")
        if sid not in base:
            continue
        live_q = r.get("questions_answered") or 0
        if not live_q:
            continue
        base[sid] = {**base[sid],
                     "total_questions": (base[sid].get("total_questions") or 0) + live_q,
                     "total_correct": (base[sid].get("total_correct") or 0)
                     + (r.get("correct_answers") or 0)}
    return base


@app.get("/api/stats/me")
def my_stats(request: Request):
    user = get_user(request)
    return _stats_including_open_session(user["id"])

@app.get("/api/stats/student/{student_id}")
def student_stats(student_id: str, request: Request):
    _verify_can_view_student(get_user(request), student_id)
    return _stats_including_open_session(student_id)

@app.get("/api/sessions/student/{student_id}")
def student_sessions(student_id: str, request: Request):
    """A student's recent sessions, with abandoned ones marked as such.

    `abandoned` is derived here rather than in the browser so the threshold
    has one definition. The Sessions list decided "live" from `ended_at`
    alone, which is true of a session started two months ago and never closed
    -- it rendered a pulsing LIVE badge for a student who had not been seen
    since June.

    It is an **age, not an idleness**, and the flag is named for what it can
    actually support. A session open for two hours with a student answering
    throughout is not abandoned by this measure and is correctly not marked.
    `class_live` is the surface that computes real last-activity from signal
    rows and answers, on a much tighter window; this one only has to stop the
    list asserting that a long-dead session is in progress.
    """
    _verify_can_view_student(get_user(request), student_id)
    res = supabase.table("sessions").select("*").eq("user_id", student_id).order("started_at", desc=True).limit(20).execute()
    rows = res.data or []
    cutoff = _utc_now() - timedelta(seconds=_SESSION_ABANDONED_AFTER_SEC)
    for r in rows:
        started = _parse_ts(r.get("started_at"))
        # Only open sessions can be abandoned, and an unparseable start is not
        # evidence of anything -- False, so a bad timestamp does not relabel a
        # session nobody has looked at.
        r["abandoned"] = bool(
            not r.get("ended_at") and started is not None and started < cutoff)
    return rows

@app.get("/api/performance/student/{student_id}")
def student_performance(student_id: str, request: Request):
    _verify_can_view_student(get_user(request), student_id)
    res = supabase.table("user_math_performance") \
        .select("*, math_topics(topic_name)") \
        .eq("user_id", student_id).execute()
    return res.data or []


@app.get("/api/students/{student_id}/signal-trend")
def student_signal_trend(student_id: str, request: Request, weeks: int = 8,
                         include_face: bool = True):
    """Week-over-week signal averages for a student.

    Same access rule and same consent gating as the weekly report -- it is the
    same data one aggregation coarser, so nothing here may be readable by
    anyone who could not read that.
    """
    _verify_can_view_student(get_user(request), student_id)
    channels = _reportable_channels(student_id, include_face)
    return _signal_trend(student_id, max(2, min(weeks, _TREND_MAX_WEEKS)),
                         include_heart=channels.heart,
                         include_emotion=channels.emotion,
                         consent_retrieved=channels.consent_retrieved,
                         emotion_revoked_at=channels.emotion_revoked_at,
                         heart_revoked_at=channels.heart_revoked_at)


@app.get("/api/students/{student_id}/weekly-report")
def student_weekly_report(student_id: str, request: Request, days: int = 7, include_face: bool = True):
    """Aggregated EEG/facial signals for a student over the last `days`.

    Role-neutral: both teachers and parents read this for students they're
    entitled to see, so it isn't namespaced under /api/teacher/. Access is
    decided by relationship, not role.

    include_face=false narrows the report further, but never past what stored
    consent already allows -- see `_reportable_channels`.
    """
    _verify_can_view_student(get_user(request), student_id)
    p = _profile(student_id)
    channels = _reportable_channels(student_id, include_face)
    return {
        "student_name": p.get("display_name") or p.get("email") or "Student",
        **_weekly_signal_report(student_id, max(1, min(days, 30)),
                                include_heart=channels.heart,
                                include_emotion=channels.emotion,
                                consent_retrieved=channels.consent_retrieved,
                                emotion_revoked_at=channels.emotion_revoked_at,
                                heart_revoked_at=channels.heart_revoked_at),
    }


@app.get("/api/students/{student_id}/signal-summary")
def student_signal_summary(student_id: str, request: Request, days: int = 7, include_face: bool = True):
    """Headline signal averages for a student, aggregated in Postgres.

    Role-neutral like the weekly report: gated on relationship, not role.

    The aggregate runs in the database rather than reading raw rows here,
    because seven days at 1 Hz is up to half a million rows per student -- a
    row cap would silently average only the newest few minutes and call it
    "last 7d". The RPC is granted to service_role only, so this has to stay a
    backend endpoint rather than a direct browser rpc() call.
    """
    _verify_can_view_student(get_user(request), student_id)
    channels = _reportable_channels(student_id, include_face)
    return _signal_summary(student_id, max(1, min(days, 30)),
                           include_heart=channels.heart,
                           include_emotion=channels.emotion,
                           consent_retrieved=channels.consent_retrieved,
                           emotion_revoked_at=channels.emotion_revoked_at,
                           heart_revoked_at=channels.heart_revoked_at,
                           eeg_enabled=channels.eeg,
                           eeg_revoked_at=channels.eeg_revoked_at)


@app.get("/api/students/{student_id}/topic-breakdown")
def student_topic_breakdown(student_id: str, request: Request):
    _verify_can_view_student(get_user(request), student_id)
    return _topic_breakdown(student_id)


# ─── at-home learning strategies ─────────────────────────────────────────

# The model pass is opt-in. Off, the endpoint always answers from the
# deterministic rules below and never opens a socket -- the right default for
# CI and any deployment with no local Ollama.
#
# This lives in the `feature_flags` table (`strategy_llm_enabled`), not an env
# var, so an admin can switch off a misbehaving model without a redeploy.
# Read per request rather than at import for the same reason.
STRATEGY_LLM_MODEL   = os.getenv("STRATEGY_LLM_MODEL", "llama3.1:8b")
# Wall-clock budget for the whole model call. A hung Ollama server won't raise
# on its own, so without this the endpoint could block a worker thread forever
# instead of falling back to the rule-based answer.
#
# Floored at 1s: at zero or below, the call times out before the model could
# ever answer, silently disabling the model pass even though the flag says
# it's on.
STRATEGY_LLM_TIMEOUT = _env_number("STRATEGY_LLM_TIMEOUT", 20.0, float, minimum=1.0)

# The model call runs in its own pool so the timeout above can actually be
# enforced. httpx's timeout applies per network operation, not to the whole
# call, so a server that dribbles a byte at a time could stay alive well past
# STRATEGY_LLM_TIMEOUT if we only relied on that. Waiting on a future here
# bounds what the caller experiences regardless of what the transport does;
# max_workers bounds how many such calls can run at once, and an abandoned
# wait cancels its future (see _llm_strategies_bounded) so a stalled server
# can't build an unbounded backlog of work that still runs later.
#
# Built on first use, not at import, since the model pass is off by default
# and most deployments never need this pool. Tests substitute the global
# directly, so this only builds one if none exists yet.
_STRATEGY_LLM_POOL: ThreadPoolExecutor | None = None
_strategy_pool_lock = threading.Lock()


def _strategy_pool() -> ThreadPoolExecutor:
    """The model-call pool, created on first use.

    Locked so two requests racing to create it can't each build their own
    pool -- the loser's executor would sit outside max_workers and outside
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

    wait=False and cancel_futures=True rather than a clean join: a worker may
    be stuck in a socket read against a stalled Ollama, and nothing is waiting
    on that answer once the process is shutting down. Cancelling drops the
    queued work; the running worker can't be interrupted and is collected by
    the interpreter's own atexit join.

    Resets the global to None so a later reload in the same process builds a
    fresh, working pool rather than reusing a shut-down one.
    """
    global _STRATEGY_LLM_POOL
    with _strategy_pool_lock:
        pool, _STRATEGY_LLM_POOL = _STRATEGY_LLM_POOL, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


# How many requests may be *waiting* on the model at once, process-wide.
#
# This is different from max_workers (which bounds the generating threads):
# it bounds callers blocked in future.result(), because FastAPI runs sync
# endpoints on anyio's shared threadpool (40 slots by default). Without a cap,
# enough parents clicking Generate against a stalled Ollama could occupy the
# whole threadpool and take down every other sync endpoint in the app.
#
# Past the cap, the model pass is just skipped -- the rule-based list is
# always the fallback, so this only costs generic advice instead of tuned
# advice.
#
# Floored at 1, or the semaphore admits nobody and the model pass is silently
# off no matter what the feature flag says.
_STRATEGY_LLM_MAX_WAITERS = _env_number("STRATEGY_LLM_MAX_WAITERS", 4, int, minimum=1)
_strategy_llm_waiters = threading.BoundedSemaphore(_STRATEGY_LLM_MAX_WAITERS)


# Per-caller ceiling on the endpoint. It's the heaviest thing a parent can
# trigger by clicking a button, and the button is repeatable at whatever rate
# they click it.
#
# In-process: with multiple uvicorn workers the effective ceiling is this many
# per worker. That's fine -- the goal is just to blunt one caller looping on
# the button, not to enforce an exact global count.
#
# Both this and the window below are floored at their minimum, since a limit
# of 0 would 429 every request and a window of 0 would never expire hits, i.e.
# silently disable the limiter -- either way a typo bricks or removes the
# feature instead of just tuning it.
_STRATEGY_RATE_LIMIT  = _env_number("STRATEGY_RATE_LIMIT", 10, int, minimum=1)

# Ingestion is a trust boundary: the local sidecar posts these with the
# student's own bearer token, so a compromised or buggy process on a laptop
# must not be able to flood the table. Ownership and consent checks say
# *whether* something may be recorded, not *how much* -- volume needs its own
# limit.
#
# At the defaults a student may post 120 batches of 500 a minute, ~60,000
# samples/min -- about a thousandfold of headroom over a 1 Hz sensor, on
# purpose: this is sized to stop a runaway or hostile client, not to police a
# working one, and still has to allow a legitimate backlog flush after a
# dropped connection. Tighten the batch size before the rate if this ever
# needs revisiting.
_INGEST_MAX_BATCH   = _env_number("INGEST_MAX_BATCH", 500, int, minimum=1)
_INGEST_RATE_LIMIT  = _env_number("INGEST_RATE_LIMIT", 120, int, minimum=1)
_INGEST_RATE_WINDOW = _env_number("INGEST_RATE_WINDOW", 60.0, float, minimum=1.0)

_ingest_hits: dict[str, list[float]] = {}
_ingest_hits_lock = threading.Lock()
# Same sweep as the strategy limiter, but more needed: without it, a student
# who posts once and stops leaves an entry behind for the process lifetime,
# and ingest is the higher-volume endpoint so it accumulates fastest.
_ingest_sweep_at = time.monotonic()
_INGEST_SWEEP_EVERY = 60.0
_INGEST_SWEEP_ABOVE = 1024

# Which heart sources each sensor permits, one entry per sensor -- a student
# who allowed the headband but declined the camera has consented to
# muse_optics/muse_ppg and not to rppg.
#
# Keyed on `_may_record`'s composed `record_*` flags rather than the raw
# consent flags, since consent alone isn't permission -- the school year also
# has to be open -- and keying on raw flags would make every caller re-check
# the window itself.
_HEART_SOURCES_BY_RECORD_FLAG = {
    "record_headband_optical": ("muse_optics", "muse_ppg"),
    "record_camera":           ("rppg",),
}

_STRATEGY_RATE_WINDOW = _env_number("STRATEGY_RATE_WINDOW", 60.0, float, minimum=1.0)
_strategy_hits: dict[str, list[float]] = {}
_strategy_hits_lock = threading.Lock()
# When the sweep below last ran. Pairs a size threshold with a time interval
# so the sweep is proportional to time, not traffic -- size alone means every
# request scans and holds the lock once the dict is big, even if nothing is
# stale yet.
#
# Seeded from time.monotonic() itself, not 0.0: monotonic()'s reference point
# is undefined (boot time on Linux), so 0.0 would mean "last swept at boot",
# suppressing the sweep on any host up for less than the interval -- exactly
# the window a fresh container spends starting up.
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
        # Only sweep once the dict is large and only once per interval, so the
        # common path stays a single lookup instead of a scan on every request.
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
# A floor, not just a ceiling: without one, a truncated or degenerate reply
# ("1. a\n2. b\n3. c") passed every check and reached a parent as model advice.
_STRATEGY_MIN_CHARS = 25

# Clinical vocabulary that must not reach a parent from this endpoint -- a
# local model asked for study tips will occasionally volunteer a diagnosis.
# A match discards the whole reply rather than editing it: a sentence that
# needs a word removed isn't safe to hand a parent either.
#
# Terms are stemmed narrowly, not defensively, because an over-broad stem
# silently disables the model pass for every reply containing an ordinary
# word -- indistinguishable from outside from a genuinely unsafe model.
# "patient" in particular only matches the noun forms (plural, or singular
# behind a determiner), not the common adjective in "be patient when they get
# stuck" -- a bare predicative noun ("they are not patient") slips through,
# but that reading is rare here and a real clinical framing will likely trip
# another term in the same sentence anyway.
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
# Stripped rather than left alone, since nothing renders markdown between here
# and the parent -- the asterisks would show up as literal punctuation.
#
# Both patterns need a word-boundary guard: without it, a "*" or "_" that was
# never emphasis fuses the surrounding text into a garbled word. Underscores
# in a snake_case topic name ("angle_relationships") would otherwise vanish
# into "anglerelationships", and asterisks used as multiplication ("7*8 and
# 9*6") would fuse into "78 and 96". Genuine emphasis ("**Keep sessions
# short**") is unaffected, since its delimiters sit against whitespace or line
# ends.
_MD_ASTERISK = re.compile(r"(?<![\w*])(\*{1,3})(?=\S)(.+?)(?<=\S)\1(?![\w*])")
_MD_UNDERSCORE = re.compile(r"(?<!\w)(_{1,3})(?=\S)(.+?)(?<=\S)\1(?!\w)")


def _strip_emphasis(line: str) -> str:
    return _MD_UNDERSCORE.sub(r"\2", _MD_ASTERISK.sub(r"\2", line))


def _weakest_topic(topics: list[dict]):
    """Lowest-accuracy topic the student has actually attempted.

    Topics with no attempts are excluded, since _topic_breakdown reports them
    at 0% and would otherwise always "win" and point a parent at a topic
    their child has never even been given.
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

    Built from the aggregate RPC rather than _weekly_signal_report, which
    would transfer up to _REPORT_ROW_CAP rows per signal table just to derive
    the six numbers actually used here -- wasteful on the endpoint that is
    also the heaviest thing a click can trigger.

    Shaped like a report because _rule_based_strategies and _strategy_prompt
    both read report keys and are also tested directly against real reports.

    `averages` is built as an explicit list rather than copied wholesale, so a
    field this response was never about can't reach `basis` by accident.

    include_face is threaded into the aggregate, so opting out skips the
    facial row read entirely, not just the output field.
    """
    channels = _reportable_channels(student_id, include_face)
    summary = _signal_summary(student_id, days, include_heart=channels.heart,
                              include_emotion=channels.emotion,
                              consent_retrieved=channels.consent_retrieved,
                              eeg_enabled=channels.eeg,
                              eeg_revoked_at=channels.eeg_revoked_at)
    return {
        "days": days,
        "face_included": summary["face_included"],
        # A failed aggregate leaves every average None, which the rules below
        # already read as "nothing to act on", so this just lets a caller tell
        # a genuinely quiet week apart from a query that never ran.
        #
        # Named signals_retrieved, not `retrieved`: a real weekly report's
        # `retrieved` is a dict of three per-table booleans, and this dict is
        # deliberately shaped like a report for shared consumers -- reusing
        # the same key name here would invite `report.get("retrieved", {})`
        # style code that breaks on this shape.
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
            f"Spend 10-15 minutes on {label} before new material -- it is currently "
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
            "them -- stress indicators ran high this week."
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
            "goal per block -- focus indicators were low this week."
        )
    else:
        strategies.append(
            "Keep the study setup and time of day consistent, since the current "
            "routine is holding up."
        )

    # The attention rule that sat here is gone with the prompt line above.
    # `face_attention` has no producer, so `attention is not None` was never
    # true and the strategy could never be emitted -- dead code that read as a
    # live feature, over a measurement this product has decided not to claim
    # until there is a labelled reference for it.

    strategies.append(
        "Close each session by asking which problem felt hardest and what helped "
        "most -- it makes the next session easier to plan."
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
    # No attention line: `face_attention` has no producer yet, so this would
    # always say "unavailable" -- an unmeasured metric fed to a model whose
    # output a parent reads as real advice. Add it back once the column is
    # actually measured, alongside restoring the UI tiles for it.
    return (
        "You are helping a parent support their child's maths practice at home.\n"
        "Use only the weekly summary below. These are classroom learning "
        "indicators, not medical measurements -- do not diagnose, do not name any "
        "condition, and do not give medical advice.\n"
        f"Return exactly {_STRATEGY_COUNT} short, practical, at-home strategies as "
        "a numbered list. One sentence each, no preamble.\n\n"
        f"Weekly summary (last {report.get('days', 7)} days):\n"
        f"- average focus {_pct(averages.get('focus'))}\n"
        f"- average stress {_pct(averages.get('stress'))}\n"
        f"- average engagement {_pct(averages.get('engagement'))}\n"
        f"- weakest attempted topic: {topic_line}\n"
        f"- practice sessions recorded: {(report.get('sample_counts') or {}).get('sessions', 0)}\n\n"
        "For reference, here is a safe baseline answer:\n"
        + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(baseline))
    )


def _parse_strategy_lines(raw: str) -> list[str]:
    """The list items of a model reply, in order.

    Only lines with an actual list marker count -- taking every non-empty line
    would turn a lead-in like "Here are five strategies:" into strategy #1,
    and a strategy wrapped across two lines keeps only its first, marked line.

    Emphasis is unwrapped before the marker is stripped: a bolded whole item
    ("**1. Keep sessions short**") puts an asterisk in front of the number,
    which would otherwise get consumed as part of the bullet.
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
    # Check the whole raw reply, not just the parsed lines -- a clinical term
    # in a preamble should reject the reply even if the list items themselves
    # look clean.
    if _CLINICAL_TERMS.search(raw or ""):
        return None
    lines = _parse_strategy_lines(raw)
    if len(lines) < 3:
        return None
    # Bounded at both ends: the ceiling catches a model that ran on, the floor
    # catches empty list scaffolding like "1. a" that is well-formed but not
    # actually advice.
    if any(not _STRATEGY_MIN_CHARS <= len(line) <= _STRATEGY_MAX_CHARS for line in lines):
        return None
    return lines[:_STRATEGY_COUNT]


def _llm_strategies(prompt: str, timeout: float | None = None) -> list[str] | None:
    """One model attempt, or None on any failure.

    Goes through `llm_client`, which is also what question generation calls, so
    the provider switch is one setting rather than two -- and so this call is
    inside the same process-wide concurrency ceiling as the thirteen generation
    calls it now competes with for it.

    0.4, not the generation default: this writes advice a parent reads, where
    "keep it varied" is the wrong instinct. `llm_client` sends it as
    `temperature` on Ollama and `claude_temperature` on Claude, since the two
    providers do not accept the same range.

    `timeout` is what remains of the caller's budget after any time spent
    queued, not the full budget again -- charging it the full timeout once a
    worker frees up let a queued call hold that worker for nearly twice
    STRATEGY_LLM_TIMEOUT. Defaults to the full budget for a direct call.

    Catches everything, including `GenerationUnavailable`: the rule-based list
    is always available, so a ceiling here costs generic advice rather than an
    error, which is not the trade on the generation path.
    """
    try:
        raw = llm_client.generate_text(
            prompt,
            ollama_model=STRATEGY_LLM_MODEL,
            temperature=0.4, top_p=None, top_k=None,
            claude_temperature=0.4,
            max_tokens=1024,
            timeout=STRATEGY_LLM_TIMEOUT if timeout is None else timeout,
        )
    except Exception as e:
        print(f"[learning_strategies:llm] {e}")
        return None
    return _validated_strategies(raw or "")


def _llm_strategies_bounded(prompt: str) -> list[str] | None:
    """_llm_strategies under a deadline the caller actually feels, if admitted.

    Checks _STRATEGY_LLM_MAX_WAITERS first: this endpoint is sync, so a
    caller blocked on the model holds one of anyio's shared threadpool slots.
    Bounding the workers alone doesn't bound how many callers are waiting.
    """
    # Non-blocking: a caller who can't get in must not queue on the semaphore
    # either, which would just reintroduce the same wait one lock deeper.
    if not _strategy_llm_waiters.acquire(blocking=False):
        # Not an error: the rule-based list is always the guaranteed answer,
        # so being over the cap just costs generic advice instead of tuned
        # advice.
        print(f"[learning_strategies:llm] at capacity "
              f"({_STRATEGY_LLM_MAX_WAITERS} in flight); using the rule-based answer")
        return None
    try:
        return _llm_strategies_admitted(prompt)
    finally:
        _strategy_llm_waiters.release()


def _llm_strategies_admitted(prompt: str) -> list[str] | None:
    """The wait itself, once _llm_strategies_bounded has admitted the caller.

    Split out so the semaphore's release is a plain `finally` around one call.

    One deadline shared by the wait and the work, not two of the same length
    measured from different moments -- otherwise a submission that queued
    behind a busy worker could spend most of its budget waiting, then start a
    fresh timeout of its own, keeping the pool saturated for nearly twice
    STRATEGY_LLM_TIMEOUT.
    """
    deadline = time.monotonic() + STRATEGY_LLM_TIMEOUT

    def _run():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # Started after the caller gave up, so skip opening a socket at
            # all -- this catches the item already handed to a worker, and
            # the cancel below catches the ones still queued.
            return None
        return _llm_strategies(prompt, remaining)

    future = None
    try:
        # Inside the try: submit() itself raises if the pool is shutting down,
        # which the handler below turns into the rule-based fallback.
        future = _strategy_pool().submit(_run)
        return future.result(timeout=STRATEGY_LLM_TIMEOUT)
    except FutureTimeoutError:
        # Cancel rather than just drop the reference: bounding the worker
        # threads doesn't bound the queue behind them, so a sustained outage
        # would otherwise pile up abandoned prompts that all still run once
        # the server recovers. cancel() only succeeds for a queued task that
        # hasn't started yet, which is exactly what piles up.
        future.cancel()
        print(f"[learning_strategies:llm] abandoned after {STRATEGY_LLM_TIMEOUT}s")
        return None
    except Exception as e:
        # _llm_strategies already swallows its own failures, so reaching here
        # means the pool itself refused the work (e.g. shutting down).
        print(f"[learning_strategies:llm] {e}")
        return None


class LearningStrategyRequest(BaseModel):
    include_face: bool = True
    days: int = 7


@app.post("/api/students/{student_id}/learning-strategies")
def student_learning_strategies(student_id: str, request: Request, payload: LearningStrategyRequest):
    """At-home practice strategies derived from a student's weekly report.

    Role-neutral like the weekly report it reads: gated on relationship, not
    role.

    Always answers. The deterministic rules produce the response unless the
    optional model pass is enabled and its output passes
    _validated_strategies; `source` says which happened.

    The access check runs before the rate limit, so a caller with no
    relationship to the student gets 403 rather than a 429 masking that.
    """
    viewer = get_user(request)
    _verify_can_view_student(viewer, student_id)
    _rate_limit_strategies(viewer["id"])

    days = max(1, min(payload.days, 30))
    report = _strategy_basis(student_id, days, payload.include_face)
    topics = _topic_breakdown(student_id)

    strategies = _rule_based_strategies(report, topics)
    source = "rule-based"

    if _feature_flags()["strategy_llm_enabled"]["enabled"]:
        refined = _llm_strategies_bounded(_strategy_prompt(report, topics, strategies))
        if refined:
            strategies, source = refined, "model-refined"
        else:
            # Distinct from the plain rule-based case: "the model was asked
            # and its answer was rejected" is worth showing in the UI.
            source = "rule-based (model output rejected)"

    return {
        "student_id": student_id,
        "generated_at": _utc_now().isoformat(),
        "strategies": strategies,
        "source": source,
        "basis": {
            "days": days,
            "face_included": payload.include_face,
            # False means the averages are defaults, not a genuinely quiet
            # week -- so these strategies are the generic list, not one tuned
            # to the student.
            "signals_retrieved": report.get("signals_retrieved", True),
            "averages": report.get("averages") or {},
            # Named fields rather than the whole _topic_breakdown row, which
            # also carries topic_id, a stress reading and updated_at that
            # aren't part of what this response should promise.
            "weakest_topic": _weakest_topic_summary(topics),
        },
    }


# ─── leaderboard ─────────────────────────────────────────────────────────

_LEADERBOARD_MAX = 100


@app.get("/api/leaderboard")
def leaderboard(request: Request, limit: int = 20):
    """Top students by correct answers.

    Reads through the service-role client, bypassing RLS, so `limit` is the
    only thing bounding how much of the user base comes back with names
    attached -- it must stay clamped to _LEADERBOARD_MAX.

    user_id is resolved but never returned; the page only needs to know which
    row is the viewer's own, not a UUID -> name map for everyone on the board.
    """
    user = get_user(request)
    res = supabase.table("user_stats") \
        .select("user_id, total_correct, total_questions, current_streak, best_streak") \
        .order("total_correct", desc=True).limit(max(1, min(limit, _LEADERBOARD_MAX))).execute()
    rows = res.data or []
    # One read for the whole board, not one per row.
    profiles = _profiles_many(r.get("user_id") for r in rows)
    enriched = []
    for i, row in enumerate(rows):
        uid = row.pop("user_id", None)
        p = profiles.get(uid) or {}
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
    if _role(user["id"]) != "teacher":
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

# Must be registered **before** `/api/classes/{class_id}` -- FastAPI matches
# in registration order, so a parameterised route first would bind
# `class_id="summary"` and 404 instead of routing here.
@app.get("/api/classes/summary")
def class_summaries(request: Request):
    """Per-class headline averages for the teacher's dashboard, in three reads.

    Accuracy is averaged over students who have *attempted* something (a
    student with no attempts isn't a 0% student, they're excluded), while the
    streak is averaged over the whole roster. `None` accuracy means nobody
    has attempted anything.

    `retrieved` rides on each class so the page can tell "no attempts yet"
    apart from "the read failed", which would otherwise both look like a
    missing number.
    """
    user = get_user(request)
    classes = supabase.table("classes").select("id") \
        .eq("teacher_id", user["id"]).execute().data or []
    ids = _unique_ids(c["id"] for c in classes)
    if not ids:
        return {}

    try:
        members = supabase.table("class_memberships").select("class_id, student_id") \
            .in_("class_id", ids).execute().data or []
    except Exception as e:                                     # noqa: BLE001
        print(f"[classes] could not read memberships for {len(ids)} classes: {e}")
        return {cid: {"avgAccuracy": None, "avgStreak": 0, "retrieved": False}
                for cid in ids}

    by_class: dict[str, list] = {}
    for m in members:
        by_class.setdefault(m.get("class_id"), []).append(m.get("student_id"))

    stats = _stats_including_open_session_many(
        _unique_ids(m.get("student_id") for m in members))

    out = {}
    for cid in ids:
        roster = [stats.get(sid) or {} for sid in by_class.get(cid, [])]
        # One unretrieved student makes the whole class figure unretrieved --
        # it's an average, so a missing member doesn't make it partly right.
        retrieved = all(s.get("retrieved", False) for s in roster) if roster else True
        attempted = [s for s in roster if (s.get("total_questions") or 0) > 0]
        avg_accuracy = round(sum(
            (s.get("total_correct") or 0) / s["total_questions"] * 100
            for s in attempted) / len(attempted)) if attempted else None
        avg_streak = round(sum(s.get("current_streak") or 0
                               for s in roster) / len(roster)) if roster else 0
        out[cid] = {"avgAccuracy": avg_accuracy, "avgStreak": avg_streak,
                    "retrieved": retrieved}
    return out


@app.get("/api/classes/{class_id}")
def get_class(class_id: str, request: Request):
    """One class, for the pages that need its name and join code.

    Owner-only. Reads through the service-role client, so
    `_verify_class_owner` is the whole access check.
    """
    user = get_user(request)
    _verify_class_owner(class_id, user["id"])
    # Named columns, not "*", so a column added to `classes` later doesn't
    # start reaching the browser just by existing.
    return _row_or_404(
        supabase.table("classes").select("id, name, join_code, grade_level")
                .eq("id", class_id),
        "Class")

@app.put("/api/classes/{class_id}")
def update_class(class_id: str, payload: UpdateClassRequest, request: Request):
    user = get_user(request)
    cls = _row_or_404(
        supabase.table("classes").select("*").eq("id", class_id), "Class")
    if cls["teacher_id"] != user["id"]:
        raise HTTPException(403, "Not your class")
    fields = {k: v for k, v in payload.dict().items() if v is not None}
    if fields:
        supabase.table("classes").update(fields).eq("id", class_id).execute()
    # Re-read so the response reflects what was actually stored. A 404 here
    # means the row was deleted between the update and this read -- rare, but
    # "the class isn't there" is still the right thing to say.
    return _row_or_404(
        supabase.table("classes").select("*").eq("id", class_id), "Class")

@app.get("/api/classes")
def my_classes(request: Request):
    user = get_user(request)
    role = _role(user["id"])
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
        raise HTTPException(404, "Class not found -- check the code")
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

    These endpoints query through the service-role client, which bypasses
    RLS, so this check is the only thing enforcing that.
    """
    # `.single()` raises rather than returning empty when the row is missing;
    # `_row_or_404` is the shared guard for that.
    cls = _row_or_404(
        supabase.table("classes").select("teacher_id").eq("id", class_id), "Class")
    if cls["teacher_id"] != user_id:
        raise HTTPException(403, "Not your class")


def _can_view_student(viewer: dict, student_id: str) -> bool:
    """Whether `viewer` is allowed to see this student's data.

    Four legitimate relationships: the student themselves, a teacher of a
    class the student is enrolled in, a linked parent, or an admin. Reads go
    through the service-role client, so RLS is not a backstop -- this check
    is what enforces it.

    Admin is checked here rather than duplicated per admin endpoint, so
    access stays one decision in one place instead of parallel checks that
    can drift apart.
    """
    uid = viewer["id"]
    if uid == student_id:
        return True

    if _is_admin(uid):
        return True

    # Teacher of a class this student belongs to.
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
    roster = [m["student_id"] for m in (memberships.data or [])]
    # Three reads for the whole roster, not three per student.
    all_stats = _stats_including_open_session_many(roster)
    profiles = _profiles_many(roster)
    last_active = _last_active_many(roster)
    for m in (memberships.data or []):
        sid = m["student_id"]
        stats = all_stats.get(sid) or {}
        p = profiles.get(sid) or {}
        students.append({
            "user_id":   sid,
            "name":      p.get("display_name") or "Student",
            "email":     p.get("email") or "",
            "joined_at": m["joined_at"],
            # Three states, not two. A timestamp, `None` for a student who has
            # never started a session, and `last_active_retrieved: False` when
            # the read itself failed -- a roster that quietly shows everyone as
            # never-active is how a teacher decides nobody is working.
            **last_active.get(sid, _LAST_ACTIVE_UNKNOWN),
            **stats,
        })
    return students


# ─── teacher analytics ────────────────────────────────────────────────────
#
# Class-scoped aggregates behind the teacher analytics panels. Four rules
# apply to every one of them and are not repeated in each docstring:
#
#   * The access check is `_verify_class_owner`, before any read. These go
#     through the service-role client, so it is the only thing enforcing that
#     a teacher reads their own class.
#   * Every payload carries `retrieved`, on both the populated and the empty
#     branch. A failed read must never render as a quiet week. Where an
#     endpoint makes two reads, both fold into the one flag -- the roster and
#     the aggregate over it fail the same way from the reader's side, as an
#     empty chart, so a caller cannot act on the difference.
#   * Anything bucketed by time is bucketed at the school's timezone, through
#     `_school_timezone`, never against a UTC clock.
#   * Anything that aggregates over answers does it in Postgres. The raw
#     reporting reads are capped and the cap trims oldest-first, so a
#     Python-side average over a month would describe the recent tail while
#     the early days read as a quiet term.

# Below this many attempts a topic's accuracy is one or two answers wide and
# reads as 0% or 100%. The figure is still returned -- withholding real data is
# not this layer's decision -- but it rides out beside the threshold so the
# grid can render a thin cell differently from a confident one, in one named
# place rather than as a magic number in the markup.
_MIN_TOPIC_ATTEMPTS = 4

# How far back the class-level series look by default, and the ceiling on what
# a caller may ask for. Bounded like every other report window: the read is one
# query whatever the range, but a caller asking for two years would build a
# chart nobody can read.
_CLASS_TREND_DEFAULT_DAYS = 30
_CLASS_TREND_MAX_DAYS = 180

# Focus-vs-accuracy. `_FOCUS_MIN_PAIRS` is the point below which no
# correlation is shown at all: this renders to a teacher as a single
# objective-looking number, and r over a handful of answers is noise. It is
# deliberately far above the two pairs `corr()` itself needs.
_FOCUS_MIN_PAIRS = 30
_FOCUS_BUCKETS = 5
# How near in time a focus reading has to be to count as "during" an answer.
# The poller writes at roughly 1 Hz, so this is generous rather than tight --
# it is bridging a gap in the samples, not defining a window.
_FOCUS_MATCH_SECONDS = 30

_LAST_ACTIVE_UNKNOWN = {"last_active": None, "last_active_retrieved": False}

# How far back the alert feed looks by default, and how many rows it will
# return. A week is the span a teacher acts on; the cap is a backstop against
# a runaway emitter filling a page, and the payload says when it bites.
_ALERT_FEED_DAYS = 7
_ALERT_FEED_CAP = 200


def _last_active_many(student_ids) -> dict[str, dict]:
    """When each student was last doing anything, for a whole roster.

    "Newest row per student" has no PostgREST form -- one `in_` query ordered
    by time returns the newest rows overall, which is one busy student's -- so
    this goes through an RPC. That is also why the column was simply absent
    from the roster before rather than being wrong.

    A student with no sessions comes back `last_active: None` with
    `last_active_retrieved: True`: never active is a real fact about a roster
    and must stay distinguishable from a read that failed.
    """
    ids = _unique_ids(student_ids)
    if not ids:
        return {}
    try:
        rows = supabase.rpc("last_active_for_users",
                            {"p_user_ids": ids}).execute().data or []
    except Exception as e:                                     # noqa: BLE001
        # PGRST202 means the migration has not been applied, so *every* roster
        # reads as unknown until it is -- unlike an ordinary error, which is
        # one-off. Named here because the symptom is a column that degrades
        # quietly rather than a request that fails.
        if "PGRST202" in str(e):
            print(f"[last_active] last_active_for_users is missing from the "
                  f"database -- apply 20260831000000; the roster will show "
                  f"'unknown' until then: {e}")
        else:
            print(f"[last_active] could not read for {len(ids)}: {e}")
        return {sid: dict(_LAST_ACTIVE_UNKNOWN) for sid in ids}
    found = {r.get("user_id"): r.get("last_active") for r in rows}
    return {sid: {"last_active": found.get(sid), "last_active_retrieved": True}
            for sid in ids}


def _class_roster(class_id: str) -> tuple[list[str], bool]:
    """The student ids in a class, in join order, and whether the read worked.

    An earlier version let the error escape, on the reasoning that a 500 is
    louder than a wrong number. It is -- but it also contradicts this
    section's own rule that every payload carries `retrieved`, and the rule is
    right: a failed roster read produces an empty roster, every aggregate below
    is then computed over nobody, and the result is a well-formed payload
    describing a class with no students. That is a claim about the class, and a
    failed query has not earned it.

    So the flag is returned and each caller folds it into its own `retrieved`,
    exactly as `_topic_performance_rows` does. `student_count: 0` beside
    `retrieved: false` is then readable as "we could not find out", where
    `student_count: 0` beside `retrieved: true` is a genuinely empty class.
    """
    try:
        rows = supabase.table("class_memberships").select("student_id") \
            .eq("class_id", class_id).execute().data or []
    except Exception as e:                                     # noqa: BLE001
        print(f"[class_analytics] could not read the roster for {class_id}: {e}")
        return [], False
    return _unique_ids(r.get("student_id") for r in rows), True


def _class_topic_heatmap(class_id: str) -> dict:
    """Per-student, per-topic accuracy for a class.

    Read from `user_math_performance`, which already holds the counts -- this
    is a reshape of one query, not a new aggregate. `record_topic_attempt`
    keeps that table current on every answered question.

    Cells come back as a list aligned to `topics`, built from the same list in
    the same pass, so a row cannot drift out of step with its headings. Keying
    them by topic id would be equally safe and much more awkward to render as
    a grid; what is not safe is two independently ordered lists, which is the
    same failure `AccessibleChart`'s single `columns` spec exists to prevent.

    An untouched topic is `null`, never 0 -- a topic a student has never been
    served is not a topic they got wrong.
    """
    roster, roster_retrieved = _class_roster(class_id)
    profiles = _profiles_many(roster)
    rows, perf_retrieved = _topic_performance_rows(roster)
    # Either read failing makes the grid unreliable, and for the same reason:
    # a missing roster and missing performance rows both render as an empty
    # board. One flag out, because a caller cannot act on the difference.
    retrieved = roster_retrieved and perf_retrieved
    by_student = _group_by_user(rows)

    # Topic identity comes from the rows themselves rather than from a read of
    # `math_topics`: the grid should show the topics this class has actually
    # been served, not every topic that exists. A class three weeks into term
    # would otherwise open on a wall of empty columns.
    topic_names: dict[int, str] = {}
    for r in rows:
        tid = r.get("topic_id")
        if tid is None:
            continue
        joined = r.get("math_topics") or {}
        topic_names.setdefault(tid, joined.get("topic_name") or f"Topic {tid}")
    topic_ids = sorted(topic_names, key=lambda t: topic_names[t])

    def _cell(perf: dict | None) -> dict | None:
        if not perf:
            return None
        attempted = perf.get("attempted_questions") or 0
        if not attempted:
            return None
        correct = perf.get("correct_questions") or 0
        return {"attempted": attempted, "correct": correct,
                "accuracy": round(correct / attempted, 4)}

    students = []
    for sid in roster:
        perf_by_topic = {p.get("topic_id"): p for p in by_student.get(sid, [])}
        cells = [_cell(perf_by_topic.get(tid)) for tid in topic_ids]
        attempted = sum(c["attempted"] for c in cells if c)
        correct = sum(c["correct"] for c in cells if c)
        students.append({
            "user_id": sid,
            "name": (profiles.get(sid) or {}).get("display_name") or "Student",
            "cells": cells,
            "attempted": attempted,
            "accuracy": round(correct / attempted, 4) if attempted else None,
        })

    topics = []
    for i, tid in enumerate(topic_ids):
        attempted = sum(s["cells"][i]["attempted"] for s in students if s["cells"][i])
        correct = sum(s["cells"][i]["correct"] for s in students if s["cells"][i])
        topics.append({
            "topic_id": tid,
            "topic_name": topic_names[tid],
            "attempted": attempted,
            "correct": correct,
            "accuracy": round(correct / attempted, 4) if attempted else None,
        })

    return {"topics": topics, "students": students,
            "min_attempts": _MIN_TOPIC_ATTEMPTS, "retrieved": retrieved}


def _answer_buckets(roster: list[str], days: int,
                    tz: tzinfo) -> tuple[list, bool, date]:
    """Answers per school day and hour for a roster, from the RPC.

    Shared by the accuracy trend and the time-of-day heatmap -- they are two
    readings of one grouping. Called once per endpoint rather than cached
    between them, so a failure in either cannot blank the other; the two
    panels load independently on the page for the same reason.

    Returns the rows, whether the read worked, and the first school day of the
    range it asked for -- see below for why the caller must not recompute that.
    """
    # The clock is read once, here, and the first day is handed back with the
    # rows. The caller needs the same range to lay out its buckets, and reading
    # `_utc_now()` again after the round trip is a second clock: a request that
    # straddles local midnight would query from day N and bucket from day N+1,
    # so the oldest day's rows land in no bucket and are dropped. Narrow and
    # self-healing on the next request, which is exactly why it would never be
    # reported -- one day quietly missing from the left edge of a chart.
    school_today = _utc_now().astimezone(tz).date()
    start = school_today - timedelta(days=days - 1)
    if not roster:
        return [], True, start
    # Half-open on the far end, and the end is *tomorrow* at local midnight so
    # today's answers are in range. Converting a local date back to an instant
    # here rather than in SQL keeps the boundary in the same place as
    # `_school_day` puts it.
    #
    # `datetime.min.time()` rather than `time.min`: the module-level `time` here
    # is the stdlib module, not `datetime.time`, and importing the latter would
    # shadow it for the whole file.
    midnight = datetime.min.time()
    try:
        rows = supabase.rpc("class_answer_buckets", {
            "p_user_ids": roster,
            "p_from": datetime.combine(start, midnight, tzinfo=tz).isoformat(),
            "p_to": datetime.combine(school_today + timedelta(days=1),
                                     midnight, tzinfo=tz).isoformat(),
            "p_timezone": str(tz),
        }).execute().data or []
    except Exception as e:                                     # noqa: BLE001
        print(f"[class_analytics] answer buckets failed for {len(roster)}: {e}")
        return [], False, start
    return rows, True, start


def _class_accuracy_trend(class_id: str, days: int) -> dict:
    """Class accuracy per school day.

    Every day in the range appears, including the ones with no answers. A day
    dropped from the series renders as the days either side sitting adjacent,
    so a week of half-term reads as a smooth run rather than as a gap -- the
    same reason `_signal_trend` emits its empty weeks.

    `accuracy` is null on a day nobody answered, never 0. Zero is a real and
    bad reading; it is not what "nobody was in" looks like.
    """
    tz = _school_timezone()
    roster, roster_retrieved = _class_roster(class_id)
    rows, buckets_retrieved, start = _answer_buckets(roster, days, tz)
    retrieved = roster_retrieved and buckets_retrieved

    # `start` comes back from the read rather than being recomputed here, so
    # the buckets cannot describe a different range from the query that filled
    # them. See `_answer_buckets`.
    buckets = {start + timedelta(days=i): [0, 0] for i in range(days)}
    for r in rows:
        try:
            day = date.fromisoformat(str(r.get("day")))
        except (TypeError, ValueError):
            continue
        b = buckets.get(day)
        if b is None:
            continue
        b[0] += r.get("attempted") or 0
        b[1] += r.get("correct") or 0

    series = [{"day": day.isoformat(), "attempted": a, "correct": c,
               "accuracy": round(c / a, 4) if a else None}
              for day, (a, c) in sorted(buckets.items())]
    answered = sum(d["attempted"] for d in series)
    correct = sum(d["correct"] for d in series)
    return {
        "days": series,
        "attempted": answered,
        "correct": correct,
        "accuracy": round(correct / answered, 4) if answered else None,
        "days_with_data": sum(1 for d in series if d["attempted"]),
        "student_count": len(roster),
        "timezone": str(tz),
        "retrieved": retrieved,
    }


def _class_time_of_day(class_id: str, days: int) -> dict:
    """When in the week a class actually works, as weekday x hour.

    Weekday is derived from the school-local date the RPC already bucketed on,
    so it needs no second timezone conversion -- and must not get one, or the
    hour and the day it belongs to would be resolved against different clocks.

    Only the hours the class has ever worked in are emitted. A grid spanning
    midnight to midnight is 168 cells of which a school uses perhaps thirty,
    and the empty rows are not a finding.
    """
    tz = _school_timezone()
    roster, roster_retrieved = _class_roster(class_id)
    rows, buckets_retrieved, _start = _answer_buckets(roster, days, tz)
    retrieved = roster_retrieved and buckets_retrieved

    grid: dict[tuple[int, int], list[int]] = {}
    for r in rows:
        try:
            day = date.fromisoformat(str(r.get("day")))
            hour = int(r.get("hour"))
        except (TypeError, ValueError):
            continue
        if not 0 <= hour <= 23:
            continue
        cell = grid.setdefault((day.weekday(), hour), [0, 0])
        cell[0] += r.get("attempted") or 0
        cell[1] += r.get("correct") or 0

    cells = [{"weekday": wd, "hour": hour, "attempted": a, "correct": c,
              "accuracy": round(c / a, 4) if a else None}
             for (wd, hour), (a, c) in sorted(grid.items())]
    return {
        "cells": cells,
        "hours": sorted({c["hour"] for c in cells}),
        "attempted": sum(c["attempted"] for c in cells),
        "days": days,
        "student_count": len(roster),
        "timezone": str(tz),
        "retrieved": retrieved,
    }


def _focus_accuracy(student_id: str, days: int) -> dict:
    """Whether this student answers better when the headband reads focused.

    Gated on EEG consent by the caller, which skips the read entirely rather
    than discarding the result -- an absent figure cannot otherwise tell
    "asked and found nothing" from "never asked".

    `correlation` is withheld below `_FOCUS_MIN_PAIRS` even when the database
    computed one. It reaches a teacher as a single number with no visible
    denominator, and r over a dozen answers is noise wearing the costume of a
    finding. `pairs` rides alongside so the surface can say why it is absent,
    and the per-bucket accuracies are still returned: a bar chart of five bins
    shows its own sample sizes, which a scalar cannot.
    """
    since = (_utc_now() - timedelta(days=days)).isoformat()
    try:
        data = supabase.rpc("focus_accuracy_for_user", {
            "p_user_id": student_id,
            "p_from": since,
            "p_bucket_count": _FOCUS_BUCKETS,
            "p_match_seconds": _FOCUS_MATCH_SECONDS,
        }).execute().data or {}
    except Exception as e:                                     # noqa: BLE001
        print(f"[focus_accuracy] {student_id}: {e}")
        return _focus_accuracy_payload(None, days, retrieved=False)
    return _focus_accuracy_payload(data, days, retrieved=True)


def _focus_accuracy_payload(data: dict | None, days: int, retrieved: bool,
                            eeg_enabled: bool = True,
                            eeg_revoked_at: str | None = None,
                            consent_retrieved: bool = True) -> dict:
    """One shape for every outcome, so no caller sees a field only sometimes.

    Built on both branches for the same reason `_shape_summary` is: a consumer
    that has to check whether a key exists before reading it will eventually
    treat "absent" as a fourth state.
    """
    data = data or {}
    pairs = data.get("n") or 0
    r = data.get("r")
    buckets = [
        {"focus_low": b.get("focus_low"), "focus_high": b.get("focus_high"),
         "answered": b.get("answered") or 0, "correct": b.get("correct") or 0,
         "accuracy": round((b.get("correct") or 0) / b["answered"], 4)
         if b.get("answered") else None}
        for b in (data.get("buckets") or [])
    ]
    sufficient = pairs >= _FOCUS_MIN_PAIRS
    return {
        "correlation": round(float(r), 4)
        if sufficient and isinstance(r, (int, float)) else None,
        "pairs": pairs,
        "sufficient": sufficient,
        "min_pairs": _FOCUS_MIN_PAIRS,
        "buckets": buckets,
        "days": days,
        "retrieved": retrieved,
        "eeg_enabled": eeg_enabled,
        "eeg_revoked_at": eeg_revoked_at,
        "consent_retrieved": consent_retrieved,
    }


def _clamp_days(days: int) -> int:
    return max(1, min(days, _CLASS_TREND_MAX_DAYS))


@app.get("/api/classes/{class_id}/topic-heatmap")
def class_topic_heatmap(class_id: str, request: Request):
    """Per-student, per-topic accuracy across a class."""
    user = get_user(request)
    _verify_class_owner(class_id, user["id"])
    return _class_topic_heatmap(class_id)


@app.get("/api/classes/{class_id}/accuracy-trend")
def class_accuracy_trend(class_id: str, request: Request,
                         days: int = _CLASS_TREND_DEFAULT_DAYS):
    """Class accuracy per school day, over the last `days`."""
    user = get_user(request)
    _verify_class_owner(class_id, user["id"])
    return _class_accuracy_trend(class_id, _clamp_days(days))


@app.get("/api/classes/{class_id}/time-of-day")
def class_time_of_day(class_id: str, request: Request,
                      days: int = _CLASS_TREND_DEFAULT_DAYS):
    """When in the week a class works, as weekday x hour of the school day."""
    user = get_user(request)
    _verify_class_owner(class_id, user["id"])
    return _class_time_of_day(class_id, _clamp_days(days))


def _class_alerts(class_id: str, days: int) -> dict:
    """Recent operational alerts for a class's roster.

    Read-only and append-only: there is no acknowledge or dismiss, and that is
    a decision rather than an omission. Both kinds are facts about a session
    that has already ended -- a lesson that timed out yesterday stays timed
    out -- so there is nothing to resolve. Dismissal would imply a workflow
    (assign, triage, close) this product does not have, and the window below
    already bounds what a teacher is shown.

    Alerts are stored per student, not per class, because a student can change
    class and a stored `class_id` would go stale. The roster resolves it at
    read time instead.
    """
    tz = _school_timezone()
    roster, roster_retrieved = _class_roster(class_id)
    profiles = _profiles_many(roster)
    since = (_utc_now() - timedelta(days=days)).isoformat()

    rows, retrieved = [], roster_retrieved
    if roster:
        try:
            rows = (supabase.table("session_alerts")
                    .select("id, user_id, session_id, kind, detail, created_at")
                    .in_("user_id", roster)
                    .gte("created_at", since)
                    .order("created_at", desc=True)
                    .limit(_ALERT_FEED_CAP).execute().data or [])
        except Exception as e:                                 # noqa: BLE001
            print(f"[alerts] could not read the feed for {class_id}: {e}")
            retrieved = False

    alerts = [{
        **r,
        "student_name": (profiles.get(r.get("user_id")) or {}).get("display_name")
        or "Student",
        # The school's day, not the viewer's, so an alert groups under the
        # lesson it belongs to rather than under whatever day it is where the
        # teacher happens to be marking.
        "school_day": _school_day(r.get("created_at"), tz),
    } for r in rows]

    return {
        "alerts": alerts,
        "days": days,
        "student_count": len(roster),
        "timezone": str(tz),
        # The cap is disclosed rather than left to be inferred from a round
        # number of rows. Silent truncation reads as "that is all of them".
        "truncated": len(rows) >= _ALERT_FEED_CAP,
        "retrieved": retrieved,
    }


@app.get("/api/classes/{class_id}/alerts")
def class_alerts(class_id: str, request: Request, days: int = _ALERT_FEED_DAYS):
    """Operational alerts for a class, newest first.

    Teacher-of-this-class only, deliberately narrower than
    `_verify_can_view_student`. These are classroom-operations facts -- a
    lesson that timed out, a headband that recorded nothing -- and they are
    for the person who can walk over and fix it. A parent reading "signals
    missing" has no action available and the weekly report is their surface.
    """
    user = get_user(request)
    _verify_class_owner(class_id, user["id"])
    return _class_alerts(class_id, _clamp_days(days))


@app.get("/api/students/{student_id}/focus-accuracy")
def student_focus_accuracy(student_id: str, request: Request, days: int = 30):
    """Answer accuracy against the EEG focus reading at the time.

    Student-scoped rather than class-scoped, and so gated by
    `_verify_can_view_student` -- this is one child's cognitive data joined to
    their answers, which is the weekly report's access rule, not the roster's.

    A student who has not consented to EEG is never queried. The payload comes
    back with `eeg_enabled: false` and no buckets, which is a different claim
    from an empty result and has to stay one: never fall back to a query that
    reads what the caller opted out of.
    """
    _verify_can_view_student(get_user(request), student_id)
    window = _clamp_days(days)
    channels = _reportable_channels(student_id)
    if not channels.eeg:
        return _focus_accuracy_payload(
            None, window, retrieved=True, eeg_enabled=False,
            eeg_revoked_at=channels.eeg_revoked_at,
            consent_retrieved=channels.consent_retrieved)
    return {**_focus_accuracy(student_id, window),
            "eeg_enabled": True,
            "eeg_revoked_at": channels.eeg_revoked_at,
            "consent_retrieved": channels.consent_retrieved}


# ─── consent: what may be recorded, per student ───────────────────────────
#
# Three channels, named for the sensor rather than the signal it produces.
# `camera` covers both expression and the rPPG heart-rate fallback, so a
# heart-rate failover can never quietly open a webcam the student declined.
#
# Everything is off until a linked parent turns it on. This is the only write
# path -- signal_consent has no insert/update RLS policy for anyone, so the
# frontend's anon key cannot reach it through PostgREST. The checks below are
# the actual enforcement, not a convenience layer over one.

CONSENT_CHANNELS = ("eeg", "headband_optical", "camera")

# What `_may_record` substitutes while the admin consent bypass is live.
# Never returned by `_consent` itself: the bypass decides whether to *ask*,
# not whether anyone agreed, so the consent screen, reporting surfaces and
# poller status must keep showing what the family actually decided.
_CONSENT_ENABLED_ALL = {
    **{f"{c}_enabled": True for c in CONSENT_CHANNELS},
    **{f"{c}_revoked_at": None for c in CONSENT_CHANNELS},
    **{f"{c}_revoked_by": None for c in CONSENT_CHANNELS},
    "updated_by": None,
    "updated_at": None,
    "parent_enabled_at": None,
    "student_ack_at": None,
}

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

    A Z-suffixed value and a +00:00 one mean the same instant but sort
    differently as strings, so both sides must be normalized before
    comparing. This decides whether a student sees a notice about their own
    consent.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        # Should not happen -- these come from the database. Log it: a silent
        # failure here would suppress needs_student_ack, so a student would
        # not learn a parent turned a sensor back on.
        print(f"[consent:parse_ts] unparseable timestamp {value!r}")
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _consent(student_id: str) -> dict:
    """Consent flags for a student. Absent row and failed read both deny.

    Fails closed on purpose -- unlike the reporting helpers below, which
    swallow errors and return an empty payload. Defaulting to "enabled" here
    would record data the student declined, invisibly.

    `retrieved` tells apart "nobody consented" from "we couldn't find out",
    which callers need to phrase differently to a parent.
    """
    try:
        rows = supabase.table("signal_consent").select("*") \
            .eq("user_id", student_id).limit(1).execute().data or []
    except Exception as e:
        print(f"[consent:read] {student_id}: {e}")
        return {**_CONSENT_DENIED, "retrieved": False, "exists": False}
    if not rows:
        # `exists` differs from `retrieved`: both a missing row and a failed
        # read deny, but only a missing row means a write should insert.
        return {**_CONSENT_DENIED, "retrieved": True, "exists": False}
    return {**_CONSENT_DENIED, **rows[0], "retrieved": True, "exists": True}


# The poller writes `cognitive_signals` directly with the service-role client,
# so neither RLS nor the ingest endpoint's gate applies to it. This is what
# subjects it to the same consent rule as the push path.
def _poller_may_record_eeg(student_id: str) -> bool:
    """The poller's recurring permission check, and the log line explaining a refusal.

    A bool alone can't say *why* -- withdrawn consent, a closed school year, and
    a failed read of either would otherwise all be logged as "consent
    withdrawn", which names a decision the family may not have made.
    """
    gate = _may_record(student_id)
    if gate["record_eeg"]:
        return True
    # "EEG" not "eeg": this also renders as a standalone sentence in a 403 body,
    # and `_as_sentence` capitalises the first letter without knowing which
    # words are acronyms. The ingest endpoints' machine-readable `reason` field
    # keeps the lowercase form; this one is read by a person.
    reason = _as_sentence(_not_recording_reason(gate, "EEG not consented"))
    print(f"<<< [eeg-poller] {student_id[:8]}: {reason}", flush=True)
    return False


def _poller_may_record_eeg_reason(student_id: str) -> str:
    """Why `eeg_poller.start()`'s own recheck refused, for its exception text.

    Re-reads `_may_record` rather than caching what the bool check just
    computed -- a shared cache keyed by student would race against the
    poller's own recheck loop for other students. This only ever runs on a
    refusal, so the extra read is cheap, and the two reads disagreeing is
    harmless: worst case is a refusal explained by a reason that became true a
    moment later.
    """
    gate = _may_record(student_id)
    return _as_sentence(_not_recording_reason(gate, "EEG not consented"))


eeg_poller.set_consent_check(_poller_may_record_eeg)
eeg_poller.set_consent_reason_check(_poller_may_record_eeg_reason)


def _IS_DUPLICATE_KEY(exc: Exception) -> bool:
    """Whether a write failed because the row already existed.

    supabase-py's APIError shape has changed between versions, so this checks
    the SQLSTATE (23505) and the message rather than one specific attribute.
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

    Narrower than `_verify_can_view_student`, which also admits teachers. A
    teacher may see that a channel is off, but consent over a child's body is
    not theirs to change.
    """
    if viewer["id"] == student_id:
        return "student"
    if _is_linked_parent(viewer["id"], student_id):
        return "parent"
    raise HTTPException(403, "Only the student or a linked parent can change consent")


def _shape_consent(row: dict, student_id: str, erasures: dict | None = None) -> dict:
    """Per-channel payload: enabled, when it was revoked, and by which role.

    `revoked_by` is a role, never an identity -- a teacher needs to know
    roughly who made the decision ("student opted out" vs "parent opted out"),
    but not which guardian.

    The role is derived per channel from that channel's own revoker, not from
    the row's single `updated_by`: channels are revoked independently, so
    `updated_by` alone would misattribute one channel's withdrawal to
    whoever last touched a different one.
    """
    channels = {}
    for c in CONSENT_CHANNELS:
        enabled = bool(row.get(f"{c}_enabled"))
        revoker = row.get(f"{c}_revoked_by")
        channels[c] = {
            "enabled": enabled,
            "revoked_at": row.get(f"{c}_revoked_at"),
            # Independent of `enabled`/`revoked_at`, and kept even after the
            # channel is re-enabled -- erasure is a fact about stored history,
            # not about the current decision.
            "erased_at": (erasures or {}).get(c),
            # Only meaningful while the channel is off.
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
        # A parent turning a channel back ON must be visible to the student --
        # discovering it by noticing data reappear is not consent. Turning one
        # OFF raises nothing; the student loses nothing and can see it in
        # settings.
        "needs_student_ack": bool(
            enabled_at and (ack_at is None or ack_at < enabled_at)
        ),
    }


class ConsentUpdate(BaseModel):
    eeg_enabled:              bool | None = None
    headband_optical_enabled: bool | None = None
    camera_enabled:           bool | None = None


class ErasureRequest(BaseModel):
    channel: str
    # Erasure is unrecoverable with no undo anywhere in the system, so the
    # request carries its own confirmation rather than trusting a dialog
    # nobody can audit. Omitting it gets a 422 naming the field.
    confirm: bool = False


def _erasures(student_id: str) -> dict:
    """`{channel: erased_at}` for channels whose history has been erased.

    Fails **open** to an empty map, unlike `_consent()` -- same asymmetry as
    `_school_timezone()`. A failed consent read must deny, or it records
    against a refusal. This only decides whether a tile says "erased" or "no
    sensor", so failing here should not blank a dashboard over a fact that
    changes nothing about what may be collected.
    """
    try:
        rows = supabase.table("signal_erasure").select("channel, erased_at") \
            .eq("user_id", student_id).execute().data or []
    except Exception:
        return {}
    return {r["channel"]: r["erased_at"] for r in rows if r.get("channel")}


@app.get("/api/consent/{student_id}")
def get_consent(student_id: str, request: Request):
    user = get_user(request)
    _verify_can_view_student(user, student_id)
    return _shape_consent(_consent(student_id), student_id, _erasures(student_id))


@app.put("/api/consent/{student_id}")
def update_consent(student_id: str, payload: ConsentUpdate, request: Request):
    user = get_user(request)
    actor = _consent_actor(user, student_id)

    current = _consent(student_id)
    if not current["retrieved"]:
        # Writing blind would mean deciding the student's state from a failed
        # read. A 503 is recoverable; a wrongly-enabled channel is not.
        raise HTTPException(503, "Could not read current consent; not changing it")

    now = _utc_now().isoformat()
    fields: dict = {}
    guards: dict = {}
    re_enabled = False
    withdrawn: list[str] = []
    for c in CONSENT_CHANNELS:
        requested = getattr(payload, f"{c}_enabled")
        if requested is None:
            continue
        was = bool(current[f"{c}_enabled"])
        if requested == was:
            continue

        # A student may withdraw at any time; only a parent may re-enable, or
        # the parent's control would be nominal.
        if requested and actor == "student":
            raise HTTPException(
                403,
                f"You can turn {c} off, but only a parent can turn it back on",
            )

        fields[f"{c}_enabled"] = requested
        fields[f"{c}_revoked_at"] = None if requested else now
        fields[f"{c}_revoked_by"] = None if requested else user["id"]
        if not requested:
            # Also recorded as an event: `*_revoked_at` is nulled when the
            # channel comes back on, so it can't answer "what happened" on its
            # own -- a re-enable would wipe the withdrawal notice.
            withdrawn.append(c)
        # State this decision was made against, asserted on the write below.
        guards[f"{c}_enabled"] = was
        if requested and actor == "parent":
            re_enabled = True

    if not fields:
        # No-op. Don't restamp updated_by/updated_at, or a parent re-saving
        # unchanged settings would raise a notice about a change that never
        # happened.
        return _shape_consent(current, student_id, _erasures(student_id))

    fields["updated_by"] = user["id"]
    fields["updated_at"] = now
    if re_enabled:
        fields["parent_enabled_at"] = now

    try:
        if not current["exists"]:
            # Insert rather than upsert, so a row created by a concurrent
            # request collides instead of silently overwriting it.
            try:
                supabase.table("signal_consent") \
                    .insert({"user_id": student_id, **fields}).execute()
            except Exception as e:
                # Same race as the conditional update below -- give it the
                # same 409 rather than a 500, so a client always knows
                # "reload and try again" is the right response.
                if _IS_DUPLICATE_KEY(e):
                    raise HTTPException(
                        409, "Consent changed while you were editing it; reload and try again"
                    )
                raise
            return _shape_consent(_consent(student_id), student_id, _erasures(student_id))

        # Conditional on every flag this call decided against. Read-then-write
        # is not atomic, and the two writes that can race here are a student's
        # withdrawal and a parent's re-enable on the same channel -- losing
        # that race silently would record against a refusal. If the state
        # moved underneath us, the update matches nothing and the caller is
        # told to look again instead of being told it worked.
        q = supabase.table("signal_consent").update(fields).eq("user_id", student_id)
        for col, was in guards.items():
            q = q.eq(col, was)
        written = q.execute().data or []
    except HTTPException:
        # The 409 from the lost insert race above -- re-raise so the outer
        # handler doesn't turn it back into a 500.
        raise
    except Exception as e:
        print(f"[consent:write] {student_id}: {e}")
        raise HTTPException(500, "Could not save consent")

    if not written:
        raise HTTPException(409, "Consent changed while you were editing it; reload and try again")

    _record_withdrawals(student_id, withdrawn, user["id"], now)

    return _shape_consent(_consent(student_id), student_id, _erasures(student_id))


def _record_withdrawals(student_id: str, channels: list[str],
                        by: str, at: str) -> None:
    """Append one row per channel switched off. Never raises.

    Written **after** the conditional update has been confirmed, so a lost race
    does not record a withdrawal that did not happen -- the consent row is the
    authority and this is the log of how it got there.

    It cannot be the other way round either: recording first and writing second
    would leave a notice standing for a change that was rejected, which is a
    parent being told their child did something they did not.

    Swallows its own failure by design. The consent decision is already durable
    at this point, and raising here would turn a successful withdrawal into a
    500 -- telling a student their refusal did not save when it did. The cost of
    the failure is a notice a parent does not get, which is the smaller harm and
    the one that leaves a log line.
    """
    if not channels:
        return
    try:
        supabase.table("consent_withdrawals").insert([
            {"user_id": student_id, "channel": c,
             "withdrawn_at": at, "withdrawn_by": by}
            for c in channels
        ]).execute()
    except Exception as e:
        print(f"[consent:withdrawal-log] {student_id} {channels}: {e}")


@app.post("/api/consent/{student_id}/erase")
def erase_consent_channel(student_id: str, payload: ErasureRequest,
                          request: Request):
    """Destroy one channel's stored signals for one student. Irreversible.

    **A linked parent only** -- not the student, and not a teacher. A student
    can undo a withdrawal by asking a parent, but nothing undoes this, so it
    doesn't use `_consent_actor`, which admits the student.

    **Not** wired to a consent change: withdrawal keeps history, and tying a
    revocation to a delete would turn a reversible control into an
    irreversible one by a side effect nobody asked for. This runs only when
    someone asks for it by name.

    The database half is one transaction: rows, rollups, `chart_paths`, and
    the tombstone recording that it happened. Storage runs after that commits
    and is reported, not awaited -- `charts_failed` on the response, plus a
    log line. Anything left in the bucket afterward is orphaned but
    unservable, since `chart_paths` no longer points at it.
    """
    user = get_user(request)
    if not _is_linked_parent(user["id"], student_id):
        raise HTTPException(403, "Only a linked parent can erase stored signals")
    if payload.channel not in CONSENT_CHANNELS:
        raise HTTPException(422, f"Unknown channel {payload.channel!r}")
    if not payload.confirm:
        raise HTTPException(422, "Set confirm=true; erasing stored signals cannot be undone")

    try:
        result = supabase.rpc("erase_signals", {
            "p_user_id": student_id,
            "p_channel": payload.channel,
            "p_erased_by": user["id"],
            # School timezone, because the rollup rows this rebuilds are
            # bucketed by school day; UTC would move every boundary.
            # `.key`, not the ZoneInfo object: RPC params go through plain
            # `json.dumps`, which can't serialise a ZoneInfo.
            "p_timezone": _school_timezone().key,
        }).execute().data or {}
    except Exception as e:
        # No partial state to describe: the function is one transaction, so
        # either all of it happened or none of it did.
        print(f"[erase] {student_id} {payload.channel}: {e}")
        raise HTTPException(500, "Could not erase stored signals")

    removed, failed = chart_archive.remove_objects(
        supabase, result.pop("object_paths", []))

    return {**result, "charts_removed": removed, "charts_failed": len(failed),
            "erased_at": _erasures(student_id).get(payload.channel)}


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

# The keys inside `features`/`bands` that land in numeric columns. Everything
# else in those dicts is metadata and ends up in `raw`, which is jsonb.
_COGNITIVE_NUMERIC_KEYS = (
    "focus_score", "calm_score", "confidence",
    "alpha", "beta", "theta", "delta", "gamma",
)


class CognitiveSample(BaseModel):
    """One EEG reading, in either of two shapes.

    The flat fields are **already-mapped rows**: 0..1 ratios, the shape a
    developer hand-posting a batch would use.

    `features`/`bands` are **sensor output**: the sidecar's own payload, on
    its 0..100 scale, passed through untouched. The push client sends this
    and does no arithmetic, so the /100 conversion happens exactly once, in
    `signal_mapping`, reached the same way by both the poller and push paths.
    """
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
    # The sidecar-native alternative described above. Free-form keys, so a new
    # sidecar feature doesn't need this model changed too -- but the values
    # that reach numeric columns are still checked, below.
    features:   dict  | None = None
    bands:      dict  | None = None

    @field_validator("focus", "stress", "engagement",
                     "alpha", "beta", "theta", "delta", "gamma")
    @classmethod
    def _finite(cls, v: float | None) -> float | None:
        """NaN and inf reach a `float | None` field untouched -- see below."""
        if v is not None and not math.isfinite(v):
            raise ValueError("must be a finite number")
        return v

    @field_validator("features", "bands")
    @classmethod
    def _numeric_values_must_be_storable(cls, v: dict | None) -> dict | None:
        """Reject what the numeric columns cannot hold, here rather than at the database.

        A `float | None` annotation alone is not enough: Pydantic v2 allows
        NaN/Infinity by default, and `json.loads` accepts those literals too,
        so a non-finite value can reach either the typed fields or these
        dicts. `_finite` below checks the typed ones; this checks these.

        Skipping this costs more than one bad sample: `double precision`
        can't hold either value, so PostgREST rejects the whole batch,
        including every valid sample in it, with a 500 the client retries.

        Only the keys that become columns are checked. Everything else is
        passed-through metadata bound for `raw`, a jsonb column that can hold
        it.
        """
        if v is None:
            return v
        for key in _COGNITIVE_NUMERIC_KEYS:
            if key not in v or v[key] is None:
                continue
            try:
                n = float(v[key])
            except (TypeError, ValueError):
                raise ValueError(f"{key!r} must be a number, got {v[key]!r}")
            # NaN survives both `float()` and json.loads, and would be stored
            # as 100% focus if it ever reached `_ratio` -- see its isfinite guard.
            if not math.isfinite(n):
                raise ValueError(f"{key!r} must be finite, got {v[key]!r}")
        return v

class CognitiveBatch(BaseModel):
    session_id: str
    # Bounded like the other two ingest batches. Under push the writer is an
    # untrusted local process on a student's machine, so this endpoint is the
    # trust boundary and needs the cap.
    samples:    list[CognitiveSample] = Field(max_length=_INGEST_MAX_BATCH)

class FaceSample(BaseModel):
    ts:                  str | None = None
    emotion:             str   | None = None
    attention:           float | None = None
    gaze_x:              float | None = None
    gaze_y:              float | None = None
    # Head pose, in degrees. Separate from gaze: gaze is the iris within the
    # eye opening, this is where the head points, and only the pair together
    # says where a student is actually looking.
    #
    # **Every column the mapper writes needs a field here.** Pydantic drops
    # unrecognised keys silently, so a sidecar field this model doesn't
    # declare is discarded before the handler runs, and the column stays NULL
    # forever, reading as "not measured".
    head_yaw:            float | None = None
    head_pitch:          float | None = None
    head_roll:           float | None = None
    # Qualified name on purpose: its old sibling `identity_confidence` is
    # gone, but the name keeps that ambiguity from returning. See
    # `signal_fusion.face_channel`.
    emotion_confidence:  float | None = None
    emotion_trusted:     bool  | None = None
    raw:                 dict  | None = None

class FaceBatch(BaseModel):
    session_id: str
    samples:    list[FaceSample] = Field(max_length=_INGEST_MAX_BATCH)


class HeartSample(BaseModel):
    """One derived heart reading, from whichever sensor produced it.

    `source` is required and constrained in the database to
    muse_optics | muse_ppg | rppg, because consent is per *sensor* -- a row
    that can't say which sensor produced it can't be consent-checked. Nothing
    writes `rppg` today (camera heart rate failed ECG validation), but the
    column still reflects what the schema permits.
    """
    ts:                 str | None = None
    source:             str
    heart_rate_bpm:     float | None = None
    rmssd_ms:           float | None = None
    beat_coverage:      float | None = None
    rmssd_rejected_by:  str   | None = None
    sqi:                float | None = None
    stress_score:       float | None = None
    stress_category:    str   | None = None
    trusted:            bool  | None = None
    raw:                dict  | None = None

    @field_validator("heart_rate_bpm", "rmssd_ms", "beat_coverage",
                     "sqi", "stress_score")
    @classmethod
    def _finite(cls, v: float | None) -> float | None:
        """Same check as `CognitiveSample._finite`: a `float | None` annotation
        alone doesn't reject NaN/Infinity, and both would fail the insert and
        take the whole batch down with them."""
        if v is not None and not math.isfinite(v):
            raise ValueError("must be a finite number")
        return v


class HeartBatch(BaseModel):
    session_id: str
    samples:    list[HeartSample] = Field(max_length=_INGEST_MAX_BATCH)

def _rate_limit_ingest(user_id: str):
    """Raise 429 once a caller has spent its allowance for the window.

    Monotonic, like `_rate_limit_strategies`, so a clock adjustment can't wipe
    or extend the window. Kept as a separate dict from that limiter: the
    budgets differ by two orders of magnitude, and sharing one would make a
    student's steady 1 Hz ingest compete with their own strategy requests.
    """
    global _ingest_sweep_at
    now = time.monotonic()
    with _ingest_hits_lock:
        # Sweep only once the dict is large and only once per interval -- a
        # size-only trigger would scan every caller on every request and
        # free nothing, since a large dict usually means real callers.
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


def _permitted_heart_sources(gate: dict) -> set[str]:
    """The sources this student may currently be recorded from.

    Takes a `_may_record` result and reads its `record_*` flags -- consent
    and the school year, already composed -- rather than the raw consent
    flags beside them, so callers don't re-check the window by hand.

    A raw `_consent` dict has no `record_*` keys, so passing one yields the
    empty set: nothing recorded. Safe direction for a mistake, pinned by
    `test_a_raw_consent_dict_permits_nothing`.
    """
    allowed: set[str] = set()
    for flag, sources in _HEART_SOURCES_BY_RECORD_FLAG.items():
        if gate.get(flag):
            allowed.update(sources)
    return allowed


def _heart_consent_for_poller(student_id: str, source: str) -> bool:
    """Whether `student_id` consents to heart data from `source`.

    Handed to `eeg_poller` at import (below) because the poller writes with the
    service-role client -- neither RLS nor the ingest endpoint's per-sample
    check reaches it, so under `INGEST_MODE=pull` this is the only enforcement
    there is.

    Built from `_may_record` and `_permitted_heart_sources`, the same two calls
    `/api/signals/heart` makes, rather than a second read of the same tables:
    the two ingestion paths giving different answers about one student is the
    failure this whole arrangement exists to prevent. Both halves of
    `_may_record` already fail closed, so no denial is added here -- and because
    `_permitted_heart_sources` reads the composed `record_*` flags, the school
    year is applied without this function mentioning it.
    """
    return source in _permitted_heart_sources(_may_record(student_id))


eeg_poller.set_heart_consent_check(_heart_consent_for_poller)


def _session_or_403(session_id: str, user_id: str, columns: str = "user_id") -> dict:
    """Fetch a session, refusing it unless the caller owns it. Returns the row.

    Returns the row so callers that need the session anyway (`record_answer`,
    `end_session`) can check ownership without a second query.

    Ownership only -- a session is one student's, so unlike
    `_verify_can_view_student` no teacher or parent is admitted.

    `columns` must include `user_id`, but fails safe if it doesn't: an absent
    column reads as `None`, matching no caller and refusing everyone.

    `_row_or_404` deliberately does not distinguish "no such row" from "the
    read failed" -- both answer 404, since a failed read must never become a
    way past the ownership check below.
    """
    row = _row_or_404(
        supabase.table("sessions").select(columns).eq("id", session_id),
        "Session")
    if row.get("user_id") != user_id:
        raise HTTPException(403, "Not your session")
    return row


def _verify_session_owner(session_id: str, user_id: str):
    """`_session_or_403` for the callers that want only the refusal."""
    _session_or_403(session_id, user_id)

@app.post("/api/signals/cognitive")
def ingest_cognitive(payload: CognitiveBatch, request: Request):
    user = get_user(request)
    # Rate-limit before the session lookup -- the limiter needs only the
    # caller's id, and checking it first spares a flooding client a `sessions`
    # query per request.
    _rate_limit_ingest(user["id"])
    _verify_session_owner(payload.session_id, user["id"])

    # Consent check: the sidecar gates on it too, but a stale process that
    # kept sending after a withdrawal would otherwise keep recording EEG with
    # the withdrawal looking respected everywhere else. `_may_record` fails
    # closed on both the consent read and the school-year check, and the
    # reason says which one refused -- "the year has ended" shouldn't send
    # anyone to the consent screen to fix a setting that's fine.
    consent = _may_record(user["id"])
    if not consent["record_eeg"]:
        return {"ok": True, "inserted": 0, "dropped": len(payload.samples),
                "reason": _not_recording_reason(consent, "eeg not consented")}

    # Accepted in either mode -- rejecting it under `pull` would break mixed
    # local dev -- but warned about when this session is *actually* being
    # double-written: a live poller for it writes the same table from the
    # same sidecar, so every EEG sample lands twice with no dedupe key to
    # catch it.
    #
    # Keyed on a live poller for this session, not on `INGEST_MODE`, which
    # was only a proxy: it fired on the harmless hand-posted dev batch, and a
    # once-per-process flag meant a real double-write later in that process
    # went unreported.
    #
    # Checked and claimed in one call so two concurrent batches can't both
    # pass and log twice; the eviction lives next to `stop()`, the only thing
    # that can bound it.
    if eeg_poller.claim_double_write_warning(payload.session_id):
        print(f"[ingest] session {payload.session_id[:8]} is being written by both "
              f"the poller and /api/signals/cognitive. Every EEG sample is landing "
              f"twice and cognitive_signals has no dedupe key to catch it.",
              flush=True)
    def _row(s: CognitiveSample) -> dict:
        # Sensor output goes through the shared mapper, which owns the
        # 0..100 -> 0..1 conversion and the `stress = 1 - calm` inversion.
        # Flat samples are already in table units and are stored as given.
        if s.features is not None or s.bands is not None:
            # Un-nested into the shape the mapper reads, rather than handed
            # over as one `raw` blob -- the mapper reads `device_id`,
            # `channels`, `state` and `ingestion` off the top level.
            #
            # This doesn't give derived keys precedence over client-supplied
            # ones the way `_raw` does elsewhere: here the promoted values
            # are the client's own either way, since the sidecar is the only
            # thing that knows its device_id. Session ownership and consent
            # are the real defence here, not key precedence.
            raw = dict(s.raw or {})
            envelope = {k: raw.pop(k, None)
                        for k in ("device_id", "channels", "state", "ingestion")}
            return signal_mapping.map_eeg_to_cognitive(
                {"timestamp": s.ts or _utc_now().isoformat(),
                 "features": s.features or {}, "bands": s.bands or {},
                 **envelope,
                 # Whatever the client sent outside the envelope fields.
                 "raw": raw},
                payload.session_id, user["id"])
        return {
            "session_id": payload.session_id,
            "user_id":    user["id"],
            "ts":         s.ts or _utc_now().isoformat(),
            "focus":      s.focus, "stress": s.stress, "engagement": s.engagement,
            "alpha":      s.alpha, "beta":   s.beta,   "theta":      s.theta,
            "delta":      s.delta, "gamma":  s.gamma,  "raw":        s.raw,
        }

    # `None` from the mapper means a disconnected headband reporting zeroed
    # scores, not a real reading of zero. Dropped and counted, so a caller
    # can tell "sent 50, recorded 0" from "sent nothing".
    rows = [r for r in (_row(s) for s in payload.samples) if r is not None]
    if rows: supabase.table("cognitive_signals").insert(rows).execute()
    return {"ok": True, "inserted": len(rows),
            "dropped": len(payload.samples) - len(rows)}

@app.post("/api/signals/face")
def ingest_face(payload: FaceBatch, request: Request):
    user = get_user(request)
    # Rate-limit before the session lookup, for the same reason as the
    # cognitive endpoint above.
    _rate_limit_ingest(user["id"])
    _verify_session_owner(payload.session_id, user["id"])

    # Last line of defence: the sidecar gates on consent too, but a stale
    # process that kept sending after a withdrawal would otherwise keep
    # recording. `_may_record` fails closed, so an unreadable consent row
    # records nothing.
    consent = _may_record(user["id"])
    if not consent["record_camera"]:
        return {"ok": True, "inserted": 0, "dropped": len(payload.samples),
                "reason": _not_recording_reason(consent, "camera not consented")}

    # Through the shared mapper, not inline, so the field list can't drift
    # from what the mapper actually reads.
    rows = [r for r in (
        signal_mapping.map_face_to_face_signal(
            {"timestamp": s.ts or _utc_now().isoformat(),
             "face": {"emotion": s.emotion, "attention": s.attention,
                      "gaze_x": s.gaze_x, "gaze_y": s.gaze_y,
                      "head_yaw": s.head_yaw, "head_pitch": s.head_pitch,
                      "head_roll": s.head_roll,
                      "emotion_confidence": s.emotion_confidence,
                      "trusted": s.emotion_trusted},
             "raw": s.raw},
            payload.session_id, user["id"])
        for s in payload.samples
    ) if r is not None]
    # Insert, not upsert: `face_signals` has no dedupe key yet. See
    # CLAUDE.md for why that is deferred rather than undecided, and the
    # query to run before adding one.
    if rows: supabase.table("face_signals").insert(rows).execute()
    return {"ok": True, "inserted": len(rows)}


@app.post("/api/signals/heart")
def ingest_heart(payload: HeartBatch, request: Request):
    """Derived heart readings, from whichever sensor produced them.

    Consent is checked **per sample**, against the sensor named in `source`,
    since one channel can arrive from two sensors under two separate
    permissions. Samples from a declined sensor are dropped and counted
    rather than failing the whole batch, which would also reject the
    consented samples in it.

    The count comes back so a caller can tell "recorded nothing" from "sent
    nothing".
    """
    user = get_user(request)
    # Rate-limit before the session lookup, for the same reason as the other
    # ingest endpoints.
    _rate_limit_ingest(user["id"])
    _verify_session_owner(payload.session_id, user["id"])

    consent = _may_record(user["id"])
    allowed = _permitted_heart_sources(consent)
    kept = [s for s in payload.samples if s.source in allowed]
    dropped = len(payload.samples) - len(kept)

    # "every sensor was declined" and "we could not find out" both record
    # nothing, but only one is a fault worth chasing -- without this
    # distinction a down consent table looks exactly like a student saying no.
    reason = None
    if not allowed:
        reason = _not_recording_reason(consent, "no consented heart sensor")

    rows = [r for r in (
        signal_mapping.map_heart_to_heart_signal(
            {"timestamp": s.ts or _utc_now().isoformat(),
             "heart": {"source": s.source, "bpm": s.heart_rate_bpm,
                       "rmssd_ms": s.rmssd_ms,
                       "beat_coverage": s.beat_coverage,
                       "rmssd_rejected_by": s.rmssd_rejected_by,
                       "sqi": s.sqi,
                       "stress_score": s.stress_score,
                       "stress_category": s.stress_category,
                       "trusted": s.trusted},
             "raw": s.raw},
            payload.session_id, user["id"])
        for s in kept
    ) if r is not None]

    written = 0
    if rows:
        # ON CONFLICT DO NOTHING on (session_id, source, ts), so a retried
        # batch is idempotent instead of doubling every average it touches.
        #
        # `inserted` counts what the database actually wrote, not what was
        # sent -- taking it from `len(rows)` would report a replay as having
        # inserted a batch it actually inserted none of.
        resp = supabase.table("heart_signals").upsert(
            rows, on_conflict="session_id,source,ts", ignore_duplicates=True
        ).execute()
        # Relies on PostgREST returning a representation (postgrest-py's
        # default). Under `return=minimal` this would misreport every
        # successful write as inserted: 0. If that default ever changes,
        # this arithmetic has to change with it.
        written = len(resp.data or [])
    return {"ok": True, "inserted": written, "dropped": dropped,
            "duplicates": len(rows) - written, "reason": reason}

@app.get("/api/signals/session/{session_id}")
def session_signals(session_id: str, request: Request, since: str | None = None):
    # Returns raw EEG and facial-emotion samples for a session, so resolve
    # whose session it is and check access rather than trusting any
    # authenticated caller.
    #
    # No include_face, deliberately: the facial-recognition opt-out covers
    # only the reporting surfaces that render the switch. This endpoint's
    # only caller, session review, doesn't -- see the scope note in
    # frontend/src/lib/viewPrefs.js.
    user = get_user(request)
    # `_row_or_404`, not `_session_or_403`: a teacher and a parent may both
    # read this, so `_verify_can_view_student` decides access, not ownership.
    sess = _row_or_404(
        supabase.table("sessions").select("user_id").eq("id", session_id), "Session")
    _verify_can_view_student(user, sess["user_id"])

    cog = supabase.table("cognitive_signals").select("*").eq("session_id", session_id)
    fac = supabase.table("face_signals").select("*").eq("session_id", session_id)
    hrt = supabase.table("heart_signals").select("*").eq("session_id", session_id)
    if since:
        cog = cog.gt("ts", since); fac = fac.gt("ts", since); hrt = hrt.gt("ts", since)
    cog_data = cog.order("ts").limit(20000).execute().data or []
    fac_data = fac.order("ts").limit(20000).execute().data or []
    # `source` rides along on every heart row: accuracy differs by sensor and
    # a session can fail over mid-way, so a reader comparing two halves of
    # one trace needs to see the sensor changed, not infer a physiological
    # event.
    hrt_data = hrt.order("ts").limit(20000).execute().data or []
    # The question rides along on the answer, embedded rather than fetched per
    # row. A bare `session_answers` row carries a question *id* and a
    # `selected_index`, which is unreadable on a review screen: a teacher was
    # shown a truncated uuid and the number 2, with no way to know what was
    # asked or what 2 meant. Named columns, not `questions(*)` -- `id` and
    # `created_at` add nothing here, and a column added to the bank later
    # should not start reaching the browser on its own.
    #
    # Embedded, so this stays one query however many answers a session has.
    # It is left-joined by PostgREST, so an answer whose question row was
    # deleted still appears, with `questions: null` -- the answer happened and
    # dropping it would change the session's history.
    answers = (supabase.table("session_answers")
               .select("*, questions(question_text, options, correct_answer, "
                       "subject, difficulty)")
               .eq("session_id", session_id).order("answered_at")
               .execute().data or [])
    return {"cognitive": cog_data, "face": fac_data, "heart": hrt_data, "answers": answers}


@app.get("/api/signals/session/{session_id}/charts")
def session_charts(session_id: str, request: Request):
    """Short-lived signed URLs for a closed session's archived charts.

    The same access rule as the endpoint above, and for a stronger reason. Those
    are rows, which RLS would still filter if this check were wrong; these are
    objects in a bucket with no policies at all, so `_verify_can_view_student`
    is the only thing between a caller and another child's charts.

    Signed and short-lived rather than public. A public object URL cannot be
    un-shared once it has travelled, and a signed one cannot be revoked either
    -- which is why the TTL is small. Consent withdrawn a minute after a URL is
    issued does not reach back and invalidate it.

    The payload keeps the states apart that a bare map of URLs would collapse:

    | Field | Meaning |
    | --- | --- |
    | `archived: false` | the archive never ran -- session closed before archiving shipped, or the job failed |
    | `charts[name]: null` | that channel produced nothing to draw |
    | `name in unavailable` | a path was recorded and the object could not be read |

    A tile rendering "no charts" has to consult all three, the same rule the
    reporting helpers follow -- an absence must not look like a quiet term.
    There is deliberately no `retrieved` flag: unlike those helpers this one
    raises rather than degrading to an empty payload, matching the endpoint
    above, and a flag that is never false is a state that does not exist.
    """
    user = get_user(request)
    # Same as `session_signals` above: shared helper for the lookup, and a
    # relationship check rather than ownership, because a parent reads this too.
    sess = _row_or_404(
        supabase.table("sessions").select("user_id, chart_paths").eq("id", session_id),
        "Session")
    _verify_can_view_student(user, sess["user_id"])

    paths = sess.get("chart_paths")
    if paths is None:
        # Column-NULL is its own answer and needs no storage call: the archive
        # never ran for this session. Distinct from `{}` and from four nulls.
        return {"archived": False, "charts": {}, "unavailable": [],
                "expires_in": chart_archive.SIGNED_URL_TTL_SECONDS}

    # The session's own owner and id, not anything read out of `chart_paths`.
    # That column is writable by the student through PostgREST, so a path taken
    # from it is attacker-controlled: pointed at another child's object, it
    # would be signed by an endpoint that had just correctly confirmed the
    # caller owns *this* session. Presence is all it decides.
    urls, unavailable = chart_archive.signed_chart_urls(
        supabase, paths, sess["user_id"], session_id)
    return {"archived": True, "charts": urls, "unavailable": unavailable,
            "expires_in": chart_archive.SIGNED_URL_TTL_SECONDS}


# ─── live monitoring (only show truly active sessions) ───────────────────



_SIGNAL_CHANNELS = ("cognitive", "face", "heart", "answer")


def _latest_signals_many(session_ids) -> dict[str, dict[str, dict]]:
    """The newest cognitive, face, heart and answer row for many sessions, once.

    `{session_id: {channel: row}}`, with a channel absent when that session
    has no row on it. One call to `latest_signals_for_sessions`, which does
    the `DISTINCT ON` selection in SQL for the whole roster, rather than four
    queries per session in a loop -- this page polls every second.

    **Don't widen the fan-out into a worker pool instead.** Fanning this
    outer loop into a shared pool deadlocks: the waiters and the work they
    wait on end up sharing the same few slots. `_admin_live_pool` is separate
    for exactly this reason.

    Errors are not caught here. `class_live` is the caller, and a failed
    read silently turned into an empty map would draw every student as idle
    -- the surface a teacher uses to decide who needs help.
    """
    ids = _unique_ids(session_ids)
    if not ids:
        return {}
    res = supabase.rpc("latest_signals_for_sessions",
                       {"p_session_ids": ids}).execute()
    rows = res.data or []
    if isinstance(rows, dict):
        rows = [rows]
    out: dict[str, dict[str, dict]] = {}
    for r in rows:
        sid = r.get("session_id")
        channel = r.get("channel")
        if sid and channel in _SIGNAL_CHANNELS:
            out.setdefault(sid, {})[channel] = r.get("payload") or {}
    return out


@app.get("/api/teacher/classes/{class_id}/live")
def class_live(class_id: str, request: Request):
    """Live signals for the students of a class the caller owns.

    No include_face, deliberately, for the reason `session_signals` gives
    above: the facial opt-out covers reporting surfaces, and this is a live
    view of whether the camera is currently working, which a reporting-window
    preference has no sensible reading on. See the scope note in
    frontend/src/lib/viewPrefs.js.
    """
    user = get_user(request)
    _verify_class_owner(class_id, user["id"])

    LIVE_WINDOW_SEC = _LIVE_WINDOW_SEC
    STALE_AFTER_SEC = _STALE_AFTER_SEC
    now = datetime.utcnow()
    live_cutoff  = (now - timedelta(seconds=LIVE_WINDOW_SEC)).isoformat()
    stale_cutoff = (now - timedelta(seconds=STALE_AFTER_SEC)).isoformat()

    members = supabase.table("class_memberships").select("student_id").eq("class_id", class_id).execute().data or []
    roster = [m["student_id"] for m in members]
    # Batched reads for the whole roster, not per student -- this endpoint
    # polls every few seconds while a class is working.
    profiles = _profiles_many(roster)
    open_by_student = _open_sessions_many(roster)
    latest_by_session = _latest_signals_many(
        s["id"] for rows in open_by_student.values() for s in rows[:1])
    out = []
    for m in members:
        sid = m["student_id"]
        p = profiles.get(sid) or {}
        open_sessions = open_by_student.get(sid) or []

        active = None
        latest_cog = latest_face = latest_heart = None

        if open_sessions:
            sess = open_sessions[0]
            sid2 = sess["id"]
            newest = latest_by_session.get(sid2) or {}
            c = [newest["cognitive"]] if "cognitive" in newest else []
            f = [newest["face"]] if "face" in newest else []
            h = [newest["heart"]] if "heart" in newest else []
            a = [newest["answer"]] if "answer" in newest else []

            latest_cog  = c[0] if c else None
            latest_face = f[0] if f else None
            # Newest row, trusted or not -- unlike the weekly aggregate, which
            # takes the newest *trusted* one. A blank card here would be
            # indistinguishable from a stopped sensor, so the row carries
            # `trusted` and lets the card say which it is.
            latest_heart = h[0] if h else None

            candidates = []
            if latest_cog and latest_cog.get("ts"):       candidates.append(latest_cog["ts"])
            if latest_face and latest_face.get("ts"):     candidates.append(latest_face["ts"])
            if latest_heart and latest_heart.get("ts"):   candidates.append(latest_heart["ts"])
            if a and a[0].get("answered_at"):             candidates.append(a[0]["answered_at"])
            if sess.get("started_at"):                    candidates.append(sess["started_at"])
            last_activity = max(candidates) if candidates else sess.get("started_at")

            if last_activity and last_activity < stale_cutoff:
                # `sid` is the student, not the teacher viewing this page --
                # the reservation and the close both belong to whoever the
                # stale session is about.
                #
                # Stop the poller before closing: a poller left running past
                # this point could insert a row for the session after
                # `_discard_if_nothing_recorded` has already looked, leaving
                # an empty pairing deleted with a row still pointing at it.
                eeg_poller.stop(sid2, sid)
                # Goes through the shared `_close_session` helper so the
                # empty-session discard, credit, rollup and archive all run --
                # see the close-site ordering rule in CLAUDE.md.
                _close_session(sid, sess, now.isoformat(),
                               closed_by=CLOSED_BY_SWEEP)
                active = None; latest_cog = None; latest_face = None; latest_heart = None
            elif last_activity and last_activity >= live_cutoff:
                active = sess

        out.append({
            "user_id":          sid,
            "name":             p.get("display_name") or "Student",
            "email":            p.get("email") or "",
            "active_session":   active,
            "latest_cognitive": latest_cog,
            "latest_face":      latest_face,
            # `source` rides along so the card can show which sensor is
            # live: accuracy differs by sensor, and a teacher watching a
            # trace change shape should see the sensor changed, not read it
            # as the student changing.
            "latest_heart":     latest_heart,
        })
    return out


# ─── EEG sidecar integration ─────────────────────────���───────────────────

def _poller_status(user_id: str) -> dict:
    """`eeg_poller.status`, plus why it is not running when the answer is known.

    "Stopped because the student withdrew consent" and "stopped because the
    headband dropped" are different sentences; the frontend rendered both as
    just "not running".

    Derived from current consent rather than remembered on the poller: a
    remembered reason needed the dead poller to stay around to be readable,
    and could go stale the moment a parent re-enabled the channel. Consent is
    the authority here, so ask it directly.
    """
    status = eeg_poller.status(user_id)
    if status.get("running"):
        return status
    # Window checked first: outside the school year nothing records, so
    # reporting "consent_withdrawn" here would describe a decision the family
    # never made.
    #
    # `_may_record`, not a hand-ordered window read plus consent read, so
    # this can't disagree with the recording sites about which answer wins.
    # It carries the raw consent flags too, which is what lets this report a
    # withdrawal by name rather than only "not recording".
    gate = _may_record(user_id)
    stopped = _window_meaning(gate["window_state"]).stopped_reason
    if stopped:
        return {**status, "stopped_reason": stopped,
                "window_starts_on": gate["window_starts_on"],
                "window_ends_on": gate["window_ends_on"]}
    if not gate.get("retrieved"):
        # Unknown, not "withdrawn": a failed read is not a refusal, and
        # saying so wrongly to a parent is worse than saying nothing.
        return {**status, "stopped_reason": "consent_unknown"}
    if not gate.get("eeg_enabled"):
        return {**status, "stopped_reason": "consent_withdrawn",
                "revoked_at": gate.get("eeg_revoked_at")}
    return status


def _refuse_under_push(what: str) -> None:
    """409 rather than the 503 the liveness probe would raise.

    Under push ingestion this backend has no route to a sidecar, so "EEG
    service not running on port 8001" is true but misleading -- it reads as
    a fault when the deployment just works differently.

    Every endpoint that would otherwise raise that 503 calls this first, so
    the message can't drift between endpoints. Must run *before*
    `eeg_client.is_alive()`, or the misleading message wins the race.
    """
    if eeg_poller.INGEST_MODE == "push":
        raise HTTPException(
            409,
            f"This deployment uses push ingestion: the sidecar on the student's "
            f"own device posts to /api/signals/*, and this backend cannot {what}. "
            f"Nothing is wrong with the headband.",
        )


def _reserve_and_call(user_id: str, device_id: str, fn, *args,
                      session_id: str | None = None):
    """Claim device_id's pre-claim reservation, then run the bridge call.

    `session_id`, when sent, records which pairing attempt owns the
    reservation, so closing that session releases it and closing a
    different one does not. Optional: an older frontend that sends nothing
    gets the old release-everything behaviour instead of an unreleasable
    reservation.

    Shared by muse/refresh and muse/connect, the two actions that *start* a
    pairing attempt. muse/disconnect checks ownership instead of claiming it
    -- see its own comment.

    Claims before knowing whether `fn` will succeed, so every path that ends
    the request without a working bridge call releases what it just
    claimed, scoped to this device_id only -- a failure here must not drop a
    different reservation the same user holds elsewhere.
    """
    if not eeg_poller.reserve_device(user_id, device_id, session_id):
        raise HTTPException(403, "Station in use by another user")
    if not eeg_client.is_alive():
        eeg_poller.release_reservation(user_id, device_id)
        raise HTTPException(503, "EEG service not running on port 8001")
    try:
        return fn(*args)
    except Exception as e:
        eeg_poller.release_reservation(user_id, device_id)
        raise HTTPException(502, f"Bridge error: {e}")


@app.post("/api/eeg/muse/refresh")
def eeg_muse_refresh(request: Request, body: dict = Body(default={})):
    """Trigger a Bluetooth scan for nearby Muse headbands."""
    user = get_user(request)
    device_id = (body or {}).get("device_id") or eeg_client.DEFAULT_DEVICE_ID
    # Refuse before _reserve_and_call, since reserve_device mutates the
    # reservation registry and under push there is no /api/eeg/start to ever
    # release it -- a reservation claimed here would sit until its TTL with
    # no legitimate way to clear it early, on a device potentially shared by
    # many students. Station contention is a pull-deployment concept and
    # meaningless under push, so refusing first costs nothing real.
    _refuse_under_push("scan for headbands")
    # A station with a live poller belongs to that poller's owner; another
    # user rescanning it disrupts their session. reserve_device also claims
    # the pre-claim pairing window here, since a scan is the first
    # interaction with an unclaimed station.
    return _reserve_and_call(user["id"], device_id, eeg_client.muse_refresh, device_id,
                             session_id=(body or {}).get("session_id"))

@app.post("/api/eeg/muse/connect")
def eeg_muse_connect(request: Request, body: dict = Body(...)):
    """Connect to a specific Muse headband by name."""
    user = get_user(request)
    name = (body.get("name") or "").strip()
    device_id = body.get("device_id") or eeg_client.DEFAULT_DEVICE_ID
    if not name:
        raise HTTPException(400, "Device name required")
    # See eeg_muse_refresh above -- before _reserve_and_call, not after.
    _refuse_under_push("connect to a headband")
    return _reserve_and_call(user["id"], device_id, eeg_client.muse_connect, name, device_id,
                             session_id=body.get("session_id"))

@app.post("/api/eeg/muse/disconnect")
def eeg_muse_disconnect(request: Request, body: dict = Body(default={})):
    """Tell the native bridge to disconnect from the current headband."""
    user = get_user(request)
    device_id = (body or {}).get("device_id") or eeg_client.DEFAULT_DEVICE_ID
    # can_use_device, not reserve_device: disconnect is a teardown action,
    # not the start of a pairing attempt, so calling it against a station
    # nobody owns has nothing worth protecting with an exclusive hold.
    # Claiming a reservation here would let a disconnect against a free
    # station lock it up indefinitely by repeating the call. can_use_device
    # still blocks a stranger from disconnecting someone else's live or
    # reserved station, the actual case worth guarding against.
    if not eeg_poller.can_use_device(user["id"], device_id):
        raise HTTPException(403, "Station in use by another user")
    # Releases the caller's own reservation on this device if they hold one
    # (a no-op otherwise, same as /api/eeg/stop) -- a student who
    # scanned/connected and then disconnects is done with this station just
    # as explicitly as calling /stop, and without this it stayed locked to
    # them until its TTL expired.
    eeg_poller.release_reservation(user["id"], device_id)
    _refuse_under_push("disconnect a headband")
    if not eeg_client.is_alive():
        raise HTTPException(503, "EEG service not running on port 8001")
    try:
        return eeg_client.muse_disconnect(device_id)
    except Exception as e:
        raise HTTPException(502, f"Bridge error: {e}")

@app.get("/api/eeg/devices")
def eeg_devices(request: Request):
    """List the sidecar's registered devices (stations), for the frontend picker.

    Enumeration-only, gated on being logged in (not can_use_device): returns
    device_id/kind/running/connection_state_name for every station, with no
    biometric values or per-user ownership. The picker needs the full list,
    and "station X is in use" is the extent of what leaks.
    """
    get_user(request)
    if eeg_poller.INGEST_MODE == "push":
        return {"available": None, "ingest_mode": "push", "devices": []}
    if not eeg_client.is_alive():
        return {"available": False, "ingest_mode": "pull", "devices": []}
    return {"available": True, "ingest_mode": "pull",
            "devices": eeg_client.list_devices()}

@app.get("/api/eeg/debug")
def eeg_debug(request: Request, device_id: str = eeg_client.DEFAULT_DEVICE_ID):
    """Raw EEG snapshot for local development -- returns the full state from EEGResearch."""
    user = get_user(request)
    if eeg_poller.INGEST_MODE == "push":
        return {"available": None, "ingest_mode": "push"}
    if not eeg_client.is_alive():
        return {"available": False, "ingest_mode": "pull"}
    # A station with a live poller holds that poller owner's in-progress
    # biometric data -- don't let another user read it just by knowing the
    # device_id.
    if not eeg_poller.can_use_device(user["id"], device_id):
        return {"available": False, "reason": "in_use_by_other"}
    # Same as eeg_health: a missing/misconfigured token makes get_state and
    # get_muse_status raise by design, which would otherwise surface as a
    # bare 500. Report it instead.
    try:
        snapshot = eeg_client.get_state(device_id, timeout=1.5)
        muse     = eeg_client.get_muse_status(device_id)
    except RuntimeError as e:
        return {"available": False, "error": str(e)}
    return {"available": True, "snapshot": snapshot, "muse": muse}


@app.get("/api/eeg/health")
def eeg_health():
    """Tells the frontend whether the EEGResearch sidecar service is reachable.

    Under push ingestion there is nothing reachable from here, and this poll
    runs from page load, unlike /status which waits for a session. A flat
    `available: False` here would put "EEG service not reachable" on the
    first screen a student sees.
    """
    if eeg_poller.INGEST_MODE == "push":
        # None, not False, for the same reason as /status: "not probed in
        # this deployment" differs from "probed and down".
        return {"available": None, "ingest_mode": "push", "url": None}
    alive = eeg_client.is_alive()
    if not alive:
        return {"available": False, "ingest_mode": "pull",
                "url": eeg_client.EEG_API_URL}
    # is_alive() only hits the sidecar's unauthenticated /healthz, so a
    # reachable sidecar says nothing about auth. get_muse_status() needs the
    # learner token and raises by design when it's missing or misconfigured
    # -- a config error, not an outage, so report it rather than 500.
    try:
        muse = eeg_client.get_muse_status()
    except RuntimeError as e:
        return {"available": False, "url": eeg_client.EEG_API_URL, "error": str(e)}
    return {"available": True, "url": eeg_client.EEG_API_URL, "muse": muse}

@app.post("/api/eeg/start")
def eeg_start(payload: EegSessionRequest, request: Request):
    user = get_user(request)
    # Shared helper rather than a hand-written copy of the same two checks
    # -- re-deriving an access rule per endpoint is what let the
    # `class_live` guard drift.
    sess = _session_or_403(payload.session_id, user["id"], "user_id, ended_at")
    if sess.get("ended_at"):
        raise HTTPException(400, "Session already ended")
    _refuse_under_push("start a poller")
    # Checked before the poller exists at all. Historical rows stay; nothing
    # new is written until consent is given. `_may_record` fails closed, so
    # an unreadable row refuses rather than records.
    consent = _may_record(user["id"])
    if not consent["record_eeg"]:
        # 403, not 409: 409 means "this deployment doesn't work that way",
        # and a closed school year is a fact about the school, not the
        # deployment. Same helper as the ingest endpoints, so the
        # window-before-consent precedence is decided once.
        raise HTTPException(403, _as_sentence(_not_recording_reason(
            consent,
            "EEG recording is switched off for this student.",
            "Could not check whether EEG recording is allowed, so it was not started.")))
    if not eeg_client.is_alive():
        raise HTTPException(503, "EEG service is not running on port 8001")
    device_id = payload.device_id or eeg_client.DEFAULT_DEVICE_ID
    # Without this, a typo'd device_id spawns a poller that dies on the
    # sidecar's 404 -- but eeg_poller.start() has already returned
    # running: True, so the user sees "connected" and silently gets no
    # data. An empty known_ids (list_devices() erroring even though
    # is_alive() just succeeded) falls back to allowing the start rather
    # than blocking on a transient glitch.
    known_ids = {d.get("device_id") for d in eeg_client.list_devices()}
    if known_ids and device_id not in known_ids:
        raise HTTPException(404, f"Unknown device_id: {device_id!r}")
    try:
        out = eeg_poller.start(supabase, user["id"], payload.session_id, device_id)
    except eeg_poller.ConsentError as e:
        # `_may_record` passed a moment ago and `start()` checks again, so
        # this is the gap between them (a withdrawal, or the year ending, in
        # between) or an unwired consent check on this deployment. Either
        # way the message comes from the exception rather than being
        # reported as a decision about this student.
        raise HTTPException(403, str(e))
    except eeg_poller.DeviceClaimedError:
        # Covers two causes with one message: a live poller already
        # recording for someone else, or someone else's reservation from an
        # in-progress scan/connect. Both resolve the same way -- wait for
        # the other user -- so the caller doesn't need to know which.
        raise HTTPException(
            409,
            "This headband is already in use by another user. Ask them to "
            "disconnect, or wait a few seconds and try again.",
        )
    return {"ok": True, **out}

@app.post("/api/eeg/stop")
def eeg_stop(payload: EegSessionRequest, request: Request):
    user = get_user(request)
    _session_or_403(payload.session_id, user["id"])
    # Releases user["id"]'s reservation too, unconditionally -- not just
    # when a live poller existed. A user who only got as far as scan/connect
    # still holds a claim on the station, and /stop is the signal they're
    # done with it.
    out = eeg_poller.stop(payload.session_id, user["id"])
    return {"ok": True, **out}

@app.get("/api/eeg/status")
def eeg_status(request: Request, device_id: str = eeg_client.DEFAULT_DEVICE_ID):
    user = get_user(request)
    # Under push ingestion this backend has no route to a sidecar, so
    # `is_alive()` is false forever, and this endpoint is polled every 3
    # seconds -- without the mode check it would render as a continuous
    # "EEG service is down" after /start's carefully-worded 409.
    push = eeg_poller.INGEST_MODE == "push"
    # Must run **before** the muse probe. `get_muse_status` calls
    # `_learner_headers()`, which raises when EEG_API_TOKEN is unset (the
    # normal state under push) outside any try block, so the endpoint would
    # 500 every 3 seconds. With a token set it's a 2 s blocking probe to a
    # host that doesn't exist, per student, holding an anyio threadpool slot
    # each time. Blanks only the muse block, not the whole response -- the
    # caller's own poller status is theirs regardless of device_id.
    if push:
        # None, not False: "not probed in this deployment" differs from
        # "probed and absent".
        muse = {"available": None, "reason": "push_ingestion"}
    elif eeg_poller.can_use_device(user["id"], device_id):
        muse = eeg_client.get_muse_status(device_id)
    else:
        muse = {"available": False, "reason": "in_use_by_other"}
    return {
        # None, not False, for the same reason as `muse` above -- a
        # consumer branching on falsiness would render both the same.
        "service": None if push else eeg_client.is_alive(),
        # Carried so a client can say *why* rather than inferring it from a null.
        "ingest_mode": eeg_poller.INGEST_MODE,
        "muse":    muse,
        "poller":  _poller_status(user["id"]),
    }


# ─── parent endpoints ────────────────────────────────────────────────────

@app.post("/api/parent/link-child")
def link_child(payload: LinkChildRequest, request: Request):
    user = get_user(request)
    if _role(user["id"]) != "parent":
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


@app.delete("/api/parent/children/{child_id}")
def unlink_child(child_id: str, request: Request):
    """Remove one parent-child link.

    A parent who pasted the wrong UUID, or whose child left the household,
    otherwise has a standing relationship that `_verify_can_view_student`
    reads as entitlement to that child's reports and `signal_consent` reads
    as the right to re-enable a sensor the child switched off. That
    relationship needed a way to end.

    Scoped by `parent_id` as well as `child_id`, so this can only delete the
    caller's own link -- scoped by child alone, it would let one parent cut
    another off from their own child.

    **Consent and recorded signals are untouched.** Unlinking is not erasure
    or a withdrawal: `signal_consent` still holds what the family decided,
    and a re-link restores the view of a history that was never destroyed.
    Erasure is `POST /api/consent/{id}/erase`, a separate request a parent
    must make by name, so this can never destroy data as a side effect.
    """
    user = get_user(request)
    res = supabase.table("parent_child_links").delete()         .eq("parent_id", user["id"]).eq("child_id", child_id).execute()
    # 404 on a link that wasn't there, rather than a cheerful ok -- a parent
    # who unlinked the wrong child needs to tell "that is done" from "that
    # was never yours".
    if not res.data:
        raise HTTPException(404, "Not linked to this child")
    return {"ok": True, "child_id": child_id}


@app.get("/api/student/parent-links")
def my_unacknowledged_parent_links(request: Request):
    """Links to this student that they have not been told about yet.

    A parent creates a link knowing only a user id -- nothing asks the child
    or tells them, and from that moment the parent may read their reports and
    re-enable a sensor the child switched off. This is the read behind the
    banner that closes that gap.

    **Notify, not block.** No endpoint waits on the acknowledgement -- a gate
    would put a child between a parent and reports they're entitled to, and
    some children would simply never clear it.

    Fails **open**, to an empty list. It decides whether an advisory banner is
    drawn and nothing else, so the reporting-surface direction is the right one
    here: a database blip must not put a notice on a child's dashboard about a
    link that may not exist. That is the opposite of `_consent()`, which fails
    closed because it decides whether data may be recorded.
    """
    user = get_user(request)
    try:
        rows = supabase.table("parent_child_links")             .select("id, parent_id, created_at")             .eq("child_id", user["id"]).is_("student_ack_at", "null")             .order("created_at", desc=True).limit(10).execute().data or []
    except Exception as e:
        print(f"[parent-links] {user['id']}: {e}")
        return {"links": [], "retrieved": False}
    # Names in one read rather than one per link, and only for the handful of
    # rows that survived the filter -- normally none.
    names = _profiles_many([r["parent_id"] for r in rows])
    return {
        "links": [{
            "id":          r["id"],
            "parent_name": (names.get(r["parent_id"]) or {}).get("display_name") or "A parent",
            "linked_at":   r["created_at"],
        } for r in rows],
        "retrieved": True,
    }


@app.post("/api/student/parent-links/ack")
def ack_parent_links(request: Request):
    """The student has seen the notice.

    Stamps every unacknowledged link for the caller, not one by one: the banner
    names them together and dismissing it is one decision. Scoped by
    `child_id`, so a student can only ever acknowledge their own -- and the link
    id is never taken from the client, which would let a caller stamp a link
    that is not theirs and suppress somebody else's notice.

    A write that matched nothing is a 404 rather than a cheerful ok, like
    `/api/consent/ack` beside it: reporting success for a dismissal that did not
    land leaves the client believing a notice is gone that is still there.
    """
    user = get_user(request)
    try:
        written = supabase.table("parent_child_links")             .update({"student_ack_at": _utc_now().isoformat()})             .eq("child_id", user["id"]).is_("student_ack_at", "null")             .execute().data or []
    except Exception as e:
        print(f"[parent-links:ack] {user['id']}: {e}")
        raise HTTPException(500, "Could not acknowledge")
    if not written:
        raise HTTPException(404, "Nothing to acknowledge")
    return {"ok": True, "acknowledged": len(written)}


# A cap on one parent's banner, not on the table. A parent who has not opened
# the dashboard in months would otherwise pull a term of events to render a
# handful of lines -- and the endpoint dedupes to one line per channel anyway,
# so beyond a few dozen rows there is nothing left to say.
_MAX_WITHDRAWAL_NOTICES = 200


class ConsentNoticeAck(BaseModel):
    """`{child_id: iso8601}` -- the newest withdrawal the parent was shown, per
    child. The client hands back what the server gave it rather than a
    timestamp of its own, so "seen" means one agreed value."""
    through: dict[str, str] = {}


CONSENT_CHANNEL_LABELS = {
    "eeg":              "the headband",
    "headband_optical": "the headband's heart-rate sensor",
    "camera":           "the camera",
}


@app.get("/api/parent/consent-notices")
def parent_consent_notices(request: Request):
    """Channels a linked child has switched off since this parent last looked.

    The consent model only notified in one direction: a parent re-enabling a
    channel raises `needs_student_ack`, but nothing told a parent their child
    had turned one off. This is the read behind the notice that closes that gap.

    Read from `consent_withdrawals`, **not** `signal_consent`'s
    `*_revoked_at` -- those columns are correctly nulled when a channel is
    re-enabled, so deriving the notice from them would let restoring a
    channel silently erase the notice for every linked parent.

    Keyed on `parent_child_links.parent_ack_at`, per (parent, child): keeping
    it on `signal_consent` would let the first of two linked parents to
    acknowledge clear the notice for the second.

    Fails **open** to an empty list with `retrieved: false`, like the link
    notice: this only decides whether an advisory banner is drawn, so a blip
    must not falsely tell a parent their child withdrew something.
    `_consent()` itself still fails closed, since that one decides whether
    data may be recorded -- a different question.
    """
    user = get_user(request)
    try:
        links = supabase.table("parent_child_links").select("child_id, parent_ack_at")             .eq("parent_id", user["id"]).execute().data or []
    except Exception as e:
        print(f"[consent-notices] {user['id']}: {e}")
        return {"notices": [], "retrieved": False}
    if not links:
        return {"notices": [], "retrieved": True}

    ids = [l["child_id"] for l in links]
    # One query covers every linked child; the per-child comparison happens
    # below. Bounded, since a long-unacknowledged parent could otherwise
    # pull a whole term of events.
    try:
        rows = supabase.table("consent_withdrawals")             .select("user_id, channel, withdrawn_at")             .in_("user_id", ids)             .order("withdrawn_at", desc=True)             .limit(_MAX_WITHDRAWAL_NOTICES).execute().data or []
    except Exception as e:
        print(f"[consent-notices] {user['id']}: {e}")
        return {"notices": [], "retrieved": False}

    names = _profiles_many(ids)
    since_by_child = {l["child_id"]: l.get("parent_ack_at") for l in links}

    by_child: dict[str, list[dict]] = {}
    for r in rows:
        cid = r["user_id"]
        stamp = r["withdrawn_at"]
        since = since_by_child.get(cid)
        # Newer than this parent's last ack, or any withdrawal if they never
        # acknowledged. Both sides are ISO-8601 UTC from PostgREST, so
        # lexical comparison works -- and this is a fail-open advisory
        # banner, not one of the gates `_parse_ts` protects.
        if since and stamp <= since:
            continue
        # One line per channel, newest first. A channel switched off twice is
        # one thing to tell a parent, not two.
        seen = by_child.setdefault(cid, [])
        if any(c["channel"] == r["channel"] for c in seen):
            continue
        seen.append({"channel": r["channel"],
                     "label": CONSENT_CHANNEL_LABELS.get(r["channel"], r["channel"]),
                     "at": stamp})

    notices = []
    for cid, channels in by_child.items():
        notices.append({
            "child_id":   cid,
            "child_name": (names.get(cid) or {}).get("display_name") or "Your child",
            "channels":   channels,
            # Watermark the client hands back on acknowledgement -- sent by
            # the server, not recomputed client-side, so both sides agree on
            # "what you were shown".
            "through":    max(c["at"] for c in channels),
        })
    return {"notices": notices, "retrieved": True}


@app.post("/api/parent/consent-notices/ack")
def ack_parent_consent_notices(payload: ConsentNoticeAck, request: Request):
    """The parent has seen these notices, up to the point they were shown.

    **Stamps the watermark the client was given, not `now()`.** Stamping the
    current time would acknowledge withdrawals the parent never saw -- one
    landing between the read that drew the banner and the click that
    dismissed it would be marked seen and never shown again.

    Per child, since the column lives on the link row: acking one child's
    notice must not clear another's.

    Scoped by `parent_id` on every write, and the link id is never taken
    from the client, which would let a caller clear somebody else's notice.

    Not a 404 when nothing changed, unlike `/api/consent/ack`: double-clicking
    is ordinary, and re-acknowledging the same watermark is harmless.
    """
    user = get_user(request)
    try:
        for child_id, through in (payload.through or {}).items():
            supabase.table("parent_child_links")                 .update({"parent_ack_at": through})                 .eq("parent_id", user["id"]).eq("child_id", child_id).execute()
    except Exception as e:
        print(f"[consent-notices:ack] {user['id']}: {e}")
        raise HTTPException(500, "Could not acknowledge")
    return {"ok": True}


@app.get("/api/parent/children")
def my_children(request: Request, include_face: bool = True):
    """A parent's linked children with their headline signal averages.

    include_face=false carries the facial-recognition opt-out into the
    aggregate, so the dashboard honours the same control as the child's
    report. Without it, switching facial reporting off on a report and
    navigating back put facial attention straight back on screen.

    Stored consent is resolved per child on top of that, so one sibling's
    refusal cannot suppress another's data, and no child's declined channel
    is read because a sibling permitted it.
    """
    user = get_user(request)
    links = supabase.table("parent_child_links").select("child_id, created_at") \
        .eq("parent_id", user["id"]).execute()
    # Grouped by consent flags, since the batch RPC takes one flag pair per
    # call -- a single pair would mean reading a channel a child declined, or
    # hiding one a sibling permitted. At most four groups exist (heart x
    # emotion) and usually just one, since siblings are typically configured
    # alike.
    child_ids = [lnk["child_id"] for lnk in (links.data or [])]
    channels_by_child = {cid: _reportable_channels(cid, include_face)
                         for cid in child_ids}
    # Keyed on the flags alone, not the whole ReportChannels: `consent_retrieved`
    # doesn't change what the RPC is asked for, so including it would split
    # two children with identical flags into separate round-trips.
    by_channels: dict[tuple[bool, bool], list[str]] = {}
    for cid, ch in channels_by_child.items():
        by_channels.setdefault((ch.heart, ch.emotion), []).append(cid)

    summaries: dict | None = {}
    for (heart_flag, emotion_flag), group in by_channels.items():
        part = _signal_summaries(group, include_heart=heart_flag,
                                 include_emotion=emotion_flag,
                                 channels_by_student=channels_by_child)
        if part is None:
            # One failed group fails the whole call, discarding groups that
            # succeeded. Deliberate: `summaries_retrieved` is one flag for
            # the endpoint, so a partial result would have to report failed
            # children as "empty" -- exactly the word that must not stand in
            # for "we couldn't read it".
            summaries = None
            break
        summaries.update(part)
    # None is a failed read; {} is a read that found nothing. Both fall back
    # below, and the fallback has to say which -- otherwise a broken RPC
    # reaches a parent as "your child recorded nothing this week".
    summaries_retrieved = summaries is not None
    summaries = summaries or {}
    children = []
    kids = [lnk["child_id"] for lnk in (links.data or [])]
    # One read each for all the children, not one each per child.
    all_stats = _stats_including_open_session_many(kids)
    profiles = _profiles_many(kids)
    all_perf = _topic_performance_many(kids)
    for lnk in (links.data or []):
        cid = lnk["child_id"]
        stats = all_stats.get(cid) or {}
        # Still per child, deliberately: "the five most recent per child" has
        # no batch form in PostgREST -- one `in_` query returns the newest
        # five overall, which could be one busy child's five. A parent has a
        # handful of children, so this stays cheap; a class roster would not.
        sess_res = supabase.table("sessions").select("*").eq("user_id", cid).order("started_at", desc=True).limit(5).execute()
        p = profiles.get(cid) or {}
        children.append({
            "user_id":     cid,
            "name":        p.get("display_name") or "Student",
            "email":       p.get("email") or "",
            "linked_at":   lnk["created_at"],
            "stats":       stats,
            "sessions":    sess_res.data or [],
            "performance": all_perf.get(cid) or [],
            # Headline averages only, deliberately not the full weekly
            # report: that pulls thousands of raw rows per child, and this
            # runs on a dashboard that loads every visit.
            # The per-child consent fields are stamped inside
            # `_signal_summaries`, since the batch RPC groups children by
            # flag pair and can't carry a per-child revocation date or
            # `consent_retrieved` on its own.
            "signal_summary": summaries[str(cid)]
                              if str(cid) in summaries
                              else _shape_summary(None,
                                                channels_by_child[cid].heart,
                                                channels_by_child[cid].emotion,
                                                summaries_retrieved,
                                                channels_by_child[cid].consent_retrieved,
                                                emotion_revoked_at=channels_by_child[cid].emotion_revoked_at,
                                                heart_revoked_at=channels_by_child[cid].heart_revoked_at,
                                                eeg_enabled=channels_by_child[cid].eeg,
                                                eeg_revoked_at=channels_by_child[cid].eeg_revoked_at),
        })
    return children


# ─── admin ───────────────────────────────────────────────────────────────
#
# Everything below is gated on `_require_admin`, which reads `profiles.role`
# -- the same column as every other role gate, and never `user_metadata.role`,
# which the client can rewrite through `supabase.auth.updateUser` without
# this backend seeing it.
#
# `admin` is a role rather than a side table because the column is
# server-controlled on both edges: the client cannot write it, and sign-up
# cannot request it. It's set from the dashboard SQL editor, like
# `retention_window`'s row.


def _is_admin(user_id: str) -> bool:
    """Whether this user is a platform administrator.

    One source of truth: `profiles.role`, the same column every other role
    gate reads through `_role`. A separate membership table would give two
    answers to one question if the two ever disagreed.

    Safe as a role only because UPDATE/INSERT on the column is revoked from
    client roles, and `handle_new_user` refuses 'admin' from the sign-up
    form. Without both, this would read a value the caller chose.

    Fails **closed** through `_role`, which degrades to 'student' on a
    failed read -- this gates the switch that decides whether consent is
    enforced, so an unreadable profile must not admit anyone.
    """
    return _role(user_id) == ADMIN_ROLE


def _require_admin(request: Request) -> dict:
    """The caller, if they are an admin. 401 without a token, 403 without a row.

    Returns the user so callers don't resolve it twice -- the id is needed
    for `updated_by`/`changed_by` on every write below.
    """
    user = get_user(request)
    if not _is_admin(user["id"]):
        raise HTTPException(403, "Admin access required")
    return user


class FeatureFlagUpdate(BaseModel):
    enabled: bool
    # Only read when disabling `consent_enforcement_enabled`. Bounded, since
    # the point of the bypass is that it can't be left on -- a window
    # measured in days is indistinguishable from leaving it on.
    bypass_minutes: int | None = None


# Longest the bypass may be set for in one go. Re-arming is a deliberate act
# that lands in the audit log -- a long window produces one log line and a
# week of unenforced consent; a short one produces a line every time someone
# chooses to continue.
_MAX_BYPASS_MINUTES = 240


@app.get("/api/admin/me")
def admin_me(request: Request):
    """200 for an admin, 403 for anyone else. What the frontend guard calls."""
    user = _require_admin(request)
    return {"user_id": user["id"], "is_admin": True}


def _flag_rows() -> list:
    """Every known flag as a response row, in the declared order.

    Ordered from `_FEATURE_FLAG_DEFAULTS` rather than from the table so the
    dashboard's rows do not reorder themselves when a flag is written, and so a
    key the table is missing still appears -- with its default, which is the
    value the backend is actually using for it.
    """
    flags = _feature_flags()
    try:
        rows = {r["key"]: r for r in
                (supabase.table("feature_flags").select("*").execute().data or [])
                if r.get("key")}
    except Exception as e:
        print(f"[admin:flags] {e}")
        rows = {}

    out = []
    for key in _FEATURE_FLAG_DEFAULTS:
        row = rows.get(key, {})
        out.append({
            "key": key,
            "enabled": flags[key]["enabled"],
            "bypass_until": flags[key]["bypass_until"],
            "description": row.get("description"),
            "updated_at": row.get("updated_at"),
            # True when the row is missing and the default is answering --
            # otherwise a dashboard would show a flag as set by someone when
            # nobody has ever set it.
            "is_default": key not in rows,
        })
    return out


@app.get("/api/admin/flags")
def admin_flags(request: Request):
    _require_admin(request)
    return {"flags": _flag_rows(),
            "consent_enforcement_active": _consent_enforcement_active()}


@app.put("/api/admin/flags/{key}")
def admin_set_flag(key: str, request: Request, payload: FeatureFlagUpdate):
    """Set one flag, audit the change, and drop the cache.

    Refuses a key that isn't declared: `_feature_flags` ignores unknown
    rows, so writing one would create a switch that reads back as set and
    controls nothing.
    """
    user = _require_admin(request)
    if key not in _FEATURE_FLAG_DEFAULTS:
        raise HTTPException(404, f"Unknown flag {key!r}")

    bypass_until = None
    if key == CONSENT_ENFORCEMENT_FLAG and not payload.enabled:
        minutes = payload.bypass_minutes
        # Required rather than defaulted -- a default here would be this
        # file choosing how long consent goes unenforced, a decision that
        # should be made out loud every time.
        if minutes is None:
            raise HTTPException(
                422, "bypass_minutes is required when disabling consent enforcement")
        if minutes < 1 or minutes > _MAX_BYPASS_MINUTES:
            raise HTTPException(
                422, f"bypass_minutes must be between 1 and {_MAX_BYPASS_MINUTES}")
        bypass_until = (_utc_now() + timedelta(minutes=minutes)).isoformat()

    before = _feature_flags().get(key, {})
    row = {"key": key, "enabled": payload.enabled, "bypass_until": bypass_until,
           "updated_by": user["id"], "updated_at": _utc_now().isoformat()}
    try:
        supabase.table("feature_flags").upsert(row, on_conflict="key").execute()
    except Exception as e:
        print(f"[admin:set_flag] {e}")
        raise HTTPException(500, "Could not update the flag")

    # After the write, so the next read cannot be served the old value, and
    # before the audit insert, so a failing audit does not leave the cache
    # holding a value the table no longer has.
    _feature_flags_cache_clear()

    try:
        supabase.table("feature_flag_changes").insert({
            "key": key,
            "old_enabled": before.get("enabled"),
            "new_enabled": payload.enabled,
            "bypass_until": bypass_until,
            "changed_by": user["id"],
        }).execute()
    except Exception as e:
        # Never raises: the flag is already set, and turning a successful
        # change into a 500 would invite a retry that changes nothing and
        # audits nothing.
        print(f"[admin:audit] {key} change not recorded: {e}")

    return {"flags": _flag_rows(),
            "consent_enforcement_active": _consent_enforcement_active()}


@app.get("/api/admin/flags/{key}/history")
def admin_flag_history(key: str, request: Request, limit: int = 20):
    _require_admin(request)
    if key not in _FEATURE_FLAG_DEFAULTS:
        raise HTTPException(404, f"Unknown flag {key!r}")
    limit = max(1, min(limit, 100))
    try:
        rows = supabase.table("feature_flag_changes").select("*") \
            .eq("key", key).order("changed_at", desc=True) \
            .limit(limit).execute().data or []
    except Exception as e:
        print(f"[admin:history] {e}")
        # Degrades rather than raising, like the reporting helpers -- and
        # says so, since an empty history and an unreadable one are
        # different claims about this flag.
        return {"key": key, "changes": [], "retrieved": False}

    names = _display_names({r.get("changed_by") for r in rows if r.get("changed_by")})
    return {"key": key, "retrieved": True, "changes": [{
        "changed_at": r.get("changed_at"),
        "old_enabled": r.get("old_enabled"),
        "new_enabled": r.get("new_enabled"),
        "bypass_until": r.get("bypass_until"),
        "changed_by": names.get(r.get("changed_by"), "Unknown"),
    } for r in rows]}


def _display_names(user_ids) -> dict:
    """Names for a set of ids, for the audit view. Never raises."""
    ids = [u for u in user_ids if u]
    if not ids:
        return {}
    try:
        rows = supabase.table("profiles").select("id, display_name") \
            .in_("id", ids).execute().data or []
        return {r["id"]: r.get("display_name") or "Unknown" for r in rows}
    except Exception as e:
        print(f"[admin:names] {e}")
        return {}


# The env-var flags this dashboard can show but not change. Named here
# rather than discovered: `os.environ` also holds the service-role key, and
# enumerating the environment would eventually render a secret.
_DEPLOYMENT_FLAGS = (
    ("INGEST_MODE", "pull", "Whether the backend polls the sidecar, or the sidecar posts here."),
    ("EEG_API_URL", None, "Where the EEG sidecar is expected, under pull ingestion."),
    ("FACE_ENABLED", None, "Camera capture, in the sidecar's own environment."),
    ("FACE_EMOTION_ENABLED", None, "FER+ emotion classification, in the sidecar."),
    ("FACE_GAZE_ENABLED", None, "Gaze and head pose, in the sidecar."),
    ("FACE_HEART_ENABLED", None, "Camera rPPG. Validated and rejected -- expected off."),
    ("MUSE_ENABLE_OPTICS", None, "The headband's optical channels, read by the native bridge."),
    ("MUSE_OPTICS_PRESET", None, "Which optics rung the bridge asks for."),
)


@app.get("/api/admin/env-flags")
def admin_env_flags(request: Request):
    """The process's env-var switches, read-only.

    Lets one screen answer "how is this deployment configured" instead of
    half of it living in a `.env` nobody can see from the browser. Every
    entry is marked `editable: false` in the payload, not left for the UI to
    remember.

    Several of these belong to the sidecar's environment, not this process,
    so a null here means "not set for the backend" -- a hint about the
    deployment, not an authority on what the sidecar is actually doing.
    """
    _require_admin(request)
    return {"flags": [{
        "key": key,
        "value": os.getenv(key, default),
        "description": description,
        "editable": False,
    } for key, default, description in _DEPLOYMENT_FLAGS]}


class RetentionWindowUpdate(BaseModel):
    enforced: bool
    starts_on: str | None = None
    ends_on: str | None = None
    timezone: str = "UTC"


@app.get("/api/admin/retention-window")
def admin_get_retention_window(request: Request):
    """The school year as configured, plus where today sits in it."""
    _require_admin(request)
    window = _retention_window()
    try:
        rows = supabase.table("retention_window").select("*").limit(1).execute().data or []
    except Exception as e:
        print(f"[admin:window] {e}")
        rows = []
    row = rows[0] if rows else {}
    return {"state": window["state"],
            "configured": bool(rows),
            "enforced": row.get("enforced"),
            "starts_on": row.get("starts_on"),
            "ends_on": row.get("ends_on"),
            "timezone": row.get("timezone") or "UTC"}


@app.put("/api/admin/retention-window")
def admin_set_retention_window(request: Request, payload: RetentionWindowUpdate):
    """Replace the school-year row. The SQL editor's job, with a form on it.

    Validates here rather than relying on the table's CHECKs, so a bad edit
    is a 422 naming the field instead of a 500 from the client library. The
    CHECKs stay as a second line of defence, since this isn't the only thing
    that can write that row.
    """
    user = _require_admin(request)

    try:
        ZoneInfo(payload.timezone)
    except Exception:
        # An unknown zone makes `_retention_window` deny all recording, and
        # this field is edited only twice a year -- a typo here is easy to miss.
        raise HTTPException(422, f"Unknown timezone {payload.timezone!r}")

    starts, ends = payload.starts_on, payload.ends_on
    if payload.enforced:
        if not starts or not ends:
            raise HTTPException(
                422, "starts_on and ends_on are required when the year is enforced")
        try:
            starts_d, ends_d = date.fromisoformat(starts), date.fromisoformat(ends)
        except ValueError:
            raise HTTPException(422, "starts_on and ends_on must be YYYY-MM-DD")
        if ends_d <= starts_d:
            raise HTTPException(422, "ends_on must be after starts_on")

    row = {"id": True, "enforced": payload.enforced,
           "starts_on": starts or None, "ends_on": ends or None,
           "timezone": payload.timezone,
           "updated_by": user["id"],
           "updated_at": _utc_now().isoformat()}
    try:
        supabase.table("retention_window").upsert(row, on_conflict="id").execute()
    except Exception as e:
        print(f"[admin:set_window] {e}")
        raise HTTPException(500, "Could not update the school year")

    _retention_cache_clear()
    return admin_get_retention_window(request)


# How recently a channel must have written to count as flowing, and how long
# before it counts as stale. Shared with `class_live`, the other page
# answering this question about the same sessions -- two sets of numbers
# would let one page call a session live while the other called it stale.
_LIVE_WINDOW_SEC = 90
_STALE_AFTER_SEC = 600

# This endpoint is platform-wide where `class_live` is one class, and the
# dashboard polls it every few seconds. A school doesn't have 200
# simultaneous sessions, so reaching this cap means something is wrong,
# hence the payload reports it rather than quietly truncating.
_ADMIN_LIVE_SESSION_CAP = 200

# **Nothing submitted here may wait on anything else in here.** Reads are
# submitted flat and gathered afterwards -- a task that blocks on another
# task in its own pool puts the waiter and the work it waits for in the same
# fixed queue, which deadlocks rather than slows down.
_ADMIN_LIVE_POOL: ThreadPoolExecutor | None = None
_admin_live_pool_lock = threading.Lock()


def _admin_live_pool() -> ThreadPoolExecutor:
    global _ADMIN_LIVE_POOL
    with _admin_live_pool_lock:
        if _ADMIN_LIVE_POOL is None:
            _ADMIN_LIVE_POOL = ThreadPoolExecutor(max_workers=8,
                                                  thread_name_prefix="admin-live")
        return _ADMIN_LIVE_POOL


def _shutdown_admin_live_pool():
    """Drop the queue on the way out. Same shape as `_shutdown_strategy_pool`
    -- see it for why wait=False/cancel_futures=True -- called from
    `_lifespan` alongside it."""
    global _ADMIN_LIVE_POOL
    with _admin_live_pool_lock:
        pool, _ADMIN_LIVE_POOL = _ADMIN_LIVE_POOL, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


# What a channel read answers with when the query itself failed. Distinct
# from `None`, which means "nothing has ever arrived" -- reporting an
# unreadable channel as never-reported would be the same absence-as-data
# failure one layer further in.
_TS_UNREADABLE = object()


def _latest_signal_ts(session_ids: list[str]) -> dict:
    """Newest timestamp per session per channel, and nothing else.

    `{session_id: {"eeg": ts|None|_TS_UNREADABLE, "camera": ...}}`.

    **Selects `ts` alone**, so the readings never leave the database rather
    than being fetched and dropped on the way out. `class_live` needs the
    rows themselves; this endpoint needs only whether something arrived, and
    asking for less is a stronger privacy property than filtering afterward.

    Two channels, not four: `_latest_signals_many` also reads `heart_signals`
    and `session_answers`, which this endpoint discarded.

    Every read is submitted before any is waited on, so the pool runs them
    concurrently across sessions as well as across channels.
    """
    pool = _admin_live_pool()

    def _newest(table: str, session_id: str):
        try:
            rows = supabase.table(table).select("ts") \
                .eq("session_id", session_id) \
                .order("ts", desc=True).limit(1).execute().data or []
            return rows[0]["ts"] if rows else None
        except Exception as e:
            print(f"[admin:live:{table}] {session_id}: {e}")
            return _TS_UNREADABLE

    channels = (("eeg", "cognitive_signals"), ("camera", "face_signals"))
    futures = {(sid, name): pool.submit(_newest, table, sid)
               for sid in session_ids
               for name, table in channels}

    out = {sid: {} for sid in session_ids}
    for (sid, name), future in futures.items():
        try:
            out[sid][name] = future.result()
        except Exception as e:
            # `_newest` catches its own, so this is the pool itself failing --
            # a rejected submission on shutdown, say.
            print(f"[admin:live:{name}] {sid}: {e}")
            out[sid][name] = _TS_UNREADABLE
    return out


@app.get("/api/admin/live-signals")
def admin_live_signals(request: Request):
    """Whether signals are *arriving* for each open session. Not what they say.

    The content is dropped here, in the endpoint, not left for the frontend
    not to render: this answers "is the data flowing", and an admin has no
    relationship to these students entitling them to the readings. Only the
    newest timestamp per channel survives -- no band powers, no emotion
    label, no bpm.

    Same thresholds and per-session helper as `class_live`, the other page
    answering this question -- a second set of numbers would make one of the
    two pages wrong about the same session.
    """
    _require_admin(request)

    now = _utc_now()
    live_cutoff = now - timedelta(seconds=_LIVE_WINDOW_SEC)
    stale_cutoff = now - timedelta(seconds=_STALE_AFTER_SEC)

    try:
        sessions = supabase.table("sessions").select("id, user_id, started_at") \
            .is_("ended_at", "null").order("started_at", desc=True) \
            .limit(_ADMIN_LIVE_SESSION_CAP).execute().data or []
    except Exception as e:
        print(f"[admin:live] {e}")
        return {"sessions": [], "retrieved": False}

    names = _display_names({s.get("user_id") for s in sessions})
    stamps = _latest_signal_ts([s["id"] for s in sessions])

    def _channel(raw):
        """A channel's liveness, from its newest timestamp alone.

        Three sources of "not flowing" are kept apart, since a reader acts
        differently on each: a sensor that stopped, a session that never had
        one, and a read that failed.
        """
        if raw is _TS_UNREADABLE:
            return {"flowing": False, "stale": False, "seen": None}
        ts = _parse_ts(raw)
        if ts is None:
            return {"flowing": False, "stale": False, "seen": False}
        return {"flowing": ts >= live_cutoff,
                "stale": ts < stale_cutoff,
                "seen": True,
                # The one value that leaves this endpoint -- lets the
                # dashboard pulse on a *new* sample rather than every poll,
                # and says nothing about the reading itself.
                "last_ts": raw}

    out = []
    for s in sessions:
        seen = stamps.get(s["id"], {})
        out.append({
            "session_id": s["id"],
            "student_id": s.get("user_id"),
            "student_name": names.get(s.get("user_id"), "Student"),
            "started_at": s.get("started_at"),
            "eeg": _channel(seen.get("eeg")),
            "camera": _channel(seen.get("camera")),
        })
    return {"sessions": out, "retrieved": True,
            "capped": len(sessions) >= _ADMIN_LIVE_SESSION_CAP}


@app.get("/api/admin/health")
def admin_health(request: Request):
    """One place to see whether the moving parts are moving.

    Three states per check -- `ok`, `degraded`, `unknown` -- and a failed
    read is `unknown`, never `ok`: a check that couldn't run hasn't earned
    the right to say everything is fine.
    """
    _require_admin(request)

    checks = []

    mode = eeg_poller.INGEST_MODE
    if mode == "push":
        # Not a fault. Under push the sidecar is on a student's laptop with
        # no route from here, so "unreachable" would be true and misleading.
        checks.append({"key": "eeg_sidecar", "status": "unknown",
                       "detail": "Not probed: this deployment uses push ingestion."})
    else:
        try:
            alive = eeg_client.is_alive()
            checks.append({"key": "eeg_sidecar",
                           "status": "ok" if alive else "degraded",
                           "detail": eeg_client.EEG_API_URL if alive
                                     else f"Not answering on {eeg_client.EEG_API_URL}"})
        except Exception as e:
            checks.append({"key": "eeg_sidecar", "status": "unknown",
                           "detail": f"Could not probe: {e}"})

    checks.append({"key": "ingest_mode", "status": "ok", "detail": mode})

    window = _retention_window()
    meaning = _WINDOW_STATES.get(window["state"])
    checks.append({
        "key": "school_year",
        "status": "ok" if meaning and meaning.records else "degraded",
        "detail": window["state"],
    })

    # Newest rollup row, as a proxy for "the summary writer is running". Not
    # a scheduler status -- an old one just means nobody closed a session
    # recently, which is not a fault on a quiet day. Reported as a date for
    # a person to judge, not as a verdict.
    try:
        rows = supabase.table("signal_daily_rollup").select("day") \
            .order("day", desc=True).limit(1).execute().data or []
        checks.append({"key": "last_rollup", "status": "ok" if rows else "degraded",
                       "detail": rows[0]["day"] if rows else "No rollup rows yet"})
    except Exception as e:
        print(f"[admin:health:rollup] {e}")
        checks.append({"key": "last_rollup", "status": "unknown",
                       "detail": "Could not read the rollup table"})

    # One call, not one per field -- two calls could straddle the cache
    # expiry and report a status and a detail that disagree.
    enforced = _consent_enforcement_active()
    checks.append({
        "key": "consent_enforcement",
        "status": "ok" if enforced else "degraded",
        "detail": "Enforced" if enforced
                  else "BYPASSED -- recording without consent",
    })

    return {"checks": checks}


@app.get("/api/admin/consent-summary")
def admin_consent_summary(request: Request):
    """Counts only. How many students, how many have said yes to each channel.

    Deliberately aggregate: an admin needs to know whether the consent flow
    is working, which is a number, not a list of children and what they
    agreed to.
    """
    _require_admin(request)
    try:
        students = supabase.table("profiles").select("id") \
            .eq("role", "student").execute().data or []
        consents = supabase.table("signal_consent").select("*").execute().data or []
    except Exception as e:
        print(f"[admin:consent_summary] {e}")
        return {"retrieved": False}

    def _n(channel):
        return sum(1 for c in consents if c.get(f"{channel}_enabled"))

    return {
        "retrieved": True,
        "students": len(students),
        "with_any_consent_row": len(consents),
        "eeg": _n("eeg"),
        "headband_optical": _n("headband_optical"),
        "camera": _n("camera"),
        # A parent re-enabled a channel and the student hasn't acknowledged
        # it yet -- the one consent state that needs someone to act.
        "awaiting_student_ack": sum(
            1 for c in consents
            if c.get("parent_enabled_at") and not c.get("student_ack_at")),
    }


@app.get("/api/admin/students/search")
def admin_student_search(request: Request, q: str = "", limit: int = 10):
    """Find a student by name or email, to jump to their existing report.

    Returns identifiers and nothing else -- the report itself is served by
    the endpoints that already exist, behind the relationship check that
    admits admins.
    """
    _require_admin(request)
    term = (q or "").strip()
    if len(term) < 2:
        # Not an error: an empty box is the normal state of a search field.
        # A one-character term would return most of the school.
        return {"students": [], "query": term}
    limit = max(1, min(limit, 25))

    # PostgREST `or` with `ilike`. Escaped for the filter's own syntax: a
    # comma or parenthesis would otherwise be read as structure, not text.
    safe = term.replace("\\", "\\\\").replace("%", "\\%").replace(",", "").replace("(", "").replace(")", "")
    try:
        rows = supabase.table("profiles") \
            .select("id, display_name, email, grade_level") \
            .eq("role", "student") \
            .or_(f"display_name.ilike.%{safe}%,email.ilike.%{safe}%") \
            .limit(limit).execute().data or []
    except Exception as e:
        print(f"[admin:search] {e}")
        return {"students": [], "query": term, "retrieved": False}

    return {"query": term, "retrieved": True, "students": [{
        "id": r["id"],
        "display_name": r.get("display_name") or "Student",
        "email": r.get("email") or "",
        "grade_level": r.get("grade_level"),
    } for r in rows]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=BACKEND_PORT, reload=True)