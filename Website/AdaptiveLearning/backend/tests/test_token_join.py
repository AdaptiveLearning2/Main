"""`variables` comes from the model, so joining it must not raise (it runs before the solve's retry)."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import token_join  # noqa: E402


def test_strings_join_unchanged():
    assert token_join.join_tokens(["2x", "+", "3", "=", "7"]) == "2x+3=7"


def test_numeric_tokens_are_coerced_rather_than_refused():
    assert token_join.join_tokens([3, "+", 2, "=", 5]) == "3+2=5"
    assert token_join.join_tokens([1.5, "+", 2]) == "1.5+2"


def test_the_unicode_minus_is_translated():
    """U+2212 is what a model writes for a minus sign and sympy will not parse."""
    assert token_join.join_tokens(["5", "−", "2"]) == "5-2"


@pytest.mark.parametrize("variables", [
    [["3"], "+", "2"],          # a nested list
    [{"a": 1}, "+", "2"],       # a dict
    ["3", None, "2"],           # a null
    "3+2",                      # a bare string, which would join per-character
    {"a": "3"},                 # a mapping, which would join its keys
    [],                         # empty
    None,
])
def test_anything_that_cannot_make_an_expression_is_none(variables):
    """None, not a raise: every caller's retry loop treats None as "ask again"."""
    assert token_join.join_tokens(variables) is None


def test_booleans_are_refused_even_though_they_are_ints():
    """`True` is an `int`, but `"True+2"` would reach sympy as a symbol."""
    assert token_join.join_tokens([True, "+", "2"]) is None
