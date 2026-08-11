"""The archived SVGs: that they draw, and that they still mean what the app drew.

These are re-renders from the same payload, not captures of the live component,
so the failure mode is silent divergence: the archive keeps rendering, and the
colour that meant "happy" last year means something else now. The drift test at
the bottom is the one that matters most here -- the rest check that a chart
appears at all.
"""

import os
import re
from pathlib import Path
from xml.etree import ElementTree

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import chart_render as cr  # noqa: E402

# tests/ -> backend/ -> AdaptiveLearning/ -> frontend/
_JSX = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages"
        / "teacher" / "SessionReview.jsx")


def _parses(svg: str):
    """Every assertion depends on the output being real SVG, so parse it."""
    return ElementTree.fromstring(svg)


# ── pies ────────────────────────────────────────────────────────────────────

def test_a_pie_renders_a_slice_per_label():
    svg = cr.pie_svg({"happy": 3, "sad": 1}, "Emotion", cr.EMOTION_COLOURS)
    _parses(svg)
    assert svg.count("<path") == 2
    assert cr.EMOTION_COLOURS["happy"] in svg and cr.EMOTION_COLOURS["sad"] in svg


def test_a_single_slice_pie_is_not_blank():
    """The reference renderer's one real bug, carried over fixed.

    An arc of exactly 360 degrees starts and ends at the same point, so the path
    degenerates and draws nothing -- a session where every window scored the
    same renders as an empty chart, which reads as "no data" for the most
    unambiguous data there is.
    """
    svg = cr.pie_svg({"low": 12}, "Stress", cr.STRESS_COLOURS)
    _parses(svg)
    assert "<circle" in svg and cr.STRESS_COLOURS["low"] in svg
    assert "<path" not in svg, "a full circle was drawn as an arc, which is invisible"


def test_an_unknown_label_is_visibly_unknown():
    """Grey, not a rotating hue. A label the model gains later must not borrow
    the colour that already means something else."""
    svg = cr.pie_svg({"smug": 4}, "Emotion", cr.EMOTION_COLOURS)
    assert cr.UNKNOWN_COLOUR in svg


def test_an_empty_pie_says_so_rather_than_drawing_nothing():
    svg = cr.pie_svg({}, "Emotion", cr.EMOTION_COLOURS)
    _parses(svg)
    assert "No scored data" in svg


def test_slice_order_does_not_depend_on_dict_order():
    """Two renders of one session must be byte-identical, or an archive diff is
    noise."""
    a = cr.pie_svg({"happy": 3, "sad": 1, "fear": 2}, "E", cr.EMOTION_COLOURS)
    b = cr.pie_svg({"fear": 2, "happy": 3, "sad": 1}, "E", cr.EMOTION_COLOURS)
    assert a == b


# ── lines ───────────────────────────────────────────────────────────────────

def test_a_line_chart_draws_one_series_per_key():
    svg = cr.line_svg({"focus": [(0, 0.4), (1, 0.6)],
                       "stress": [(0, 0.2), (1, 0.3)]}, "Cognitive")
    _parses(svg)
    assert svg.count("<polyline") == 2
    assert cr.SERIES_COLOURS["focus"] in svg and cr.SERIES_COLOURS["stress"] in svg


def test_a_gap_breaks_the_line_rather_than_bridging_it():
    """Joining across a null draws a measurement nobody took -- the chart-shaped
    version of the rule that absence must not render as a value."""
    svg = cr.line_svg({"focus": [(0, 0.4), (1, None), (2, 0.6), (3, 0.7)]}, "C")
    _parses(svg)
    assert svg.count("<polyline") == 1, "the gap was bridged into one line"
    assert "<circle" in svg, "the isolated point before the gap vanished"


def test_a_single_reading_still_renders():
    """A polyline of one point draws nothing, which would make a session that
    recorded once look like one that recorded never."""
    svg = cr.line_svg({"heart_rate_bpm": [(0, 72.0)]}, "Heart", unit=" bpm")
    _parses(svg)
    assert "<circle" in svg


def test_a_flat_series_is_not_pinned_to_the_axis():
    svg = cr.line_svg({"focus": [(0, 0.5), (1, 0.5), (2, 0.5)]}, "C")
    _parses(svg)
    assert "<polyline" in svg


def test_non_finite_values_are_dropped_not_drawn():
    """NaN and inf survive float() and then produce path commands renderers
    disagree about -- some clip, some blank the whole chart."""
    svg = cr.line_svg({"focus": [(0, 0.4), (1, float("nan")),
                                 (2, float("inf")), (3, 0.6)]}, "C")
    _parses(svg)
    assert "nan" not in svg.lower() and "inf" not in svg.lower()


def test_an_empty_line_chart_says_so():
    svg = cr.line_svg({"focus": []}, "Cognitive")
    _parses(svg)
    assert "No readings" in svg


# ── the drift check ─────────────────────────────────────────────────────────

def _jsx_map(name: str) -> dict:
    source = _JSX.read_text(encoding="utf-8")
    block = re.search(rf"const {name} = \{{(.*?)\}}", source, re.S)
    assert block, f"{name} not found in {_JSX.name} -- the frontend moved"
    return dict(re.findall(r"(\w+):\s*'(#[0-9a-fA-F]{3,8})'", block.group(1)))


@pytest.mark.parametrize("name,mapping", [
    ("EMOTION_COLOURS", "EMOTION_COLOURS"),
    ("STRESS_COLOURS", "STRESS_COLOURS"),
])
def test_the_archive_palette_matches_the_live_charts(name, mapping):
    """The archive is a re-render, so nothing enforces this but a test.

    A drift here is silent and permanent: the SVG keeps rendering, and the
    colour that meant `happy` in one year's archive means something else in the
    next. Someone comparing two terms would be reading a difference that is
    entirely ours.
    """
    assert _jsx_map(name) == getattr(cr, mapping), (
        f"{name} differs between SessionReview.jsx and chart_render.py. These "
        "are two hand-kept copies of one palette; update both or the archive "
        "stops meaning what the parent saw."
    )


def test_every_named_chart_is_one_this_module_can_draw():
    """`CHART_NAMES` is what `chart_paths` on the session row will hold, so a
    name here with no renderer is a path promising a file nobody writes."""
    assert set(cr.CHART_NAMES) == {
        "cognitive_timeline", "heart_rate", "stress_pie", "emotion_pie"}
