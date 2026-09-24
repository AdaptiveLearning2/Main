"""Measure how often the fail-open generation checks *engage*, not just fire.

    python scripts/measure_generation_checks.py --per-topic 3   (bills LLM_PROVIDER)

Per topic and check: engaged/agreed, engaged/rejected, inert (input not found), n/a
(not wired). `inert` is the number to read: a mostly-inert check is absent, not passing.
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

# Which topics wire which check is observed at the call sites, not listed here.

# Owns no rows, so the "do not repeat" history is empty and prompts are reproducible.
MEASURE_USER_ID = "00000000-0000-0000-0000-000000000000"

ALL_TOPICS = ("algebra", "ordering", "rationals", "mean", "median", "mode",
              "probability", "geometry", "angle_relationships", "expressions")


# ─── observing the checks where they actually run ────────────────────────
# Spies on the module attributes, since the checks see raw model JSON, not the
# returned dict; every generator reaches them through the module, so one swap covers all.

_SEEN = collections.defaultdict(list)

_orig_dataset_mismatch = qc.dataset_mismatch
_orig_negation_mismatch = qc.negation_mismatch
_orig_refuse = grade_appropriateness.refuse


def _spy_dataset(text, values):
    state, reason = qc.dataset_check(text, values)
    _SEEN["dataset"].append(state)
    return reason


def _spy_negation(text, scenario):
    reason = _orig_negation_mismatch(text, scenario)
    _SEEN["negation"].append(qc.ENGAGED_MISMATCH if reason else qc.ENGAGED_AGREED)
    return reason


def _spy_refuse(text, topic, band, difficulty, attempt):
    refused = _orig_refuse(text, topic, band, difficulty, attempt)
    _SEEN["grade"].append(bool(refused))
    return refused


def _install_spies():
    qc.dataset_mismatch = _spy_dataset
    qc.negation_mismatch = _spy_negation
    grade_appropriateness.refuse = _spy_refuse


def _classify(topic):
    """What the checks did during the generation that just finished; clears `_SEEN`.

    A check never called is "n/a", distinct from "inert" (called, found no input).
    """
    out = {}

    for name in ("dataset", "negation"):
        states = _SEEN.get(name) or []
        if not states:
            out[name] = "n/a"
        elif qc.ENGAGED_MISMATCH in states:
            out[name] = "engaged/rejected"
        elif qc.ENGAGED_AGREED in states:
            out[name] = "engaged/agreed"
        else:
            # Carry the specific inert state; the reasons are different problems.
            out[name] = f"inert:{states[-1].removeprefix('inert_')}"

    refusals = _SEEN.get("grade") or []
    if not refusals:
        out["grade"] = "n/a"
    else:
        # Every attempt is recorded, so a retried question shows its rejection.
        out["grade"] = "engaged/rejected" if any(refusals) else "engaged/agreed"

    _SEEN.clear()
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

    _install_spies()
    tally = collections.defaultdict(collections.Counter)
    failures = collections.Counter()

    for topic in args.topics:
        for i in range(args.per_topic):
            try:
                # (topic, difficulty, user_id, grade); history lists are the generators' own.
                data = decider.question_generation(
                    topic, args.difficulty, MEASURE_USER_ID, args.grade)
            except Exception as e:
                # Exhausted retries are an outcome to report, not a reason to stop.
                failures[topic] += 1
                print(f"  {topic}[{i}] generation failed: {type(e).__name__}: {e}")
                # Or this question's checks count against the next one.
                _SEEN.clear()
                continue
            if not isinstance(data, dict):
                failures[topic] += 1
                continue
            for check, outcome in _classify(topic).items():
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
