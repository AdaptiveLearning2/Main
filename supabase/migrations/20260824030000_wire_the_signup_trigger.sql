-- Create the trigger handle_new_user was written for, and backfill missing
-- profiles rows (_role gates on them). on conflict (id) do nothing makes a
-- second, hand-made trigger harmless.

DROP TRIGGER IF EXISTS "on_auth_user_created" ON "auth"."users";

CREATE TRIGGER "on_auth_user_created"
    AFTER INSERT ON "auth"."users"
    FOR EACH ROW EXECUTE FUNCTION "public"."handle_new_user"();

-- Role whitelist repeated: raw_user_meta_data is client-supplied.

INSERT INTO "public"."profiles" ("id", "display_name", "email", "role")
SELECT
    u."id",
    COALESCE(u."raw_user_meta_data"->>'display_name', split_part(u."email", '@', 1)),
    u."email",
    CASE
        WHEN u."raw_user_meta_data"->>'role' IN ('student', 'teacher', 'parent')
            THEN u."raw_user_meta_data"->>'role'
        ELSE 'student'
    END
FROM "auth"."users" u
WHERE NOT EXISTS (
    SELECT 1 FROM "public"."profiles" p WHERE p."id" = u."id"
)
ON CONFLICT ("id") DO NOTHING;

-- No UPDATE of existing rows: refreshing from sign-up metadata would demote admins.

NOTIFY pgrst, 'reload schema';
