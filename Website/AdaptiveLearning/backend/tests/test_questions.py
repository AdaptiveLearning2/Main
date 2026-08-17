"""The question bank's list and count endpoints.

Both shipped untested. `/api/questions/count` states in its own docstring that
it follows "the same three-state rule as the reporting helpers" -- a rule that
is asserted everywhere else it is used in this codebase, because the failure it
prevents is silent by construction: a count that degrades to 0 renders as a
question bank with nothing in it, which is indistinguishable from a real one.

The ordering on `/api/questions` is the other half. Without it the endpoint
returned whatever Postgres handed back, so the dashboard's "Recent Questions"
panel showed an arbitrary five of however many it had pulled -- a list that
looks chronological and is not, which is exactly the kind of wrong that nobody
reports as a bug.
"""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402


class _Questions:
    """A `questions` table that records how it was queried.

    The count endpoint asks PostgREST for a header rather than rows, so `count`
    and `data` are independent here -- a fake that derived one from the other
    could not tell the two apart, and telling them apart is the whole point of
    `count="exact"`.
    """

    def __init__(self, rows=(), count=None, raises=None):
        # `None` is a real answer from this client and has to survive as one:
        # PostgREST hands back a null `data` often enough that every caller in
        # main.py guards with `or []`, and a fake that normalised it could not
        # test the guard.
        self.rows = None if rows is None else list(rows)
        self.count = count
        self.raises = raises
        self.selects = []     # (cols, kwargs) per call
        self.orders = []      # (column, desc)
        self.limits = []
        self.filters = []

    def table(self, name):
        client = self
        assert name == "questions", f"unexpected table {name}"

        class _Q:
            def select(self, *cols, **kw):
                client.selects.append((cols, kw))
                return self

            def order(self, col, desc=False, **_k):
                client.orders.append((col, desc))
                return self

            def limit(self, n, *_a, **_k):
                client.limits.append(n)
                return self

            def eq(self, col, val):
                client.filters.append((col, val))
                return self

            def execute(self):
                if client.raises:
                    raise client.raises
                return type("R", (), {"data": client.rows, "count": client.count})()

        return _Q()


@pytest.fixture
def _questions(monkeypatch):
    def _install(**kw):
        c = _Questions(**kw)
        monkeypatch.setattr(main, "supabase", c)
        return c
    return _install


# ── /api/questions/count ────────────────────────────────────────────────────

def test_the_count_comes_from_the_header_not_the_rows(_questions):
    """`count="exact"` is what makes this cheap *and* correct.

    The dashboard used to fetch `?limit=1000` and take the length, which was
    silently wrong above the cap: a bank that grew past 1000 simply stopped
    counting. So the assertion is that the answer is the count PostgREST
    reported, not the number of rows that came back with it.
    """
    c = _questions(rows=[{"id": "q1"}], count=4212)

    out = main.count_questions()

    assert out == {"total": 4212, "retrieved": True}
    assert any(kw.get("count") == "exact" for _, kw in c.selects), (
        "the endpoint is not asking PostgREST to count; it is counting rows")
    # And it does not pull the bank down to do it.
    assert c.limits == [1], f"expected a single-row limit, got {c.limits}"


def test_a_failed_count_is_not_an_empty_question_bank(_questions):
    """The three-state rule, on the endpoint that names it in its docstring.

    `{"total": 0}` and a failed read are the same tile to a teacher -- "no
    questions" -- and one of them is a claim about the database made from a
    request that never landed.
    """
    _questions(raises=RuntimeError("postgrest down"))

    out = main.count_questions()

    assert out["retrieved"] is False
    assert out["total"] is None, (
        "a failed count came back as a number, which renders as a real zero")


def test_a_genuinely_empty_bank_still_counts_zero(_questions):
    """The mirror, so the check above cannot be satisfied by never counting."""
    _questions(rows=[], count=0)

    assert main.count_questions() == {"total": 0, "retrieved": True}


def test_a_null_count_is_zero_rather_than_null(_questions):
    """PostgREST omits the count when it was not asked for one.

    Distinct from the failure above on purpose: the request succeeded, so
    `retrieved` stays true and the total falls back to 0 rather than claiming
    the read did not happen.
    """
    _questions(rows=[], count=None)

    assert main.count_questions() == {"total": 0, "retrieved": True}


@pytest.mark.parametrize("kwargs,expected", [
    ({"subject": "algebra"}, [("subject", "algebra")]),
    ({"difficulty": "hard"}, [("difficulty", "hard")]),
    ({"subject": "algebra", "difficulty": "hard"},
     [("subject", "algebra"), ("difficulty", "hard")]),
    ({}, []),
])
def test_the_count_is_filtered_the_same_way_the_list_is(_questions, kwargs, expected):
    """A total that ignored the filters would disagree with the list beside it."""
    c = _questions(count=3)

    main.count_questions(**kwargs)

    assert c.filters == expected


# ── /api/questions ──────────────────────────────────────────────────────────

def test_the_list_is_newest_first(_questions):
    """Without an order this returned whatever Postgres handed back, so the
    dashboard's "Recent Questions" panel showed an arbitrary five of however
    many it had pulled -- a list that looks chronological and is not.

    Asserted on the query rather than on the returned rows: the fake hands back
    whatever it was given, so checking the output would pass just as happily
    with the ordering removed.
    """
    c = _questions(rows=[{"id": "q1"}])

    main.get_questions()

    assert ("created_at", True) in c.orders, (
        f"the question list is not ordered newest-first: {c.orders}")


def test_the_list_passes_its_limit_through(_questions):
    c = _questions(rows=[])

    main.get_questions(limit=5)

    assert c.limits == [5]


def test_the_list_returns_an_empty_list_rather_than_none(_questions):
    """`res.data or []` -- the page maps over this."""
    _questions(rows=None)

    assert main.get_questions() == []
