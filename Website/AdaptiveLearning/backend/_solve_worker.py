"""Parse and solve one expression, then exit.

Runs as a child of `safe_solve`; never imported by the app. Everything it
returns is a string -- pickling a sympy object across the boundary would mean
unpickling data the model influenced, and the parent re-parses anyway.
"""
import json
import math
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
        # Angles: the whole solve, like geometry. Four of the five scenarios
        # need only a number, but `algebra_complementary` solves an equation in
        # `x`, so the variables have to stay sympy expressions and the parse
        # cannot be separated from the solve.
        if scenario == "angle":
            import angle_solvers
            value = angle_solvers.solve_scenario(req["angle_scenario"],
                                                 req["variables"])
            if value is None:
                print(json.dumps({"ok": False, "error": "unsolvable scenario"}))
            else:
                print(json.dumps({"ok": True, "result": repr(value)}))
            return

        # A list of scalar values, sympified. This is the shape six of the
        # ten topics need: they parse the model's numbers and then do ordinary
        # arithmetic on them, so only the parse has to be bounded.
        #
        # Floats out, not sympy's canonical strings: `str(sympify("0.75"))` is
        # `"0.750000000000000"`, and every caller here either does arithmetic
        # or keeps the model's original string for display. Nothing needs the
        # sympy object, which is what lets the parent avoid a second parse.
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

        # Geometry's whole solve, including `preprocess_variables` -- which is
        # `sympify` over the model's raw values and is the unbounded part.
        # `sympify("9**9**9")` never returns, and `SCENARIO_VARS` checks only
        # that the keys exist, which says nothing about the values behind them.
        if scenario == "geometry":
            import geometry_solvers
            value = geometry_solvers.solve_scenario(req["geometry_scenario"],
                                                    req["variables"])
            if value is None:
                print(json.dumps({"ok": False, "error": "unsolvable scenario"}))
            else:
                print(json.dumps({"ok": True, "result": repr(value)}))
            return

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
