"""Operational counters: accumulation, flush and read-back, what the model path records, and the admin reads."""

import os
import sys
import threading
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import llm_client  # noqa: E402
import main  # noqa: E402
import ops_metrics  # noqa: E402


def _hour(offset_hours=0):
    t = datetime.now(timezone.utc) + timedelta(hours=offset_hours)
    return t.replace(minute=0, second=0, microsecond=0).isoformat()


class _Query:
    def __init__(self, owner):
        self.owner = owner
        self.filters = []

    def select(self, cols):
        self.filters.append(("select", cols))
        return self

    def in_(self, col, values):
        self.filters.append(("in", col, tuple(values)))
        return self

    def gte(self, col, value):
        self.filters.append(("gte", col, value))
        return self

    def order(self, col):
        return self

    def execute(self):
        self.owner.reads.append(self.filters)
        if self.owner.read_error:
            raise RuntimeError("read failed")
        kinds = next(f[2] for f in self.filters if f[0] == "in")
        return type("R", (), {"data": [r for r in self.owner.stored if r["kind"] in kinds]})()


class _Rpc:
    def __init__(self, owner, name, params):
        self.owner, self.name, self.params = owner, name, params

    def execute(self):
        if self.owner.rpc_error:
            raise RuntimeError("database unreachable")
        self.owner.rpcs.append((self.name, self.params))
        return type("R", (), {"data": len(self.params["p_rows"])})()


class _Fake:
    """`ops_counters` as PostgREST returns it: `hour` comes back as a timestamptz string."""

    def __init__(self, stored=(), rpc_error=False, read_error=False):
        self.stored = list(stored)
        self.rpc_error = rpc_error
        self.read_error = read_error
        self.rpcs = []
        self.reads = []

    def table(self, name):
        assert name == "ops_counters", name
        return _Query(self)

    def rpc(self, name, params):
        return _Rpc(self, name, params)


# ─── accumulate, flush, read ─────────────────────────────────────────────

def test_bumps_accumulate_into_one_cell_per_hour_kind_and_key():
    ops_metrics.bump("refusal", "429 GET /api/topics")
    ops_metrics.bump("refusal", "429 GET /api/topics", 2)
    ops_metrics.bump("refusal", "403 GET /api/x")

    assert ops_metrics.pending() == {("refusal", "429 GET /api/topics"): 3,
                                     ("refusal", "403 GET /api/x"): 1}


def test_a_flush_sends_every_cell_through_the_increment_function_and_empties_the_buffer():
    ops_metrics.bump("question", "served:inline")
    ops_metrics.observe("llm_latency_ms", "claude", 120.0)
    ops_metrics.observe("llm_latency_ms", "claude", 80.0)
    fake = _Fake()

    assert ops_metrics.flush(fake) is True

    [(name, params)] = fake.rpcs
    assert name == "ops_counters_add"
    rows = {(r["kind"], r["key"]): r for r in params["p_rows"]}
    assert rows[("question", "served:inline")]["n"] == 1
    assert rows[("question", "served:inline")]["sum"] is None
    assert rows[("llm_latency_ms", "claude")] | {"hour": None} == {
        "hour": None, "kind": "llm_latency_ms", "key": "claude", "n": 2, "sum": 200.0, "max": 120.0}
    assert ops_metrics.pending() == {}


def test_a_failed_flush_keeps_the_counts_for_the_next_one():
    ops_metrics.bump("refusal", "503 GET /api/generate-question", 4)
    ops_metrics.observe("llm_latency_ms", "claude", 50.0)

    assert ops_metrics.flush(_Fake(rpc_error=True)) is False
    ops_metrics.bump("refusal", "503 GET /api/generate-question")

    fake = _Fake()
    ops_metrics.flush(fake)
    rows = {(r["kind"], r["key"]): r for r in fake.rpcs[0][1]["p_rows"]}
    assert rows[("refusal", "503 GET /api/generate-question")]["n"] == 5
    assert rows[("llm_latency_ms", "claude")]["sum"] == 50.0


def test_nothing_pending_makes_no_call():
    fake = _Fake()
    assert ops_metrics.flush(fake) is True
    assert fake.rpcs == []


def test_past_the_cap_new_cells_are_dropped_and_counted(monkeypatch):
    monkeypatch.setattr(ops_metrics, "_MAX_PENDING", 2)
    for i in range(4):
        ops_metrics.bump("refusal", f"429 GET /r{i}")

    assert len(ops_metrics.pending()) == 2
    assert ops_metrics.read(_Fake(), ["refusal"], 1)["dropped"] == 2


