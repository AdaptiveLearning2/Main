"""Drive a class-sized burst of question generation at a real server.

CLAUDE.md states four bounds around generation, and until this script every
one of them was checked by calling a helper directly. That leaves the claim
they exist to support untested: that when thirty students press start at
once, the requests that cannot be served are *refused* rather than parked in
anyio's shared threadpool, and the rest of the API keeps answering.

Threadpool starvation is the hazard, so the victim probe is `/api/topics`:
sync, no database, no auth, pure CPU. If it slows down while generation is
saturated, the threadpool is starved -- and that is exactly what
`GENERATION_MAX_WAITERS` exists to prevent. Ingest is the surface CLAUDE.md
names, but it shares the same threadpool and needs a session, consent and a
token to reach; `/api/topics` measures the same mechanism with nothing else
in the way. Say "threadpool", not "ingest", when quoting these numbers.

**No model is called and nothing is billed.** The fake stands in for the
network peer only: `llm_client.generate_text` runs for real, so the
semaphore, the budget arithmetic, the timeout and the refusals are the
shipped code. `FAKE_LATENCY` is what a model call costs.

    python scripts/load_test_generation.py --students 30 --latency 2.0

Exit status is non-zero if a bound did not hold.
"""
import argparse
import json
import os
import statistics
import sys
import threading
import time
import types

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "Website", "AdaptiveLearning", "backend")
sys.path.insert(0, os.path.abspath(BACKEND))

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")
os.environ["LLM_PROVIDER"] = "ollama"

ap = argparse.ArgumentParser()
ap.add_argument("--students", type=int, default=30,
                help="how many press start at the same instant")
ap.add_argument("--latency", type=float, default=2.0,
                help="seconds a model call takes")
ap.add_argument("--stagger", type=float, default=0.0,
                help="seconds over which the class arrives (0 = same instant, "
                     "which is the worst case and not what a room does)")
ap.add_argument("--grade", default="3rd Grade")
ap.add_argument("--port", type=int, default=8123)
args = ap.parse_args()

# ── the network peer, and nothing else ────────────────────────────────────

_calls = {"n": 0}
_calls_lock = threading.Lock()
_concurrent = {"now": 0, "peak": 0}


class _FakeOllamaClient:
    """Sleeps like a model and answers like one.

    Tracks its own concurrency, which is the direct measurement of
    `GENERATION_MAX_CONCURRENCY`: nothing downstream of the semaphore can
    exceed it, so a peak above the cap means the cap does not work.
    """

    def __init__(self, *a, **k):
        pass

    def generate(self, model=None, prompt="", options=None):
        with _calls_lock:
            _calls["n"] += 1
            _concurrent["now"] += 1
            _concurrent["peak"] = max(_concurrent["peak"], _concurrent["now"])
        try:
            time.sleep(args.latency)
            if "TOPIC SELECTION RULES" in prompt:
                body = {"topic": "patterns", "difficulty": "easy"}
            else:
                # Strings, not ints: `solve_pattern` refuses a list that is
                # not all strings, which is how the model actually replies.
                body = {"values": ["2", "4", "?", "8", "10"],
                        "question_text":
                            "What number replaces ? in the sequence "
                            "2, 4, ?, 8, 10?"}
            return {"response": json.dumps(body)}
        finally:
            with _calls_lock:
                _concurrent["now"] -= 1


_ollama = types.ModuleType("ollama")
_ollama.Client = _FakeOllamaClient
sys.modules["ollama"] = _ollama

# ── a database that answers nothing, which is all this path needs ─────────


class _Result:
    def __init__(self, data):
        self.data = data
        self.count = len(data) if data is not None else 0


class _Query:
    """Every PostgREST builder method returns self; `execute` ends it."""

    def __init__(self, table):
        self._table = table

    def __getattr__(self, _name):
        return lambda *a, **k: self

    def execute(self):
        if self._table == "questions":
            # `add_question_to_supabase` reads an id back off the insert.
            return _Result([{"id": "00000000-0000-0000-0000-000000000001"}])
        return _Result([])


class _FakeSupabase:
    def table(self, name):
        return _Query(name)


import lesson_plan_context                                    # noqa: E402
lesson_plan_context.get_lesson_context = lambda t, b: None

import main                                                   # noqa: E402
main.supabase = _FakeSupabase()
main.get_user = lambda request: {"id": "loadtest-user"}

import llm_client                                             # noqa: E402
import LLM_topic_decider                                      # noqa: E402
LLM_topic_decider.supabase = _FakeSupabase()

import requests                                               # noqa: E402
import uvicorn                                                # noqa: E402

BASE = f"http://127.0.0.1:{args.port}"


def _serve():
    uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=args.port,
                                  log_level="error")).run()


