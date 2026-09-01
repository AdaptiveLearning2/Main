# Generates area/perimeter/volume geometry questions via LLM and solves them with sympy.
import math
import os
import re
import random
from supabase import create_client, Client #pip install supabase
from dotenv import load_dotenv   #pip install dotenv
import llm_client
import question_schemas
import json
from flask import Flask, jsonify
from flask_cors import CORS #pip install flask-cors
import sympy as sp #pip install sympy
from sympy import sqrt, symbols, Eq, solve, sympify, Integer, Rational, pi
import incorrect_solution_generation as inc_gen
import lesson_plan_context
import geometry_solvers
import safe_solve
import grade_levels
import scenario_tiers
import grade_appropriateness

# Scenarios: perimeter, area, volume, missing_side, pythagorean_theorem.
# pi is approximated as 3.14 for simplicity/consistency.
def extract_json(text):
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i+1]

    return None

# The solve path -- the solvers, `preprocess_variables`,
# `normalize_solution`, SCENARIO_VARS and SOLVABLE_SCENARIOS -- lives in
# `geometry_solvers`, which the bounded worker imports. Only the
# presentation helpers stay here.
SCENARIO_VARS = geometry_solvers.SCENARIO_VARS
SOLVABLE_SCENARIOS = geometry_solvers.SOLVABLE_SCENARIOS


def serialize_sympy(x):
    if isinstance(x, sp.Rational):
        return str(x)
    if isinstance(x, sp.Integer):
        return int(x)
    if isinstance(x, sp.Float):
        return float(x)
    if isinstance(x, sp.Expr):
        return str(x)
    return str(x)


def format_two_decimals(x):
    if isinstance(x, list):
        x = x[0]
    val = float(x.evalf()) if hasattr(x, "evalf") else float(x)
    if val.is_integer():
        return str(int(val))
    else:
        return f"{val:.2f}"


# The scenario is chosen in code by _pick_scenario before the prompt is
# built, so only that one block is sent. Sending all eighteen -- as this
# prompt did until the switch to a billed API made the waste visible -- put
# seventeen irrelevant worked examples in front of the model on every call,
# every one of them a phrasing template it could copy from ("...units. What
# is its...") for a question it was not being asked to write.
#
# Split into header/blocks/footer rather than one f-string for that reason.
# The blocks are plain strings, not f-strings: the original was an f-string
# with no interpolation, so its `{{`/`}}` were escapes rendering as single
# braces -- doubling them here would emit literal `{{` into the prompt.
GEOMETRY_HEADER = """
You are to provide a Math question suitable for students. The response must be in JSON format.
The Question Text, Question Topic, Scenario, Variables, and Target will be displayed. The Question Topic will always be "geometry".

Generate a question for the one scenario given below.

IMPORTANT:
- ALWAYS approximate pi as 3.14
- All numeric values must be simple (integers or one decimal max)
- Ensure the problem is solvable using the provided variables
"""

# The EASY/MEDIUM/HARD TOPICS listing that sat here is gone with the other
# seventeen blocks: it named scenarios the model can no longer see, and
# difficulty is not its decision -- _pick_scenario has already applied both
# the difficulty tier and the grade-band restriction by this point.