def test_a_read_asks_only_for_the_kinds_and_the_hours_it_was_given():
    fake = _Fake()
    ops_metrics.read(fake, ["refusal"], 6)

    [filters] = fake.reads
    assert ("in", "kind", ("refusal",)) in filters
    [gte] = [f for f in filters if f[0] == "gte"]
    assert gte[1] == "hour"
    assert datetime.fromisoformat(gte[2]) == datetime.fromisoformat(_hour(-6))


def test_a_read_merges_unflushed_counts_into_the_stored_hour():
    # PostgREST's rendering of a timestamptz, not Python's isoformat.
    stored = [{"hour": _hour().replace("T", " ").replace("+00:00", "+00"), "kind": "refusal",
               "key": "429 GET /api/topics", "n": 3, "sum": None, "max": None}]
    ops_metrics.bump("refusal", "429 GET /api/topics", 2)
    ops_metrics.bump("question", "served:queue")   # another kind: not asked for

    got = ops_metrics.read(_Fake(stored), ["refusal"], 1)

    assert got["retrieved"] is True
    assert [(r["key"], r["n"]) for r in got["rows"]] == [("429 GET /api/topics", 5)]


def test_a_failed_read_says_so_and_still_shows_what_this_process_holds():
    ops_metrics.bump("refusal", "429 GET /api/topics")

    got = ops_metrics.read(_Fake(read_error=True), ["refusal"], 1)

    assert got["retrieved"] is False
    assert [(r["key"], r["n"]) for r in got["rows"]] == [("429 GET /api/topics", 1)]


def test_the_flusher_writes_on_stop_and_joins():
    fake = _Fake()
    ops_metrics.bump("question", "prefetched")
    assert ops_metrics.start(lambda: fake) is True

    ops_metrics.stop()

    assert [r["key"] for r in fake.rpcs[0][1]["p_rows"]] == ["prefetched"]
    assert not any(t.name == "ops-flush" for t in threading.enumerate())


# ─── what a model call records ───────────────────────────────────────────

@pytest.fixture
def _llm(monkeypatch):
    monkeypatch.setattr(llm_client, "_call_times", [])
    monkeypatch.setattr(llm_client, "_generation_slots", threading.BoundedSemaphore(2))


def _claude(monkeypatch, *, usage=None, boom=None):
    class _Messages:
        def create(self, **_kwargs):
            if boom is not None:
                raise boom
            return type("Resp", (), {
                "content": [type("B", (), {"type": "text", "text": "{}"})()],
                "usage": usage})()

    class _Client:
        base_url = "https://api.anthropic.com"
        messages = _Messages()

        def with_options(self, **_k):
            return self

    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "claude")
    monkeypatch.setattr(llm_client, "_anthropic_client", _Client())


def test_a_completed_claude_call_records_its_outcome_wait_and_tokens(monkeypatch, _llm):
    _claude(monkeypatch, usage=type("U", (), {"input_tokens": 900, "output_tokens": 150})())

    llm_client.generate_text("p")

    p = ops_metrics.pending()
    assert p[("llm_call", "claude:ok")] == 1
    assert p[("llm_latency_ms", "claude")] == 1
    assert p[("llm_tokens", "claude:in")] == 900
    assert p[("llm_tokens", "claude:out")] == 150


def test_a_response_with_no_usage_records_the_call_and_no_tokens(monkeypatch, _llm):
    _claude(monkeypatch, usage=None)

    llm_client.generate_text("p")

    p = ops_metrics.pending()
    assert p[("llm_call", "claude:ok")] == 1
    assert not any(kind == "llm_tokens" for kind, _ in p)


def test_a_refused_call_records_the_bound_that_refused_it(monkeypatch, _llm):
    _claude(monkeypatch)
    monkeypatch.setattr(llm_client, "GENERATION_DAILY_CALL_LIMIT", 0)

    with pytest.raises(llm_client.GenerationUnavailable):
        llm_client.generate_text("p")

    p = ops_metrics.pending()
    assert p[("llm_call", "claude:unavailable:daily")] == 1
    assert ("llm_call", "claude:ok") not in p


def test_an_unexpected_error_records_its_class_and_still_raises(monkeypatch, _llm):
    _claude(monkeypatch, boom=ValueError("bad"))

    with pytest.raises(ValueError):
        llm_client.generate_text("p")

    assert ops_metrics.pending()[("llm_call", "claude:error:ValueError")] == 1


