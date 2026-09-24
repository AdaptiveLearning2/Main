# Generates kindergarten questions: counting, comparing, adding and subtracting, teen
# numbers and shapes (CCSS K.CC, K.OA, K.NBT, K.G), in the question types of IXL's
# kindergarten skills. Code picks the scenario and every number and computes the
# answer; the model only words the question. See docs/question-generation.md.

import json
import random
import re

import llm_client
import question_schemas
import ccss_standards
import grade_appropriateness
import answer_format
import question_figures
from question_figures import FIGURE_ITEMS, SHAPE_SIDES

TOPICS = ("counting", "comparing_numbers", "add_and_subtract", "teen_numbers", "shapes")

# Topic -> difficulty -> scenarios. Each tier stays inside its standard's own limit.
SCENARIOS = {
    "counting": {
        "easy":   ["count_objects", "one_more"],
        "medium": ["count_objects", "one_more", "one_less", "next_number"],
        "hard":   ["count_objects", "one_less", "next_number", "count_by_tens"],
    },
    "comparing_numbers": {
        "easy":   ["larger_number", "smaller_number"],
        "medium": ["larger_number", "smaller_number", "compare_groups"],
        "hard":   ["largest_of_three", "compare_groups"],
    },
    "add_and_subtract": {
        "easy":   ["add", "subtract"],
        "medium": ["add", "subtract", "add_story", "subtract_story"],
        "hard":   ["add_story", "subtract_story", "make_ten"],
    },
    "teen_numbers": {
        "easy":   ["count_ten_frames"],
        "medium": ["teen_make"],
        "hard":   ["teen_take_apart"],
    },
    "shapes": {
        "easy":   ["name_shape"],
        "medium": ["count_sides"],
        "hard":   ["count_corners"],
    },
}

NUMBER_WORDS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                "fourteen fifteen sixteen seventeen eighteen nineteen twenty").split()
# Words that make a story take-away (K.OA.2); an addition story must use none of them.
TAKE_AWAY = ("left", "away", "ate", "gave", "lost", "fell", "flew", "popped", "broke", "sold")
MAX_WORDS = 40


def plural(item):
    return item if item == "fish" else item + "s"


