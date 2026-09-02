# Generates a function-notation question and evaluates it exactly.
#
# CCSS F-IF.2 -- "use function notation, evaluate functions for inputs in their
# domains" -- and F-BF.1c, composing functions. It is the second of the two
# topics added to give grades 9-12 content of their own; see `hs_solvers` for
# the audit that made the case.
#
# The grade claim is narrower than it looks and is worth stating, because the
# obvious reading of this topic is grade 8. Evaluating a *rule* at a value is
# 8.F.2, and grade 8 explicitly does not require function notation. What is
# high school here is the notation itself (F-IF.2) and, unambiguously,
# composition (F-BF.1c), which has no grade-8 equivalent at all. That is why
# `compose` is the medium and hard tier rather than a flourish on top: a topic
# whose every tier was `evaluate` would be 8.F.2 wearing an f(x).

import json
import random

import llm_client
import lesson_plan_context
import question_schemas
import grade_levels
import hs_solvers
import incorrect_solution_generation as inc_gen
import answer_format


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


EVALUATE = "evaluate"
COMPOSE = "compose"

# Which scenario each tier asks for. Chosen here rather than by the model, as
# in geometry and probability: the two scenarios are scored by different
# functions and carry different keys, so a reply free to pick could hand
# `compose`'s keys to `evaluate`'s solver.
#
# Only "advanced" is reachable (TOPIC_MIN_GRADE puts this topic at grade 9);
# the rest are defense-in-depth if that floor is ever bypassed.
DIFFICULTY_SCENARIOS = {
    "easy":   EVALUATE,
    "medium": COMPOSE,
    "hard":   COMPOSE,
}

# The input a function is evaluated at. Bounded well below what `parse_int`
# allows: composition squares its inner value, so a three-digit input turns a
# reasonable-looking pair of quadratics into an answer in the millions, which
# `hs_solvers.MAX_ABS_RESULT` would then refuse -- a retry spent on a reply
# that followed every instruction it was given.
MAX_ABS_INPUT = 12

_HEADER = """
You are to provide a Math question suitable for high school students. The response must be in JSON format.
The Question Topic will be "functions".
"""

_EVALUATE_BLOCK = """Scenario: evaluate

The question gives one function and asks the student to evaluate it at a value.

Example: "If f(x) = 3x^2 - 2x + 1, what is f(4)?"

Rules:
- "f" is a list of whole numbers written as strings: the coefficients in
  DESCENDING powers of x. ["3", "-2", "1"] means 3x^2 - 2x + 1.
- "f" must have 2 or 3 entries, and its FIRST entry must not be "0".
- "input" is the whole number to evaluate at, written as a string.
- Do NOT include "g".

The JSON must follow this exact structure:

{
  "question_text": "If f(x) = 3x^2 - 2x + 1, what is f(4)?",
  "question_topic": "functions",
  "scenario": "evaluate",
  "f": ["3", "-2", "1"],
  "input": "4"
}
"""

_COMPOSE_BLOCK = """Scenario: compose

The question gives two functions and asks for one applied to the other.

Example: "If f(x) = x^2 + 1 and g(x) = 2x - 3, what is f(g(4))?"

Rules:
- "f" and "g" are lists of whole numbers written as strings: the coefficients
  in DESCENDING powers of x. ["2", "-3"] means 2x - 3.
- Each must have 2 or 3 entries, and the FIRST entry of each must not be "0".
- "input" is the whole number to evaluate at, written as a string.
- The question asks for f(g(input)) -- the INNER function is g.

The JSON must follow this exact structure:

{
  "question_text": "If f(x) = x^2 + 1 and g(x) = 2x - 3, what is f(g(4))?",
  "question_topic": "functions",
  "scenario": "compose",
  "f": ["1", "0", "1"],
  "g": ["2", "-3"],
  "input": "4"
}
"""

_FOOTER = """
Rules for "question_text":
- It must contain each function written EXACTLY as given below under FUNCTIONS
  AS THEY MUST APPEAR, character for character.
- It must contain the evaluation written EXACTLY as given below under WHAT TO
  ASK FOR.
- Write a squared term as "x^2". Do NOT use "x2", "x**2" or "x2".

Return ONLY valid JSON with no text before or after the JSON object.

Rules:
- Use ONLY double quotes for all strings.
- Do NOT include any characters outside the JSON object.
"""


def _prompt(scenario):
    """Header + the one selected scenario's block + footer. KeyError on an
    unknown scenario, for the reason `_geometry_prompt` documents."""
    block = {EVALUATE: _EVALUATE_BLOCK, COMPOSE: _COMPOSE_BLOCK}[scenario]
    return _HEADER + "\n" + block + _FOOTER


def _grade_band(grade):
    # Delegated so the copies cannot drift apart, and so an unreadable grade
    # ("Grade 1") lands in "early" rather than "advanced". See grade_levels.
    return grade_levels.grade_band(grade)


# What each band scales. Unlike the seven topics with a `COMPLEXITY_BY_GRADE`
# table, difficulty here selects the *scenario* above -- so, exactly as in
# geometry and probability, this table scales magnitude only and stating the
# difficulty rule twice is what it avoids.
GRADE_COMPLEXITY = {
    "early":    "Use coefficients between 1 and 5 and an input between 1 and 5.",
    "middle":   "Use coefficients between 1 and 8 and an input between 1 and 6.",
    "upper":    "Use coefficients between -9 and 9 and an input between -6 and 8.",
    "advanced": "Use coefficients between -12 and 12, at least one of them negative, and an input between -8 and 10.",
}


