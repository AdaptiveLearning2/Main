# One dispatch point for every model call the backend makes.
#
# Question generation reaches a model from 13 places -- the 10
# `LLM_*_generation.py` topic files and three in `LLM_topic_decider.py` -- and
# `main.py:_llm_strategies` is a 14th. Each of those called `ollama` directly,
# so switching provider meant 14 edits and the bounds below had nowhere to live
# at all.

import math
import os
import threading
import time

from dotenv import load_dotenv

load_dotenv()


def _env_number(name, default, cast, minimum=None):
    """Read a numeric setting, falling back on a bad value.

    A copy of `main.py:_env_number` rather than an import: `main` imports this
    module (via `LLM_topic_decider`), so importing back would be a cycle. Same
    contract -- see the docstring there for why non-finite values fall back
    rather than clamp.
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


# `ollama`, not `claude`, and deliberately so: a developer who checks the repo
# out and runs `start.ps1` must not start billing an Anthropic account. A
# deployment opts in explicitly.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()

# The undated alias. This was `claude-haiku-4-5-20251001` on the reasoning
# that pinning a snapshot protects the CLAUDE.md measurements from an alias
# moving under them -- sound in principle, and the wrong trade here, because
# the two IDs fail differently: the alias resolves whether or not a dated
# snapshot exists, while a wrong dated string is `404 not_found_error` on
# every question, on every topic, from the first call.
#
# The sources disagree and neither could be checked: Anthropic's published
# model table lists `claude-haiku-4-5` and says the IDs there are complete as
# given, while other references show a dated form for this model (plausible --
# Haiku 4.5 predates the 4.6+ generation that dropped date suffixes). The
# Models API settles it, and needs a key nobody has configured here yet.
#
# So: take the form that cannot be wrong, and pin the snapshot later if the
# measurements ever need it. `GET /v1/models` (or `client.models.list()`)
# under a real key is the check -- do that before pinning, not after.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5")

# Anthropic's `temperature` range is 0.0-1.0; Ollama's is not bounded there and
# every generation call site passes 1.1. Passed through, that returns
# `400 invalid_request_error` -- so the Claude branch takes its own value,
# floored at 0.0 and capped at 1.0, since a knob that 400s every call is worse
# than one that is merely mistuned.
#
# 1.0 is the top of the valid range, which is the nearest thing to the "keep it
# varied" intent behind 1.1.
CLAUDE_TEMPERATURE = min(1.0, _env_number("CLAUDE_TEMPERATURE", 1.0, float, minimum=0.0))

# Wall-clock budget for one model call. The Anthropic SDK's own default is 10
# minutes: a prefetch worker blocked that long never refills the queue, and the
# student waits on an inline generation instead.
GENERATION_LLM_TIMEOUT = _env_number("GENERATION_LLM_TIMEOUT", 30.0, float, minimum=1.0)

# How many model calls may be in flight process-wide.
#
# `_prefetch_active` in main.py bounds this per *user* (at QUEUE_SIZE, 2),
# which was the whole bound while the model was a local Ollama nobody paid per
# call. Thirty students starting sessions is sixty concurrent calls and sixty
# OS threads with nothing in between, and `start_session` prewarms before the
# first question is served -- so the peak was a function of how many children
# pressed start at once rather than of anything anyone chose.
#
# Bounds both providers, not just Claude: this is a resource ceiling rather
# than a spend one, and a local 8B model is the likelier of the two to thrash.
GENERATION_MAX_CONCURRENCY = _env_number("GENERATION_MAX_CONCURRENCY", 8, int, minimum=1)
_generation_slots = threading.BoundedSemaphore(GENERATION_MAX_CONCURRENCY)

# Ceiling on billable calls per rolling 24h, across the whole process.
#
# Scoped to the Claude branch on purpose: Ollama is local and free, so a call
# ceiling there would refuse a child a question to protect nothing.
#
# The default is sized off the product rather than picked round, and it is
# counted in **calls**, not questions: a question served is two of them, the
# topic-and-difficulty decision and the generation. A class of 30 answering 20
# questions in a day is ~600 questions and so ~1200 calls; at 30 questions it
# is ~1800. 2500 covers the second shape with room for retries, and stops well
# short of a second class -- which is the point.
#
# It was 5000, described as "eightfold headroom on ~600 generations": that
# compared a call ceiling against a question count and overstated the headroom
# by two. Corrected to 1500 against a 20-question day, which was then under the
# 1800 a 30-question day actually makes -- the ceiling has to be sized against
# the workload, not against the example that happened to be written down.
#
# What bounds one call is `max_tokens`, not this: 2048 output tokens at Haiku
# 4.5's $5/MTok is ~$0.0102, so the worst case here is ~$15/day where the
# ~$0.003-per-question average suggests ~$4.50. Size this against the worst
# case; the average is not what a runaway produces.
#
# Two things it does not bound, both worth knowing before trusting it. It
# counts calls rather than tokens, so seeding more lesson-plan text raises the
# bill without moving the ceiling. And it is in-process and in-memory: several
# uvicorn workers multiply it, and a restart resets the window, so a crash-loop
# defeats it entirely. Same tradeoff `_STRATEGY_RATE_LIMIT` documents -- the
# job is to bound a runaway, not to bill-count exactly.
GENERATION_DAILY_CALL_LIMIT = _env_number("GENERATION_DAILY_CALL_LIMIT", 2500, int, minimum=1)
_call_times: list[float] = []
_call_lock = threading.Lock()

# The SDK retries 429/5xx/connection errors twice by default, and every call
# site already sits inside its own `for attempt in range(3)` loop -- so one
# failing generation would be up to nine billed attempts. The loop the call
# sites own is the one to keep, because it can also reject a *well-formed*
# response for being bad JSON or the wrong shape, which no transport retry can.
CLAUDE_MAX_RETRIES = _env_number("CLAUDE_MAX_RETRIES", 0, int, minimum=0)


# What the Messages API uses when no `temperature` is sent. Named because
# `_claude_sampling` compares against it to decide whether to send anything,
# and comparing against the wrong constant silently disables the setting.
_API_DEFAULT_TEMPERATURE = 1.0


def _claude_sampling(claude_temperature):
    """The sampling kwargs for one Claude call -- usually none at all.

    `temperature` is NOT a parameter of `messages.create` in anthropic 1.x. It
    went with the 0.x -> 1.x major version, so passing it raises `TypeError:
    Messages.create() got an unexpected keyword argument 'temperature'` before
    a request is ever built -- on every question, from the first call. The plan
    for this migration corrected Ollama's 1.1 down into Anthropic's 0.0-1.0
    range, which was a real problem and not this one.

    Nothing caught it because the test double accepted `**kwargs`, so the suite
    pinned a request shape the SDK cannot accept. `_FakeMessages.create` now
    validates against the real signature.

    The default is to send no sampling parameter at all: the API's own default
    temperature is 1.0, which is exactly what `CLAUDE_TEMPERATURE` defaults to,
    so the hot path asks for nothing and cannot be refused for asking. A caller
    wanting something else -- `_llm_strategies` wants 0.4, because advice a
    parent reads is not the place for "keep it varied" -- gets it through
    `extra_body`, the escape hatch for a wire parameter the typed signature no
    longer carries.

    That `extra_body` path is UNVERIFIED: the account had no credits when this
    was written, so no billed call could confirm the wire still accepts
    `temperature` for this model. It degrades safely -- `_llm_strategies`
    catches everything and falls back to the rule-based list -- but if the
    strategies pass reports `source: "rule-based"` against a working key, this
    is the first thing to check.
    """
    temp = CLAUDE_TEMPERATURE if claude_temperature is None else claude_temperature
    temp = min(1.0, max(0.0, temp))
    # Against the API's default, which is what omitting the parameter selects
    # -- NOT against CLAUDE_TEMPERATURE. Comparing to the setting inverted the
    # feature: with CLAUDE_TEMPERATURE=0.4, `_llm_strategies` asking for 0.4
    # matched, so nothing was sent, so the call ran at the API default of 1.0
    # -- the one value that configuration existed to avoid. And generation
    # passes None, which returned early before the setting was ever read, so
    # CLAUDE_TEMPERATURE was inert on both paths while `.env.example`
    # documented it as the knob.
    if temp == _API_DEFAULT_TEMPERATURE:
        return {}
    return {"extra_body": {"temperature": temp}}


class GenerationUnavailable(RuntimeError):
    """A bound refused this call: the daily ceiling, or a concurrency slot.

    A distinct type because refusing is a *decision*. The alternative -- quietly
    serving a question from somewhere else, or from a cheaper model -- changes
    what a child is asked with nothing on any surface saying so, which is the
    same class of failure as a dashboard that cannot tell "no data" from "zero".
    Callers that can degrade (the prefetch worker, the strategies pass) already
    catch broadly; `/api/questions/generate` turns this into a 503 rather than a
    500, so a ceiling reads differently from a crash.
    """


_anthropic_client = None
_client_lock = threading.Lock()


def _get_anthropic_client():
    """The Anthropic client, built on first use.

    Lazy for the same reason `lesson_plan_context` builds its Supabase client
    lazily: with `LLM_PROVIDER` defaulting to ollama, a dev machine with no
    `ANTHROPIC_API_KEY` must not fail at import -- and this module is imported
    by every generation file, so an import-time failure here would take the
    whole app down over a provider nobody selected.
    """
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
                  claude_temperature: float | None = None,
                  max_tokens: int = 2048, timeout: float | None = None) -> str:
    """One model call, against whichever provider is configured.

    The sampling parameters do not carry across, so each provider takes its
    own. `temperature`/`top_p`/`top_k`/`ollama_model` are Ollama's and are
    ignored on the Claude branch; `claude_temperature` overrides
    `CLAUDE_TEMPERATURE` for a caller that wants something specific (the
    strategies pass wants 0.4, not "keep it varied"), and no `top_p`/`top_k` is
    sent at all, because on Claude 4.x and later at most one of
    `temperature`/`top_p` should be set.

    One signature rather than two functions is worth the comment: the 13
    generation call sites pass none of these, so the split would buy them
    nothing and cost every one of them a branch.

    Raises rather than returning "" when a bound refuses -- see
    `GenerationUnavailable`. Network and API errors are deliberately *not*
    caught here: the call sites do not catch them today either, and a swallow
    would turn a misconfigured API key into questions that quietly stop
    generating.
    """
    budget = GENERATION_LLM_TIMEOUT if timeout is None else timeout

    # Acquired with a deadline rather than blockingly: a caller that waits out
    # its whole budget for a slot has spent the student's time and still has no
    # question to show for it.
    queued_at = time.monotonic()
    if not _generation_slots.acquire(timeout=budget):
        raise GenerationUnavailable(
            f"no model slot free within {budget}s "
            f"({GENERATION_MAX_CONCURRENCY} concurrent)"
        )
    try:
        # What is LEFT of the budget, not the budget again. Charging the model
        # call the full amount after queueing for a slot lets one caller block
        # for nearly twice `budget` -- and `budget` is what the caller was told
        # it would wait. `_llm_strategies` had exactly this bug against its own
        # pool; this is the same fix one layer down, and it has to be here
        # rather than at the call site because the queueing happens here.
        remaining = budget - (time.monotonic() - queued_at)
        if remaining <= 0:
            raise GenerationUnavailable(
                f"budget of {budget}s spent waiting for a model slot")
        if LLM_PROVIDER == "claude":
            _claim_call_slot()
            client = _get_anthropic_client().with_options(timeout=remaining)
            resp = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
                **_claude_sampling(claude_temperature),
            )
            return next((b.text for b in resp.content if b.type == "text"), "")

        # Imported here, not at module scope, so a deployment running
        # `LLM_PROVIDER=claude` need not have the ollama package installed.
        #
        # An explicit Client, not the module-level `generate()` the call sites
        # used to reach: that one carries no deadline, so a server that accepts
        # the connection and then stalls never raises. Same reasoning
        # `_llm_strategies` already had for building its own.
        from ollama import Client
        # A None is dropped rather than sent: `_llm_strategies` sets only a
        # temperature, and adding a top_p/top_k it never sent would change what
        # that endpoint returns as a side effect of moving it onto this module.
        options = {"temperature": temperature, "top_p": top_p, "top_k": top_k}
        resp = Client(timeout=remaining).generate(
            model=ollama_model,
            prompt=prompt,
            options={k: v for k, v in options.items() if v is not None},
        )
        # A mapping on some versions of the client and an object on others --
        # `_llm_strategies` already had to handle both.
        if isinstance(resp, dict):
            return resp.get("response") or ""
        return getattr(resp, "response", "") or ""
    finally:
        _generation_slots.release()
