"""The one place both providers' request shapes are pinned.

Nothing else asserts on the request that is *built* -- the 13 generation call
sites all check that a question came back, which passes against a fixture and
says nothing about what was sent. That matters most for the sampling
parameters: Ollama's `temperature=1.1` is outside Anthropic's 0.0-1.0 range, so
passing it through would have returned `400 invalid_request_error` on every
question, at every topic, the moment `LLM_PROVIDER=claude`.
"""

import sys
import threading

import httpx2 as httpx
import pytest

import llm_client


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Each test gets its own counters, and none of them may reach a network.

    The call ledger and the semaphore are module state, so a test that fills
    one would otherwise decide the next test's outcome.
    """
    monkeypatch.setattr(llm_client, "_call_times", [])
    monkeypatch.setattr(llm_client, "_generation_slots",
                        threading.BoundedSemaphore(llm_client.GENERATION_MAX_CONCURRENCY))
    monkeypatch.setattr(llm_client, "_anthropic_client", None)


def _real_create_params():
    """Parameter names the installed SDK's `messages.create` actually accepts.

    Read off `anthropic` itself rather than listed here, so the check tracks
    the pinned version instead of a copy of it that ages.
    """
    import inspect
    import anthropic
    sig = inspect.signature(anthropic.Anthropic(api_key="x").messages.create)
    return set(sig.parameters)


class _FakeMessages:
    def __init__(self, owner):
        self._owner = owner

    def create(self, **kwargs):
        # The load-bearing line. This double used to take `**kwargs` and record
        # them, so every assertion about "the request we build" was made
        # against a stub that accepts anything -- including `temperature`,
        # which anthropic 1.x removed and which raises TypeError before a
        # request is built. The suite was green over a call that could not run.
        # A double that is more permissive than the thing it stands in for
        # cannot fail on the one difference that matters.
        unknown = set(kwargs) - _real_create_params()
        assert not unknown, (
            f"messages.create() does not accept {sorted(unknown)} in "
            f"anthropic {__import__('anthropic').__version__} -- this would "
            f"raise TypeError against the real SDK"
        )
        self._owner.calls.append(kwargs)
        return type("Resp", (), {"content": [
            type("Block", (), {"type": "thinking", "text": "ignored"})(),
            type("Block", (), {"type": "text", "text": "  {\"topic\": \"algebra\"}"})(),
        ]})()


class _FakeAnthropic:
    """Records what reached `messages.create`, and what `with_options` was given."""

    def __init__(self):
        self.calls: list[dict] = []
        self.options: list[dict] = []
        self.messages = _FakeMessages(self)
        # The real client exposes this and the unreachable-API message reads
        # it. Carried on the double deliberately: a double that is *missing*
        # something the real object has fails a correct implementation, which
        # is the mirror of the `**kwargs` trap this file already documents --
        # there the double was more permissive and hid a broken request.
        self.base_url = "https://api.anthropic.com"

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        return self


def _claude(monkeypatch):
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "claude")
    fake = _FakeAnthropic()
    monkeypatch.setattr(llm_client, "_anthropic_client", fake)
    return fake


def _fake_ollama(monkeypatch, response=None, boom=None):
    """A stand-in `ollama` module, recording the Client and generate kwargs."""
    class _Client:
        def __init__(self, **kwargs):
            _Client.last_init = kwargs

        def generate(self, **kwargs):
            _Client.last_generate = kwargs
            if boom is not None:
                raise boom
            return response

    module = type(sys)("ollama")
    module.Client = _Client
    monkeypatch.setitem(sys.modules, "ollama", module)
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "ollama")
    return _Client


# ─── provider selection ──────────────────────────────────────────────────

def test_the_default_provider_is_ollama(monkeypatch):
    """A checkout that has never been configured must not bill an API key.

    Read off the environment rather than the module constant, so this fails if
    someone changes the default rather than passing against whatever it is.
    """
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    import os
    assert os.getenv("LLM_PROVIDER", "ollama") == "ollama"
    assert llm_client.LLM_PROVIDER in ("ollama", "claude")


def test_the_ollama_branch_keeps_all_three_sampling_parameters(monkeypatch):
    """They are valid there, and today's behaviour must not move."""
    client = _fake_ollama(monkeypatch, response={"response": "hello"})
    assert llm_client.generate_text("p") == "hello"
    assert client.last_generate["options"] == {
        "temperature": 1.1, "top_p": 0.95, "top_k": 100,
    }
    assert client.last_generate["model"] == "llama3.1:8b"


