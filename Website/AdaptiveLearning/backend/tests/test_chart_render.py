"""The archived SVGs: that they draw, and that they still mean what the app drew."""

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
    """A 360-degree arc starts and ends at one point, so it draws nothing."""
    svg = cr.pie_svg({"low": 12}, "Stress", cr.STRESS_COLOURS)
    _parses(svg)
    assert "<circle" in svg and cr.STRESS_COLOURS["low"] in svg
    assert "<path" not in svg, "a full circle was drawn as an arc, which is invisible"


def test_an_unknown_label_is_visibly_unknown():
    """Grey, not a rotating hue that already means something else."""
    svg = cr.pie_svg({"smug": 4}, "Emotion", cr.EMOTION_COLOURS)
    assert cr.UNKNOWN_COLOUR in svg


def test_an_empty_pie_says_so_rather_than_drawing_nothing():
    svg = cr.pie_svg({}, "Emotion", cr.EMOTION_COLOURS)
    _parses(svg)
    assert "No scored data" in svg


def test_slice_order_does_not_depend_on_dict_order():
    """Two renders of one session must be byte-identical."""
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
    """Joining across a null would draw a measurement nobody took."""
    svg = cr.line_svg({"focus": [(0, 0.4), (1, None), (2, 0.6), (3, 0.7)]}, "C")
    _parses(svg)
    assert svg.count("<polyline") == 1, "the gap was bridged into one line"
    assert "<circle" in svg, "the isolated point before the gap vanished"


def test_a_single_reading_still_renders():
    """A one-point polyline draws nothing."""
    svg = cr.line_svg({"heart_rate_bpm": [(0, 72.0)]}, "Heart", unit=" bpm")
    _parses(svg)
    assert "<circle" in svg


def test_a_flat_series_is_not_pinned_to_the_axis():
    svg = cr.line_svg({"focus": [(0, 0.5), (1, 0.5), (2, 0.5)]}, "C")
    _parses(svg)
    assert "<polyline" in svg


def test_non_finite_values_are_dropped_not_drawn():
    """NaN and inf survive float() and some renderers blank the whole chart on them."""
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
    """The archive is a re-render, so nothing but a test enforces this."""
    assert _jsx_map(name) == getattr(cr, mapping), (
        f"{name} differs between SessionReview.jsx and chart_render.py. These "
        "are two hand-kept copies of one palette; update both or the archive "
        "stops meaning what the parent saw."
    )


def _jsx_line_strokes(path: Path) -> dict:
    """series `key` -> `colour` from a chart file's per-series list (line, chip and archive).

    Refuses an empty result, so a moved list names its file.
    """
    source = path.read_text(encoding="utf-8")
    found = dict(re.findall(
        r"key:\s*'([^']+)'[^{}]*?colour:\s*'(#[0-9a-fA-F]{3,8})'", source, re.S))
    assert found, (
        f"no series colours found in {path.name} -- the frontend moved them "
        "again, and this check reads as passing when it finds nothing"
    )
    return found


def test_the_line_palette_matches_the_session_charts():
    """`SessionReview.jsx` is the reference: an archived SVG re-renders the per-session charts."""
    assert _jsx_line_strokes(_JSX) == cr.SERIES_COLOURS, (
        "line colours differ between SessionReview.jsx and chart_render.py. "
        "The archive is a re-render, so nothing else keeps these in step."
    )


def test_the_two_chart_surfaces_agree_on_shared_series():
    """One colour, one meaning, across both places a line is drawn."""
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

    # Each file can be self-consistent while the pair reuses a colour.
    combined = {**session, **panel}
    by_colour: dict = {}
    for series, colour in combined.items():
        by_colour.setdefault(colour, []).append(series)
    clashes = {c: sorted(v) for c, v in by_colour.items() if len(v) > 1}
    assert not clashes, f"one colour used for several series: {clashes}"


def test_every_named_chart_is_one_this_module_can_draw():
    """A name with no renderer would be a path promising a file nobody writes."""
    assert set(cr.CHART_NAMES) == {
        "cognitive_timeline", "heart_rate", "stress_pie", "emotion_pie"}


# --- Escaping -------------------------------------------------------------
# The SVGs reach a browser, so the renderer is an HTML sink; asserted on output, not source.
# `tip` (escaped at its sink) and `{total}`/`{value}` (numeric) are unescaped on purpose.

_HOSTILE = '</text><script>alert(1)</script><text x="0">'


def _parsed(svg):
    """The SVG as a tree, which is also the assertion that it is well-formed."""
    return ElementTree.fromstring(svg)


def _all_text(svg):
    return "".join(_parsed(svg).itertext())


def _tags(svg):
    return {el.tag.split("}")[-1] for el in _parsed(svg).iter()}


@pytest.mark.parametrize("render", [
    pytest.param(lambda s: cr.pie_svg({s: 3, "sad": 1}, "Emotion", cr.EMOTION_COLOURS),
                 id="pie-label-the-database-supplies"),
    pytest.param(lambda s: cr.pie_svg({"happy": 3}, s, cr.EMOTION_COLOURS),
                 id="pie-title"),
    pytest.param(lambda s: cr.line_svg({s: [(0, 1), (1, 2)]}, "Signals"),
                 id="line-series-name"),
    pytest.param(lambda s: cr.line_svg({"focus": [(0, 1), (1, 2)]}, s),
                 id="line-title"),
    pytest.param(lambda s: cr.line_svg({"focus": [(0, 1), (1, 2)]}, "Signals", unit=s),
                 id="line-unit"),
    pytest.param(lambda s: cr._empty(s, "No readings."), id="empty-title"),
    pytest.param(lambda s: cr._empty("Emotion", s), id="empty-reason"),
])
def test_a_hostile_string_renders_as_text_not_markup(render):
    svg = render(_HOSTILE)
    # Well-formed: the payload did not close a tag or open one.
    assert "script" not in _tags(svg)
    # And still shown: escaped, not dropped.
    assert _HOSTILE in _all_text(svg)


def test_a_hostile_colour_cannot_escape_its_attribute():
    """Palettes are a parameter; a quote in one must not end the `fill=` attribute."""
    poisoned = dict(cr.EMOTION_COLOURS)
    poisoned["happy"] = '#fff" onload="alert(1)'
    svg = cr.pie_svg({"happy": 3, "sad": 1}, "Emotion", poisoned)
    assert not any("onload" in el.attrib for el in _parsed(svg).iter())


def test_the_title_is_the_accessible_name_and_is_escaped_there_too():
    """`aria-label` is a second, attribute interpolation of the title."""
    svg = cr.pie_svg({"happy": 1}, 'Emotion" role="img', cr.EMOTION_COLOURS)
    root = _parsed(svg)
    assert root.attrib.get("aria-label") == 'Emotion" role="img'
