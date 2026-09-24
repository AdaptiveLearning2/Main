"""A reply with no JSON in it (`extract_json` -> None) must cost a retry, not crash the loop."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import importlib  # noqa: E402

import json  # noqa: E402
import pytest  # noqa: E402

import lesson_plan_context  # noqa: E402
import llm_client  # noqa: E402

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Derived from filenames, not ALL_TOPICS: `angle_relationships` lives in
# LLM_angle_relationship_generation.py.
TOPICS = sorted(
    name[len("LLM_"):-len("_generation.py")]
    for name in os.listdir(BACKEND)
    if name.startswith("LLM_") and name.endswith("_generation.py")
)


def test_the_topic_list_is_not_empty():
    """An empty glob would make the parametrised test below pass with zero cases."""
    assert len(TOPICS) >= 16, TOPICS


@pytest.mark.parametrize("topic", TOPICS)
def test_a_response_with_no_json_is_retried_rather_than_raising(topic, monkeypatch):
    """Counts attempts: running out after three is honest, an AttributeError on one is not."""
    module = importlib.import_module(f"LLM_{topic}_generation")

    # Otherwise it reaches for Supabase on every attempt; not under test.
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
    """Asserts which branch fired, so each branch has a case of its own."""
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
