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
    """series `key` -> the colour its line is stroked with, for a chart file.

    These are not a `const NAME = {...}` object, so the map above can't reach
    them. They used to be inline `dataKey`/`stroke` attributes on the Recharts
    elements -- that's why the drift test above missed this palette -- and
    since the series filter landed they are `colour` on the per-chart series
    list each `<Line>` is derived from, which is the same palette one step
    earlier. Reading it there is what keeps the swatch on a teacher's toggle
    inside the check too: chip, line and archive are now one value.

    **It refuses an empty result rather than returning one**, which is a
    better message and not a second catch: both callers below already fail on
    an empty map -- one compares it against a populated `SERIES_COLOURS`, the
    other asserts the two files have a series in common, and that is what went
    red when the colours moved. What the guard adds is naming the file that
    moved, rather than presenting a whole palette as having drifted at once.
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


# --- Escaping -------------------------------------------------------------
#
# These SVGs are uploaded to storage and later handed to a browser through a
# signed URL, so the renderer is an HTML sink like any other. Every text
# interpolation runs through `html.escape` today, and the question these
# answer is whether it stays that way.
#
# One of the inputs is genuinely database-sourced: `_counts(face, "emotion")`
# in `chart_archive.py` takes its keys from `face_signals.emotion`, so a pie
# label is a stored value rather than a constant. Titles and units are
# hardcoded at the call sites today -- tested anyway, because "no caller
# passes anything interesting" is a property of the callers, and this module
# is the thing that has to hold when one does.
#
# Asserted on the rendered output rather than on the source: a scan for
# `html.escape` cannot tell a call from a mention, and cannot see a *new*
# interpolation that needed one. `ElementTree` parsing is the whole check --
# it fails on a structural break, and `.itertext()` proves the payload landed
# as text instead of markup.
#
# Two interpolations are deliberately not escaped and are not gaps. `tip` is
# built unescaped and escaped at its sink, so escaping it twice would print
# the entities. `{total}` and `{value}` are counts the pie divides to get each
# fraction, so a non-numeric one raises before anything is rendered.

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
    # And it is still *shown*, escaped rather than dropped -- a renderer that
    # silently discarded it would pass the check above while losing a real
    # emotion label like "n/a".
    assert _HOSTILE in _all_text(svg)


def test_a_hostile_colour_cannot_escape_its_attribute():
    """`colour` is interpolated into `fill=`/`stroke=` without escaping.

    It comes from this module's own palettes today, so nothing reaches it --
    but the palettes are a parameter, and a quote in one would end the
    attribute and start another of the string's choosing.
    """
    poisoned = dict(cr.EMOTION_COLOURS)
    poisoned["happy"] = '#fff" onload="alert(1)'
    svg = cr.pie_svg({"happy": 3, "sad": 1}, "Emotion", poisoned)
    assert not any("onload" in el.attrib for el in _parsed(svg).iter())


def test_the_title_is_the_accessible_name_and_is_escaped_there_too():
    """`aria-label` is a second interpolation of the same value, and an
    attribute rather than text -- a quote in the title would end it."""
    svg = cr.pie_svg({"happy": 1}, 'Emotion" role="img', cr.EMOTION_COLOURS)
    root = _parsed(svg)
    assert root.attrib.get("aria-label") == 'Emotion" role="img'