SCENARIO_BLOCKS = {
    19: """SCENARIO 19: rectangle_area_by_counting
Example:
"A rectangle is split into 3 rows of 4 same-size squares. How many squares is that in total?"

This scenario is for the youngest students. Ask ONLY how many squares fill the
rectangle -- do NOT use the words "area", "multiply" or "units squared", and do
NOT ask for a formula. Keep both numbers between 2 and 6.

{
  "question_text": "A rectangle is split into 3 rows of 4 same-size squares. How many squares is that in total?",
  "type": "geometry",
  "scenario": "rectangle_area_by_counting",
  "variables": {
    "rows": "3",
    "columns": "4"
  }
}
""",

    1: """SCENARIO 1: rectangle_area
Example:
"A rectangle has a length of 5 units and a width of 3 units. What is its area?"

JSON structure:
{
  "question_text": "A rectangle has a length of 5 units and a width of 3 units. What is its area?",
  "type": "geometry",
  "scenario": "rectangle_area",
  "variables": {
    "length": "5",
    "width": "3"
  }
}
""",

    2: """SCENARIO 2: rectangle_perimeter
Example:
"A rectangle has a length of 8 units and a width of 2 units. What is its perimeter?"

{
  "question_text": "A rectangle has a length of 8 units and a width of 2 units. What is its perimeter?",
  "type": "geometry",
  "scenario": "rectangle_perimeter",
  "variables": {
    "length": "8",
    "width": "2"
  }
}
""",

    3: """SCENARIO 3: triangle_area
Example:
"A triangle has a base of 6 units and a height of 4 units. What is its area?"

{
  "question_text": "A triangle has a base of 6 units and a height of 4 units. What is its area?",
  "type": "geometry",
  "scenario": "triangle_area",
  "variables": {
    "base": "6",
    "height": "4"
  }
}
""",

    4: """SCENARIO 4: triangle_perimeter
Example:
"A triangle has side lengths 3, 4, and 5 units. What is its perimeter?"

{
  "question_text": "A triangle has side lengths 3, 4, and 5 units. What is its perimeter?",
  "type": "geometry",
  "scenario": "triangle_perimeter",
  "variables": {
    "s1": "3",
    "s2": "4",
    "s3": "5"
  }
}
""",

    5: """SCENARIO 5: circle_area
Example:
"A circle has a radius of 7 units. What is its area?"

{
  "question_text": "A circle has a radius of 7 units. What is its area?",
  "type": "geometry",
  "scenario": "circle_area",
  "variables": {
    "radius": "7"
  }
}
""",

    6: """SCENARIO 6: circle_circumference
Example:
"A circle has a radius of 5 units. What is its circumference?"

{
  "question_text": "A circle has a radius of 5 units. What is its circumference?",
  "type": "geometry",
  "scenario": "circle_circumference",
  "variables": {
    "radius": "5"
  }
}
""",

    7: """SCENARIO 7: rectangular_prism_volume
Example:
"A rectangular prism has a length of 4, width of 3, and height of 2. What is its volume?"

{
  "question_text": "A rectangular prism has a length of 4, width of 3, and height of 2. What is its volume?",
  "type": "geometry",
  "scenario": "rect_volume",
  "variables": {
    "length": "4",
    "width": "3",
    "height": "2"
  }
}
""",

    8: """SCENARIO 8: cylinder_volume
Example:
"A cylinder has a radius of 3 and height of 5. What is its volume?"

{
  "question_text": "A cylinder has a radius of 3 and height of 5. What is its volume?",
  "type": "geometry",
  "scenario": "cylinder_volume",
  "variables": {
    "radius": "3",
    "height": "5"
  }
}
""",

    9: """SCENARIO 9: sphere_volume
Example:
"A sphere has a radius of 3. What is its volume?"

{
  "question_text": "A sphere has a radius of 3. What is its volume?",
  "type": "geometry",
  "scenario": "sphere_volume",
  "variables": {
    "radius": "3"
  }
}
""",

    10: """SCENARIO 10: pythagorean
Example:
"A right triangle has legs of 3 and 4 units. What is the hypotenuse?"

{
  "question_text": "A right triangle has legs of 3 and 4 units. What is the hypotenuse?",
  "type": "geometry",
  "scenario": "pythagorean",
  "variables": {
    "a": "3",
    "b": "4"
  }
}
""",

    11: """SCENARIO 11: rectangle_missing_side_area
Example:
"A rectangle has an area of 20 square units and a width of 4 units. What is the length?"

{
  "question_text": "A rectangle has an area of 20 square units and a width of 4 units. What is the length?",
  "type": "geometry",
  "scenario": "rect_area_missing_side",
  "variables": {
    "area": "20",
    "known_side": "4"
  }
}
""",

    12: """SCENARIO 12: rectangle_missing_side_perimeter
Example:
"A rectangle has a perimeter of 24 units and one side length of 5 units. What is the other side?"

{
  "question_text": "A rectangle has a perimeter of 24 units and one side length of 5 units. What is the other side?",
  "type": "geometry",
  "scenario": "rect_perimeter_missing_side",
  "variables": {
    "perimeter": "24",
    "known_side": "5"
  }
}
""",

    13: """SCENARIO 13: circle_missing_radius_area
Example:
"A circle has an area of 50.24 square units. What is the radius?"

{
  "question_text": "A circle has an area of 50.24 square units. What is the radius?",
  "type": "geometry",
  "scenario": "circle_area_missing_side",
  "variables": {
    "area": "50.24"
  }
}
""",

    14: """SCENARIO 14: triangle_missing_side_area
Example:
"A triangle has an area of 12 square units and a base of 6 units. What is the height?"

{
  "question_text": "A triangle has an area of 12 square units and a base of 6 units. What is the height?",
  "type": "geometry",
  "scenario": "triangle_area_missing_side",
  "variables": {
    "area": "12",
    "known_side": "6"
  }
}
""",

    15: """SCENARIO 15: triangle_missing_side_perimeter
Example:
"A triangle has a perimeter of 18 units. Two of its sides are 5 units and 7 units. What is the length of the third side?"

{
  "question_text": "A triangle has a perimeter of 18 units. Two of its sides are 5 units and 7 units. What is the length of the third side?",
  "type": "geometry",
  "scenario": "triangle_perimeter_missing_side",
  "variables": {
    "perimeter": "18",
    "s1": "5",
    "s2": "7"
  }
}
""",

    16: """SCENARIO 16: circle_missing_radius_circumference
Example:
"A circle has a circumference of 31.4 units. What is the radius?"

{
  "question_text": "A circle has a circumference of 31.4 units. What is the radius?",
  "type": "geometry",
  "scenario": "circle_circumference_missing_side",
  "variables": {
    "circumference": "31.4"
  }
}
""",

    17: """SCENARIO 17: cube_volume
Example:
"A cube has a side length of 4 units. What is its volume?"

{
  "question_text": "A cube has a side length of 4 units. What is its volume?",
  "type": "geometry",
  "scenario": "cube_volume",
  "variables": {
    "side": "4"
  }
}
""",

    18: """SCENARIO 18: pyramid_volume
Example:
"A pyramid has a base area of 30 square units and a height of 9 units. What is its volume?"

{
  "question_text": "A pyramid has a base area of 30 square units and a height of 9 units. What is its volume?",
  "type": "geometry",
  "scenario": "pyramid_volume",
  "variables": {
    "base_area": "30",
    "height": "9"
  }
}
""",
}

