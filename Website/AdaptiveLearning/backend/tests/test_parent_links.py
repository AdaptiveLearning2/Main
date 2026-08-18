"""Ending a parent-child link.

There was no route for this, so a link was permanent once made. That matters
more than the missing button suggests: `_verify_can_view_student` reads a link
as entitlement to a child's reports, and `signal_consent` reads it as the right
to switch a sensor back on after the child switched it off. A relationship that
cannot be ended is the wrong shape for the one relationship in this product
that grants the most.

What these pin is the two things that could go wrong in the other direction:
deleting a link that is not the caller's, and deleting anything *else* while
deleting a link.
"""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402

PARENT = "parent-1"
CHILD = "child-1"


class _Result:
    def __init__(self, data):
        self.data = data


class _Delete:
    def __init__(self, table, rows, log):
        self._table = table
        self._rows = rows
        self._filters = {}
        self._log = log

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def execute(self):
        matched = [r for r in self._rows
                   if all(r.get(k) == v for k, v in self._filters.items())]
        self._log.append((self._table, dict(self._filters), len(matched)))
        for r in matched:
            self._rows.remove(r)
        return _Result(matched)


class _Client:
    """Only what the endpoint touches. A table it does not know about raises,
    so a handler that deleted from a second table would fail loudly here rather
    than passing quietly."""

    def __init__(self, links):
        self.links = links
        self.deletes = []

    def table(self, name):
        if name != "parent_child_links":
            raise AssertionError(f"unlink touched an unexpected table: {name}")
        client = self

        class _T:
            def delete(_self):
                return _Delete(name, client.links, client.deletes)

        return _T()


@pytest.fixture
def client(monkeypatch):
    c = _Client([
        {"id": "l-1", "parent_id": PARENT, "child_id": CHILD},
        {"id": "l-2", "parent_id": "other-parent", "child_id": CHILD},
    ])
    monkeypatch.setattr(main, "supabase", c)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": PARENT})
    return c


def test_a_parent_can_end_their_own_link(client):
    out = main.unlink_child(CHILD, None)
    assert out["ok"] is True
    assert out["child_id"] == CHILD
    assert {"l-2"} == {r["id"] for r in client.links}


def test_the_delete_is_scoped_to_the_caller(client):
    """Both halves of the filter, asserted directly.

    Scoped by `child_id` alone this would take every parent's link to that
    child -- an endpoint one parent could use to cut another off from their own
    child. The row id is never taken from the client for the same reason
    `/charts` derives its object path instead of reading it back out of
    `chart_paths`.
    """
    main.unlink_child(CHILD, None)
    _table, filters, _n = client.deletes[0]
    assert filters == {"parent_id": PARENT, "child_id": CHILD}


def test_a_link_that_is_not_yours_is_a_404_not_a_cheerful_ok(client):
    """"That is done" and "that was never yours" are different facts about the
    caller's account, and a parent who unlinked the wrong child needs to be able
    to tell them apart."""
    with pytest.raises(main.HTTPException) as e:
        main.unlink_child("someone-elses-child", None)
    assert e.value.status_code == 404
    assert len(client.links) == 2


def test_unlinking_destroys_nothing_but_the_link(client):
    """Unlinking is not erasure and not a withdrawal.

    `signal_consent` still holds what the family decided and every recorded row
    survives, so a re-link restores the view of a history that was never
    destroyed. Erasure is `POST /api/consent/{id}/erase`, which a parent has to
    ask for by name -- wiring destruction to this would make the reversible
    control the irreversible one by a side effect nobody asked for, which is the
    rule `test_changing_consent_never_erases` pins one table over.

    The fake raises on any table but `parent_child_links`, so this fails loudly
    rather than by inspection.
    """
    main.unlink_child(CHILD, None)
    assert [t for t, _f, _n in client.deletes] == ["parent_child_links"]
