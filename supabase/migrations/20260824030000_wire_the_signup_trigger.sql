-- Create the trigger that `handle_new_user` was always written for, and
-- backfill the rows it should already have made.
--
-- Now worth doing because `profiles.role` stopped being decoration: `_role`
-- gates three endpoints on it and `_is_admin` reads it. A missing profiles
-- row is no longer cosmetic -- it's a teacher who can't create a class, or an
-- administrator whose account only works because someone ran an INSERT by
-- hand.
--
-- Safe whether or not a hand-made trigger already exists under a different
-- name: `on conflict (id) do nothing` means the row is written once either
-- way, even if both triggers fire.

DROP TRIGGER IF EXISTS "on_auth_user_created" ON "auth"."users";

CREATE TRIGGER "on_auth_user_created"
    AFTER INSERT ON "auth"."users"
    FOR EACH ROW EXECUTE FUNCTION "public"."handle_new_user"();

-- Backfill: anyone who signed up while nothing was wired has no profiles row
-- and now needs one -- `_profile` degrades to a student-shaped dict on a
-- miss, so a teacher in that state is refused their own classes with no
-- error to read.
--
-- The role whitelist is repeated here on purpose: this reads the same
-- client-supplied `raw_user_meta_data` the trigger does, so trusting it here
-- would be the same escalation the trigger's whitelist exists to prevent.

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

-- Deliberately no UPDATE of existing rows. A profile that is already there may
-- have been edited since -- a display name changed, a role set by hand in the
-- SQL editor, an admin promoted -- and `raw_user_meta_data` still holds whatever
-- was typed at sign-up. Overwriting from it would silently demote every
-- administrator this series exists to create.

NOTIFY pgrst, 'reload schema';
