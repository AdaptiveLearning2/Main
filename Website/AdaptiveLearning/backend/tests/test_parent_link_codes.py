"""A parent links with a code the child made; a user id is not a secret."""
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
    """Codes, links and profiles, recording every write so tests assert on the request."""

    def __init__(self, codes=(), links=(), role="student", code_read_raises=None,
                 profile_raises=None, link_insert_raises=None, link_on_failure=False,
                 link_read_raises_after_insert=False, link_read_raises=False):
        self.codes = [dict(c) for c in codes]
        self.links = [dict(l) for l in links]
        self.role = role       # the *child's* profile: the one link_child reads
        self.code_read_raises = code_read_raises
        self.profile_raises = profile_raises
        self.link_insert_raises = link_insert_raises
        # The race a unique violation means: another request wrote the link.
        self.link_on_failure = link_on_failure
        # Whether the link exists cannot be found out after the write failed.
        self.link_read_raises_after_insert = link_read_raises_after_insert
        self.link_read_raises = link_read_raises
        self.insert_attempted = False
        self.delete_kwargs = []
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

            def delete(self, **kw):
                self._write = ("delete", None, kw)
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
                        if table == "parent_child_links":
                            client.insert_attempted = True
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
                        client.delete_kwargs.append(kw)
                        # A delete returns the rows it removed, which is what makes it a claim.
                        gone = [c for c in client.codes if self._matches(c)]
                        client.codes = [c for c in client.codes if not self._matches(c)]
                        return type("R", (), {"data": gone})()
                    return type("R", (), {"data": [row] if row else []})()

                if table == "parent_link_codes":
                    if client.code_read_raises:
                        raise client.code_read_raises
                    rows = [c for c in client.codes if self._matches(c)]
                elif table == "parent_child_links":
                    if client.link_read_raises or (
                            client.link_read_raises_after_insert and client.insert_attempted):
                        raise RuntimeError("links unreadable")
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
    # The caller's own id: the endpoint takes no body, so the owner is unforgeable.
    assert row["student_id"] == CHILD
    assert row["code"] == out["code"]
    # Upserted on the student, or a second code leaves both live.
    assert kw.get("on_conflict") == "student_id"
    assert [c["code"] for c in fake.codes] == [out["code"]]


def test_only_a_student_can_create_a_code(monkeypatch):
    """A teacher's code would link a parent to a teacher, which `_verify_can_view_student` honours."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "teacher-1"})
    monkeypatch.setattr(main, "_role", lambda _uid: "teacher")
    monkeypatch.setattr(main, "supabase", _Fake())

    with pytest.raises(main.HTTPException) as e:
        main.create_parent_link_code(None)
    assert e.value.status_code == 403


def test_a_code_is_drawn_from_an_unambiguous_alphabet_and_does_not_repeat():
    """Read aloud by a child, so no O/0 or I/1; drawn from `secrets`, not `random`."""
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
    class _Broken(_Fake):
        def table(self, name):
            raise RuntimeError("postgrest down")

    monkeypatch.setattr(main, "supabase", _Broken())

    with pytest.raises(main.HTTPException) as e:
        main.create_parent_link_code(None)
    assert e.value.status_code == 503


# ── reading it back ──────────────────────────────────────────────────────

def test_the_outstanding_code_is_readable_again(monkeypatch, student):
    """Otherwise a child who navigated away makes another, invalidating the last."""
    monkeypatch.setattr(main, "supabase", _Fake(codes=[_code()]))

    out = main.my_parent_link_code(None)

    assert out["code"] == CODE
    assert out["retrieved"] is True
    assert main._parse_ts(out["expires_at"]) > main._utc_now()


def test_an_expired_code_is_not_offered_back(monkeypatch, student):
    """The row survives until the nightly sweep, but redemption already refuses it."""
    monkeypatch.setattr(main, "supabase", _Fake(codes=[_code(minutes=-1)]))

    out = main.my_parent_link_code(None)

    assert out["code"] is None
    assert out["retrieved"] is True, "an expired code is not a failed read"


def test_a_failed_read_is_not_no_code(monkeypatch, student):
    """Collapsed, the page offers `create one` and replaces a code the child may have read out."""
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
    # The child comes off the code row; the request carries no child id.
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
    """The conditional delete is the claim: only one racing request gets the row back."""
    fake = _Fake(codes=[_code()])
    monkeypatch.setattr(main, "supabase", fake)

    main.link_child(main.LinkChildRequest(link_code=CODE), None)

    assert fake.ops[:2] == [("delete", "parent_link_codes"),
                            ("insert", "parent_child_links")], fake.ops
    table, filters, gt = fake.deletes[0]
    assert (table, filters) == ("parent_link_codes", {"code": CODE})
    # Conditional on the expiry in the same statement, or an expired code is honoured.
    assert main._parse_ts(gt["expires_at"]) <= main._utc_now()
    # Under `returning=minimal` the answer is always empty and every good code reads as unknown.
    assert fake.delete_kwargs[0].get("returning") == main.ReturnMethod.representation


def test_a_claim_that_failed_links_nobody(monkeypatch, parent):
    """503, not "not valid": the code may be fine."""
    fake = _Fake(codes=[_code()], code_read_raises=RuntimeError("down"))
    monkeypatch.setattr(main, "supabase", fake)

    with pytest.raises(main.HTTPException) as e:
        main.link_child(main.LinkChildRequest(link_code=CODE), None)

    assert e.value.status_code == 503
    assert fake.links == []


def test_a_code_for_an_account_that_is_no_longer_a_student_links_nobody(monkeypatch, parent):
    """The role can change inside the code's lifetime; refused like any bad code, and spent."""
    fake = _Fake(codes=[_code()], role="teacher")
    monkeypatch.setattr(main, "supabase", fake)

    with pytest.raises(main.HTTPException) as e:
        main.link_child(main.LinkChildRequest(link_code=CODE), None)

    assert e.value.status_code == 404
    assert fake.links == []
    assert fake.codes == []
    # Recorded under its own check, so the log doesn't count a genuine code as guessing.
    checks = [(ev.get("detail") or {}).get("check") for ev in fake.events
              if ev["kind"] == "authz_denied"]
    assert checks == ["parent_link_code_not_student"], fake.events


