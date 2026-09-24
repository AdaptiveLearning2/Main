-- Self-study practice with no signal recording. Separate from `sessions`, whose
-- close sequence assumes signal data. Practice answers never feed
-- user_math_performance, which drives the adaptive engine.
CREATE TABLE IF NOT EXISTS "public"."practice_sessions" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "mode" "text" NOT NULL,
    "topics" "text"[] NOT NULL,
    "difficulty" "text" NOT NULL,
    "grade_level" "text",
    "started_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "ended_at" timestamp with time zone,
    -- test: graded answers; flashcard: cards viewed.
    "questions_answered" integer DEFAULT 0 NOT NULL,
    -- Stays 0 for flashcard sessions.
    "correct_answers" integer DEFAULT 0 NOT NULL,
    -- {topic: {attempted, correct}}, correct null for flashcard-only; set at close.
    "topic_summary" "jsonb" DEFAULT '{}'::"jsonb" NOT NULL,
    CONSTRAINT "practice_sessions_mode_check"
        CHECK ("mode" = ANY (ARRAY['flashcard'::"text", 'test'::"text"])),
    CONSTRAINT "practice_sessions_difficulty_check"
        CHECK ("difficulty" = ANY (ARRAY['easy'::"text", 'medium'::"text", 'hard'::"text"])),
    CONSTRAINT "practice_sessions_topics_not_empty" CHECK (array_length("topics", 1) > 0)
);

ALTER TABLE "public"."practice_sessions" OWNER TO "postgres";

ALTER TABLE ONLY "public"."practice_sessions"
    ADD CONSTRAINT "practice_sessions_pkey" PRIMARY KEY ("id");

CREATE INDEX IF NOT EXISTS "practice_sessions_user_started_idx"
    ON "public"."practice_sessions" USING "btree" ("user_id", "started_at" DESC);

CREATE TABLE IF NOT EXISTS "public"."practice_session_answers" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "practice_session_id" "uuid" NOT NULL,
    "user_id" "uuid" NOT NULL,
    "question_id" "uuid",
    -- From questions.subject server-side, never the client; null if unresolvable.
    "topic" "text",
    "selected_index" integer,
    -- NULL = flashcard viewed (ungraded); no default, or a view reads as wrong.
    "correct" boolean,
    "answered_at" timestamp with time zone DEFAULT "now"() NOT NULL
);

ALTER TABLE "public"."practice_session_answers" OWNER TO "postgres";

ALTER TABLE ONLY "public"."practice_session_answers"
    ADD CONSTRAINT "practice_session_answers_pkey" PRIMARY KEY ("id");

ALTER TABLE ONLY "public"."practice_session_answers"
    ADD CONSTRAINT "practice_session_answers_session_fkey"
    FOREIGN KEY ("practice_session_id") REFERENCES "public"."practice_sessions"("id") ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS "practice_session_answers_session_idx"
    ON "public"."practice_session_answers" USING "btree" ("practice_session_id");

-- No client grants: reached only through the backend. RLS with no policies.
REVOKE ALL ON TABLE "public"."practice_sessions" FROM "anon";
REVOKE ALL ON TABLE "public"."practice_sessions" FROM "authenticated";
GRANT ALL ON TABLE "public"."practice_sessions" TO "service_role";
ALTER TABLE "public"."practice_sessions" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."practice_session_answers" FROM "anon";
REVOKE ALL ON TABLE "public"."practice_session_answers" FROM "authenticated";
GRANT ALL ON TABLE "public"."practice_session_answers" TO "service_role";
ALTER TABLE "public"."practice_session_answers" ENABLE ROW LEVEL SECURITY;

-- Atomic increment, as in bump_session_counters.
CREATE OR REPLACE FUNCTION "public"."bump_practice_session_counters"(
  "p_session_id" "uuid",
  "p_graded" boolean,
  "p_correct" boolean
) RETURNS TABLE (
  "questions_answered" integer,
  "correct_answers" integer
)
LANGUAGE "sql"
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
  UPDATE "public"."practice_sessions"
     SET "questions_answered" = "practice_sessions"."questions_answered" + 1,
         "correct_answers"    = "practice_sessions"."correct_answers"
                                 + CASE WHEN p_graded AND p_correct THEN 1 ELSE 0 END
   WHERE "id" = p_session_id
  RETURNING "practice_sessions"."questions_answered", "practice_sessions"."correct_answers";
$$;

REVOKE ALL ON FUNCTION "public"."bump_practice_session_counters"("uuid", boolean, boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."bump_practice_session_counters"("uuid", boolean, boolean) FROM "anon";
REVOKE ALL ON FUNCTION "public"."bump_practice_session_counters"("uuid", boolean, boolean) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."bump_practice_session_counters"("uuid", boolean, boolean) TO "service_role";

NOTIFY pgrst, 'reload schema';
