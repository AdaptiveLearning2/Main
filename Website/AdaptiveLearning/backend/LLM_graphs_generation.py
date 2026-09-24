# Generates a bar-graph reading question (1.MD.4, 2.MD.10) and solves it exactly.
#
# The one topic whose figure is required: the counts live only in the graph, so
# a figure that cannot be built is a retry, not a fail-open.

import json
import random
import re

import llm_client
import lesson_plan_context
import question_figures
import question_schemas
import grade_levels
import ccss_standards
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


# Block number -> scenario name; cross-checked against the blocks by a test,
# since the solver dispatches on the name.
_SCENARIO_NAMES = {
    1: "how_many_total",
    2: "how_many_more",
}

SCENARIO_BLOCKS = {
    1: '''Scenario 1: how_many_total
"The graph shows the pets in Ms Lee's class. How many pets are there altogether?"
JSON for this scenario must follow this exact structure:
{
  "question_text": "The graph shows the pets in Ms Lee's class. How many pets are there altogether?",
  "question_topic": "graphs",
  "scenario": "how_many_total",
  "categories": [
    {"name": "cats", "count": "6"},
    {"name": "dogs", "count": "4"},
    {"name": "fish", "count": "3"}
  ],
  "target": []
}''',
    2: '''Scenario 2: how_many_more
"The graph shows the pets in Ms Lee's class. How many more cats than dogs are there?"
JSON for this scenario must follow this exact structure:
{
  "question_text": "The graph shows the pets in Ms Lee's class. How many more cats than dogs are there?",
  "question_topic": "graphs",
  "scenario": "how_many_more",
  "categories": [
    {"name": "cats", "count": "6"},
    {"name": "dogs", "count": "4"},
    {"name": "fish", "count": "3"}
  ],
  "target": ["cats", "dogs"]
}''',
}

GRAPHS_HEADER = """
You are to provide a Math question suitable for young students. The response must be in JSON format.
The Question Topic will be "graphs".

The student is shown a BAR GRAPH and answers a question by reading it. The graph
is drawn from "categories" -- you do not describe it in words.

Generate a question for the one scenario given below.
"""

GRAPHS_FOOTER = """

Rules:
- "categories" must be a list of 2 to 5 entries, each with a "name" and a "count".
- Each "count" must be a whole number from 1 to 20, written as a string.
- Every "name" must be different, and must be a simple plural noun a young
  child knows (cats, apples, books, cars).
- For "how_many_more", the question asks how many more of the LARGER category
  than the smaller, and "target" names EXACTLY TWO categories in the order the
  question names them. For "how_many_total", "target" must be an empty list.
- "question_text" must NOT contain any digits. The counts are in the graph --
  writing them in the question is giving away the reading the student is
  being asked to do.
- "question_text" must not describe the bars in words either. Refer to "the
  graph".
- Use ONLY double quotes for all strings.
- Do NOT include any characters outside the JSON object.
"""


def _graphs_prompt(scenario):
    """Header + the selected block + footer + the scenario as a rule. KeyError if unknown."""
    name = _SCENARIO_NAMES[scenario]
    return (GRAPHS_HEADER + "\n" + SCENARIO_BLOCKS[scenario] + GRAPHS_FOOTER
            + f'- "scenario" MUST be exactly "{name}"\n')


def _grade_band(grade):
    # An unreadable grade ("Grade 1") lands in "early", not "advanced".
    return grade_levels.grade_band(grade)


# Only "early" is reachable (TOPIC_MAX_GRADE is 3); the rest is defense-in-depth.
COMPLEXITY_BY_GRADE = {
    "early": {
        "easy":   "Use 2 categories with counts of 10 or below.",
        "medium": "Use 3 categories with counts of 10 or below.",
        "hard":   "Use 4 categories with counts of 20 or below.",
    },
    "middle": {
        "easy":   "Use 3 categories with counts of 20 or below.",
        "medium": "Use 4 categories with counts of 20 or below.",
        "hard":   "Use 5 categories with counts of 20 or below.",
    },
    "upper": {
        "easy":   "Use 4 categories with counts of 20 or below.",
        "medium": "Use 5 categories with counts of 20 or below.",
        "hard":   "Use 5 categories with counts of 20 or below.",
    },
    "advanced": {
        "easy":   "Use 4 categories with counts of 20 or below.",
        "medium": "Use 5 categories with counts of 20 or below.",
        "hard":   "Use 5 categories with counts of 20 or below.",
    },
}

# 1.MD.4 allows three categories, 2.MD.10 four; the early hard tier alone would
# give a 1st grader a grade-2 graph.
GRADE_OVERRIDES = {
    1: "This student is in GRADE 1. Use at most THREE categories and counts of 10 or below (1.MD.4).",
}

_SCENARIOS_BY_DIFFICULTY = {
    "easy":   [1],
    "medium": [2],
    "hard":   [2],
}


def _pick_scenario(difficulty):
    """Total for easy, comparison for harder. No `scenario_tiers`: no grade filter applies."""
    return random.choice(
        _SCENARIOS_BY_DIFFICULTY.get(difficulty, _SCENARIOS_BY_DIFFICULTY["medium"]))


