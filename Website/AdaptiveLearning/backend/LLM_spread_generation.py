# Generates a spread question and computes the answer exactly.
# CCSS S-ID.2, standard deviation only: IQR and MAD are 6.SP.5c, i.e. grade-6 content.

import json
import random
import re

import llm_client
from llm_json import extract_json
import lesson_plan_context
import question_schemas
import grade_levels
import ccss_standards
import hs_solvers
import incorrect_solution_generation as inc_gen
import answer_format


ONE_SET = "population_sd"
TWO_SETS = "compare_spread"

# Difficulty selects the scenario; comparing two sets is what S-ID.2 actually asks.
DIFFICULTY_SCENARIOS = {
    "easy":   ONE_SET,
    "medium": ONE_SET,
    "hard":   TWO_SETS,
}

# Zero-sum deviations with an integer population SD; hardcoded, not searched, so bounded.
# Scaling by m scales the SD by m. Checked by `test_every_pattern_has_an_exact_sd`.
_DEVIATION_PATTERNS = (
    (-7, -1, 1, 7),                     # n=4, sd 5
    (-3, -1, 0, 1, 3),                  # n=5, sd 2
    (-6, -3, 1, 3, 5),                  # n=5, sd 4
    (-7, -3, 0, 2, 3, 5),               # n=6, sd 4
    (-7, -2, -1, 1, 4, 5),              # n=6, sd 4
    (-3, -2, -1, 0, 1, 2, 3),           # n=7, sd 2
    (-5, -4, -1, 0, 1, 2, 3, 4),        # n=8, sd 3
)

# Mean range per band; the deviations decide the answer.
_MEAN_RANGE = {
    "early":    (10, 30),
    "middle":   (15, 60),
    "upper":    (20, 90),
    "advanced": (25, 120),
}


def _grade_band(grade):
    # Shared so copies can't drift; profiles.grade_level is free text. See grade_levels.
    return grade_levels.grade_band(grade)


def _choose_dataset(grade_band, scale=1):
    """A non-negative dataset whose population standard deviation is a whole number.

    Built in code: a model's own values almost never have an exact SD.
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

The two sets MUST be labelled "Set A" and "Set B", exactly as they are given
under DATA. The context around them is yours to write; the labels are not.

Example: "Two machines are tested. Set A: 14, 16, 17, 18, 20. Set B: 5, 11,
15, 17, 19. How much larger is the population standard deviation of Set B
than that of Set A?"

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
- It must contain the data written EXACTLY as given below under DATA, in that
  order, comma separated, character for character.
- It must contain no other list of numbers at all.
- It must say "population standard deviation" in full. A question that says
  only "standard deviation" is ambiguous -- a student taught the sample
  formula would get a different number and be marked wrong.
"""

# Kept out of the shared footer: a rule the scenario can't use reads as a suggestion.
_ONE_SET_RULES = """- It must ask for the population standard deviation of the one data set, and
  nothing else. Do NOT compare it with anything: not with the mean, not with
  another data set, not with a number of your own.
- There is one data set and it has no name. Do NOT label it.
"""

_TWO_SETS_RULES = """- It must contain the "Set A:" and "Set B:" labels exactly as DATA gives
  them. A set written under the other set's label is a different question
  from the one being scored.
- It must ask HOW MUCH LARGER Set B's population standard deviation is than
  Set A's, naming Set B before Set A. Asking for either set's own value, or
  comparing them the other way round, is a different question from the one
  being scored.
"""

_FOOTER_TAIL = """- Vary the context between questions. Do not write any number that is not in
  the data.

Return ONLY valid JSON with no text before or after the JSON object.

Rules:
- Use ONLY double quotes for all strings.
- Do NOT include any characters outside the JSON object.
"""


def _prompt(scenario):
    """Header + the selected scenario's block + footer. KeyError on an unknown scenario."""
    block, rules = {ONE_SET: (_ONE_SET_BLOCK, _ONE_SET_RULES),
                    TWO_SETS: (_TWO_SETS_BLOCK, _TWO_SETS_RULES)}[scenario]
    return _HEADER + "\n" + block + _FOOTER + rules + _FOOTER_TAIL


def _render(values):
    return ", ".join(str(v) for v in values)


# Two or more comma-separated numbers; a lone number ("each of 5 games") is prose.
_DATA_RUN = re.compile(r"\d+(?:\s*,\s*\d+)+")


def _data_runs(text):
    """Every data set the text puts on screen, in the order it writes them."""
    return [", ".join(part.strip() for part in run.group().split(","))
            for run in _DATA_RUN.finditer(text)]


# The ask is read from interrogative clauses only, so context wording can't flip the check.
_ASKED = re.compile(r"[^.!?]*\?")
_SENTENCE = re.compile(r"[^.!?]+[.!?]?")

