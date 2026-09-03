# Generates a spread question and computes the answer exactly.
#
# CCSS S-ID.2 -- "use statistics appropriate to the shape of the data
# distribution to compare center and spread (interquartile range, standard
# deviation) of two or more different data sets". The third topic added for
# grades 9-12; see `hs_solvers` for the audit that made the case.
#
# It is standard deviation only, and that is the grade claim. Interquartile
# range and mean absolute deviation are the other measures S-ID.2 names, they
# are exactly scoreable, and they are **6.SP.5c** -- so a `spread` topic that
# offered them would score as grade-6 content on the very measure that
# motivated these topics. Same reasoning that puts `compose` in two of three
# tiers of `functions` rather than one.

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


ONE_SET = "population_sd"
TWO_SETS = "compare_spread"

# Difficulty selects the scenario, as in geometry and probability. `compare`
# is the hard tier because it is two full computations and a subtraction --
# and because comparing the spread of two sets is what S-ID.2 actually asks
# for, where a single set is the ingredient.
DIFFICULTY_SCENARIOS = {
    "easy":   ONE_SET,
    "medium": ONE_SET,
    "hard":   TWO_SETS,
}

# Deviations from the mean whose population variance is a perfect square, so
# the standard deviation is an integer.
#
# Hardcoded rather than searched: whether a multiset has an exact standard
# deviation is a property of the numbers, not something to discover per
# request, and a search would be a loop whose termination depends on its
# input -- which is the shape that has hung this codebase before. Every entry
# here sums to zero and is checked by `test_every_pattern_has_an_exact_sd`.
#
# Scaling all deviations by m scales the standard deviation by m exactly, so
# each line is a family rather than one dataset.
_DEVIATION_PATTERNS = (
    (-7, -1, 1, 7),                     # n=4, sd 5
    (-3, -1, 0, 1, 3),                  # n=5, sd 2
    (-6, -3, 1, 3, 5),                  # n=5, sd 4
    (-7, -3, 0, 2, 3, 5),               # n=6, sd 4
    (-7, -2, -1, 1, 4, 5),              # n=6, sd 4
    (-3, -2, -1, 0, 1, 2, 3),           # n=7, sd 2
    (-5, -4, -1, 0, 1, 2, 3, 4),        # n=8, sd 3
)

# What the values look like, per band. The mean is what moves them into a
# plausible range; the deviations decide the answer.
_MEAN_RANGE = {
    "early":    (10, 30),
    "middle":   (15, 60),
    "upper":    (20, 90),
    "advanced": (25, 120),
}


def _grade_band(grade):
    # Delegated so the copies cannot drift apart, and so an unreadable grade
    # ("Grade 1") lands in "early" rather than "advanced". See grade_levels.
    return grade_levels.grade_band(grade)


def _choose_dataset(grade_band, scale=1):
    """A dataset whose population standard deviation is a whole number.

    Built here rather than asked for, for the reason `quadratics` documents:
    a model choosing its own values would almost never land on an exact
    standard deviation -- most datasets have an irrational one -- so every
    question would be a retry against a constraint the model cannot see.

    Values are kept non-negative: a negative reading in a dataset described
    as heights or scores is a distractor of its own, and the wording is the
    model's to write.
    """
    low, high = _MEAN_RANGE.get(grade_band, _MEAN_RANGE["advanced"])
    deviations = [d * scale for d in random.choice(_DEVIATION_PATTERNS)]
    floor = -min(deviations)
    mean = random.randint(max(low, floor), max(high, floor + 10))
    values = [mean + d for d in deviations]
    random.shuffle(values)
    return values


_HEADER = """
You are to provide a Math question suitable for high school students. The response must be in JSON format.
The Question Topic will be "spread".
"""

_ONE_SET_BLOCK = """Scenario: population_sd

The question gives one data set and asks for its POPULATION standard deviation.

Example: "A coach records the number of points scored in each of five games:
14, 16, 17, 18, 20. What is the population standard deviation of the scores?"

The JSON must follow this exact structure:

{
  "question_text": "... 14, 16, 17, 18, 20 ... population standard deviation ...?",
  "question_topic": "spread",
  "scenario": "population_sd"
}
"""

_TWO_SETS_BLOCK = """Scenario: compare_spread

The question gives two data sets and asks HOW MUCH LARGER one population
standard deviation is than the other.

Example: "Two machines are tested. Machine A: 14, 16, 17, 18, 20. Machine B:
5, 11, 15, 17, 19. How much larger is the population standard deviation of
Machine B than that of Machine A?"

The JSON must follow this exact structure:

{
  "question_text": "... Set A ... Set B ... how much larger ...?",
  "question_topic": "spread",
  "scenario": "compare_spread"
}
"""

_FOOTER = """
You are given the data and what to ask for. Your ONLY job is to write the
sentence around them: a short, plausible context and the question. Do NOT
change any number, do NOT add a number, and do NOT work out the answer.

Rules for "question_text":
- It must contain each data set written EXACTLY as given below under DATA,
  in that order, comma separated, character for character.
- It must say "population standard deviation" in full. A question that says
  only "standard deviation" is ambiguous -- a student taught the sample
  formula would get a different number and be marked wrong.
- Vary the context between questions. Do not write any number that is not in
  the data.

Return ONLY valid JSON with no text before or after the JSON object.

Rules:
- Use ONLY double quotes for all strings.
- Do NOT include any characters outside the JSON object.
"""


