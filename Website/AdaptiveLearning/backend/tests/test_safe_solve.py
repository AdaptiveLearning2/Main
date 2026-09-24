"""The solve step is bounded by a killable subprocess; see docs/question-generation.md."""
import os
import time

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import llm_client  # noqa: E402
import safe_solve  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_module_state():
    """`_probe_startup` writes module globals directly, which monkeypatch cannot undo."""
    saved = (safe_solve.SOLVE_TIMEOUT_S, safe_solve.STARTUP_COST_S,
             safe_solve._CONFIGURED_TIMEOUT_S)
    yield
    (safe_solve.SOLVE_TIMEOUT_S, safe_solve.STARTUP_COST_S,
     safe_solve._CONFIGURED_TIMEOUT_S) = saved


@pytest.mark.parametrize("expression,scenario,expected", [
    ("2*(15+8)-9", "evaluate", "37"),
    ("(4+6)*3-5", "order_of_operations", "25"),
    ("2x+3x", "simplify", "5*x"),
])
def test_ordinary_expressions_still_solve(expression, scenario, expected):
    assert safe_solve.safe_solve(expression, scenario) == expected


def test_an_unbounded_expression_is_killed_rather_than_waited_on():
    """`SolverUnavailable`, not `None`: a kill says nothing about the input."""
    started = time.monotonic()
    with pytest.raises(safe_solve.SolverUnavailable, match="killed"):
        safe_solve.safe_solve("9**9**9", "evaluate", timeout=3)
    assert time.monotonic() - started < 30, "the bound did not take effect"


def test_an_unknown_scenario_is_refused_rather_than_falling_through():
    assert safe_solve.safe_solve("2+2", "no_such_scenario") is None


def test_a_result_too_long_to_be_an_answer_is_discarded(monkeypatch):
    """The parent re-parses the result outside the bound, so the cap is its bound."""
    monkeypatch.setattr(safe_solve, "MAX_RESULT_CHARS", 3)
    assert safe_solve.safe_solve("123456789*987654321", "evaluate") is None


def test_a_syntactically_invalid_expression_is_none_not_a_raise():
    """Callers treat None as "retry this question"; a raise would escape the retry loop."""
    assert safe_solve.safe_solve("2 +* 3", "evaluate") is None


@pytest.mark.parametrize("value,expected", [
    ("abc", 10.0),   # unparseable -- falls back rather than killing the import
    ("", 10.0),      # empty
    ("0", 1.0),      # below the floor -- clamped, not honoured
    ("0.1", 1.0),    # ditto
    ("30", 30.0),    # a real value still works
])
def test_the_timeout_is_read_through_env_number_with_a_floor(value, expected,
                                                             monkeypatch):
    import llm_client
    monkeypatch.setenv("SOLVE_TIMEOUT", value)
    assert llm_client._env_number("SOLVE_TIMEOUT", 10.0, float,
                                  minimum=1.0) == expected


# ─── the equation op, which algebra runs entirely in the worker ──────────

@pytest.mark.parametrize("equation,expected", [
    ("2*x+3=11", "4"),
    ("x+7=15", "8"),
])
def test_an_ordinary_equation_solves_in_the_worker(equation, expected):
    assert safe_solve.safe_solve(equation, "equation") == expected


@pytest.mark.parametrize("equation,why", [
    ("x+1=x+2", "no solution"),
    ("x**2=4", "two roots -- this topic scores exactly one"),
    ("2*x+3", "no equals sign"),
    ("1=x=2", "two equals signs"),
    ("a+b=c", "solution is not a number"),
])
def test_an_unscorable_equation_comes_back_none(equation, why):
    assert safe_solve.safe_solve(equation, "equation") is None, why


def test_an_unbounded_equation_is_killed_rather_than_waited_on():
    """The spin holds the GIL, so only an external kill can stop it."""
    started = time.monotonic()
    with pytest.raises(safe_solve.SolverUnavailable):
        safe_solve.safe_solve("9**9**9+x=5", "equation", timeout=3)
    assert time.monotonic() - started < 30, "the bound did not take effect"


# ─── geometry, whose `sympify` is the unbounded part ─────────────────────

