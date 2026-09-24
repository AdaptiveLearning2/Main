"""Linking a parent takes a code the child made, not a string about the child.

`POST /api/parent/link-child` took the child's **user id**, and the page told
the parent to have the child read it off their own profile -- so possession of
the id was standing in for the child's consent to the link. It cannot: the id is
on every roster payload a teacher of that child reads, in the URL of every
report page about them, and in the admin student search. Anyone holding one and
an account with `role = 'parent'` could link themselves, and from that moment
read the child's reports and re-enable a sensor the child had switched off.

**This does not reverse "notify, not block".** `ParentLinkedBanner` still tells
the child after the fact and nothing waits on the acknowledgement -- see that
component for why an approval gate was rejected. What the code changes is that
the handover is now an act by the child, which is what the id was being trusted
to prove and did not.

**A student still cannot remove a link; only the parent can.** That is a
safeguarding decision rather than an engineering one -- a child cutting off a
legitimate parent is the other failure -- and it is recorded here rather than
left to be rediscovered as an omission.
"""
import os
from datetime import timedelta

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402

PARENT = "parent-1"
CHILD = "child-1"
CODE = "ABCD2345"


class _Fake:
    """`parent_link_codes`, `parent_child_links` and `profiles`, plus a record
    of every write, so a test can assert on what was *asked* rather than only on
    the answer."""

    def __init__(self, codes=(), links=(), role="student", code_read_raises=None,
                 profile_raises=None, link_insert_raises=None, link_on_failure=False):
        self.codes = [dict(c) for c in codes]
        self.links = [dict(l) for l in links]
        self.role = role       # the *child's* profile: the one link_child reads
        self.code_read_raises = code_read_raises
        self.profile_raises = profile_raises
        self.link_insert_raises = link_insert_raises
        # The race a unique violation means: another request wrote the link.
        self.link_on_failure = link_on_failure
        self.upserts = []      # (table, row, kwargs)
        self.inserts = []      # (table, row)
        self.deletes = []      # (table, filters, gt)
        self.events = []       # every security_events insert
        self.ops = []          # (kind, table), in order, writes only

    def table(self, name):
        client, table = self, name

        class _Q:
            def __init__(self):
                self._filters = {}
                self._gt = {}
                self._write = None

            def select(self, *_a, **_k):   return self
            def order(self, *_a, **_k):    return self
            def limit(self, *_a, **_k):    return self
            def is_(self, *_a):            return self

            def eq(self, col, val):
                self._filters[col] = val
                return self

            def gt(self, col, val):
                self._gt[col] = val
                return self

            def _matches(self, row):
                return (all(row.get(k) == v for k, v in self._filters.items())
                        and all(main._parse_ts(row.get(k)) > main._parse_ts(v)
                                for k, v in self._gt.items()))

            def upsert(self, row, **kw):
                self._write = ("upsert", row, kw)
                return self

            def insert(self, row):
                self._write = ("insert", row, {})
                return self

            def update(self, row):
                self._write = ("update", row, {})
                return self

            def delete(self):
                self._write = ("delete", None, {})
                return self

            def execute(self):
                if self._write:
                    kind, row, kw = self._write
                    if table != "security_events":
                        client.ops.append((kind, table))
                    if kind == "upsert":
                        client.upserts.append((table, row, kw))
                        client.codes = [c for c in client.codes
                                        if c["student_id"] != row["student_id"]]
                        client.codes.append(dict(row))
                    elif kind == "insert":
                        if table == "parent_child_links" and client.link_insert_raises:
                            if client.link_on_failure:
                                client.links.append(dict(row))
                            raise client.link_insert_raises
                        client.inserts.append((table, row))
                        if table == "security_events":
                            client.events.append(row)
                        if table == "parent_child_links":
                            client.links.append(dict(row))
                        if table == "parent_link_codes":
                            client.codes.append(dict(row))
                    elif kind == "delete":
                        if client.code_read_raises:
                            raise client.code_read_raises
                        client.deletes.append((table, dict(self._filters), dict(self._gt)))
                        # PostgREST's default for a delete is to return the
                        # rows it removed, which is what makes it a claim.
                        gone = [c for c in client.codes if self._matches(c)]
                        client.codes = [c for c in client.codes if not self._matches(c)]
                        return type("R", (), {"data": gone})()
                    return type("R", (), {"data": [row] if row else []})()

                if table == "parent_link_codes":
                    if client.code_read_raises:
                        raise client.code_read_raises
                    rows = [c for c in client.codes if self._matches(c)]
                elif table == "parent_child_links":
                    rows = [l for l in client.links if self._matches(l)]
                elif table == "profiles":
                    if client.profile_raises:
                        raise client.profile_raises
                    rows = [{"id": self._filters.get("id"),
                             "display_name": "Ada", "role": client.role}]
                else:
                    rows = []
                return type("R", (), {"data": rows})()

        return _Q()


