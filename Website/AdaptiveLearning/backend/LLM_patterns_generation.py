# Generates a number-sequence question and solves it exactly.
# CCSS 1.NBT.1, 2.NBT.2, 3.OA.9, 4.OA.5, 5.OA.3; capped at grade 5 by TOPIC_MAX_GRADE.
# Arithmetic sequences only: other rules can be ambiguous, and the solver scores one answer.

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
MIN_TERMS = 4          # three known terms plus the blank: fewer is not a pattern
MAX_TERMS = 8

# Small integers only and no sympy, so no bounded subprocess is needed.
_NUMBER = re.compile(r"^\d{1,5}$")

patterns_prompt = """
You are to provide a Math question suitable for young students. The response must be in JSON format.
The Question Topic will be "patterns".

The question shows a number sequence with ONE number replaced by "?" and asks for it.

Example: "What number is missing? 3, 6, 9, ?, 15"

Rules for "values":
- It must be a list of 4 to 8 strings.
- EXACTLY ONE entry must be the string "?".
- Every other entry must be a whole number with no decimal point and no sign.
- The sequence must go up by the SAME amount each time (a constant step).
- The step must be a whole number of 1 or more.
- Every number in the sequence must be 0 or more.

Rules for "question_text":
- It must contain the sequence written exactly as the values read, separated by
  ", ": for ["3", "6", "9", "?", "15"] the text must contain "3, 6, 9, ?, 15".
- Use "?" for the blank. Do NOT use "x", "n", a letter, or a line of underscores.
- Do NOT include any other digits anywhere in the question text.

Return ONLY valid JSON with no text before or after the JSON object.

The JSON must follow this exact structure:

{
  "question_text": "What number is missing? 3, 6, 9, ?, 15",
  "question_topic": "patterns",
  "values": ["3", "6", "9", "?", "15"]
}

Rules:
- Use ONLY double quotes for all strings.
- "values" must be a list of strings.
- Do NOT include any characters outside the JSON object.
"""


def _grade_band(grade):
    # Shared so copies can't drift; profiles.grade_level is free text. See grade_levels.
    return grade_levels.grade_band(grade)


# "upper"/"advanced" are unreachable (grade 5 cap), kept as defence in depth.
COMPLEXITY_BY_GRADE = {
    "early": {
        "easy":   "Use 4 or 5 numbers counting up by 1 or by 2, all 20 or below.",
        "medium": "Use 5 numbers counting up by 2, 5 or 10, all 50 or below.",
        "hard":   "Use 5 or 6 numbers counting up by 2, 3, 5 or 10, all 120 or below.",
    },
    "middle": {
        "easy":   "Use 5 numbers with a constant step between 2 and 10, all 200 or below.",
        "medium": "Use 5 or 6 numbers with a constant step between 3 and 25, all 500 or below.",
        "hard":   "Use 6 numbers with a constant step between 5 and 50, all 1000 or below. Put the blank in the MIDDLE of the sequence, not at the end.",
    },
    "upper": {
        "easy":   "Use 5 numbers with a constant step between 5 and 50.",
        "medium": "Use 6 numbers with a constant step between 10 and 100. Put the blank in the middle.",
        "hard":   "Use 6 or 7 numbers with a constant step between 25 and 250. Put the blank in the middle.",
    },
    "advanced": {
        "easy":   "Use 6 numbers with a constant step between 25 and 100.",
        "medium": "Use 6 or 7 numbers with a constant step between 50 and 500. Put the blank in the middle.",
        "hard":   "Use 7 or 8 numbers with a constant step between 100 and 1000. Put the blank in the middle.",
    },
}

# Skip-counting is 2.NBT.2, so a "hard" 1st grader would otherwise get grade-2 content.
GRADE_OVERRIDES = {
    1: "This student is in GRADE 1. Count up by 1 or by 2 only, and every number must be 20 or below (1.NBT.1).",
}


