# Generates a "find the unknown in an equation" question and solves it exactly.
#
# CCSS 1.OA.8 -- "determine the unknown whole number in an addition or
# subtraction equation relating three whole numbers" -- extending to 2.OA.1 and
# 3.OA.4 (the unknown factor). It exists because grade 1 had two topics,
# `ordering` and `expressions`, and a 6-year-old was seeing the two on rotation.
#
# It is deliberately NOT `algebra`, which is grade 6 (6.EE.7) and uses `x`. The
# unknown here is written `?`, because algebraic notation at grade 1 is the
# thing `grade_appropriateness` exists to catch.

import json
import random
import re

import llm_client
import lesson_plan_context
import question_schemas
import grade_levels
import grade_appropriateness
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


BLANK = "?"
OPERATORS = {"+", "-", "*"}

# A whole number a student of this age would recognise, and short enough that
# `int()` on it is bounded by inspection. Nothing here reaches sympy -- the
# arithmetic is one operation on two integers -- so unlike the other topics
# this one needs no bounded subprocess. `safe_solve` exists because
# `sympify("9**9**9")` never returns; there is no parser here to feed.
_NUMBER = re.compile(r"^\d{1,4}$")

missing_prompt = """
You are to provide a Math question suitable for young students. The response must be in JSON format.
The Question Topic will be "missing_number".

The question asks the student to find the ONE missing number in a simple equation.

Example: "What number goes in the blank? 8 + ? = 11"

Rules for "variables":
- It must be a list of exactly FIVE strings: a number, an operator, a number, "=", and a number.
- EXACTLY ONE of the three numbers must be replaced by the string "?".
- The operator must be one of "+", "-", "*".
- Every other entry must be a whole number with no decimal point and no sign.
- The equation must be TRUE when the correct number replaces "?".
- The missing number must be a whole number of 0 or more.

Rules for "question_text":
- It must contain the equation written exactly as the variables read, with single
  spaces: for ["8", "+", "?", "=", "11"] the text must contain "8 + ? = 11".
- Use "?" for the blank. Do NOT use "x", "n", a letter, or a line of underscores.
- Do NOT include any other digits anywhere in the question text.

Return ONLY valid JSON with no text before or after the JSON object.

The JSON must follow this exact structure:

{
  "question_text": "What number goes in the blank? 8 + ? = 11",
  "question_topic": "missing_number",
  "variables": ["8", "+", "?", "=", "11"]
}

Rules:
- Use ONLY double quotes for all strings.
- "variables" must be a list of strings.
- Do NOT include any characters outside the JSON object.
"""


def _grade_band(grade):
    # Delegated so the copies cannot drift apart, and so an unreadable grade
    # ("Grade 1") lands in "early" rather than "advanced". See grade_levels.
    return grade_levels.grade_band(grade)


# Only "early" is reachable: LLM_topic_decider.TOPIC_MAX_GRADE caps this topic
# at grade 3. The other three bands are defense-in-depth, in the same spirit as
# the "early" tables on the topics that gate to grade 6+ -- if that ceiling is
# ever removed or bypassed, the content must still be something.
COMPLEXITY_BY_GRADE = {
    "early": {
        "easy":   "Use ADDITION only, with all numbers 10 or below (e.g. 3 + ? = 7).",
        "medium": "Use addition or subtraction, with all numbers 20 or below.",
        "hard":   "Use addition or subtraction, with all numbers 100 or below.",
    },
    "middle": {
        "easy":   "Use addition or subtraction, with all numbers 100 or below.",
        "medium": "Use addition, subtraction or multiplication, numbers 100 or below.",
        "hard":   "Use multiplication with an unknown factor, numbers 144 or below.",
    },
    "upper": {
        "easy":   "Use addition or subtraction, with all numbers 200 or below.",
        "medium": "Use multiplication with an unknown factor, numbers 200 or below.",
        "hard":   "Use multiplication with an unknown factor, numbers 500 or below.",
    },
    "advanced": {
        "easy":   "Use multiplication with an unknown factor, numbers 500 or below.",
        "medium": "Use multiplication with an unknown factor, numbers 1000 or below.",
        "hard":   "Use multiplication with an unknown factor, numbers 5000 or below.",
    },
}

