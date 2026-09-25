"""Generate every kindergarten scenario against a real model and count the attempts each takes.

Uses the provider `backend/.env` names (or `LLM_PROVIDER` in the environment), so a Claude run is
billed. Exit status is non-zero if any run failed to produce a question.

    python scripts/trial_kindergarten_prompts.py --runs 5 [--only one_more,one_less]
"""
import argparse
import os
import random
import sys

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "Website", "AdaptiveLearning", "backend")
sys.path.insert(0, BACKEND)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(BACKEND, ".env"))

import llm_client  # noqa: E402
import LLM_kindergarten_generation as kg  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=5, help="seeds per (topic, tier, scenario) cell")
    parser.add_argument("--only", default="", help="comma-separated scenario names")
    args = parser.parse_args()
    only = set(filter(None, args.only.split(",")))

    calls = []
    real = llm_client.generate_text
    kg.llm_client.generate_text = lambda prompt, **kw: calls.append(1) or real(prompt, **kw)
    model = llm_client.CLAUDE_MODEL if llm_client.LLM_PROVIDER == "claude" else "ollama"
    print(f"provider: {llm_client.LLM_PROVIDER} ({model}), runs per cell: {args.runs}")

    failed = 0
    for topic, tiers in kg.SCENARIOS.items():
        for difficulty, scenarios in tiers.items():
            for scenario in scenarios:
                if only and scenario not in only:
                    continue
                original = kg.SCENARIOS[topic][difficulty]
                kg.SCENARIOS[topic][difficulty] = [scenario]
                attempts = []
                for seed in range(args.runs):
                    calls.clear()
                    try:
                        q = kg.generate_kindergarten_question(
                            [], [], difficulty, "Kindergarten", topic=topic, rng=random.Random(seed))
                        attempts.append(len(calls))
                        print(f"  {scenario}/{difficulty}/{seed}: {q['question_text']!r} -> {q['correct_answer']}")
                    except Exception as e:  # noqa: BLE001 - a trial records every failure
                        attempts.append("FAIL")
                        print(f"  {scenario}/{difficulty}/{seed}: FAIL {type(e).__name__}: {e}")
                kg.SCENARIOS[topic][difficulty] = original
                ok = sum(isinstance(a, int) for a in attempts)
                first = sum(a == 1 for a in attempts)
                failed += len(attempts) - ok
                print(f"{scenario:18} {difficulty:6} ok={ok}/{len(attempts)} "
                      f"first_try={first}/{len(attempts)} {attempts}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
