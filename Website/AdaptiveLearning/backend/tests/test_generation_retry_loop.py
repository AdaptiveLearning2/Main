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

import pytest  # noqa: E402

import lesson_plan_context  # noqa: E402
import llm_client  # noqa: E402

TOPICS = [
    "algebra", "angle_relationship", "expressions", "geometry", "mean",
    "median", "mode", "ordering", "probability", "rationals",
]


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