def test_a_geometry_scenario_solves_in_the_worker():
    assert safe_solve.safe_solve_geometry("cube_volume", {"side": "3"}) == 27.0


def test_an_unbounded_geometry_variable_is_killed_rather_than_waited_on():
    """`sympify` over the model's raw values; `SCENARIO_VARS` checks keys, not values."""
    started = time.monotonic()
    with pytest.raises(safe_solve.SolverUnavailable):
        safe_solve.safe_solve_geometry(
        "cube_volume", {"side": "9**9**9"}, timeout=3)
    assert time.monotonic() - started < 30, "the bound did not take effect"


def test_an_unlisted_geometry_scenario_comes_back_none():
    """`solve_scenario`'s `case _`; the generator's SOLVABLE_SCENARIOS check is in another file."""
    assert safe_solve.safe_solve_geometry("no_such_scenario", {"a": "1"}) is None


# ─── a refusal has to say which refusal it was ───────────────────────────

@pytest.mark.parametrize("scenario,variables,expected", [
    ("triangle_sum", ["75", "105"], "no such figure"),
    ("triangle_sum", ["!!", "105"], "could not solve"),
    ("nope", ["1"], "no such scenario"),
    ("triangle_sum", ["50"], "needs 2 variable"),
])
def test_an_angle_refusal_carries_its_reason(scenario, variables, expected, capsys):
    """A degenerate figure and an unparseable variable need different fixes, so the log says which."""
    assert safe_solve.safe_solve_angle(scenario, variables) is None
    assert expected in capsys.readouterr().out


@pytest.mark.parametrize("scenario,variables,expected", [
    ("pythagorean", {"a": "3"}, "missing variables"),
    ("cube_volume", {"side": "1e200"}, "non-finite"),
    ("cube_volume", {"side": "!!"}, "could not solve"),
    ("nope", {"side": "3"}, "no such scenario"),
])
def test_a_geometry_refusal_carries_its_reason(scenario, variables, expected, capsys):
    assert safe_solve.safe_solve_geometry(scenario, variables) is None
    assert expected in capsys.readouterr().out


# ─── the startup probe ───────────────────────────────────────────────────

def test_the_probe_measures_and_leaves_a_sufficient_budget_alone(monkeypatch, capsys):
    monkeypatch.setattr(safe_solve, "_CONFIGURED_TIMEOUT_S", 30.0)
    monkeypatch.setattr(safe_solve, "SOLVE_TIMEOUT_S", 30.0)
    safe_solve._probe_startup()
    assert safe_solve.SOLVE_TIMEOUT_S == 30.0
    assert safe_solve.STARTUP_COST_S is not None
    assert "below" not in capsys.readouterr().out


def test_a_budget_under_the_measured_floor_is_raised_and_announced(monkeypatch, capsys):
    """Clamped up rather than refused: raising at import would take the whole backend down."""
    # The floor depends on this machine's speed, so measure it first.
    monkeypatch.setattr(safe_solve, "_CONFIGURED_STARTUP_BUDGET_S", 3600.0)
    monkeypatch.setattr(safe_solve, "SOLVE_STARTUP_BUDGET_S", 3600.0)
    safe_solve._probe_startup()
    assert safe_solve.STARTUP_COST_S is not None, "the probe could not measure"
    capsys.readouterr()

    # A tenth, not half: the re-measure below varies, and cannot cross a tenth.
    too_small = (safe_solve.STARTUP_COST_S
                 * safe_solve._STARTUP_SAFETY_FACTOR / 10)
    monkeypatch.setattr(safe_solve, "_CONFIGURED_STARTUP_BUDGET_S", too_small)
    monkeypatch.setattr(safe_solve, "SOLVE_STARTUP_BUDGET_S", too_small)
    safe_solve._probe_startup()

    # Against the second probe's measurement, which the clamp used.
    floor = safe_solve.STARTUP_COST_S * safe_solve._STARTUP_SAFETY_FACTOR
    assert safe_solve.SOLVE_STARTUP_BUDGET_S == pytest.approx(floor)
    assert safe_solve.SOLVE_STARTUP_BUDGET_S > too_small
    out = capsys.readouterr().out
    assert "is below" in out
    assert "Raised to" in out


