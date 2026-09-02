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

Example: "Solve x^2 - 5x + 6 = 0. What is the larger solution?"

Rules for "coefficients":
- It is an object with the keys "a", "b" and "c", each a whole number written as a string.
- They are the coefficients of a*x^2 + b*x + c = 0, so "a" must NOT be "0".
- The equation MUST have two DIFFERENT solutions and BOTH must be whole numbers.
  Choose two different whole numbers p and q first, then use
  a = 1, b = -(p + q), c = p * q. You may multiply all three by the same whole
  number if you want a leading coefficient other than 1.
- Do NOT write an equation whose solutions are fractions, decimals, or square roots.

Rules for "question_text":
- It must contain the equation written EXACTLY as given below under EQUATION AS
  IT MUST APPEAR, character for character.
- It must ask for the solution named below under WHICH SOLUTION TO ASK FOR, and
  must NOT use a word meaning the other one.
- Write the squared term as "x^2". Do NOT use "x²", "x**2" or "x2".

Return ONLY valid JSON with no text before or after the JSON object.

The JSON must follow this exact structure:

{
  "question_text": "Solve x^2 - 5x + 6 = 0. What is the larger solution?",
  "question_topic": "quadratics",
  "coefficients": {"a": "1", "b": "-5", "c": "6"}
}

Rules:
- Use ONLY double quotes for all strings.
- Do NOT include any characters outside the JSON object.
"""


def _grade_band(grade):
    # Delegated so the copies cannot drift apart, and so an unreadable grade
    # ("Grade 1") lands in "early" rather than "advanced". See grade_levels.
    return grade_levels.grade_band(grade)


# Only "advanced" is reachable: LLM_topic_decider.TOPIC_MIN_GRADE puts this
# topic at grade 9. The other three bands are defense-in-depth in the same
# spirit as the "early" tables on the topics gated to grade 6+ -- if that floor
# is ever removed or bypassed, the content must still be something rather than
# whatever the model reaches for. They are deliberately not *easier* versions
# of a quadratic: there is no grade-3 quadratic, so what they scale is the
# arithmetic, and the gate above is what actually keeps the topic away.
COMPLEXITY_BY_GRADE = {
    "early": {
        "easy":   "Use a = 1 and solutions between 1 and 5.",
        "medium": "Use a = 1 and solutions between 1 and 6.",
        "hard":   "Use a = 1 and solutions between 1 and 8.",
    },
    "middle": {
        "easy":   "Use a = 1 and solutions between 1 and 6.",
        "medium": "Use a = 1 and solutions between 1 and 9.",
        "hard":   "Use a = 1 and solutions between 1 and 10.",
    },
    "upper": {
        "easy":   "Use a = 1 and solutions between 1 and 9.",
        "medium": "Use a = 1, and make ONE of the two solutions negative.",
        "hard":   "Use a = 1, and make BOTH solutions negative.",
    },
    "advanced": {
        "easy":   "Use a = 1 and two positive solutions between 2 and 12.",
        "medium": "Use a = 1 and make at least ONE solution negative, with both between -12 and 12.",
        # BOTH solutions negative, and deliberately NOT a leading coefficient
        # above 1 -- which is the rung this tier should eventually be, and is
        # measured as not working on llama3.1:8b.
        #
        # A leading coefficient of 2-4 is the natural step up: it is what turns
        # factoring into the AC method, and it is the standard Algebra I
        # progression. Asked for as a description ("use a leading coefficient
        # of 2, 3 or 4 and make at least one solution negative") it failed all
        # three attempts with "no real roots". Rewritten as an explicit
        # construction from p and q, with a worked example, it got to 2 of 3 --
        # and both successes silently dropped the constraint, returning
        # `x^2 - 9x + 20 = 0` and `x^2 - 17x + 60 = 0`: a = 1, both roots
        # positive, which is the medium tier wearing a hard label. So the cell
        # was failing a third of the time *and* not producing hard content the
        # rest of it.
        #
        # Production runs Claude, and CLAUDE.md is explicit that a rate
        # measured on llama3.1:8b describes only the Ollama path -- Haiku may
        # well construct these. But a tier that raises for one student in three
        # on the development provider is not something to leave in on the
        # assumption that the other provider is better, and re-measuring means
        # billing the API. Two roots of the same sign and larger magnitudes is
        # a real step above medium, is reliably constructible, and leaves the
        # AC-method rung to be enabled by whoever measures it.
        "hard":   "First choose two different whole numbers p and q, BOTH negative and both between -2 and -14. Then use a = 1, b = -(p + q), c = p * q -- so b and c are both positive. Example: p = -3 and q = -8 give x^2 + 11x + 24 = 0, whose solutions are -3 and -8.",
    },
}


def _coefficients(question_data, attempt):
    """The three coefficients as ints, or None to retry.

    Every value goes through `hs_solvers.parse_int`, which bounds the magnitude
    before `int()` sees it. That is what lets this topic skip the bounded
    subprocess the sympy-backed topics need: there is no parser here to hand a
    `9**9**9` to.
    """
    raw = question_data.get("coefficients")
    if not isinstance(raw, dict):
        print(f"[Attempt {attempt}] coefficients is not an object: {raw!r:.60}")
        return None
    values = []
    for key in ("a", "b", "c"):
        value = hs_solvers.parse_int(raw.get(key))
        if value is None:
            print(f"[Attempt {attempt}] coefficient {key!r} is unusable: "
                  f"{raw.get(key)!r:.40}")
            return None
        values.append(value)
    return values


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
        prompt += (
            f"\nCOMPLEXITY FOR THIS GRADE AND DIFFICULTY: "
            f"{COMPLEXITY_BY_GRADE[grade_band].get(difficulty, COMPLEXITY_BY_GRADE[grade_band]['medium'])}\n"
        )
        prompt += f"\nWHICH SOLUTION TO ASK FOR: the {target} solution.\n"
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

        coefficients = _coefficients(question_data, attempt + 1)
        if coefficients is None:
            continue
        a, b, c = coefficients

        # Solved inside the loop, so an equation this topic cannot score is
        # another attempt rather than an exception below the `for/else`. Four
        # of the refusals here are quadratics a student could be asked about
        # by a teacher and not by this solver -- irrational roots, a repeated
        # root -- so a retry is the honest response to all of them.
        solution, reason = hs_solvers.solve_quadratic(a, b, c, target)
        if solution is None:
            print(f"[Attempt {attempt+1}] Unusable quadratic: {reason}")
            continue

        # After the solve: the equation it renders is the one that has just
        # been scored, so a mismatch here is the model's text disagreeing with
        # the model's own coefficients.
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
