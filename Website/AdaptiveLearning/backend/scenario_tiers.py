"""Difficulty tiers chosen from what a grade can actually see.

`hard` is the hardest third of the scenarios this grade is allowed, so the
tiers cannot invert however much the grade filter removes -- otherwise a
fusion push to `hard` could serve an easier question.
"""


def tiers(ranked):
    """Split `ranked` -- scenario numbers, hardest last -- into three tiers.

    Each tier is non-empty while `ranked` is; with fewer than three scenarios
    the tiers overlap rather than empty, so `random.choice` never sees `[]`.
    """
    items = list(ranked)
    if not items:
        return {"easy": [], "medium": [], "hard": []}
    n = len(items)
    if n < 3:
        # Medium takes both rather than arbitrarily picking one.
        return {"easy": items[:1], "medium": items, "hard": items[-1:]}
    cut = n // 3
    return {
        "easy":   items[:cut] or items[:1],
        "medium": items[cut:n - cut] or items[cut:cut + 1],
        "hard":   items[n - cut:] or items[-1:],
    }


def pick(difficulty, allowed, rank):
    """A scenario number for `difficulty`, from `allowed`, ranked by `rank`.

    `rank` maps a scenario to a difficulty order, not a grade: a lower-grade
    standard can be the harder question.
    """
    ordered = sorted(allowed, key=lambda s: (rank(s), s))
    band = tiers(ordered)
    return band.get(difficulty) or band["medium"]