def _plan(topic, scenario, difficulty, rng):
    """Everything but the wording: numbers shown, answer, options, figure, and what to ask.

    `shown` is every number the text may carry, in order; `require` is groups of words
    of which each needs one; `forbid` is words the text may not use.
    """
    limit = {"easy": 5, "medium": 10, "hard": 20}[difficulty]
    item = rng.choice(FIGURE_ITEMS)
    items = plural(item)
    p = {"shown": [], "options": None, "figure": None, "require": [], "forbid": [],
         "sentences": 2}

    if scenario == "count_objects":
        low = {"easy": 1, "medium": 6, "hard": 11}[difficulty]
        n = rng.randint(low, limit)
        p.update(answer=n, figure={"groups": [{"item": item, "count": n}]},
                 brief=f"The picture shows some {items}. Ask how many {items} there are.",
                 example=f"How many {items} are there?", require=[[items]])
    elif scenario == "one_more":
        # From 2: at 1 the model writes "one more than one" and fails the digit rule.
        n = rng.randint(*{"easy": (2, 4), "medium": (2, 9), "hard": (10, 19)}[difficulty])
        # An exact phrase: told only the words, both models wrote a get-one-more story.
        p.update(shown=[n], answer=n + 1, phrase=f"one more than {n}",
                 brief=f"Ask what number is one more than {n}. Ask about the number, not a story.",
                 example=f"What number is one more than {n}?")
    elif scenario == "one_less":
        n = rng.randint(*{"easy": (3, 5), "medium": (3, 10), "hard": (11, 20)}[difficulty])
        p.update(shown=[n], answer=n - 1, phrase=f"one less than {n}",
                 brief=f"Ask what number is one less than {n}. Ask about the number, not a story.",
                 example=f"What number is one less than {n}?")
    elif scenario == "next_number":
        s = rng.randint(*{"easy": (1, 2), "medium": (1, 7), "hard": (8, 17)}[difficulty])
        p.update(shown=[s, s + 1, s + 2], answer=s + 3, require=[["next"]],
                 brief=f"Show the numbers {s}, {s + 1}, {s + 2} and ask what number comes next.",
                 example=f"Count on. {s}, {s + 1}, {s + 2}. What number comes next?")
    elif scenario == "count_by_tens":
        s = 10 * rng.randint(1, 6)
        p.update(shown=[s, s + 10, s + 20], answer=s + 30, require=[["next"]],
                 brief=f"Count by tens: show {s}, {s + 10}, {s + 20} and ask what number comes next.",
                 example=f"Count by tens. {s}, {s + 10}, {s + 20}. What number comes next?")
    elif scenario in ("larger_number", "smaller_number", "largest_of_three"):
        top = min(limit, 10)                       # K.CC.7 compares numerals 1 to 10
        numbers = rng.sample(range(1, top + 1), 3 if scenario == "largest_of_three" else 2)
        larger = scenario != "smaller_number"
        word = {"larger_number": "larger", "smaller_number": "smaller",
                "largest_of_three": "largest"}[scenario]
        p.update(answer=max(numbers) if larger else min(numbers), options=numbers,
                 brief=f"Ask which number is {word}. The numbers are the answer choices, "
                       f"so do not write any number in the question.",
                 example=f"Which number is {word}?",
                 require=[["which number"],
                          ["larger", "greater", "bigger", "largest", "greatest", "biggest"]
                          if larger else ["smaller", "less", "smallest", "least"]],
                 forbid=["smaller", "smallest", "less", "least"] if larger
                        else ["larger", "greater", "bigger", "largest", "greatest"])
    elif scenario == "compare_groups":
        first, second = rng.sample(FIGURE_ITEMS, 2)
        a, b = rng.sample(range(1, min(limit, 10) + 1), 2)
        fewer = difficulty == "hard" and rng.random() < 0.5
        word, other = ("fewer", "more") if fewer else ("more", "fewer")
        winner = first if (a < b) == fewer else second      # a != b: drawn by `sample`
        p.update(answer=plural(winner), options=[plural(first), plural(second)],
                 figure={"groups": [{"item": first, "count": a}, {"item": second, "count": b}]},
                 brief=f"The picture shows some {plural(first)} and some {plural(second)}. "
                       f"Ask whether there are {word} {plural(first)} or {word} {plural(second)}.",
                 example=f"Are there {word} {plural(first)} or {word} {plural(second)}?",
                 require=[[plural(first)], [plural(second)], [word]],
                 forbid=[other, "less", "same", "how many"])
    elif scenario in ("add", "subtract"):
        total = 5 if difficulty == "easy" else 10
        if scenario == "add":
            a = rng.randint(1, total - 1)
            b = rng.randint(1, total - a)
            sign, answer = "+", a + b
        else:
            a = rng.randint(2, total)
            b = rng.randint(1, a - 1)
            sign, answer = "-", a - b
        verb = "Add" if sign == "+" else "Subtract"
        p.update(shown=[a, b], answer=answer, equation=f"{a} {sign} {b}",
                 brief=f"{verb}: write the number sentence '{a} {sign} {b} = ?' and ask for the answer.",
                 example=f"{verb}. {a} {sign} {b} = ?")
    elif scenario == "add_story":
        a = rng.randint(2, 8)
        b = rng.randint(2, 10 - a)
        p.update(shown=[a, b], answer=a + b, sentences=3, require=[[items]], forbid=list(TAKE_AWAY),
                 brief=f"Write a very short story: there are {a} {items}, then {b} more {items} join them. "
                       f"Ask how many {items} there are now.",
                 example=f"There are {a} {items} in the yard. Then {b} more {items} come. "
                         f"How many {items} are there now?")
    elif scenario == "subtract_story":
        a = rng.randint(3, 10)
        b = rng.randint(2, a - 1)
        p.update(shown=[a, b], answer=a - b, sentences=3,
                 require=[[items], list(TAKE_AWAY)], forbid=["more", "in all", "altogether"],
                 brief=f"Write a very short story: there are {a} {items}, then {b} of them go away. "
                       f"Ask how many {items} are left.",
                 example=f"There are {a} {items} on a plate. {b} {items} are taken away. "
                         f"How many {items} are left?")
    elif scenario == "make_ten":
        n = rng.randint(1, 9)
        p.update(shown=[n, 10], answer=10 - n, require=[["make"]],
                 brief=f"Ask what number goes with {n} to make 10.",
                 example=f"What number goes with {n} to make 10?")
    elif scenario == "count_ten_frames":
        n = rng.randint(11, 19)
        p.update(answer=n, figure={"count": n}, require=[["dots"]],
                 brief="The picture shows dots in ten frames. Ask how many dots there are.",
                 example="How many dots are there?")
    elif scenario == "teen_make":
        k = rng.randint(1, 9)
        p.update(shown=[10, k], answer=10 + k, require=[["make", "+"]],
                 brief=f"Ask what number 10 and {k} make.",
                 example=f"10 and {k} make what number?")
    elif scenario == "teen_take_apart":
        n = rng.randint(11, 19)
        p.update(shown=[n, 10], answer=n - 10, figure={"count": n}, require=[["and"]],
                 brief=f"The picture shows {n} dots in ten frames. Ask: {n} is 10 and how many more?",
                 example=f"{n} is 10 and how many more?")
    elif scenario == "name_shape":
        shape = rng.choice(list(SHAPE_SIDES))
        others = rng.sample([s for s in SHAPE_SIDES if s != shape], 3)
        p.update(answer=shape, options=[shape, *others], figure={"shape": shape},
                 require=[["shape"]], forbid=[*SHAPE_SIDES, *(plural(s) for s in SHAPE_SIDES)],
                 brief="The picture shows a flat shape. Ask what the shape is called. Do not name it.",
                 example="What is the name of this shape?")
    elif scenario in ("count_sides", "count_corners"):
        shape = rng.choice([s for s in SHAPE_SIDES if SHAPE_SIDES[s]])
        part = "sides" if scenario == "count_sides" else "corners"
        p.update(answer=SHAPE_SIDES[shape], figure={"shape": shape}, require=[[part]],
                 brief=f"The picture shows a {shape}. Ask how many {part} it has.",
                 example=f"How many {part} does this {shape} have?")
    else:
        raise ValueError(f"no kindergarten scenario {scenario!r}")

    p["scenario"] = scenario
    if isinstance(p["answer"], int):
        # The answer as a word would give it away where the digit is refused.
        p["forbid"] = [*p["forbid"], NUMBER_WORDS[p["answer"]]] if p["answer"] <= 20 else p["forbid"]
    return p


