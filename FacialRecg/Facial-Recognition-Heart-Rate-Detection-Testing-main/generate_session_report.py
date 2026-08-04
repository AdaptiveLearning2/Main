import argparse
import html
import json
import math
from pathlib import Path

COLORS = ["#2563eb", "#16a34a", "#f59e0b", "#dc2626", "#7c3aed", "#0891b2", "#db2777", "#64748b"]


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def num(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def pct(value):
    value = num(value)
    if value is None:
        return "N/A"
    if value <= 1:
        value *= 100
    return f"{value:.1f}%"


def chart_rows(report, summary_key, label_key):
    rows = report.get("summary", {}).get(summary_key, [])
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        count = int(num(row.get("count")) or 0)
        if count > 0:
            result.append((str(row.get(label_key, "unknown")).title(), count))
    return result


def pie_svg(data, title):
    total = sum(v for _, v in data)
    if total <= 0:
        return (
            '<section class="panel"><h2>' + html.escape(title) + '</h2>'
            '<div class="empty">No scored data available.</div></section>'
        )

    cx, cy, r = 115, 105, 76
    angle = -math.pi / 2
    paths = []
    legend = []

    for i, (label, value) in enumerate(data):
        fraction = value / total
        color = COLORS[i % len(COLORS)]

        if fraction >= 0.999999:
            paths.append(
                f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{color}">'
                f'<title>{html.escape(label)}: {value} (100.0%)</title>'
                '</circle>'
            )
            end = angle + math.tau
        else:
            end = angle + fraction * math.tau
            x1, y1 = cx + r * math.cos(angle), cy + r * math.sin(angle)
            x2, y2 = cx + r * math.cos(end), cy + r * math.sin(end)
            large = 1 if fraction > 0.5 else 0
            d = f"M {cx} {cy} L {x1:.2f} {y1:.2f} A {r} {r} 0 {large} 1 {x2:.2f} {y2:.2f} Z"
            paths.append(
                f'<path d="{d}" fill="{color}">'
                f'<title>{html.escape(label)}: {value} ({fraction*100:.1f}%)</title>'
                '</path>'
            )
        legend.append(
            f'<li><span class="swatch" style="background:{color}"></span>'
            f'<span>{html.escape(label)}</span><strong>{value} ({fraction*100:.1f}%)</strong></li>'
        )
        angle = end

    return (
        '<section class="panel"><h2>' + html.escape(title) + '</h2>'
        '<div class="chart-wrap"><svg viewBox="0 0 300 220" role="img" aria-label="' + html.escape(title) + '">'
        + "".join(paths)
        + f'<circle cx="{cx}" cy="{cy}" r="34" fill="white"></circle>'
        + f'<text x="{cx}" y="{cy}" text-anchor="middle" class="total">{total}</text>'
        + f'<text x="{cx}" y="{cy+18}" text-anchor="middle" class="small">samples</text>'
        + '</svg><ul class="legend">' + "".join(legend) + '</ul></div></section>'
    )


def build_html(fusion, facial):
    fs = fusion.get("summary", {})
    rs = facial.get("summary", {})

    total = fs.get("totalSnapshotCount", 0)
    valid = fs.get("validFusedSnapshotCount", 0)
    trusted = num(rs.get("trustedSnapshotPercent")) or 0

    cards = [
        ("Average focus", pct(fs.get("averageFinalFocus")), "Valid fused readings"),
        ("Average stress", pct(fs.get("averageFinalStress")), "Valid fused readings"),
        ("Valid fused samples", str(valid), f"Out of {total} timestamps"),
        ("Trusted rPPG", f"{trusted:.1f}%", "SQI + HR + RMSSD"),
    ]

    cards_html = "".join(
        '<article class="card"><span>' + html.escape(label) + '</span>'
        '<strong>' + html.escape(value) + '</strong>'
        '<small>' + html.escape(note) + '</small></article>'
        for label, value, note in cards
    )

    decision_rows = "".join(
        '<tr><td>' + html.escape(str(k).replace("_", " ").title()) + '</td><td>' + str(v) + '</td></tr>'
        for k, v in fs.get("decisionDistribution", {}).items()
    ) or '<tr><td colspan="2">No decisions available.</td></tr>'

    stress = pie_svg(chart_rows(facial, "stressPieChart", "category"), "Facial stress distribution")
    emotion = pie_svg(chart_rows(facial, "emotionPieChart", "emotion"), "Emotion distribution")

    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Adaptive Learning Session Report</title>
<style>
*{{box-sizing:border-box}} body{{margin:0;background:#f8fafc;color:#0f172a;font-family:Arial,sans-serif}}
main{{max-width:1100px;margin:auto;padding:30px 20px}} h1{{margin:0 0 6px}} .sub{{color:#64748b;margin:0 0 24px}}
.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:20px}}
.card,.panel{{background:#fff;border:1px solid #e2e8f0;border-radius:14px}}
.card{{padding:17px}} .card span{{display:block;color:#64748b;font-size:14px}} .card strong{{display:block;font-size:29px;margin:7px 0}} .card small{{color:#64748b}}
.charts{{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-bottom:20px}} .panel{{padding:18px}} .panel h2{{font-size:18px;margin:0 0 12px}}
.chart-wrap{{display:grid;grid-template-columns:1fr .9fr;align-items:center}} svg{{width:100%}} .total{{font-size:22px;font-weight:bold;fill:#0f172a}} .small{{font-size:12px;fill:#64748b}}
.legend{{list-style:none;padding:0;margin:0;display:grid;gap:9px}} .legend li{{display:grid;grid-template-columns:12px 1fr auto;gap:7px;align-items:center;font-size:14px}}
.swatch{{width:12px;height:12px;border-radius:50%}} .empty{{min-height:210px;display:grid;place-items:center;color:#64748b;background:#f8fafc;border-radius:10px}}
table{{width:100%;border-collapse:collapse}} th,td{{padding:11px;border-bottom:1px solid #e2e8f0;text-align:left}} th{{color:#64748b}}
.note{{color:#64748b;font-size:13px;margin-top:18px}}
@media(max-width:800px){{.cards{{grid-template-columns:1fr 1fr}}.charts{{grid-template-columns:1fr}}}}
@media(max-width:500px){{.cards{{grid-template-columns:1fr}}.chart-wrap{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<main>
<h1>Adaptive Learning Session Report</h1>
<p class="sub">EEG, facial rPPG, and emotion summary</p>
<section class="cards">{cards_html}</section>
<section class="charts">{stress}{emotion}</section>
<section class="panel">
<h2>Adaptive decisions</h2>
<table><thead><tr><th>Decision</th><th>Count</th></tr></thead><tbody>{decision_rows}</tbody></table>
</section>
<p class="note">Provisional research estimates; not clinical measurements.</p>
</main>
</body>
</html>'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fusion", required=True)
    parser.add_argument("--facial", required=True)
    parser.add_argument("--out", default="session_report.html")
    args = parser.parse_args()

    Path(args.out).write_text(
        build_html(load_json(args.fusion), load_json(args.facial)),
        encoding="utf-8",
    )
    print(f"Session report written to: {args.out}")


if __name__ == "__main__":
    main()
