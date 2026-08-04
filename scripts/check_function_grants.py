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

Scope, stated plainly
---------------------
Functions are matched by NAME, not by full signature. Reconstructing a signature
from `CREATE FUNCTION` (named parameters with types, often across several lines)
and matching it against `REVOKE ... ON FUNCTION` (types only) is more fragile
than the problem warrants. The consequence is a real gap: a migration that adds
an overload and revokes only the previous signature passes this check. That is a
narrower mistake than forgetting the revokes altogether, which is what this
catches, but it is not covered -- review still has to.

The check is cumulative across all migrations rather than per-file, so a
function created in one migration and revoked in a later one passes. That is
deliberate: handle_new_user was created in the initial schema and revoked years
of migrations later, and rewriting history to satisfy a lint would be worse than
the lint understanding it.
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

# "public" and the function name may or may not be double-quoted, and CREATE may
# be CREATE OR REPLACE.
CREATE_RE = re.compile(
    r'CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+"?public"?\s*\.\s*"?(?P<name>\w+)"?',
    re.IGNORECASE,
)
REVOKE_RE = re.compile(
    r'REVOKE\s+.*?\s+ON\s+FUNCTION\s+"?public"?\s*\.\s*"?(?P<name>\w+)"?'
    r'.*?\sFROM\s+(?P<grantee>"?\w+"?)',
    re.IGNORECASE | re.DOTALL,
)


def main() -> int:
    if not MIGRATIONS.is_dir():
        print(f"error: no migrations directory at {MIGRATIONS}", file=sys.stderr)
        return 2

    created: dict[str, str] = {}          # name -> first migration that created it
    revoked: dict[str, set[str]] = {}     # name -> grantees revoked from

    for path in sorted(MIGRATIONS.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")

        for match in CREATE_RE.finditer(sql):
            created.setdefault(match.group("name"), path.name)

        for match in REVOKE_RE.finditer(sql):
            grantee = match.group("grantee").strip('"').lower()
            revoked.setdefault(match.group("name"), set()).add(grantee)

    failures: list[str] = []
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

    checked = len(created) - len(ALLOWLIST & created.keys())
    if failures:
        print("Public-schema functions missing an EXECUTE revoke:\n", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        print(
            "\nAdd to the migration that creates each one:\n\n"
            '  REVOKE ALL ON FUNCTION "public"."<name>"(<argtypes>) FROM PUBLIC;\n'
            '  REVOKE ALL ON FUNCTION "public"."<name>"(<argtypes>) FROM "anon";\n'
            '  REVOKE ALL ON FUNCTION "public"."<name>"(<argtypes>) FROM "authenticated";\n'
            '  GRANT EXECUTE ON FUNCTION "public"."<name>"(<argtypes>) TO "service_role";\n\n'
            "If it is genuinely meant to be callable by anon or authenticated, add it\n"
            "to ALLOWLIST in this script with the reason.",
            file=sys.stderr,
        )
        return 1

    print(f"ok: {checked} public-schema function(s) revoked, {len(ALLOWLIST & created.keys())} allowlisted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
