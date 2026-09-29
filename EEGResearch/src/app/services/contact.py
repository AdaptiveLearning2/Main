"""Whether each electrode is seated, from the bridge's `hsi` and `is_good` lists."""

from __future__ import annotations

from typing import Any

# The bridge's channel order, which both lists follow.
CHANNELS = ("tp9", "af7", "af8", "tp10")


def seated_channels(meta: dict[str, Any] | None) -> list[bool] | None:
    """Per channel, in CHANNELS order, whether contact data vouches for it; None with no data.

    The bridge's band-power mask: IS_GOOD >= 1 and HSI <= 2, read independently. An entry that
    is neither a number nor null leaves that channel to the other list; NaN or null unseats it."""
    meta = meta or {}
    lists = [(meta.get("is_good"), lambda v: v >= 1.0), (meta.get("hsi"), lambda v: v <= 2.0)]
    lists = [(values, ok) for values, ok in lists
             if isinstance(values, list) and len(values) == len(CHANNELS)]
    if not lists:
        return None
    seated = [True] * len(CHANNELS)
    for values, ok in lists:
        for i, value in enumerate(values):
            # The bridge writes a non-finite value as null; NaN fails both comparisons, so both unseat.
            try:
                number = float("nan") if value is None else float(value)
            except (TypeError, ValueError):
                continue
            if not ok(number):
                seated[i] = False
    return seated
