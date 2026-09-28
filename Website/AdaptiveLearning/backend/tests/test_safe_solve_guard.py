"""Model text a sympy parse sees is plain arithmetic: sympify and parse_expr eval their input."""
import json
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import safe_solve  # noqa: E402

# The review's two executed payloads, and the syntax any other would need.
PAYLOADS = [
    '__import__("os").getpid()',
    'len(open("C:/Windows/win.ini").read())',
    "(1).__class__",
    "x[0]",
    "'a'",
    "a,b",
    "x\\n",
]

CALLS = {
    "expression": lambda t: safe_solve.safe_solve(t, "evaluate"),
    "equation":   lambda t: safe_solve.safe_solve(f"{t} = 1", "equation"),
    "values":     lambda t: safe_solve.safe_sympify_values(["1", t]),
    "angle":      lambda t: safe_solve.safe_solve_angle("complementary", [t]),
    "geometry":   lambda t: safe_solve.safe_solve_geometry("cube_volume", {"side": t}),
}


@pytest.mark.parametrize("call", sorted(CALLS))
@pytest.mark.parametrize("text", PAYLOADS)
def test_text_a_parse_would_eval_never_reaches_a_worker(monkeypatch, call, text):
    spawned = []

    def _spawn(request):
        spawned.append(request)
        raise RuntimeError("a worker was started")
    monkeypatch.setattr(safe_solve, "_spawn", _spawn)

    assert CALLS[call](text) is None
    assert spawned == []


@pytest.mark.parametrize("call,expected", [
    (lambda: safe_solve.safe_solve("(4 + 6)*3 - 5", "evaluate"), "25"),
    (lambda: safe_solve.safe_solve("2x + 3 = 11", "equation"), "4"),
    (lambda: safe_solve.safe_solve("2*x + 3*x", "simplify"), "5*x"),
    (lambda: safe_solve.safe_sympify_values(["1/2", "0.75", "-3"]), [0.5, 0.75, -3.0]),
    (lambda: safe_solve.safe_solve_geometry("cube_volume", {"side": "3"}), 27.0),
    (lambda: safe_solve.safe_solve_angle("complementary", ["35"]), 55.0),
])
def test_ordinary_arithmetic_still_solves(call, expected):
    assert call() == expected


# Spellings a model writes that sympy solved before the guard; measured, not guessed.
@pytest.mark.parametrize("call,expected", [
    (lambda: safe_solve.safe_solve("12 ÷ 3 × 2", "evaluate"), "8"),
    (lambda: safe_solve.safe_solve("3·4 + 2⋅1", "evaluate"), "14"),
    (lambda: safe_solve.safe_solve("x − 4 = 6", "equation"), "10"),
    (lambda: safe_solve.safe_solve("3\t+ 4\n", "evaluate"), "7"),
    (lambda: safe_solve.safe_solve("5!", "evaluate"), "120"),
    (lambda: safe_solve.safe_sympify_values(["−3", "1,200", "7\n", "–2"]), [-3.0, 1200.0, 7.0, -2.0]),
])
def test_a_models_spelling_of_arithmetic_still_solves(call, expected):
    assert call() == expected


def test_a_comma_that_is_not_thousands_is_still_refused(monkeypatch):
    monkeypatch.setattr(safe_solve, "_spawn", lambda _r: pytest.fail("a worker was started"))
    for text in ("1,2", "1,2000", "f(1,200)"[:-1] + ",1)"):
        assert safe_solve.safe_sympify_values([text]) is None, text


class _Proc:
    returncode = 0

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


def _worker_answering(monkeypatch, result):
    monkeypatch.setattr(safe_solve, "_spawn", lambda request: (_Proc(), None, None))
    monkeypatch.setattr(safe_solve, "_await_answer",
                        lambda *a: (json.dumps({"ok": True, "result": result}), "solve"))


def test_a_result_callers_would_sympify_is_plain_arithmetic_too(monkeypatch):
    """The generators sympify it in-process, with the full environment."""
    _worker_answering(monkeypatch, '__import__("os").getpid()')
    assert safe_solve.safe_solve("1 + 1", "evaluate") is None


def test_a_values_result_is_json_and_passes(monkeypatch):
    """`values` answers a JSON list, read by json.loads rather than sympify."""
    _worker_answering(monkeypatch, "[0.5, 2.0]")
    assert safe_solve.safe_sympify_values(["1/2", "2"]) == [0.5, 2.0]