def test_the_ollama_branch_reads_an_object_response_too(monkeypatch):
    """Some versions of the client return an object rather than a mapping."""
    _fake_ollama(monkeypatch, response=type("R", (), {"response": "hi"})())
    assert llm_client.generate_text("p") == "hi"


def test_an_unset_ollama_sampling_parameter_is_dropped_rather_than_sent(monkeypatch):
    """`_llm_strategies` sets only a temperature.

    Moving it onto this module must not add a top_p/top_k it never sent -- that
    would change what a parent is shown as a side effect of a refactor, on the
    provider that is still the default.
    """
    client = _fake_ollama(monkeypatch, response={"response": "x"})
    llm_client.generate_text("p", temperature=0.4, top_p=None, top_k=None)
    assert client.last_generate["options"] == {"temperature": 0.4}


def test_the_ollama_model_is_selectable(monkeypatch):
    """The decider's parallel pass runs a 3b, not the 8b everything else uses."""
    client = _fake_ollama(monkeypatch, response={"response": "x"})
    llm_client.generate_text("p", ollama_model="llama3.2:3b", temperature=0.7)
    assert client.last_generate["model"] == "llama3.2:3b"
    assert client.last_generate["options"]["temperature"] == 0.7


# ─── the sampling parameters that would have 400'd ───────────────────────

def test_the_ollama_sampling_parameters_do_not_reach_the_claude_request(monkeypatch):
    """The failure this pins is total, not partial.

    Every generation call site passes Ollama's `temperature=1.1, top_p=0.95,
    top_k=100`. None of the three may reach `messages.create`: `top_p`/`top_k`
    are not parameters of it in anthropic 1.x, and neither is `temperature`.
    Sent, they raise TypeError before a request exists -- every question, on
    every topic, from the first call.
    """
    fake = _claude(monkeypatch)
    llm_client.generate_text("p", temperature=1.1, top_p=0.95, top_k=100)
    sent = fake.calls[0]
    assert "temperature" not in sent
    assert "top_p" not in sent
    assert "top_k" not in sent
    assert sent["model"] == llm_client.CLAUDE_MODEL
    assert sent["messages"] == [{"role": "user", "content": "p"}]


def test_the_hot_path_asks_for_no_sampling_parameter_at_all(monkeypatch):
    """Generation takes the API's own default temperature, which is 1.0 -- the
    same value `CLAUDE_TEMPERATURE` defaults to.

    Asking for nothing is what makes the hot path unrejectable: there is no
    parameter for the wire to refuse. `extra_body` stays absent here, since an
    unnecessary one would put the 99% path on the unverified escape hatch.
    """
    fake = _claude(monkeypatch)
    llm_client.generate_text("p")
    assert "extra_body" not in fake.calls[0]


def test_a_caller_can_ask_for_its_own_claude_temperature(monkeypatch):
    """The strategies pass writes advice a parent reads, and wants 0.4.

    It travels in `extra_body` because the typed signature no longer carries
    it. Asserted on the wire shape rather than a kwarg, so this test cannot
    again pass against a request the SDK would reject.
    """
    fake = _claude(monkeypatch)
    llm_client.generate_text("p", claude_temperature=0.4)
    assert fake.calls[0]["extra_body"] == {"temperature": 0.4}


def test_an_out_of_range_claude_temperature_is_clamped_rather_than_sent(monkeypatch):
    """A caller passing Ollama's 1.1 through the Claude knob still must not 400.

    Clamped to 1.0, which equals the default, so it collapses to sending
    nothing -- the clamp and the omission agree rather than fighting.
    """
    fake = _claude(monkeypatch)
    llm_client.generate_text("p", claude_temperature=1.1)
    assert "extra_body" not in fake.calls[0]
    assert "temperature" not in fake.calls[0]


def test_the_timeout_reaches_the_client_rather_than_the_request(monkeypatch):
    """The SDK's own default is ten minutes; a prefetch worker blocked that
    long never refills the queue.

    Never *more* than the budget, and not exactly it either: the uncontended
    acquire above still takes a few microseconds, and those come out of the
    budget rather than being added to it. See the queueing test below.
    """
    fake = _claude(monkeypatch)
    llm_client.generate_text("p", timeout=12.0)
    assert 11.9 < fake.options[-1]["timeout"] <= 12.0


