"""Parent-child links: ending one (own link only, nothing else touched) and notifying each side."""
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
    """Only `parent_child_links`; any other table raises."""

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
    """By `child_id` alone, one parent could delete every other parent's link to that child."""
    main.unlink_child(CHILD, None)
    _table, filters, _n = client.deletes[0]
    assert filters == {"parent_id": PARENT, "child_id": CHILD}


def test_a_link_that_is_not_yours_is_a_404_not_a_cheerful_ok(client):
    """"Done" and "never yours" are different facts about the caller's account."""
    with pytest.raises(main.HTTPException) as e:
        main.unlink_child("someone-elses-child", None)
    assert e.value.status_code == 404
    assert len(client.links) == 2


def test_unlinking_destroys_nothing_but_the_link(client):
    """Unlinking is neither erasure nor a withdrawal; the fake raises on any other table."""
    main.unlink_child(CHILD, None)
    assert [t for t, _f, _n in client.deletes] == ["parent_child_links"]


# ── the student is told ─────────────────────────────────────────────────────


@pytest.fixture
def student(monkeypatch, client):
    """The same fake, read as the child."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": CHILD})
    # `_profiles_many` reads a table this fake refuses; the name lookup isn't under test.
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
    """Unscoped, this is a platform-wide list of who is linked to whom."""
    main.my_unacknowledged_parent_links(None)
    _kind, filters, _n = student.reads[0]
    assert filters == {"child_id": CHILD, "student_ack_at": None}


def test_a_failed_read_says_so_rather_than_reporting_no_links(monkeypatch, student):
    """Fails open to an empty list, with `retrieved: false` saying it was not read."""
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
    """A client-supplied link id would let a caller suppress somebody else's notice."""
    main.ack_parent_links(None)
    _kind, filters, _n = student.reads[0]
    assert filters == {"child_id": CHILD, "student_ack_at": None}


def test_acknowledging_nothing_is_a_404(student):
    """A dismissal that did not land is not a success; same rule as `/api/consent/ack`."""
    for row in student.links:
        row["student_ack_at"] = "2026-08-14T09:00:00Z"
    with pytest.raises(main.HTTPException) as e:
        main.ack_parent_links(None)
    assert e.value.status_code == 404


# ── the parent is told ──────────────────────────────────────────────────────
# Read from append-only `consent_withdrawals`, not `*_revoked_at`, which a re-enable nulls.


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
    """The append-only event survives the restore; the fixture holds no consent row at all."""
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
    """Unscoped, this lists which children withdrew what, platform-wide."""
    main.parent_consent_notices(None)
    kind, filters, _n = notices.reads[0]
    assert kind == "select"
    assert filters == {"parent_id": PARENT}


def test_a_failed_read_says_so_rather_than_reporting_no_withdrawals(monkeypatch, notices):
    """Fails open, with `retrieved: false` saying it was not read."""
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
    """Stamping `now()` would mark seen a withdrawal landing between the read and the click."""
    notices.withdrawals.append(_w(CHILD, "camera", "2026-08-12T09:00:00Z"))
    shown = main.parent_consent_notices(None)["notices"][0]["through"]

    # Lands after the banner was drawn, before the click.
    notices.withdrawals.append(_w(CHILD, "eeg", "2026-08-13T09:00:00Z"))
    _ack({CHILD: shown})

    still = main.parent_consent_notices(None)["notices"]
    assert [c["channel"] for c in still[0]["channels"]] == ["eeg"]


def test_acknowledging_one_child_does_not_clear_another(notices):
    """`.eq("parent_id", ...)` alone would stamp every link the parent holds."""
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
    """`parent_ack_at` lives on the link row, so each parent acknowledges their own."""
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
    """A `child_id` the caller is not linked to matches no row."""
    _ack({CHILD: "2026-08-12T09:00:00Z"})
    kind, filters, _n = notices.reads[0]
    assert kind == "update"
    assert filters == {"parent_id": PARENT, "child_id": CHILD}


def test_acknowledging_a_child_that_is_not_yours_changes_nothing(notices):
    _ack({"someone-elses-child": "2026-08-12T09:00:00Z"})
    assert notices.links[0]["parent_ack_at"] == "2026-08-10T09:00:00Z"