GEOMETRY_FOOTER = """
FINAL RULES:
- Generate ONLY ONE question, return ONLY ONE JSON object.
- Return ONLY valid JSON, with NO additional text or characters
- Do NOT include: explanations, markdown, backticks, extra text before or after JSON
- Use ONLY double quotes
- All keys must match EXACTLY as shown
"""


# Block number -> the scenario name that block asks for, read out of the
# block's own JSON example rather than typed again here. `_geometry_prompt`
# restates the name as a rule, and a second hand-maintained copy of this
# mapping would be one more pair of lists that can disagree.
_SCENARIO_NAMES = {
    19: "rectangle_area_by_counting",
    1: "rectangle_area",
    2: "rectangle_perimeter",
    3: "triangle_area",
    4: "triangle_perimeter",
    5: "circle_area",
    6: "circle_circumference",
    7: "rect_volume",
    8: "cylinder_volume",
    9: "sphere_volume",
    10: "pythagorean",
    11: "rect_area_missing_side",
    12: "rect_perimeter_missing_side",
    13: "circle_area_missing_side",
    14: "triangle_area_missing_side",
    15: "triangle_perimeter_missing_side",
    16: "circle_circumference_missing_side",
    17: "cube_volume",
    18: "pyramid_volume",
}


def _solve_scenario(scenario, raw_vars, attempt):
    """The scenario's numeric solution, or None to retry.

    Delegates to the bounded worker. The arithmetic itself lives in
    `geometry_solvers`, which imports nothing heavy so a subprocess can load
    it; this module pulls in supabase, flask and dotenv.

    The bound is not belt-and-braces. `preprocess_variables` applies `sympify`
    to the model's raw values, and an earlier version of this function ran that
    in the request thread while its own docstring claimed otherwise:
    `{"side": "9**9**9"}` never returns, and the spin holds the GIL inside
    CPython's long-integer code, so no watchdog thread and no signal handler
    can stop it. `SCENARIO_VARS` checks that the keys are present, which says
    nothing about the values behind them, and the `try/except` around the
    dispatch caught exceptions rather than non-termination.
    """
    value = safe_solve.safe_solve_geometry(scenario, raw_vars)
    if value is None:
        print(f"[Attempt {attempt}] Could not solve {scenario} from "
              f"{raw_vars!r:.60}")
    return value


