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

_client: Client | None = None
_client_lock = threading.Lock()

_cache = {}  # (topic_name, grade_band) -> (expires_at, str | None)
_cache_lock = threading.Lock()


def _get_client():
    """Lazy, so a missing SUPABASE_* env var surfaces at the first real
    lookup rather than at import time. Eager creation here ran ahead of
    main.py's own RuntimeError for the same variables: main.py imports
    LLM_topic_decider, which imports the ten LLM_*_generation modules, which
    import this one -- all before main.py reaches its own check. A missing
    env var must still be a clear RuntimeError from main.py, not a bare
    KeyError from three imports away.
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


def get_lesson_context(topic_name, grade_band):
    """Curriculum text for a topic/grade_band, or None if there is nothing
    to add -- no row on file, a blank row, an unreadable table, or missing
    Supabase credentials all collapse to the same None. This is prompt
    grounding, not a consent or access gate, so every failure mode fails
    open to "generate without lesson-plan context" rather than blocking
    generation.

    grade_band must be one of the bands LLM_*_generation.py already derives
    via _grade_band() ("early"/"middle"/"upper"/"advanced") -- lesson plans
    are stored at that granularity, not per exact grade.
    """
    key = (topic_name, grade_band)
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
    if cached and now < cached[0]:
        return cached[1]

    client = _get_client()
    if client is None:
        return None

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
        print(f"[lesson_plan_context] lookup failed for {key}: {e}")
        # Not cached -- same reasoning as main.py's _feature_flags(): a
        # transient blip should not keep answering None for the TTL once the
        # database is back.
        return None

    text = None
    if resp.data:
        row = resp.data[0]
        # A row with blank objectives has nothing to ground a question in,
        # so it is treated the same as no row -- this is about whether
        # there is content to append, not whether a row exists.
        text = row["objectives"] or None
        if text and row.get("notes"):
            text += "\n" + row["notes"]
        if text:
            text = text[:_MAX_CONTEXT_CHARS]

    with _cache_lock:
        _cache[key] = (now + _CACHE_TTL_SECONDS, text)
    return text


def append_lesson_context(prompt, topic_name, grade_band):
    """One-line call site for the LLM_*_generation.py modules: appends
    lesson-plan grounding for (topic_name, grade_band) to prompt if there is
    any, otherwise returns prompt unchanged."""
    context = get_lesson_context(topic_name, grade_band)
    if context:
        prompt += f"\nLESSON PLAN CONTEXT -- ground the question in this: {context}\n"
    return prompt
