# One dispatch point for every model call the backend makes: the 10
# `LLM_*_generation.py` files, `LLM_topic_decider.py` and `main.py:_llm_strategies`.

import math
import os
import threading
import time

from dotenv import load_dotenv

import console_encoding

load_dotenv()

# Here because every generator imports this module.
console_encoding.make_console_safe()


def _env_number(name, default, cast, minimum=None):
    """Read a numeric setting, falling back on a bad value.

    A copy of `main.py:_env_number`: `main` imports this module, so importing back would cycle.
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


# Defaults to ollama so a fresh checkout never bills an Anthropic account.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()

# Undated alias: a mistyped or aged-out dated snapshot is a 404 on every call.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")

# Anthropic accepts 0.0-1.0 (call sites pass Ollama's 1.1, which would 400), so clamped.
CLAUDE_TEMPERATURE = min(1.0, _env_number("CLAUDE_TEMPERATURE", 1.0, float, minimum=0.0))

# Seconds per model call, including queueing; the SDK's own 10-minute default would stall prefetch.
GENERATION_LLM_TIMEOUT = _env_number("GENERATION_LLM_TIMEOUT", 30.0, float, minimum=1.0)

# Process-wide in-flight model calls, both providers; `_prefetch_active` bounds only per user.
GENERATION_MAX_CONCURRENCY = _env_number("GENERATION_MAX_CONCURRENCY", 8, int, minimum=1)
_generation_slots = threading.BoundedSemaphore(GENERATION_MAX_CONCURRENCY)

# Billable Claude calls per rolling 24h (a question is 2 calls; a class of 30 x 30 questions ~ 1800).
# Counts calls, not tokens; in-memory and per worker, so a restart resets it.
GENERATION_DAILY_CALL_LIMIT = _env_number("GENERATION_DAILY_CALL_LIMIT", 2500, int, minimum=1)
_call_times: list[float] = []
_call_lock = threading.Lock()

# 0: call sites already retry 3x and can also reject bad JSON; SDK retries would multiply billing.
CLAUDE_MAX_RETRIES = _env_number("CLAUDE_MAX_RETRIES", 0, int, minimum=0)


# Temperature the Messages API applies when none is sent.
_API_DEFAULT_TEMPERATURE = 1.0


def _claude_sampling(claude_temperature):
    """The sampling kwargs for one Claude call -- usually none at all.

    anthropic 1.x `messages.create` has no `temperature` parameter (TypeError), so a
    non-default one goes through `extra_body`. That path is unverified against a billed call.
    """
    temp = CLAUDE_TEMPERATURE if claude_temperature is None else claude_temperature
    temp = min(1.0, max(0.0, temp))
    # Compare against the API default, not CLAUDE_TEMPERATURE, or a matching request sends nothing.
    if temp == _API_DEFAULT_TEMPERATURE:
        return {}
    return {"extra_body": {"temperature": temp}}


class GenerationUnavailable(RuntimeError):
    """A bound refused this call (daily ceiling or concurrency slot), or the API was unreachable.

    `/api/questions/generate` answers 503. Never degrade by silently serving from elsewhere.
    """


_anthropic_client = None
_client_lock = threading.Lock()


def _get_anthropic_client():
    """The Anthropic client, built lazily so a machine without `ANTHROPIC_API_KEY` still imports."""
    global _anthropic_client
    with _client_lock:
        if _anthropic_client is None:
            import anthropic
            _anthropic_client = anthropic.Anthropic(max_retries=CLAUDE_MAX_RETRIES)
        return _anthropic_client


def _claim_call_slot():
    """Count one billable call against the rolling 24h ceiling, or refuse."""
    now = time.monotonic()
    window = 24 * 60 * 60
    with _call_lock:
        _call_times[:] = [t for t in _call_times if now - t < window]
        if len(_call_times) >= GENERATION_DAILY_CALL_LIMIT:
            raise GenerationUnavailable(
                f"daily model-call ceiling reached "
                f"({GENERATION_DAILY_CALL_LIMIT} in 24h)"
            )
        _call_times.append(now)


def _calls_in_window() -> int:
    """Billable calls counted in the last 24h. For tests and diagnostics."""
    now = time.monotonic()
    with _call_lock:
        return len([t for t in _call_times if now - t < 24 * 60 * 60])


def generate_text(prompt: str, *, temperature: float = 1.1,
                  top_p: float | None = 0.95, top_k: int | None = 100, ollama_model: str = "llama3.1:8b",
                  claude_temperature: float | None = None, schema: dict | None = None,
                  max_tokens: int = 2048, timeout: float | None = None) -> str:
    """One model call, against whichever provider is configured.

    `temperature`/`top_p`/`top_k`/`ollama_model` are Ollama-only; `claude_temperature` and
    `schema` are Claude-only (Ollama runs unschema'd, so downstream JSON checks stay load-bearing).
    Raises `GenerationUnavailable` for a refused bound or an unreachable API; everything else propagates.
    """
    budget = GENERATION_LLM_TIMEOUT if timeout is None else timeout

    queued_at = time.monotonic()
    if not _generation_slots.acquire(timeout=budget):
        raise GenerationUnavailable(
            f"no model slot free within {budget}s "
            f"({GENERATION_MAX_CONCURRENCY} concurrent)"
        )
    try:
        # What is left of the budget after queueing, so one call cannot block for ~2x `budget`.
        remaining = budget - (time.monotonic() - queued_at)
        if remaining <= 0:
            raise GenerationUnavailable(
                f"budget of {budget}s spent waiting for a model slot")
        if LLM_PROVIDER == "claude":
            _claim_call_slot()
            client = _get_anthropic_client().with_options(timeout=remaining)
            import anthropic
            # No schema sends no `output_config` at all, not an empty constraint.
            structured = ({"output_config":
                           {"format": {"type": "json_schema", "schema": schema}}}
                          if schema is not None else {})
            try:
                resp = client.messages.create(
                    model=CLAUDE_MODEL,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                    **_claude_sampling(claude_temperature),
                    **structured,
                )
            except anthropic.APIConnectionError as e:
                # Covers APITimeoutError too; AuthenticationError is deliberately not caught, a bad key stays loud.
                # The base URL is logged because a stale `ANTHROPIC_BASE_URL` is the usual cause; the key is not.
                raise GenerationUnavailable(
                    f"cannot reach the model API at {client.base_url} -- "
                    f"{type(e).__name__}") from e
            return next((b.text for b in resp.content if b.type == "text"), "")

        # An explicit Client, because module-level `generate()` has no deadline.
        from ollama import Client
        # None is dropped rather than sent, so callers setting only temperature are unchanged.
        options = {"temperature": temperature, "top_p": top_p, "top_k": top_k}
        resp = Client(timeout=remaining).generate(
            model=ollama_model,
            prompt=prompt,
            options={k: v for k, v in options.items() if v is not None},
        )
        # A mapping on some client versions, an object on others.
        if isinstance(resp, dict):
            return resp.get("response") or ""
        return getattr(resp, "response", "") or ""
    finally:
        _generation_slots.release()