def _geometry_prompt(scenario):
    """Header + the one selected scenario's block + footer.

    KeyError rather than a default block on an unknown scenario: every caller
    gets its number from _pick_scenario, which draws from the scenarios
    SCENARIO_MIN_GRADE allows, ranked by SCENARIO_DIFFICULTY, so an unknown one
    means those tables have drifted apart -- which should
    fail loudly here rather than silently generate a rectangle-area question
    the solver will then score against whatever scenario it was asked for.
    """
    # The scenario name and its variable keys are restated as a hard
    # requirement, because sending the block alone was not enough. Measured
    # against Haiku 4.5 at 8th grade: it answered `rect_perimeter_missing_side`
    # carrying `{"area", "known_side"}` -- two scenarios blended, which
    # `SCENARIO_VARS` correctly refuses and which, before that check existed,
    # was a KeyError or a question scored against the wrong formula.
    #
    # The block already shows the right shape in an example. This says it as a
    # rule as well, which is the one thing the block does not do.
    name = _SCENARIO_NAMES[scenario]
    required = ", ".join(f'"{key}"' for key in SCENARIO_VARS[name])
    return (GEOMETRY_HEADER + "\n" + SCENARIO_BLOCKS[scenario] + GEOMETRY_FOOTER
            + f'- "scenario" MUST be exactly "{name}"\n'
            + f'- "variables" MUST contain exactly these keys: {required}\n'
            + "- Do NOT mix keys from another scenario; the value of each key "
              "must match what its name says it is\n")


solution = -1

# Every scenario the `match` below dispatches on, derived by hand from it and
# asserted against it in tests/test_geometry_scenarios.py -- two lists that
# can disagree is how the crash this prevents would come back.

# The grade at which each scenario's formula is introduced, by CCSS code.
#
# This replaces a single `EARLY_BAND_SCENARIOS` allowlist, and the replacement
# is the point rather than the values. That allowlist filtered one band and
# left the other three unfiltered, so fixing `early` did nothing for `middle`
# -- grades 4-6 -- which was being offered the Pythagorean theorem (8.G.7),
# circle area (7.G.4), and on the hard tier *only* volumes, two of them
# 8.G.9. A 4th grader on that tier was always asked a grade-8 question.
#
# Keyed per scenario so no band can be forgotten: every band is filtered by
# the same rule, and a scenario added without a grade here fails
# `tests/test_early_band_geometry.py` rather than silently defaulting to
# available everywhere.
SCENARIO_MIN_GRADE = {
    "rectangle_area_by_counting":        2,   # 2.G.2 -- count the squares that
                                              # fill a rectangle. The only
                                              # numeric geometry standard below
                                              # grade 3, and so the only thing
                                              # grades 1-2 can be asked here.
    "rectangle_area":                    3,   # 3.MD.7
    "rectangle_perimeter":               3,   # 3.MD.8
    "triangle_perimeter":                3,   # 3.MD.8
    "rect_area_missing_side":            4,   # 4.MD.3, unknown side from area
    "rect_perimeter_missing_side":       4,   # 4.MD.3
    "triangle_perimeter_missing_side":   4,   # 4.MD.3
    "rect_volume":                       5,   # 5.MD.5
    "cube_volume":                       5,   # 5.MD.5
    "triangle_area":                     6,   # 6.G.1
    "triangle_area_missing_side":        6,   # 6.G.1 inverted
    "circle_area":                       7,   # 7.G.4
    "circle_circumference":              7,   # 7.G.4
    "circle_area_missing_side":          7,   # 7.G.4 inverted
    "circle_circumference_missing_side": 7,   # 7.G.4 inverted
    "pythagorean":                       8,   # 8.G.7
    "cylinder_volume":                   8,   # 8.G.9
    "sphere_volume":                     8,   # 8.G.9
    "pyramid_volume":                    9,   # HS G-GMD.3; not in 8.G.9
}

