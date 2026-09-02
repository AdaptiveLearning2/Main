# Generates a "solve the quadratic" question and solves it exactly.
#
# CCSS A-REI.4b -- "solve quadratic equations by inspection, taking square
# roots, completing the square, the quadratic formula and factoring". It exists
# because grades 9-12 had no content of their own: every other topic here tops
# out at grade 8 by the CCSS grade of its concept, so an audit of 640 generated
# questions found 81% of grade-9 questions three or more grades below grade,
# and writing a harder requirement into every `advanced` tier moved that by two
# points. Harder numbers inside 8.EE.7b are still 8.EE.7b.
#
# It is deliberately NOT `algebra`, which is one linear equation with exactly
# one solution (8.EE.7b) and whose solver *refuses* a quadratic -- correctly,
# since presenting one root as the answer marks the other correct choice wrong.
# Asking which root is what makes a two-root equation scoreable here.

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


# Which root the question asks for. Chosen here rather than by the model, the
# way `_pick_scenario` is in geometry and probability: the solver has to know
# which one it is scoring, and a model free to pick would eventually write
# "smaller" into the text while the reply's field said "larger". Pinning it in
# the schema's enum makes that reply unrepresentable on the Claude branch and a
# refusal on the Ollama one.
TARGETS = ("larger", "smaller")

# The words a question may use for each, and -- the load-bearing half -- the
# words that mean the *other* one. A text saying "smaller" scored against the
# larger root is a question answered correctly and marked wrong, which is the
# failure shape this codebase treats as worse than any refusal.
_TARGET_WORDS = {
    "larger":  ("larger", "greater", "bigger", "largest", "greatest"),
    "smaller": ("smaller", "lesser", "smallest", "least"),
}

quadratic_prompt = """
You are to provide a Math question suitable for high school students. The response must be in JSON format.
The Question Topic will be "quadratics".

The question asks the student to solve a quadratic equation and give ONE of its two solutions.

You are given the equation and which solution to ask for. Your ONLY job is to
write the sentence around them. Do NOT change the equation, do NOT solve it,
and do NOT mention either solution.

Rules for "question_text":
- It must contain the equation written EXACTLY as given below under EQUATION,
  character for character, including every sign and space.
- It must ask for the solution named below under WHICH SOLUTION, and must NOT
  use a word meaning the other one.
- Vary the wording between questions. Do not write any other number anywhere.

Return ONLY valid JSON with no text before or after the JSON object.

The JSON must follow this exact structure:

{
  "question_text": "Solve x^2 - 5x + 6 = 0. What is the larger solution?",
  "question_topic": "quadratics"
}

Rules:
- Use ONLY double quotes for all strings.
- Do NOT include any characters outside the JSON object.
"""

# How big the roots get, per band. The band scales magnitude only; which
# *shape* of equation each difficulty gets is `_TIERS` below, exactly as in
# geometry and probability, where difficulty selects the scenario and the band
# scales the numbers. Stating the difficulty rule in both places is what a
# single table avoids.
_ROOT_RANGE = {
    "early":    (1, 5),
    "middle":   (1, 8),
    "upper":    (1, 10),
    "advanced": (2, 12),
}

# `signs` decides whether a root may be negative; `scales` is the leading
# coefficient. `hard` multiplying through by 2-4 is the step that turns
# factoring into the AC method, and is the standard Algebra I progression.
_TIERS = {
    "easy":   {"signs": "positive", "scales": (1,)},
    "medium": {"signs": "mixed",    "scales": (1,)},
    "hard":   {"signs": "mixed",    "scales": (2, 3, 4)},
}


def _choose_coefficients(difficulty, grade_band):
    """`(a, b, c)` for an equation that is factorable over the integers.

    Built here rather than asked for, which is the whole reason this topic is
    reliable. Measured on llama3.1:8b across three promptings -- a description
    of the constraint, a construction recipe, and a construction recipe with a
    worked example -- the model produced a usable equation 0 of 3, 2 of 3 and
    1 of 4 times. Almost every refusal was `irrational roots`: it picks b and c
    freely, and a random pair almost never leaves b^2 - 4ac a perfect square.
    Of the successes, two silently dropped the constraint they were given and
    one copied the worked example verbatim, so the tier was also not producing
    the content it named.

    None of that is a wording problem, so no wording fixed it. Choosing p and q
    here makes the equation factorable by construction, which:

      * removes the refusal class entirely -- every retry that class caused was
        a billed model call that could not have succeeded;
      * makes the `hard` tier possible at all. A leading coefficient of 2-4 is
        the AC method and the right rung, and it was measured as the *least*
        achievable thing to ask for;
      * gives the difficulty tiers a uniform meaning rather than whatever the
        model happened to reach for.

    It is the same move already made for `target` here, and for the scenario in
    geometry and probability: the part with a right answer is decided in code,
    and the model writes the sentence.
    """
    low, high = _ROOT_RANGE.get(grade_band, _ROOT_RANGE["advanced"])
    tier = _TIERS.get(difficulty, _TIERS["medium"])
    p, q = random.sample(range(low, high + 1), 2)
    if tier["signs"] == "mixed":
        # One of the two, not both: "both negative" is a narrower question and
        # `-p` on the larger keeps the pair straddling zero more often.
        p = -p
    scale = random.choice(tier["scales"])
    # x^2 - (p + q)x + pq, multiplied through. The roots stay p and q, so they
    # are whole by construction and the discriminant is a perfect square.
    return scale, -scale * (p + q), scale * p * q