def _prompt(scenario):
    """Header + the one selected scenario's block + footer. KeyError on an
    unknown scenario, for the reason `_geometry_prompt` documents."""
    block = {ONE_SET: _ONE_SET_BLOCK, TWO_SETS: _TWO_SETS_BLOCK}[scenario]
    return _HEADER + "\n" + block + _FOOTER


def _render(values):
    return ", ".join(str(v) for v in values)


def shown_matches_scored(question_text, shown):
    """Every data set on screen must be one being scored, and the question
    must name the population formula. A reason, or None.

    The data is rendered from what the solver reads rather than parsed back
    out of the text -- the direction `question_figures` establishes. And the
    "population" check is not pedantry: sample standard deviation over n-1 is
    what many courses teach, so a question that omits the word has two
    defensible answers and scores only one of them.
    """
    if not isinstance(question_text, str):
        return "question_text is not a string"
    for rendered in shown:
        if rendered not in question_text:
            return f"text does not contain the data {rendered!r}"
    if "population standard deviation" not in question_text.lower():
        return "text does not say 'population standard deviation'"
    return None


def generate_incorrect_answers(solution, near):
    """Near-misses first, the general generator for any gap.

    `near` carries the mistake the topic invites -- reporting the variance,
    which is the answer with the square root left off. Bounded by
    construction, never a search.
    """
    wrong = []
    for candidate in list(near) + [solution + 1, solution - 1, solution + 2,
                                   solution * 2]:
        if candidate is None or candidate == solution or candidate in wrong:
            continue
        if candidate < 0:
            continue                    # a spread is never negative
        wrong.append(candidate)
        if len(wrong) == 3:
            return wrong
    return inc_gen.generate_general_incorrect_answers(float(solution))


def generate_spread_question(global_questions, prev_questions,
                             difficulty, grade, max_retries=3):
    grade_band = _grade_band(grade)
    scenario = DIFFICULTY_SCENARIOS.get(difficulty, ONE_SET)
    # Settled before the loop, as in quadratics and functions: a retry is
    # then only ever the model failing to write the sentence.
    if scenario == TWO_SETS:
        first = _choose_dataset(grade_band)
        # A different scale, so the two spreads differ and the comparison has
        # an answer. Drawn until they do rather than assumed: two patterns
        # can share a standard deviation, and "how much larger" would then be
        # 0 -- true, and not a question anyone means to ask.
        second = None
        for _ in range(20):
            candidate = _choose_dataset(grade_band, scale=random.choice((2, 3)))
            if hs_solvers.population_sd(candidate)[0] != \
                    hs_solvers.population_sd(first)[0]:
                second = candidate
                break
        if second is None:
            raise ValueError("could not build two datasets with different spreads")
        low, _r1 = hs_solvers.population_sd(first)
        high, _r2 = hs_solvers.population_sd(second)
        if high < low:
            first, second, low, high = second, first, high, low
        solution = high - low
        shown = [_render(first), _render(second)]
        near = [hs_solvers.population_variance(second),
                hs_solvers.population_variance(first), high, low]
        data_note = (f"DATA:\n  Set A: {shown[0]}\n  Set B: {shown[1]}\n\n"
                     "ASK FOR: how much larger the population standard "
                     "deviation of Set B is than that of Set A.")
    else:
        values = _choose_dataset(grade_band)
        solution, reason = hs_solvers.population_sd(values)
        if solution is None:
            # Unreachable: every pattern is checked by its own test. Raising
            # rather than retrying, as quadratics does -- no model call can
            # change these numbers.
            raise ValueError(f"built a dataset with no exact spread: {reason}")
        shown = [_render(values)]
        near = [hs_solvers.population_variance(values)]
        data_note = (f"DATA:\n  {shown[0]}\n\n"
                     "ASK FOR: the population standard deviation.")

    for attempt in range(max_retries):
        prompt = _prompt(scenario)
        if attempt > 0:
            prompt += "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT."

        prompt += (
            "\nPreviously generated questions:\n"
            + "\n".join(q["text"] for q in prev_questions)
            + "\n\nRecent global questions:\n"
            + "\n".join(q["text"] for q in global_questions)
            + "\n\nDO NOT generate a question matching any of the above. Use different wording."
        )
        prompt += (
            f"\nGenerate a question of this topic that a {grade} student would consider to be of {difficulty} difficulty.\n"
        )
        prompt += f"\n{data_note}\n"
        prompt = lesson_plan_context.append_lesson_context(prompt, "spread", grade_band)

        response_text = llm_client.generate_text(
            prompt, schema=question_schemas.spread(scenario))

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

        # The reply's own scenario against the one asked for. Selecting a
        # block is a prompt-level act; only this is enforcement.
        if question_data.get("scenario") != scenario:
            print(f"[Attempt {attempt+1}] Wrong scenario: "
                  f"{question_data.get('scenario')!r}, asked for {scenario!r}")
            continue

        mismatch = shown_matches_scored(question_data["question_text"], shown)
        if mismatch:
            print(f"[Attempt {attempt+1}] Shown/scored mismatch: {mismatch}")
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
        "question_topic": "spread",
        "answer_options": answers,
        "correct_answer": correct,
    }
