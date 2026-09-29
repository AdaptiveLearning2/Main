#!/usr/bin/env python3
"""Fail if a public function or procedure is left executable by a client role, or is an unpinned definer.

Needs revokes from PUBLIC, anon and authenticated (CLAUDE.md, *Database*), read in order so a later
GRANT back counts. Revokes match by name; the allowlist by full signature. `--self-test` checks the parser.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parent.parent / "supabase" / "migrations"

# Signatures deliberately callable by anon and authenticated; each needs a real reason. By signature,
# so a new overload that could pivot on a user id does not inherit the exemption.
ALLOWLIST = {
    "is_member_of_class(p_class_id uuid)": (
        "RLS policies evaluate it as the calling user, so it must be granted to "
        "anon/authenticated or the policies it exists to serve deny everything. "
        "Safe by construction: an auth.uid()-scoped boolean with no parameter to "
        "pivot on, and a pinned search_path."
    ),
    "is_teacher_of_class(p_class_id uuid)": (
        "Same as is_member_of_class -- see "
        "20260709154104_teacher_read_policies_and_recursion_fix.sql."
    ),
}

REQUIRED_GRANTEES = ("PUBLIC", "anon", "authenticated")

# Schema prefix OPTIONAL: an unqualified CREATE FUNCTION lands in public too.
CREATE_RE = re.compile(
    r'CREATE\s+(?:OR\s+REPLACE\s+)?(?:FUNCTION|PROCEDURE)\s+'
    r'(?:(?P<schema>"?\w+"?)\s*\.\s*)?'
    r'"?(?P<name>\w+)"?\s*\(',
    re.IGNORECASE,
)

# [^;]* bounds each match to one statement; the grantees capture takes a comma list.
REVOKE_RE = re.compile(
    r'REVOKE\s+[^;]*?\bON\s+(?:FUNCTION|PROCEDURE|ROUTINE)\s+'
    r'(?:"?\w+"?\s*\.\s*)?'
    r'"?(?P<name>\w+)"?'
    r'[^;]*?\bFROM\s+(?P<grantees>[^;]+);',
    re.IGNORECASE,
)

GRANT_RE = re.compile(
    r'GRANT\s+[^;]*?\bON\s+(?:FUNCTION|PROCEDURE|ROUTINE)\s+'
    r'(?:"?\w+"?\s*\.\s*)?'
    r'"?(?P<name>\w+)"?'
    r'[^;]*?\bTO\s+(?P<grantees>[^;]+);',
    re.IGNORECASE,
)

DOLLAR_TAG_RE = re.compile(r"\$(\w*)\$")
DEFINER_RE = re.compile(r"\bSECURITY\s+DEFINER\b", re.IGNORECASE)
PINNED_RE = re.compile(r'\bSET\s+"?search_path"?\s*(?:TO|=)', re.IGNORECASE)

BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT_RE = re.compile(r"--[^\n]*")

# Strips a trailing CASCADE/RESTRICT from the last grantee.
DROP_BEHAVIOUR_RE = re.compile(r"\s+(?:CASCADE|RESTRICT)\s*$", re.IGNORECASE)


def strip_sql_comments(sql: str) -> str:
    """Remove -- and /* */ comments. Naive about literals and bodies, which nothing here matches on."""
    return LINE_COMMENT_RE.sub("", BLOCK_COMMENT_RE.sub("", sql))


def _grantees(text: str) -> set[str]:
    return {cleaned for g in text.split(",")
            if (cleaned := DROP_BEHAVIOUR_RE.sub("", g.strip()).strip('"').lower())}


def _signature(sql: str, open_paren: int) -> tuple[str, int]:
    """The argument list from `open_paren`, normalised, and the index just past its `)`."""
    depth, i = 0, open_paren
    while i < len(sql):
        depth += {"(": 1, ")": -1}.get(sql[i], 0)
        i += 1
        if depth == 0:
            break
    args = " ".join(sql[open_paren + 1:i - 1].replace('"', "").lower().split())
    return re.sub(r"\s*,\s*", ", ", args), i


def _options(sql: str, after_args: int) -> str:
    """The statement's text outside its dollar-quoted body: where SECURITY DEFINER and SET live."""
    tag = DOLLAR_TAG_RE.search(sql, after_args)
    end = sql.find(";", after_args)
    if tag is None or (end != -1 and end < tag.start()):
        return sql[after_args:end if end != -1 else len(sql)]
    close = sql.find(tag.group(0), tag.end())
    close = len(sql) if close == -1 else close + len(tag.group(0))
    tail_end = sql.find(";", close)
    return sql[after_args:tag.start()] + " " + sql[close:tail_end if tail_end != -1 else len(sql)]


def scan(sql: str) -> list[tuple]:
    """Events in statement order: ("create", name, signature, unpinned definer), ("revoke"|"grant", name, grantees)."""
    sql = strip_sql_comments(sql)
    events = []
    for match in CREATE_RE.finditer(sql):
        schema = (match.group("schema") or "public").strip('"').lower()
        if schema != "public":
            continue  # another schema's problem, and not world-executable here
        args, after = _signature(sql, match.end() - 1)
        options = _options(sql, after)
        unpinned = bool(DEFINER_RE.search(options)) and not PINNED_RE.search(options)
        events.append((match.start(), ("create", match.group("name"),
                                       f"{match.group('name')}({args})", unpinned)))
    for kind, regex in (("revoke", REVOKE_RE), ("grant", GRANT_RE)):
        for match in regex.finditer(sql):
            events.append((match.start(), (kind, match.group("name"), _grantees(match.group("grantees")))))
    return [event for _pos, event in sorted(events, key=lambda e: e[0])]