def _grade_band(grade):
    # Delegated so the copies cannot drift apart, and so an unreadable grade
    # ("Grade 1") lands in "early" rather than "advanced". See grade_levels.
    return grade_levels.grade_band(grade)


def shown_matches_scored(question_text, a, b, c, target):
    """The equation on screen must be the equation being scored, and the root
    it asks for must be the root that will be scored. A reason, or None.

    Two separate ways for this question to be answered correctly and marked
    wrong, so two checks:

      * the **equation**. Rendered from the coefficients rather than parsed out
        of the text, which is the direction `question_figures` establishes:
        derive what is shown from what is scored, and a disagreement stops
        being representable.
      * the **root**. `x^2 - 5x + 6 = 0` asked as "the smaller solution" and
        scored as the larger one is a perfectly well-formed question with the
        wrong answer attached, and no check on the equation alone can see it.
    """
    if not isinstance(question_text, str):
        return "question_text is not a string"
    equation = hs_solvers.render_quadratic(a, b, c)
    if equation not in question_text:
        return f"text does not contain {equation!r}"
    lowered = question_text.lower()
    if not any(word in lowered for word in _TARGET_WORDS[target]):
        return f"text does not ask for the {target} solution"
    opposite = "smaller" if target == "larger" else "larger"
    if any(word in lowered for word in _TARGET_WORDS[opposite]):
        return f"text asks for the {opposite} solution, which is not what is scored"
    return None


def generate_incorrect_answers(solution, a, b, c, target):
    """Near-misses first, the general generator for any gap.

    Bounded by construction -- a fixed candidate list, never a search. Whether
    three distinct wrong answers exist near a given number is a property of the
    number, which is what made the unbounded loops elsewhere in this codebase
    hang rather than merely retry.
    """
    candidates = []
    other = hs_solvers.other_root(a, b, c, target)
    if other is not None:
        # The best distractor available: a student who solves correctly and
        # reads "larger" as "smaller" lands exactly here.
        candidates.append(other)
    # Sign errors are the other mistake this topic actually produces, since
    # the roots come out of `-b ± sqrt(...)`.
    candidates += [-solution, solution + 1, solution - 1, solution + 2]
    wrong = []
    for candidate in candidates:
        if candidate != solution and candidate not in wrong:
            wrong.append(candidate)
        if len(wrong) == 3:
            return wrong
    return inc_gen.generate_general_incorrect_answers(float(solution))


def generate_quadratics_question(global_questions, prev_questions,
                                 difficulty, grade, max_retries=3):
    grade_band = _grade_band(grade)
    target = random.choice(TARGETS)
    # Both decided before the loop, so every attempt asks for the same
    # question and a retry is only ever the model failing to write the
    # sentence -- not a fresh roll of the dice that might land on something
    # unsolvable. The equation is factorable by construction.
    a, b, c = _choose_coefficients(difficulty, grade_band)
    equation = hs_solvers.render_quadratic(a, b, c)
    solution, reason = hs_solvers.solve_quadratic(a, b, c, target)
    if solution is None:
        # Unreachable: `_choose_coefficients` builds from two distinct integer
        # roots. Raising rather than retrying because a retry cannot help --
        # nothing about the next model call changes these coefficients -- and
        # this is a bug in the construction, not a bad reply.
        raise ValueError(f"built an unsolvable quadratic {equation!r}: {reason}")
    for attempt in range(max_retries):
        if attempt > 0:
            prompt = quadratic_prompt + "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."
        else:
            prompt = quadratic_prompt

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
        prompt += f"\nEQUATION: {equation}\n"
        prompt += f"\nWHICH SOLUTION: the {target} solution.\n"
        prompt = lesson_plan_context.append_lesson_context(prompt, "quadratics", grade_band)

        response_text = llm_client.generate_text(
            prompt, schema=question_schemas.quadratics())

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

        # The only thing left to get wrong: the sentence. The equation and the
        # root were settled before the loop, so this is the model dropping or
        # altering what it was handed rather than inventing something
        # unsolvable.
        mismatch = shown_matches_scored(question_data["question_text"],
                                        a, b, c, target)
        if mismatch:
            print(f"[Attempt {attempt+1}] Shown/scored mismatch: {mismatch}")
            continue

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    incorrect = generate_incorrect_answers(solution, a, b, c, target)
    answers = [answer_format.format_value(value) for value in incorrect]
    correct = answer_format.format_value(solution)
    answers.append(correct)
    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "quadratics",
        "answer_options": answers,
        "correct_answer": correct,
    }
