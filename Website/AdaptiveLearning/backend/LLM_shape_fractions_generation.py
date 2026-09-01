# Generates "what fraction of the shape is shaded?" and solves it exactly.
#
# CCSS 1.G.3 (partition circles and rectangles into two and four equal shares,
# describe them as halves and fourths), 2.G.3 (halves, thirds, fourths), and
# 3.NF.1 (understand a/b as a parts of a whole partitioned into b equal parts).
#
# Distinct from `rationals`, which is 4.NF.3 onward and is fraction
# *arithmetic*. This is fraction recognition: reading one off a picture. It is
# the second topic whose figure is required rather than enriching -- "what
# fraction of the shape is shaded" has nothing to read without the shape.

import json
import math
import random
import re

import llm_client
import lesson_plan_context
import question_figures
import question_schemas
import grade_levels
import grade_appropriateness
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


SHAPE_PROMPT = """
You are to provide a Math question suitable for young students. The response must be in JSON format.
The Question Topic will be "shape_fractions".

The student is shown a rectangle divided into equal parts, some of them shaded,
and answers what fraction is shaded. The shape is drawn from "parts" and
"shaded" -- you do not describe it in words.

Example: "What fraction of the shape is shaded?" with parts "4" and shaded "3".

The JSON must follow this exact structure:

{
  "question_text": "What fraction of the shape is shaded?",
  "question_topic": "shape_fractions",
  "scenario": "part_whole",
  "parts": "4",
  "shaded": "3"
}

Rules:
- "parts" must be a whole number from 2 to 8, written as a string.
- "shaded" must be a whole number of at least 1 and less than "parts".
- "shaded" and "parts" must share no common factor. 2 out of 4 is NOT allowed,
  because it can be read as one half or as two fourths and both are right.
  1 out of 2, 1 out of 4, 3 out of 4, 1 out of 3, 2 out of 3, 3 out of 8 are all fine.
- "question_text" must NOT contain any digits. The parts are in the picture --
  writing them in the question is giving away the reading being asked for.
- "scenario" MUST be exactly "part_whole".
- Use ONLY double quotes for all strings.
- Do NOT include any characters outside the JSON object.
"""


def _grade_band(grade):
    # Delegated so the copies cannot drift apart, and so an unreadable grade
    # ("Grade 1") lands in "early" rather than "advanced". See grade_levels.
    return grade_levels.grade_band(grade)


# Only "early" is reachable -- TOPIC_MAX_GRADE caps this at grade 3 -- and the
# rest is defense-in-depth, like the other capped topics.
COMPLEXITY_BY_GRADE = {
    "early": {
        "easy":   "Use 2 or 4 parts (halves and fourths).",
        "medium": "Use 2, 3 or 4 parts.",
        "hard":   "Use 3, 4, 6 or 8 parts.",
    },
    "middle": {
        "easy":   "Use 2, 3 or 4 parts.",
        "medium": "Use 3, 4, 6 or 8 parts.",
        "hard":   "Use 5, 6, 7 or 8 parts.",
    },
    "upper": {
        "easy":   "Use 3, 4, 6 or 8 parts.",
        "medium": "Use 5, 6, 7 or 8 parts.",
        "hard":   "Use 5, 6, 7 or 8 parts.",
    },
    "advanced": {
        "easy":   "Use 5, 6, 7 or 8 parts.",
        "medium": "Use 5, 6, 7 or 8 parts.",
        "hard":   "Use 5, 6, 7 or 8 parts.",
    },
}

# 1.G.3 is two and four equal shares and nothing else -- thirds are 2.G.3.
# Difficulty and grade are independent inputs, so a "hard" 1st grader is a real
# state and the band's hard tier alone would hand them eighths.
GRADE_OVERRIDES = {
    1: "This student is in GRADE 1. Use exactly 2 or 4 parts, nothing else (1.G.3).",
    2: "This student is in GRADE 2. Use 2, 3 or 4 parts (2.G.3).",
}


def solve_shape_fraction(parts, shaded):
    """The shaded fraction as a string, or None if the picture does not
    determine one answer.

    **Lowest terms is required, and refusing otherwise is the whole point.**
    Two shaded parts in four is a perfectly good picture and an ambiguous
    question: `2/4` and `1/2` are both correct readings of it, and whichever
    one the solver picked, a student giving the other would be marked wrong for
    a right answer. That is the failure this codebase treats as the worst
    available -- worse than a refused question, which costs one retry.

    Reducing the answer instead was the other option and is worse: the student
    is asked to read the picture, and the picture says two of four.
    """
    try:
        parts, shaded = int(parts), int(shaded)
    except (TypeError, ValueError):
        return None
    if not 2 <= parts or not 1 <= shaded < parts:
        return None
    if math.gcd(shaded, parts) != 1:
        return None
    return f"{shaded}/{parts}"


