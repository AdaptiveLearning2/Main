-- `profiles.role` becomes server-controlled, so it can be trusted as a gate.
--
-- Three endpoints decide what a caller may do based on role: creating a
-- class, listing classes, linking a child. All three read
-- `user_metadata.role`, which the client can rewrite at any time with
-- `supabase.auth.updateUser` -- a call that never passes through the backend.
-- A student could self-elevate and create classes.
--
-- Switching those gates to `profiles.role` doesn't fix it on its own, since
-- `profiles` carries a FOR ALL own-row policy and `authenticated` holds
-- UPDATE, so a student can reach their own row directly through PostgREST. A
-- CHECK can't express "not by you" -- only a grant can.
--
-- So the column privilege is what makes the gate real. Grants are per-column
-- for UPDATE and INSERT, so the row stays writable for what a student
-- legitimately edits -- display name, grade, the learning preferences --
-- while `role` is refused.
--
-- Self-service sign-up is unaffected: `handle_new_user` is SECURITY DEFINER
-- owned by postgres, so it bypasses these grants and still writes the role
-- the registration form chose. What changes is that the value can no longer
-- be edited afterwards by the account it describes.

-- UPDATE is the escalation path: PATCH /rest/v1/profiles?id=eq.<own id>.
REVOKE UPDATE ("role") ON TABLE "public"."profiles" FROM "anon";
REVOKE UPDATE ("role") ON TABLE "public"."profiles" FROM "authenticated";

-- INSERT is the same path wearing a hat: without this, a student could
-- DELETE their profile and re-insert it as a teacher. Revoked at the column,
-- the insert still succeeds and `role` takes its column DEFAULT ('student');
-- only naming the column is refused.
REVOKE INSERT ("role") ON TABLE "public"."profiles" FROM "anon";
REVOKE INSERT ("role") ON TABLE "public"."profiles" FROM "authenticated";

COMMENT ON COLUMN "public"."profiles"."role" IS
    'Server-controlled. Written once by handle_new_user at sign-up and by '
    'service_role; UPDATE/INSERT on this column are revoked from anon and '
    'authenticated, because three endpoints gate on it. The client-writable '
    'user_metadata.role is not a gate and must not be read as one.';

NOTIFY pgrst, 'reload schema';
