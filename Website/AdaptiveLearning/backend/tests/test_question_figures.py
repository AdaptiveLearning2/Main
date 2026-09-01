"""Figures are derived from the solver's own data, never from the model."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import geometry_solvers  # noqa: E402
import question_figures as figures  # noqa: E402


def test_the_grid_is_built_from_the_numbers_the_solver_multiplies():
    """The whole design. `question_consistency` exists because a model free to
    write the text and the scored data separately eventually disagrees with
    itself; a picture is the same hazard with no text for a check to read.

    Reading `variables` -- the dict `geometry_solvers` indexes -- makes that
    disagreement unrepresentable rather than unlikely.
    """
    spec = figures.figure_for("rectangle_area_by_counting",
                              {"rows": "3", "columns": "4"})
    assert spec == {"type": "rect_grid", "rows": 3, "columns": 4}


def test_it_reads_exactly_the_keys_that_scenario_declares():
    """Derived, not restated: a figure keyed on a name the solver does not use
    would draw one thing and score another, and nothing would notice."""
    assert set(geometry_solvers.SCENARIO_VARS["rectangle_area_by_counting"]) == {
        "rows", "columns"}


@pytest.mark.parametrize("variables,why", [
    ({"rows": "3"}, "a missing key"),
    ({"rows": "0", "columns": "4"}, "a zero side is not a rectangle"),
    ({"rows": "-2", "columns": "4"}, "negative"),
    ({"rows": "40", "columns": "40"}, "1600 rects in a question card"),
    ({"rows": "two", "columns": "4"}, "not a number"),
    ({"rows": "2.5", "columns": "4"}, "not whole squares"),
    ("not a dict", "not a variables mapping"),
])
def test_a_figure_it_cannot_draw_is_no_figure_rather_than_an_error(variables, why):
    """Fail open, like `lesson_plan_context`. The question was complete without
    a picture -- the text still reads "3 rows of 4 same-size squares" -- so a
    figure that cannot be built must cost the picture and nothing else."""
    assert figures.figure_for("rectangle_area_by_counting", variables) is None, why


def test_a_scenario_with_no_figure_gets_none():
    """Most questions here are text, and that is the ordinary case rather than
    a gap."""
    assert figures.figure_for("circle_area", {"radius": "3"}) is None
    assert figures.figure_for("no_such_scenario", {}) is None


def test_a_builder_that_raises_costs_the_picture_and_not_the_question(monkeypatch):
    """It runs on the hot generation path, after the solve. An escaping
    exception there would turn a solved, checked, grade-appropriate question
    into a 500 over a decoration."""
    def _boom(_variables):
        raise RuntimeError("boom")

    monkeypatch.setitem(figures.BUILDERS, "rectangle_area_by_counting", _boom)
    assert figures.figure_for("rectangle_area_by_counting",
                              {"rows": "3", "columns": "4"}) is None


def test_the_generator_attaches_it_without_asking_the_model(monkeypatch):
    """End to end through the retry loop: the served question carries a figure
    built from its own variables, and the model was never asked for one."""
    import json

    import llm_client
    import LLM_geometry_generation as geo

    payload = {"question_text": "A rectangle is split into 3 rows of 4 "
                                "same-size squares. How many squares is that?",
               "question_topic": "geometry",
               "scenario": "rectangle_area_by_counting",
               "variables": {"rows": "3", "columns": "4"}}
    monkeypatch.setattr(llm_client, "generate_text",
                        lambda *a, **k: json.dumps(payload))
    monkeypatch.setattr(geo.lesson_plan_context, "append_lesson_context",
                        lambda p, t, b: p)

    question = geo.generate_geometry_question([], [], "easy", "2nd Grade")
    assert question["figure"] == {"type": "rect_grid", "rows": 3, "columns": 4}
    assert question["correct_answer"] == "12"
    assert "figure" not in payload, "the model was asked for a picture"


def test_a_question_with_no_figure_carries_the_key_as_none(monkeypatch):
    """Present-and-null, not absent. The three states downstream are a spec,
    no figure, and a payload predating figures entirely -- and a key that
    disappears collapses the first two."""
    import json

    import llm_client
    import LLM_geometry_generation as geo

    payload = {"question_text": "A circle has a radius of 3 units. What is "
                                "its area?",
               "question_topic": "geometry", "scenario": "circle_area",
               "variables": {"radius": "3"}}
    monkeypatch.setattr(llm_client, "generate_text",
                        lambda *a, **k: json.dumps(payload))
    monkeypatch.setattr(geo.lesson_plan_context, "append_lesson_context",
                        lambda p, t, b: p)

    question = geo.generate_geometry_question([], [], "hard", "8th Grade")
    assert "figure" in question and question["figure"] is None