def test_an_ollama_call_records_its_eval_counts(monkeypatch, _llm):
    class _Client:
        def __init__(self, **_k):
            pass

        def generate(self, **_k):
            return {"response": "{}", "prompt_eval_count": 40, "eval_count": 12}

    module = type(sys)("ollama")
    module.Client = _Client
    monkeypatch.setitem(sys.modules, "ollama", module)
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "ollama")

    assert llm_client.generate_text("p") == "{}"

    p = ops_metrics.pending()
    assert p[("llm_call", "ollama:ok")] == 1
    assert p[("llm_tokens", "ollama:in")] == 40
    assert p[("llm_tokens", "ollama:out")] == 12


# ─── the admin reads ─────────────────────────────────────────────────────

@pytest.fixture
def _admin(monkeypatch):
    monkeypatch.setattr(main, "_require_admin", lambda _r: {"id": "admin"})


def _row(kind, key, n, s=None, m=None, hour=None):
    return {"hour": hour or _hour(), "kind": kind, "key": key, "n": n, "sum": s, "max": m}


def test_generation_totals_outcomes_waits_tokens_and_cost(monkeypatch, _admin):
    monkeypatch.setattr(main, "supabase", _Fake([
        _row("llm_call", "claude:ok", 10),
        _row("llm_call", "claude:unavailable:daily", 2),
        _row("llm_latency_ms", "claude", 12, s=24000.0, m=9000.0),
        _row("llm_tokens", "claude:in", 2_000_000),
        _row("llm_tokens", "claude:out", 100_000),
        _row("question", "served:inline", 4),
    ]))
    monkeypatch.setattr(main, "CLAUDE_PRICE_INPUT_PER_MTOK", 1.0)
    monkeypatch.setattr(main, "CLAUDE_PRICE_OUTPUT_PER_MTOK", 5.0)

    got = main.admin_generation(None)

    assert got["retrieved"] is True
    assert got["outcomes"] == {"claude:ok": 10, "claude:unavailable:daily": 2}
    assert got["waits"] == {"claude": {"calls": 12, "mean_ms": 2000, "max_ms": 9000}}
    assert got["estimated_cost_usd"] == 2.5
    assert got["questions"] == {"served:inline": 4}
    assert got["hourly"] == [{"hour": _hour(), "ok": 10, "failed": 2}]
    assert got["daily_ceiling"]["scope"] == "this server process"


def test_generation_reads_only_its_own_kinds(monkeypatch, _admin):
    fake = _Fake()
    monkeypatch.setattr(main, "supabase", fake)

    main.admin_generation(None, hours=100000)

    [filters] = fake.reads
    assert ("in", "kind", ("llm_call", "llm_latency_ms", "llm_tokens", "question")) in filters
    gte = next(f[2] for f in filters if f[0] == "gte")
    assert datetime.fromisoformat(gte) == datetime.fromisoformat(_hour(-main._OPS_MAX_HOURS))


def test_refusals_split_the_key_and_sort_by_count(monkeypatch, _admin):
    monkeypatch.setattr(main, "supabase", _Fake([
        _row("refusal", "429 GET /api/topics", 2),
        _row("refusal", "403 GET /api/students/{student_id}/report", 7, hour=_hour(-5)),
    ]))

    got = main.admin_refusals(None)

    assert got["refusals"] == [
        {"status": 403, "method": "GET", "route": "/api/students/{student_id}/report", "count": 7},
        {"status": 429, "method": "GET", "route": "/api/topics", "count": 2},
    ]
    # Only the current and previous hour's buckets are recent.
    assert got["recent"] == [{"status": 429, "method": "GET", "route": "/api/topics", "count": 2}]


def test_a_failed_counter_read_is_not_a_quiet_day(monkeypatch, _admin):
    monkeypatch.setattr(main, "supabase", _Fake(read_error=True))

    assert main.admin_refusals(None)["retrieved"] is False
    assert main.admin_generation(None)["retrieved"] is False


@pytest.mark.parametrize("hours,expected", [(-5, 1), (0, 1), (3, 3), (10**6, 168)])
def test_the_hour_range_is_clamped_not_refused(monkeypatch, _admin, hours, expected):
    monkeypatch.setattr(main, "supabase", _Fake())
    assert main.admin_refusals(None, hours=hours)["hours"] == expected
