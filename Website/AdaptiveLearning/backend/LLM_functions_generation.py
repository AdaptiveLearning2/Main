# Generates a function-notation question (F-IF.2, F-BF.1c) and evaluates it exactly.
#
# Evaluating a rule alone is 8.F.2; composition is what makes this high school,
# which is why `compose` is both the medium and hard tier.

import json
import random
import re

import llm_client
from llm_json import extract_json
import lesson_plan_context
import question_schemas
import grade_levels
import ccss_standards
import hs_solvers
import incorrect_solution_generation as inc_gen
import answer_format


EVALUATE = "evaluate"
COMPOSE = "compose"

# Scenario per tier, chosen here rather than by the model: the two are scored
# by different solvers.
DIFFICULTY_SCENARIOS = {
    "easy":   EVALUATE,
    "medium": COMPOSE,
    "hard":   COMPOSE,
}

_HEADER = """
You are to provide a Math question suitable for high school students. The response must be in JSON format.
The Question Topic will be "functions".
"""

_EVALUATE_BLOCK = """Scenario: evaluate

The question gives ONE function and asks the student to evaluate it at a value.

Example: "If f(x) = 3x^2 - 2x + 1, what is f(4)?"

Do NOT mention a function called g. There is only f.

The JSON must follow this exact structure:

{
  "question_text": "If f(x) = 3x^2 - 2x + 1, what is f(4)?",
  "question_topic": "functions",
  "scenario": "evaluate"
}
"""

_COMPOSE_BLOCK = """Scenario: compose

The question gives TWO functions and asks for one applied to the other, inner
function first.

Example: "If f(x) = x^2 + 1 and g(x) = 2x - 3, what is f(g(4))?"

The JSON must follow this exact structure:

{
  "question_text": "If f(x) = x^2 + 1 and g(x) = 2x - 3, what is f(g(4))?",
  "question_topic": "functions",
  "scenario": "compose"
}
"""

_FOOTER = """
You are given the functions and the evaluation to ask for. Your ONLY job is to
write the sentence around them. Do NOT change them, do NOT invent another
function, and do NOT work out the answer.

Rules for "question_text":
- It must contain each function written EXACTLY as given below under FUNCTIONS
  AS THEY MUST APPEAR, character for character.
- It must contain the evaluation written EXACTLY as given below under WHAT TO
  ASK FOR, character for character.
- Vary the wording between questions. Do not write any other number anywhere.

Return ONLY valid JSON with no text before or after the JSON object.

Rules:
- Use ONLY double quotes for all strings.
- Do NOT include any characters outside the JSON object.
"""


def _prompt(scenario):
    """Header + the selected scenario's block + footer. KeyError on an unknown scenario."""
    block = {EVALUATE: _EVALUATE_BLOCK, COMPOSE: _COMPOSE_BLOCK}[scenario]
    return _HEADER + "\n" + block + _FOOTER


def _grade_band(grade):
    # An unreadable grade ("Grade 1") lands in "early", not "advanced".
    return grade_levels.grade_band(grade)


# Magnitude per band (difficulty picks the scenario). Read by
# `_choose_functions`, not sent to the model.
_FUNCTION_RANGE = {
    "early":    {"coeff": 5,  "input": 5,  "negatives": False},
    "middle":   {"coeff": 8,  "input": 6,  "negatives": False},
    "upper":    {"coeff": 9,  "input": 8,  "negatives": True},
    "advanced": {"coeff": 12, "input": 10, "negatives": True},
}

# Degree-1 inner function with tighter coefficients keeps f(g(x)) under
# `hs_solvers.MAX_ABS_RESULT` by construction.
_INNER_COEFF = 5

# The widest input a composition uses, whatever the band otherwise allows.
_COMPOSE_INPUT = 5


