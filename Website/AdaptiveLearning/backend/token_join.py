"""Turn a model's `variables` list into the string a solver can parse.

`"".join(variables)` raises `TypeError: sequence item 0: expected str instance,
int found` the moment a model writes `[3, "+", 2]` rather than `["3", "+", "2"]`
-- a JSON-legal reply, and the prompts ask for strings without anything
enforcing it. Uncaught, that is a 500 on the first attempt with no retry, which
is the failure moving the solve inside the retry loop was supposed to remove:
the join happens *before* the solve, so it escaped anyway.

Shared by the two topics whose scored field is a token list, so the coercion
cannot be fixed in one and left in the other.
"""

# What a token may be. A number is coerced because `3` and `"3"` mean the same
# thing here and rejecting the reply would be pedantry; anything else -- a
# nested list, a dict, a null -- is a reply this cannot score, and rendering it
# with str() would put `{'a': 1}` into an expression and fail later, further
# from the cause.
_SCALAR = (str, int, float)


def join_tokens(variables):
    """The joined expression, or None if the list cannot produce one.

    None rather than a raise: every caller is inside a retry loop that already
    treats None as "ask again".
    """
    if not isinstance(variables, list) or not variables:
        return None
    if not all(isinstance(token, _SCALAR) and not isinstance(token, bool)
               for token in variables):
        return None
    # U+2212 MINUS SIGN, which models emit in place of a hyphen and sympy does
    # not accept. Written as an escape: the literal was previously pasted in as
    # mojibake ("âˆ’"), so it could never match what it was
    # meant to catch.
    return "".join(str(token) for token in variables).replace("−", "-")
