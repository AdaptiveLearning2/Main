-- Create the trigger that `handle_new_user` was always written for, and backfill
-- the rows it should already have made.
--
-- 20260804000000 recorded the drift and deliberately did not fix it, because the
-- fix depended on something only the production database could answer: whether a
-- trigger had been made by hand in the dashboard. Its own words -- "no migration
-- in this repo creates a trigger on auth.users", and "confirmed empirically: a
-- user created through the admin API gets no profiles row".
--
-- It is now worth answering, because `profiles.role` stopped being decoration:
-- `_role` gates three endpoints on it and `_is_admin` reads it. A missing
-- profiles row is no longer a cosmetic gap, it is a teacher who cannot create a
-- class -- and, in the other direction, an administrator whose account works
-- only because someone remembered to run an INSERT by hand.
--
-- **Safe whether or not a hand-made trigger exists.** If one exists under this
-- name, the DROP replaces it with the version in source control. If one exists
-- under a different name, both fire and the second insert hits
-- `on conflict (id) do nothing`, so the row is written once either way. That
-- clause is what makes this migration idempotent against a database nobody can
-- fully describe. Check for a survivor after applying, and drop it if there is
-- one -- two triggers doing the same job is not harmful, but it is one more
-- thing the migrations do not describe:
--
--   SELECT tgname FROM pg_trigger
--   WHERE NOT tgisinternal AND tgrelid = 'auth.users'::regclass;

DROP TRIGGER IF EXISTS "on_auth_user_created" ON "auth"."users";

CREATE TRIGGER "on_auth_user_created"
    AFTER INSERT ON "auth"."users"
    FOR EACH ROW EXECUTE FUNCTION "public"."handle_new_user"();

-- ── backfill ────────────────────────────────────────────────────────────────
--
-- Anyone who signed up while nothing was wired has no profiles row, and now
-- needs one: `_profile` degrades to a student-shaped dict on a miss, so a
-- teacher in that state is refused their own classes with no error to read.
--
-- **The role whitelist is repeated here on purpose.** This reads the same
-- client-supplied `raw_user_meta_data` the trigger does, so a backfill that
-- trusted it would be exactly the escalation the trigger's whitelist exists to
-- prevent -- one INSERT, run once, granting whatever anyone typed at sign-up.
-- Kept as a duplicated CASE rather than a shared helper because a helper here
-- would be a third place to look, and this runs once.

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
