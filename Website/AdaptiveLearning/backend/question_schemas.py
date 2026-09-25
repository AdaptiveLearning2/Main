"""JSON schemas for the Claude generation calls, one per topic (sent as `output_config`).

A schema constrains shape only: every code-level check (grade gating, consistency, solvers)
still runs, Ollama sends no schema, and `extract_json` still handles truncated replies.
Scenario keys derive from `geometry_solvers.SCENARIO_VARS` so the two cannot drift.
"""

import angle_solvers
import geometry_solvers

# `question_topic` is required to match the prompts' examples; no code reads it.
_TEXT = {"type": "string"}
_STRING_LIST = {"type": "array", "items": {"type": "string"}}


def _object(properties, required=None):
    """A closed object (`additionalProperties: False`): an unexpected key is drift."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(required if required is not None else properties),
        "additionalProperties": False,
    }


def token_list(topic):
    """algebra, expressions, rationals: an expression as a token list.

    Tokens mix numerals and operators, so no pattern; the bounded worker validates them.
    """
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "variables": _STRING_LIST})


def dataset(topic):
    """mean, median, mode: a list of numeric values, as pattern-constrained strings."""
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "variables": {"type": "array",
                                  "items": {"type": "string",
                                            "pattern": r"^-?\d+(\.\d+)?$"}}})


def ordering():
    """values plus the direction they are to be sorted in.

    `direction` is an enum because `solve_ordering` treats anything else as least-to-greatest.
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
    """`variables` is a closed object with exactly this scenario's `SCENARIO_VARS` keys."""
    keys = sorted(geometry_solvers.SCENARIO_VARS[scenario_name])
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "scenario": {"type": "string", "enum": [scenario_name]},
                    "variables": _object({k: _TEXT for k in keys})})


def angles(scenario_name):
    """`variables` is a list of numeric strings, with the scenario pinned.

    Length is not constrained: the API rejects `minItems` > 1, so `SCENARIO_ARITY` stays a
    runtime check. `_EXPRESSION_SCENARIOS` take expressions in `x`, so get no numeral pattern.
    """
    items = _TEXT if scenario_name in _EXPRESSION_SCENARIOS else \
        {"type": "string", "pattern": r"^-?\d+(\.\d+)?$"}
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "scenario": {"type": "string", "enum": [scenario_name]},
                    "variables": {"type": "array", "items": items}})


# The angle scenarios whose variables are expressions rather than numbers.
_EXPRESSION_SCENARIOS = frozenset({"algebra_complementary"})


def probability(scenario_name):
    """Only `dice` gets a schema; the bag scenarios return None and keep `extract_json`.

    Pinning the enum matters: the solver dispatches on the scenario name the reply carries.
    """
    if scenario_name != "dice":
        # `items` needs an open object (invented category names), which the API refuses.
        return None
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "scenario": {"type": "string", "enum": ["dice"]},
                    "sides": {"type": "string", "pattern": r"^\d+$"},
                    "target": {"type": "array", "items": {"type": "string"}}})


def missing_number():
    """`variables` is an equation with exactly one `?` in it.

    Length and blank position are not expressible; `solve_missing` enforces both.
    The token pattern keeps `x` out, which is what keeps this at grade 1.
    """
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "variables": {"type": "array",
                                  "items": {"type": "string",
                                            "pattern": r"^(\d{1,4}|[+\-*]|=|\?)$"}}})


def kindergarten():
    """Wording only: every number and the answer are chosen in code."""
    return _object({"question_text": _TEXT, "question_topic": _TEXT})


def patterns():
    """A number sequence with the term to find marked `?`."""
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "values": {"type": "array",
                               "items": {"type": "string",
                                         "pattern": r"^(\d{1,5}|\?)$"}}})


def graphs(scenario_name):
    """A bar chart's data, as a *list* of named counts.

    A list of closed objects because the API refuses an open name->count map.
    `question_figures._bar_chart` enforces the 2..5 category count.
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
    """Just the sentence: `_choose_coefficients` builds the equation, which the reply must contain verbatim."""
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT})


def functions(scenario_name):
    """The sentence, plus the scenario it was written for.

    The functions are chosen in code, as for `quadratics`; `scenario` is a cross-check the generator enforces.
    """
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "scenario": {"type": "string", "enum": [scenario_name]}})


def spread(scenario_name):
    """The sentence, with the scenario pinned; `_choose_dataset` picks data with a rational SD."""
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "scenario": {"type": "string", "enum": [scenario_name]}})


def shape_fractions():
    """A shape's part count and how many are shaded.

    `question_figures._part_whole` enforces `1 <= shaded < parts`; the generator enforces lowest terms.
    """
    return _object({"question_text": _TEXT,
                    "question_topic": _TEXT,
                    "scenario": {"type": "string", "enum": ["part_whole"]},
                    "parts": {"type": "string", "pattern": r"^\d$"},
                    "shaded": {"type": "string", "pattern": r"^\d$"}})
