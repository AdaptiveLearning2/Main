"""The solve step is bounded, and a subprocess is what makes that possible.

`parse_expr` evaluates eagerly and the operand comes from the model, so
`9**9**9` -- a number with ~370 million digits -- never returns. Nothing
in-process can stop it: a thread cannot be killed, and a signal is not
delivered while the interpreter is inside a long integer computation.
`GENERATION_LLM_TIMEOUT` bounds the model call and bounded nothing after it.

Measured on this codebase before the fix: expression generation span at 100%
CPU for 28 minutes before being killed by hand. On the inline path -- every
question, with QUESTION_QUEUE_SIZE at 0 -- that is one of anyio's ~40
threadpool slots held until the process restarts.
"""
import os
import time

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import llm_client  # noqa: E402
import safe_solve  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_module_state():
    """`_probe_startup` writes module globals directly, so a test that calls it
    changes what every later test reads. monkeypatch cannot undo a write it did
    not make."""
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
    """The bound must not cost the questions it exists to protect."""
    assert safe_solve.safe_solve(expression, scenario) == expected


def test_an_unbounded_expression_is_killed_rather_than_waited_on():
    """The whole point. A short budget so the suite does not pay the default.

    Asserted as an upper bound on elapsed time, not a lower one: the claim is
    that it *stopped*, and a machine slower than this one must not fail.

    `SolverUnavailable` rather than `None`, and the type is the assertion: a
    kill says nothing about the input, so reporting it the same way as a
    rejected one made a load-dependent timeout read as a bad model reply --
    which is how one showed up as an intermittent failure in an unrelated
    topic's test.
    """
    started = time.monotonic()
    with pytest.raises(safe_solve.SolverUnavailable, match="killed"):
        safe_solve.safe_solve("9**9**9", "evaluate", timeout=3)
    assert time.monotonic() - started < 30, "the bound did not take effect"


def test_an_unknown_scenario_is_refused_rather_than_falling_through():
    """The worker's `match` equivalent has no default branch by accident twice
    over in this codebase -- geometry raised UnboundLocalError the same way."""
    assert safe_solve.safe_solve("2+2", "no_such_scenario") is None


def test_a_result_too_long_to_be_an_answer_is_discarded(monkeypatch):
    """Re-parsing the worker's result happens in the parent, outside the bound,
    so the cap is what keeps that from being the same hazard one step later."""
    monkeypatch.setattr(safe_solve, "MAX_RESULT_CHARS", 3)
    assert safe_solve.safe_solve("123456789*987654321", "evaluate") is None


def test_a_syntactically_invalid_expression_is_none_not_a_raise():
    """Every caller treats None as "retry this question"; a raise would escape
    the retry loop instead."""
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
    """CLAUDE.md requires every numeric setting to go through `_env_number`
    with a floor, and this one did not: it was `float(os.getenv(...))` at
    import, so `SOLVE_TIMEOUT=abc` raised ValueError out of `import main` and
    took the entire backend down over one topic's solver knob.

    `SOLVE_TIMEOUT=0` was the worse half -- it imported cleanly and then failed
    every expression question instantly, which reads as the model being unable
    to write a solvable expression rather than as a misconfiguration.
    """
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
    """The finding this op exists for. `_solve_equation` used to call
    `parse_expr`/`solve` in the request thread, and `9**9**9 + x = 5` never
    returns -- the spin holds the GIL inside CPython's long-integer code, so no
    watchdog thread and no signal handler can interrupt it. Only an external
    kill works, which is what the subprocess provides.
    """
    started = time.monotonic()
    with pytest.raises(safe_solve.SolverUnavailable):
        safe_solve.safe_solve("9**9**9+x=5", "equation", timeout=3)
    assert time.monotonic() - started < 30, "the bound did not take effect"


# ─── geometry, whose `sympify` is the unbounded part ─────────────────────

def test_a_geometry_scenario_solves_in_the_worker():
    assert safe_solve.safe_solve_geometry("cube_volume", {"side": "3"}) == 27.0


