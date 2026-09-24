"""Parse and solve one expression, then exit. Child of `safe_solve` only.

Prints a readiness line once sympy is imported, then one JSON answer line;
the parent times the two phases separately. Results are strings, never
pickled sympy objects. A malformed request answers before the readiness line.
"""
import json
import math
import sys

# Line-buffered, or the readiness marker sits in the pipe until exit.
sys.stdout.reconfigure(line_buffering=True)

READY = "ready"


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
    # The parent starts the `SOLVE_TIMEOUT` clock here.
    print(json.dumps({READY: True}))
    try:
        scenario = req.get("scenario")
        if scenario == "angle":
            import angle_solvers
            value, reason = angle_solvers.solve_scenario(req["angle_scenario"],
                                                 req["variables"])
            if value is None:
                print(json.dumps({"ok": False, "error": reason}))
            else:
                print(json.dumps({"ok": True, "result": repr(value)}))
            return

        # Scalars sympified to floats; the caller does its own arithmetic.
        if scenario == "values":
            out = []
            for raw in req["values"]:
                value = sp.sympify(raw)
                if not value.is_number:
                    print(json.dumps({
                        "ok": False, "error": f"not a number: {raw!r}"}))
                    return
                as_float = float(value)
                if not math.isfinite(as_float):
                    print(json.dumps({
                        "ok": False, "error": f"not finite: {raw!r}"}))
                    return
                out.append(as_float)
            print(json.dumps({"ok": True, "result": json.dumps(out)}))
            return

        if scenario == "geometry":
            import geometry_solvers
            value, reason = geometry_solvers.solve_scenario(req["geometry_scenario"],
                                                    req["variables"])
            if value is None:
                print(json.dumps({"ok": False, "error": reason}))
            else:
                print(json.dumps({"ok": True, "result": repr(value)}))
            return

        # The whole solve runs here, not just the parse: `parse_expr` is the unbounded half.
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
        if scenario == "simplify":
            solution = sp.simplify(expr)
        elif scenario in ("evaluate", "order_of_operations"):
            solution = expr
        else:
            print(json.dumps({"ok": False,
                              "error": f"unknown scenario {scenario!r}"}))
            return
        # Not `is_number` (`simplify` may return `5*x`); and `nan.is_finite` is
        # None, not False, so the `has` half is needed too.
        if solution.is_finite is False or solution.has(sp.nan, sp.zoo, sp.oo):
            print(json.dumps({
                "ok": False,
                "error": f"not a usable answer: {solution}"}))
            return
        print(json.dumps({"ok": True, "result": str(solution)}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))


if __name__ == "__main__":
    main()
