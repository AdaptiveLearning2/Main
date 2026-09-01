"""Difficulty tiers chosen from what a grade can actually see.

Each topic used to map a difficulty to a fixed list of scenario numbers. That
works while every scenario is available, and breaks once a grade filter removes
some: `geometry`'s hard tier is the volumes, and the genuinely hard ones --
cylinder and sphere, with pi -- are 8.G.9, so grades 6 and 7 were left with the
two simplest. Measured: at grade 6, `medium` offered missing-side problems
(invert a formula and divide) while `hard` offered `rect_volume` and
`cube_volume` (multiply three numbers).

That matters beyond tidiness because difficulty is what the biosignals move. A
focused student is pushed from medium to hard, so at those grades acting on the
EEG signal made the question *easier* -- the fusion firing correctly and then
being undone one layer down.

Ranking and slicing fixes it by construction: `hard` is the hardest third of
whatever this grade can see, so the tiers cannot inverate however much the grade
filter removes.
"""


def tiers(ranked):
    """Split `ranked` -- scenario numbers, hardest last -- into three tiers.

    Returns `{"easy": [...], "medium": [...], "hard": [...]}`, each non-empty
    as long as `ranked` is. With fewer than three scenarios the tiers overlap
    rather than emptying: a grade with one scenario gets it whatever the
    difficulty, which is honest, and `random.choice` never sees an empty list.
    """
    items = list(ranked)
    if not items:
        return {"easy": [], "medium": [], "hard": []}
    n = len(items)
    if n < 3:
        # Two scenarios: easiest and hardest, with medium taking both rather
        # than arbitrarily picking one to be "medium".
        return {"easy": items[:1], "medium": items, "hard": items[-1:]}
    cut = n // 3
    return {
        "easy":   items[:cut] or items[:1],
        "medium": items[cut:n - cut] or items[cut:cut + 1],
        "hard":   items[n - cut:] or items[-1:],
    }


def pick(difficulty, allowed, rank):
    """A scenario number for `difficulty`, from `allowed`, ranked by `rank`.

    `rank` maps a scenario number to a difficulty order -- not to a grade. The
    two are different axes and conflating them is how `algebra_complementary`
    (7.G.5, set up and solve an equation) looks easier than `triangle_sum`
    (8.G.5, one subtraction).
    """
    ordered = sorted(allowed, key=lambda s: (rank(s), s))
    band = tiers(ordered)
    return band.get(difficulty) or band["medium"]
