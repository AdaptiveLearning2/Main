"""Measure how often the fail-open generation checks *engage*, not just fire.

CLAUDE.md records every rate for these checks against `llama3.1:8b`, and says
in as many words that they stop describing production once a different model
is generating -- but also that the measurement nobody runs is engagement:

    Measure how often a fail-open check *engages*, never just how often it
    fires. A check that never finds anything to compare reports a perfect
    false-positive rate while doing nothing.

That has already happened once here (`_as_floats` rejecting fractions made the
dataset check inert on half the `ordering` questions while reporting clean).
Both `question_consistency` checks and `grade_appropriateness` are fail-open by
design, so a model that formats its output differently -- a different
separator, the dataset in a different position -- silently disables them rather
than failing. This script is what turns that from an assumption into a number.

It was previously done by hand; CLAUDE.md notes `scripts/` had no home for it.

Usage, from the repo root:

    python scripts/measure_generation_checks.py --per-topic 3

Generation goes through `llm_client`, so it obeys `LLM_PROVIDER` -- run it
against `ollama` for a free baseline and against `claude` for the number that
describes production. It bills the configured provider like any other caller.

Reports, per topic and per check, four things kept apart:

    engaged/agreed     the check found its input and was satisfied
    engaged/rejected   the check found its input and refused the question
    inert              the check could not locate its input at all
    n/a                the check does not apply to this topic

`inert` is the number to read. A check that is mostly inert is not passing --
it is absent, and the questions it was meant to catch are reaching students
unchecked.
"""

import argparse
import collections
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_REPO_ROOT, "Website", "AdaptiveLearning", "backend"))

import grade_appropriateness          # noqa: E402
import grade_levels                   # noqa: E402
import question_consistency as qc     # noqa: E402
import llm_client                     # noqa: E402
import LLM_topic_decider as decider    # noqa: E402

# The five topics whose scored field is a comparable multiset -- the same five
# `question_consistency` is wired into. algebra/expressions/geometry/
# angle_relationships mix operators and labels with numbers, so there is no
# list to compare and the dataset check correctly does not apply to them.
DATASET_TOPICS = ("mean", "median", "mode", "ordering", "probability")

ALL_TOPICS = ("algebra", "ordering", "rationals", "mean", "median", "mode",
              "probability", "geometry", "angle_relationships", "expressions")


def _scored_values(topic, data):
    """The list the solver will actually compute over, per topic."""
    if topic == "ordering":
        return data.get("values")
    if topic == "probability":
        items = data.get("items")
        # `items` is a mapping of category -> count for the probability
        # generator; the comparable multiset is the counts.
        if isinstance(items, dict):
            return list(items.values())
        return items
    return data.get("variables")


def _classify(topic, data, grade_band):
    """One question -> {check_name: outcome}."""
    text = data.get("question_text") or ""
    out = {}

    # --- dataset agreement -------------------------------------------------
    if topic not in DATASET_TOPICS:
        out["dataset"] = "n/a"
    else:
        state, _ = qc.dataset_check(text, _scored_values(topic, data))
        if state == qc.ENGAGED_MISMATCH:
            out["dataset"] = "engaged/rejected"
        elif state == qc.ENGAGED_AGREED:
            out["dataset"] = "engaged/agreed"
        else:
            # Carry the specific inert state: "no list in the text" and
            # "the scored field was not numeric" are different problems.
            out["dataset"] = f"inert:{state.removeprefix('inert_')}"

    # --- negation agreement (probability only) -----------------------------
    if topic != "probability":
        out["negation"] = "n/a"
    elif "scenario" not in data:
        out["negation"] = "inert:no_scenario"
    else:
        out["negation"] = ("engaged/rejected"
                           if qc.negation_mismatch(text, data["scenario"])
                           else "engaged/agreed")

    # --- grade appropriateness ---------------------------------------------
    # Unlike the two above this one always has its input (the question text),
    # so it cannot go inert -- it is reported for the rejection rate only.
    if not text:
        out["grade"] = "inert:no_text"
    else:
        out["grade"] = ("engaged/rejected"
                        if grade_appropriateness.find_violation(text, topic, grade_band)
                        else "engaged/agreed")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--per-topic", type=int, default=3,
                    help="questions to generate per topic (default 3)")
    ap.add_argument("--grade", default="5th Grade")
    ap.add_argument("--difficulty", default="medium")
    ap.add_argument("--topics", nargs="*", default=list(ALL_TOPICS))
    args = ap.parse_args()

    grade_band = grade_levels.grade_band(args.grade)
    print(f"provider={llm_client.LLM_PROVIDER} "
          f"model={llm_client.CLAUDE_MODEL if llm_client.LLM_PROVIDER == 'claude' else 'llama'} "
          f"grade={args.grade!r} band={grade_band} difficulty={args.difficulty}\n")

    tally = collections.defaultdict(collections.Counter)
    failures = collections.Counter()

    for topic in args.topics:
        for i in range(args.per_topic):
            try:
                data = decider.question_generation(topic, args.difficulty, args.grade, [], [])
            except Exception as e:
                # A generator that exhausts its retries raises; that is a real
                # outcome to report, not a reason to stop measuring.
                failures[topic] += 1
                print(f"  {topic}[{i}] generation failed: {type(e).__name__}: {e}")
                continue
            if not isinstance(data, dict):
                failures[topic] += 1
                continue
            for check, outcome in _classify(topic, data, grade_band).items():
                tally[(topic, check)][outcome] += 1

    print(f"\n{'topic':<20} {'check':<10} {'outcome':<28} n")
    print("-" * 70)
    for (topic, check), counter in sorted(tally.items()):
        for outcome, n in counter.most_common():
            print(f"{topic:<20} {check:<10} {outcome:<28} {n}")

    # The headline: how much of the dataset check was actually doing work.
    eng = sum(n for (t, c), ctr in tally.items() if c == "dataset"
              for o, n in ctr.items() if o.startswith("engaged"))
    inert = sum(n for (t, c), ctr in tally.items() if c == "dataset"
                for o, n in ctr.items() if o.startswith("inert"))
    if eng + inert:
        print(f"\ndataset check engaged on {eng}/{eng + inert} "
              f"({100 * eng / (eng + inert):.0f}%) of the questions it applies to")
        print("A low number here means the check is absent, not that it passed.")
    if failures:
        print(f"\ngeneration failures: {dict(failures)}")


if __name__ == "__main__":
    main()