def test_an_unbounded_geometry_variable_is_killed_rather_than_waited_on():
    """`_solve_scenario` ran `preprocess_variables` -- `sympify` over the
    model's raw values -- in the request thread, while its own docstring said
    the call was bounded. `{"side": "9**9**9"}` never returns.

    `SCENARIO_VARS` checks that the keys are present, which says nothing about
    the values behind them, and the `try/except` around the dispatch caught
    exceptions rather than non-termination.
    """
    started = time.monotonic()
    with pytest.raises(safe_solve.SolverUnavailable):
        safe_solve.safe_solve_geometry(
        "cube_volume", {"side": "9**9**9"}, timeout=3)
    assert time.monotonic() - started < 30, "the bound did not take effect"


def test_an_unlisted_geometry_scenario_comes_back_none():
    """`solve_scenario`'s `case _`. Unreachable through the generator, which
    checks SOLVABLE_SCENARIOS first -- but the extraction made it a standalone
    unit, so the guarantee now lives in a different file from the check."""
    assert safe_solve.safe_solve_geometry("no_such_scenario", {"a": "1"}) is None


# ─── a refusal has to say which refusal it was ───────────────────────────

@pytest.mark.parametrize("scenario,variables,expected", [
    ("triangle_sum", ["75", "105"], "no such figure"),
    ("triangle_sum", ["!!", "105"], "could not solve"),
    ("nope", ["1"], "no such scenario"),
    ("triangle_sum", ["50"], "needs 2 variable"),
])
def test_an_angle_refusal_carries_its_reason(scenario, variables, expected, capsys):
    """The reason is computed one frame from the worker's only channel back --
    a string -- and was being thrown away for a placeholder.

    A degenerate triangle and an unparseable variable both printed
    `unsolvable scenario`, so the log could not tell "the model wrote a figure
    that does not exist" from "the model wrote something sympy cannot read".
    Those need different fixes.
    """
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
    """The ordinary case: the configured value clears the measured floor, so
    nothing changes and nothing is logged about it."""
    monkeypatch.setattr(safe_solve, "_CONFIGURED_TIMEOUT_S", 30.0)
    monkeypatch.setattr(safe_solve, "SOLVE_TIMEOUT_S", 30.0)
    safe_solve._probe_startup()
    assert safe_solve.SOLVE_TIMEOUT_S == 30.0
    assert safe_solve.STARTUP_COST_S is not None
    assert "below" not in capsys.readouterr().out


def test_a_budget_under_the_measured_floor_is_raised_and_announced(monkeypatch, capsys):
    """The case this exists for.

    `SOLVE_STARTUP_BUDGET` bounds launching Python and importing sympy, which
    is ~99% of an ordinary solve. A machine slower than the one the default was
    measured on would fail *every* question that needs a solve -- and the
    symptom reads as the model being unable to write solvable maths, so nobody
    looks at this setting.

    It used to clamp `SOLVE_TIMEOUT`, because startup was inside that budget.
    It no longer is, so clamping it would protect the wrong number.

    Clamped up rather than refused: raising here would take the whole backend
    down over one topic's tuning knob, and every non-generation surface with
    it.
    """
    # Ask the machine what a solve costs before choosing a budget it should
    # refuse. The first version hardcoded 1.0s as "obviously too small" -- true
    # on the laptop this was written on, where startup is ~0.8s and the floor
    # is ~2.4s, and false on CI, where startup is 0.32s and the floor is 0.97s.
    # So the clamp correctly did not fire and the test failed for asserting it
    # had. A threshold that depends on how fast the machine is has to be
    # derived from that machine, not written down.
    monkeypatch.setattr(safe_solve, "_CONFIGURED_STARTUP_BUDGET_S", 3600.0)
    monkeypatch.setattr(safe_solve, "SOLVE_STARTUP_BUDGET_S", 3600.0)
    safe_solve._probe_startup()
    assert safe_solve.STARTUP_COST_S is not None, "the probe could not measure"
    capsys.readouterr()

    # A tenth of the floor, not half: the assertion below re-measures, and two
    # probes on a noisy runner differ. A tenth cannot be crossed by variance.
    too_small = (safe_solve.STARTUP_COST_S
                 * safe_solve._STARTUP_SAFETY_FACTOR / 10)
    monkeypatch.setattr(safe_solve, "_CONFIGURED_STARTUP_BUDGET_S", too_small)
    monkeypatch.setattr(safe_solve, "SOLVE_STARTUP_BUDGET_S", too_small)
    safe_solve._probe_startup()

    # Against the second probe's own measurement, since that is the one the
    # clamp used.
    floor = safe_solve.STARTUP_COST_S * safe_solve._STARTUP_SAFETY_FACTOR
    assert safe_solve.SOLVE_STARTUP_BUDGET_S == pytest.approx(floor)
    assert safe_solve.SOLVE_STARTUP_BUDGET_S > too_small
    out = capsys.readouterr().out
    assert "is below" in out
    assert "Raised to" in out


