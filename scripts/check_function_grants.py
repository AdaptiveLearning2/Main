#!/usr/bin/env python3
"""Fail if a migration creates a public-schema function without revoking EXECUTE.

Postgres grants EXECUTE on a new function to PUBLIC automatically -- unlike
tables, which are deny-by-default -- and Supabase additionally grants it to anon
and authenticated by name. An explicit grant to a named role survives a revoke
aimed at the PUBLIC pseudo-role, so all three revokes are needed and the usual
one-liner is not enough.

That matters more here than in most projects: the backend reads through the
service-role client, which bypasses RLS, so the checks in main.py are the only
thing between a caller and another student's data. A function left at the
default is reachable by any authenticated user holding the anon key, and the
anon key ships in the frontend bundle.

Why this is a lint and not a database default
---------------------------------------------
The obvious fix is ALTER DEFAULT PRIVILEGES ... REVOKE EXECUTE ON FUNCTIONS FROM
PUBLIC, anon, authenticated. It was tried on a local Supabase stack on
2026-08-04 and it does not work: the pg_default_acl row is recorded correctly
and the anon/authenticated named grants do disappear, but new functions still
come back with `=X/postgres` -- the PUBLIC grant -- and both anon and
authenticated can still execute them. Verified with three throwaway functions,
with the grantees combined in one statement and separated, and with no event
trigger re-granting. A default that silently fails to deny is worse than none,
because it invites trust it has not earned.

So the enforcement lives here, where it is deterministic and visible at review
time.

What it does and does not catch
-------------------------------
Comments are stripped before matching, so neither a commented-out CREATE nor a
commented-out REVOKE counts. An unqualified `CREATE FUNCTION foo()` is treated
as public, because that is where it lands under the default search_path -- and
it is world-executable there exactly like a qualified one. A function created in
a schema that is named and is not public is ignored.

Functions are matched by NAME, not by full signature. Reconstructing a signature
from `CREATE FUNCTION` (named parameters with types, often across several lines)
and matching it against `REVOKE ... ON FUNCTION` (types only) is more fragile
than the problem warrants. The consequence is a real gap: a migration that adds
an overload and revokes only the previous signature passes this check. That is a
narrower mistake than forgetting the revokes altogether, which is what this
catches, but it is not covered -- review still has to.

The check is cumulative across all migrations rather than per-file, so a
function created in one migration and revoked in a later one passes. That is
deliberate: handle_new_user was created in the initial schema and revoked many
migrations later, and rewriting history to satisfy a lint would be worse than
the lint understanding it.

Run `python scripts/check_function_grants.py --self-test` to exercise the parser
against its own known cases. Stdlib only, so it runs anywhere the repo is
checked out.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parent.parent / "supabase" / "migrations"

# Functions that are deliberately callable by anon and authenticated, and why.
# Anything added here needs a reason that survives someone reading it cold in a
# year; "it broke without it" is a symptom, not a reason.
ALLOWLIST = {
    "is_member_of_class": (
        "RLS policies evaluate it as the calling user, so it must be granted to "
        "anon/authenticated or the policies it exists to serve deny everything. "
        "Safe by construction: an auth.uid()-scoped boolean with no parameter to "
        "pivot on, and a pinned search_path."
    ),
    "is_teacher_of_class": (
        "Same as is_member_of_class -- see "
        "20260709154104_teacher_read_policies_and_recursion_fix.sql."
    ),
}

REQUIRED_GRANTEES = ("PUBLIC", "anon", "authenticated")

# The schema prefix is OPTIONAL. An unqualified CREATE FUNCTION lands in public
# under the default search_path and is world-executable there just the same, so
# requiring "public". would make the most dangerous case -- the one where the
# author was not thinking about schemas at all -- the one the check cannot see.
CREATE_RE = re.compile(
    r'CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+'
    r'(?:(?P<schema>"?\w+"?)\s*\.\s*)?'
    r'"?(?P<name>\w+)"?\s*\(',
    re.IGNORECASE,
)

# [^;]* rather than .*? with DOTALL: bounding each match to its own statement
# stops a REVOKE naming one function from binding to a FROM clause in a later
# one. Everything between FROM and the semicolon is captured, so a
# comma-separated grantee list -- valid SQL that does exactly the right thing --
# is read as the three revokes it is, rather than reported as two missing ones.
REVOKE_RE = re.compile(
    r'REVOKE\s+[^;]*?\bON\s+FUNCTION\s+'
    r'(?:"?\w+"?\s*\.\s*)?'
    r'"?(?P<name>\w+)"?'
    r'[^;]*?\bFROM\s+(?P<grantees>[^;]+);',
    re.IGNORECASE,
)

BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT_RE = re.compile(r"--[^\n]*")


def strip_sql_comments(sql: str) -> str:
    """Remove -- and /* */ comments.

    Naive about string literals and dollar-quoted bodies, which is fine for what
    this reads: a `--` inside a function body can only blank the rest of that
    line of body text, and nothing here matches on bodies. Getting it wrong the
    other way -- counting a commented-out REVOKE as real -- is the failure that
    would matter.
    """
    return LINE_COMMENT_RE.sub("", BLOCK_COMMENT_RE.sub("", sql))


def scan(sql: str) -> tuple[list[str], dict[str, set[str]]]:
    """Return (public functions created, {function: grantees revoked from})."""
    sql = strip_sql_comments(sql)

    created = []
    for match in CREATE_RE.finditer(sql):
        schema = (match.group("schema") or "public").strip('"').lower()
        if schema != "public":
            continue  # another schema's problem, and not world-executable here
        created.append(match.group("name"))

    revoked: dict[str, set[str]] = {}
    for match in REVOKE_RE.finditer(sql):
        grantees = {
            g.strip().strip('"').lower()
            for g in match.group("grantees").split(",")
            if g.strip()
        }
        revoked.setdefault(match.group("name"), set()).update(grantees)

    return created, revoked


def analyse(sources: list[tuple[str, str]]) -> tuple[list[str], dict[str, str]]:
    """sources is [(label, sql)] in application order."""
    created: dict[str, str] = {}
    revoked: dict[str, set[str]] = {}

    for label, sql in sources:
        file_created, file_revoked = scan(sql)
        for name in file_created:
            created.setdefault(name, label)
        for name, grantees in file_revoked.items():
            revoked.setdefault(name, set()).update(grantees)

    failures = []
    for name, origin in sorted(created.items()):
        if name in ALLOWLIST:
            continue
        have = revoked.get(name, set())
        missing = [g for g in REQUIRED_GRANTEES if g.lower() not in have]
        if missing:
            failures.append(
                f"  {name}  (created in {origin})\n"
                f"      missing REVOKE from: {', '.join(missing)}"
            )
    return failures, created


REMEDY = (
    "\nAdd to the migration that creates each one:\n\n"
    '  REVOKE ALL ON FUNCTION "public"."<name>"(<argtypes>) FROM PUBLIC;\n'
    '  REVOKE ALL ON FUNCTION "public"."<name>"(<argtypes>) FROM "anon";\n'
    '  REVOKE ALL ON FUNCTION "public"."<name>"(<argtypes>) FROM "authenticated";\n'
    '  GRANT EXECUTE ON FUNCTION "public"."<name>"(<argtypes>) TO "service_role";\n\n'
    "A single REVOKE ... FROM PUBLIC, \"anon\", \"authenticated\"; is equivalent and\n"
    "is accepted. If the function is genuinely meant to be callable by anon or\n"
    "authenticated, add it to ALLOWLIST in this script with the reason."
)


def main() -> int:
    if not MIGRATIONS.is_dir():
        print(f"error: no migrations directory at {MIGRATIONS}", file=sys.stderr)
        return 2

    paths = sorted(MIGRATIONS.glob("*.sql"))
    if not paths:
        # A glob matching nothing used to report "ok: 0 functions", so a wrong
        # path or a partial checkout read as a pass. There is always at least
        # the initial schema.
        print(f"error: no .sql files found in {MIGRATIONS}", file=sys.stderr)
        return 2

    sources = [(p.name, p.read_text(encoding="utf-8")) for p in paths]
    failures, created = analyse(sources)

    if failures:
        print("Public-schema functions missing an EXECUTE revoke:\n", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        print(REMEDY, file=sys.stderr)
        return 1

    allowlisted = len(ALLOWLIST.keys() & created.keys())
    print(
        f"ok: {len(paths)} migration(s), "
        f"{len(created) - allowlisted} function(s) revoked, "
        f"{allowlisted} allowlisted"
    )
    return 0


# \x00 splits a case into separate "migration files", for the cumulative rule.
CASES: list[tuple[str, str, bool]] = [
    (
        "qualified, three separate revokes",
        'CREATE FUNCTION "public"."f"() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;'
        'REVOKE ALL ON FUNCTION "public"."f"() FROM PUBLIC;'
        'REVOKE ALL ON FUNCTION "public"."f"() FROM "anon";'
        'REVOKE ALL ON FUNCTION "public"."f"() FROM "authenticated";',
        True,
    ),
    (
        "comma-separated grantees in one statement",
        'CREATE FUNCTION "public"."f"() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;'
        'REVOKE ALL ON FUNCTION "public"."f"() FROM PUBLIC, "anon", "authenticated";',
        True,
    ),
    (
        "unqualified CREATE still lands in public",
        "CREATE FUNCTION leaky() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;",
        False,
    ),
    (
        "unqualified CREATE with unqualified revokes",
        "CREATE FUNCTION leaky() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;"
        "REVOKE ALL ON FUNCTION leaky() FROM PUBLIC, anon, authenticated;",
        True,
    ),
    (
        "no revokes at all",
        'CREATE FUNCTION "public"."f"() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;',
        False,
    ),
    (
        "revoked from PUBLIC only -- the Supabase trap",
        'CREATE FUNCTION "public"."f"() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;'
        'REVOKE ALL ON FUNCTION "public"."f"() FROM PUBLIC;',
        False,
    ),
    (
        "commented-out revokes do not count",
        'CREATE FUNCTION "public"."f"() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;\n'
        '-- REVOKE ALL ON FUNCTION "public"."f"() FROM PUBLIC;\n'
        '-- REVOKE ALL ON FUNCTION "public"."f"() FROM "anon";\n'
        '-- REVOKE ALL ON FUNCTION "public"."f"() FROM "authenticated";\n',
        False,
    ),
    (
        "commented-out CREATE is not flagged",
        '/* CREATE FUNCTION "public"."ghost"() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$; */',
        True,
    ),
    (
        "a named non-public schema is ignored",
        'CREATE FUNCTION "storage"."f"() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;',
        True,
    ),
    (
        "allowlisted function needs no revoke",
        'CREATE FUNCTION "public"."is_member_of_class"("p_class_id" "uuid") '
        "RETURNS boolean LANGUAGE sql AS $$ SELECT true $$;",
        True,
    ),
    (
        "revoke in a later migration counts",
        'CREATE FUNCTION "public"."f"() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;'
        "\x00"
        'REVOKE ALL ON FUNCTION "public"."f"() FROM PUBLIC, "anon", "authenticated";',
        True,
    ),
    (
        "a REVOKE does not bind across a statement boundary",
        'CREATE FUNCTION "public"."a"() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;'
        'CREATE FUNCTION "public"."b"() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;'
        'REVOKE ALL ON FUNCTION "public"."a"() FROM PUBLIC, "anon", "authenticated";',
        False,
    ),
]


def self_test() -> int:
    failed = []
    for name, sql, should_pass in CASES:
        sources = [
            (f"{name}#{i}", part) for i, part in enumerate(sql.split("\x00"))
        ]
        failures, _ = analyse(sources)
        passed = not failures
        if passed != should_pass:
            failed.append(
                f"  {name}: expected {'pass' if should_pass else 'fail'}, "
                f"got {'pass' if passed else 'fail'}"
            )

    if failed:
        print("self-test failures:\n" + "\n".join(failed), file=sys.stderr)
        return 1
    print(f"ok: {len(CASES)} self-test case(s) passed")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    raise SystemExit(main())
