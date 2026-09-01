"""Figure specifications for questions that need a picture.

Grades 1-3 mathematics is largely visual, and the standards this system could
not ask were mostly the visual ones: 1.G.3 and 2.G.3 partition a shape, 1.MD.4
and 2.MD.10 read a picture or bar graph. `rectangle_area_by_counting` (2.G.2)
was being asked in *words* -- "a rectangle split into 3 rows of 4 same-size
squares" -- which is a description of a picture rather than the picture.

THE FIGURE IS DERIVED FROM THE DATA THE SOLVER USES, NEVER FROM THE MODEL.
That is the whole design, and it is not a preference. `question_consistency`
exists because a model free to write the question text and the scored data
separately will eventually disagree with itself, and a student answers the
version on screen while being marked against the other. A picture is the same
hazard with no text for any check to read: nothing downstream could compare a
model-drawn diagram against the numbers it is scored on. Deriving the figure
from `variables` -- the same dict `geometry_solvers` indexes -- makes that
disagreement unrepresentable rather than merely unlikely.

So no generator asks a model for a figure, and none may. What is stored is a
*specification*, not markup: the browser draws it, and derives the description
a screen reader is given from the same spec. One source for the picture and the
sentence, for the reason `AccessibleChart` takes one `columns` spec for its
chart and its table -- as two literals they drifted twice in one PR.

**A figure is an enrichment and never a requirement.** `figure_for` returns
`None` for a scenario with no figure, values it cannot use, or a size it will
not draw, and every caller treats that as "no picture" rather than as a reason
to reject the question. The question was already complete without one: the text
still says "3 rows of 4 same-size squares".
"""

# Grids larger than this are unreadable at the size a question card gives them,
# and the SVG grows with the product. Refusing is safe -- the question keeps its
# wording and loses the picture -- so the bound is deliberately tight rather
# than generous.
MAX_GRID_SIDE = 12


def _positive_int(value, limit):
    """`value` as an int in 1..limit, or None.

    The generators hand these over as strings, because that is what the model
    produced and what the solver parses. Anything else is not a figure this
    module will draw.
    """
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= limit else None


def _rect_grid(variables):
    """2.G.2 -- a rectangle partitioned into rows of same-size squares.

    Reads `rows` and `columns`, which is exactly what
    `geometry_solvers.SCENARIO_VARS["rectangle_area_by_counting"]` declares and
    what its solver multiplies. Sharing the keys is the point: there is no
    second reading of the question for the picture to be drawn from.
    """
    rows = _positive_int(variables.get("rows"), MAX_GRID_SIDE)
    columns = _positive_int(variables.get("columns"), MAX_GRID_SIDE)
    if rows is None or columns is None:
        return None
    return {"type": "rect_grid", "rows": rows, "columns": columns}


# Scenario name -> the builder for its figure. A scenario absent from this map
# has no figure, which is the ordinary case: most questions here are text.
BUILDERS = {
    "rectangle_area_by_counting": _rect_grid,
}


def figure_for(scenario, variables):
    """The figure spec for this scenario, or None.

    Never raises. It runs on the hot generation path and a figure is an
    enrichment -- a picture that could not be built must cost the picture and
    nothing else, in the same fail-open direction as `lesson_plan_context`.
    """
    builder = BUILDERS.get(scenario)
    if builder is None or not isinstance(variables, dict):
        return None
    try:
        return builder(variables)
    except Exception as e:                       # pragma: no cover - defensive
        print(f"[question_figures] could not build {scenario!r}: "
              f"{type(e).__name__}: {e}")
        return None