def _polynomial(question_data, key, attempt):
    """One coefficient list as ints, or None to retry.

    Bounded before `int()` by `parse_int_list`, which is what lets this topic
    skip the subprocess the sympy-backed ones need. A constant is refused
    here rather than solved: it evaluates fine and asks nothing.
    """
    raw = question_data.get(key)
    values = hs_solvers.parse_int_list(raw, hs_solvers.MAX_DEGREE + 1)
    if values is None:
        print(f"[Attempt {attempt}] {key!r} is not a usable coefficient list: "
              f"{raw!r:.60}")
        return None
    if hs_solvers.is_constant_polynomial(values):
        print(f"[Attempt {attempt}] {key!r} is a constant function")
        return None
    return values


def shown_matches_scored(question_text, shown, asked):
    """Every function on screen must be one being scored, and the evaluation
    asked for must be the one computed. A reason, or None.

    `shown` is the rendered functions, `asked` the rendered call. Both are
    built from the coefficients rather than parsed out of the text -- the
    direction `question_figures` establishes and the one that makes a
    disagreement unrepresentable instead of merely unlikely.

    The call matters as much as the functions here: "f(g(4))" scored while the
    text asks for "g(f(4))" is a well-formed question with the wrong answer
    attached, and the two differ by one character.
    """
    if not isinstance(question_text, str):
        return "question_text is not a string"
    for rendered in shown:
        if rendered not in question_text:
            return f"text does not contain {rendered!r}"
    if asked not in question_text:
        return f"text does not ask for {asked!r}"
    return None


def generate_incorrect_answers(solution, near):
    """Near-misses first, the general generator for any gap.

    `near` carries the answers a student reaches by making the mistake this
    scenario invites -- applying the composition the other way round, or
    dropping the constant term -- which is a better distractor than a number
    one away. Bounded by construction, never a search.
    """
    wrong = []
    for candidate in list(near) + [solution + 1, solution - 1, -solution,
                                   solution + 10]:
        if candidate != solution and candidate not in wrong:
            wrong.append(candidate)
        if len(wrong) == 3:
            return wrong
    return inc_gen.generate_general_incorrect_answers(float(solution))


def generate_functions_question(global_questions, prev_questions,
                                difficulty, grade, max_retries=3):
    grade_band = _grade_band(grade)
    scenario = DIFFICULTY_SCENARIOS.get(difficulty, COMPOSE)
    for attempt in range(max_retries):
        prompt = _prompt(scenario)
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
        prompt += f"\nCOMPLEXITY FOR THIS GRADE: {GRADE_COMPLEXITY[grade_band]}\n"
        prompt = lesson_plan_context.append_lesson_context(prompt, "functions", grade_band)

        response_text = llm_client.generate_text(
            prompt, schema=question_schemas.functions(scenario))

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

        if "question_text" not in question_data:
            print(f"[Attempt {attempt+1}] Missing keys:", question_data)
            continue

        # The reply's own scenario, checked against the one that was asked
        # for. Selecting a block is a prompt-level act; only this is
        # enforcement -- the same split geometry and angles arrived at after
        # Haiku returned a scenario other than the one requested twice.
        if question_data.get("scenario") != scenario:
            print(f"[Attempt {attempt+1}] Wrong scenario: "
                  f"{question_data.get('scenario')!r}, asked for {scenario!r}")
            continue

        f = _polynomial(question_data, "f", attempt + 1)
        if f is None:
            continue
        x = hs_solvers.parse_int(question_data.get("input"))
        if x is None or abs(x) > MAX_ABS_INPUT:
            print(f"[Attempt {attempt+1}] Unusable input: "
                  f"{question_data.get('input')!r:.40}")
            continue

        # Solved inside the loop, so a function this cannot evaluate is
        # another attempt rather than an exception below the `for/else`.
        if scenario == COMPOSE:
            g = _polynomial(question_data, "g", attempt + 1)
            if g is None:
                continue
            solution, reason = hs_solvers.solve_composition(f, g, x)
            shown = [f"f(x) = {hs_solvers.render_polynomial(f)}",
                     f"g(x) = {hs_solvers.render_polynomial(g)}"]
            asked = f"f(g({x}))"
            # The mistake this scenario invites: composing the other way.
            swapped, _ = hs_solvers.solve_composition(g, f, x)
            near = [v for v in (swapped,) if v is not None]
        else:
            solution, reason = hs_solvers.evaluate_polynomial(f, x)
            shown = [f"f(x) = {hs_solvers.render_polynomial(f)}"]
            asked = f"f({x})"
            # Dropping the constant term is the arithmetic slip here.
            dropped, _ = hs_solvers.evaluate_polynomial(f[:-1] + [0], x)
            near = [v for v in (dropped,) if v is not None]

        if solution is None:
            print(f"[Attempt {attempt+1}] Unusable function: {reason}")
            continue

        mismatch = shown_matches_scored(question_data["question_text"],
                                        shown, asked)
        if mismatch:
            print(f"[Attempt {attempt+1}] Shown/scored mismatch: {mismatch}")
            continue

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    incorrect = generate_incorrect_answers(solution, near)
    answers = [answer_format.format_value(value) for value in incorrect]
    correct = answer_format.format_value(solution)
    answers.append(correct)
    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "functions",
        "answer_options": answers,
        "correct_answer": correct,
    }
