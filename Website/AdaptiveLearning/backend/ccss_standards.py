"""The Common Core standard a generated question is scored against.

Resolved by grade, not band (a band spans three grades). A ladder is
`[(floor_grade, code), ...]`: the highest floor at or below the grade wins;
below every floor takes the lowest rung; an unreadable grade counts as the
youngest. A scenario ladder, where one exists, beats the topic ladder.
"""
import grade_levels

# fmt: off
TOPIC_LADDER = {
    # Comparing whole numbers, then decimals and fractions, then negatives.
    "ordering":            [(1, "1.NBT.3"), (2, "2.NBT.4"), (4, "4.NF.7"), (7, "6.NS.7")],
    # 8.EE.7b is the ceiling, so grades 9+ score below grade (see hs_solvers).
    "algebra":            [(1, "6.EE.7"), (7, "7.EE.4"), (8, "8.EE.7b")],
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
    # Kindergarten only (grade 0); refined per scenario below.
    "counting":            [(0, "K.CC.5")],
    "comparing_numbers":   [(0, "K.CC.7")],
    "add_and_subtract":    [(0, "K.OA.2")],
    "teen_numbers":        [(0, "K.NBT.1")],
    "shapes":              [(0, "K.G.2")],
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
        # Parentheses only from 5: `GRADE_OVERRIDES[4]` keeps grade 4 off 5.OA.1.
        "evaluate":           [(1, "1.OA.6"), (2, "2.NBT.5"), (3, "3.NBT.2"), (4, "4.OA.3"), (5, "5.OA.1")],
        "order_of_operations": [(1, "4.OA.3"), (5, "5.OA.1")],
        "simplify":            [(1, "6.EE.3")],
    },
    "functions": {
        "evaluate": [(1, "F-IF.2")],
        "compose":  [(1, "F-BF.1c")],
    },
    "counting": {
        "count_objects": [(0, "K.CC.5")],
        "one_more":      [(0, "K.CC.4c")],
        "one_less":      [(0, "K.CC.4c")],
        "next_number":   [(0, "K.CC.2")],
        "count_by_tens": [(0, "K.CC.1")],
    },
    "comparing_numbers": {
        "larger_number":    [(0, "K.CC.7")],
        "smaller_number":   [(0, "K.CC.7")],
        "largest_of_three": [(0, "K.CC.7")],
        "compare_groups":   [(0, "K.CC.6")],
    },
    "add_and_subtract": {
        "add":            [(0, "K.OA.5")],
        "subtract":       [(0, "K.OA.5")],
        "add_story":      [(0, "K.OA.2")],
        "subtract_story": [(0, "K.OA.2")],
        "make_ten":       [(0, "K.OA.4")],
    },
    "teen_numbers": {
        "count_ten_frames": [(0, "K.NBT.1")],
        "teen_make":        [(0, "K.NBT.1")],
        "teen_take_apart":  [(0, "K.NBT.1")],
    },
    "shapes": {
        "name_shape":    [(0, "K.G.2")],
        "count_sides":   [(0, "K.G.4")],
        "count_corners": [(0, "K.G.4")],
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
    """The code for `topic` at `grade`, refined by `scenario`; None for an unknown topic."""
    number = grade_levels.grade_number(grade)
    if number is None:
        number = 1
    ladder = SCENARIO_LADDER.get(topic, {}).get(scenario) or TOPIC_LADDER.get(topic)
    if not ladder:
        return None
    return _rung(ladder, number)