def _code(student_id=CHILD, code=CODE, minutes=10):
    return {"code": code, "student_id": student_id,
            "created_at": main._utc_now().isoformat(),
            "expires_at": (main._utc_now() + timedelta(minutes=minutes)).isoformat()}


@pytest.fixture
def parent(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": PARENT})
    monkeypatch.setattr(main, "_role", lambda _uid: "parent")


@pytest.fixture
def student(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": CHILD})
    monkeypatch.setattr(main, "_role", lambda _uid: "student")


# ── making one ───────────────────────────────────────────────────────────

def test_a_code_is_stored_against_the_caller_and_replaces_the_last(monkeypatch, student):
    fake = _Fake(codes=[_code(code="OLDCODE1")])
    monkeypatch.setattr(main, "supabase", fake)

    out = main.create_parent_link_code(None)

    table, row, kw = fake.upserts[0]
    assert table == "parent_link_codes"
    # The caller's own id, never anything from a request: this endpoint takes no
    # body at all, which is what makes the code's owner unforgeable.
    assert row["student_id"] == CHILD
    assert row["code"] == out["code"]
    # Upserted on the student, or generating a second code leaves both live and
    # the child has no way to know which one they read out.
    assert kw.get("on_conflict") == "student_id"
    assert [c["code"] for c in fake.codes] == [out["code"]]


def test_only_a_student_can_create_a_code(monkeypatch):
    """A teacher's code, handed over, would produce a parent linked to a
    teacher -- which `_verify_can_view_student` would honour. Refused at the
    source, and checked again on redemption, since a role can change inside a
    code's lifetime."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "teacher-1"})
    monkeypatch.setattr(main, "_role", lambda _uid: "teacher")
    monkeypatch.setattr(main, "supabase", _Fake())

    with pytest.raises(main.HTTPException) as e:
        main.create_parent_link_code(None)
    assert e.value.status_code == 403


def test_a_code_is_drawn_from_an_unambiguous_alphabet_and_does_not_repeat():
    """Read aloud by a child, so no O/0 or I/1; and from `secrets` rather than
    `random`, because this is the only credential between an account claiming
    to be a parent and a child's reports."""
    codes = {main._new_link_code() for _ in range(200)}

    assert len(codes) == 200, "two of two hundred codes collided"
    for code in codes:
        assert len(code) == main._LINK_CODE_LEN
        assert set(code) <= set(main._LINK_CODE_ALPHABET)
    assert not (set("O0I1") & set(main._LINK_CODE_ALPHABET))


def test_a_stored_code_expires(monkeypatch, student):
    fake = _Fake()
    monkeypatch.setattr(main, "supabase", fake)

    out = main.create_parent_link_code(None)

    lifetime = main._parse_ts(out["expires_at"]) - main._utc_now()
    assert timedelta(seconds=0) < lifetime <= timedelta(seconds=main._LINK_CODE_TTL_SEC)


def test_a_write_that_failed_is_not_reported_as_a_code(monkeypatch, student):
    """Returning a code that was never stored sends a child to read out eight
    characters that refuse."""
    class _Broken(_Fake):
        def table(self, name):
            raise RuntimeError("postgrest down")

    monkeypatch.setattr(main, "supabase", _Broken())

    with pytest.raises(main.HTTPException) as e:
        main.create_parent_link_code(None)
    assert e.value.status_code == 503


# ── reading it back ──────────────────────────────────────────────────────

def test_the_outstanding_code_is_readable_again(monkeypatch, student):
    """A child who navigated away would otherwise have to make another, and
    each new one invalidates the last."""
    monkeypatch.setattr(main, "supabase", _Fake(codes=[_code()]))

    out = main.my_parent_link_code(None)

    assert out["code"] == CODE
    assert out["retrieved"] is True
    # The expiry too, or the page cannot say when the code stops working.
    assert main._parse_ts(out["expires_at"]) > main._utc_now()


def test_an_expired_code_is_not_offered_back(monkeypatch, student):
    """The row survives until the nightly sweep and the redemption path refuses
    it, so reporting it here would be one surface disagreeing with the other."""
    monkeypatch.setattr(main, "supabase", _Fake(codes=[_code(minutes=-1)]))

    out = main.my_parent_link_code(None)

    assert out["code"] is None
    assert out["retrieved"] is True, "an expired code is not a failed read"


def test_a_failed_read_is_not_no_code(monkeypatch, student):
    """Three states. A page that collapsed them offers `create one` to an
    account that has one, replacing a code the child may have just read out."""
    monkeypatch.setattr(main, "supabase",
                        _Fake(code_read_raises=RuntimeError("down")))

    out = main.my_parent_link_code(None)

    assert out == {"code": None, "expires_at": None, "retrieved": False}


# ── redeeming it ─────────────────────────────────────────────────────────

def test_a_valid_code_links_the_parent_to_the_child_it_belongs_to(monkeypatch, parent):
    fake = _Fake(codes=[_code()])
    monkeypatch.setattr(main, "supabase", fake)

    out = main.link_child(main.LinkChildRequest(link_code=CODE), None)

    assert out["ok"] is True
    assert out["child_id"] == CHILD
    # The child comes off the code row. The request carries no child id at all
    # now, which is the point -- there is nothing for a caller to put there.
    assert ("parent_child_links", {"parent_id": PARENT, "child_id": CHILD}) in \
        [(t, r) for t, r in fake.inserts]


def test_a_redeemed_code_does_not_work_twice(monkeypatch, parent):
    fake = _Fake(codes=[_code()])
    monkeypatch.setattr(main, "supabase", fake)

    main.link_child(main.LinkChildRequest(link_code=CODE), None)
    assert fake.codes == [], "the spent code is still outstanding"

    # A second adult with the same code.
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "parent-2"})
    with pytest.raises(main.HTTPException) as e:
        main.link_child(main.LinkChildRequest(link_code=CODE), None)
    assert e.value.status_code == 404
    assert [l["parent_id"] for l in fake.links] == [PARENT]


