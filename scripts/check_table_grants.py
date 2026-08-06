#!/usr/bin/env python3
"""Fail if a migration creates a public-schema table without revoking the
default grants.

The table-level twin of check_function_grants.py, and the same trap: Supabase
ships ALTER DEFAULT PRIVILEGES granting every table privilege to anon and
authenticated **by name** in public, so a new table arrives as
anon=arwdDxtm,authenticated=arwdDxtm before its own migration grants anything.
Adding a narrow GRANT SELECT on top is a no-op that reads like a restriction --
which is exactly what heart_signals and signal_consent both shipped with before
this check existed.

Why it matters when RLS is already on
-------------------------------------
RLS covers INSERT, UPDATE and DELETE: with no policy for a command, the command
is denied whatever the grant says. It does **not** cover TRUNCATE. Probed on a
local stack: as anon, INSERT was blocked and TRUNCATE succeeded on every table
in the schema.

PostgREST does not expose TRUNCATE, so the anon key in the frontend bundle is
not a path to it -- it needs a direct Postgres connection. That makes this a
defence-in-depth issue rather than an open door, but "not reachable from the
client we happen to ship" is a weaker property than the one a narrow GRANT
appears to promise, and the promise is what the next person reads.

What it requires
----------------
For every CREATE TABLE in public, a REVOKE ... FROM anon and a REVOKE ... FROM
authenticated somewhere in the migrations. It does not check what is granted
back afterwards -- that is per-table judgement (user_math_performance genuinely
needs INSERT/UPDATE for the frontend's upsert under "perf: own"; heart_signals
needs SELECT and nothing else) and encoding it here would mean encoding the
whole access model in a lint.

Matching is by table NAME, cumulative across migrations, and comments are
stripped first -- same shape, and same limitations, as the function check.

Run with --self-test to exercise the parser against its own cases.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parent.parent / "supabase" / "migrations"

# Tables that deliberately keep a default grant, and why.
ALLOWLIST: dict[str, str] = {}

REQUIRED_GRANTEES = ("anon", "authenticated")

CREATE_RE = re.compile(
    r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?'
    r'(?:(?P<schema>"?\w+"?)\s*\.\s*)?'
    r'"?(?P<name>\w+)"?\s*\(',
    re.IGNORECASE,
)

# ON TABLE is optional in Postgres -- REVOKE ALL ON "public"."x" is valid.
REVOKE_RE = re.compile(
    r'REVOKE\s+[^;]*?\bON\s+(?:TABLE\s+)?'
    r'(?:"?\w+"?\s*\.\s*)?'
    r'"?(?P<name>\w+)"?'
    r'[^;]*?\bFROM\s+(?P<grantees>[^;]+);',
    re.IGNORECASE,
)

BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT_RE = re.compile(r"--[^\n]*")
DROP_BEHAVIOUR_RE = re.compile(r"\s+(?:CASCADE|RESTRICT)\s*$", re.IGNORECASE)


def strip_sql_comments(sql: str) -> str:
    return LINE_COMMENT_RE.sub("", BLOCK_COMMENT_RE.sub("", sql))


def scan(sql: str) -> tuple[list[str], dict[str, set[str]]]:
    sql = strip_sql_comments(sql)

    created = []
    for match in CREATE_RE.finditer(sql):
        schema = (match.group("schema") or "public").strip('"').lower()
        if schema != "public":
            continue
        created.append(match.group("name"))

    revoked: dict[str, set[str]] = {}
    for match in REVOKE_RE.finditer(sql):
        grantees = {
            cleaned
            for g in match.group("grantees").split(",")
            if (cleaned := DROP_BEHAVIOUR_RE.sub("", g.strip()).strip('"').lower())
        }
        revoked.setdefault(match.group("name"), set()).update(grantees)

    return created, revoked


def analyse(sources: list[tuple[str, str]]) -> tuple[list[str], dict[str, str]]:
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
    "\nSupabase grants every table privilege to anon and authenticated by name,\n"
    "so a new table is fully granted before your migration says anything. Revoke\n"
    "first, then grant back only what the table's policies need:\n\n"
    '  REVOKE ALL ON TABLE "public"."<name>" FROM "anon";\n'
    '  REVOKE ALL ON TABLE "public"."<name>" FROM "authenticated";\n'
    '  GRANT SELECT ON TABLE "public"."<name>" TO "authenticated";\n'
    '  GRANT ALL ON TABLE "public"."<name>" TO "service_role";\n\n'
    "RLS already denies INSERT/UPDATE/DELETE without a policy, but it does NOT\n"
    "filter TRUNCATE -- which is what the default grant actually leaves open."
)


def main() -> int:
    if not MIGRATIONS.is_dir():
        print(f"error: no migrations directory at {MIGRATIONS}", file=sys.stderr)
        return 2

    paths = sorted(MIGRATIONS.glob("*.sql"))
    if not paths:
        print(f"error: no .sql files found in {MIGRATIONS}", file=sys.stderr)
        return 2

    sources = [(p.name, p.read_text(encoding="utf-8")) for p in paths]
    failures, created = analyse(sources)

    if failures:
        print("Public-schema tables missing a default-grant revoke:\n", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        print(REMEDY, file=sys.stderr)
        return 1

    allowlisted = len(ALLOWLIST.keys() & created.keys())
    print(
        f"ok: {len(paths)} migration(s), "
        f"{len(created) - allowlisted} table(s) revoked, "
        f"{allowlisted} allowlisted"
    )
    return 0


# \x00 splits a case into separate "migration files", for the cumulative rule.
CASES: list[tuple[str, str, bool]] = [
    (
        "both roles revoked",
        'CREATE TABLE "public"."t" ("id" bigint);'
        'REVOKE ALL ON TABLE "public"."t" FROM "anon";'
        'REVOKE ALL ON TABLE "public"."t" FROM "authenticated";',
        True,
    ),
    (
        "comma-separated grantees",
        'CREATE TABLE "public"."t" ("id" bigint);'
        'REVOKE ALL ON TABLE "public"."t" FROM "anon", "authenticated";',
        True,
    ),
    (
        "IF NOT EXISTS is still a CREATE",
        'CREATE TABLE IF NOT EXISTS "public"."t" ("id" bigint);',
        False,
    ),
    (
        "unqualified CREATE lands in public",
        "CREATE TABLE t (id bigint);",
        False,
    ),
    (
        "ON TABLE is optional in REVOKE",
        'CREATE TABLE "public"."t" ("id" bigint);'
        'REVOKE ALL ON "public"."t" FROM "anon", "authenticated";',
        True,
    ),
    (
        "a narrow GRANT alone is not a revoke -- the whole point",
        'CREATE TABLE "public"."t" ("id" bigint);'
        'GRANT SELECT ON TABLE "public"."t" TO "authenticated";',
        False,
    ),
    (
        "revoking only anon leaves authenticated",
        'CREATE TABLE "public"."t" ("id" bigint);'
        'REVOKE ALL ON TABLE "public"."t" FROM "anon";',
        False,
    ),
    (
        "commented-out revokes do not count",
        'CREATE TABLE "public"."t" ("id" bigint);\n'
        '-- REVOKE ALL ON TABLE "public"."t" FROM "anon", "authenticated";\n',
        False,
    ),
    (
        "another schema is not our problem",
        'CREATE TABLE "storage"."t" ("id" bigint);',
        True,
    ),
    (
        "revoke in a later migration counts",
        'CREATE TABLE "public"."t" ("id" bigint);'
        "\x00"
        'REVOKE ALL ON TABLE "public"."t" FROM "anon", "authenticated";',
        True,
    ),
    (
        "trailing CASCADE is not part of the last grantee",
        'CREATE TABLE "public"."t" ("id" bigint);'
        'REVOKE ALL ON TABLE "public"."t" FROM "anon", "authenticated" CASCADE;',
        True,
    ),
]


def self_test() -> int:
    failed = []
    for name, sql, should_pass in CASES:
        sources = [(f"{name}#{i}", part) for i, part in enumerate(sql.split("\x00"))]
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