def solve_pattern(values):
    """The missing term, or None if the sequence does not determine one.

    The step is checked against every known term, not just the first pair (2, 4, 6, ?, 9).
    """
    if not isinstance(values, list) or not MIN_TERMS <= len(values) <= MAX_TERMS:
        return None
    if not all(isinstance(v, str) for v in values):
        return None
    if values.count(BLANK) != 1:
        return None
    if not all(_NUMBER.match(v) for v in values if v != BLANK):
        return None

    known = [(i, int(v)) for i, v in enumerate(values) if v != BLANK]
    if len(known) < 2:
        return None

    span = known[-1][0] - known[0][0]
    if span == 0:
        return None
    total = known[-1][1] - known[0][1]
    if total % span:
        return None                      # a fractional step is not these grades
    step = total // span
    if step < 1:
        return None                      # ascending sequences only, per the prompt

    base_index, base_value = known[0]
    for index, value in known:
        if value != base_value + (index - base_index) * step:
            return None

    blank_index = values.index(BLANK)
    answer = base_value + (blank_index - base_index) * step
    return answer if answer >= 0 else None


def _sequence_text(values):
    return ", ".join(values)


def shown_matches_scored(question_text, values):
    """A reason the on-screen sequence differs from the scored `values`, or None if it matches."""
    if not isinstance(question_text, str):
        return "question_text is not a string"
    shown = re.sub(r"\s+", " ", question_text)
    sequence = _sequence_text(values)
    if sequence not in shown:
        return f"text does not contain {sequence!r}"
    without = shown.replace(sequence, " ", 1)
    if re.search(r"\d", without):
        return "question_text carries digits outside the sequence"
    return None


def generate_incorrect_answers(solution, values, step):
    """Off-by-one-step first, since stepping wrong is the mistake being tested."""
    candidates = [solution + step, solution - step, solution + 1, solution - 1,
                  solution + 2 * step]
    wrong = []
    for candidate in candidates:
        if candidate >= 0 and candidate != solution and candidate not in wrong:
            wrong.append(candidate)
        if len(wrong) == 3:
            return wrong
    return inc_gen.generate_general_incorrect_answers(float(solution))


def _step_of(values):
    known = [(i, int(v)) for i, v in enumerate(values) if v != BLANK]
    return (known[-1][1] - known[0][1]) // (known[-1][0] - known[0][0])


def generate_patterns_question(global_questions, prev_questions,
                               difficulty, grade, max_retries=3):
    grade_band = _grade_band(grade)
    for attempt in range(max_retries):
        if attempt > 0:
            prompt = patterns_prompt + "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."
        else:
            prompt = patterns_prompt

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
        prompt = lesson_plan_context.append_lesson_context(prompt, "patterns", grade_band)

        response_text = llm_client.generate_text(
            prompt, schema=question_schemas.patterns())

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

        required_keys = ["values", "question_text"]
        if not all(k in question_data for k in required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", question_data)
            continue

        # Backstop on what the model produced, not what the prompt asked for.
        if grade_appropriateness.refuse(question_data.get("question_text"),
                                        "patterns", grade_band, difficulty,
                                        attempt + 1):
            continue

        # Solved in the loop, so an undetermined sequence is a retry.
        solution = solve_pattern(question_data["values"])
        if solution is None:
            print(f"[Attempt {attempt+1}] Sequence determines no answer:",
                  repr(question_data["values"])[:80])
            continue

        mismatch = shown_matches_scored(question_data.get("question_text"),
                                        question_data["values"])
        if mismatch:
            print(f"[Attempt {attempt+1}] Shown/scored mismatch: {mismatch}")
            continue

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    step = _step_of(question_data["values"])
    incorrect = generate_incorrect_answers(solution, question_data["values"], step)
    answers = [answer_format.format_value(a) for a in incorrect]
    correct = answer_format.format_value(solution)
    answers.append(correct)
    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "patterns",
        "ccss_standard": ccss_standards.ccss_for("patterns", grade),
        "answer_options": answers,
        "correct_answer": correct,
    }