def test_max_tokens_is_passed_through(monkeypatch):
    fake = _claude(monkeypatch)
    llm_client.generate_text("p", max_tokens=1024)
    assert fake.calls[0]["max_tokens"] == 1024


def test_only_the_text_block_is_returned(monkeypatch):
    """A response may carry blocks that are not the answer."""
    _claude(monkeypatch)
    assert llm_client.generate_text("p").strip().startswith("{")


# ─── the bounds ──────────────────────────────────────────────────────────

def test_the_daily_ceiling_refuses_rather_than_billing_on(monkeypatch):
    """Refusing is the decision -- see `GenerationUnavailable`."""
    _claude(monkeypatch)
    monkeypatch.setattr(llm_client, "GENERATION_DAILY_CALL_LIMIT", 2)
    llm_client.generate_text("p")
    llm_client.generate_text("p")
    with pytest.raises(llm_client.GenerationUnavailable):
        llm_client.generate_text("p")


def test_the_daily_ceiling_does_not_count_ollama_calls(monkeypatch):
    """Local and free: a call ceiling there would refuse a child a question to
    protect nothing."""
    _fake_ollama(monkeypatch, response={"response": "x"})
    monkeypatch.setattr(llm_client, "GENERATION_DAILY_CALL_LIMIT", 1)
    for _ in range(5):
        assert llm_client.generate_text("p") == "x"
    assert llm_client._calls_in_window() == 0


# Every slot test runs against a semaphore of ONE. Sized to the real ceiling
# instead, a leaked slot is invisible: the test would have to make eight calls
# before the eighth blocked, so `release()` could be deleted outright and all
# three of these would still pass. They did, until this was fixed.

def test_a_completed_call_releases_its_concurrency_slot(monkeypatch):
    _claude(monkeypatch)
    monkeypatch.setattr(llm_client, "_generation_slots", threading.BoundedSemaphore(1))
    llm_client.generate_text("p")
    assert llm_client._generation_slots.acquire(blocking=False)
    llm_client._generation_slots.release()


def test_a_refused_call_releases_its_concurrency_slot(monkeypatch):
    """The daily ceiling raises from *inside* the slot. Leaked, a refusal would
    be permanent rather than daily -- every later call would find no slot."""
    _claude(monkeypatch)
    monkeypatch.setattr(llm_client, "GENERATION_DAILY_CALL_LIMIT", 1)
    monkeypatch.setattr(llm_client, "_generation_slots", threading.BoundedSemaphore(1))
    llm_client.generate_text("p")
    with pytest.raises(llm_client.GenerationUnavailable):
        llm_client.generate_text("p", timeout=0.05)
    assert llm_client._generation_slots.acquire(blocking=False)
    llm_client._generation_slots.release()


def test_an_api_error_releases_its_slot_too(monkeypatch):
    """Errors are deliberately not swallowed here, so the release has to sit in
    a finally rather than after the call."""
    _fake_ollama(monkeypatch, boom=ConnectionError("refused"))
    monkeypatch.setattr(llm_client, "_generation_slots", threading.BoundedSemaphore(1))
    with pytest.raises(ConnectionError):
        llm_client.generate_text("p")
    assert llm_client._generation_slots.acquire(blocking=False)
    llm_client._generation_slots.release()


def test_time_spent_queueing_comes_out_of_the_budget_it_was_promised(monkeypatch):
    """Charged twice, one caller blocks for nearly double what it asked for.

    The budget is a promise about the whole call. `_llm_strategies` had this
    exact bug against its own pool -- a queued call held a worker for nearly
    twice STRATEGY_LLM_TIMEOUT -- and the fix has to live here rather than at
    the call site, because this is where the queueing happens.
    """
    import time as _time
    fake = _claude(monkeypatch)
    monkeypatch.setattr(llm_client, "_generation_slots", threading.BoundedSemaphore(1))

    llm_client._generation_slots.acquire()
    held = 0.15

    # The releasing thread records the moment it let go. "Did this really
    # queue" is then a question about *ordering* -- the call cannot have
    # acquired the only slot before it was released -- rather than about a
    # duration, and ordering between two readings of one clock with a genuine
    # happens-before between them is exact.
    #
    # It was `monotonic() - started >= held`, which is not the same claim and
    # flaked on Windows: the default timer resolution there is ~15.6 ms, so a
    # `Timer(0.15)` can fire a hair early and the exact-boundary comparison
    # fails against a correct implementation. Measured 2 failures in 5 runs on
    # a clean checkout, which reads as caused by whatever you are working on.
    released_at = []

    def _release():
        released_at.append(_time.monotonic())
        llm_client._generation_slots.release()

    timer = threading.Timer(held, _release)
    timer.start()
    try:
        llm_client.generate_text("p", timeout=1.0)
        finished = _time.monotonic()
    finally:
        timer.cancel()

    # Teeth: drop the semaphore acquire from `generate_text` and it returns
    # immediately, long before the timer fires, so nothing was ever appended.
    assert released_at, "the call did not queue -- it returned without waiting for a slot"
    assert finished >= released_at[0], "the call returned before the slot was released"

    charged = fake.options[-1]["timeout"]
    assert charged < 1.0 - held / 2, f"model call was charged {charged}s of a 1.0s budget"


