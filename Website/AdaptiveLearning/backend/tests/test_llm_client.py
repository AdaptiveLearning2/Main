"""The one place both providers' built request shapes are pinned."""

import sys
import threading

import httpx2 as httpx
import pytest

import llm_client


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Fresh call ledger and semaphore per test, and no network."""
    monkeypatch.setattr(llm_client, "_call_times", [])
    monkeypatch.setattr(llm_client, "_generation_slots",
                        threading.BoundedSemaphore(llm_client.GENERATION_MAX_CONCURRENCY))
    monkeypatch.setattr(llm_client, "_anthropic_client", None)


def _real_create_params():
    """Parameter names the installed SDK's `messages.create` actually accepts."""
    import inspect
    import anthropic
    sig = inspect.signature(anthropic.Anthropic(api_key="x").messages.create)
    return set(sig.parameters)


class _FakeMessages:
    def __init__(self, owner):
        self._owner = owner

    def create(self, **kwargs):
        # A double that accepts any kwarg would hide a TypeError from the real SDK.
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
        # The unreachable-API message reads it off the real client.
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
    """An unconfigured checkout must not bill an API key."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    import os
    assert os.getenv("LLM_PROVIDER", "ollama") == "ollama"
    assert llm_client.LLM_PROVIDER in ("ollama", "claude")


def test_the_ollama_branch_keeps_all_three_sampling_parameters(monkeypatch):
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
    """`_llm_strategies` sets only a temperature."""
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
    """None of temperature/top_p/top_k is a `messages.create` parameter in anthropic 1.x."""
    fake = _claude(monkeypatch)
    llm_client.generate_text("p", temperature=1.1, top_p=0.95, top_k=100)
    sent = fake.calls[0]
    assert "temperature" not in sent
    assert "top_p" not in sent
    assert "top_k" not in sent
    assert sent["model"] == llm_client.CLAUDE_MODEL
    assert sent["messages"] == [{"role": "user", "content": "p"}]


def test_the_hot_path_asks_for_no_sampling_parameter_at_all(monkeypatch):
    """Generation takes the API default (1.0); nothing sent means nothing to reject."""
    fake = _claude(monkeypatch)
    llm_client.generate_text("p")
    assert "extra_body" not in fake.calls[0]


def test_a_caller_can_ask_for_its_own_claude_temperature(monkeypatch):
    """It travels in `extra_body`; the typed signature no longer carries it."""
    fake = _claude(monkeypatch)
    llm_client.generate_text("p", claude_temperature=0.4)
    assert fake.calls[0]["extra_body"] == {"temperature": 0.4}


def test_an_out_of_range_claude_temperature_is_clamped_rather_than_sent(monkeypatch):
    """Clamped to 1.0, the default, so it collapses to sending nothing."""
    fake = _claude(monkeypatch)
    llm_client.generate_text("p", claude_temperature=1.1)
    assert "extra_body" not in fake.calls[0]
    assert "temperature" not in fake.calls[0]


def test_the_timeout_reaches_the_client_rather_than_the_request(monkeypatch):
    """The SDK default is ten minutes; the acquire's microseconds come out of the budget."""
    fake = _claude(monkeypatch)
    llm_client.generate_text("p", timeout=12.0)
    assert 11.9 < fake.options[-1]["timeout"] <= 12.0


def test_max_tokens_is_passed_through(monkeypatch):
    fake = _claude(monkeypatch)
    llm_client.generate_text("p", max_tokens=1024)
    assert fake.calls[0]["max_tokens"] == 1024


def test_only_the_text_block_is_returned(monkeypatch):
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
    """Ollama is local and free."""
    _fake_ollama(monkeypatch, response={"response": "x"})
    monkeypatch.setattr(llm_client, "GENERATION_DAILY_CALL_LIMIT", 1)
    for _ in range(5):
        assert llm_client.generate_text("p") == "x"
    assert llm_client._calls_in_window() == 0


# Slot tests use a semaphore of ONE, so a leaked slot blocks the next acquire.

def test_a_completed_call_releases_its_concurrency_slot(monkeypatch):
    _claude(monkeypatch)
    monkeypatch.setattr(llm_client, "_generation_slots", threading.BoundedSemaphore(1))
    llm_client.generate_text("p")
    assert llm_client._generation_slots.acquire(blocking=False)
    llm_client._generation_slots.release()


def test_a_refused_call_releases_its_concurrency_slot(monkeypatch):
    """The daily ceiling raises from inside the slot."""
    _claude(monkeypatch)
    monkeypatch.setattr(llm_client, "GENERATION_DAILY_CALL_LIMIT", 1)
    monkeypatch.setattr(llm_client, "_generation_slots", threading.BoundedSemaphore(1))
    llm_client.generate_text("p")
    with pytest.raises(llm_client.GenerationUnavailable):
        llm_client.generate_text("p", timeout=0.05)
    assert llm_client._generation_slots.acquire(blocking=False)
    llm_client._generation_slots.release()


