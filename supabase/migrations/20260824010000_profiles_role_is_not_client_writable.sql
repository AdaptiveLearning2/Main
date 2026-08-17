-- `profiles.role` becomes server-controlled, so it can be trusted as a gate.
--
-- Three endpoints decide what a caller may do from their role: creating a
-- class, listing classes, and linking a child. All three read
-- `user_metadata.role`, which the client sets at sign-up and can rewrite at any
-- time with `supabase.auth.updateUser({ data: { role: 'teacher' } })` -- a call
-- that goes straight to GoTrue and never passes through the backend. A student
-- could therefore self-elevate and create classes.
--
-- **Switching those gates to `profiles.role` does not fix it on its own**, and
-- that is what this migration is for. `profiles` carries a `FOR ALL` own-row
-- policy and `authenticated` holds UPDATE, so a student reaches their own row
-- through PostgREST directly -- 20260822000000 already documents this for the
-- preference columns and bounds them with CHECKs. A CHECK cannot express "not
-- by you"; only the grant can.
--
-- So the column privilege is what makes the gate real. Postgres grants are
-- per-column for UPDATE and INSERT, so the row stays writable for the things a
-- student legitimately edits -- display name, grade, the three learning
-- preferences -- while `role` is refused with a permission error.
--
-- Self-service sign-up is unaffected and is still intentional: `handle_new_user`
-- is SECURITY DEFINER and owned by postgres, so it bypasses these grants and
-- keeps writing the role the registration form chose. What changes is that the
-- value can no longer be edited afterwards by the account it describes. The
-- backend writes with the service-role key and is likewise unaffected;
-- `UpdateProfileRequest` has no `role` field, so no endpoint here even offers
-- it.

-- UPDATE is the escalation path: PATCH /rest/v1/profiles?id=eq.<own id>.
REVOKE UPDATE ("role") ON TABLE "public"."profiles" FROM "anon";
REVOKE UPDATE ("role") ON TABLE "public"."profiles" FROM "authenticated";

-- INSERT is the same path wearing a hat. `authenticated` holds INSERT and the
-- own-row policy admits a row with their own id, so without this a student
-- could DELETE their profile and re-insert it as a teacher. Revoked at the
-- column, the insert still succeeds and `role` takes its column DEFAULT
-- ('student'); it is only naming the column that is refused.
REVOKE INSERT ("role") ON TABLE "public"."profiles" FROM "anon";
REVOKE INSERT ("role") ON TABLE "public"."profiles" FROM "authenticated";

COMMENT ON COLUMN "public"."profiles"."role" IS
    'Server-controlled. Written once by handle_new_user at sign-up and by '
    'service_role; UPDATE/INSERT on this column are revoked from anon and '
    'authenticated, because three endpoints gate on it. The client-writable '
    'user_metadata.role is not a gate and must not be read as one.';

NOTIFY pgrst, 'reload schema';
