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

# No list of "topics the dataset check applies to" is kept here any more, and
# that is the point: this script observes the real call sites, so which topics
# wire which check is something it *reports* rather than something it asserts.
# The list that used to sit here named five topics including `probability`,
# copied from CLAUDE.md, which was wrong -- `LLM_probability_generation` wires
# `negation_mismatch` and not `dataset_mismatch`. A harness carrying its own
# copy of the answer cannot discover that the answer changed.

# A user id that owns no rows. `question_generation` reads recent questions for
# it to build the "do not repeat" block; a miss fails open to an empty list,
# which is what this harness wants -- a real student's history would make each
# run's prompts depend on whoever was picked.
MEASURE_USER_ID = "00000000-0000-0000-0000-000000000000"

ALL_TOPICS = ("algebra", "ordering", "rationals", "mean", "median", "mode",
              "probability", "geometry", "angle_relationships", "expressions")


# ─── observing the checks where they actually run ────────────────────────
#
# The first version of this re-derived each check's inputs from the dict the
# generator RETURNS, and reported the dataset check inert on 15 of 15 -- which
# reads exactly like the finding this script was written to produce, and was an
# artefact. The generators run the check against the raw model JSON, which
# carries `variables`/`values`/`items`; what they return is
# question_text/answer_options/correct_answer, with the scored field stripped.
# So the harness was measuring an object the check never sees, and answering
# "no input" about its own reconstruction.
#
# The lesson is the script's own: a measurement that cannot see its input
# reports absence, and absence reads as a finding. So observe the real calls
# instead of rebuilding their arguments -- module attributes, which every
# generator reaches through the module object, so one swap covers all ten.

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
    """What the checks did during the generation that just finished.

    Reads `_SEEN`, which the spies filled as the generator ran, then clears it.
    A check with no entry was never *called* for this topic -- "not wired"
    ("n/a"), which is a different fact from "called and found no input"
    ("inert"). Collapsing those two is how a check that was never connected
    reads as a check that is working.
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
            # Carry the specific inert state: "no list in the text" and "the
            # scored field was not numeric" are different problems.
            out[name] = f"inert:{states[-1].removeprefix('inert_')}"

    refusals = _SEEN.get("grade") or []
    if not refusals:
        out["grade"] = "n/a"
    else:
        # Every attempt is recorded, so a question that took two tries shows
        # the rejection that caused the retry as well as the acceptance.
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
                # (topic, difficulty, user_id, grade) -- the history lists are
                # the generators' own arguments, not this one's. Called with
                # five positionals this raised TypeError before any request was
                # made, so the run reported an empty table and zero findings:
                # this script's own thesis, applied to itself. A harness that
                # cannot fail loudly measures nothing and says so quietly.
                data = decider.question_generation(
                    topic, args.difficulty, MEASURE_USER_ID, args.grade)
            except Exception as e:
                # A generator that exhausts its retries raises; that is a real
                # outcome to report, not a reason to stop measuring.
                failures[topic] += 1
                print(f"  {topic}[{i}] generation failed: {type(e).__name__}: {e}")
                # Or the checks this question did run would be counted against
                # the next one.
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