def _has(text, word):
    """`word` in `text` as a whole word (or phrase); symbols match anywhere."""
    if not re.match(r"\w", word):
        return word in text
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


def wording_problem(text, plan):
    """Why `text` cannot be served for `plan`, or None."""
    if not isinstance(text, str) or not text.strip():
        return "question_text is empty"
    if len(text.split()) > MAX_WORDS:
        return "question_text is too long for a kindergartner"
    lower = re.sub(r"[‒-―−]", "-", text.lower())
    numbers = [int(n) for n in re.findall(r"\d+", lower)]
    if numbers != plan["shown"]:
        return f"shows numbers {numbers}, expected {plan['shown']}"
    spaced = re.sub(r"\s+", " ", lower)
    for exact in (plan.get("equation"), plan.get("phrase")):
        if exact and exact not in spaced:
            return f"does not contain {exact!r}"
    for group in plan["require"]:
        if not any(_has(lower, word) for word in group):
            return f"uses none of {group}"
    for word in plan["forbid"]:
        if _has(lower, word):
            return f"uses {word!r}"
    return None


def _prompt(topic, plan, grade, global_questions, prev_questions):
    shown = ", ".join(str(n) for n in plan["shown"]) or "none -- do not write any number"
    # The checks in `wording_problem`, stated: an 8B model left out an unstated word most often.
    rules = [f"- It MUST use {' or '.join(repr(w) for w in group)}." for group in plan["require"]]
    if plan.get("equation"):
        rules.append(f"- It MUST contain exactly \"{plan['equation']} = ?\".")
    if plan.get("phrase"):
        rules.append(f"- It MUST contain exactly \"{plan['phrase']}\".")
    if plan["forbid"]:
        rules.append(f"- Do NOT use these words: {', '.join(plan['forbid'])}.")
    must = "\n".join(rules)
    return f"""
You write one maths question for a {grade} student, who is five years old.
The Question Topic is "{topic}".

WHAT TO ASK: {plan['brief']}
THE STYLE, not to be copied word for word: "{plan['example']}"

Rules for "question_text":
- Very short, simple sentences a five-year-old can follow; at most {plan['sentences']} sentences.
- Write numbers as digits. The ONLY numbers in the question are: {shown}, in that order.
- Do NOT give the answer, in digits or in words.
- Do NOT use a letter such as x or n for a number.
{must}

Previously generated questions:
{chr(10).join(q["text"] for q in prev_questions)}

Recent global questions:
{chr(10).join(q["text"] for q in global_questions)}

Use different wording from all of the above.

Return ONLY valid JSON, with double quotes, and nothing outside the object:
{{"question_text": "{plan['example']}", "question_topic": "{topic}"}}
"""


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
                return text[start:i + 1]
    return None


