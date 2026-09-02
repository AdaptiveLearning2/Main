"""A response with no JSON in it must cost a retry, not crash the loop.

`extract_json` answers `None` when the model wrapped its answer in prose, or
refused, or returned nothing usable -- which is precisely the case the
`for attempt in range(3)` loop exists to absorb. Anything that touches `raw`
before the `if not raw:` guard turns that into an `AttributeError` escaping the
loop, so the retry never happens and the caller sees a crash instead of a
second attempt.

Nine of the ten generators check first. `LLM_median_generation` had the two
lines the other way round.
"""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import importlib  # noqa: E402

import json  # noqa: E402
import pytest  # noqa: E402

import lesson_plan_context  # noqa: E402
import llm_client  # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Derived from the generator files rather than written out. It was a hand-kept
# list of the original ten, so the four topics added for grades 1-3 and the two
# for grades 9-12 were never covered by it -- the same shape as the endpoint
# list in `test_ingest_mode.py` before it was parametrised, where the test
# listed only what someone had already remembered.
#
# Keyed on the *filename* rather than on `ALL_TOPICS`, because the two do not
# always match: the `angle_relationships` topic lives in
# `LLM_angle_relationship_generation.py`, singular. The filename is what the
# import needs.
TOPICS = sorted(
    name[len("LLM_"):-len("_generation.py")]
    for name in os.listdir(BACKEND)
    if name.startswith("LLM_") and name.endswith("_generation.py")
)


def test_the_topic_list_is_not_empty():
    """A glob that matches nothing turns every test below into zero tests, and
    a parametrised suite with no parameters passes silently."""
    assert len(TOPICS) >= 16, TOPICS


@pytest.mark.parametrize("topic", TOPICS)
def test_a_response_with_no_json_is_retried_rather_than_raising(topic, monkeypatch):
    """Parametrised over all ten so the next copy of this cannot be a one-off.

    Asserts on the *number of attempts*: the loop is what proves a retry
    happened, and `ValueError("Failed to generate valid JSON after retries")`
    at the end is the honest way to run out -- an AttributeError on attempt one
    is not.
    """
    module = importlib.import_module(f"LLM_{topic}_generation")

    # Every generator calls this before the model, and it fails open by
    # reaching for Supabase -- three attempts per topic against a database
    # that is not there took this file to two minutes. It is also not what is
    # under test here.
    monkeypatch.setattr(lesson_plan_context, "append_lesson_context",
                        lambda prompt, *_a, **_k: prompt)

    attempts = []

    def _prose(*_a, **_k):
        attempts.append(1)
        return "I'd be happy to help with that! Here is a question about maths."

    monkeypatch.setattr(llm_client, "generate_text", _prose)

    fn = next(getattr(module, n) for n in dir(module)
              if n.startswith("generate_") and n.endswith("_question"))
    with pytest.raises(Exception) as exc:
        fn([], [], "easy", "5th Grade")

    assert not isinstance(exc.value, AttributeError), \
        f"{topic} crashed on attempt {len(attempts)} instead of retrying: {exc.value}"
    assert len(attempts) == 3, f"{topic} made {len(attempts)} attempts, not 3"


@pytest.mark.parametrize("variables,branch", [
    # `join_tokens` returns None: not a list, empty, or a non-scalar token.
    ([], "Unusable variables"),
    ([None], "Unusable variables"),
    ("1/2 + 1/3", "Unusable variables"),
    (["1", "+", None], "Unusable variables"),
    # Joins fine, and the worker refuses the result.
    (["1", "/", "0"], "Could not solve"),
    (["0", "/", "0"], "Could not solve"),
    (["!!"], "Could not solve"),
])
def test_rationals_retries_an_unsolvable_reply_rather_than_raising(variables,
                                                                   branch,
                                                                   monkeypatch,
                                                                   capsys):
    """`rationals` was the last generator solving below its `for/else`, so each
    of these was a 500 on attempt 1 -- and the division by zero was worse than
    that before the worker guard, since "zoo" came back as a usable result and
    was *served* as the correct answer.

    Asserted on which branch fired, not only that it raised. The first version
    of this test claimed to cover both and covered one: `["!!"]` joins fine
    (`join_tokens` returns `'!!'`) and fails at the solve, so both its cases
    exercised the same path and the `equation_str is None` branch had no test
    at all. Two cases that cannot be told apart are one case.
    """
    import LLM_rationals_generation as rationals

    payload = {"question_text": "Solve it", "question_topic": "rationals",
               "variables": variables}
    monkeypatch.setattr(llm_client, "generate_text",
                        lambda *a, **k: json.dumps(payload))
    monkeypatch.setattr(rationals.lesson_plan_context, "append_lesson_context",
                        lambda prompt, topic, band: prompt)
    with pytest.raises(ValueError, match="after retries"):
        rationals.generate_rational_question([], [], "medium", "7th Grade")
    printed = capsys.readouterr().out
    assert branch in printed, f"expected the {branch!r} branch; got: {printed}"