# Grade 1 is 1.OA.8, which is within 20 and addition/subtraction only.
# Multiplication is 3.OA, so a grade-1 or grade-2 student must not meet it
# however the difficulty tier landed -- difficulty and grade are independent
# inputs, so a "hard" 1st grader is a real state.
GRADE_OVERRIDES = {
    1: "This student is in GRADE 1. Use ADDITION or SUBTRACTION only, and every number must be 20 or below (1.OA.8). Do NOT use multiplication.",
    2: "This student is in GRADE 2. Use ADDITION or SUBTRACTION only, and every number must be 100 or below (2.OA.1, 2.NBT.5). Do NOT use multiplication.",
}

# Both entries above forbid multiplication, and nothing enforced it: a reply
# of `3 * ? = 12` cleared solve_missing (it accepts "*"), shown_matches_scored
# and grade_appropriateness (which only looks for variable notation) just as
# happily as an addition equation, and was served two years above 1.OA.8. Keyed
# off GRADE_OVERRIDES rather than restated so the prompt-level rule and the
# code-level one cannot drift apart -- the same trap the `expressions`
# parenthesis leak was, per CLAUDE.md. `None` is included because an
# unreadable grade is treated as the youngest (see grade_levels), not as
# unrestricted.
_NO_MULTIPLICATION_GRADES = set(GRADE_OVERRIDES) | {None}


def solve_missing(tokens):
    """The number that makes the equation true, or None if it cannot be one.

    `None` rather than a raise: every caller is inside the retry loop, so an
    unusable reply must cost an attempt rather than escaping the generator as a
    500. That is the rule the other nine generators arrived at the hard way.

    No sympy anywhere, so no bounded subprocess -- see `_NUMBER`.
    """
    if not isinstance(tokens, list) or len(tokens) != 5:
        return None
    left, operator, right, equals, result = tokens
    if equals != "=" or operator not in OPERATORS:
        return None

    slots = [left, right, result]
    if slots.count(BLANK) != 1:
        return None
    if not all(isinstance(t, str) for t in slots):
        return None
    if not all(_NUMBER.match(t) for t in slots if t != BLANK):
        return None

    a, b, c = [None if t == BLANK else int(t) for t in slots]

    # Solved by rearranging, never by searching: the answer is one arithmetic
    # step whichever slot is blank.
    if operator == "+":
        value = c - b if a is None else c - a if b is None else a + b
    elif operator == "-":
        value = c + b if a is None else a - c if b is None else a - b
    else:
        if a is None:
            value = None if not b else c / b
        elif b is None:
            value = None if not a else c / a
        else:
            value = a * b

    if value is None or value < 0:
        return None                          # negatives are not grade 1-3
    if isinstance(value, float):
        if not value.is_integer():
            return None                      # the blank must be a whole number
        value = int(value)
    return value


def _forbidden_operator(tokens, grade):
    """Multiplication where a grade-1/2 (or unreadable) student must not see
    it, or None. Reads the operator straight off the structured token list
    rather than pattern-matching text -- unlike the `expressions` early-band
    check, there's no ambiguity here between an operator and a stray
    character to guess at.
    """
    if not isinstance(tokens, list) or len(tokens) != 5:
        return None
    if tokens[1] == "*" and grade_levels.grade_number(grade) in _NO_MULTIPLICATION_GRADES:
        return "multiplication"
    return None


def _equation_text(tokens):
    return " ".join(tokens)


