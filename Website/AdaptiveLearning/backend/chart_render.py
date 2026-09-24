"""Session charts as standalone SVG, for the end-of-year archive.

Stdlib only. These re-render the payload rather than capture the live
component, so colours are kept in step with the frontend by hand;
`test_chart_render.py` fails when they drift. Every style is inline, since a
stored object has no stylesheet.
"""

from __future__ import annotations

import html
import math

# ── palette ────────────────────────────────────────────────────────────────
# Mirrors `frontend/src/lib/emotions.js` and `SessionReview.jsx`, pinned. Fixed per
# label, not by position, so a label keeps its colour whatever else is present.

EMOTION_COLOURS = {
    "neutral": "#94a3b8", "happy": "#10b981", "surprise": "#38bdf8",
    "sad": "#6366f1", "angry": "#f43f5e", "disgust": "#84cc16",
    "fear": "#a855f7", "contempt": "#f59e0b",
}

# `calibrating` and `unknown` are muted, not dropped: real states of the session.
STRESS_COLOURS = {
    "low": "#10b981", "moderate": "#f59e0b", "high": "#f43f5e",
    "calibrating": "#cbd5e1", "unknown": "#94a3b8",
}

# Line series, keyed by the payload field they draw.
SERIES_COLOURS = {
    "focus": "#6366f1",
    "stress": "#f43f5e",
    "heart_rate_bpm": "#a855f7",
    "rmssd_ms": "#f59e0b",
}

# Grey for a label with no fixed colour, so it never borrows a known one's identity.
UNKNOWN_COLOUR = "#cbd5e1"

# Must agree with the keys of `sessions.chart_paths`.
CHART_NAMES = ("cognitive_timeline", "heart_rate", "stress_pie", "emotion_pie")

_WIDTH, _HEIGHT = 480, 300
_PAD = 44


def _finite(value):
    """A float, or None for anything that cannot be plotted (including nan/inf)."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _svg(body: str, title: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_WIDTH} {_HEIGHT}" '
        f'width="{_WIDTH}" height="{_HEIGHT}" role="img" '
        f'aria-label="{html.escape(title)}">'
        f'<rect width="{_WIDTH}" height="{_HEIGHT}" fill="#ffffff"/>'
        f'<text x="{_PAD}" y="26" font-family="sans-serif" font-size="14" '
        f'font-weight="700" fill="#0f172a">{html.escape(title)}</text>'
        f"{body}</svg>"
    )


def _empty(title: str, reason: str) -> str:
    """A chart with nothing to draw, captioned with the caller's `reason`."""
    return _svg(
        f'<text x="{_WIDTH / 2}" y="{_HEIGHT / 2}" text-anchor="middle" '
        f'font-family="sans-serif" font-size="13" fill="#64748b">'
        f"{html.escape(reason)}</text>",
        title,
    )


def pie_svg(counts: dict, title: str, colours: dict) -> str:
    """A donut of labelled counts.

    A single slice is drawn as a circle: a 360-degree arc degenerates and renders blank.
    """
    data = [(str(k), int(v)) for k, v in (counts or {}).items()
            if _finite(v) and int(v) > 0]
    total = sum(v for _, v in data)
    if total <= 0:
        return _empty(title, "No scored data available.")

    # Deterministic order, so two renders of one session are byte-identical.
    data.sort(key=lambda kv: (-kv[1], kv[0]))

    cx, cy, r = 150, 168, 92
    angle = -math.pi / 2
    out = []
    for label, value in data:
        fraction = value / total
        colour = colours.get(label.lower(), UNKNOWN_COLOUR)
        tip = f"{label}: {value} ({fraction * 100:.1f}%)"
        if fraction >= 0.999999:
            out.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{html.escape(colour)}">'
                       f"<title>{html.escape(tip)}</title></circle>")
            angle += math.tau
            continue
        end = angle + fraction * math.tau
        x1, y1 = cx + r * math.cos(angle), cy + r * math.sin(angle)
        x2, y2 = cx + r * math.cos(end), cy + r * math.sin(end)
        large = 1 if fraction > 0.5 else 0
        out.append(
            f'<path d="M {cx} {cy} L {x1:.2f} {y1:.2f} '
            f'A {r} {r} 0 {large} 1 {x2:.2f} {y2:.2f} Z" fill="{html.escape(colour)}">'
            f"<title>{html.escape(tip)}</title></path>"
        )
        angle = end

    out.append(f'<circle cx="{cx}" cy="{cy}" r="42" fill="#ffffff"/>')
    out.append(f'<text x="{cx}" y="{cy + 2}" text-anchor="middle" '
               f'font-family="sans-serif" font-size="18" font-weight="700" '
               f'fill="#0f172a">{total}</text>')
    out.append(f'<text x="{cx}" y="{cy + 20}" text-anchor="middle" '
               f'font-family="sans-serif" font-size="10" fill="#64748b">samples</text>')

    for i, (label, value) in enumerate(data):
        y = 60 + i * 20
        colour = colours.get(label.lower(), UNKNOWN_COLOUR)
        out.append(f'<rect x="286" y="{y - 9}" width="11" height="11" rx="2" fill="{html.escape(colour)}"/>')
        out.append(f'<text x="304" y="{y}" font-family="sans-serif" font-size="11" '
                   f'fill="#334155">{html.escape(label)} -- {value}</text>')

    return _svg("".join(out), title)


