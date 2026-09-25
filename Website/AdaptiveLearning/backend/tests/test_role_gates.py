"""Role gates read `profiles.role` (never `user_metadata.role`), and a migration makes it client-unwritable."""

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
            def gt(self, *_a):      return self
            def delete(self, **_k): return self   # claims no code: there is none
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
        main.link_child(main.LinkChildRequest(link_code="ABCD2345"), None)
    assert e.value.status_code == 403


def test_my_classes_reads_the_profile_not_the_claim(monkeypatch):
    """Not a privilege boundary, but must agree with the other gates about the role."""
    monkeypatch.setattr(main, "supabase", _Profiles(role="student"))
    monkeypatch.setattr(main, "get_user", lambda _r: _claiming("teacher"))
    seen = []
    monkeypatch.setattr(main, "_role", lambda uid: seen.append(uid) or "student")

    main.my_classes(None)
    assert seen == [UID]


# ── the gate still admits the people it should ──────────────────────────────

def test_a_real_teacher_may_still_create_a_class(monkeypatch):
    """Every test above would also pass against "refuse everyone"."""
    monkeypatch.setattr(main, "supabase", _Profiles(role="teacher"))
    # Claims nothing: the profile alone is the basis for admitting them.
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": UID})

    out = main.create_class(main.CreateClassRequest(name="Maths"), None)
    assert out["teacher_id"] == UID


def test_a_real_parent_reaches_the_code_lookup(monkeypatch):
    """Past the role gate, so it fails on the code instead of on the role."""
    monkeypatch.setattr(main, "supabase", _Profiles(role="parent"))
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": UID})

    with pytest.raises(main.HTTPException) as e:
        main.link_child(main.LinkChildRequest(link_code="ABCD2345"), None)
    assert e.value.status_code == 404


# ── failing closed ──────────────────────────────────────────────────────────

def test_an_unreadable_profile_denies_rather_than_admitting(monkeypatch):
    """`_profile` falls back to a student-shaped dict on a failed read."""
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
    """`handle_new_user` copies client metadata into the role; checked as a whitelist, not a blacklist."""
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
    sql = _migration_sql()
    assert re.search(
        r'CREATE\s+TRIGGER\s+"?on_auth_user_created"?\s+AFTER\s+INSERT\s+ON\s+'
        r'"?auth"?\s*\.\s*"?users"?', sql, re.IGNORECASE), (
        "no migration creates the auth.users trigger, so profiles rows depend "
        "on something hand-made in the dashboard")


def test_the_backfill_applies_the_same_role_whitelist_as_the_trigger():
    """The backfill reads the same client-supplied metadata as the trigger."""
    sql = _migration_sql()
    inserts = re.findall(
        r'INSERT INTO\s+"?public"?\.\s*"?profiles"?(.*?);', sql,
        re.IGNORECASE | re.DOTALL)
    # `NOT EXISTS` marks the backfill, excluding the trigger's older INSERTs.
    backfills = [i for i in inserts
                 if "raw_user_meta_data" in i and re.search(r"NOT\s+EXISTS", i, re.I)]
    assert backfills, "no profiles backfill found"
    for body in backfills:
        listed = set(re.findall(r"'(student|teacher|parent|admin)'", body))
        assert listed == {"student", "teacher", "parent"}, (
            f"a profiles backfill's role whitelist is {sorted(listed)} -- it "
            "must match the trigger's and must not admit 'admin'")


def test_signup_keeps_only_a_grade_from_the_pickers_list():
    """The trigger's grade whitelist is the frontend's list, and each label is one the backend reads.

    The behaviour (a junk or teacher's grade stored as none) is asserted in `assert_signal_rls.sql`.
    """
    bodies = re.findall(
        r'CREATE OR REPLACE FUNCTION\s+"?public"?\.\s*"?handle_new_user"?.*?\$\$(.*?)\$\$',
        _migration_sql(), re.IGNORECASE | re.DOTALL)
    match = re.search(r"grade\s+in\s*\((.*?)\)", bodies[-1], re.IGNORECASE | re.DOTALL)
    assert match, "the newest handle_new_user keeps no grade whitelist"
    trigger = re.findall(r"'([^']+)'", match.group(1))

    grades_js = (_MIGRATIONS.parent.parent / "Website" / "AdaptiveLearning" / "frontend" / "src"
                 / "lib" / "grades.js").read_text(encoding="utf-8")
    block = re.search(r"export const GRADES = \[(.*?)\]", grades_js, re.DOTALL)
    assert block, "GRADES not found in lib/grades.js -- this check is inert"
    frontend = re.findall(r"'([^']+)'", block.group(1))

    assert trigger == frontend
    for label in trigger:
        assert main.grade_levels.validated_grade(label) == label


def test_the_role_check_constraint_admits_admin():
    sql = _migration_sql()
    checks = re.findall(r'CONSTRAINT\s+"?profiles_role_check"?\s+CHECK\s*\((.*?)\)\s*;',
                        sql, re.IGNORECASE | re.DOTALL)
    assert checks, "no profiles_role_check found"
    assert "'admin'" in checks[-1].replace('"', ""), (
        "the newest profiles_role_check does not admit 'admin'")


def test_no_endpoint_gates_on_user_metadata(monkeypatch):
    """Matches `role` reads only; `user_metadata` is legitimately written for the display name."""
    import inspect
    source = inspect.getsource(main)
    hits = re.findall(r'user_metadata.{0,40}?["\']role["\']', source)
    assert not hits, (
        f"a role gate reads user_metadata, which the client can rewrite: {hits}")