def _probe_once(timeout=30):
    t0 = time.monotonic()
    try:
        r = requests.get(f"{BASE}/api/topics", params={"grade": "5"},
                         timeout=timeout)
        return time.monotonic() - t0, r.status_code
    except Exception:
        return time.monotonic() - t0, 0


def _generate(i):
    if args.stagger:
        # Spread arrival across the window rather than firing together: a
        # room of students does not press start on the same millisecond, and
        # the burst is the worst case rather than the expected one.
        time.sleep(args.stagger * i / max(1, args.students - 1))
    t0 = time.monotonic()
    try:
        # A distinct student each, so the per-user rate limit cannot be what
        # refuses them -- this is about the process-wide bounds.
        r = requests.get(f"{BASE}/api/generate-question",
                         params={"user_id": f"kid-{i:03d}",
                                 "grade": args.grade},
                         timeout=180)
        return r.status_code, time.monotonic() - t0
    except Exception as e:
        return f"ERR {type(e).__name__}", time.monotonic() - t0


def _pct(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p / 100))]


def main_():
    threading.Thread(target=_serve, daemon=True).start()
    for _ in range(100):
        try:
            if requests.get(f"{BASE}/api/topics", timeout=1).status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        sys.exit("server did not start")

    print(f"bounds: concurrency={llm_client.GENERATION_MAX_CONCURRENCY} "
          f"waiters={main._GENERATION_MAX_WAITERS} "
          f"budget={llm_client.GENERATION_LLM_TIMEOUT}s "
          f"| {args.students} students, {args.latency}s per model call")

    base = [_probe_once()[0] for _ in range(15)]
    print(f"\nbaseline /api/topics idle: p50 {_pct(base,50)*1000:.0f}ms  "
          f"max {max(base)*1000:.0f}ms")

    probe, stop = [], threading.Event()

    def _probe_loop():
        while not stop.is_set():
            probe.append(_probe_once())
            time.sleep(0.1)

    pt = threading.Thread(target=_probe_loop, daemon=True)
    pt.start()

    from concurrent.futures import ThreadPoolExecutor
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.students) as ex:
        results = list(ex.map(_generate, range(args.students)))
    wall = time.monotonic() - t0
    stop.set()
    pt.join()

    codes = {}
    for c, _ in results:
        codes[c] = codes.get(c, 0) + 1
    lat = [d for _, d in results]
    plat = [d for d, _ in probe]
    pcodes = {c for _, c in probe}

    print(f"\nwall {wall:.1f}s   model calls {_calls['n']}   "
          f"peak concurrent model calls {_concurrent['peak']}")
    print("responses:", ", ".join(f"{k}x{v}" for k, v in sorted(
        codes.items(), key=lambda kv: str(kv[0]))))
    print(f"generation latency: p50 {_pct(lat,50):.1f}s  "
          f"p95 {_pct(lat,95):.1f}s  max {max(lat):.1f}s")
    print(f"/api/topics under load ({len(probe)} probes): "
          f"p50 {_pct(plat,50)*1000:.0f}ms  p95 {_pct(plat,95)*1000:.0f}ms  "
          f"max {max(plat)*1000:.0f}ms  statuses {sorted(pcodes)}")

    ok = True

    def check(name, passed, detail):
        nonlocal ok
        ok &= passed
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}: {detail}")

    print("\nbounds:")
    check("concurrency cap holds",
          _concurrent["peak"] <= llm_client.GENERATION_MAX_CONCURRENCY,
          f"peak {_concurrent['peak']} <= {llm_client.GENERATION_MAX_CONCURRENCY}")
    # The waiter cap *subsumes* the concurrency cap rather than adding to it:
    # `_generation_waiter` wraps the whole call, so it bounds requests
    # in flight, and the semaphore bounds model calls inside that. The
    # ceiling is therefore the waiter cap alone -- 12, not 8 + 12, which is
    # what this check asserted until a 30-student run showed 12 admitted
    # against a ceiling it had computed as 20.
    admitted = sum(v for k, v in codes.items() if k != 503)
    check("requests in flight are bounded by the waiter cap",
          admitted <= main._GENERATION_MAX_WAITERS,
          f"{admitted} admitted, ceiling {main._GENERATION_MAX_WAITERS}")
    check("the rest were refused rather than parked",
          codes.get(503, 0) == max(0, args.students
                                   - main._GENERATION_MAX_WAITERS),
          f"{codes.get(503, 0)} x 503, expected "
          f"{max(0, args.students - main._GENERATION_MAX_WAITERS)}")
    print(f"  [ -- ] served {admitted}/{args.students} "
          f"({100 * admitted / args.students:.0f}% of the class)")
    check("the rest of the API kept answering",
          pcodes == {200}, f"probe statuses {sorted(pcodes)}")
    check("threadpool not starved (probe p95 < 1s)",
          _pct(plat, 95) < 1.0, f"p95 {_pct(plat,95)*1000:.0f}ms")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main_())
