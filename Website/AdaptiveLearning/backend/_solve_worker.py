"""Parse and solve one expression, then exit.

Runs as a child of `safe_solve`; never imported by the app. Everything it
returns is a string -- pickling a sympy object across the boundary would mean
unpickling data the model influenced, and the parent re-parses anyway.
"""
import json
import sys


def main():
    try:
        req = json.loads(sys.stdin.read())
    except ValueError:
        print(json.dumps({"ok": False, "error": "unreadable request"}))
        return
    # Imported inside main so a malformed request costs no sympy startup.
    import sympy as sp
    from sympy.parsing.sympy_parser import (
        parse_expr, standard_transformations,
        implicit_multiplication_application)
    transformations = standard_transformations + (
        implicit_multiplication_application,)
    try:
        scenario = req.get("scenario")
        # Algebra's whole solve runs here, not just its parse. Splitting the
        # work would leave `parse_expr` in the parent, which is the unbounded
        # half: `9**9**9 + x = 5` never returns, and because the spin holds the
        # GIL inside CPython's long-integer code nothing in that process can
        # interrupt it -- not a watchdog thread, not a signal handler. Only an
        # external kill works, which is what the subprocess makes possible.
        if scenario == "equation":
            sides = req["expr"].split("=")
            if len(sides) != 2:
                print(json.dumps({"ok": False, "error": "not one equation"}))
                return
            left = parse_expr(sides[0], transformations=transformations)
            right = parse_expr(sides[1], transformations=transformations)
            solutions = sp.solve(sp.Eq(left, right), sp.symbols("x"))
            if len(solutions) != 1:
                print(json.dumps({
                    "ok": False,
                    "error": f"{len(solutions)} solutions; this topic scores exactly one"}))
                return
            if not solutions[0].is_number or not solutions[0].is_finite:
                print(json.dumps({
                    "ok": False,
                    "error": f"solution is not a finite number: {solutions[0]}"}))
                return
            print(json.dumps({"ok": True, "result": str(solutions[0])}))
            return

        expr = parse_expr(req["expr"], transformations=transformations)
        # `simplify` is the only scenario that transforms the expression; the
        # other two are the evaluated value, which parse_expr has already
        # produced. An unknown scenario is an error rather than a fall-through
        # -- the caller retries, instead of the parent binding nothing.
        if scenario == "simplify":
            solution = sp.simplify(expr)
        elif scenario in ("evaluate", "order_of_operations"):
            solution = expr
        else:
            print(json.dumps({"ok": False,
                              "error": f"unknown scenario {scenario!r}"}))
            return
        print(json.dumps({"ok": True, "result": str(solution)}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))


if __name__ == "__main__":
    main()