# The top grade in each band -- `grade_levels.grade_band` buckets 1-3, 4-6,
# 7-8, 9+.
#
# Gating used to be on the band's *top* grade, which put grade-6 content in
# front of a 4th grader: the middle band spans 4-6, so a 4th grader was offered
# rectangular-prism volume (5.MD.5) on 3 of 10 measured questions. There is no
# reason to round up here -- `SCENARIO_MIN_GRADE` is per scenario and the
# student's own grade is known, so the ceiling is only a fallback for a grade
# that could not be read at all.
_BAND_CEILING = {"early": 3, "middle": 6, "upper": 8, "advanced": 13}


def _band_scenarios(grade):
    """Scenario numbers whose formula this student has actually reached.

    Takes the grade -- a number, a string like "4th Grade", or a band name.
    A band name resolves to that band's ceiling, which is what an unreadable
    grade gets: `grade_levels.grade_number` answers None there, and
    `_grade_band` answers "early", so the youngest content is the fallback in
    both directions.
    """
    if grade in _BAND_CEILING:
        ceiling = _BAND_CEILING[grade]
    else:
        number = grade_levels.grade_number(grade)
        # An unreadable grade is the youngest, matching
        # `LLM_topic_decider._allowed_topics` and `_grade_band`. It used to
        # resolve to the early band's ceiling of 3, which is two grades of
        # content granted to a student nobody could identify.
        ceiling = number if number is not None else 1
    allowed = {number_ for number_, name in _SCENARIO_NAMES.items()
               if SCENARIO_MIN_GRADE[name] <= ceiling}
    if allowed:
        return allowed
    # Reached only by a grade below the easiest scenario, which is now
    # `rectangle_area_by_counting` at 2.G.2. Grade 2 is served directly, and
    # grade 1 has no geometry at all -- `TOPIC_MIN_GRADE["geometry"]` is 2 --
    # so this is a backstop for a caller that asks anyway rather than a path
    # the product takes. It returns the easiest scenarios that exist rather
    # than nothing, because an empty set would make `random.choice` raise.
    floor = min(SCENARIO_MIN_GRADE.values())
    return {number_ for number_, name in _SCENARIO_NAMES.items()
            if SCENARIO_MIN_GRADE[name] <= floor}


# How hard each scenario is to *do*, independent of the grade that teaches it.
# The two are different axes: `algebra_complementary` is 7.G.5 and harder than
# `triangle_sum` at 8.G.5, and `circle_area_missing_side` is the same standard
# as `circle_area` while needing a square root on top.
#
# Ordered by steps: count -> one operation -> two -> invert a formula -> invert
# one containing pi or a square.
SCENARIO_DIFFICULTY = {
    "rectangle_area_by_counting":        1,   # count the squares
    "rectangle_area":                    2,   # one multiplication
    "rectangle_perimeter":               2,
    "triangle_perimeter":                2,   # add three
    "triangle_area":                     3,   # multiply then halve
    "circle_circumference":              3,   # 2 pi r
    "triangle_perimeter_missing_side":   3,   # invert by subtracting
    "circle_area":                       4,   # pi r squared
    "rect_volume":                       4,   # multiply three
    "cube_volume":                       4,
    "rect_area_missing_side":            4,   # invert by dividing
    "rect_perimeter_missing_side":       4,
    "pyramid_volume":                    5,   # a third of base times height
    "cylinder_volume":                   5,   # pi r squared h
    "pythagorean":                       5,   # squares and a root
    "triangle_area_missing_side":        5,   # invert, with the halving
    "circle_circumference_missing_side": 5,   # invert, with pi
    "sphere_volume":                     6,   # four thirds pi r cubed
    "circle_area_missing_side":          6,   # invert, with pi and a root
}


