"""Role gates read `profiles.role`, never `user_metadata.role`.

`user_metadata` is written by the client at sign-up and can be rewritten at any
time with `supabase.auth.updateUser({data: {role: 'teacher'}})`, which talks to
GoTrue directly and never passes through this process. Three endpoints gated on
it, so any student could self-elevate and create classes.

Switching to `profiles.role` is only half the fix, and the half that is easy to
mistake for the whole one: `profiles` carries a `FOR ALL` own-row policy and
`authenticated` holds UPDATE, so that column was equally client-writable until
`20260824010000` revoked UPDATE/INSERT on it. Both halves are asserted here --
the code reads the right column, and the migration takes the write away.
"""

import io
import os
import pathlib
import re

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402

UID = "user-1"


def _claiming(role):
    """A token whose `user_metadata` claims a role. What an attacker controls."""
    return {"id": UID, "user_metadata": {"role": role}}


class _Profiles:
    """Just enough PostgREST to answer `_profile`, plus the class tables."""

    def __init__(self, role="student", raises=False):
        self.role = role
        self.raises = raises
        self.inserted = []

    def table(self, name):
        client, table = self, name

        class _Q:
            def select(self, *_a):  return self
            def eq(self, *_a):      return self
            def in_(self, *_a):     return self
            def order(self, *_a, **_k): return self
            def limit(self, *_a):   return self
            def single(self):
                self._single = True
                return self

            def insert(self, row):
                client.inserted.append((table, row))
                self._insert = row
                return self

            def execute(self):
                if table == "profiles":
                    if client.raises:
                        raise RuntimeError("profiles unavailable")
                    row = {"id": UID, "display_name": "Sam", "email": "s@x.com",
                           "role": client.role}
                    return type("R", (), {
                        "data": row if getattr(self, "_single", False) else [row]})()
                if getattr(self, "_insert", None) is not None:
                    return type("R", (), {"data": [{"id": "class-1",
                                                    **self._insert}]})()
                return type("R", (), {"data": []})()

        return _Q()


# ── the escalation itself ───────────────────────────────────────────────────

def test_claiming_teacher_in_user_metadata_does_not_let_a_student_create_a_class(
        monkeypatch):
    """The vulnerability, stated directly. `user_metadata` says teacher and
    `profiles` says student; the endpoint must believe `profiles`."""
    monkeypatch.setattr(main, "supabase", _Profiles(role="student"))
    monkeypatch.setattr(main, "get_user", lambda _r: _claiming("teacher"))

    with pytest.raises(main.HTTPException) as e:
        main.create_class(main.CreateClassRequest(name="Maths"), None)
    assert e.value.status_code == 403


def test_claiming_parent_in_user_metadata_does_not_let_a_student_link_a_child(
        monkeypatch):
    monkeypatch.setattr(main, "supabase", _Profiles(role="student"))
    monkeypatch.setattr(main, "get_user", lambda _r: _claiming("parent"))

    with pytest.raises(main.HTTPException) as e:
        main.link_child(main.LinkChildRequest(child_id="child-1"), None)
    assert e.value.status_code == 403


def test_my_classes_reads_the_profile_not_the_claim(monkeypatch):
    """Not a privilege boundary on its own -- a self-elevated student would get
    an empty list -- but it must agree with the other two about what a role is,
    or the app has two answers to one question."""
    monkeypatch.setattr(main, "supabase", _Profiles(role="student"))
    monkeypatch.setattr(main, "get_user", lambda _r: _claiming("teacher"))
    seen = []
    monkeypatch.setattr(main, "_role", lambda uid: seen.append(uid) or "student")

    main.my_classes(None)
    assert seen == [UID]


# ── the gate still admits the people it should ──────────────────────────────

def test_a_real_teacher_may_still_create_a_class(monkeypatch):
    """The fix has to not be "refuse everyone", which every test above would
    also pass against."""
    monkeypatch.setattr(main, "supabase", _Profiles(role="teacher"))
    # Claiming nothing at all: the profile is the whole basis for admitting them.
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": UID})

    out = main.create_class(main.CreateClassRequest(name="Maths"), None)
    assert out["teacher_id"] == UID


def test_a_real_parent_reaches_the_child_lookup(monkeypatch):
    """Past the role gate, so it fails on the child instead of on the role."""
    monkeypatch.setattr(main, "supabase", _Profiles(role="parent"))
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": UID})

    with pytest.raises(main.HTTPException) as e:
        main.link_child(main.LinkChildRequest(child_id="child-1"), None)
    assert e.value.status_code == 404


# ── failing closed ──────────────────────────────────────────────────────────

def test_an_unreadable_profile_denies_rather_than_admitting(monkeypatch):
    """`_profile` degrades to a student-shaped dict on a failed read, which is
    the safe direction here: a database blip must not be a way past a role
    check."""
    monkeypatch.setattr(main, "supabase", _Profiles(raises=True))
    monkeypatch.setattr(main, "get_user", lambda _r: _claiming("teacher"))

    assert main._role(UID) == "student"
    with pytest.raises(main.HTTPException) as e:
        main.create_class(main.CreateClassRequest(name="Maths"), None)
    assert e.value.status_code == 403


# ── the other half: the column is not client-writable ───────────────────────

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[4] / "supabase" / "migrations"


def _migration_sql() -> str:
    return "\n".join(
        io.open(p, encoding="utf-8").read() for p in sorted(_MIGRATIONS.glob("*.sql")))


@pytest.mark.parametrize("command", ["UPDATE", "INSERT"])
@pytest.mark.parametrize("grantee", ["anon", "authenticated"])
def test_the_role_column_write_is_revoked_from_the_client_roles(command, grantee):
    """Reading `profiles.role` is only a fix while the client cannot write it.
    `profiles` has a FOR ALL own-row policy and `authenticated` holds UPDATE, so
    without these revokes the gate reads a column the caller can set."""
    sql = _migration_sql()
    pattern = re.compile(
        rf'REVOKE\s+{command}\s*\(\s*"?role"?\s*\)\s+ON\s+(?:TABLE\s+)?'
        rf'(?:"?public"?\s*\.\s*)?"?profiles"?\s+FROM\s+"?{grantee}"?',
        re.IGNORECASE)
    assert pattern.search(sql), (
        f"no REVOKE {command} (role) ON profiles FROM {grantee} in any migration "
        "-- the endpoints gate on this column, so a client that can write it can "
        "still self-elevate")


def test_no_endpoint_gates_on_user_metadata(monkeypatch):
    """The three call sites were found by hand. A fourth would be found by this.

    `user_metadata` is legitimately *written* on profile update, to keep the
    display name in sync, so this looks for reads of `role` specifically rather
    than for the field.
    """
    import inspect
    source = inspect.getsource(main)
    hits = re.findall(r'user_metadata.{0,40}?["\']role["\']', source)
    assert not hits, (
        f"a role gate reads user_metadata, which the client can rewrite: {hits}")
