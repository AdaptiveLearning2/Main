# Generates a bar-graph reading question and solves it exactly.
#
# CCSS 1.MD.4 ("organize, represent and interpret data with up to three
# categories; ask and answer questions about how many more or less") and
# 2.MD.10 (a bar graph with up to four categories, and compare problems using
# the information in it).
#
# THIS IS THE ONE TOPIC WHOSE FIGURE IS REQUIRED, NOT AN ENRICHMENT. Everywhere
# else a figure that cannot be built costs the picture and nothing else,
# because the question text stands alone: "a rectangle split into 3 rows of 4
# same-size squares" is answerable read aloud. "How many more cats than dogs?"
# is not -- the counts live only in the graph. So this generator treats a
# figure it could not build as an unusable reply and retries, which inverts the
# fail-open rule in exactly one place. `question_figures.figure_for` keeps its
# own contract and still returns None rather than raising; the decision that
# None is fatal belongs to the topic that cannot do without it.
#
# It is also the visual precursor to `mean`/`median`/`mode`: a student reads
# counts off a graph here at grade 1-2, and computes statistics over a listed
# dataset at grade 6.

import json
import random
import re

import llm_client
import lesson_plan_context
import question_figures
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


# Block number -> the scenario name that block asks for. A literal, like the
# other three, and cross-checked against the blocks by a test: the prompt names
# the wanted scenario by number while the reply must carry the matching name,
# and the solver dispatches on the name it is given.
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
- For "how_many_more", "target" must name EXACTLY TWO of the categories, the
  larger one first. For "how_many_total", "target" must be an empty list.
- "question_text" must NOT contain any digits. The counts are in the graph --
  writing them in the question is giving away the reading the student is
  being asked to do.
- "question_text" must not describe the bars in words either. Refer to "the
  graph".
- Use ONLY double quotes for all strings.
- Do NOT include any characters outside the JSON object.
"""


def _graphs_prompt(scenario):
    """Header + the one selected scenario's block + footer, and the scenario
    restated as a rule. KeyError on an unknown scenario, for the reason
    `_geometry_prompt` documents."""
    name = _SCENARIO_NAMES[scenario]
    return (GRAPHS_HEADER + "\n" + SCENARIO_BLOCKS[scenario] + GRAPHS_FOOTER
            + f'- "scenario" MUST be exactly "{name}"\n')


def _grade_band(grade):
    # Delegated so the copies cannot drift apart, and so an unreadable grade
    # ("Grade 1") lands in "early" rather than "advanced". See grade_levels.
    return grade_levels.grade_band(grade)


# Only "early" is reachable -- TOPIC_MAX_GRADE caps this at grade 3 -- and the
# rest is defense-in-depth, like the other capped topics.
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

# 1.MD.4 is "up to three categories". 2.MD.10 is four. Difficulty and grade are
# independent inputs, so a "hard" 1st grader is a real state and the band's
# hard tier alone would hand them a grade-2 graph.
GRADE_OVERRIDES = {
    1: "This student is in GRADE 1. Use at most THREE categories and counts of 10 or below (1.MD.4).",
}

_SCENARIOS_BY_DIFFICULTY = {
    "easy":   [1],
    "medium": [2],
    "hard":   [2],
}


def _pick_scenario(difficulty):
    """Reading a total is one addition; comparing two bars is a reading *and* a
    subtraction, which is the harder half of 1.MD.4. Not ranked through
    `scenario_tiers` because there are two scenarios and no grade filter
    removes either -- the ordering cannot invert."""
    return random.choice(
        _SCENARIOS_BY_DIFFICULTY.get(difficulty, _SCENARIOS_BY_DIFFICULTY["medium"]))


def solve_graph(scenario, categories, target):
    """The answer, or None if the reply does not determine one.

    `None` rather than a raise: the caller is inside the retry loop. No sympy,
    so no bounded subprocess -- this is a sum or a subtraction over small
    integers the figure builder has already bounded.
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
        # Negative means the question asked how many more of the *smaller*
        # category, which reads as a question with no answer for these grades.
        # Refused rather than answered with an absolute value, which would
        # score a different question from the one on screen.
        return difference if difference > 0 else None

    return None


def generate_incorrect_answers(solution, counts):
    """Near-misses first -- the individual bars and an off-by-one read are the
    mistakes this question tests for. Bounded by construction: a fixed
    candidate list, not a search."""
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

        # The scenario that came back, not the one that was asked for. The
        # solver dispatches on the name it is given.
        if question_data["scenario"] != _SCENARIO_NAMES[scenario]:
            print(f"[Attempt {attempt+1}] Wrong scenario:",
                  question_data["scenario"])
            continue

        if grade_appropriateness.refuse(question_data.get("question_text"),
                                        "graphs", grade_band, difficulty,
                                        attempt + 1):
            continue

        # A digit in the text is a count written out, which hands the student
        # the reading the question exists to ask for. Checked rather than only
        # requested: it is a prompt rule everywhere else in this codebase that
        # leaked at least once.
        text = question_data.get("question_text")
        if not isinstance(text, str) or re.search(r"\d", text):
            print(f"[Attempt {attempt+1}] Digits in the question text:",
                  repr(text)[:80])
            continue

        # THE FIGURE IS REQUIRED HERE. Built before the solve, because a reply
        # whose categories cannot be drawn is not a graph question at all --
        # "how many more cats than dogs" with no graph has no answer on screen.
        figure = question_figures.figure_for(question_data["scenario"],
                                             question_data)
        if figure is None:
            print(f"[Attempt {attempt+1}] Unusable categories:",
                  repr(question_data.get("categories"))[:80])
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
        "answer_options": answers,
        "correct_answer": correct,
        # Drawn from the same list the solver summed, so the bar a student
        # counts is the number being scored.
        "figure": figure,
    }
