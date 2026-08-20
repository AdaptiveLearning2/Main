import os
import threading
import time

from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# Same TTL and the same reasoning as main.py's _FEATURE_FLAGS_TTL_SECONDS:
# this is reference content edited via the dashboard, not a per-student
# decision, so a bounded staleness after an edit costs nothing that isn't
# corrected within the TTL.
_CACHE_TTL_SECONDS = 30.0

# Dashboard-authored text still lands in an LLM prompt, so it gets the same
# treatment as every other prompt input in this codebase: bounded rather than
# trusted to be well-behaved, even though only the dashboard/SQL editor can
# write it (same trust level as math_topics/questions).
_MAX_CONTEXT_CHARS = 2000

# Why a cell did or didn't contribute prompt text. Only FOUND carries text;
# the rest are four operationally distinct ways of getting None -- see
# _lookup's docstring for why they are kept apart rather than collapsed.
FOUND          = "found"
NO_ROW         = "no_row"          # unseeded: a content gap to write
BLANK_ROW      = "blank_row"       # seeded but empty: a half-finished edit
READ_FAILED    = "read_failed"     # an outage; the row may well exist
NO_CREDENTIALS = "no_credentials"  # misconfigured process, never asked

_client: Client | None = None
_client_lock = threading.Lock()

_cache = {}  # (topic_name, grade_band) -> (expires_at, str | None, reason)
_cache_lock = threading.Lock()


def _get_client():
    """Lazy, so a missing SUPABASE_* env var surfaces at the first real
    lookup rather than at import time. Creating this eagerly would run ahead
    of main.py's own RuntimeError for the same variables, since main.py
    imports this module (via LLM_topic_decider) before it reaches its own
    check -- a missing env var must be a clear error from main.py, not a
    bare KeyError from three imports away.
    """
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            url = os.environ.get("SUPABASE_URL")
            key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
            if not url or not key:
                return None
            _client = create_client(url, key)
    return _client


def _lookup(topic_name, grade_band):
    """(text, reason) for one cell. `text` is None unless reason == FOUND.

    The reason exists because the four ways of getting None here are
    operationally different: an unseeded cell needs content written, a
    blank row is a half-finished edit, a failed read is an outage, absent
    credentials is a misconfigured process. All four degrade to the same
    prompt, so nothing downstream branches on it -- but the log line does.
    """
    key = (topic_name, grade_band)
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
    if cached and now < cached[0]:
        return cached[1], cached[2]

    client = _get_client()
    if client is None:
        # Not cached: credentials can appear later in a process's life, and
        # caching their absence would outlive that. Logged every call, not
        # once, since a single startup line is easy to miss.
        print(f"[lesson_plan_context] {key}: no Supabase credentials, "
              f"generating without lesson-plan grounding")
        return None, NO_CREDENTIALS

    try:
        resp = (
            client.table("lesson_plans")
            .select("objectives,notes")
            .eq("topic_name", topic_name)
            .eq("grade_band", grade_band)
            .limit(1)
            .execute()
        )
    except Exception as e:
        print(f"[lesson_plan_context] {key}: READ FAILED ({e}) -- this is an "
              f"outage, not an unseeded cell; the row may well exist")
        # Not cached: a transient blip shouldn't keep answering None for the
        # TTL once the database is back.
        return None, READ_FAILED

    text, reason = None, NO_ROW
    if resp.data:
        row = resp.data[0]
        # A row with blank objectives produces the same absent prompt text
        # as no row, but it's a different fact, so it gets its own reason.
        text = row["objectives"] or None
        reason = FOUND if text else BLANK_ROW
        if text and row.get("notes"):
            text += "\n" + row["notes"]
        if text:
            text = text[:_MAX_CONTEXT_CHARS]

    if reason == NO_ROW:
        print(f"[lesson_plan_context] {key}: no row -- cell is unseeded, "
              f"falling back to the difficulty/grade heuristics")
    elif reason == BLANK_ROW:
        print(f"[lesson_plan_context] {key}: row exists but objectives are "
              f"blank -- a half-finished edit, not an unseeded cell")

    with _cache_lock:
        _cache[key] = (now + _CACHE_TTL_SECONDS, text, reason)
    return text, reason


def get_lesson_context(topic_name, grade_band):
    """Curriculum text for a topic/grade_band, or None if there is nothing
    to add -- no row on file, a blank row, an unreadable table, or missing
    Supabase credentials all collapse to the same None. This is prompt
    grounding, not a consent or access gate, so every failure mode fails
    open to "generate without lesson-plan context". The four cases are told
    apart in the **log**, not the return value, since nothing in generation
    branches on the difference.

    grade_band must be one of the bands LLM_*_generation.py already derives
    via _grade_band() ("early"/"middle"/"upper"/"advanced") -- lesson plans
    are stored at that granularity, not per exact grade.
    """
    return _lookup(topic_name, grade_band)[0]


def append_lesson_context(prompt, topic_name, grade_band):
    """One-line call site for the LLM_*_generation.py modules: appends
    lesson-plan grounding for (topic_name, grade_band) to prompt if there is
    any, otherwise returns prompt unchanged."""
    context = get_lesson_context(topic_name, grade_band)
    if context:
        prompt += f"\nLESSON PLAN CONTEXT -- ground the question in this: {context}\n"
    return prompt