def test_a_probe_that_cannot_run_keeps_the_configured_value(monkeypatch, capsys):
    """A failed measurement is not a reason to invent a budget -- it means the
    subprocess mechanism is broken, which is a different problem and gets its
    own line."""
    monkeypatch.setattr(safe_solve, "_CONFIGURED_STARTUP_BUDGET_S", 3.0)
    monkeypatch.setattr(safe_solve, "SOLVE_STARTUP_BUDGET_S", 3.0)
    monkeypatch.setattr(safe_solve, "STARTUP_COST_S", None)
    monkeypatch.setattr(safe_solve, "_WORKER", "no_such_worker.py")
    safe_solve._probe_startup()

    assert safe_solve.SOLVE_STARTUP_BUDGET_S == 3.0
    assert safe_solve.STARTUP_COST_S is None
    assert "startup probe failed" in capsys.readouterr().out


def test_the_probe_runs_at_import_and_leaves_the_budget_above_the_floor():
    """Two things the tests above cannot cover, because they call
    `_probe_startup()` themselves.

    First, that a normal process gets the guard **without asking**: delete the
    import-time call and every other test here still passes, while generation
    silently loses its floor.

    Second, the post-condition, with no escape clause. The version this
    replaces read `budget >= floor or budget == configured`, which cannot
    fail: the clamp sets the budget to exactly `floor`, so the first disjunct
    holds whenever it fired and the second holds whenever it did not. It
    asserted the disjunction of "clamped" and "not clamped". Dropping the
    second half is what makes it able to fail
    -- a clamp that computed the wrong value, or forgot to assign, now shows
    up here.

    This is the second tautological test written in this work; the first
    passed the expected answer in as an argument. Both looked like assertions
    about behaviour and were assertions about arithmetic.
    """
    if not safe_solve._startup_probe_enabled():
        pytest.skip("SOLVE_STARTUP_PROBE is off, so no floor was measured")
    assert safe_solve.STARTUP_COST_S is not None, (
        "the import-time probe did not run, so nothing measured the floor")
    floor = safe_solve.STARTUP_COST_S * safe_solve._STARTUP_SAFETY_FACTOR
    # The *startup* budget, since that is what now covers the import the floor
    # is measured from. `SOLVE_TIMEOUT` deliberately no longer has to clear it
    # -- severing that is the whole point of the two-phase protocol.
    assert safe_solve.SOLVE_STARTUP_BUDGET_S >= floor


def test_with_the_probe_off_the_configured_value_stands_unchecked():
    """The honest description of `SOLVE_STARTUP_PROBE=0`: the budget is used
    exactly as configured and nothing has verified it is large enough.

    Worth its own test because the skip above must not be read as "the
    invariant holds here too". It does not -- that is the trade the switch
    makes, and `SOLVE_TIMEOUT=1` with the probe off is precisely the
    configuration the probe exists to catch.
    """
    if safe_solve._startup_probe_enabled():
        pytest.skip("the probe is on; the case under test is it being off")
    assert safe_solve.STARTUP_COST_S is None
    assert safe_solve.SOLVE_TIMEOUT_S == safe_solve._CONFIGURED_TIMEOUT_S


