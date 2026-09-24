# Generates a "solve the quadratic" question and solves it exactly.
# CCSS A-REI.4b: high-school content, since every other topic tops out at grade 8.
# Not `algebra`, whose solver refuses a quadratic; asking which root makes two roots scoreable.

import json
import random

import llm_client
from llm_json import extract_json
import lesson_plan_context
import question_schemas
import grade_levels
import ccss_standards
import hs_solvers
import incorrect_solution_generation as inc_gen
import answer_format


# Which root is asked for; chosen in code so the solver knows what it scores.
TARGETS = ("larger", "smaller")

# Words for each root; the opposite root's words are what catch a mis-asked question.
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

# Root magnitude per band; equation shape per difficulty is `_TIERS`.
_ROOT_RANGE = {
    "early":    (1, 5),
    "middle":   (1, 8),
    "upper":    (1, 10),
    "advanced": (2, 12),
}

# `signs`: may a root be negative; `scales`: leading coefficient (2-4 means the AC method).
_TIERS = {
    "easy":   {"signs": "positive", "scales": (1,)},
    "medium": {"signs": "mixed",    "scales": (1,)},
    "hard":   {"signs": "mixed",    "scales": (2, 3, 4)},
}


def _choose_coefficients(difficulty, grade_band):
    """`(a, b, c)` for an equation that is factorable over the integers.

    Built in code, not by the model, which rarely produces integer roots;
    the model only writes the sentence.
    """
    low, high = _ROOT_RANGE.get(grade_band, _ROOT_RANGE["advanced"])
    tier = _TIERS.get(difficulty, _TIERS["medium"])
    p, q = random.sample(range(low, high + 1), 2)
    if tier["signs"] == "mixed":
        # One root negative, not both, so the pair straddles zero.
        p = -p
    scale = random.choice(tier["scales"])
    # scale * (x^2 - (p + q)x + pq): roots stay p and q.
    return scale, -scale * (p + q), scale * p * q


def _grade_band(grade):
    # Shared so copies can't drift; profiles.grade_level is free text. See grade_levels.
    return grade_levels.grade_band(grade)


def shown_matches_scored(question_text, a, b, c, target):
    """A reason the shown equation or requested root differs from what is scored, or None.

    The equation is rendered from the coefficients, not parsed from the text.
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
    """Near-misses first, the general generator for any gap; a fixed list, so bounded."""
    candidates = []
    other = hs_solvers.other_root(a, b, c, target)
    if other is not None:
        # Solved correctly, but the other root.
        candidates.append(other)
    # Sign errors, since roots come from `-b ± sqrt(...)`.
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
    # Decided before the loop, so a retry only re-asks for the sentence.
    a, b, c = _choose_coefficients(difficulty, grade_band)
    equation = hs_solvers.render_quadratic(a, b, c)
    solution, reason = hs_solvers.solve_quadratic(a, b, c, target)
    if solution is None:
        # Unreachable by construction; a retry would not change the coefficients.
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
        "ccss_standard": ccss_standards.ccss_for("quadratics", grade),
        "answer_options": answers,
        "correct_answer": correct,
    }
