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


# ── the parent is told ──────────────────────────────────────────────────────
#
# The reverse direction, and the one the consent model had no path for at all:
# a parent re-enabling raises `needs_student_ack`, and a *student* switching a
# sensor off raised nothing.
#
# Read from `consent_withdrawals`, an append-only log, rather than from
# `signal_consent`'s `*_revoked_at` -- those are nulled when a channel is turned
# back on, so the first version of this feature had a parent restoring a channel
# erase the notice for every linked parent at once.


class _WithdrawalClient(_Client):
    """`parent_child_links` plus the append-only withdrawal log."""

    def __init__(self, links, withdrawals):
        super().__init__(links)
        self.withdrawals = withdrawals

    def table(self, name):
        if name == "consent_withdrawals":
            rows, log = self.withdrawals, self.reads

            class _W:
                def select(_self, *_cols, **_kw):
                    class _Q:
                        def in_(_s, col, vals):
                            _s.rows = [r for r in rows if r.get(col) in vals]
                            return _s

                        def order(_s, col, desc=False):
                            _s.rows = sorted(_s.rows, key=lambda r: r[col],
                                             reverse=desc)
                            return _s

                        def limit(_s, n):
                            _s.rows = _s.rows[:n]
                            return _s

                        def execute(_s):
                            log.append(("withdrawals", {}, len(_s.rows)))
                            return _Result(_s.rows)
                    return _Q()
            return _W()
        return super().table(name)


def _w(user_id, channel, at):
    return {"user_id": user_id, "channel": channel, "withdrawn_at": at}


@pytest.fixture
def notices(monkeypatch):
    c = _WithdrawalClient(
        links=[{"id": "l-1", "parent_id": PARENT, "child_id": CHILD,
                "created_at": "2026-08-01T09:00:00Z",
                "parent_ack_at": "2026-08-10T09:00:00Z"}],
        withdrawals=[],
    )
    monkeypatch.setattr(main, "supabase", c)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": PARENT})
    monkeypatch.setattr(main, "_profiles_many",
                        lambda ids: {i: {"display_name": "Ada"} for i in ids})
    return c


def test_a_withdrawal_after_the_last_look_is_reported(notices):
    notices.withdrawals.append(_w(CHILD, "camera", "2026-08-12T09:00:00Z"))
    out = main.parent_consent_notices(None)
    assert out["retrieved"] is True
    assert [c["channel"] for c in out["notices"][0]["channels"]] == ["camera"]
    assert out["notices"][0]["child_name"] == "Ada"


def test_a_withdrawal_the_parent_has_already_seen_is_not_repeated(notices):
    notices.withdrawals.append(_w(CHILD, "camera", "2026-08-09T09:00:00Z"))
    assert main.parent_consent_notices(None)["notices"] == []


def test_nothing_withdrawn_is_no_notice(notices):
    assert main.parent_consent_notices(None)["notices"] == []


def test_a_parent_who_never_acknowledged_sees_everything(notices):
    notices.links[0]["parent_ack_at"] = None
    notices.withdrawals.append(_w(CHILD, "eeg", "2026-07-01T09:00:00Z"))
    out = main.parent_consent_notices(None)
    assert [c["channel"] for c in out["notices"][0]["channels"]] == ["eeg"]


def test_re_enabling_the_channel_does_not_erase_the_notice(notices):
    """The bug this table exists for.

    `set_consent` nulls `*_revoked_at` when a channel is turned back on --
    correctly, since that column answers "is this off, and since when". Deriving
    the notice from it meant a parent restoring the channel erased the notice
    for every linked parent, and a withdraw/restore inside one dashboard-load
    interval made the whole feature silently no-op.

    The event row is append-only, so the notice survives the restore. Modelled
    by the fixture holding no consent row at all: if the endpoint still read
    current state it would have nothing to report.
    """
    notices.withdrawals.append(_w(CHILD, "camera", "2026-08-12T09:00:00Z"))
    out = main.parent_consent_notices(None)
    assert [c["channel"] for c in out["notices"][0]["channels"]] == ["camera"]


def test_one_channel_switched_off_twice_is_one_line(notices):
    notices.withdrawals += [_w(CHILD, "camera", "2026-08-12T09:00:00Z"),
                            _w(CHILD, "camera", "2026-08-11T09:00:00Z")]
    out = main.parent_consent_notices(None)
    channels = out["notices"][0]["channels"]
    assert [c["channel"] for c in channels] == ["camera"]
    # The newest, so the date shown is the last time it happened.
    assert channels[0]["at"] == "2026-08-12T09:00:00Z"


def test_the_read_is_scoped_to_the_caller_s_own_links(notices):
    """Unscoped, this is a list of which children have withdrawn what,
    platform-wide."""
    main.parent_consent_notices(None)
    kind, filters, _n = notices.reads[0]
    assert kind == "select"
    assert filters == {"parent_id": PARENT}