def test_a_solver_that_cannot_run_is_not_reported_as_a_bad_reply():
    """Five things used to return `None` and only one was the model's fault.

    Collapsed, a load-dependent timeout retried three times -- billing a model
    call each time that could not possibly help -- and then raised "Failed to
    generate valid JSON after retries", a claim about the model for a
    subprocess that never ran. That misdiagnosis is the reason for the type:
    an intermittent timeout under suite load reads as a defect in whichever
    topic drew the short straw.
    """
    assert issubclass(safe_solve.SolverUnavailable, llm_client.GenerationUnavailable), (
        "it must reach a student as the 503 that already means 'cannot serve "
        "right now', not as a 500")


def test_a_worker_that_ran_and_rejected_the_input_still_returns_none():
    """The teeth. Raising on everything would make the distinction useless --
    a reply the worker read and refused *is* the model's fault, and must stay a
    retry."""
    assert safe_solve.safe_sympify_values(["not a number"]) is None
    assert safe_solve.safe_solve("2+2", "no_such_scenario") is None


def test_the_startup_probe_never_propagates_it(monkeypatch, capsys):
    """`_probe_startup` runs at import, so a raise escaping it takes the whole
    backend down -- ingest, dashboards and consent with it -- over one topic's
    tuning knob. That is the one thing its docstring says it may not do, and
    making `_run` raise nearly did it.
    """
    def _cannot_run(*a, **k):
        raise safe_solve.SolverUnavailable("the solver could not be started")

    monkeypatch.setattr(safe_solve, "_run", _cannot_run)
    monkeypatch.setattr(safe_solve, "SOLVE_TIMEOUT_S", 3.0)
    safe_solve._probe_startup()          # must not raise
    assert "could not run" in capsys.readouterr().out
    # And the configured budget is left alone, not guessed at from a failure.
    assert safe_solve.SOLVE_TIMEOUT_S == 3.0


@pytest.mark.parametrize("expression", ["1/0", "0/0", "1/0 + 2", "(3-3)/(3-3)"])
def test_a_division_by_zero_is_not_an_answer(expression):
    """The `evaluate`/`simplify` branch was the one place in the worker with no
    usability check, unlike every sibling here and in the two solver modules.

    `1/0` came back as the string "zoo" and `0/0` as "nan". `rationals` served
    them: `correct_answer='zoo'` among the options. `expressions` raised
    `TypeError: Cannot convert complex to float` building distractors -- a 500,
    since it is past the point where a retry is possible.
    """
    assert safe_solve.safe_solve(expression, "evaluate") is None


@pytest.mark.parametrize("expression,expected", [
    ("2*x + 3*x", "5*x"),
    ("x + x + y", "2*x + y"),
])
def test_a_symbolic_result_is_still_an_answer(expression, expected):
    """The teeth, and the reason `is_number` cannot be the check: `simplify`
    exists to return `5*x`. Rejecting anything non-numeric would refuse every
    question that scenario is for.

    `is_finite is False` alone is not the check either -- **`nan.is_finite` is
    None, not False** -- which is why the guard tests for the symbols as well.
    """
    assert safe_solve.safe_solve(expression, "simplify") == expected