# Separate patterns so word order need not be guessed ("... exceed that of Set A?").
_MAGNITUDE = re.compile(r"\bhow much\b|\bwhat amount\b", re.IGNORECASE)
_INCREASE = re.compile(r"\b(?:larger|greater|bigger|higher|more|exceeds?)\b",
                       re.IGNORECASE)
_DECREASE = re.compile(r"\b(?:smaller|lesser|less|lower|fewer)\b",
                       re.IGNORECASE)
_SET_A = re.compile(r"\bset\s+a\b", re.IGNORECASE)
_SET_B = re.compile(r"\bset\s+b\b", re.IGNORECASE)


def _questions_asked(text):
    """The interrogative clauses, or the closing sentence when the ask is an instruction.

    Never empty for non-empty text: an empty result would skip the one-set comparison guard.
    """
    asked = [m.group() for m in _ASKED.finditer(text)]
    if asked:
        return asked
    sentences = [s for s in (m.group().strip()
                             for m in _SENTENCE.finditer(text)) if s]
    return sentences[-1:]


def _without_data(clause, labelled):
    """The clause with the labelled data removed, so the data's labels don't set the direction.

    Relies on the anchors having been checked verbatim and the runs matched exactly first.
    """
    for anchor in labelled:
        clause = clause.replace(anchor, " ")
    return clause


def _asks_the_scored_comparison(clause):
    """Whether the clause asks how much Set B's spread exceeds Set A's.

    Direction comes from label order; a decrease word is refused as ambiguous.
    `_MAGNITUDE` excludes "which is larger, Set B or Set A?".
    """
    if not _MAGNITUDE.search(clause) or _DECREASE.search(clause):
        return False
    if not _INCREASE.search(clause):
        return False
    a, b = _SET_A.search(clause), _SET_B.search(clause)
    return bool(a and b and b.start() < a.start())


def _asks_any_comparison(clause):
    """A one-set question comparing against an invented number, which the data check can't see."""
    return bool(_MAGNITUDE.search(clause)
                and (_INCREASE.search(clause) or _DECREASE.search(clause)))


def shown_matches_scored(question_text, shown, labelled=(), scenario=ONE_SET):
    """A reason the shown data, labels or ask differ from what is scored, or None.

    Runs must match exactly and in order, each labelled set must appear verbatim, and
    "population" is required (sample SD is a second defensible answer). Strict, since
    the generator supplied every number.
    """
    if not isinstance(question_text, str):
        return "question_text is not a string"
    runs = _data_runs(question_text)
    if runs != list(shown):
        return f"data on screen is {runs!r}, but {list(shown)!r} is scored"
    for anchor in labelled:
        if anchor not in question_text:
            return f"text does not contain {anchor!r}"
    if "population standard deviation" not in question_text.lower():
        return "text does not say 'population standard deviation'"
    asked = _questions_asked(question_text)
    if scenario == TWO_SETS:
        if not any(_asks_the_scored_comparison(_without_data(c, labelled))
                   for c in asked):
            return ("text does not ask how much larger Set B's population "
                    "standard deviation is than Set A's, which is what is "
                    "scored")
    elif any(_asks_any_comparison(c) for c in asked):
        return "text asks a comparison, but only one data set is scored"
    return None


def generate_incorrect_answers(solution, near):
    """Near-misses first (`near` holds the variance), the general generator for any gap."""
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
    # Settled before the loop, so a retry only re-asks for the sentence.
    if scenario == TWO_SETS:
        first = _choose_dataset(grade_band)
        # Redrawn (bounded) until the spreads differ, or the answer would be 0.
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
        # Labels travel with the data into the check: swapped labels negate the answer.
        labelled = [f"Set A: {shown[0]}", f"Set B: {shown[1]}"]
        near = [hs_solvers.population_variance(second),
                hs_solvers.population_variance(first), high, low]
        data_note = (f"DATA:\n  {labelled[0]}\n  {labelled[1]}\n\n"
                     "ASK FOR: how much larger the population standard "
                     "deviation of Set B is than that of Set A.")
    else:
        values = _choose_dataset(grade_band)
        solution, reason = hs_solvers.population_sd(values)
        if solution is None:
            # Unreachable (patterns are tested); a retry would not change the numbers.
            raise ValueError(f"built a dataset with no exact spread: {reason}")
        shown = [_render(values)]
        labelled = []               # one set, so there is nothing to mislabel
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

        # The prompt only requests the scenario; this enforces it.
        if question_data.get("scenario") != scenario:
            print(f"[Attempt {attempt+1}] Wrong scenario: "
                  f"{question_data.get('scenario')!r}, asked for {scenario!r}")
            continue

        mismatch = shown_matches_scored(question_data["question_text"],
                                        shown, labelled, scenario)
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
        "ccss_standard": ccss_standards.ccss_for("spread", grade),
        "answer_options": answers,
        "correct_answer": correct,
    }