def _wrong_numbers(answer, plan):
    """Three near-misses: off by one and two, then the numbers shown."""
    wrong = []
    for candidate in (answer + 1, answer - 1, answer + 2, answer - 2, *plan["shown"], answer + 3):
        if candidate >= 0 and candidate != answer and candidate not in wrong:
            wrong.append(candidate)
    return wrong[:3]


def generate_kindergarten_question(global_questions, prev_questions, difficulty, grade,
                                   max_retries=3, *, topic=None, rng=random):
    """A question on `topic`, one of TOPICS; None picks one."""
    topic = rng.choice(TOPICS) if topic is None else topic
    if topic not in SCENARIOS:
        raise ValueError(f"not a kindergarten topic: {topic!r}")
    if difficulty not in ("easy", "medium", "hard"):
        difficulty = "medium"
    plan = _plan(topic, rng.choice(SCENARIOS[topic][difficulty]), difficulty, rng)
    scenario = plan["scenario"]

    for attempt in range(max_retries):
        prompt = _prompt(topic, plan, grade, global_questions, prev_questions)
        if attempt > 0:
            prompt += "\nREMEMBER: ONLY RETURN VALID JSON. NO EXTRA TEXT.\n"
        response_text = llm_client.generate_text(prompt, schema=question_schemas.kindergarten())

        raw = extract_json(response_text)
        if not raw:
            print(f"[Attempt {attempt+1}] No JSON found")
            continue
        try:
            question_data = json.loads(raw)
        except Exception as e:
            print(f"[Attempt {attempt+1}] JSON parse failed:", e)
            continue
        text = question_data.get("question_text") if isinstance(question_data, dict) else None

        if grade_appropriateness.refuse(text, topic, "early", difficulty, attempt + 1):
            continue
        problem = wording_problem(text, plan)
        if problem:
            print(f"[Attempt {attempt+1}] Kindergarten wording refused ({scenario}): {problem}")
            continue
        break
    else:
        raise ValueError("Failed to generate valid JSON after retries")

    answer = plan["answer"]
    if plan["options"] is not None:
        options = list(plan["options"])
    else:
        options = [answer, *_wrong_numbers(answer, plan)]
    options = [answer_format.format_value(o) if isinstance(o, int) else o for o in options]
    rng.shuffle(options)

    figure = None
    if plan["figure"] is not None:
        figure = question_figures.figure_for(scenario, plan["figure"])
        if figure is None:
            raise ValueError(f"kindergarten figure for {scenario!r} could not be built")

    return {
        "question_text": text.strip(),
        "question_topic": topic,
        "ccss_standard": ccss_standards.ccss_for(topic, grade, scenario),
        "answer_options": options,
        "correct_answer": answer_format.format_value(answer) if isinstance(answer, int) else answer,
        # Drawn from the same numbers the answer is computed from.
        "figure": figure,
    }
