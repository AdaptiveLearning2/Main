"""The Common Core standard a generated question is scored against.

Every topic is already grade-gated against a CCSS code -- `TOPIC_MIN_GRADE`,
`TOPIC_MAX_GRADE` and the two `SCENARIO_MIN_GRADE` tables cite one next to each
integer -- but only as a comment. This is the machine-readable copy, so the
code can ride on the question row and reach a teacher.

Resolved by *grade*, not band, for the reason the scenario gates use the grade:
a band spans three grades and the standard can change inside it (grade 1's
`1.MD.4` and grade 2's `2.MD.10` are both "early"). A ladder is a list of
`(floor_grade, code)`; the code with the highest floor at or below the
student's grade wins, and a grade below every floor takes the lowest rung --
the defense-in-depth tiers still describe content, and the floor's standard is
the honest name for it. An unreadable grade counts as the youngest, as it does
everywhere else.

Scenario ladders come first where a topic selects one, because there the
standard is a property of the scenario (`triangle_sum` is 8.G.5 inside a
grade-7 topic). Topics whose structure only scales by grade get a topic ladder;
topics whose structure never changes get one rung.
"""
import grade_levels

# fmt: off
TOPIC_LADDER = {
    # Comparing whole numbers, then decimals and fractions, then negatives.
    "ordering":            [(1, "1.NBT.3"), (2, "2.NBT.4"), (4, "4.NF.7"), (7, "6.NS.7")],
    # One-step within the topic's floor, two-step at 7, x on both sides and
    # distribution at 8 -- and 8.EE.7b stays the ceiling however far the
    # numbers grow, which is why grades 9+ score below grade (see hs_solvers).
    "algebra":             [(1, "6.EE.7"), (7, "7.EE.4"), (8, "8.EE.7b")],
    # Like denominators, then unlike, then negatives.
    "rationals":           [(1, "4.NF.3"), (5, "5.NF.1"), (7, "7.NS.1")],
    # One statistic over a listed dataset, at every grade it is offered.
    "mean":                [(1, "6.SP.5c")],
    "median":              [(1, "6.SP.5c")],
    "mode":                [(1, "6.SP.5c")],
    "missing_number":      [(1, "1.OA.8"), (2, "2.OA.1"), (3, "3.OA.4")],
    "patterns":            [(1, "1.NBT.1"), (2, "2.NBT.2"), (3, "3.OA.9"), (4, "4.OA.5"), (5, "5.OA.3")],
    "graphs":              [(1, "1.MD.4"), (2, "2.MD.10"), (3, "3.MD.3")],
    "shape_fractions":     [(1, "1.G.3"), (2, "2.G.3"), (3, "3.NF.1")],
    "quadratics":          [(1, "A-REI.4b")],
    "spread":              [(1, "S-ID.2")],
    # Fallback for a scenario the table below does not name.
    "probability":         [(1, "7.SP.5")],
    "angle_relationships": [(1, "7.G.5")],
    "expressions":         [(1, "1.OA.6")],
    "geometry":            [(1, "2.G.2")],
    "functions":           [(1, "F-IF.2")],
}

SCENARIO_LADDER = {
    "geometry": {
        "rectangle_area_by_counting":        [(1, "2.G.2")],
        "rectangle_area":                    [(1, "3.MD.7")],
        "rectangle_perimeter":               [(1, "3.MD.8")],
        "triangle_perimeter":                [(1, "3.MD.8")],
        "rect_area_missing_side":            [(1, "4.MD.3")],
        "rect_perimeter_missing_side":       [(1, "4.MD.3")],
        "triangle_perimeter_missing_side":   [(1, "4.MD.3")],
        "rect_volume":                       [(1, "5.MD.5")],
        "cube_volume":                       [(1, "5.MD.5")],
        "triangle_area":                     [(1, "6.G.1")],
        "triangle_area_missing_side":        [(1, "6.G.1")],
        "circle_area":                       [(1, "7.G.4")],
        "circle_circumference":              [(1, "7.G.4")],
        "circle_area_missing_side":          [(1, "7.G.4")],
        "circle_circumference_missing_side": [(1, "7.G.4")],
        "pythagorean":                       [(1, "8.G.7")],
        "cylinder_volume":                   [(1, "8.G.9")],
        "sphere_volume":                     [(1, "8.G.9")],
        "pyramid_volume":                    [(1, "G-GMD.3")],
    },
    "angle_relationships": {
        "complementary":         [(1, "7.G.5")],
        "supplementary":         [(1, "7.G.5")],
        "linear_pair":           [(1, "7.G.5")],
        "algebra_complementary": [(1, "7.G.5")],
        "triangle_sum":          [(1, "8.G.5")],
    },
    "probability": {
        "probability_of":     [(1, "7.SP.5")],
        "not_probability_of": [(1, "7.SP.5")],
        "dice":               [(1, "7.SP.5")],
    },
    "expressions": {
        # Add and subtract within 20, then within 100, then whole-number
        # arithmetic, then mixed operations with parentheses.
        "evaluate":            [(1, "1.OA.6"), (2, "2.NBT.5"), (3, "3.NBT.2"), (4, "5.OA.1")],
        "order_of_operations": [(1, "5.OA.1")],
        "simplify":            [(1, "6.EE.3")],
    },
    "functions": {
        "evaluate": [(1, "F-IF.2")],
        "compose":  [(1, "F-BF.1c")],
    },
}
# fmt: on


def _rung(ladder, number):
    code = ladder[0][1]
    for floor, candidate in ladder:
        if floor <= number:
            code = candidate
    return code


def ccss_for(topic, grade, scenario=None):
    """The code for `topic` at `grade`, refined by `scenario` where the topic
    has one. None only for a topic this module does not know."""
    number = grade_levels.grade_number(grade)
    if number is None:
        number = 1
    ladder = SCENARIO_LADDER.get(topic, {}).get(scenario) or TOPIC_LADDER.get(topic)
    if not ladder:
        return None
    return _rung(ladder, number)