def test_a_failed_read_says_so_rather_than_reporting_no_withdrawals(monkeypatch, notices):
    """Fails open, like the link notice: a blip must not tell a parent their
    child withdrew something. `retrieved: false` is what stops the banner
    reading that failure as a confident "nothing happened"."""
    class _Boom:
        def table(self, _name):
            raise RuntimeError("postgrest is down")
    monkeypatch.setattr(main, "supabase", _Boom())

    assert main.parent_consent_notices(None) == {"notices": [], "retrieved": False}


# ── acknowledging ───────────────────────────────────────────────────────────


def _ack(through):
    return main.ack_parent_consent_notices(main.ConsentNoticeAck(through=through), None)


def test_acknowledging_stamps_the_watermark_it_was_given(notices):
    notices.withdrawals.append(_w(CHILD, "camera", "2026-08-12T09:00:00Z"))
    out = main.parent_consent_notices(None)
    _ack({CHILD: out["notices"][0]["through"]})

    assert notices.links[0]["parent_ack_at"] == "2026-08-12T09:00:00Z"
    assert main.parent_consent_notices(None)["notices"] == []


def test_a_withdrawal_landing_during_the_read_is_not_swallowed(notices):
    """The race the watermark exists for.

    Stamping `now()` acknowledges withdrawals the parent never saw: one landing
    between the read that drew the banner and the click that dismissed it was
    marked seen and never shown again -- silently and permanently, on the
    notification whose entire job is to stop that happening.
    """
    notices.withdrawals.append(_w(CHILD, "camera", "2026-08-12T09:00:00Z"))
    shown = main.parent_consent_notices(None)["notices"][0]["through"]

    # Lands after the banner was drawn, before the click.
    notices.withdrawals.append(_w(CHILD, "eeg", "2026-08-13T09:00:00Z"))
    _ack({CHILD: shown})

    still = main.parent_consent_notices(None)["notices"]
    assert [c["channel"] for c in still[0]["channels"]] == ["eeg"]


def test_acknowledging_one_child_does_not_clear_another(notices):
    """Two children, and the ack names one of them.

    `.eq("parent_id", ...)` alone stamps every link the parent holds, so
    dismissing one child's notice silently dropped the other's.
    """
    other = "child-2"
    notices.links.append({"id": "l-9", "parent_id": PARENT, "child_id": other,
                          "created_at": "2026-08-01T09:00:00Z",
                          "parent_ack_at": "2026-08-10T09:00:00Z"})
    notices.withdrawals += [_w(CHILD, "camera", "2026-08-12T09:00:00Z"),
                            _w(other, "eeg", "2026-08-12T09:00:00Z")]

    _ack({CHILD: "2026-08-12T09:00:00Z"})

    left = main.parent_consent_notices(None)["notices"]
    assert [n["child_id"] for n in left] == [other]


def test_two_parents_linked_to_one_child_are_told_independently(monkeypatch, notices):
    """The schema's whole justification, asserted rather than assumed.

    The obvious design puts `parent_ack_at` on `signal_consent`, which has one
    row per *student* -- so the first of two linked parents to acknowledge would
    clear the notice for the second, who would never learn their child had
    turned a camera off. On the link row, each parent has their own.
    """
    OTHER_PARENT = "parent-2"
    notices.links.append({"id": "l-9", "parent_id": OTHER_PARENT, "child_id": CHILD,
                          "created_at": "2026-08-01T09:00:00Z",
                          "parent_ack_at": "2026-08-10T09:00:00Z"})
    notices.withdrawals.append(_w(CHILD, "camera", "2026-08-12T09:00:00Z"))

    # The first parent reads it and dismisses it.
    _ack({CHILD: "2026-08-12T09:00:00Z"})
    assert main.parent_consent_notices(None)["notices"] == []

    # The second parent has not, and still sees it.
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": OTHER_PARENT})
    out = main.parent_consent_notices(None)
    assert [c["channel"] for c in out["notices"][0]["channels"]] == ["camera"]


def test_acknowledging_is_scoped_to_the_caller(notices):
    """The link id is never taken from the client, and a `child_id` the caller
    is not linked to matches no row."""
    _ack({CHILD: "2026-08-12T09:00:00Z"})
    kind, filters, _n = notices.reads[0]
    assert kind == "update"
    assert filters == {"parent_id": PARENT, "child_id": CHILD}


def test_acknowledging_a_child_that_is_not_yours_changes_nothing(notices):
    _ack({"someone-elses-child": "2026-08-12T09:00:00Z"})
    assert notices.links[0]["parent_ack_at"] == "2026-08-10T09:00:00Z"
