-- `profiles.role` becomes server-controlled so it can gate endpoints. RLS
-- narrows rows, not columns; only a column grant can refuse `role`.
-- handle_new_user (SECURITY DEFINER, owned by postgres) bypasses these grants.

REVOKE UPDATE ("role") ON TABLE "public"."profiles" FROM "anon";
REVOKE UPDATE ("role") ON TABLE "public"."profiles" FROM "authenticated";

-- INSERT too, or a student could delete and re-insert as a teacher.
REVOKE INSERT ("role") ON TABLE "public"."profiles" FROM "anon";
REVOKE INSERT ("role") ON TABLE "public"."profiles" FROM "authenticated";

COMMENT ON COLUMN "public"."profiles"."role" IS
    'Server-controlled. Written once by handle_new_user at sign-up and by '
    'service_role; UPDATE/INSERT on this column are revoked from anon and '
    'authenticated, because three endpoints gate on it. The client-writable '
    'user_metadata.role is not a gate and must not be read as one.';

NOTIFY pgrst, 'reload schema';
