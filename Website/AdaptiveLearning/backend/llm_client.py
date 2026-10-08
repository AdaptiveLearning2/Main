# One dispatch point for every model call the backend makes: the 10
# `LLM_*_generation.py` files, `LLM_topic_decider.py` and `main.py:_llm_strategies`.

import os
import threading
import time

from dotenv import load_dotenv

import console_encoding
import ops_metrics
from env_config import env_number

load_dotenv()

# Here because every generator imports this module.
console_encoding.make_console_safe()


# Defaults to ollama so a fresh checkout never bills an Anthropic account.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()

# Undated alias: a mistyped or aged-out dated snapshot is a 404 on every call.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")

# Anthropic accepts 0.0-1.0 (call sites pass Ollama's 1.1, which would 400), so clamped.
CLAUDE_TEMPERATURE = min(1.0, env_number("CLAUDE_TEMPERATURE", 1.0, float, minimum=0.0))

# Seconds per model call, including queueing; the SDK's own 10-minute default would stall prefetch.
GENERATION_LLM_TIMEOUT = env_number("GENERATION_LLM_TIMEOUT", 30.0, float, minimum=1.0)

# Process-wide in-flight model calls, both providers; `main._ensure_queue` bounds only per session.
GENERATION_MAX_CONCURRENCY = env_number("GENERATION_MAX_CONCURRENCY", 8, int, minimum=1)
_generation_slots = threading.BoundedSemaphore(GENERATION_MAX_CONCURRENCY)

# Billable Claude calls per rolling 24h (a question is 2 calls; a class of 30 x 30 questions ~ 1800).
# Counts calls, not tokens; in-memory and per worker, so a restart resets it.
GENERATION_DAILY_CALL_LIMIT = env_number("GENERATION_DAILY_CALL_LIMIT", 2500, int, minimum=1)
_call_times: list[float] = []
_call_lock = threading.Lock()

# 0: call sites already retry 3x and can also reject bad JSON; SDK retries would multiply billing.
CLAUDE_MAX_RETRIES = env_number("CLAUDE_MAX_RETRIES", 0, int, minimum=0)


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
    `reason` is one of `concurrency`, `queued`, `daily`, `connection` (the admin counters' key).
    """

    def __init__(self, message: str, reason: str = "unavailable"):
        super().__init__(message)
        self.reason = reason


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
                f"({GENERATION_DAILY_CALL_LIMIT} in 24h)", reason="daily"
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
    provider = LLM_PROVIDER if LLM_PROVIDER == "claude" else "ollama"
    started = time.monotonic()
    try:
        text, tokens_in, tokens_out = _generate(
            prompt, temperature=temperature, top_p=top_p, top_k=top_k,
            ollama_model=ollama_model, claude_temperature=claude_temperature,
            schema=schema, max_tokens=max_tokens, timeout=timeout)
    except GenerationUnavailable as e:
        ops_metrics.bump("llm_call", f"{provider}:unavailable:{e.reason}")
        raise
    except Exception as e:
        ops_metrics.bump("llm_call", f"{provider}:error:{type(e).__name__}")
        raise
    finally:
        # Includes waiting for a slot: the wait a student feels, not the provider's alone.
        ops_metrics.observe("llm_latency_ms", provider, (time.monotonic() - started) * 1000)
    ops_metrics.bump("llm_call", f"{provider}:ok")
    ops_metrics.bump("llm_tokens", f"{provider}:in", tokens_in)
    ops_metrics.bump("llm_tokens", f"{provider}:out", tokens_out)
    return text


def _usage(resp, *names) -> int:
    """The first token count `resp` carries under `names` (mapping or object), else 0."""
    for name in names:
        value = resp.get(name) if isinstance(resp, dict) else getattr(resp, name, None)
        if isinstance(value, int):
            return value
    return 0


def _generate(prompt, *, temperature, top_p, top_k, ollama_model, claude_temperature,
              schema, max_tokens, timeout):
    """`generate_text`'s call, returning (text, input tokens, output tokens)."""
    budget = GENERATION_LLM_TIMEOUT if timeout is None else timeout

    queued_at = time.monotonic()
    if not _generation_slots.acquire(timeout=budget):
        raise GenerationUnavailable(
            f"no model slot free within {budget}s "
            f"({GENERATION_MAX_CONCURRENCY} concurrent)", reason="concurrency"
        )
    try:
        # What is left of the budget after queueing, so one call cannot block for ~2x `budget`.
        remaining = budget - (time.monotonic() - queued_at)
        if remaining <= 0:
            raise GenerationUnavailable(
                f"budget of {budget}s spent waiting for a model slot", reason="queued")
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
                    f"{type(e).__name__}", reason="connection") from e
            usage = getattr(resp, "usage", None)
            return (next((b.text for b in resp.content if b.type == "text"), ""),
                    _usage(usage, "input_tokens"), _usage(usage, "output_tokens"))

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
        text = (resp.get("response") if isinstance(resp, dict)
                else getattr(resp, "response", "")) or ""
        return text, _usage(resp, "prompt_eval_count"), _usage(resp, "eval_count")
    finally:
        _generation_slots.release()
