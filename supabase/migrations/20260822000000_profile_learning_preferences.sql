-- Learning preferences, moved out of localStorage and onto the profile. The
-- Preferences tab wrote `al_prefs` to localStorage and nothing read it back --
-- these three controls looked like settings and were decoration.
--
-- Kept on `profiles` rather than a table of their own: these are a handful of
-- scalars about one person, `profiles` already carries `grade_level` (the
-- same kind of thing) and `/api/profile/me` already reads and writes it.

-- Starting value of the Easier/Auto/Harder control on the practice page, which
-- already reaches the LLM decider. -1 easier, 0 adaptive, +1 harder.
--
-- Deliberately a bias, not an absolute difficulty. `_shift_difficulty` applies
-- it on top of what the model already chose from accuracy history, and
-- `LLM_topic_decider` overrides it downward whenever the fused signal says
-- stressed. Storing "always hard" would store a value the ease-off rule has
-- to contradict.
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

-- Bounded in the database too, not just the endpoint: `profiles` carries a
-- FOR ALL own-row policy, so a student can reach this table directly through
-- PostgREST -- a bias of 7 would shift `_shift_difficulty` off the end of
-- DIFFS on every question served.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profiles_difficulty_bias_range') THEN
        ALTER TABLE "public"."profiles"
            ADD CONSTRAINT "profiles_difficulty_bias_range"
            CHECK ("difficulty_bias" BETWEEN -1 AND 1);
    END IF;
    -- A range, not the four values the UI offers, so a fifth button doesn't
    -- need a migration. The endpoint validates the same range; disagreement
    -- between the two would surface as a 500 instead of a 422.
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
