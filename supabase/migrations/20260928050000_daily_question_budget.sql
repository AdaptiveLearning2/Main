-- Questions served per student per school day. In the database, not the backend's memory,
-- so the budget survives a restart and is one count across workers.

CREATE TABLE IF NOT EXISTS "public"."daily_question_usage" (
    "user_id" "uuid" NOT NULL,
    -- The school's calendar day, computed by the backend in the school's timezone.
    "day" "date" NOT NULL,
    "served" integer NOT NULL DEFAULT 0 CHECK ("served" >= 0),
    CONSTRAINT "daily_question_usage_pkey" PRIMARY KEY ("user_id", "day")
);

ALTER TABLE "public"."daily_question_usage" OWNER TO "postgres";

ALTER TABLE ONLY "public"."daily_question_usage"
    ADD CONSTRAINT "daily_question_usage_user_id_fkey"
    FOREIGN KEY ("user_id") REFERENCES "public"."profiles"("id") ON DELETE CASCADE;

-- RLS with no policies plus revokes (RLS never filters TRUNCATE). Backend only.
ALTER TABLE "public"."daily_question_usage" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."daily_question_usage" FROM "anon";
REVOKE ALL ON TABLE "public"."daily_question_usage" FROM "authenticated";
GRANT ALL ON TABLE "public"."daily_question_usage" TO "service_role";

-- One statement: two requests at once cannot both take the last question.
CREATE OR REPLACE FUNCTION "public"."claim_daily_question"("p_user_id" "uuid", "p_day" "date", "p_limit" integer)
RETURNS boolean
LANGUAGE "sql"
SECURITY INVOKER
AS $$
    WITH claimed AS (
        INSERT INTO "public"."daily_question_usage" ("user_id", "day", "served")
        SELECT "p_user_id", "p_day", 1 WHERE "p_limit" >= 1
        ON CONFLICT ("user_id", "day") DO UPDATE
            SET "served" = "daily_question_usage"."served" + 1
            WHERE "daily_question_usage"."served" < "p_limit"
        RETURNING 1
    )
    SELECT EXISTS (SELECT 1 FROM claimed);
$$;

ALTER FUNCTION "public"."claim_daily_question"("uuid", "date", integer) OWNER TO "postgres";

REVOKE ALL ON FUNCTION "public"."claim_daily_question"("uuid", "date", integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."claim_daily_question"("uuid", "date", integer) FROM "anon";
REVOKE ALL ON FUNCTION "public"."claim_daily_question"("uuid", "date", integer) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."claim_daily_question"("uuid", "date", integer) TO "service_role";

-- Gives back a claim whose question never reached the student (the model was down, the server busy).
CREATE OR REPLACE FUNCTION "public"."release_daily_question"("p_user_id" "uuid", "p_day" "date")
RETURNS void
LANGUAGE "sql"
SECURITY INVOKER
AS $$
    UPDATE "public"."daily_question_usage" SET "served" = "served" - 1
    WHERE "user_id" = "p_user_id" AND "day" = "p_day" AND "served" > 0;
$$;

ALTER FUNCTION "public"."release_daily_question"("uuid", "date") OWNER TO "postgres";

REVOKE ALL ON FUNCTION "public"."release_daily_question"("uuid", "date") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."release_daily_question"("uuid", "date") FROM "anon";
REVOKE ALL ON FUNCTION "public"."release_daily_question"("uuid", "date") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."release_daily_question"("uuid", "date") TO "service_role";

-- A count per day is only ever read for today; keep a week for anyone checking a complaint.
SELECT "cron"."schedule"(
    'expire-daily-question-usage', '40 3 * * *',
    $$DELETE FROM "public"."daily_question_usage" WHERE "day" < CURRENT_DATE - 7$$
);

NOTIFY pgrst, 'reload schema';
