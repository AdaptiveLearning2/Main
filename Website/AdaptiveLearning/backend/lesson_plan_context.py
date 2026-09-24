import os
import threading
import time

from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# Same as main.py's _FEATURE_FLAGS_TTL_SECONDS; dashboard-edited reference content.
_CACHE_TTL_SECONDS = 30.0

# Dashboard text still lands in an LLM prompt, so it is bounded like any prompt input.
_MAX_CONTEXT_CHARS = 2000

# Only FOUND carries text; the other four are distinct ways of getting None.
FOUND         = "found"
NO_ROW         = "no_row"          # unseeded: a content gap to write
BLANK_ROW      = "blank_row"       # seeded but empty: a half-finished edit
READ_FAILED    = "read_failed"     # an outage; the row may well exist
NO_CREDENTIALS = "no_credentials"  # misconfigured process, never asked

_client: Client | None = None
_client_lock = threading.Lock()

_cache = {}  # (topic_name, grade_band) -> (expires_at, str | None, reason)
_cache_lock = threading.Lock()


def _get_client():
    """Lazy, so main.py's own missing-env RuntimeError fires first; None without credentials."""
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

    All None reasons degrade to the same prompt; only the log line tells them apart.
    """
    key = (topic_name, grade_band)
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
    if cached and now < cached[0]:
        return cached[1], cached[2]

    client = _get_client()
    if client is None:
        # Not cached (credentials can appear later); logged every call.
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
        # Not cached: a transient blip must not outlive the outage.
        return None, READ_FAILED

    text, reason = None, NO_ROW
    if resp.data:
        row = resp.data[0]
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
    """Curriculum text for a topic/grade_band, or None; every failure fails open.

    grade_band is a `_grade_band()` value ("early"/"middle"/"upper"/"advanced").
    """
    return _lookup(topic_name, grade_band)[0]


def append_lesson_context(prompt, topic_name, grade_band):
    """`prompt` with lesson-plan grounding appended, if there is any."""
    context = get_lesson_context(topic_name, grade_band)
    if context:
        prompt += f"\nLESSON PLAN CONTEXT -- ground the question in this: {context}\n"
    return prompt