def test_a_caller_blocks_when_every_worker_slot_is_taken(monkeypatch):
    """The bound's teeth, asserted without threads.

    The budget covers the whole child process and nearly all of it is sympy
    startup, so concurrent solves contend for CPU and the budget collapses for
    all of them together rather than degrading. Measured unbounded on this
    machine: 16 concurrent all succeeded, 32 gave 7 of 32, 48 gave 0 of 48 --
    and since a solve failure became `SolverUnavailable`, that took every
    solve-backed topic down as a 503 with no retry.

    `GENERATION_MAX_CONCURRENCY` does not cover this: a solve is not a model
    call, and prefetch, the inline path and practice all reach here
    independently.

    Holding the permits directly rather than measuring a peak across threads.
    A peak is not deterministic -- an earlier version of this test passed
    against a build with the bound removed, because the pool happened to start
    its threads in groups no larger than the bound. What is deterministic is
    that a caller finding no free slot is refused rather than run.
    """
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

    # And once a permit is free it runs again, so the refusal was the bound
    # rather than something broken.
    assert safe_solve.safe_sympify_values(["1"]) == [1.0]


# The seam the timeout tests fake.
#
# They used to patch `subprocess.run`, which `_run` no longer calls: the
# two-phase protocol needs `Popen` plus a reader, so the timeout is raised by
# `_await_answer` instead. Faking `_spawn` too keeps these tests from starting
# a real interpreter apiece.
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
    """The point of the whole two-phase protocol.

    `SOLVE_TIMEOUT` used to cover launching Python and importing sympy as well
    as the arithmetic, and startup is ~99% of an ordinary solve -- so it was a
    bound on *startup plus a little*, and any co-tenant on the machine spent
    it. Measured before: 8 of 8 solves killed at 3.0s under CPU hogs, on
    arithmetic that takes ~10ms. After: 8 of 8 succeeded.

    Asserted as the invariant rather than by timing, which would be an
    elapsed-time threshold on a loaded runner -- the thing this file already
    learned not to write. A solve budget far below the measured startup cost
    must still solve, which is impossible unless startup is outside it.
    """
    assert safe_solve.STARTUP_COST_S is not None, "the probe could not measure"
    tight = safe_solve.STARTUP_COST_S / 4
    assert safe_solve.safe_solve("2+3", "evaluate", timeout=tight) == "5"


def test_the_readiness_marker_is_the_same_word_on_both_sides():
    """The parent reads the worker's first line and decides whether it is a
    readiness marker or a final answer. A rename on one side turns every solve
    into "unreadable output", so the two constants are pinned together rather
    than trusted to stay in step."""
    import _solve_worker
    assert safe_solve._READY == _solve_worker.READY


def test_a_worker_that_answers_and_lingers_is_not_treated_as_a_failure(monkeypatch):
    """A child that has printed its answer has not necessarily exited: it
    still has to flush and tear the interpreter down. The first version killed
    it on a bare `poll() is None`, which made *every* successful solve report
    a non-zero exit code -- caught only because the smoke test ran before the
    unit tests did.
    """
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
    # On `killed`, not on the recorded wait. `_kill` waits too, and with the
    # same 5s -- so `waits == [_EXIT_GRACE_S]` held whether the child was given
    # its grace period or shot immediately, and the test passed against its own
    # mutation. Two code paths that agree on a number cannot be told apart by
    # that number.
    assert not proc.killed, (
        "the child is given a grace period to exit, not killed on the spot")
    assert proc.waits == [safe_solve._EXIT_GRACE_S]


def test_the_probe_is_not_bounded_by_the_budget_it_measures(monkeypatch):
    """Circular, and it bit: the probe runs a solve, and once startup had its
    own budget that solve was bounded by the very setting the probe exists to
    validate. A too-small `SOLVE_STARTUP_BUDGET` therefore made the probe time
    out reporting the problem it should have measured, and the clamp that
    would have fixed it never ran.

    `_PROBE_TIMEOUT_S` is documented as deliberately unrelated to the setting
    it validates; this is what makes that true of both phases.
    """
    seen = {}

    def _record(request, timeout, label, startup_timeout=None):
        seen["startup"] = startup_timeout
        return None

    monkeypatch.setattr(safe_solve, "_run", _record)
    monkeypatch.setattr(safe_solve, "SOLVE_STARTUP_BUDGET_S", 0.001)
    safe_solve._probe_startup()
    assert seen["startup"] == safe_solve._PROBE_TIMEOUT_S


