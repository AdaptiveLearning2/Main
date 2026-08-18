"""The two ends of a parent-child link: telling the student, and ending it.

There was no route for this, so a link was permanent once made. That matters
more than the missing button suggests: `_verify_can_view_student` reads a link
as entitlement to a child's reports, and `signal_consent` reads it as the right
to switch a sensor back on after the child switched it off. A relationship that
cannot be ended is the wrong shape for the one relationship in this product
that grants the most.

What these pin is the two things that could go wrong in the other direction:
deleting a link that is not the caller's, and deleting anything *else* while
deleting a link.

The other half is the notification. A link is created knowing only the child's
user id -- nothing asks them and, until now, nothing told them either, while the
link grants a parent their reports and the right to switch a sensor back on that
the child switched off.
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


class _Select:
    def __init__(self, rows, log):
        self._rows = rows
        self._filters = {}
        self._log = log

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def is_(self, col, val):
        assert val == "null", f"only IS NULL is modelled, got {val}"
        self._filters[col] = None
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        matched = [r for r in self._rows
                   if all(r.get(k) == v for k, v in self._filters.items())]
        self._log.append(("select", dict(self._filters), len(matched)))
        return _Result(matched)


class _Update:
    def __init__(self, rows, fields, log):
        self._rows = rows
        self._fields = fields
        self._filters = {}
        self._log = log

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def is_(self, col, val):
        assert val == "null", f"only IS NULL is modelled, got {val}"
        self._filters[col] = None
        return self

    def execute(self):
        matched = [r for r in self._rows
                   if all(r.get(k) == v for k, v in self._filters.items())]
        self._log.append(("update", dict(self._filters), len(matched)))
        for r in matched:
            r.update(self._fields)
        return _Result(matched)


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
        self.reads = []

    def table(self, name):
        if name != "parent_child_links":
            raise AssertionError(f"unlink touched an unexpected table: {name}")
        client = self

        class _T:
            def delete(_self):
                return _Delete(name, client.links, client.deletes)

            def select(_self, *_cols, **_kw):
                return _Select(client.links, client.reads)

            def update(_self, fields):
                return _Update(client.links, fields, client.reads)

        return _T()


@pytest.fixture
def client(monkeypatch):
    c = _Client([
        {"id": "l-1", "parent_id": PARENT, "child_id": CHILD,
         "created_at": "2026-08-14T09:00:00Z"},
        {"id": "l-2", "parent_id": "other-parent", "child_id": CHILD,
         "created_at": "2026-08-10T09:00:00Z"},
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


# ── the student is told ─────────────────────────────────────────────────────


@pytest.fixture
def student(monkeypatch, client):
    """The same fake, read as the child rather than the parent."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": CHILD})
    # `_profiles_many` reads a table this fake refuses. Stubbed rather than
    # modelled: the name lookup is not what any of these are about.
    monkeypatch.setattr(main, "_profiles_many",
                        lambda ids: {i: {"display_name": f"Parent {i}"} for i in ids})
    return client


def test_a_new_link_is_reported_to_the_student(student):
    out = main.my_unacknowledged_parent_links(None)
    assert out["retrieved"] is True
    assert {l["parent_name"] for l in out["links"]} ==         {f"Parent {PARENT}", "Parent other-parent"}


def test_an_acknowledged_link_is_not_reported_again(student):
    for row in student.links:
        row["student_ack_at"] = "2026-08-14T09:00:00Z"
    assert main.my_unacknowledged_parent_links(None)["links"] == []


def test_the_read_is_scoped_to_the_caller(student):
    """A student may only ever see links to *them*.

    Unscoped this is a list of who is linked to whom, platform-wide.
    """
    main.my_unacknowledged_parent_links(None)
    _kind, filters, _n = student.reads[0]
    assert filters == {"child_id": CHILD, "student_ack_at": None}


def test_a_failed_read_says_so_rather_than_reporting_no_links(monkeypatch, student):
    """Fails open to an empty list -- it decides whether an advisory banner is
    drawn and nothing else, so a blip must not put a notice on a child's
    dashboard about a link that may not exist.

    But it says which it is. `retrieved: false` is what stops the banner
    treating a failure as a confident "nothing happened", the same three-state
    rule the reporting helpers carry the flag for.
    """
    class _Boom:
        def table(self, _name):
            raise RuntimeError("postgrest is down")
    monkeypatch.setattr(main, "supabase", _Boom())

    out = main.my_unacknowledged_parent_links(None)
    assert out == {"links": [], "retrieved": False}


def test_acknowledging_stamps_every_waiting_link(student):
    out = main.ack_parent_links(None)
    assert out["acknowledged"] == 2
    assert all(r.get("student_ack_at") for r in student.links)


def test_acknowledging_is_scoped_to_the_caller(student):
    """The link id is never taken from the client -- that would let a caller
    stamp a link that is not theirs and suppress somebody else's notice."""
    main.ack_parent_links(None)
    _kind, filters, _n = student.reads[0]
    assert filters == {"child_id": CHILD, "student_ack_at": None}


def test_acknowledging_nothing_is_a_404(student):
    """Reporting success for a dismissal that did not land leaves the client
    believing a notice is gone that is still there -- the same rule
    `/api/consent/ack` beside it already follows."""
    for row in student.links:
        row["student_ack_at"] = "2026-08-14T09:00:00Z"
    with pytest.raises(main.HTTPException) as e:
        main.ack_parent_links(None)
    assert e.value.status_code == 404