def test_a_probe_that_cannot_run_keeps_the_configured_value(monkeypatch, capsys):
    """A failed measurement means the subprocess is broken; it gets its own log line."""
    monkeypatch.setattr(safe_solve, "_CONFIGURED_STARTUP_BUDGET_S", 3.0)
    monkeypatch.setattr(safe_solve, "SOLVE_STARTUP_BUDGET_S", 3.0)
    monkeypatch.setattr(safe_solve, "STARTUP_COST_S", None)
    monkeypatch.setattr(safe_solve, "_WORKER", "no_such_worker.py")
    safe_solve._probe_startup()

    assert safe_solve.SOLVE_STARTUP_BUDGET_S == 3.0
    assert safe_solve.STARTUP_COST_S is None
    assert "startup probe failed" in capsys.readouterr().out


def test_the_probe_runs_at_import_and_leaves_the_budget_above_the_floor():
    """The import-time call, and the post-condition with no `or budget == configured` escape."""
    if not safe_solve._startup_probe_enabled():
        pytest.skip("SOLVE_STARTUP_PROBE is off, so no floor was measured")
    assert safe_solve.STARTUP_COST_S is not None, (
        "the import-time probe did not run, so nothing measured the floor")
    floor = safe_solve.STARTUP_COST_S * safe_solve._STARTUP_SAFETY_FACTOR
    # The startup budget covers the import; `SOLVE_TIMEOUT` deliberately need not clear it.
    assert safe_solve.SOLVE_STARTUP_BUDGET_S >= floor


def test_with_the_probe_off_the_configured_value_stands_unchecked():
    """With the probe off, nothing verifies the budget; the skip above is not "it holds here too"."""
    if safe_solve._startup_probe_enabled():
        pytest.skip("the probe is on; the case under test is it being off")
    assert safe_solve.STARTUP_COST_S is None
    assert safe_solve.SOLVE_TIMEOUT_S == safe_solve._CONFIGURED_TIMEOUT_S


def test_a_solver_that_cannot_run_is_not_reported_as_a_bad_reply():
    """A timeout reported as `None` would be retried as a bad model reply."""
    assert issubclass(safe_solve.SolverUnavailable, llm_client.GenerationUnavailable), (
        "it must reach a student as the 503 that already means 'cannot serve "
        "right now', not as a 500")


def test_a_worker_that_ran_and_rejected_the_input_still_returns_none():
    """A reply the worker read and refused is the model's fault, so it stays a retry."""
    assert safe_solve.safe_sympify_values(["not a number"]) is None
    assert safe_solve.safe_solve("2+2", "no_such_scenario") is None


def test_the_startup_probe_never_propagates_it(monkeypatch, capsys):
    """`_probe_startup` runs at import, so a raise would take the whole backend down."""
    def _cannot_run(*a, **k):
        raise safe_solve.SolverUnavailable("the solver could not be started")

    monkeypatch.setattr(safe_solve, "_run", _cannot_run)
    monkeypatch.setattr(safe_solve, "SOLVE_TIMEOUT_S", 3.0)
    safe_solve._probe_startup()          # must not raise
    assert "could not run" in capsys.readouterr().out
    # The configured budget is left alone.
    assert safe_solve.SOLVE_TIMEOUT_S == 3.0


@pytest.mark.parametrize("expression", ["1/0", "0/0", "1/0 + 2", "(3-3)/(3-3)"])
def test_a_division_by_zero_is_not_an_answer(expression):
    """`1/0` is sympy's "zoo" and `0/0` is "nan"; neither may become a `correct_answer`."""
    assert safe_solve.safe_solve(expression, "evaluate") is None


@pytest.mark.parametrize("expression,expected", [
    ("2*x + 3*x", "5*x"),
    ("x + x + y", "2*x + y"),
])
def test_a_symbolic_result_is_still_an_answer(expression, expected):
    """So `is_number` cannot be the check; nor `is_finite is False`, since `nan.is_finite` is None."""
    assert safe_solve.safe_solve(expression, "simplify") == expected


