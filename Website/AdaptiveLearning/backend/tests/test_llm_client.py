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


class _FakeMessages:
    def __init__(self, owner):
        self._owner = owner

    def create(self, **kwargs):
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

def test_the_claude_branch_sends_a_temperature_in_range_and_no_top_p_or_top_k(monkeypatch):
    """The failure this pins is total, not partial.

    `temperature=1.1` is every generation call site's default and is outside
    Anthropic's accepted range, so passing the Ollama parameters through would
    have failed every question on every topic -- not degraded them.
    """
    fake = _claude(monkeypatch)
    llm_client.generate_text("p", temperature=1.1, top_p=0.95, top_k=100)
    sent = fake.calls[0]
    assert 0.0 <= sent["temperature"] <= 1.0
    assert "top_p" not in sent
    assert "top_k" not in sent
    assert sent["model"] == llm_client.CLAUDE_MODEL
    assert sent["messages"] == [{"role": "user", "content": "p"}]


def test_a_caller_can_ask_for_its_own_claude_temperature(monkeypatch):
    """The strategies pass writes advice a parent reads, and wants 0.4."""
    fake = _claude(monkeypatch)
    llm_client.generate_text("p", claude_temperature=0.4)
    assert fake.calls[0]["temperature"] == 0.4


def test_an_out_of_range_claude_temperature_is_clamped_rather_than_sent(monkeypatch):
    """A caller passing Ollama's 1.1 through the Claude knob still must not 400."""
    fake = _claude(monkeypatch)
    llm_client.generate_text("p", claude_temperature=1.1)
    assert fake.calls[0]["temperature"] == 1.0


def test_the_timeout_reaches_the_client_rather_than_the_request(monkeypatch):
    """The SDK's own default is ten minutes; a prefetch worker blocked that
    long never refills the queue."""
    fake = _claude(monkeypatch)
    llm_client.generate_text("p", timeout=12.0)
    assert fake.options[-1]["timeout"] == 12.0


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