def _polynomial(degree, limit, negatives):
    """Coefficients in descending powers, leading one non-zero, never `±x`.

    The identity would make f(g(x)) == g(x) and silently drop the swapped-order
    distractor. Forced rather than redrawn, since a loop is unbounded at limit 1.
    """
    leading = random.randint(1, limit)
    coefficients = [-leading if negatives and random.random() < 0.5 else leading]
    for _ in range(degree):
        value = random.randint(0, limit)
        if negatives and value and random.random() < 0.5:
            value = -value
        coefficients.append(value)
    if degree == 1 and abs(coefficients[0]) == 1 and coefficients[1] == 0:
        coefficients[1] = random.randint(1, limit)
        if negatives and random.random() < 0.5:
            coefficients[1] = -coefficients[1]
    return coefficients


def _choose_functions(scenario, grade_band):
    """`(f, g, x)` for this scenario, with `g` None unless composing.

    Chosen here and handed to the model rendered, so it copies a string rather
    than reproducing `render_polynomial`'s conventions.
    """
    spec = _FUNCTION_RANGE.get(grade_band, _FUNCTION_RANGE["advanced"])
    limit, negatives = spec["coeff"], spec["negatives"]
    f = _polynomial(random.choice((1, 2)), limit, negatives)
    g = _polynomial(1, _INNER_COEFF, negatives) if scenario == COMPOSE else None
    # Composing narrows the input, or a quadratic f answers in the tens of thousands.
    span = min(spec["input"], _COMPOSE_INPUT) if scenario == COMPOSE \
        else spec["input"]
    # Not 0: f(0) is just the constant term.
    x = random.choice([v for v in range(-span if negatives else 1, span + 1)
                       if v != 0])
    return f, g, x


# `\bg`, not bare "g(": "Solving (x + 1)" contains one.
_SECOND_FUNCTION = re.compile(r"\bg\s*\(")


def _mentions_second_function(question_text):
    """True if an `evaluate` question introduces a function it is not scored on.

    `shown_matches_scored` checks every scored function appears, never that
    nothing else does.
    """
    return bool(_SECOND_FUNCTION.search(question_text or ""))


def shown_matches_scored(question_text, shown, asked):
    """A reason if the text lacks a scored function or the scored call, else None.

    `shown` and `asked` are rendered from the coefficients, never parsed from
    the text. The call matters: "f(g(4))" vs "g(f(4))" differ by one character.
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
    """Near-misses first (`near`: the scenario's typical mistake), then the general generator."""
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
    # Settled before the loop: a retry only re-asks for the sentence.
    f, g, x = _choose_functions(scenario, grade_band)
    if scenario == COMPOSE:
        solution, reason = hs_solvers.solve_composition(f, g, x)
        shown = [f"f(x) = {hs_solvers.render_polynomial(f)}",
                 f"g(x) = {hs_solvers.render_polynomial(g)}"]
        asked = f"f(g({x}))"
        # Distractor: composing the other way round.
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
        # Unreachable by construction; a retry would not change the coefficients.
        raise ValueError(f"built an unusable function question: {reason}")

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
        # The two sections `_FOOTER` tells the model to look for.
        prompt += ("\nFUNCTIONS AS THEY MUST APPEAR:\n" + "\n".join(shown)
                   + "\n")
        prompt += f"\nWHAT TO ASK FOR: {asked}\n"
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

        # Selecting a block only asks; this check enforces.
        if question_data.get("scenario") != scenario:
            print(f"[Attempt {attempt+1}] Wrong scenario: "
                  f"{question_data.get('scenario')!r}, asked for {scenario!r}")
            continue

        mismatch = shown_matches_scored(question_data["question_text"],
                                        shown, asked)
        if mismatch:
            print(f"[Attempt {attempt+1}] Shown/scored mismatch: {mismatch}")
            continue

        # Only for `evaluate`: `compose` must name g.
        if scenario == EVALUATE and _mentions_second_function(
                question_data["question_text"]):
            print(f"[Attempt {attempt+1}] Names a second function in an "
                  "evaluate question, which nothing scores")
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
        "ccss_standard": ccss_standards.ccss_for("functions", grade, scenario),
        "answer_options": answers,
        "correct_answer": correct,
    }
