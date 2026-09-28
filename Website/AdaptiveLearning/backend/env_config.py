"""Numeric settings read at import. Its own module so `main`, `llm_client` and `eeg_poller`
share one copy: `main` imports the other two, so none of them can import it from `main`."""

import math
import os


def env_number(name: str, default, cast, minimum=None):
    """Read a numeric setting at import, falling back to `default` on a bad value.

    Below `minimum` clamps to the minimum; non-finite values ("inf", "nan")
    fall back to the default, since inf passes any floor and nan fails every one.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = cast(raw)
    except (TypeError, ValueError):
        print(f"[config] {name}={raw!r} is not a number; using {default}")
        return default
    if not math.isfinite(value):
        print(f"[config] {name}={raw!r} is not a finite number; using {default}")
        return default
    if minimum is not None and value < minimum:
        print(f"[config] {name}={raw!r} is below the usable minimum; using {minimum}")
        return minimum
    return value
