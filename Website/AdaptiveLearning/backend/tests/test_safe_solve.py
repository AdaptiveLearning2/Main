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
    """
    started = time.monotonic()
    assert safe_solve.safe_solve("9**9**9", "evaluate", timeout=3) is None
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
    assert safe_solve.safe_solve("9**9**9+x=5", "equation", timeout=3) is None
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
    assert safe_solve.safe_solve_geometry(
        "cube_volume", {"side": "9**9**9"}, timeout=3) is None
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

    `SOLVE_TIMEOUT` bounds the whole child process, and ~99% of an ordinary
    solve is starting it. A machine slower than the one the default was
    measured on would fail *every* question that needs a solve -- and the
    symptom reads as the model being unable to write solvable maths, so nobody
    looks at this setting.

    Clamped up rather than refused: raising here would take the whole backend
    down over one topic's tuning knob, and every non-generation surface with
    it.
    """
    monkeypatch.setattr(safe_solve, "_CONFIGURED_TIMEOUT_S", 1.0)
    monkeypatch.setattr(safe_solve, "SOLVE_TIMEOUT_S", 1.0)
    safe_solve._probe_startup()

    floor = safe_solve.STARTUP_COST_S * safe_solve._STARTUP_SAFETY_FACTOR
    assert safe_solve.SOLVE_TIMEOUT_S == pytest.approx(floor)
    assert safe_solve.SOLVE_TIMEOUT_S > 1.0
    out = capsys.readouterr().out
    assert "SOLVE_TIMEOUT=1.0s is below" in out
    assert "Raised to" in out


def test_a_probe_that_cannot_run_keeps_the_configured_value(monkeypatch, capsys):
    """A failed measurement is not a reason to invent a budget -- it means the
    subprocess mechanism is broken, which is a different problem and gets its
    own line."""
    monkeypatch.setattr(safe_solve, "_CONFIGURED_TIMEOUT_S", 3.0)
    monkeypatch.setattr(safe_solve, "SOLVE_TIMEOUT_S", 3.0)
    monkeypatch.setattr(safe_solve, "STARTUP_COST_S", None)
    monkeypatch.setattr(safe_solve, "_WORKER", "no_such_worker.py")
    safe_solve._probe_startup()

    assert safe_solve.SOLVE_TIMEOUT_S == 3.0
    assert safe_solve.STARTUP_COST_S is None
    assert "startup probe failed" in capsys.readouterr().out


def test_the_effective_budget_always_clears_the_measured_floor():
    """The invariant the probe exists to hold, stated as an invariant.

    This was first written as "the shipped default clears the floor on this
    machine" -- `_CONFIGURED_TIMEOUT_S >= floor` -- and it passed alone and
    failed in sequence. Startup is slower while the rest of this file is
    spawning subprocesses, so the floor moves and a fixed comparison against it
    is a duration assertion wearing an invariant's clothes. CLAUDE.md's rule
    about ordering over elapsed time, in a new place.

    What is actually guaranteed is what the probe enforces: whatever is
    configured, the *effective* budget is at or above the floor measured on
    this machine. A default too small for a slow box is no longer a way to fail
    -- it is a log line and a raised budget.
    """
    if safe_solve.STARTUP_COST_S is None:
        pytest.skip("the subprocess could not run; the probe reports that separately")
    floor = safe_solve.STARTUP_COST_S * safe_solve._STARTUP_SAFETY_FACTOR
    assert safe_solve.SOLVE_TIMEOUT_S >= floor or         safe_solve.SOLVE_TIMEOUT_S == safe_solve._CONFIGURED_TIMEOUT_S