def shown_matches_scored(question_text, tokens):
    """The equation on screen must be the equation being scored.

    Its own check rather than `question_consistency.dataset_mismatch`, which
    locates a dataset after the last colon -- there is no dataset here, there is
    an equation, and a model free to write "8 + ? = 12" above scored variables
    reading `["8","+","?","=","11"]` produces a question answered correctly and
    marked wrong. That is the worst failure shape available here, and it is the
    one this topic is most exposed to, since the whole question *is* the
    equation.
    """
    if not isinstance(question_text, str):
        return "question_text is not a string"
    shown = re.sub(r"\s+", " ", question_text)
    equation = _equation_text(tokens)
    if equation not in shown:
        return f"text does not contain {equation!r}"
    # Any digit outside the equation is a second number on screen, and a young
    # reader cannot tell which one the question means.
    without = shown.replace(equation, " ", 1)
    if re.search(r"\d", without):
        return "question_text carries digits outside the equation"
    return None


def generate_incorrect_answers(solution, tokens):
    """Near-misses first, because a distractor a student can reason about is
    the point of the exercise; the general generator fills any gap.

    Bounded by construction -- a fixed candidate list, not a search. Whether
    three distinct wrong answers exist near a small number is a property of the
    number, which is what made the unbounded loops elsewhere hang.
    """
    numbers = [int(t) for t in (tokens[0], tokens[2], tokens[4]) if t != BLANK]
    candidates = [solution + 1, solution - 1, *numbers,
                  sum(numbers), abs(numbers[0] - numbers[1]),
                  solution + 2, solution + 10]
    wrong = []
    for candidate in candidates:
        if candidate >= 0 and candidate != solution and candidate not in wrong:
            wrong.append(candidate)
        if len(wrong) == 3:
            return wrong
    return inc_gen.generate_general_incorrect_answers(float(solution))


def generate_missing_number_question(global_questions, prev_questions,
                                     difficulty, grade, max_retries=3):
    grade_band = _grade_band(grade)
    for attempt in range(max_retries):
        if attempt > 0:
            prompt = missing_prompt + "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."
        else:
            prompt = missing_prompt

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
        override = GRADE_OVERRIDES.get(grade_levels.grade_number(grade))
        if override:
            prompt += "\nGRADE-SPECIFIC RULE: " + override + "\n"
        prompt = lesson_plan_context.append_lesson_context(prompt, "missing_number", grade_band)

        response_text = llm_client.generate_text(
            prompt, schema=question_schemas.missing_number())

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

        required_keys = ["variables", "question_text"]
        if not all(k in question_data for k in required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", question_data)
            continue

        # Backstop on what the model produced, not on what the prompt asked
        # for -- see grade_appropriateness. It matters unusually much here:
        # the whole topic is one step away from algebra notation, and `?`
        # rather than `x` is what keeps it at grade 1.
        if grade_appropriateness.refuse(question_data.get("question_text"),
                                        "missing_number", grade_band, difficulty,
                                        attempt + 1):
            continue

        forbidden = _forbidden_operator(question_data.get("variables"), grade)
        if forbidden:
            print(f"[Attempt {attempt+1}] {forbidden} not allowed at this grade")
            continue

        # Solved inside the loop, so an unsolvable equation is another attempt
        # rather than an exception below the `for/else`.
        solution = solve_missing(question_data["variables"])
        if solution is None:
            print(f"[Attempt {attempt+1}] Unsolvable equation:",
                  repr(question_data["variables"])[:80])
            continue

        mismatch = shown_matches_scored(question_data.get("question_text"),
                                        question_data["variables"])
        if mismatch:
            print(f"[Attempt {attempt+1}] Shown/scored mismatch: {mismatch}")
            continue

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    incorrect = generate_incorrect_answers(solution, question_data["variables"])
    answers = [answer_format.format_value(a) for a in incorrect]
    correct = answer_format.format_value(solution)
    answers.append(correct)
    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "missing_number",
        "answer_options": answers,
        "correct_answer": correct,
    }
