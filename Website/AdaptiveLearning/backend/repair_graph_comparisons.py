"""Re-score stored "how many more" graph questions from their text, as generation now does.

Run to report; --apply rewrites `correct_answer` and `options` on rows whose stored answer
differs. Rows whose text compares no two bars larger-first, and rows a student has answered
(each answer stores an option's position), are reported, never changed.
"""
from __future__ import annotations

import argparse
import os
import random
import sys

import answer_format
import LLM_graphs_generation as graphs

_PAGE = 1000   # PostgREST's db-max-rows; keyset-paged on id so no row is skipped
_ANSWER_TABLES = ("session_answers", "practice_session_answers")


def _rows(client):
    last = None
    while True:
        q = (client.table("questions").select("id, question_text, correct_answer, options, figure")
             .eq("subject", "graphs").order("id").limit(_PAGE))
        page = (q.gt("id", last) if last is not None else q).execute().data or []
        yield from page
        if len(page) < _PAGE:
            return
        last = page[-1]["id"]


def _answered(client, question_id):
    """True if a lesson or practice answer points at this question; raises if a read fails."""
    return any(client.table(table).select("id").eq("question_id", question_id)
               .limit(1).execute().data for table in _ANSWER_TABLES)


def rescore(row):
    """("ok" | "fix" | "unanswerable" | "skip", correct, options) for one stored row."""
    text, figure = row.get("question_text"), row.get("figure") or {}
    bars = figure.get("bars") if isinstance(figure, dict) else None
    if not isinstance(text, str) or not bars or not graphs._HOW_MANY_MORE.search(text):
        return "skip", None, None
    categories = [{"name": b.get("label"), "count": b.get("value")} for b in bars]
    target = graphs.comparison_in_text(text, [b.get("label") for b in bars])
    solution = graphs.solve_graph("how_many_more", categories, target) if target else None
    if solution is None:
        return "unanswerable", None, None
    correct = answer_format.format_value(solution)
    if str(row.get("correct_answer")) == correct:
        return "ok", correct, None
    options = [answer_format.format_value(a) for a in
               graphs.generate_incorrect_answers(solution, [b.get("value") for b in bars])]
    options.append(correct)
    random.shuffle(options)
    return "fix", correct, options


def repair(client, dry_run=True):
    """Counts per outcome, and the ids behind every one but "ok"; raises if the read fails."""
    report = {"ok": 0, "fix": [], "unanswerable": [], "answered": [], "skip": 0, "applied": 0}
    for row in _rows(client):
        outcome, correct, options = rescore(row)
        if outcome in ("ok", "skip"):
            report[outcome] += 1
            continue
        if outcome == "fix" and _answered(client, row["id"]):
            outcome = "answered"
        report[outcome].append(row["id"])
        if outcome == "fix" and not dry_run:
            client.table("questions").update({"correct_answer": correct, "options": options}) \
                .eq("id", row["id"]).execute()
            report["applied"] += 1
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="write the fixes; without it nothing changes")
    args = ap.parse_args(argv)
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set", file=sys.stderr)
        return 2
    from supabase import create_client
    report = repair(create_client(url, key), dry_run=not args.apply)
    print(f"ok {report['ok']}, skipped {report['skip']}, applied {report['applied']}")
    print(f"wrong answer stored ({len(report['fix'])}): {report['fix']}")
    print(f"wrong answer stored, already answered, left alone ({len(report['answered'])}): "
          f"{report['answered']}")
    print(f"text compares no two bars larger-first ({len(report['unanswerable'])}): "
          f"{report['unanswerable']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