def _pick_scenario(difficulty, grade):
    """A scenario for this difficulty, chosen from what this grade can see.

    Ranked and sliced rather than looked up in a fixed per-tier list. A fixed
    list is right until a grade filter removes part of it -- geometry's hard
    tier is the volumes, and the hard ones are 8.G.9, so grades 6-7 were left
    with the two simplest and `hard` became easier than `medium`.

    Not cosmetic: difficulty is what the biosignals move, so a focused student
    pushed from medium to hard was getting an easier question. The fusion
    fired correctly and was undone one layer down.
    """
    allowed = _band_scenarios(grade)
    return random.choice(scenario_tiers.pick(
        difficulty, allowed,
        lambda number: SCENARIO_DIFFICULTY[_SCENARIO_NAMES[number]]))

# Difficulty governs which scenario gets picked above; grade controls the
# magnitude of the given measurements (side lengths, radii, etc.) within
# whatever scenario gets chosen.
def _grade_band(grade):
    # Delegated so ten copies of this cannot drift apart, and so an
    # unreadable grade ("Grade 1") lands in "early" rather than
    # "advanced" -- profiles.grade_level is free text. See grade_levels.
    return grade_levels.grade_band(grade)

GRADE_COMPLEXITY = {
    "early":    "Keep all given measurements (lengths, radii, etc.) between 1 and 12.",
    "middle":   "Measurements may range from 1 to 30.",
    "upper":    "Measurements may range from 1 to 100.",
    # Grades 9+. "No additional restriction" read to the model as no
    # requirement and produced the easiest shape that fit -- an audit of 640
    # questions found 8 of 10 grade-9 geometry questions three or more
    # grades below grade.
    "advanced": "Use two-digit measurements, and include one value with a decimal place (e.g. 12.5) so the arithmetic does not stay whole-number.",
}
# Kept separate from EARLY_BAND_SCENARIOS above rather than folded into one
# dict: this scales a number, that gates which formulas are even in play,
# and conflating them would make either change look like it needs the other.