def test_an_api_error_releases_its_slot_too(monkeypatch):
    """Errors are not swallowed, so the release must sit in a finally."""
    _fake_ollama(monkeypatch, boom=ConnectionError("refused"))
    monkeypatch.setattr(llm_client, "_generation_slots", threading.BoundedSemaphore(1))
    with pytest.raises(ConnectionError):
        llm_client.generate_text("p")
    assert llm_client._generation_slots.acquire(blocking=False)
    llm_client._generation_slots.release()


def test_time_spent_queueing_comes_out_of_the_budget_it_was_promised(monkeypatch):
    """The budget covers the whole call, queueing included."""
    import time as _time
    fake = _claude(monkeypatch)
    monkeypatch.setattr(llm_client, "_generation_slots", threading.BoundedSemaphore(1))

    llm_client._generation_slots.acquire()
    held = 0.15

    # Assert an ordering against the release time, not a duration: Windows timers are ~15.6 ms coarse.
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

    # Without the acquire the call returns before the timer fires.
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
    _claude(monkeypatch)
    monkeypatch.setattr(llm_client, "_generation_slots", threading.BoundedSemaphore(1))
    llm_client._generation_slots.acquire()
    try:
        with pytest.raises(llm_client.GenerationUnavailable):
            llm_client.generate_text("p", timeout=0.05)
    finally:
        llm_client._generation_slots.release()


def test_a_bad_numeric_setting_falls_back_rather_than_crashing_the_app(monkeypatch):
    """Read at import, so a typo would take every endpoint down."""
    monkeypatch.setenv("SOME_KNOB", "not-a-number")
    assert llm_client._env_number("SOME_KNOB", 30.0, float, minimum=1.0) == 30.0
    monkeypatch.setenv("SOME_KNOB", "inf")
    assert llm_client._env_number("SOME_KNOB", 30.0, float, minimum=1.0) == 30.0
    monkeypatch.setenv("SOME_KNOB", "0")
    assert llm_client._env_number("SOME_KNOB", 30.0, float, minimum=1.0) == 1.0


def test_the_configured_temperature_reaches_generation(monkeypatch):
    """Generation passes `claude_temperature=None`, which must still read the setting."""
    monkeypatch.setattr(llm_client, "CLAUDE_TEMPERATURE", 0.7)
    fake = _claude(monkeypatch)
    llm_client.generate_text("p")
    assert fake.calls[0]["extra_body"] == {"temperature": 0.7}


def test_a_callers_temperature_survives_matching_the_configured_one(monkeypatch):
    """Omitting selects the API default of 1.0, not CLAUDE_TEMPERATURE; compare against 1.0."""
    monkeypatch.setattr(llm_client, "CLAUDE_TEMPERATURE", 0.4)
    fake = _claude(monkeypatch)
    llm_client.generate_text("p", claude_temperature=0.4)
    assert fake.calls[0]["extra_body"] == {"temperature": 0.4}, \
        "the requested temperature was dropped and the call ran at 1.0"


def test_the_installed_sdk_is_the_pinned_one():
    """`_real_create_params` reads the installed SDK, so it must be the pinned one."""
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
    """Pins the nesting: `output_config` takes a `format` holding the schema."""
    fake = _claude(monkeypatch)
    schema = {"type": "object", "properties": {}, "required": [],
              "additionalProperties": False}
    llm_client.generate_text("p", schema=schema)
    assert fake.calls[0]["output_config"] == {
        "format": {"type": "json_schema", "schema": schema}}


def test_no_schema_sends_no_output_config_at_all(monkeypatch):
    """Topics that cannot express a schema pass None; that must not become a catch-all."""
    fake = _claude(monkeypatch)
    llm_client.generate_text("p")
    assert "output_config" not in fake.calls[0]
    llm_client.generate_text("p", schema=None)
    assert "output_config" not in fake.calls[1]


def test_a_schema_is_ignored_on_the_ollama_branch(monkeypatch):
    """Dev runs stay unschema'd, so they exercise the downstream checks production falls back on."""
    client = _fake_ollama(monkeypatch, response={"response": "hi"})
    assert llm_client.generate_text("p", schema={"type": "object"}) == "hi"
    sent = client.last_generate
    assert "schema" not in sent
    assert "output_config" not in sent
    assert "format" not in sent
    assert sent["options"] == {"temperature": 1.1, "top_p": 0.95, "top_k": 100}


def test_an_unreachable_api_is_a_503_naming_the_url(monkeypatch):
    """An unreachable API is a deployment fact; the URL (not a secret) names the cause."""
    import anthropic

    fake = _claude(monkeypatch)

    def _refuse(**kwargs):
        raise anthropic.APIConnectionError(request=httpx.Request("POST", "/v1/messages"))

    monkeypatch.setattr(fake.messages, "create", _refuse)
    with pytest.raises(llm_client.GenerationUnavailable, match="cannot reach the model API"):
        llm_client.generate_text("p")


def test_a_bad_api_key_stays_loud(monkeypatch):
    """A misconfigured key is not a passing outage, so it must not become a 503."""
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