def test_the_code_is_claimed_before_the_link_is_written(monkeypatch, parent):
    """**The delete is the claim.** Two requests carrying one code both read it
    as valid if the read is the check; only one gets rows back from a delete.
    So the delete comes first, is conditional on the code being unexpired, and
    nothing is linked unless it returned the row."""
    fake = _Fake(codes=[_code()])
    monkeypatch.setattr(main, "supabase", fake)

    main.link_child(main.LinkChildRequest(link_code=CODE), None)

    assert fake.ops[:2] == [("delete", "parent_link_codes"),
                            ("insert", "parent_child_links")], fake.ops
    table, filters, gt = fake.deletes[0]
    assert (table, filters) == ("parent_link_codes", {"code": CODE})
    # Conditional on the expiry, in the same statement -- or an expired code
    # is deleted and honoured.
    assert main._parse_ts(gt["expires_at"]) <= main._utc_now()


def test_a_claim_that_failed_links_nobody(monkeypatch, parent):
    """A delete that could not run has claimed nothing, so nothing is linked --
    and it is a 503, not "not valid": the code may be fine."""
    fake = _Fake(codes=[_code()], code_read_raises=RuntimeError("down"))
    monkeypatch.setattr(main, "supabase", fake)

    with pytest.raises(main.HTTPException) as e:
        main.link_child(main.LinkChildRequest(link_code=CODE), None)

    assert e.value.status_code == 503
    assert fake.links == []


def test_a_code_for_an_account_that_is_no_longer_a_student_links_nobody(monkeypatch, parent):
    """Checked when the code was made, and again here: the role can change
    inside the code's lifetime. Same refusal as any other code that does not
    work, recorded like one, and spent rather than given back."""
    fake = _Fake(codes=[_code()], role="teacher")
    monkeypatch.setattr(main, "supabase", fake)

    with pytest.raises(main.HTTPException) as e:
        main.link_child(main.LinkChildRequest(link_code=CODE), None)

    assert e.value.status_code == 404
    assert fake.links == []
    assert fake.codes == []
    assert [ev for ev in fake.events if ev["kind"] == "authz_denied"], fake.events


def test_a_child_profile_that_could_not_be_read_links_nobody(monkeypatch, parent):
    """Not through `_role`, which answers "student" for a failed read -- the
    permissive answer on this side. The code goes back: nothing is wrong with it."""
    fake = _Fake(codes=[_code()], profile_raises=RuntimeError("down"))
    monkeypatch.setattr(main, "supabase", fake)

    with pytest.raises(main.HTTPException) as e:
        main.link_child(main.LinkChildRequest(link_code=CODE), None)

    assert e.value.status_code == 503
    assert fake.links == []
    assert [c["code"] for c in fake.codes] == [CODE], "a good code was spent"


@pytest.mark.parametrize("linked_meanwhile,status", [
    (True,  409),   # the unique constraint: this parent's other request won
    (False, 503),
])
def test_a_link_that_was_not_written_is_answered_and_gives_the_code_back(
        monkeypatch, parent, linked_meanwhile, status):
    """A failed insert was an unhandled 500 that left the code spent. It is
    asked what happened rather than guessed from the error text."""
    fake = _Fake(codes=[_code()], link_insert_raises=RuntimeError("23505"),
                 link_on_failure=linked_meanwhile)
    monkeypatch.setattr(main, "supabase", fake)

    with pytest.raises(main.HTTPException) as e:
        main.link_child(main.LinkChildRequest(link_code=CODE), None)

    assert e.value.status_code == status
    assert [c["code"] for c in fake.codes] == [CODE]