def line_svg(points: dict, title: str, unit: str = "") -> str:
    """One or more series over time.

    `points` maps a series name to [(x, y)]: x numeric (epoch ms), y a value or None.
    A None breaks the line rather than bridging a gap nothing was recorded in.
    """
    series = {}
    for name, raw in (points or {}).items():
        cleaned = [(_finite(x), _finite(y)) for x, y in (raw or [])]
        cleaned = [(x, y) for x, y in cleaned if x is not None]
        if any(y is not None for _, y in cleaned):
            series[name] = sorted(cleaned, key=lambda p: p[0])
    if not series:
        return _empty(title, "No readings in this session.")

    xs = [x for pts in series.values() for x, _ in pts]
    ys = [y for pts in series.values() for _, y in pts if y is not None]
    x_lo, x_hi = min(xs), max(xs)
    y_lo, y_hi = min(ys), max(ys)
    # Pad a flat series (zero range) so it sits mid-plot.
    if y_hi - y_lo < 1e-9:
        y_lo, y_hi = y_lo - 1, y_hi + 1
    x_span = (x_hi - x_lo) or 1

    plot_w = _WIDTH - _PAD - 110
    plot_h = _HEIGHT - 90

    def sx(x):
        return _PAD + (x - x_lo) / x_span * plot_w

    def sy(y):
        return 52 + (1 - (y - y_lo) / (y_hi - y_lo)) * plot_h

    out = [
        f'<line x1="{_PAD}" y1="{52 + plot_h}" x2="{_PAD + plot_w}" y2="{52 + plot_h}" '
        f'stroke="#e2e8f0" stroke-width="1"/>',
        f'<line x1="{_PAD}" y1="52" x2="{_PAD}" y2="{52 + plot_h}" '
        f'stroke="#e2e8f0" stroke-width="1"/>',
        f'<text x="{_PAD - 6}" y="56" text-anchor="end" font-family="sans-serif" '
        f'font-size="9" fill="#94a3b8">{y_hi:.0f}{html.escape(unit)}</text>',
        f'<text x="{_PAD - 6}" y="{52 + plot_h}" text-anchor="end" '
        f'font-family="sans-serif" font-size="9" fill="#94a3b8">'
        f'{y_lo:.0f}{html.escape(unit)}</text>',
    ]

    for i, (name, pts) in enumerate(sorted(series.items())):
        colour = SERIES_COLOURS.get(name, UNKNOWN_COLOUR)

        def flush(run, colour=colour):
            """Emit one unbroken run; a lone point becomes a dot, since a one-point polyline draws nothing."""
            if len(run) > 1:
                out.append(f'<polyline points="{" ".join(run)}" fill="none" '
                           f'stroke="{html.escape(colour)}" stroke-width="2"/>')
            elif len(run) == 1:
                x, y = run[0].split(",")
                out.append(f'<circle cx="{x}" cy="{y}" r="2.5" fill="{html.escape(colour)}"/>')

        run = []
        for x, y in pts:
            if y is None:
                flush(run)
                run = []
                continue
            run.append(f"{sx(x):.1f},{sy(y):.1f}")
        flush(run)

        ly = 60 + i * 18
        out.append(f'<rect x="{_PAD + plot_w + 14}" y="{ly - 9}" width="11" height="11" '
                   f'rx="2" fill="{html.escape(colour)}"/>')
        out.append(f'<text x="{_PAD + plot_w + 32}" y="{ly}" font-family="sans-serif" '
                   f'font-size="11" fill="#334155">{html.escape(name)}</text>')

    return _svg("".join(out), title)