def solve_graph(scenario, categories, target):
    """The answer, or None (a retry) if the reply does not determine one.

    No sympy, so no bounded subprocess: small integers only.
    """
    counts = {}
    for entry in categories:
        try:
            counts[entry["name"].strip().lower()] = int(entry["count"])
        except (AttributeError, KeyError, TypeError, ValueError):
            return None

    if scenario == "how_many_total":
        return sum(counts.values()) if counts else None

    if scenario == "how_many_more":
        if not isinstance(target, list) or len(target) != 2:
            return None
        try:
            names = [t.strip().lower() for t in target]
        except AttributeError:
            return None
        if names[0] == names[1] or any(n not in counts for n in names):
            return None
        difference = counts[names[0]] - counts[names[1]]
        # Refuse a non-positive difference; abs() would score a different question.
        return difference if difference > 0 else None

    return None


_HOW_MANY_MORE = re.compile(r"how\s+many\s+more\b", re.I)


def _target_follows_text(text, target):
    """True if the text asks "how many more" and then names both `target` categories, in order.

    Read after the last "how many more": that is the comparison asked, not an earlier mention.
    """
    if not isinstance(text, str) or not isinstance(target, list) or len(target) != 2:
        return False
    asks = list(_HOW_MANY_MORE.finditer(text))
    if not asks:
        return False
    question = text[asks[-1].end():]
    found = [re.search(rf"\b{re.escape(str(name).strip())}\b", question, re.I) for name in target]
    return all(found) and found[0].start() < found[1].start()


def generate_incorrect_answers(solution, counts):
    """Near-misses first: individual bars and off-by-one reads."""
    candidates = [solution + 1, solution - 1, *counts, sum(counts),
                  solution + 2, solution + 10]
    wrong = []
    for candidate in candidates:
        if candidate >= 0 and candidate != solution and candidate not in wrong:
            wrong.append(candidate)
        if len(wrong) == 3:
            return wrong
    return inc_gen.generate_general_incorrect_answers(float(solution))


def generate_graphs_question(global_questions, prev_questions, difficulty,
                             grade, max_retries=3):
    grade_band = _grade_band(grade)
    for attempt in range(max_retries):
        scenario = _pick_scenario(difficulty)
        prompt = _graphs_prompt(scenario)
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
        prompt += (
            f"\nCOMPLEXITY FOR THIS GRADE AND DIFFICULTY: "
            f"{COMPLEXITY_BY_GRADE[grade_band].get(difficulty, COMPLEXITY_BY_GRADE[grade_band]['medium'])}\n"
        )
        override = GRADE_OVERRIDES.get(grade_levels.grade_number(grade))
        if override:
            prompt += "\nGRADE-SPECIFIC RULE: " + override + "\n"
        prompt = lesson_plan_context.append_lesson_context(prompt, "graphs", grade_band)

        response_text = llm_client.generate_text(
            prompt, schema=question_schemas.graphs(_SCENARIO_NAMES[scenario]))

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

        required_keys = ["scenario", "question_text", "categories"]
        if not all(k in question_data for k in required_keys):
            print(f"[Attempt {attempt+1}] Missing keys:", question_data)
            continue

        # Check the scenario returned: the solver dispatches on it.
        if question_data["scenario"] != _SCENARIO_NAMES[scenario]:
            print(f"[Attempt {attempt+1}] Wrong scenario:",
                  question_data["scenario"])
            continue

        if grade_appropriateness.refuse(question_data.get("question_text"),
                                        "graphs", grade_band, difficulty,
                                        attempt + 1):
            continue

        # A digit in the text gives away a count; enforced, not just requested.
        text = question_data.get("question_text")
        if not isinstance(text, str) or re.search(r"\d", text):
            print(f"[Attempt {attempt+1}] Digits in the question text:",
                  repr(text)[:80])
            continue

        # The figure is required here: without it the question has no answer on screen.
        figure = question_figures.figure_for(question_data["scenario"],
                                             question_data)
        if figure is None:
            print(f"[Attempt {attempt+1}] Unusable categories:",
                  repr(question_data.get("categories"))[:80])
            continue

        # The subtraction is target[0] - target[1], so it must be the comparison on screen.
        if question_data["scenario"] == "how_many_more" and not _target_follows_text(
                text, question_data.get("target")):
            print(f"[Attempt {attempt+1}] Target is not the comparison the text asks:",
                  repr(question_data.get("target"))[:60])
            continue

        solution = solve_graph(question_data["scenario"],
                               question_data["categories"],
                               question_data.get("target"))
        if solution is None:
            print(f"[Attempt {attempt+1}] No single answer:",
                  repr(question_data.get("target"))[:60])
            continue

        break

    else:
        raise ValueError("Failed to generate valid JSON after retries")

    counts = [bar["value"] for bar in figure["bars"]]
    incorrect = generate_incorrect_answers(solution, counts)
    answers = [answer_format.format_value(a) for a in incorrect]
    correct = answer_format.format_value(solution)
    answers.append(correct)
    random.shuffle(answers)

    return {
        "question_text": question_data["question_text"],
        "question_topic": "graphs",
        "ccss_standard": ccss_standards.ccss_for("graphs", grade),
        "answer_options": answers,
        "correct_answer": correct,
        # Drawn from the same list the solver summed.
        "figure": figure,
    }