def test_a_call_that_queues_out_its_whole_budget_is_refused_not_started(monkeypatch):
    """Zero left is not a reason to start a call with no deadline."""
    fake = _claude(monkeypatch)
    monkeypatch.setattr(llm_client, "_generation_slots", threading.BoundedSemaphore(1))

    llm_client._generation_slots.acquire()
    threading.Timer(0.06, llm_client._generation_slots.release).start()
    with pytest.raises(llm_client.GenerationUnavailable):
        llm_client.generate_text("p", timeout=0.05)
    assert fake.calls == []


def test_a_caller_that_cannot_get_a_slot_is_refused_within_its_budget(monkeypatch):
    """Waiting out the whole budget for a slot spends the student's time and
    still produces no question, so the wait is bounded by the same deadline."""
    _claude(monkeypatch)
    monkeypatch.setattr(llm_client, "_generation_slots", threading.BoundedSemaphore(1))
    llm_client._generation_slots.acquire()
    try:
        with pytest.raises(llm_client.GenerationUnavailable):
            llm_client.generate_text("p", timeout=0.05)
    finally:
        llm_client._generation_slots.release()


def test_a_bad_numeric_setting_falls_back_rather_than_crashing_the_app(monkeypatch):
    """These are read at import, so a typo would take every endpoint down over
    one tuning knob."""
    monkeypatch.setenv("SOME_KNOB", "not-a-number")
    assert llm_client._env_number("SOME_KNOB", 30.0, float, minimum=1.0) == 30.0
    monkeypatch.setenv("SOME_KNOB", "inf")
    assert llm_client._env_number("SOME_KNOB", 30.0, float, minimum=1.0) == 30.0
    monkeypatch.setenv("SOME_KNOB", "0")
    assert llm_client._env_number("SOME_KNOB", 30.0, float, minimum=1.0) == 1.0


def test_the_configured_temperature_reaches_generation(monkeypatch):
    """CLAUDE_TEMPERATURE was inert on both paths and documented as the knob.

    Generation passes `claude_temperature=None`, and the first version of
    `_claude_sampling` returned early on None -- so the setting was never read
    at all, and a deployment tuning it saw no effect.
    """
    monkeypatch.setattr(llm_client, "CLAUDE_TEMPERATURE", 0.7)
    fake = _claude(monkeypatch)
    llm_client.generate_text("p")
    assert fake.calls[0]["extra_body"] == {"temperature": 0.7}


def test_a_callers_temperature_survives_matching_the_configured_one(monkeypatch):
    """The inversion, and the reason the comparison is against 1.0.

    Omitting the parameter selects the API's default of 1.0 -- not
    CLAUDE_TEMPERATURE. Comparing against the setting meant that with
    CLAUDE_TEMPERATURE=0.4, `_llm_strategies` asking for 0.4 matched, nothing
    was sent, and the call ran at 1.0: the single value that configuration
    exists to avoid, reached only when someone configured it.
    """
    monkeypatch.setattr(llm_client, "CLAUDE_TEMPERATURE", 0.4)
    fake = _claude(monkeypatch)
    llm_client.generate_text("p", claude_temperature=0.4)
    assert fake.calls[0]["extra_body"] == {"temperature": 0.4}, \
        "the requested temperature was dropped and the call ran at 1.0"


