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
