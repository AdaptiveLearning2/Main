"""The archived SVGs: that they draw, and that they still mean what the app drew.

These are re-renders from the same payload, not captures of the live component,
so the failure mode is silent divergence: the archive keeps rendering, but a
colour that meant "happy" last year could mean something else now. The drift
test at the bottom matters most; the rest just check that a chart appears.
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
    """Confirms the output is valid SVG before checking anything else about it."""
    return ElementTree.fromstring(svg)


# ── pies ────────────────────────────────────────────────────────────────────

def test_a_pie_renders_a_slice_per_label():
    svg = cr.pie_svg({"happy": 3, "sad": 1}, "Emotion", cr.EMOTION_COLOURS)
    _parses(svg)
    assert svg.count("<path") == 2
    assert cr.EMOTION_COLOURS["happy"] in svg and cr.EMOTION_COLOURS["sad"] in svg


def test_a_single_slice_pie_is_not_blank():
    """An arc of exactly 360 degrees starts and ends at the same point, so the
    path degenerates and draws nothing. A session where every window scored
    the same would render as an empty chart -- "no data" for the most
    unambiguous data there is."""
    svg = cr.pie_svg({"low": 12}, "Stress", cr.STRESS_COLOURS)
    _parses(svg)
    assert "<circle" in svg and cr.STRESS_COLOURS["low"] in svg
    assert "<path" not in svg, "a full circle was drawn as an arc, which is invisible"


def test_an_unknown_label_is_visibly_unknown():
    """Unknown labels get grey, not a rotating hue -- a new label must not
    borrow a colour that already means something else."""
    svg = cr.pie_svg({"smug": 4}, "Emotion", cr.EMOTION_COLOURS)
    assert cr.UNKNOWN_COLOUR in svg


def test_an_empty_pie_says_so_rather_than_drawing_nothing():
    svg = cr.pie_svg({}, "Emotion", cr.EMOTION_COLOURS)
    _parses(svg)
    assert "No scored data" in svg


def test_slice_order_does_not_depend_on_dict_order():
    """Two renders of one session must be byte-identical, or an archive diff
    is just noise from dict ordering."""
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
    """Joining across a null would draw a measurement nobody took -- absence
    must not render as a value."""
    svg = cr.line_svg({"focus": [(0, 0.4), (1, None), (2, 0.6), (3, 0.7)]}, "C")
    _parses(svg)
    assert svg.count("<polyline") == 1, "the gap was bridged into one line"
    assert "<circle" in svg, "the isolated point before the gap vanished"


def test_a_single_reading_still_renders():
    """A polyline of one point draws nothing, which would make a session that
    recorded once look like one that never recorded at all."""
    svg = cr.line_svg({"heart_rate_bpm": [(0, 72.0)]}, "Heart", unit=" bpm")
    _parses(svg)
    assert "<circle" in svg


def test_a_flat_series_is_not_pinned_to_the_axis():
    svg = cr.line_svg({"focus": [(0, 0.5), (1, 0.5), (2, 0.5)]}, "C")
    _parses(svg)
    assert "<polyline" in svg


def test_non_finite_values_are_dropped_not_drawn():
    """NaN and inf survive float() and produce path commands renderers
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
    """The archive is a re-render, so nothing but a test enforces this.

    A drift here is silent and permanent: the SVG keeps rendering, but a colour
    that meant `happy` in one year's archive could mean something else the
    next. Someone comparing two terms would see a difference that is entirely
    our own bug.
    """
    assert _jsx_map(name) == getattr(cr, mapping), (
        f"{name} differs between SessionReview.jsx and chart_render.py. These "
        "are two hand-kept copies of one palette; update both or the archive "
        "stops meaning what the parent saw."
    )


def _jsx_line_strokes(path: Path) -> dict:
    """`dataKey` -> `stroke` for every `<Line>` in a chart file.

    These are not a `const NAME = {...}` object, so the map above can't reach
    them -- they're inline attributes on Recharts elements, sometimes wrapped
    across lines. That's why the drift test above missed this palette, and why
    it matters for the line chart that depends on it.
    """
    source = path.read_text(encoding="utf-8")
    found = {}
    for element in re.findall(r"<Line [^>]*?/>", source, re.S):
        key = re.search(r'dataKey="([^"]+)"', element)
        stroke = re.search(r'stroke="(#[0-9a-fA-F]{3,8})"', element)
        if key and stroke:
            found[key.group(1)] = stroke.group(1)
    return found


def test_the_line_palette_matches_the_session_charts():
    """The gap the const-parsing test above couldn't see.

    `SessionReview.jsx` is the reference on purpose: the archive is per
    session, so the per-session charts are what an archived SVG is a
    re-render *of*.
    """
    assert _jsx_line_strokes(_JSX) == cr.SERIES_COLOURS, (
        "line colours differ between SessionReview.jsx and chart_render.py. "
        "The archive is a re-render, so nothing else keeps these in step."
    )


def test_the_two_chart_surfaces_agree_on_shared_series():
    """One colour, one meaning, across both places a line is drawn.

    These used to disagree: `SignalPanel.jsx` drew `focus` in #10b981, which is
    `SessionReview.jsx`'s colour for `engagement`. A parent reading the weekly
    panel and then session review would see one green line meaning two
    different things. `SignalPanel` was changed to match, since the archive
    re-renders the *session* charts, making those the reference.

    Asserting agreement rather than just pinning the known values means this
    fails if either surface drifts, not only the one that drifted before.
    """
    panel = _jsx_line_strokes(
        _JSX.parents[2] / "components" / "signals" / "SignalPanel.jsx")
    session = _jsx_line_strokes(_JSX)

    shared = set(panel) & set(session)
    assert shared, "no series in common -- one of the files stopped drawing lines"
    disagreements = {k: (session[k], panel[k]) for k in shared
                     if session[k] != panel[k]}
    assert not disagreements, (
        f"{disagreements} -- same series, different colour on the two surfaces "
        "(SessionReview, SignalPanel). One colour must not mean two things."
    )

    # No colour should be reused for two different series across the pair --
    # that collision is what made the original bug invisible, since both files
    # were internally consistent on their own.
    combined = {**session, **panel}
    by_colour: dict = {}
    for series, colour in combined.items():
        by_colour.setdefault(colour, []).append(series)
    clashes = {c: sorted(v) for c, v in by_colour.items() if len(v) > 1}
    assert not clashes, f"one colour used for several series: {clashes}"


def test_every_named_chart_is_one_this_module_can_draw():
    """A name in `CHART_NAMES` with no renderer would be a path promising a
    file nobody writes."""
    assert set(cr.CHART_NAMES) == {
        "cognitive_timeline", "heart_rate", "stress_pie", "emotion_pie"}