def test_a_child_profile_that_could_not_be_read_links_nobody(monkeypatch, parent):
    """Not via `_role`, which answers "student" on a failed read. The code goes back."""
    fake = _Fake(codes=[_code()], profile_raises=RuntimeError("down"))
    monkeypatch.setattr(main, "supabase", fake)

    with pytest.raises(main.HTTPException) as e:
        main.link_child(main.LinkChildRequest(link_code=CODE), None)

    assert e.value.status_code == 503
    assert fake.links == []
    assert [c["code"] for c in fake.codes] == [CODE], "a good code was spent"


@pytest.mark.parametrize("linked_meanwhile,unreadable", [
    (False, False),   # no link to be seen now -- but the write may yet commit
    (False, True),    # nobody can say
    (True,  False),   # the unique constraint, or a write whose answer was lost
])
def test_a_link_write_that_raised_never_gives_the_code_back(
        monkeypatch, parent, linked_meanwhile, unreadable):
    """A write that raised may still commit, so a returned code could become a second adult's link."""
    fake = _Fake(codes=[_code()], link_insert_raises=RuntimeError("timeout"),
                 link_on_failure=linked_meanwhile,
                 link_read_raises_after_insert=unreadable)
    monkeypatch.setattr(main, "supabase", fake)

    try:
        main.link_child(main.LinkChildRequest(link_code=CODE), None)
    except main.HTTPException:
        pass

    assert fake.codes == [], "a code whose link may have been written went back"
    assert ("insert", "parent_link_codes") not in fake.ops


@pytest.mark.parametrize("linked_meanwhile,unreadable,linked", [
    (True,  False, True),
    (False, False, False),
    (False, True,  False),
])
def test_a_link_write_that_raised_is_reported_by_whether_the_link_exists(
        monkeypatch, parent, linked_meanwhile, unreadable, linked):
    """Made with the answer lost is a success; not made, or unknown, is a 503."""
    fake = _Fake(codes=[_code()], link_insert_raises=RuntimeError("timeout"),
                 link_on_failure=linked_meanwhile,
                 link_read_raises_after_insert=unreadable)
    monkeypatch.setattr(main, "supabase", fake)

    if linked:
        out = main.link_child(main.LinkChildRequest(link_code=CODE), None)
        assert out["ok"] is True and out["child_id"] == CHILD
    else:
        with pytest.raises(main.HTTPException) as e:
            main.link_child(main.LinkChildRequest(link_code=CODE), None)
        assert e.value.status_code == 503
        assert "new code" in e.value.detail


def test_a_failed_check_for_an_existing_link_gives_the_code_back(monkeypatch, parent):
    """Nothing is written yet, so the code is safe to return."""
    fake = _Fake(codes=[_code()], link_read_raises=True)
    monkeypatch.setattr(main, "supabase", fake)

    with pytest.raises(main.HTTPException) as e:
        main.link_child(main.LinkChildRequest(link_code=CODE), None)

    assert e.value.status_code == 503
    assert not fake.insert_attempted, "linked after a check that could not answer"
    assert [c["code"] for c in fake.codes] == [CODE], "a good code was spent"


def test_a_code_given_back_never_replaces_a_newer_one(monkeypatch, parent):
    """Inserted, not upserted: a code the child made meanwhile is the one they're reading out."""
    fake = _Fake(codes=[_code()], profile_raises=RuntimeError("down"))
    monkeypatch.setattr(main, "supabase", fake)

    with pytest.raises(main.HTTPException):
        main.link_child(main.LinkChildRequest(link_code=CODE), None)

    assert fake.upserts == []
    assert ("insert", "parent_link_codes") in fake.ops


def test_a_code_is_accepted_however_it_was_typed(monkeypatch, parent):
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
    """The difference is information about somebody else's account."""
    monkeypatch.setattr(main, "supabase", _Fake(codes=codes))

    with pytest.raises(main.HTTPException) as e:
        main.link_child(main.LinkChildRequest(link_code=CODE), None)

    assert e.value.status_code == 404, label
    assert "not valid or has expired" in e.value.detail


def test_a_refused_code_is_recorded(monkeypatch, parent):
    """`authz_denied` is not cooled, so every guess is a row."""
    fake = _Fake(codes=[])
    monkeypatch.setattr(main, "supabase", fake)

    with pytest.raises(main.HTTPException):
        main.link_child(main.LinkChildRequest(link_code=CODE), None)

    assert [e for e in fake.events
            if e["kind"] == "authz_denied"
            and (e.get("detail") or {}).get("check") == "parent_link_code"], \
        fake.events
    # No subject: whose code was being guessed at is unknown on this path.
    event = fake.events[0]
    assert event["actor_user_id"] == PARENT
    assert event.get("subject_user_id") is None


def test_a_failed_lookup_is_not_reported_as_an_invalid_code(monkeypatch, parent):
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
    assert fake.codes, "a refused duplicate consumed the code"


def test_attempts_are_bounded_and_the_limiter_is_named(monkeypatch, parent):
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
