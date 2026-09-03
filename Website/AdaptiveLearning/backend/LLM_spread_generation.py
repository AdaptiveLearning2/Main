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
import re

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

# Scenario-specific rules, kept out of the shared footer because a rule the
# scenario cannot use is not merely noise -- it is a suggestion. Told about
# "Set A:"/"Set B:" labels and about comparisons on a ONE_SET prompt,
# llama3.1:8b invented both: it labelled a single data set "Set A", made up a
# "Set B: 74", and asked for Set B's standard deviation -- which would have
# been scored as Set A's. Measured 4 refusals in 6 one-set generations before
# this split, and every one of them mentioned a label or a comparison.
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
    """Header + the one selected scenario's block + footer. KeyError on an
    unknown scenario, for the reason `_geometry_prompt` documents."""
    block, rules = {ONE_SET: (_ONE_SET_BLOCK, _ONE_SET_RULES),
                    TWO_SETS: (_TWO_SETS_BLOCK, _TWO_SETS_RULES)}[scenario]
    return _HEADER + "\n" + block + _FOOTER + rules + _FOOTER_TAIL


def _render(values):
    return ", ".join(str(v) for v in values)


# A written-out data set: two or more numbers separated by commas. One number
# on its own is not one, so "each of 5 games" is prose rather than data.
_DATA_RUN = re.compile(r"\d+(?:\s*,\s*\d+)+")


def _data_runs(text):
    """Every data set the text puts on screen, in the order it writes them."""
    return [", ".join(part.strip() for part in run.group().split(","))
            for run in _DATA_RUN.finditer(text)]


# What the question actually asks, which is only ever an interrogative clause.
# Searching the whole text conflates the ask with the context around it, and
# that is wrong in both directions: a comparative in the context refuses a
# perfectly good one-set question ("A coach checks how much more consistent
# the team is. Scores: ... What is the population standard deviation?" breaks
# no stated rule, and the prompt actively asks for varied context), and a
# comparative in the context would equally *accept* a two-set reply whose real
# ask is one set's own value.
_ASKED = re.compile(r"[^.!?]*\?")

# The parts of a comparison, kept apart rather than welded into one pattern so
# that word order does not have to be guessed. Pinning the comparative ahead
# of the label reads naturally and is not the only way to write it: "Set B's
# population standard deviation is how much larger than Set A's?" and "By how
# much does the population standard deviation of Set B exceed that of Set A?"
# are both exactly the scored question, and a single ordered regex refused
# both. Only the prompt was holding that, which is one model away from a 503.
_MAGNITUDE = re.compile(r"\bhow much\b|\bwhat amount\b", re.IGNORECASE)
_INCREASE = re.compile(r"\b(?:larger|greater|bigger|higher|more|exceeds?)\b",
                       re.IGNORECASE)
_DECREASE = re.compile(r"\b(?:smaller|lesser|less|lower|fewer)\b",
                       re.IGNORECASE)
_SET_A = re.compile(r"\bset\s+a\b", re.IGNORECASE)
_SET_B = re.compile(r"\bset\s+b\b", re.IGNORECASE)


def _questions_asked(text):
    """The interrogative clauses, which is where the ask lives."""
    return [m.group() for m in _ASKED.finditer(text)]


def _asks_the_scored_comparison(clause):
    """Set B's spread exceeding Set A's, however the clause words it.

    Direction rides on the order the labels appear in rather than on where
    the comparative sits, because the sign is the whole content: the same
    sentence with the labels the other way round is the negation of what is
    scored. A decrease word makes the direction ambiguous, so it is refused
    rather than guessed at.

    `_MAGNITUDE` is what separates this from "which is larger, Set B or Set
    A?" -- that names both labels in the scored order, and its answer is a
    label where a number is scored.
    """
    if not _MAGNITUDE.search(clause) or _DECREASE.search(clause):
        return False
    if not _INCREASE.search(clause):
        return False
    a, b = _SET_A.search(clause), _SET_B.search(clause)
    return bool(a and b and b.start() < a.start())


def _asks_any_comparison(clause):
    """A one-set question drifting into a comparison, which can only be
    against a number the model invented -- and a bare number is not a run, so
    the data check cannot see it."""
    return bool(_MAGNITUDE.search(clause)
                and (_INCREASE.search(clause) or _DECREASE.search(clause)))


def shown_matches_scored(question_text, shown, labelled=(), scenario=ONE_SET):
    """The data on screen must be exactly the data being scored, under the
    labels the question asks about, and the question must ask for the quantity
    that is scored. A reason, or None.

    The data is rendered from what the solver reads rather than parsed back
    out of the text -- the direction `question_figures` establishes. And the
    "population" check is not pedantry: sample standard deviation over n-1 is
    what many courses teach, so a question that omits the word has two
    defensible answers and scores only one of them.

    **Containment is not enough, and the two-set scenario is why.** Asking
    only whether each rendered set appears somewhere binds neither the extent
    of a set nor which label it sits under, so three wrong questions pass it:
    a sixth value appended ("14, 16, 17, 18, 20, 25" contains "14, 16, 17,
    18, 20"), the sets written in the other order, and -- the one no ordering
    check catches -- the same sets in the same order with the labels swapped,
    which asks for B minus A while A minus B is scored and puts the negation
    of the answer on screen. So the runs must match *exactly and in order*,
    which binds extent and order, and each labelled set must appear verbatim
    with its label, which binds the rest. `functions` gets this for free by
    carrying "f(x) =" inside its shown strings; here the label lives in the
    prose, so it has to be required.

    **Binding the data is not binding the question.** Two correctly labelled
    sets in the right order can still carry the wrong ask: "what is the
    population standard deviation of Set B?" over Set A sd 2 and Set B sd 5
    is scored 3, and 5 is on the option list, because `near` offers each
    set's own spread as a distractor. So a student who answers the question
    actually on screen picks a distractor and is marked wrong -- the failure
    this codebase treats as the worst available, reached through the one part
    of the reply nothing had checked. `quadratics` binds its ask with
    `_TARGET_WORDS` and `functions` with the literal `f(g(4))`; the `ASK FOR:`
    line here was prompt-level only.

    Strict rather than fail-open, unlike `question_consistency`'s checks over
    a dataset the *model* chose. Here the generator supplied every number, so
    a text carrying any other one is not an ambiguity to interpret
    generously -- it is a reply that ignored its instructions, and refusing
    costs a retry where accepting costs a wrong answer.
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
        # No interrogative clause refuses too, deliberately: the ask cannot be
        # located, and for this scenario an unlocatable ask is the unsafe
        # direction -- accepting one costs a wrong answer, refusing costs a
        # retry.
        if not any(_asks_the_scored_comparison(c) for c in asked):
            return ("text does not ask how much larger Set B's population "
                    "standard deviation is than Set A's, which is what is "
                    "scored")
    elif any(_asks_any_comparison(c) for c in asked):
        return "text asks a comparison, but only one data set is scored"
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
        # Which set carries which label is the whole question here -- "how
        # much larger is B than A" scored against swapped labels puts the
        # negation of the answer on screen -- so the label travels with the
        # data into the check rather than living only in the prompt.
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
            # Unreachable: every pattern is checked by its own test. Raising
            # rather than retrying, as quadratics does -- no model call can
            # change these numbers.
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

        # The reply's own scenario against the one asked for. Selecting a
        # block is a prompt-level act; only this is enforcement.
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
        "answer_options": answers,
        "correct_answer": correct,
    }
