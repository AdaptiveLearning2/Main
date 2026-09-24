# Generates a "find the unknown in an equation" question and solves it exactly.
#
# CCSS 1.OA.8, extending to 2.OA.1 and 3.OA.4. Not `algebra` (6.EE.7): the
# unknown is `?`, never `x`, which `grade_appropriateness` would refuse.

import json
import random
import re

import llm_client
from llm_json import extract_json
import lesson_plan_context
import question_schemas
import grade_levels
import ccss_standards
import grade_appropriateness
import incorrect_solution_generation as inc_gen
import answer_format


BLANK = "?"
OPERATORS = {"+", "-", "*"}

# Short enough that `int()` is bounded; no sympy here, so no bounded subprocess.
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
    # An unreadable grade ("Grade 1") lands in "early", not "advanced".
    return grade_levels.grade_band(grade)


# Only "early" is reachable (TOPIC_MAX_GRADE is 3); the rest is defense-in-depth.
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

# Multiplication is 3.OA: grades 1-2 must not meet it on any difficulty tier.
GRADE_OVERRIDES = {
    1: "This student is in GRADE 1. Use ADDITION or SUBTRACTION only, and every number must be 20 or below (1.OA.8). Do NOT use multiplication.",
    2: "This student is in GRADE 2. Use ADDITION or SUBTRACTION only, and every number must be 100 or below (2.OA.1, 2.NBT.5). Do NOT use multiplication.",
}

# Code-level enforcement of GRADE_OVERRIDES, derived so the two cannot drift.
# None: an unreadable grade is the youngest.
_NO_MULTIPLICATION_GRADES = set(GRADE_OVERRIDES) | {None}


def solve_missing(tokens):
    """The number that makes the equation true, or None (a retry, not a 500)."""
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

    # Solved by rearranging, never by searching.
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
    """`"multiplication"` if a grade-1/2 (or unreadable) student would see it, else None."""
    if not isinstance(tokens, list) or len(tokens) != 5:
        return None
    if tokens[1] == "*" and grade_levels.grade_number(grade) in _NO_MULTIPLICATION_GRADES:
        return "multiplication"
    return None


def _equation_text(tokens):
    return " ".join(tokens)


def shown_matches_scored(question_text, tokens):
    """A reason if the equation on screen is not the one being scored, else None."""
    if not isinstance(question_text, str):
        return "question_text is not a string"
    shown = re.sub(r"\s+", " ", question_text)
    equation = _equation_text(tokens)
    if equation not in shown:
        return f"text does not contain {equation!r}"
    # A digit outside the equation is a second number on screen.
    without = shown.replace(equation, " ", 1)
    if re.search(r"\d", without):
        return "question_text carries digits outside the equation"
    return None


def generate_incorrect_answers(solution, tokens):
    """Near-misses first, then the general generator. A fixed list, never a search."""
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

        # Backstop on what the model produced; `?` rather than `x` keeps this at grade 1.
        if grade_appropriateness.refuse(question_data.get("question_text"),
                                        "missing_number", grade_band, difficulty,
                                        attempt + 1):
            continue

        forbidden = _forbidden_operator(question_data.get("variables"), grade)
        if forbidden:
            print(f"[Attempt {attempt+1}] {forbidden} not allowed at this grade")
            continue

        # Solved inside the loop, so an unsolvable equation is another attempt.
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
        "ccss_standard": ccss_standards.ccss_for("missing_number", grade),
        "answer_options": answers,
        "correct_answer": correct,
    }
