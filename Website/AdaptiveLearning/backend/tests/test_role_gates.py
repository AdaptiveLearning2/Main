"""Role gates read `profiles.role`, never `user_metadata.role`.

`user_metadata` is set by the client at sign-up and can be rewritten anytime
with `supabase.auth.updateUser({data: {role: 'teacher'}})`, which talks to
GoTrue directly and bypasses this backend entirely. Three endpoints used to
gate on it, letting any student self-elevate and create classes.

Switching to `profiles.role` is only half the fix. `profiles` had a `FOR ALL`
own-row policy plus UPDATE for `authenticated`, so that column was just as
writable by the client until `20260824010000` revoked UPDATE/INSERT on it.
Both halves are tested here: the code reads the right column, and the
migration blocks writing it.
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
    """The vulnerability, stated directly: `user_metadata` says teacher and
    `profiles` says student, and the endpoint must believe `profiles`."""
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
    """Not a privilege boundary by itself -- a self-elevated student would
    just get an empty list -- but it must agree with the other gates about
    what the role is, or the app answers the same question two ways."""
    monkeypatch.setattr(main, "supabase", _Profiles(role="student"))
    monkeypatch.setattr(main, "get_user", lambda _r: _claiming("teacher"))
    seen = []
    monkeypatch.setattr(main, "_role", lambda uid: seen.append(uid) or "student")

    main.my_classes(None)
    assert seen == [UID]


# ── the gate still admits the people it should ──────────────────────────────

def test_a_real_teacher_may_still_create_a_class(monkeypatch):
    """The fix must not be "refuse everyone" -- every test above would also
    pass against that."""
    monkeypatch.setattr(main, "supabase", _Profiles(role="teacher"))
    # Claims nothing: the profile alone is the basis for admitting them.
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
    """`_profile` falls back to a student-shaped dict on a failed read --
    the safe direction, since a database blip must not be a way past a role
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
    """Reading `profiles.role` only works as a fix if the client can't write
    it. `profiles` has a FOR ALL own-row policy and `authenticated` holds
    UPDATE, so without these revokes the gate would read a column the caller
    controls."""
    sql = _migration_sql()
    pattern = re.compile(
        rf'REVOKE\s+{command}\s*\(\s*"?role"?\s*\)\s+ON\s+(?:TABLE\s+)?'
        rf'(?:"?public"?\s*\.\s*)?"?profiles"?\s+FROM\s+"?{grantee}"?',
        re.IGNORECASE)
    assert pattern.search(sql), (
        f"no REVOKE {command} (role) ON profiles FROM {grantee} in any migration "
        "-- the endpoints gate on this column, so a client that can write it can "
        "still self-elevate")


def test_signup_cannot_choose_the_admin_role():
    """Widening the CHECK to admit 'admin' is only safe because the sign-up
    trigger no longer allows that value through. `handle_new_user` copies
    `raw_user_meta_data->>'role'` straight into the column, and that metadata
    is whatever the registration form sent -- so without a whitelist,
    `signUp({data:{role:'admin'}})` from a browser console would make an
    administrator.

    Checked as a whitelist rather than "'admin' is absent", because a
    blacklist would admit every future privileged role by default.
    """
    sql = _migration_sql()
    # The newest definition of the function wins, so read the last one.
    bodies = re.findall(
        r'CREATE OR REPLACE FUNCTION\s+"?public"?\.\s*"?handle_new_user"?.*?\$\$(.*?)\$\$',
        sql, re.IGNORECASE | re.DOTALL)
    assert bodies, "handle_new_user is not defined in any migration"
    body = bodies[-1]

    assert "raw_user_meta_data" in body, "this test is looking at the wrong function"
    listed = set(re.findall(r"'(student|teacher|parent|admin)'", body))
    assert listed == {"student", "teacher", "parent"}, (
        f"the sign-up trigger's role whitelist is {sorted(listed)} -- it must "
        "name exactly the three roles a person chooses for themselves, and "
        "must not admit 'admin'")


def test_the_signup_trigger_is_created_by_a_migration():
    """`handle_new_user` was written for a trigger that no migration ever
    created. That was harmless while `profiles` was unused, but `_role` now
    gates three endpoints on it -- a profiles row that never gets written
    means a teacher who cannot create a class."""
    sql = _migration_sql()
    assert re.search(
        r'CREATE\s+TRIGGER\s+"?on_auth_user_created"?\s+AFTER\s+INSERT\s+ON\s+'
        r'"?auth"?\s*\.\s*"?users"?', sql, re.IGNORECASE), (
        "no migration creates the auth.users trigger, so profiles rows depend "
        "on something hand-made in the dashboard")


def test_the_backfill_applies_the_same_role_whitelist_as_the_trigger():
    """The backfill reads the same client-supplied metadata as the trigger, so
    trusting it without a whitelist would be exactly the escalation this is
    meant to prevent -- one INSERT granting whatever anyone typed at sign-up."""
    sql = _migration_sql()
    inserts = re.findall(
        r'INSERT INTO\s+"?public"?\.\s*"?profiles"?(.*?);', sql,
        re.IGNORECASE | re.DOTALL)
    # `NOT EXISTS` is the backfill's signature. Without it this would also
    # match the trigger's own INSERT, including old definitions that predate
    # the whitelist -- which would fail for historical reasons rather than a
    # real live hole. The trigger has its own test above.
    backfills = [i for i in inserts
                 if "raw_user_meta_data" in i and re.search(r"NOT\s+EXISTS", i, re.I)]
    assert backfills, "no profiles backfill found"
    for body in backfills:
        listed = set(re.findall(r"'(student|teacher|parent|admin)'", body))
        assert listed == {"student", "teacher", "parent"}, (
            f"a profiles backfill's role whitelist is {sorted(listed)} -- it "
            "must match the trigger's and must not admit 'admin'")


def test_the_role_check_constraint_admits_admin():
    """The other half. Without it, promoting someone in the SQL editor fails."""
    sql = _migration_sql()
    checks = re.findall(r'CONSTRAINT\s+"?profiles_role_check"?\s+CHECK\s*\((.*?)\)\s*;',
                        sql, re.IGNORECASE | re.DOTALL)
    assert checks, "no profiles_role_check found"
    assert "'admin'" in checks[-1].replace('"', ""), (
        "the newest profiles_role_check does not admit 'admin'")


def test_no_endpoint_gates_on_user_metadata(monkeypatch):
    """The three fixed call sites were found by hand; this catches a fourth.

    `user_metadata` is legitimately written on profile update to keep the
    display name in sync, so this looks for reads of `role` specifically,
    not for the field in general.
    """
    import inspect
    source = inspect.getsource(main)
    hits = re.findall(r'user_metadata.{0,40}?["\']role["\']', source)
    assert not hits, (
        f"a role gate reads user_metadata, which the client can rewrite: {hits}")
