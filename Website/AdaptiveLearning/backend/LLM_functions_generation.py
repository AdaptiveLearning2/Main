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
import re

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
#
# Read by `_choose_functions` rather than sent to the model. It was prompt
# text, and asking the model to both invent the coefficients and render them
# in this file's exact canonical form is the harder half of a job it does not
# need to do at all -- see `_choose_functions`.
_FUNCTION_RANGE = {
    "early":    {"coeff": 5,  "input": 5,  "negatives": False},
    "middle":   {"coeff": 8,  "input": 6,  "negatives": False},
    "upper":    {"coeff": 9,  "input": 8,  "negatives": True},
    "advanced": {"coeff": 12, "input": 10, "negatives": True},
}

# The inner function of a composition is always degree 1, with coefficients
# bounded tighter than the outer one's. That is what keeps the answer inside
# `hs_solvers.MAX_ABS_RESULT` *by construction* rather than by drawing until
# something fits: |g(x)| <= 5*10 + 5 = 55 at the widest band, so
# |f(g(x))| <= 12*55^2 + 12*55 + 12, comfortably under the million. Degree 1
# inner is also the standard way composition is introduced.
_INNER_COEFF = 5

# The widest input a composition uses, whatever the band otherwise allows.
_COMPOSE_INPUT = 5


def _polynomial(degree, limit, negatives):
    """Coefficients in descending powers, leading one non-zero."""
    leading = random.randint(1, limit)
    coefficients = [-leading if negatives and random.random() < 0.5 else leading]
    for _ in range(degree):
        value = random.randint(0, limit)
        if negatives and value and random.random() < 0.5:
            value = -value
        coefficients.append(value)
    return coefficients


def _choose_functions(scenario, grade_band):
    """`(f, g, x)` for this scenario, with `g` None unless composing.

    Chosen here rather than asked for, for the reason `quadratics`
    documents one file over -- and this topic had the sharper version of the
    problem. `_FOOTER` tells the model its text must contain each function
    "EXACTLY as given below under FUNCTIONS AS THEY MUST APPEAR", and that
    section was never emitted: the footer was written for a design that only
    `quadratics` ended up implementing. So the model was pointed at
    instructions that did not exist, and had to invent the coefficients *and*
    guess `render_polynomial`'s exact spacing and sign conventions.

    Measured on llama3.1:8b with the lesson plan injected: the `compose`
    scenario failed 3 of 3, every one `text does not contain 'g(x) = x + 2'`
    -- the model returning coefficients and then writing them differently in
    the prose. That is two of the topic's three tiers, since `compose` is both
    medium and hard.

    Handing the rendered functions over removes the guess entirely: the model
    copies a string instead of reproducing a convention.
    """
    spec = _FUNCTION_RANGE.get(grade_band, _FUNCTION_RANGE["advanced"])
    limit, negatives = spec["coeff"], spec["negatives"]
    f = _polynomial(random.choice((1, 2)), limit, negatives)
    g = _polynomial(1, _INNER_COEFF, negatives) if scenario == COMPOSE else None
    # Composing narrows the input, because the arithmetic compounds: at the
    # widest band an input of 10 through g reaches 55, and a quadratic f then
    # answers in the tens of thousands. `MAX_ABS_RESULT` still admits that --
    # it is there to stop a nonsense question, not to keep one reasonable --
    # and 7 * 1600 + ... is a question about stamina rather than about
    # composition, which is the thing being tested.
    span = min(spec["input"], _COMPOSE_INPUT) if scenario == COMPOSE \
        else spec["input"]
    # 0 is a legal input and a dull question -- f(0) is the constant term, so
    # it asks nothing about evaluating.
    x = random.choice([v for v in range(-span if negatives else 1, span + 1)
                       if v != 0])
    return f, g, x


# A second function named in an `evaluate` question. `\bg` rather than a bare
# "g(" because "Solving (x + 1)" contains one and is perfectly good wording --
# the word boundary is what separates a function called g from the last letter
# of an ordinary word.
_SECOND_FUNCTION = re.compile(r"\bg\s*\(")


def _mentions_second_function(question_text):
    """True if an `evaluate` question introduces a function it is not scored on.

    `_EVALUATE_BLOCK` says `Do NOT include "g"`, and that is prompt text --
    asked, not guaranteed. The lesson plan is appended after it and cannot
    know which scenario was selected, so it can only be written to name no
    function the block did not; this is what makes that hold.

    `shown_matches_scored` cannot catch it, and the reason is worth keeping:
    it checks that every scored function *appears*, never that nothing else
    does, so a text defining both f and g passes it while the solver reads f
    alone. The student is shown a function that plays no part in the answer.
    """
    return bool(_SECOND_FUNCTION.search(question_text or ""))


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
    # Settled before the loop, as in quadratics: every attempt then asks for
    # the same question, and a retry is only ever the model failing to write
    # the sentence rather than a fresh draw that might not evaluate.
    f, g, x = _choose_functions(scenario, grade_band)
    if scenario == COMPOSE:
        solution, reason = hs_solvers.solve_composition(f, g, x)
        shown = [f"f(x) = {hs_solvers.render_polynomial(f)}",
                 f"g(x) = {hs_solvers.render_polynomial(g)}"]
        asked = f"f(g({x}))"
        # The mistake this scenario invites: composing the other way round.
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
        # Unreachable: `_choose_functions` bounds the result by construction,
        # which is what the degree-1 inner function is for. Raising rather
        # than retrying for the reason quadratics does -- nothing about the
        # next model call changes these coefficients, so this is a bug in the
        # construction and not a bad reply.
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
        # The two sections `_FOOTER` has always told the model to look for.
        # Neither was ever emitted -- the footer was written for a design only
        # `quadratics` implemented -- so the model was pointed at instructions
        # that did not exist and had to guess this file's rendering.
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

        # The reply's own scenario, checked against the one that was asked
        # for. Selecting a block is a prompt-level act; only this is
        # enforcement -- the same split geometry and angles arrived at after
        # Haiku returned a scenario other than the one requested twice.
        if question_data.get("scenario") != scenario:
            print(f"[Attempt {attempt+1}] Wrong scenario: "
                  f"{question_data.get('scenario')!r}, asked for {scenario!r}")
            continue

        # The only thing left to get wrong is the sentence: the functions and
        # the evaluation were settled before the loop and handed over.
        mismatch = shown_matches_scored(question_data["question_text"],
                                        shown, asked)
        if mismatch:
            print(f"[Attempt {attempt+1}] Shown/scored mismatch: {mismatch}")
            continue

        # Only for `evaluate`: `compose` is *required* to name g, and the
        # check above already pins that it is the g being scored.
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
        "answer_options": answers,
        "correct_answer": correct,
    }