def analyse(sources: list[tuple[str, str]]) -> tuple[list[str], dict[str, str]]:
    """sources is [(label, sql)] in application order."""
    # name -> (first migration, {signature: unpinned definer})
    created: dict[str, tuple[str, dict[str, bool]]] = {}
    revoked: dict[str, set[str]] = {}
    client = {g.lower() for g in REQUIRED_GRANTEES}

    for label, sql in sources:
        for event in scan(sql):
            if event[0] == "create":
                _, name, signature, unpinned = event
                origin, signatures = created.setdefault(name, (label, {}))
                # Per signature, the latest definition decides: a later CREATE OR REPLACE may pin
                # what an earlier one did not, but a new overload replaces nothing.
                signatures[signature] = unpinned
            elif event[0] == "revoke":
                revoked.setdefault(event[1], set()).update(event[2])
            else:
                # A grant back undoes the revoke for those roles, whenever it lands.
                revoked.setdefault(event[1], set()).difference_update(event[2] & client)

    failures = []
    for name, (origin, signatures) in sorted(created.items()):
        problems = []
        if not signatures.keys() <= ALLOWLIST.keys():
            missing = [g for g in REQUIRED_GRANTEES if g.lower() not in revoked.get(name, set())]
            if missing:
                problems.append(f"missing REVOKE from: {', '.join(missing)}")
        problems += [f"SECURITY DEFINER without SET search_path: {sig}"
                     for sig, unpinned in sorted(signatures.items()) if unpinned]
        if problems:
            failures.append(f"  {name}  (created in {origin})\n"
                            + "\n".join(f"      {p}" for p in problems))
    return failures, {name: v[0] for name, v in created.items()}


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

    allowlisted = len({sig.split("(")[0] for sig in ALLOWLIST} & created.keys())
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
        "a procedure needs the same revokes",
        'CREATE PROCEDURE "public"."p"() LANGUAGE sql AS $$ SELECT 1 $$;',
        False,
    ),
    (
        "a procedure revoked as a routine",
        'CREATE PROCEDURE "public"."p"() LANGUAGE sql AS $$ SELECT 1 $$;'
        'REVOKE ALL ON ROUTINE "public"."p"() FROM PUBLIC, "anon", "authenticated";',
        True,
    ),
    (
        "a GRANT back after the revoke undoes it",
        'CREATE FUNCTION "public"."f"() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;'
        'REVOKE ALL ON FUNCTION "public"."f"() FROM PUBLIC, "anon", "authenticated";'
        "\x00"
        'GRANT EXECUTE ON FUNCTION "public"."f"() TO "authenticated";',
        False,
    ),
    (
        "a GRANT before the revoke is undone by it",
        'CREATE FUNCTION "public"."f"() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;'
        'GRANT ALL ON FUNCTION "public"."f"() TO "anon";'
        "\x00"
        'REVOKE ALL ON FUNCTION "public"."f"() FROM PUBLIC, "anon", "authenticated";',
        True,
    ),
    (
        "granting service_role back is fine",
        'CREATE FUNCTION "public"."f"() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;'
        'REVOKE ALL ON FUNCTION "public"."f"() FROM PUBLIC, "anon", "authenticated";'
        'GRANT EXECUTE ON FUNCTION "public"."f"() TO "service_role";',
        True,
    ),
    (
        "an overload of an allowlisted name is not allowlisted",
        'CREATE FUNCTION "public"."is_member_of_class"("p_class_id" "uuid", "p_user" "uuid") '
        "RETURNS boolean LANGUAGE sql AS $$ SELECT true $$;",
        False,
    ),
    (
        "an unpinned definer",
        'CREATE FUNCTION "public"."f"() RETURNS int LANGUAGE sql SECURITY DEFINER AS $$ SELECT 1 $$;'
        'REVOKE ALL ON FUNCTION "public"."f"() FROM PUBLIC, "anon", "authenticated";',
        False,
    ),
    (
        "a definer pinned after its body",
        'CREATE FUNCTION "public"."f"() RETURNS int AS $body$ SELECT 1 $body$ '
        "LANGUAGE sql SECURITY DEFINER SET search_path = '';"
        'REVOKE ALL ON FUNCTION "public"."f"() FROM PUBLIC, "anon", "authenticated";',
        True,
    ),
    (
        "a SET search_path inside the body does not pin the function",
        'CREATE FUNCTION "public"."f"() RETURNS void LANGUAGE plpgsql SECURITY DEFINER AS $$ '
        "BEGIN SET search_path = ''; END $$;"
        'REVOKE ALL ON FUNCTION "public"."f"() FROM PUBLIC, "anon", "authenticated";',
        False,
    ),
    (
        "a later replace that pins the definer",
        'CREATE FUNCTION "public"."f"() RETURNS int LANGUAGE sql SECURITY DEFINER AS $$ SELECT 1 $$;'
        'REVOKE ALL ON FUNCTION "public"."f"() FROM PUBLIC, "anon", "authenticated";'
        "\x00"
        'CREATE OR REPLACE FUNCTION "public"."f"() RETURNS int LANGUAGE sql SECURITY DEFINER '
        "SET search_path TO '' AS $$ SELECT 1 $$;",
        True,
    ),
    (
        "a pinned new overload does not hide an unpinned older one",
        'CREATE FUNCTION "public"."f"() RETURNS int LANGUAGE sql SECURITY DEFINER AS $$ SELECT 1 $$;'
        'REVOKE ALL ON FUNCTION "public"."f"() FROM PUBLIC, "anon", "authenticated";'
        "\x00"
        'CREATE FUNCTION "public"."f"("n" integer) RETURNS int LANGUAGE sql SECURITY DEFINER '
        "SET search_path TO '' AS $$ SELECT 1 $$;",
        False,
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
