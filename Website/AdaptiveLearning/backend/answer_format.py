"""One way to render a numeric answer, shared by the generators.

An answer formatted differently from its distractors (e.g. `"6.0"` among
`"31"`, `"11"`) can be picked without doing the arithmetic.
"""


def format_value(value):
    """A number as a student would write it: `6.0` -> `"6"`, `6.25` -> `"6.25"`.

    Idempotent, so tests can assert every option is already in canonical form.
    """
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return value
    number = float(value)
    if number.is_integer():
        return str(int(number))
    # Two places, trailing zeros trimmed: matches `incorrect_solution_generation`.
    return f"{number:.2f}".rstrip("0").rstrip(".")
