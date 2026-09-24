#!/usr/bin/env python3
"""Fail if a migration creates a public-schema function without revoking EXECUTE.

Needs revokes from PUBLIC, anon and authenticated (see CLAUDE.md, *Database*). Cumulative
across migrations; matched by NAME, so an overload revoking only the old signature passes.
`--self-test` exercises the parser. Stdlib only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parent.parent / "supabase" / "migrations"

# Functions deliberately callable by anon and authenticated; each needs a real reason.
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

# Schema prefix OPTIONAL: an unqualified CREATE FUNCTION lands in public too.
CREATE_RE = re.compile(
    r'CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+'
    r'(?:(?P<schema>"?\w+"?)\s*\.\s*)?'
    r'"?(?P<name>\w+)"?\s*\(',
    re.IGNORECASE,
)

# [^;]* bounds each match to one statement; the grantees capture takes a comma list.
REVOKE_RE = re.compile(
    r'REVOKE\s+[^;]*?\bON\s+FUNCTION\s+'
    r'(?:"?\w+"?\s*\.\s*)?'
    r'"?(?P<name>\w+)"?'
    r'[^;]*?\bFROM\s+(?P<grantees>[^;]+);',
    re.IGNORECASE,
)

BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT_RE = re.compile(r"--[^\n]*")

# Strips a trailing CASCADE/RESTRICT from the last grantee.
DROP_BEHAVIOUR_RE = re.compile(r"\s+(?:CASCADE|RESTRICT)\s*$", re.IGNORECASE)


def strip_sql_comments(sql: str) -> str:
    """Remove -- and /* */ comments. Naive about literals and bodies, which nothing here matches on."""
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
            cleaned
            for g in match.group("grantees").split(",")
            if (cleaned := DROP_BEHAVIOUR_RE.sub("", g.strip()).strip('"').lower())
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
        # An empty glob must not read as a pass.
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
        "trailing CASCADE is not part of the last grantee",
        'CREATE FUNCTION "public"."h"() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;'
        'REVOKE ALL ON FUNCTION "public"."h"() FROM PUBLIC, anon, authenticated CASCADE;',
        True,
    ),
    (
        "trailing RESTRICT likewise",
        'CREATE FUNCTION "public"."h"() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;'
        'REVOKE ALL ON FUNCTION "public"."h"() FROM PUBLIC, anon, authenticated RESTRICT;',
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
