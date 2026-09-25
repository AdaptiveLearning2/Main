"""Give stored shape-fraction questions the one sentence generation now writes.

Run to report; --apply sets `question_text` on rows that differ, which makes their stored
shaded/parts answer correct. A row whose answer is not its figure's shaded/parts, and a row a
student has answered (their mark was for the old text), are reported only.
"""
from __future__ import annotations

import argparse
import os
import sys

import LLM_shape_fractions_generation as shapes
import repair_common


def check(row):
    """"ok", "fix" (the text asks something else) or "mismatch" (the answer is not the figure's)."""
    figure = row.get("figure") if isinstance(row.get("figure"), dict) else {}
    answer = shapes.solve_shape_fraction(figure.get("parts"), figure.get("shaded"))
    if answer is None or str(row.get("correct_answer")) != answer:
        return "mismatch"
    return "ok" if row.get("question_text") == shapes.QUESTION_TEXT else "fix"


def repair(client, dry_run=True):
    """Count of "ok", ids for "fix", "mismatch" and "answered", and how many were written.

    Raises if a read fails.
    """
    report = {"ok": 0, "fix": [], "mismatch": [], "answered": [], "applied": 0}
    for row in repair_common.rows(client, "shape_fractions",
                                  "id, question_text, correct_answer, figure"):
        outcome = check(row)
        if outcome == "ok":
            report["ok"] += 1
            continue
        if outcome == "fix" and repair_common.answered(client, row["id"]):
            outcome = "answered"
        report[outcome].append(row["id"])
        if outcome == "fix" and not dry_run:
            client.table("questions").update({"question_text": shapes.QUESTION_TEXT}) \
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
    print(f"ok {report['ok']}, applied {report['applied']}")
    print(f"text asks something else ({len(report['fix'])}): {report['fix']}")
    print(f"text asks something else, already answered, left alone ({len(report['answered'])}): "
          f"{report['answered']}")
    print(f"answer is not the figure's ({len(report['mismatch'])}): {report['mismatch']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
