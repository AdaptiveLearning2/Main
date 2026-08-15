-- Learning preferences, moved out of localStorage and onto the profile.
--
-- The Preferences tab wrote `al_prefs` to localStorage and nothing anywhere
-- read it back -- not the adaptive engine, not the session, not any reminder.
-- Three controls that looked like settings and were decoration, which is what
-- retired the Devices tab's Connect buttons.
--
-- On `profiles` rather than in a table of their own: these are a handful of
-- scalars about one person, `profiles` already carries `grade_level` (which is
-- the same kind of thing and already drives generation), and `/api/profile/me`
-- already reads and writes it. A separate table would add a join, a policy set
-- and a grant block to store three columns.

-- Starting value of the Easier/Auto/Harder control on the practice page, which
-- already reaches the LLM decider. -1 easier, 0 adaptive, +1 harder.
--
-- Deliberately a *bias*, not an absolute difficulty. `_shift_difficulty` applies
-- it on top of whatever the model chose from the student's accuracy history, and
-- `LLM_topic_decider` overrides it downward whenever the fused signal says
-- stressed. Storing "always hard" would be storing a value that the ease-off
-- rule has to contradict, and a setting the system routinely ignores is worse
-- than one that does not exist.
ALTER TABLE "public"."profiles"
    ADD COLUMN IF NOT EXISTS "difficulty_bias" smallint NOT NULL DEFAULT 0;

-- How long a sitting is meant to last. Advisory: the session is not ended for
-- the student, it asks. Ending one mid-question because a timer expired would
-- discard an answer a child was part way through giving.
ALTER TABLE "public"."profiles"
    ADD COLUMN IF NOT EXISTS "session_duration_minutes" smallint NOT NULL DEFAULT 15;

-- Whether to show the practice nudge on the dashboard. Named for what it is: a
-- banner inside the app, not a notification that reaches a closed browser.
ALTER TABLE "public"."profiles"
    ADD COLUMN IF NOT EXISTS "practice_reminders" boolean NOT NULL DEFAULT true;

-- Bounded in the database as well as in the endpoint. The endpoint is the only
-- writer today, but `profiles` carries a FOR ALL own-row policy, so a student
-- reaches this table through PostgREST directly -- a bias of 7 would shift
-- `_shift_difficulty` off the end of DIFFS on every question they are served.
-- It clamps, so the effect is "always hard" rather than a crash: a wrong value
-- that works is the kind this has to stop at the boundary.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profiles_difficulty_bias_range') THEN
        ALTER TABLE "public"."profiles"
            ADD CONSTRAINT "profiles_difficulty_bias_range"
            CHECK ("difficulty_bias" BETWEEN -1 AND 1);
    END IF;
    -- A range, not the four values the UI offers. The endpoint validates the
    -- same range, and the two have to agree: a constraint listing exactly the
    -- current buttons turns a fifth button into a migration, and turns any
    -- disagreement into a 500 from the client library instead of a 422 that
    -- names the field. The database's job here is to refuse the absurd.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profiles_session_duration_range') THEN
        ALTER TABLE "public"."profiles"
            ADD CONSTRAINT "profiles_session_duration_range"
            CHECK ("session_duration_minutes" BETWEEN 5 AND 180);
    END IF;
END $$;

-- No grant block. These are columns on an existing table, and `GRANT` is
-- per-table rather than per-column here -- `profiles` already has the grants it
-- is meant to have, and re-granting would only risk widening them.

NOTIFY pgrst, 'reload schema';
