# Project conventions

## Database — Postgres functions are world-executable by default

**Every `CREATE FUNCTION` in the `public` schema is EXECUTE-able by every logged-in user unless
you explicitly revoke it, and the usual boilerplate revoke does not catch it.**

Two things stack up:

1. Postgres grants `EXECUTE` on new functions to `PUBLIC` automatically (unlike tables).
2. Supabase additionally ships `ALTER DEFAULT PRIVILEGES` granting `EXECUTE` to `anon` and
   `authenticated` **by name**.

Explicit grants to a named role survive a revoke aimed at the `PUBLIC` pseudo-role, so
`REVOKE ALL ... FROM PUBLIC` alone leaves `anon` and `authenticated` still holding `EXECUTE`.
Verified against `pg_proc.proacl` on a local instance — without the named revokes the ACL comes
back as `{postgres=X/postgres,anon=X/postgres,authenticated=X/postgres,...}`.

### When adding a function

Revoke from the named roles, then grant only what the caller needs:

```sql
REVOKE ALL ON FUNCTION "public"."my_function"("uuid", integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."my_function"("uuid", integer) FROM "anon";
REVOKE ALL ON FUNCTION "public"."my_function"("uuid", integer) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."my_function"("uuid", integer) TO "service_role";
```

Also:

- **Prefer `SECURITY INVOKER`** (the default). A `SECURITY DEFINER` function returning rows or
  aggregates over student data is a ready-made way to read anyone's data. As invoker, RLS still
  applies if the function is ever reached by a lower-privileged role.
- **If you do need `SECURITY DEFINER`, pin `SET search_path`.** An unpinned definer function is
  the classic privilege-escalation vector.
- **End the migration with `NOTIFY pgrst, 'reload schema';`** so PostgREST picks up the new RPC.
- **`CREATE INDEX CONCURRENTLY` is not available in migrations** — Supabase wraps each migration
  in a transaction. Plain `CREATE INDEX` takes an `ACCESS EXCLUSIVE` lock while building. If a
  table is already large, build the index manually with `CONCURRENTLY` outside a transaction
  first; the `IF NOT EXISTS` in the migration then no-ops.

### When changing an existing function's signature

Adding a parameter creates a **new** function rather than replacing the old one, so the migration
has to `DROP FUNCTION` the previous signature explicitly — `CREATE OR REPLACE` alone leaves it
behind as an overload that is still granted, still callable, and unaware of whatever the new
parameter controls. Keeping both is not an option either: with named-argument RPC calls that
match more than one signature, Postgres rejects the call as ambiguous. The new signature also
carries a fresh ACL, so repeat the revokes and the `service_role` grant against it.

That leaves a window. Backend code calling the new signature against a database that has not run
the migration yet gets PostgREST's `PGRST202`, which the callers here catch — so the failure is
silent, and the symptom is empty data rather than an error. **Apply the migration before rolling
out the code that depends on it.** Where an in-between state would be visible to a user,
degrade explicitly: `_summary_rpc` in `Website/AdaptiveLearning/backend/main.py` retries against
the old signature, but only where doing so cannot violate what the caller asked for.

### Do not "fix" the RLS helper functions

`is_member_of_class` and `is_teacher_of_class`
(`supabase/migrations/20260709154104_teacher_read_policies_and_recursion_fix.sql`) are
`SECURITY DEFINER` **and deliberately granted to `anon` and `authenticated`**. That is required:
RLS policies evaluate them as the calling user, so revoking the grants breaks the policies they
exist to serve.

They are safe by construction — both are `auth.uid()`-scoped booleans with no parameter to pivot
on (they answer "am *I* in this class", not "is user X"), and both pin
`SET search_path TO 'public'`.

Audited 2026-07-21: every function in the repo is either correctly revoked, deliberately granted
and safe as above, or uncallable (`handle_new_user` returns `trigger`, which Postgres refuses to
invoke directly and PostgREST will not expose as RPC).

## Access control — check the relationship, not the role name

Endpoints serving student data read through the **service-role Supabase client, which bypasses
RLS**, so the checks in `Website/AdaptiveLearning/backend/main.py` are the only thing standing
between a caller and another student's data.

Use the existing helpers rather than writing a new check inline — re-deriving the rule per
endpoint is how the original `class_live` guard drifted into `owner != user AND role != "teacher"`,
which let any teacher read any class:

- `_verify_class_owner(class_id, user_id)` — only the owning teacher.
- `_verify_can_view_student(viewer, student_id)` — the student themselves, a teacher of a class
  they are enrolled in, or a linked parent.

Access is a **relationship**, not a path segment or a role claim. Don't namespace an endpoint
under `/api/teacher/` when parents legitimately read it too, and don't gate on
`user_metadata.role`.

Access-control tests live in `Website/AdaptiveLearning/backend/tests/test_access_control.py` and
run in CI.
