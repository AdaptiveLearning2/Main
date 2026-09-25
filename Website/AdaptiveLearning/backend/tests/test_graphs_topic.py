"""`graphs` -- reading a bar graph (1.MD.4, 2.MD.10, 3.MD.3); the only topic whose figure is required."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import llm_client  # noqa: E402
import lesson_plan_context  # noqa: E402
import question_figures  # noqa: E402
import LLM_graphs_generation as graphs  # noqa: E402

PETS = [{"name": "cats", "count": "6"}, {"name": "dogs", "count": "4"},
        {"name": "fish", "count": "3"}]


def test_a_total_is_the_sum_of_every_bar():
    assert graphs.solve_graph("how_many_total", PETS, []) == 13


def test_a_comparison_is_the_difference_between_the_two_named_bars():
    assert graphs.solve_graph("how_many_more", PETS, ["cats", "dogs"]) == 2
    assert graphs.solve_graph("how_many_more", PETS, ["cats", "fish"]) == 3


def test_asking_how_many_more_of_the_smaller_bar_is_refused():
    """Refused, not answered with an absolute value: there are no "more dogs than cats"."""
    assert graphs.solve_graph("how_many_more", PETS, ["dogs", "cats"]) is None


@pytest.mark.parametrize("scenario,target,why", [
    ("how_many_more", ["cats"], "one target is not a comparison"),
    ("how_many_more", ["cats", "cats"], "a bar against itself"),
    ("how_many_more", ["cats", "moose"], "a category that is not on the graph"),
    ("how_many_more", None, "no target at all"),
    ("no_such_scenario", [], "a scenario the solver does not know"),
])
def test_a_reply_that_determines_no_answer_is_refused(scenario, target, why):
    assert graphs.solve_graph(scenario, PETS, target) is None, why


def test_the_figure_is_built_from_the_same_list_the_solver_reads():
    """The counts exist only in the picture, so it must come from the solver's list."""
    figure = question_figures.figure_for("how_many_more", {"categories": PETS})
    assert [bar["value"] for bar in figure["bars"]] == [6, 4, 3]
    assert [bar["label"] for bar in figure["bars"]] == ["cats", "dogs", "fish"]


@pytest.fixture
def reply(monkeypatch):
    monkeypatch.setattr(lesson_plan_context, "append_lesson_context",
                        lambda p, t, b: p)
    monkeypatch.setattr(graphs, "_pick_scenario", lambda difficulty: 2)

    def _use(payload):
        monkeypatch.setattr(llm_client, "generate_text",
                            lambda *a, **k: json.dumps(payload))
    return _use


VALID = {"question_text": "The graph shows the pets in Ms Lee's class. "
                          "How many more cats than dogs are there?",
         "question_topic": "graphs", "scenario": "how_many_more",
         "categories": PETS, "target": ["cats", "dogs"]}


def test_a_valid_reply_is_served_with_its_graph(reply):
    reply(VALID)
    question = graphs.generate_graphs_question([], [], "medium", "1st Grade")
    assert question["correct_answer"] == "2"
    assert question["correct_answer"] in question["answer_options"]
    assert len(set(question["answer_options"])) == 4
    assert question["figure"]["type"] == "bar_chart"
    assert len(question["figure"]["bars"]) == 3


def test_a_question_whose_graph_cannot_be_drawn_is_refused(reply):
    """Elsewhere a failed figure costs the picture; here it costs the question."""
    reply({**VALID, "categories": [{"name": "cats", "count": "6"}]})
    with pytest.raises(ValueError, match="after retries"):
        graphs.generate_graphs_question([], [], "medium", "1st Grade")


def test_a_question_that_writes_the_counts_out_is_refused(reply):
    """A digit in the text hands over the reading the question asks for."""
    reply({**VALID, "question_text": "The graph shows 6 cats and 4 dogs. "
                                     "How many more cats than dogs?"})
    with pytest.raises(ValueError, match="after retries"):
        graphs.generate_graphs_question([], [], "medium", "1st Grade")


def test_a_reply_answering_a_different_scenario_is_refused(reply):
    reply({**VALID, "scenario": "how_many_total", "target": []})
    with pytest.raises(ValueError, match="after retries"):
        graphs.generate_graphs_question([], [], "medium", "1st Grade")


@pytest.mark.parametrize("text", [
    # The smaller bar first: a false premise, whatever order `target` gives.
    "The graph shows the pets in Ms Lee's class. How many more dogs than cats are there?",
    "How many more cats than birds are there?",                    # one bar is not on the graph
    "How many cats and dogs are there?",                           # a total, not a comparison
    "How many more cats than dogs and fish together?",             # three bars
    "Somehow many more cats than dogs came.",                      # not the phrase
])
def test_a_text_that_does_not_compare_two_bars_larger_first_is_retried(reply, text):
    reply({**VALID, "question_text": text})
    with pytest.raises(ValueError, match="after retries"):
        graphs.generate_graphs_question([], [], "medium", "1st Grade")


@pytest.mark.parametrize("text", [
    "Look at the dogs and the cats. How many more Cats than dogs are there?",
    # The last "how many more" is the comparison asked.
    "How many more fish? No -- how many more cats than dogs are there?",
    "How many more cat than dog are there?",                       # singular of the bar names
])
def test_the_comparison_is_read_from_the_text_where_it_asks(reply, text):
    reply({**VALID, "question_text": text})
    assert graphs.generate_graphs_question([], [], "medium", "1st Grade")["correct_answer"] == "2"


def test_the_models_target_does_not_decide_the_comparison(reply):
    """The text is what the student answers; a reversed or wrong `target` is ignored."""
    reply({**VALID, "target": ["fish", "cats"]})
    assert graphs.generate_graphs_question([], [], "medium", "1st Grade")["correct_answer"] == "2"


def test_a_comparison_naming_three_bars_has_no_answer():
    """Checked on the reader itself: `solve_graph` would also refuse a three-name target."""
    names = ["cats", "dogs", "fish"]
    assert graphs.comparison_in_text("How many more cats than dogs and fish together?", names) is None
    assert graphs.comparison_in_text("How many more cats than dogs?", names) == ["cats", "dogs"]


@pytest.mark.parametrize("name,said", [("apples", "apple"), ("boxes", "box"), ("fish", "fish"),
                                       ("puppy", "puppies"), ("cherries", "cherry")])
def test_a_bar_is_found_by_its_singular_or_plural(name, said):
    assert graphs.comparison_in_text(f"How many more {said} than cats?", [name, "cats"]) == [name, "cats"]


def test_the_names_match_the_blocks_they_send():
    """A wrong map entry sends one block and validates against another."""
    import re
    source = open(graphs.__file__, encoding="utf-8").read()
    from_blocks = {int(n): name for n, name in re.findall(
        r"^    (\d+): '''Scenario \d+: ([a-z_]+)", source, re.M)}
    assert from_blocks, "the block regex matched nothing -- this test is inert"
    assert from_blocks == graphs._SCENARIO_NAMES
