"""One way to render a numeric answer, shared by the generators.

Six generation files carry their own formatter (`serialize_sympy`,
`format_number`, `serialize_answer`), and the copies drifted the moment the
values they format became plain floats rather than sympy objects:

- `mean` returned `"6.0"` while its distractors were `"31"`, `"11"`, `"12"`.
  On every whole-number-average question -- which the easy and medium tiers
  ask for explicitly -- **the answer was the only option ending in `.0`, so it
  could be picked without doing the arithmetic.**
- `mode` returned `["5.0", "7.0"]` for a dataset written "5, 5, 5, 7". No tell,
  but no option matched how the question reads.

`median` was unaffected because its `format_number` already trimmed the
trailing zero -- which is the argument for one function rather than six: two of
three copies were wrong and the third was right by accident of when it was
written.
"""


def format_value(value):
    """A number as a student would write it: `6.0` -> `"6"`, `6.25` -> `"6.25"`.

    Idempotent, so a value already formatted passes through unchanged. That is
    what lets a test assert every option is in canonical form -- an option that
    changes when re-formatted was rendered by a different rule from its
    neighbours, which is exactly the tell above.
    """
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            return value
    number = float(value)
    if number.is_integer():
        return str(int(number))
    # Two places, with trailing zeros trimmed -- matching what
    # `incorrect_solution_generation` has always produced for distractors.
    return f"{number:.2f}".rstrip("0").rstrip(".")
