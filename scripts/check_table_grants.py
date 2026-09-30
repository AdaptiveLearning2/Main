#!/usr/bin/env python3
"""Fail if a migration creates a public table, view or sequence without revoking the default grants.

Each needs a whole-object REVOKE ALL from anon and authenticated (CLAUDE.md, *Database*); a table
also needs RLS enabled and a view `security_invoker`. What is granted back is not checked.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parent.parent / "supabase" / "migrations"

# Tables that deliberately keep a default grant, and why.
ALLOWLIST: dict[str, str] = {}

REQUIRED_GRANTEES = ("anon", "authenticated")

# TEMP tables are not matched: they land in pg_temp, not public. `rest` carries a view's options.
CREATE_RE = re.compile(
    r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:UNLOGGED\s+)?'
    r'(?P<kind>TABLE|MATERIALIZED\s+VIEW|VIEW|SEQUENCE)\s+(?:IF\s+NOT\s+EXISTS\s+)?'
    r'(?:(?P<schema>"?\w+"?)\s*\.\s*)?'
    r'"?(?P<name>\w+)"?(?P<rest>[^;]*)',
    re.IGNORECASE,
)

# ON TABLE is optional in Postgres -- REVOKE ALL ON "public"."x" is valid.
REVOKE_RE = re.compile(
    r'REVOKE\s+(?P<privs>[^;]*?)\bON\s+(?:TABLE\s+|SEQUENCE\s+)?'
    r'(?:"?\w+"?\s*\.\s*)?'
    r'"?(?P<name>\w+)"?'
    r'[^;]*?\bFROM\s+(?P<grantees>[^;]+);',
    re.IGNORECASE,
)

# Covers every sequence at once; the matching default privilege covers the ones created later.
ALL_SEQUENCES_RE = re.compile(
    r'REVOKE\s+ALL\s+(?:PRIVILEGES\s+)?ON\s+ALL\s+SEQUENCES\s+IN\s+SCHEMA\s+"?public"?\s+'
    r'FROM\s+(?P<grantees>[^;]+);',
    re.IGNORECASE,
)
ALL_SEQUENCES = "*all sequences*"

RLS_RE = re.compile(
    r'ALTER\s+TABLE\s+(?:ONLY\s+)?(?:IF\s+EXISTS\s+)?(?:"?\w+"?\s*\.\s*)?"?(?P<name>\w+)"?\s+'
    r'ENABLE\s+ROW\s+LEVEL\s+SECURITY',
    re.IGNORECASE,
)

INVOKER_RE = re.compile(r'security_invoker\s*=\s*(?:true|on|1)\b', re.IGNORECASE)
ALTER_VIEW_INVOKER_RE = re.compile(
    r'ALTER\s+VIEW\s+(?:IF\s+EXISTS\s+)?(?:"?\w+"?\s*\.\s*)?"?(?P<name>\w+)"?\s+SET\s*\([^)]*'
    r'security_invoker\s*=\s*(?:true|on|1)\b',
    re.IGNORECASE,
)

BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT_RE = re.compile(r"--[^\n]*")
DROP_BEHAVIOUR_RE = re.compile(r"\s+(?:CASCADE|RESTRICT)\s*$", re.IGNORECASE)


def strip_sql_comments(sql: str) -> str:
    return LINE_COMMENT_RE.sub("", BLOCK_COMMENT_RE.sub("", sql))


def _grantees(text: str) -> set[str]:
    return {cleaned for g in text.split(",")
            if (cleaned := DROP_BEHAVIOUR_RE.sub("", g.strip()).strip('"').lower())}


def scan(sql: str) -> tuple[list[tuple[str, str, bool]], dict[str, set[str]], set[str]]:
    """(created as (name, kind, invoker option), {name: revoked from}, {names with RLS or invoker set})."""
    sql = strip_sql_comments(sql)

    created = []
    for match in CREATE_RE.finditer(sql):
        schema = (match.group("schema") or "public").strip('"').lower()
        if schema != "public":
            continue
        kind = " ".join(match.group("kind").upper().split())
        created.append((match.group("name"), kind, bool(INVOKER_RE.search(match.group("rest")))))

    revoked: dict[str, set[str]] = {}
    for match in REVOKE_RE.finditer(sql):
        privs = match.group("privs").strip().upper()
        # A column list or a single privilege leaves the rest of the default grant standing.
        if "(" in privs or privs not in ("ALL", "ALL PRIVILEGES"):
            continue
        revoked.setdefault(match.group("name"), set()).update(_grantees(match.group("grantees")))
    for match in ALL_SEQUENCES_RE.finditer(sql):
        revoked.setdefault(ALL_SEQUENCES, set()).update(_grantees(match.group("grantees")))

    secured = {m.group("name") for m in RLS_RE.finditer(sql)}
    secured |= {m.group("name") for m in ALTER_VIEW_INVOKER_RE.finditer(sql)}
    return created, revoked, secured


def analyse(sources: list[tuple[str, str]]) -> tuple[list[str], dict[str, str]]:
    created: dict[str, tuple[str, str, bool]] = {}
    revoked: dict[str, set[str]] = {}
    secured: set[str] = set()

    for label, sql in sources:
        file_created, file_revoked, file_secured = scan(sql)
        for name, kind, invoker in file_created:
            created.setdefault(name, (label, kind, invoker))
        for name, grantees in file_revoked.items():
            revoked.setdefault(name, set()).update(grantees)
        secured |= file_secured

    failures = []
    for name, (origin, kind, invoker) in sorted(created.items()):
        if name in ALLOWLIST:
            continue
        have = set(revoked.get(name, set()))
        if kind == "SEQUENCE":
            have |= revoked.get(ALL_SEQUENCES, set())
        problems = []
        missing = [g for g in REQUIRED_GRANTEES if g.lower() not in have]
        if missing:
            problems.append(f"missing REVOKE ALL from: {', '.join(missing)}")
        if kind == "TABLE" and name not in secured:
            problems.append("row level security is never enabled")
        if kind == "VIEW" and not invoker and name not in secured:
            problems.append("a view without security_invoker reads as its owner, past RLS")
        if problems:
            failures.append(f"  {name}  ({kind.lower()}, created in {origin})\n"
                            + "".join(f"      {p}\n" for p in problems).rstrip("\n"))
    return failures, {name: v[0] for name, v in created.items()}


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
        f"{len(created) - allowlisted} object(s) revoked, "
        f"{allowlisted} allowlisted"
    )
    return 0


# \x00 splits a case into separate "migration files", for the cumulative rule.
CASES: list[tuple[str, str, bool]] = [
    ('both roles revoked, RLS on',
     'CREATE TABLE "public"."t" ("id" bigint);REVOKE ALL ON TABLE "public"."t" FROM "anon";REVOKE ALL ON TABLE "public"."t" FROM "authenticated";ALTER TABLE "public"."t" ENABLE ROW LEVEL SECURITY;',
     True),
    ('comma-separated grantees',
     'CREATE TABLE "public"."t" ("id" bigint);REVOKE ALL ON TABLE "public"."t" FROM "anon", "authenticated";ALTER TABLE "public"."t" ENABLE ROW LEVEL SECURITY;',
     True),
    ('IF NOT EXISTS is still a CREATE',
     'CREATE TABLE IF NOT EXISTS "public"."t" ("id" bigint);',
     False),
    ('unqualified CREATE lands in public',
     'CREATE TABLE t (id bigint);',
     False),
    ('ON TABLE is optional in REVOKE',
     'CREATE TABLE "public"."t" ("id" bigint);REVOKE ALL ON "public"."t" FROM "anon", "authenticated";ALTER TABLE "public"."t" ENABLE ROW LEVEL SECURITY;',
     True),
    ('a narrow GRANT alone is not a revoke -- the whole point',
     'CREATE TABLE "public"."t" ("id" bigint);GRANT SELECT ON TABLE "public"."t" TO "authenticated";ALTER TABLE "public"."t" ENABLE ROW LEVEL SECURITY;',
     False),
    ('revoking only anon leaves authenticated',
     'CREATE TABLE "public"."t" ("id" bigint);REVOKE ALL ON TABLE "public"."t" FROM "anon";ALTER TABLE "public"."t" ENABLE ROW LEVEL SECURITY;',
     False),
    ('commented-out revokes do not count',
     'CREATE TABLE "public"."t" ("id" bigint);\n-- REVOKE ALL ON TABLE "public"."t" FROM "anon", "authenticated";\nALTER TABLE "public"."t" ENABLE ROW LEVEL SECURITY;',
     False),
    ('another schema is not our problem',
     'CREATE TABLE "storage"."t" ("id" bigint);',
     True),
    ('revoke in a later migration counts',
     'CREATE TABLE "public"."t" ("id" bigint);\x00REVOKE ALL ON TABLE "public"."t" FROM "anon", "authenticated";ALTER TABLE "public"."t" ENABLE ROW LEVEL SECURITY;',
     True),
    ('trailing CASCADE is not part of the last grantee',
     'CREATE TABLE "public"."t" ("id" bigint);REVOKE ALL ON TABLE "public"."t" FROM "anon", "authenticated" CASCADE;ALTER TABLE "public"."t" ENABLE ROW LEVEL SECURITY;',
     True),
    ('a column-level REVOKE is not a table revoke',
     'CREATE TABLE "public"."t" ("id" bigint);REVOKE UPDATE ("id") ON TABLE "public"."t" FROM "anon", "authenticated";ALTER TABLE "public"."t" ENABLE ROW LEVEL SECURITY;',
     False),
    ('revoking one privilege leaves the rest',
     'CREATE TABLE "public"."t" ("id" bigint);REVOKE INSERT ON TABLE "public"."t" FROM "anon", "authenticated";ALTER TABLE "public"."t" ENABLE ROW LEVEL SECURITY;',
     False),
    ('a revoked table without RLS',
     'CREATE TABLE "public"."t" ("id" bigint);REVOKE ALL ON TABLE "public"."t" FROM "anon", "authenticated";',
     False),
    ('an UNLOGGED table is a table',
     'CREATE UNLOGGED TABLE "public"."t" ("id" bigint);',
     False),
    ('CREATE TABLE AS is a table',
     'CREATE TABLE "public"."t" AS SELECT 1 AS id;',
     False),
    ('a TEMP table is not public',
     'CREATE TEMP TABLE t (id bigint);',
     True),
    ('a view needs revokes',
     'CREATE VIEW "public"."v" WITH (security_invoker = true) AS SELECT 1;',
     False),
    ('a revoked view without security_invoker',
     'CREATE VIEW "public"."v" AS SELECT 1;REVOKE ALL ON "public"."v" FROM "anon", "authenticated";',
     False),
    ('a revoked invoker view',
     'CREATE OR REPLACE VIEW "public"."v" WITH (security_invoker = true) AS SELECT 1;REVOKE ALL ON "public"."v" FROM "anon", "authenticated";',
     True),
    ('invoker set by ALTER VIEW',
     'CREATE VIEW "public"."v" AS SELECT 1;ALTER VIEW "public"."v" SET (security_invoker = on);REVOKE ALL ON "public"."v" FROM "anon", "authenticated";',
     True),
    ('a materialized view needs revokes',
     'CREATE MATERIALIZED VIEW "public"."m" AS SELECT 1;',
     False),
    ('a sequence needs revokes',
     'CREATE SEQUENCE "public"."s";',
     False),
    ('a sequence revoked by name',
     'CREATE SEQUENCE "public"."s";REVOKE ALL ON SEQUENCE "public"."s" FROM "anon", "authenticated";',
     True),
    ('a sequence covered by the schema-wide revoke',
     'CREATE SEQUENCE "public"."s";\x00REVOKE ALL ON ALL SEQUENCES IN SCHEMA "public" FROM "anon";REVOKE ALL ON ALL SEQUENCES IN SCHEMA "public" FROM "authenticated";',
     True),
    ('the schema-wide sequence revoke covers no table',
     'CREATE TABLE "public"."t" ("id" bigint);REVOKE ALL ON ALL SEQUENCES IN SCHEMA "public" FROM "anon", "authenticated";ALTER TABLE "public"."t" ENABLE ROW LEVEL SECURITY;',
     False),
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
