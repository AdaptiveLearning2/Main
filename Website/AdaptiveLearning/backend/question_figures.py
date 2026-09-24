"""Figure specifications for questions that need a picture.

The figure is derived from the `variables` the solver scores, never from the model, so a
picture cannot disagree with the answer. The browser draws the spec and derives its
screen-reader description from it. `figure_for` returns None rather than raising; only the
`graphs` generator treats a missing figure as an unusable reply.
"""

# Largest grid side drawn; beyond it the question keeps its wording and loses the picture.
MAX_GRID_SIDE = 12


def _positive_int(value, limit):
    """`value` (usually a string from the model) as an int in 1..limit, or None."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if 1 <= number <= limit else None


def _rect_grid(variables):
    """2.G.2 -- a rectangle partitioned into rows of same-size squares.

    Reads the same `rows`/`columns` keys the solver multiplies (`SCENARIO_VARS`).
    """
    rows = _positive_int(variables.get("rows"), MAX_GRID_SIDE)
    columns = _positive_int(variables.get("columns"), MAX_GRID_SIDE)
    if rows is None or columns is None:
        return None
    return {"type": "rect_grid", "rows": rows, "columns": columns}


# Tallest bar, drawn 1:1; scaled graphs (3.MD.3) would need their own figure type.
MAX_BAR = 20
MAX_CATEGORIES = 5


def _bar_chart(variables):
    """1.MD.4 / 2.MD.10 -- counts by category, from the solver's own `categories` list, in order."""
    categories = variables.get("categories")
    if not isinstance(categories, list) or not 2 <= len(categories) <= MAX_CATEGORIES:
        return None
    bars = []
    for entry in categories:
        if not isinstance(entry, dict):
            return None
        name = entry.get("name")
        count = _positive_int(entry.get("count"), MAX_BAR)
        if not isinstance(name, str) or not name.strip() or count is None:
            return None
        bars.append({"label": name.strip(), "value": count})
    if len({bar["label"].lower() for bar in bars}) != len(bars):
        return None            # two bars with one name cannot be told apart
    return {"type": "bar_chart", "bars": bars}


# Up to eighths (3.NF.1); thinner parts are too small to count on a question card.
MAX_PARTS = 8


def _part_whole(variables):
    """1.G.3 / 2.G.3 / 3.NF.1 -- a shape in equal parts, some shaded.

    Checks drawability only; lowest terms is the generator's check.
    """
    parts = _positive_int(variables.get("parts"), MAX_PARTS)
    shaded = _positive_int(variables.get("shaded"), MAX_PARTS)
    if parts is None or shaded is None or parts < 2:
        return None
    if not 1 <= shaded < parts:
        # 0/n has nothing to point at and n/n is the whole shape.
        return None
    return {"type": "part_whole", "parts": parts, "shaded": shaded}


# Scenario name -> figure builder; an absent scenario has no figure.
BUILDERS = {
    "rectangle_area_by_counting": _rect_grid,
    "how_many_total": _bar_chart,
    "how_many_more": _bar_chart,
    "part_whole": _part_whole,
}


def figure_for(scenario, variables):
    """The figure spec for this scenario, or None. Never raises: a failed figure costs only the figure."""
    builder = BUILDERS.get(scenario)
    if builder is None or not isinstance(variables, dict):
        return None
    try:
        return builder(variables)
    except Exception as e:                       # pragma: no cover - defensive
        print(f"[question_figures] could not build {scenario!r}: "
              f"{type(e).__name__}: {e}")
        return None