def test_a_permit_is_returned_even_when_the_solve_fails(monkeypatch):
    """A `BoundedSemaphore` acquired with a timeout turns a leaked permit into
    solving being off for the life of the process, and the leaking path is the
    one most likely to run under load. The context manager releases in a
    `finally` for the reason `llm_client._generation_waiter` does."""
    def _always_times_out(proc, stdout, startup_budget, solve_budget):
        raise safe_solve._Timeout("the solve", solve_budget)

    _fake_worker(monkeypatch, _always_times_out)
    for _ in range(safe_solve.SOLVE_MAX_CONCURRENCY * 2):
        with pytest.raises(safe_solve.SolverUnavailable):
            safe_solve.safe_sympify_values(["1"])

    # If permits leaked, this real solve would refuse rather than answer.
    monkeypatch.undo()
    assert safe_solve.safe_sympify_values(["1"]) == [1.0]


def test_a_timeout_gets_one_more_attempt_with_a_wider_budget(monkeypatch):
    """`SOLVE_MAX_CONCURRENCY` bounds *this process's* workers and nothing
    else. The budget is wall-clock over a child dominated by sympy startup, so
    any co-tenant on the machine spends it: reproduced with the frontend suite
    alongside, and again with CPU hogs -- 8 of 8 solves killed at 3.0s. Ten of
    fourteen topics reach the worker, and since a timeout raises rather than
    returning None, every one was a 503 on the first attempt.

    Retrying separates the two things a timeout can mean. A genuine spin is
    still spinning on the second attempt and is killed again; contention is a
    property of the moment, and the moment passes. Measured at 18 cores: at
    saturation the first attempt failed 6 of 6 and the retry rescued all 6.
    """
    calls = []

    def _slow_then_fine(proc, stdout, startup_budget, solve_budget):
        calls.append(solve_budget)
        if len(calls) == 1:
            raise safe_solve._Timeout("the solve", solve_budget)
        return '{"ok": true, "result": "[1.0]"}', "the solve"

    _fake_worker(monkeypatch, _slow_then_fine)
    assert safe_solve.safe_sympify_values(["1"]) == [1.0]
    assert len(calls) == 2, "a timeout must cost a second attempt, not a 503"
    assert calls[1] > calls[0], (
        "the retry is given room: the first timeout is itself the evidence "
        "that this machine is slower than the budget assumed")


def test_a_spin_that_survives_both_attempts_still_refuses(monkeypatch):
    """The teeth. Retrying for ever would reinstate the hang this module
    exists to bound, so the count is two and the refusal still names the
    cause."""
    calls = []

    def _always_slow(proc, stdout, startup_budget, solve_budget):
        calls.append(solve_budget)
        raise safe_solve._Timeout("the solve", solve_budget)

    _fake_worker(monkeypatch, _always_slow)
    with pytest.raises(safe_solve.SolverUnavailable, match="after 2 attempts"):
        safe_solve.safe_sympify_values(["1"])
    assert len(calls) == 2, "bounded at two attempts"


def test_the_retry_does_not_hold_a_concurrency_permit_while_it_waits(monkeypatch):
    """Taken per attempt rather than around both. Holding one through a wait
    that has already failed shrinks the effective concurrency exactly when the
    machine is busiest, which is when the retry exists."""
    depth = []

    def _timeout_once(proc, stdout, startup_budget, solve_budget):
        # How many permits are outstanding while this attempt runs.
        free = 0
        while safe_solve._solve_slots.acquire(blocking=False):
            free += 1
        for _ in range(free):
            safe_solve._solve_slots.release()
        depth.append(safe_solve.SOLVE_MAX_CONCURRENCY - free)
        raise safe_solve._Timeout("the solve", solve_budget)

    _fake_worker(monkeypatch, _timeout_once)
    with pytest.raises(safe_solve.SolverUnavailable):
        safe_solve.safe_sympify_values(["1"])
    assert depth == [1, 1], (
        f"one permit held per attempt, not accumulated: {depth}")
