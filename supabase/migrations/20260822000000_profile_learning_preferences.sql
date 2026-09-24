-- Learning preferences on `profiles`, read and written via /api/profile/me.

-- -1 easier, 0 adaptive, +1 harder. A bias on the model's choice, not an
-- absolute difficulty: the ease-off rule can still override it.
ALTER TABLE "public"."profiles"
    ADD COLUMN IF NOT EXISTS "difficulty_bias" smallint NOT NULL DEFAULT 0;

-- Advisory: the session asks, it is never ended mid-question.
ALTER TABLE "public"."profiles"
    ADD COLUMN IF NOT EXISTS "session_duration_minutes" smallint NOT NULL DEFAULT 15;

-- An in-app dashboard banner, not a push notification.
ALTER TABLE "public"."profiles"
    ADD COLUMN IF NOT EXISTS "practice_reminders" boolean NOT NULL DEFAULT true;

-- Bounded here too: profiles has a FOR ALL own-row policy.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profiles_difficulty_bias_range') THEN
        ALTER TABLE "public"."profiles"
            ADD CONSTRAINT "profiles_difficulty_bias_range"
            CHECK ("difficulty_bias" BETWEEN -1 AND 1);
    END IF;
    -- Must match the endpoint's range, or a mismatch surfaces as a 500.
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'profiles_session_duration_range') THEN
        ALTER TABLE "public"."profiles"
            ADD CONSTRAINT "profiles_session_duration_range"
            CHECK ("session_duration_minutes" BETWEEN 5 AND 180);
    END IF;
END $$;

NOTIFY pgrst, 'reload schema';