def test_a_caller_blocks_when_every_worker_slot_is_taken(monkeypatch):
    """Holds the permits directly: a peak measured across threads is not deterministic."""
    monkeypatch.setattr(safe_solve, "SOLVE_QUEUE_TIMEOUT_S", 0.1)
    held = [safe_solve._solve_slots.acquire(blocking=False)
            for _ in range(safe_solve.SOLVE_MAX_CONCURRENCY)]
    assert all(held), "the semaphore holds fewer permits than it claims"
    try:
        assert not safe_solve._solve_slots.acquire(blocking=False), (
            "it holds more permits than the bound")
        with pytest.raises(safe_solve.SolverUnavailable, match="no solver slot"):
            safe_solve.safe_sympify_values(["1"])
    finally:
        for _ in held:
            safe_solve._solve_slots.release()

    # Once a permit is free it runs again, so the refusal was the bound.
    assert safe_solve.safe_sympify_values(["1"]) == [1.0]


# The timeout tests fake `_spawn` and `_await_answer`, so no real interpreter starts.
class _FakeProc:
    returncode = 0

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class _FakePipe:
    def text(self):
        return ""


def _fake_worker(monkeypatch, on_await):
    monkeypatch.setattr(safe_solve, "_spawn",
                        lambda request: (_FakeProc(), _FakePipe(), _FakePipe()))
    monkeypatch.setattr(safe_solve, "_await_answer", on_await)


def test_the_solve_budget_no_longer_pays_for_starting_the_worker():
    """A solve budget below the startup cost still solves only if startup is outside it."""
    assert safe_solve.STARTUP_COST_S is not None, "the probe could not measure"
    tight = safe_solve.STARTUP_COST_S / 4
    assert safe_solve.safe_solve("2+3", "evaluate", timeout=tight) == "5"


def test_the_readiness_marker_is_the_same_word_on_both_sides():
    """A rename on one side would turn every solve into "unreadable output"."""
    import _solve_worker
    assert safe_solve._READY == _solve_worker.READY


def test_a_worker_that_answers_and_lingers_is_not_treated_as_a_failure(monkeypatch):
    """A child that printed its answer may still be flushing and tearing down."""
    class _Lingering(_FakeProc):
        def __init__(self):
            self.waits, self.killed = [], False

        def wait(self, timeout=None):
            self.waits.append(timeout)
            return 0

        def kill(self):
            self.killed = True

    proc = _Lingering()
    monkeypatch.setattr(safe_solve, "_spawn",
                        lambda request: (proc, _FakePipe(), _FakePipe()))
    monkeypatch.setattr(
        safe_solve, "_await_answer",
        lambda *a: ('{"ok": true, "result": "[1.0]"}', "the solve"))
    assert safe_solve.safe_sympify_values(["1"]) == [1.0]
    # Assert on `killed`: `_kill` waits the same 5s, so the wait alone can't tell them apart.
    assert not proc.killed, (
        "the child is given a grace period to exit, not killed on the spot")
    assert proc.waits == [safe_solve._EXIT_GRACE_S]


def test_an_answer_survives_having_to_kill_the_worker_that_gave_it(monkeypatch):
    """Our own kill after the grace wait sets a non-zero exit code; the answer in hand stands."""
    class _Stuck(_FakeProc):
        returncode = -9                 # what killing it leaves behind

        def __init__(self):
            self.killed = False

        def wait(self, timeout=None):
            if timeout == safe_solve._EXIT_GRACE_S:
                raise safe_solve.subprocess.TimeoutExpired(cmd="x",
                                                           timeout=timeout)
            return -9

        def kill(self):
            self.killed = True

    proc = _Stuck()
    monkeypatch.setattr(safe_solve, "_spawn",
                        lambda request: (proc, _FakePipe(), _FakePipe()))
    monkeypatch.setattr(
        safe_solve, "_await_answer",
        lambda *a: ('{"ok": true, "result": "[1.0]"}', "the solve"))

    assert safe_solve.safe_sympify_values(["1"]) == [1.0], (
        "the answer was in hand before the kill and is still good")
    assert proc.killed, "and the process was not leaked"


def test_a_worker_that_dies_without_answering_is_still_a_failure(monkeypatch):
    """The exemption above covers only our kill after an answer, not non-zero exits generally."""
    class _Crashed(_FakeProc):
        returncode = 1

    monkeypatch.setattr(safe_solve, "_spawn",
                        lambda request: (_Crashed(), _FakePipe(), _FakePipe()))
    monkeypatch.setattr(safe_solve, "_await_answer",
                        lambda *a: (None, "startup"))
    with pytest.raises(safe_solve.SolverUnavailable, match="exited 1"):
        safe_solve.safe_sympify_values(["1"])


