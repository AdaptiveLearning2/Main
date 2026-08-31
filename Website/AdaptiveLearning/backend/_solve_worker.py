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
        expr = parse_expr(req["expr"], transformations=transformations)
        scenario = req.get("scenario")
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
