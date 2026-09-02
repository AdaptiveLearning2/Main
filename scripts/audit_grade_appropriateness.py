"""Generate a sample of questions per grade and dump them for classification.

The audit that motivated `quadratics` and `functions` -- "81% of grade-9
questions three or more grades below grade", over 640 questions -- was done by
hand and left no script, which is why it could not be re-run to say how far
the two new topics moved it. This is that script.

It deliberately does **not** score anything. Deciding that a question is
"three grades below grade" means naming the CCSS standard it actually
exercises, and that is a judgement about mathematics: a `geometry` question
can be 2.G.2 or 8.G.9 depending on the scenario drawn, and an `algebra` one
can be 6.EE.7 or 8.EE.7b depending on the tier. A regex over the text would
produce a number with no defensible meaning, which is worse than no number.
So it prints the questions, grouped, for a person to classify.

Topics are drawn uniformly from `_allowed_topics(grade)`, which is what
`randomize_selection` does and is the documented fallback whenever the model's
own pick fails to parse. The live path weights by performance history instead,
so this measures the *offering* rather than one student's experience -- which
is the right unit for "what can this system ask a 12th grader".

Costs one model call per question against whatever `LLM_PROVIDER` says, so it
bills like any other caller. Run it against `claude` for the number that
describes production, and `ollama` for a free shape check.

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
            # A fresh id per question, so the repeat-avoidance history stays
            # empty and every draw is independent. Sharing one would make
            # later questions in a grade avoid earlier ones, which is right
            # for a student and wrong for a sample.
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