def test_a_spin_is_not_retried_but_contention_is(monkeypatch):
    """A solve timeout is a genuine spin; a startup timeout is transient contention."""
    def _timeout_in(phase):
        attempts = []

        def _fake(proc, stdout, startup_budget, solve_budget):
            attempts.append(phase)
            raise safe_solve._Timeout(
                phase, startup_budget if phase == "startup" else solve_budget)

        return attempts, _fake

    solve_attempts, on_solve = _timeout_in("the solve")
    _fake_worker(monkeypatch, on_solve)
    with pytest.raises(safe_solve.SolverUnavailable, match="1 attempt"):
        safe_solve.safe_sympify_values(["1"])
    assert len(solve_attempts) == 1, "a spin is not worth a second process"

    startup_attempts, on_startup = _timeout_in("startup")
    _fake_worker(monkeypatch, on_startup)
    with pytest.raises(safe_solve.SolverUnavailable, match="2 attempt"):
        safe_solve.safe_sympify_values(["1"])
    assert len(startup_attempts) == 2, "contention gets one more go"


def test_the_probe_is_not_bounded_by_the_budget_it_measures(monkeypatch):
    """A too-small `SOLVE_STARTUP_BUDGET` must not time out the probe that would clamp it."""
    seen = {}

    def _record(request, timeout, label, startup_timeout=None):
        seen["startup"] = startup_timeout
        return None

    monkeypatch.setattr(safe_solve, "_run", _record)
    monkeypatch.setattr(safe_solve, "SOLVE_STARTUP_BUDGET_S", 0.001)
    safe_solve._probe_startup()
    assert seen["startup"] == safe_solve._PROBE_TIMEOUT_S


def test_a_permit_is_returned_even_when_the_solve_fails(monkeypatch):
    """A leaked permit turns solving off for the life of the process."""
    def _always_times_out(proc, stdout, startup_budget, solve_budget):
        raise safe_solve._Timeout("the solve", solve_budget)

    _fake_worker(monkeypatch, _always_times_out)
    for _ in range(safe_solve.SOLVE_MAX_CONCURRENCY * 2):
        with pytest.raises(safe_solve.SolverUnavailable):
            safe_solve.safe_sympify_values(["1"])

    # If permits leaked, this real solve would refuse rather than answer.
    monkeypatch.undo()
    assert safe_solve.safe_sympify_values(["1"]) == [1.0]


def test_contention_that_survives_both_attempts_still_refuses(monkeypatch):
    """Retrying for ever would reinstate the hang; startup is the phase that retries."""
    calls = []

    def _always_slow(proc, stdout, startup_budget, solve_budget):
        calls.append(startup_budget)
        raise safe_solve._Timeout("startup", startup_budget)

    _fake_worker(monkeypatch, _always_slow)
    with pytest.raises(safe_solve.SolverUnavailable, match="after 2 attempt"):
        safe_solve.safe_sympify_values(["1"])
    assert len(calls) == 2, "bounded at two attempts"


def test_the_retry_does_not_hold_a_concurrency_permit_while_it_waits(monkeypatch):
    """Taken per attempt, so a failed wait does not shrink concurrency when the machine is busiest."""
    depth = []

    def _timeout_once(proc, stdout, startup_budget, solve_budget):
        # How many permits are outstanding while this attempt runs.
        free = 0
        while safe_solve._solve_slots.acquire(blocking=False):
            free += 1
        for _ in range(free):
            safe_solve._solve_slots.release()
        depth.append(safe_solve.SOLVE_MAX_CONCURRENCY - free)
        # Startup: the phase that retries.
        raise safe_solve._Timeout("startup", startup_budget)

    _fake_worker(monkeypatch, _timeout_once)
    with pytest.raises(safe_solve.SolverUnavailable):
        safe_solve.safe_sympify_values(["1"])
    assert depth == [1, 1], (
        f"one permit held per attempt, not accumulated: {depth}")