def test_the_installed_sdk_is_the_pinned_one():
    """`_real_create_params` reads the *installed* SDK, so the guard above is
    only as good as the venv it runs in.

    `anthropic` was pinned at 1.1.0 in requirements.txt and 1.0.0 was installed
    in both venvs for the whole of the Claude migration -- so the check that
    exists to catch a parameter the SDK does not accept was measuring a
    different SDK from the one a deployment gets. A signature guard against the
    wrong version is worse than none: it reports agreement it never tested.

    Same rule as CLAUDE.md's on backend/.venv -- install a runtime dependency
    in the same change that pins it.
    """
    import re
    import anthropic
    from pathlib import Path

    req = (Path(__file__).resolve().parent.parent / "requirements.txt").read_text()
    m = re.search(r'^anthropic==(\S+)$', req, re.M)
    assert m, "anthropic is no longer pinned in requirements.txt"
    assert anthropic.__version__ == m.group(1), (
        f"installed anthropic {anthropic.__version__} but requirements.txt "
        f"pins {m.group(1)} -- the signature guard is measuring the wrong SDK"
    )


def test_a_schema_reaches_the_request_as_output_config(monkeypatch):
    """The shape is checked against the installed SDK by `_FakeMessages`, so
    this pins the nesting rather than the parameter name -- `output_config`
    takes a `format`, which takes the schema under `type: json_schema`."""
    fake = _claude(monkeypatch)
    schema = {"type": "object", "properties": {}, "required": [],
              "additionalProperties": False}
    llm_client.generate_text("p", schema=schema)
    assert fake.calls[0]["output_config"] == {
        "format": {"type": "json_schema", "schema": schema}}


def test_no_schema_sends_no_output_config_at_all(monkeypatch):
    """An absent key and a permissive schema are different requests.

    Two topics cannot express a schema -- probability's bag scenarios need an
    open `items` object, which the API refuses -- so they pass `None`. If that
    became an empty or catch-all `output_config` the reply would be constrained
    by something nobody wrote, and the call would read as covered.
    """
    fake = _claude(monkeypatch)
    llm_client.generate_text("p")
    assert "output_config" not in fake.calls[0]
    llm_client.generate_text("p", schema=None)
    assert "output_config" not in fake.calls[1]


def test_a_schema_is_ignored_on_the_ollama_branch(monkeypatch):
    """Structured output is Claude-only, and the consequence is the point.

    Dev runs are unschema'd, so `extract_json`, the retry loops and every
    code-level check downstream stay load-bearing -- and stay the only thing
    between a malformed dev reply and a question. A schema silently changing
    what Ollama is asked for would also mean local testing no longer exercises
    the path production's fallback depends on.
    """
    client = _fake_ollama(monkeypatch, response={"response": "hi"})
    assert llm_client.generate_text("p", schema={"type": "object"}) == "hi"
    sent = client.last_generate
    assert "schema" not in sent
    assert "output_config" not in sent
    assert "format" not in sent
    assert sent["options"] == {"temperature": 1.1, "top_p": 0.95, "top_k": 100}


def test_an_unreachable_api_is_a_503_naming_the_url(monkeypatch):
    """Being unable to *reach* the API is the same kind of fact as a bound
    refusing: this deployment cannot serve right now, and it says nothing about
    the model or the reply.

    Unclassified it surfaced as a 500 with a 200-line traceback, and the page
    told the student to "make sure the backend is running" -- while the backend
    was running and the unreachable thing was the API. It took three rounds to
    diagnose a stale `ANTHROPIC_BASE_URL` pointing at a local proxy that was
    not listening, because "Connection error." names nothing.

    The URL is in the message for that reason. It is not a secret; the key is,
    and is not logged.
    """
    import anthropic

    fake = _claude(monkeypatch)

    def _refuse(**kwargs):
        raise anthropic.APIConnectionError(request=httpx.Request("POST", "/v1/messages"))

    monkeypatch.setattr(fake.messages, "create", _refuse)
    with pytest.raises(llm_client.GenerationUnavailable, match="cannot reach the model API"):
        llm_client.generate_text("p")


def test_a_bad_api_key_stays_loud(monkeypatch):
    """The teeth. `AuthenticationError` is an `APIStatusError`, a different
    branch, and must NOT become a 503 -- a misconfigured key is not a passing
    outage, and reporting it as one turns "this key is wrong" into "try again
    later", which is the swallow the docstring warns about."""
    import anthropic

    fake = _claude(monkeypatch)

    def _reject(**kwargs):
        raise anthropic.AuthenticationError(
            "invalid x-api-key",
            response=httpx.Response(401, request=httpx.Request("POST", "/v1/messages")),
            body=None)

    monkeypatch.setattr(fake.messages, "create", _reject)
    with pytest.raises(anthropic.AuthenticationError):
        llm_client.generate_text("p")
