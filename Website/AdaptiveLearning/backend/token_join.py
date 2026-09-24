"""Turn a model's `variables` list into the string a solver can parse.

A model may write `[3, "+", 2]`; a bare `"".join` raises TypeError outside the
retry loop. Shared by both token-list topics.
"""

# Numbers are coerced; lists, dicts and nulls are unscoreable and rejected.
_SCALAR = (str, int, float)


def join_tokens(variables):
    """The joined expression, or None (callers retry on None) if the list cannot produce one."""
    if not isinstance(variables, list) or not variables:
        return None
    if not all(isinstance(token, _SCALAR) and not isinstance(token, bool)
               for token in variables):
        return None
    # U+2212 MINUS SIGN: models emit it for a hyphen and sympy rejects it.
    return "".join(str(token) for token in variables).replace("−", "-")
