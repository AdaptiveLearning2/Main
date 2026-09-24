"""The question bank's list and count endpoints: a failed count is not zero, the list is ordered."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402


class _Questions:
    """A `questions` table that records how it was queried; `count` and `data` are independent."""

    def __init__(self, rows=(), count=None, raises=None):
        # `None` stays `None`: PostgREST returns null `data`, which callers guard with `or []`.
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


@pytest.fixture(autouse=True)
def _reset_questions_cache():
    """The questions cache is module-level state shared across tests."""
    main._questions_cache._store.clear()
    yield


# ── /api/questions/count ────────────────────────────────────────────────────

def test_the_count_comes_from_the_header_not_the_rows(_questions):
    """A row count stops counting past the 1000-row cap."""
    c = _questions(rows=[{"id": "q1"}], count=4212)

    out = main.count_questions()

    assert out == {"total": 4212, "retrieved": True}
    assert any(kw.get("count") == "exact" for _, kw in c.selects), (
        "the endpoint is not asking PostgREST to count; it is counting rows")
    # And it does not pull the bank down to do it.
    assert c.limits == [1], f"expected a single-row limit, got {c.limits}"


def test_a_failed_count_is_not_an_empty_question_bank(_questions):
    _questions(raises=RuntimeError("postgrest down"))

    out = main.count_questions()

    assert out["retrieved"] is False
    assert out["total"] is None, (
        "a failed count came back as a number, which renders as a real zero")


def test_a_genuinely_empty_bank_still_counts_zero(_questions):
    """The mirror case, so the check above can't be satisfied by never counting."""
    _questions(rows=[], count=0)

    assert main.count_questions() == {"total": 0, "retrieved": True}


def test_a_null_count_is_zero_rather_than_null(_questions):
    """The request succeeded, so `retrieved` stays true and the total falls back to 0."""
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
    c = _questions(count=3)

    main.count_questions(**kwargs)

    assert c.filters == expected


# ── /api/questions ──────────────────────────────────────────────────────────

def test_the_list_is_newest_first(_questions):
    """Asserted on the query: the fake echoes rows, so the output passes unordered."""
    c = _questions(rows=[{"id": "q1"}])

    main.get_questions()

    assert ("created_at", True) in c.orders, (
        f"the question list is not ordered newest-first: {c.orders}")


def test_the_list_passes_its_limit_through(_questions):
    c = _questions(rows=[])

    main.get_questions(limit=5)

    assert c.limits == [5]


@pytest.mark.parametrize("asked,sent", [
    (10_000_000, 1000),   # the whole bank, from an unauthenticated caller
    (1001, 1000),         # one past the ceiling
    (1000, 1000),         # the largest a real surface asks for, untouched
    (0, 1),
    (-5, 1),
])
def test_the_list_clamps_the_limit_it_was_handed(_questions, asked, sent):
    """Asserted on the query (rule 4). Unfloored, `?limit=-1` is a 500."""
    c = _questions(rows=[])

    main.get_questions(limit=asked)

    assert c.limits == [sent], f"asked for {asked}, query received {c.limits}"


def test_the_cache_is_keyed_on_the_clamped_limit(_questions):
    """Otherwise 9999 and 10000 are two keys holding one copy of the bank each."""
    c = _questions(rows=[{"id": "q1"}])

    main.get_questions(limit=9999)
    main.get_questions(limit=10_000)

    assert len(c.selects) == 1, (
        "two limits that clamp to the same value took two cache entries")


@pytest.mark.parametrize("field", ["subject", "difficulty"])
def test_an_empty_filter_shares_the_entry_of_no_filter(_questions, field):
    """The key is built from what decides the query."""
    c = _questions(rows=[{"id": "q1"}])

    main.get_questions(limit=100, **{field: ""})
    main.get_questions(limit=100)

    assert len(c.selects) == 1, (
        f"an empty {field} and no {field} took two cache entries")
    assert c.filters == [], "an empty filter reached the query as a real one"


def test_the_list_returns_an_empty_list_rather_than_none(_questions):
    """The page maps over this result."""
    _questions(rows=None)

    assert main.get_questions() == []


# ── caching ──────────────────────────────────────────────────────────────

def test_a_repeated_call_within_the_ttl_does_not_requery(_questions):
    """Analytics.jsx and Questions.jsx both fetch `?limit=1000` on mount."""
    c = _questions(rows=[{"id": "q1"}])

    first = main.get_questions(limit=1000)
    second = main.get_questions(limit=1000)

    assert first == second == [{"id": "q1"}]
    assert len(c.selects) == 1, (
        f"expected one query for two calls inside the TTL, got {len(c.selects)}")


def test_a_different_key_is_not_served_from_another_keys_cache(_questions):
    c = _questions(rows=[{"id": "q1"}])

    main.get_questions(limit=1000)
    main.get_questions(limit=5)

    assert len(c.selects) == 2, (
        "a different limit collided with another limit's cache entry")


def test_a_different_filter_is_not_served_from_another_filters_cache(_questions):
    c = _questions(rows=[{"id": "q1"}])

    main.get_questions(limit=100, subject="algebra")
    main.get_questions(limit=100, subject="geometry")

    assert len(c.selects) == 2, (
        "different subjects collided on the same cache entry")


def test_the_cache_expires_after_its_ttl(_questions, monkeypatch):
    c = _questions(rows=[{"id": "q1"}])
    clock = {"t": 1_000.0}
    monkeypatch.setattr(main.time, "monotonic", lambda: clock["t"])

    main.get_questions(limit=1000)
    clock["t"] += main.QUESTIONS_CACHE_TTL + 1
    main.get_questions(limit=1000)

    assert len(c.selects) == 2, (
        "a call past the TTL was served from a cache entry that should have expired")


def test_the_count_endpoint_is_never_cached(_questions):
    c = _questions(count=3)

    main.count_questions()
    main.count_questions()

    assert len(c.selects) == 2, (
        "count_questions is being served from the list cache")


# ── cache bound ──────────────────────────────────────────────────────────
# The key comes from unauthenticated query params, so entries are bounded; size is the clamp's job.

def test_the_cache_evicts_the_least_recently_used_entry_once_full():
    cache = main._TTLCache(ttl=60.0, max_size=2)

    cache.set("a", 1)
    cache.set("b", 2)
    cache.set("c", 3)  # over capacity -- "a" is the least recently used

    assert cache.get("a") == (None, False), "the cache grew past its bound"
    assert cache.get("b") == (2, True)
    assert cache.get("c") == (3, True)


def test_reading_an_entry_protects_it_from_eviction():
    """LRU, not FIFO."""
    cache = main._TTLCache(ttl=60.0, max_size=2)

    cache.set("a", 1)
    cache.set("b", 2)
    cache.get("a")       # "a" is now the most-recently-used
    cache.set("c", 3)    # "b" is evicted instead

    assert cache.get("a") == (1, True)
    assert cache.get("b") == (None, False)


def test_a_sweep_of_distinct_keys_cannot_grow_the_cache_past_its_bound(_questions):
    _questions(rows=[{"id": "q1"}])
    max_size = main._questions_cache._max_size

    for limit in range(max_size + 50):
        main.get_questions(limit=limit)

    assert len(main._questions_cache._store) <= max_size