def generate_incorrect_answers(parts, shaded):
    """The misreadings this question is for, in order.

    The complement first: counting the *unshaded* parts is the mistake a child
    actually makes here. Then the inverted fraction, then a miscount of each
    half of the ratio. Bounded by construction -- a fixed candidate list.
    """
    candidates = [
        (parts - shaded, parts),        # counted the unshaded parts
        (shaded, parts + 1),            # miscounted the parts
        (shaded + 1, parts),            # miscounted the shaded parts
        (shaded, parts - 1),
        (1, parts),
        (parts - shaded, parts + 1),
        (1, parts + 1),
        # Neighbouring denominators, so halves has three proper distractors at
        # all. Without these the only ones available for 1/2 were `2/2` and
        # `1/1` -- both improper, and both worth exactly one whole, so a
        # student could rule out two options with a single thought.
        (1, parts * 2),
        (parts + 1, parts + 2),
        (parts - 1, parts + 1),
        (parts, shaded),                # read the ratio upside down
    ]
    answer = (shaded, parts)
    # Proper fractions first, and improper ones only to fill. A fraction
    # greater than one cannot be part of a shape, so `2/1` is not a misreading
    # a child could make -- it is an option nobody considers, which wastes one
    # of the three and makes the question easier than it looks. The inverted
    # ratio stays available last, because for halves there is little else.
    proper = [(n, d) for n, d in candidates if 0 < n < d]
    wrong = []
    for num, den in proper + candidates:
        if den < 1 or num < 1 or (num, den) == answer:
            continue
        text = f"{num}/{den}"
        if text not in wrong:
            wrong.append(text)
        if len(wrong) == 3:
            break
    return wrong


def generate_shape_fractions_question(global_questions, prev_questions,
                                      difficulty, grade, max_retries=3):
    grade_band = _grade_band(grade)
    for attempt in range(max_retries):
        prompt = SHAPE_PROMPT
        if attempt > 0:
            prompt += "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."

        prompt += (
            "\nPreviously generated questions:\n"
            + "\n".join(q["text"] for q in prev_questions)
            + "\n\nRecent global questions:\n"
            + "\n".join(q["text"] for q in global_questions)
            + "\n\nDO NOT generate a question matching any of the above. Use different numerical values."
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
        prompt = lesson_plan_context.append_lesson_context(
            prompt, "shape_fractions", grade_band)

        response_text = llm_client.generate_text(
            prompt, schema=question_schemas.shape_fractions())

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

        required_keys = ["scenario", "question_text", "parts", "shaded"]
        if not all(k in question_data for k in required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", question_data)
            continue

        if question_data["scenario"] != "part_whole":
            print(f"[Attempt {attempt+1}] Wrong scenario:",
                  question_data["scenario"])
            continue

        if grade_appropriateness.refuse(question_data.get("question_text"),
                                        "shape_fractions", grade_band,
                                        difficulty, attempt + 1):
            continue

        # A digit in the text writes out the count the picture exists to be
        # read for. Checked rather than only requested.
        text = question_data.get("question_text")
        if not isinstance(text, str) or re.search(r"\d", text):
            print(f"[Attempt {attempt+1}] Digits in the question text:",
                  repr(text)[:80])
            continue

        # Required, like `graphs`: "what fraction of the shape is shaded" has
        # nothing to read without the shape.
        figure = question_figures.figure_for("part_whole", question_data)
        if figure is None:
            print(f"[Attempt {attempt+1}] Undrawable shape:",
                  repr({k: question_data.get(k) for k in ("parts", "shaded")}))
            continue

        solution = solve_shape_fraction(figure["parts"], figure["shaded"])
        if solution is None:
            print(f"[Attempt {attempt+1}] Ambiguous or unusable fraction:",
                  f'{question_data["shaded"]}/{question_data["parts"]}')
            continue

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    answers = generate_incorrect_answers(figure["parts"], figure["shaded"])
    correct = answer_format.format_value(solution)
    answers = [answer_format.format_value(a) for a in answers] + [correct]
    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "shape_fractions",
        "answer_options": answers,
        "correct_answer": correct,
        # Drawn from the same two numbers the answer is built from.
        "figure": figure,
    }
