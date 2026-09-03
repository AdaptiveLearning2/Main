"""JSON schemas for the generation calls, one per topic.

`extract_json` hunts a JSON object out of prose because `llama3.1:8b` wraps its
replies in markdown fences and preamble. That is a property of *that model*,
and it was carried across to Claude untouched -- so the retry loop still
absorbs malformed JSON on a provider that can be told not to produce any.

A schema closes that: `output_config` constrains the reply server-side, so the
only malformed-JSON path left on the Claude branch is a reply truncated at
`max_tokens`. Measured against `claude-haiku-4-5`: a schema'd reply is a single
text block of bare JSON, no fence, no preamble.

Three things this does NOT do, and the first is the one to hold on to:

  * **It does not replace a single code-level check.** `grade_appropriateness`,
    `question_consistency`, `SCENARIO_VARS`, the scenario-grade checks and the
    bounded solvers all still run and all still matter. A schema constrains the
    *shape* of a reply, never whether the question inside it is solvable, in
    band, or consistent with the data it will be scored against. It is also
    enforced by the provider rather than by us, and only on one branch --
    Ollama sends no schema at all, so every one of those checks is the only
    thing standing between a dev-mode reply and a student.
  * **It does not let `extract_json` go.** The Ollama branch still needs it,
    and a truncated Claude reply still reaches it.
  * **It does not bound content.** A schema can say `variables` is an array of
    strings; it cannot say they are numbers a solver can use.

What it *does* buy beyond fewer retries: `scenario` is pinned to an enum of the
one scenario that was actually selected, and geometry's `variables` to exactly
the keys that scenario's solver reads. Both are derived from the tables the
solvers already use -- `geometry_solvers.SCENARIO_VARS` and
`angle_solvers.SCENARIO_ARITY` -- rather than restated here, because a second
copy of a scenario's variable keys is how the two drift apart and the model
gets told to produce something the solver cannot read.
"""

import angle_solvers
import geometry_solvers

# Every schema shares these. `question_topic` is required because the prompts'
# JSON examples all carry it and a reply omitting it would be off-spec -- not,
# as this comment used to say, because the code reads it. Nothing does any
# more: all ten generators return their own topic name as a literal, since the
# model's value reached `questions.subject` and credited the student's work to
# whatever it felt like calling the topic.
_TEXT = {"type": "string"}
_STRING_LIST = {"type": "array", "items": {"type": "string"}}