def generate_geometry_question(global_questions, prev_questions, difficulty, grade,max_retries=3):
    for attempt in range(max_retries):
        # Scenario first: the prompt is now built around it rather than
        # listing every scenario and naming one at the end.
        grade_band = _grade_band(grade)
        # The grade itself, not the band: the band rounds a 4th grader up
        # to grade-6 content.
        scenario = _pick_scenario(difficulty, grade)

        prompt = _geometry_prompt(scenario)
        if attempt > 0:
            prompt += "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."

        prompt += (
            "\nPreviously generated questions:\n"
            + "\n".join(q["text"] for q in prev_questions)
            + "\n\nRecent global questions:\n"
            + "\n".join(q["text"] for q in global_questions)
            + "\n\nDO NOT generate a question matching any of the above. Use different wording and numerical values."
        )
        prompt += (
            f"\nGenerate a question of this topic that a {grade} student would consider to be of {difficulty} difficulty.\n"
        )
        prompt += (
            f"\nMAGNITUDE FOR THIS GRADE LEVEL: "
            f"{GRADE_COMPLEXITY[grade_band]}\n"
        )
        prompt = lesson_plan_context.append_lesson_context(prompt, "geometry", grade_band)
        response_text = llm_client.generate_text(
            prompt, schema=question_schemas.geometry(_SCENARIO_NAMES[scenario]))

        print(response_text)

        raw = extract_json(response_text)

        if not raw:
            print(f"[Attempt {attempt+1}] No JSON found")
            print(response_text)
            continue

        try:
            question_data = json.loads(raw)
        except Exception as e:
            print(f"[Attempt {attempt+1}] JSON parse failed:", e)
            print(response_text)
            continue

        required_keys = ["scenario", "variables", "question_text"]
        if not all(k in question_data for k in required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", question_data)
            continue

        # A scenario the dispatch below has no branch for is as unusable as a
        # missing key, and has to be caught *here* -- the `match` has no
        # `case _`, so an unrecognised name leaves `solution` unbound and
        # raises UnboundLocalError from a line that reads like arithmetic.
        # That is a 500 to a student where a retry would have cost nothing.
        #
        # Not hypothetical: measured against Haiku 4.5 on 2026-08-26, 2 of 3
        # geometry generations failed this way. It answered
        # `circle_missing_radius_circumference`, which is a reasonable name for
        # a scenario that exists here as `circle_circumference_missing_side`.
        # The prompt lists the valid names; a model reordering the words is
        # exactly what a validation step is for.
        if question_data["scenario"] not in SOLVABLE_SCENARIOS:
            print(f"[Attempt {attempt+1}] Unknown scenario:",
                  question_data["scenario"])
            continue

        # And one this grade may actually see. Everything above gates which
        # *block is sent*; nothing checked what came back, so a reply naming a
        # different scenario was solved and served. Not hypothetical: Haiku
        # returned a scenario other than the one asked for twice in this work.
        # A `sphere_volume` reply to a grade-4 request produced "A sphere has a
        # radius of 3 units. What is its volume?" -- 8.G.9, the exact defect
        # the grade gate was written to fix, walking straight past it.
        if question_data["scenario"] not in {
                _SCENARIO_NAMES[n] for n in _band_scenarios(grade)}:
            print(f"[Attempt {attempt+1}] Scenario above this grade:",
                  question_data["scenario"])
            continue

        # A known scenario with the wrong variables raises KeyError out of the
        # dispatch instead -- also a 500, and the commoner of the two: Haiku
        # answered `pythagorean` without a `b` on 2 of 3 upper-band
        # generations. Checked here so it retries like any other malformed
        # reply.
        missing = [k for k in SCENARIO_VARS[question_data["scenario"]]
                   if k not in (question_data.get("variables") or {})]
        if missing:
            print(f"[Attempt {attempt+1}] Scenario "
                  f"{question_data['scenario']} missing variables: {missing}")
            continue

        # Solved here rather than after the loop, so every way the solve can
        # fail is another attempt. Below the `for/else` -- where this used to
        # be -- a non-finite result, an unparseable variable or a solver raise
        # were all 500s, and the non-finite case was documented in
        # `incorrect_solution_generation` as reaching this loop when it could
        # not.
        solution_float = _solve_scenario(question_data["scenario"],
                                         question_data["variables"],
                                         attempt + 1)
        if solution_float is None:
            continue

        # Backstop on what the model actually produced, not just on what
        # the prompt asked for -- see grade_appropriateness.
        if grade_appropriateness.refuse(question_data.get("question_text"),
                                        "geometry", grade_band, difficulty,
                                        attempt + 1):
            continue

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    solution = format_two_decimals(solution_float)
    incorrect_answers = inc_gen.generate_general_incorrect_answers(solution_float)
    # Formatted the same way as the solution, so every option is a string.
    #
    # This was `round(float(ans), 2)`, which made geometry the one topic
    # shipping a mixed list -- `['13.93', 27.86, 5.0, 41.79]`, the correct
    # answer a string among floats. `Adaptive.jsx` decides correctness with
    # `JSON.stringify(option) === JSON.stringify(correct_answer)`, which is
    # type-sensitive: `24` and `"24"` do not match. It happened to work because
    # `correct_answer` is the same object that goes into the list, so it always
    # matched itself -- an invariant held by accident rather than by
    # construction, and one a later edit that re-serialised the options would
    # break with no visible symptom, since React renders 13.93 and "13.93"
    # identically.
    answers = [format_two_decimals(float(ans)) for ans in incorrect_answers] \
        + [solution]

    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "geometry",
        "answer_options": answers,
        "correct_answer": solution
    }