def test_a_code_given_back_never_replaces_a_newer_one(monkeypatch, parent):
    """Inserted, not upserted on the student: if the child made a new code while
    this one was claimed, the new one is what they are reading out."""
    fake = _Fake(codes=[_code()], profile_raises=RuntimeError("down"))
    monkeypatch.setattr(main, "supabase", fake)

    with pytest.raises(main.HTTPException):
        main.link_child(main.LinkChildRequest(link_code=CODE), None)

    assert fake.upserts == []
    assert ("insert", "parent_link_codes") in fake.ops


def test_a_code_is_accepted_however_it_was_typed(monkeypatch, parent):
    """The alphabet has no lowercase in it, so a typed `a` is the same code as
    `A`; refusing it would be a puzzle rather than a safeguard."""
    fake = _Fake(codes=[_code()])
    monkeypatch.setattr(main, "supabase", fake)

    out = main.link_child(main.LinkChildRequest(link_code=f"  {CODE.lower()} "), None)

    assert out["child_id"] == CHILD


@pytest.mark.parametrize("codes,label", [
    ([], "a code that does not exist"),
    ([_code(minutes=-1)], "a code that has expired"),
])
def test_a_code_that_does_not_work_is_refused_the_same_way(monkeypatch, parent,
                                                           codes, label):
    """One message for both, because the difference is information about
    somebody else's account -- and the parent's next action is the same either
    way: ask the child for a new one."""
    monkeypatch.setattr(main, "supabase", _Fake(codes=codes))

    with pytest.raises(main.HTTPException) as e:
        main.link_child(main.LinkChildRequest(link_code=CODE), None)

    assert e.value.status_code == 404, label
    assert "not valid or has expired" in e.value.detail


def test_a_refused_code_is_recorded(monkeypatch, parent):
    """A series of these from one account is the only shape guessing has, and
    `authz_denied` is deliberately not cooled, so every attempt is a row."""
    fake = _Fake(codes=[])
    monkeypatch.setattr(main, "supabase", fake)

    with pytest.raises(main.HTTPException):
        main.link_child(main.LinkChildRequest(link_code=CODE), None)

    assert [e for e in fake.events
            if e["kind"] == "authz_denied"
            and (e.get("detail") or {}).get("check") == "parent_link_code"], \
        fake.events
    # The actor, and no subject: which child's code was being guessed at is not
    # known on this path, and inventing one would put a child's id on a row
    # about somebody else's typing.
    event = fake.events[0]
    assert event["actor_user_id"] == PARENT
    assert event.get("subject_user_id") is None


def test_a_failed_lookup_is_not_reported_as_an_invalid_code(monkeypatch, parent):
    """Telling a parent the code is wrong sends them to ask for another one,
    which will be refused the same way."""
    monkeypatch.setattr(main, "supabase",
                        _Fake(code_read_raises=RuntimeError("down")))

    with pytest.raises(main.HTTPException) as e:
        main.link_child(main.LinkChildRequest(link_code=CODE), None)

    assert e.value.status_code == 503


def test_an_existing_link_is_not_duplicated(monkeypatch, parent):
    fake = _Fake(codes=[_code()],
                 links=[{"id": "l1", "parent_id": PARENT, "child_id": CHILD}])
    monkeypatch.setattr(main, "supabase", fake)

    with pytest.raises(main.HTTPException) as e:
        main.link_child(main.LinkChildRequest(link_code=CODE), None)
    assert e.value.status_code == 409
    # And the code is not spent by a link that was not made.
    assert fake.codes, "a refused duplicate consumed the code"


def test_attempts_are_bounded_and_the_limiter_is_named(monkeypatch, parent):
    """Not what makes guessing infeasible -- the alphabet and the TTL are --
    but what makes trying visible and slow."""
    fake = _Fake(codes=[])
    monkeypatch.setattr(main, "supabase", fake)
    from tests.conftest import tighten
    tighten(monkeypatch, main._LINK_CODE_LIMITER, limit=2, window=3600.0)

    for _ in range(2):
        with pytest.raises(main.HTTPException) as e:
            main.link_child(main.LinkChildRequest(link_code=CODE), None)
        assert e.value.status_code == 404

    with pytest.raises(main.HTTPException) as e:
        main.link_child(main.LinkChildRequest(link_code=CODE), None)
    assert e.value.status_code == 429
    assert e.value.headers.get("Retry-After")
    assert [ev for ev in fake.events
            if ev["kind"] == "rate_limited"
            and (ev.get("detail") or {}).get("limiter")
            == main._LINK_CODE_LIMITER.name], fake.events
