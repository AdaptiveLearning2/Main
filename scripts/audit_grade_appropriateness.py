"""Generate a sample of questions per grade and dump them for classification.

Deliberately scores nothing: naming a question's CCSS grade is a person's judgement.
Topics are drawn uniformly from `_allowed_topics(grade)`, so this measures the offering.
One model call per question via `LLM_PROVIDER`. See docs/question-generation.md.

    python scripts/audit_grade_appropriateness.py --grades 9 10 11 12 --per-grade 30
"""

import argparse
import collections
import json
import os
import random
import sys

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "Website", "AdaptiveLearning", "backend")
sys.path.insert(0, BACKEND)

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "audit")

import lesson_plan_context          # noqa: E402
import LLM_topic_decider as decider  # noqa: E402

DIFFICULTIES = ("easy", "medium", "hard")


def _grade_label(number):
    """The string form the grade gates actually parse."""
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(
        number if number < 20 else number % 10, "th")
    return f"{number}{suffix} Grade"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--grades", nargs="+", type=int, default=[9, 10, 11, 12])
    parser.add_argument("--per-grade", type=int, default=30)
    parser.add_argument("--seed", type=int, default=20260902)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    random.seed(args.seed)
    provider = os.environ.get("LLM_PROVIDER", "ollama")
    print(f"provider={provider}  seed={args.seed}\n")

    rows = []
    for number in args.grades:
        grade = _grade_label(number)
        allowed = decider._allowed_topics(grade)
        print(f"=== {grade}: {len(allowed)} topics offered -- "
              f"{', '.join(sorted(allowed))}\n")
        for index in range(args.per_grade):
            topic = random.choice(allowed)
            difficulty = random.choice(DIFFICULTIES)
            # Fresh id per question, so repeat-avoidance history keeps draws independent.
            user = f"audit-{number}-{index}"
            try:
                served = decider.question_generation(topic, difficulty, user, grade)
                text = served["question_text"]
                answer = served["correct_answer"]
            except Exception as exc:                       # noqa: BLE001
                text, answer = f"FAILED: {type(exc).__name__}: {exc}", None
            rows.append({"grade": number, "topic": topic,
                         "difficulty": difficulty, "question": text,
                         "answer": answer})
            print(f"[{grade} | {topic} | {difficulty}] {text}")
            if answer is not None:
                print(f"    answer: {answer}")
        print()

    counts = collections.Counter((r["grade"], r["topic"]) for r in rows)
    print("=== topic mix ===")
    for (number, topic), n in sorted(counts.items()):
        print(f"  grade {number:2}  {topic:22} {n}")
    failures = [r for r in rows if r["answer"] is None]
    print(f"\n{len(rows)} questions, {len(failures)} failed to generate")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(rows, handle, indent=2)
        print(f"written to {args.out}")


if __name__ == "__main__":
    main()
