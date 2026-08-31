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
