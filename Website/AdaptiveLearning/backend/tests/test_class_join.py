"""Joining a class: students only, a bounded number of guesses, and nothing of the teacher's returned."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402

CLASS = {"id": "class-1", "name": "Algebra", "grade_level": "7", "teacher_id": "teacher-1",
         "join_code": "ABCD2345"}


class _Db:
    """`classes` and `class_memberships`, recording each select's columns and every insert."""

    def __init__(self):
        self.tables = {"classes": [dict(CLASS)], "class_memberships": []}
        self.selects, self.inserts = [], []

    def table(self, name):
        db = self

        class _Q:
            def __init__(self):
                self.filters, self.row, self.changes, self.one = {}, None, None, False

            def select(self, cols):
                db.selects.append((name, cols))
                self.cols = [c.strip() for c in cols.split(",")]
                return self

            def eq(self, col, val):
                self.filters[col] = val
                return self

            def in_(self, col, vals):
                self.filters[col] = ("in", list(vals))
                return self

            def single(self):
                self.one = True
                return self

            def insert(self, row):
                self.row = row
                return self

            def update(self, changes):
                self.changes = changes
                return self

            def _match(self, r):
                return all(r.get(k) in v[1] if isinstance(v, tuple) else r.get(k) == v
                           for k, v in self.filters.items())

            def execute(self):
                if self.row is not None:
                    db.inserts.append((name, self.row))
                    db.tables[name].append(self.row)
                    return type("R", (), {"data": [self.row]})()
                if self.changes is not None:
                    for r in db.tables[name]:
                        if self._match(r):
                            r.update(self.changes)
                    return type("R", (), {"data": []})()
                hit = [{c: r.get(c) for c in self.cols} for r in db.tables[name] if self._match(r)]
                return type("R", (), {"data": (hit[0] if hit else None) if self.one else hit})()
        return _Q()


@pytest.fixture
def db(monkeypatch):
    fake = _Db()
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "student-1"})
    monkeypatch.setattr(main, "_role", lambda uid: "student" if uid.startswith("student") else "parent")
    monkeypatch.setattr(main, "_record_security_event", lambda *a, **k: None)
    return fake


def _from(host):
    """A request from `host`, for the per-address budget; the caller comes from `get_user`."""
    return type("Req", (), {"headers": {}, "client": type("Peer", (), {"host": host})()})()


def _join(code="ABCD2345", host="10.0.0.1"):
    return main.join_class(main.JoinClassRequest(join_code=code), _from(host))


def test_a_student_joins_and_learns_nothing_of_the_teachers(db):
    out = _join("abcd2345 ")
    assert out == {"id": "class-1", "name": "Algebra", "grade_level": "7"}
    # Asked for by name, so a column added later is not sent either.
    assert ("classes", "id, name, grade_level") in db.selects
    assert db.inserts == [("class_memberships", {"class_id": "class-1", "student_id": "student-1"})]


def test_a_code_issued_before_the_longer_codes_still_joins(db):
    db.tables["classes"][0]["join_code"] = "ABC123"
    assert _join("ABC123")["id"] == "class-1"


def test_a_parent_or_teacher_cannot_join_as_a_student(db, monkeypatch):
    """They would appear on the teacher's roster as a student."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "parent-1"})
    with pytest.raises(main.HTTPException) as e:
        _join()
    assert e.value.status_code == 403
    assert db.inserts == []


def test_guesses_past_the_limit_are_refused_before_any_lookup(db, monkeypatch):
    from conftest import tighten
    tighten(monkeypatch, main._JOIN_CODE_LIMITER, limit=2)
    recorded = []
    monkeypatch.setattr(main, "_record_security_event", lambda kind, actor, **d: recorded.append((kind, d)))
    for guess in ("AAAA2222", "BBBB3333"):
        with pytest.raises(main.HTTPException):
            _join(guess)                                   # 404s, but they count
    lookups = len(db.selects)
    with pytest.raises(main.HTTPException) as e:
        _join()
    assert e.value.status_code == 429 and int(e.value.headers["Retry-After"]) >= 1
    assert len(db.selects) == lookups, "a refused guess still reached the classes table"
    assert recorded == [("rate_limited", {"limiter": main._JOIN_CODE_LIMITER.name})]


def test_wrong_codes_from_one_network_are_bounded_across_accounts(db, monkeypatch):
    """Accounts are self-service, so the per-account limit alone is ten guesses per new account."""
    from conftest import tighten
    tighten(monkeypatch, main._JOIN_MISS_LIMITER, limit=2)
    recorded = []
    monkeypatch.setattr(main, "_record_security_event", lambda kind, actor, **d: recorded.append((kind, d)))
    for i, guess in enumerate(("AAAA2222", "BBBB3333")):
        monkeypatch.setattr(main, "get_user", lambda _r, i=i: {"id": f"student-{i + 10}"})
        with pytest.raises(main.HTTPException):
            _join(guess)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "student-99"})
    with pytest.raises(main.HTTPException) as e:
        _join()
    assert e.value.status_code == 429 and int(e.value.headers["Retry-After"]) >= 1
    assert recorded == [("rate_limited", {"limiter": main._JOIN_MISS_LIMITER.name})]
    assert _join(host="10.0.0.2")["id"] == "class-1", "another network is unaffected"


def test_right_codes_never_use_up_the_networks_budget(db, monkeypatch):
    """A school's first morning is hundreds of correct joins from one address."""
    from conftest import tighten
    tighten(monkeypatch, main._JOIN_MISS_LIMITER, limit=1)
    for i in range(5):
        monkeypatch.setattr(main, "get_user", lambda _r, i=i: {"id": f"student-{i + 20}"})
        assert _join()["id"] == "class-1"
    with pytest.raises(main.HTTPException) as e:
        _join("AAAA2222")
    assert e.value.status_code == 404, "the one allowed miss was taken by right codes"


def test_a_students_class_list_sends_what_joining_did_and_nothing_of_the_teachers(db):
    db.tables["class_memberships"].append({"class_id": "class-1", "student_id": "student-1"})
    assert main.my_classes(None) == [{"id": "class-1", "name": "Algebra", "grade_level": "7"}]
    assert ("classes", "id, name, grade_level") in db.selects


def test_the_teacher_replaces_an_old_code_and_the_class_keeps_its_members(db, monkeypatch):
    db.tables["classes"][0]["join_code"] = "ABC123"
    db.tables["class_memberships"].append({"class_id": "class-1", "student_id": "student-9"})
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "teacher-1"})
    out = main.replace_join_code("class-1", None)
    assert len(out["join_code"]) == 8 and db.tables["classes"][0]["join_code"] == out["join_code"]
    assert db.tables["class_memberships"] == [{"class_id": "class-1", "student_id": "student-9"}]
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "student-1"})
    with pytest.raises(main.HTTPException) as e:
        _join("ABC123")
    assert e.value.status_code == 404, "the old code still joins"


def test_only_the_owning_teacher_replaces_a_code(db, monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "teacher-2"})
    with pytest.raises(main.HTTPException) as e:
        main.replace_join_code("class-1", None)
    assert e.value.status_code == 403
    assert db.tables["classes"][0]["join_code"] == "ABCD2345"


def test_a_new_class_gets_a_long_code_from_the_unambiguous_alphabet(db, monkeypatch):
    monkeypatch.setattr(main, "_role", lambda _uid: "teacher")
    codes = {main._new_join_code() for _ in range(200)}
    assert all(len(c) == 8 and set(c) <= set(main._LINK_CODE_ALPHABET) for c in codes)
    assert len(codes) == 200