def _object(properties, required=None):
    """A closed object. `additionalProperties: False` throughout: an unexpected
    key is exactly the drift these schemas exist to stop, and nothing here
    reads a key it did not ask for."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(required if required is not None else properties),
        "additionalProperties": False,
    }


def token_list(topic):
    """algebra, expressions, rationals: an expression as a token list.

    Tokens mix numerals and operators ("36", "/", "(" ), so they are strings
    with no pattern -- constraining them here would reject the operators.
    `token_join` and the bounded worker are what validate them.
    """
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "variables": _STRING_LIST})


def dataset(topic):
    """mean, median, mode: a list of numeric values, as strings.

    The numerals are pattern-constrained. These three prompts each spend four
    lines insisting on it ("DO NOT use words like 'red'... If any value is not
    a number, the response is invalid"), which is a prompt-level rule and so
    leaks; the schema is where it can actually be enforced.
    """
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "variables": {"type": "array",
                                  "items": {"type": "string",
                                            "pattern": r"^-?\d+(\.\d+)?$"}}})


def ordering():
    """values plus the direction they are to be sorted in.

    `direction` is an enum of the two the solver dispatches on. It was a free
    string, and `solve_ordering` treats anything that is not
    "greatest_to_least" as least-to-greatest -- so a typo'd direction silently
    sorted the wrong way and scored the student against it.
    """
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "direction": {"type": "string",
                                  "enum": ["least_to_greatest",
                                           "greatest_to_least"]},
                    "values": _STRING_LIST})


def expressions(scenario_name):
    """As `token_list`, with `scenario` pinned to the one that was selected."""
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "scenario": {"type": "string", "enum": [scenario_name]},
                    "variables": _STRING_LIST})


def geometry(scenario_name):
    """`variables` is an object, and its required keys are this scenario's.

    Derived from `geometry_solvers.SCENARIO_VARS`, which is what the solver
    indexes. Measured against Haiku: it returned a
    `rect_perimeter_missing_side` carrying `rect_area_missing_side`'s keys --
    two scenarios blended, which was a KeyError before `SCENARIO_VARS` was
    checked at runtime. A closed object with exactly these keys makes that
    reply unrepresentable rather than merely rejected.
    """
    keys = sorted(geometry_solvers.SCENARIO_VARS[scenario_name])
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "scenario": {"type": "string", "enum": [scenario_name]},
                    "variables": _object({k: _TEXT for k in keys})})


def angles(scenario_name):
    """`variables` is a list of numeric strings, with the scenario pinned.

    The *length* is deliberately not constrained, and not for want of trying:
    the API rejects `minItems` above 1 outright --

        For 'array' type, 'minItems' values other than 0 or 1 are not
        supported

    -- so `angle_solvers.SCENARIO_ARITY` cannot be expressed here. It stays a
    runtime check, which is where it already was; the schema simply does not
    take that job over. Worth stating rather than leaving as a silent gap: a
    reader who sees every other shape pinned would reasonably assume this one
    is too, and stop checking the arity.
    """
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "scenario": {"type": "string", "enum": [scenario_name]},
                    "variables": {"type": "array",
                                  "items": {"type": "string",
                                            "pattern": r"^-?\d+(\.\d+)?$"}}})


def probability(scenario_name):
    """Two shapes, and which one is decided before the prompt is built.

    `probability_of`/`not_probability_of` carry `items`, a map of category name
    to count; `dice` carries `sides` and a list `target`. As one schema that is
    a union, and the prompt says as much ("items" or "sides"). It does not have
    to be: `_pick_scenario` runs before the call, so the scenario is known and
    each gets its own schema.

    Only `dice` gets one. `items`' keys are category names the model invents,
    which needs an open object, and the API refuses those -- so those two
    scenarios return `None` and keep the `extract_json` path.

    Pinning the enum matters more here than anywhere else: this prompt sends
    all three scenario blocks and names the wanted one by *number* ("scenario
    3"), while the reply must carry the matching *name* -- and the solver
    dispatches on the name it is given, not the one that was asked for.
    """
    if scenario_name != "dice":
        # `items` maps category names the model invents to counts, so the
        # object has to stay open -- and the API refuses that:
        #
        #     For 'object' type, 'additionalProperties: object' is not
        #     supported. Please set 'additionalProperties' to false
        #
        # Closing it would mean fixing the category names in advance, which is
        # the question. So these two scenarios get no schema at all and keep
        # the `extract_json` path, and `None` says so rather than a schema that
        # quietly permits anything.
        return None
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "scenario": {"type": "string", "enum": ["dice"]},
                    "sides": {"type": "string", "pattern": r"^\d+$"},
                    "target": {"type": "array", "items": {"type": "string"}}})


def missing_number():
    """`variables` is an equation with exactly one `?` in it.

    Neither the length nor the position of the blank is expressible -- the API
    refuses `minItems` above 1, so "exactly five tokens" cannot be stated here
    any more than angle arity could. `solve_missing` enforces both, and is the
    only thing that does.

    The token pattern allows a whole number, the three operators, `=` and `?`,
    which is narrow enough to keep `x` out. That matters more here than
    elsewhere: this topic is one notation away from `algebra`, and the `?` is
    what keeps it at grade 1.
    """
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "variables": {"type": "array",
                                  "items": {"type": "string",
                                            "pattern": r"^(\d{1,4}|[+\-*]|=|\?)$"}}})


def patterns():
    """A number sequence with the term to find marked `?`."""
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "values": {"type": "array",
                               "items": {"type": "string",
                                         "pattern": r"^(\d{1,5}|\?)$"}}})


def graphs(scenario_name):
    """A bar chart's data, as a *list* of named counts.

    A list of closed objects rather than the obvious `{"cats": "7"}` map: the
    API refuses an open `additionalProperties`, so a map keyed on names the
    model invents cannot be expressed at all -- the reason probability's bag
    scenarios get no schema. Choosing the shape that can be schema'd is
    cheaper than accepting the gap, and costs the generator one indirection.

    Length is not expressible (`minItems` above 1 is refused), so
    `question_figures._bar_chart` enforces 2..5 categories and it is the only
    thing that does.
    """
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "scenario": {"type": "string", "enum": [scenario_name]},
                    "categories": {
                        "type": "array",
                        "items": _object({
                            "name": _TEXT,
                            "count": {"type": "string", "pattern": r"^\d{1,2}$"},
                        })},
                    "target": _STRING_LIST})


def quadratics():
    """Just the sentence. The equation is not the model's to write.

    The narrowest schema here, and deliberately: `_choose_coefficients` builds
    the equation, the generator renders it into the prompt, and the reply is
    required to contain that string verbatim. So there is nothing for the
    model to return but the wording around it.

    It did carry `coefficients`, and the measurement is why it does not.
    Across three promptings llama3.1:8b produced a factorable quadratic 0 of
    3, 2 of 3 and 1 of 4 times -- almost every failure `irrational roots`,
    because a freely chosen b and c almost never leave b^2 - 4ac a perfect
    square. Every one of those retries was a billed call that could not have
    succeeded.

    `target` is absent for the older version of the same reason: which root is
    asked for is settled before the call, so a reply cannot disagree about it.
    A reply naming one root in the text and another in a field is the one
    failure the coefficients could never have revealed.
    """
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT})


def functions(scenario_name):
    """The sentence, plus the scenario it was written for.

    The functions and the value to evaluate at are not the model's to choose,
    for the reason `quadratics` above documents -- and this topic had the
    sharper version of it. Asked for its own coefficients, the model also had
    to reproduce `hs_solvers.render_polynomial`'s exact spacing and sign
    conventions, because the text is required to contain that string verbatim.
    Measured on llama3.1:8b with the lesson plan injected, `compose` failed 3
    of 3 exactly there -- coefficients returned, prose written differently --
    which is two of the topic's three tiers.

    `scenario` stays, unlike quadratics' absent `target`: it is a cheap
    cross-check that the model read the block it was handed, and the generator
    refuses a reply naming the other one.
    """
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "scenario": {"type": "string", "enum": [scenario_name]}})


def spread(scenario_name):
    """The sentence, with the scenario pinned.

    The data is not the model's to choose. Most datasets have an irrational
    population standard deviation, so a model picking its own values would
    fail a constraint it cannot see on nearly every attempt --
    `_choose_dataset` builds from deviation patterns whose variance is a
    perfect square, and the generator renders them into the prompt.
    """
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "scenario": {"type": "string", "enum": [scenario_name]}})


def shape_fractions():
    """A shape's part count and how many of them are shaded.

    Neither the relationship between them nor the lowest-terms rule is
    expressible in a schema -- `question_figures._part_whole` enforces
    `1 <= shaded < parts` and the generator enforces the rest, and they are
    the only things that do.
    """
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "scenario": {"type": "string", "enum": ["part_whole"]},
                    "parts": {"type": "string", "pattern": r"^\d$"},
                    "shaded": {"type": "string", "pattern": r"^\d$"}})
