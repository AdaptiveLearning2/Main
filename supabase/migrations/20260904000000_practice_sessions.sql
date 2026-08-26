-- A self-study mode: a student picks topic(s), difficulty and grade and gets
-- AI-generated questions with no EEG/camera/biometric involvement at all.
--
-- Deliberately two new tables rather than a `session_type` flag on
-- `sessions`. `sessions` feeds `_close_session` (rollup, lifetime credit,
-- `chart_archive`, `session_alerts`), the stale sweep, `class_live`, and
-- streak crediting -- all built around a session that has real signal-bearing
-- data. Reusing it here would mean adding a conditional branch through every
-- one of those (chart rendering for signals that don't exist, a
-- `signals_missing` alert on a session that was never meant to have signals).
-- A separate, minimal table pair needs none of that, and is trivially
-- auditable as "not a session close site" -- see the note on
-- `end_practice_session` in main.py for why its source must not trip
-- `conftest.close_sites()`'s string scan.
--
-- Practice answers never feed `user_math_performance` / `record_topic_attempt`
-- either: that table drives the live adaptive engine's own topic/difficulty
-- selection, and an untimed, explicitly-picked practice answer biasing it
-- would be exactly the "mixed into live tracking" outcome this feature is
-- meant to avoid. Practice results live only here, summarised into
-- `topic_summary` at close.
CREATE TABLE IF NOT EXISTS "public"."practice_sessions" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "user_id" "uuid" NOT NULL,
    "mode" "text" NOT NULL,
    "topics" "text"[] NOT NULL,
    "difficulty" "text" NOT NULL,
    "grade_level" "text",
    "started_at" timestamp with time zone DEFAULT "now"() NOT NULL,
    "ended_at" timestamp with time zone,
    -- test mode: graded answers; flashcard mode: cards viewed (see
    -- practice_session_answers.correct below).
    "questions_answered" integer DEFAULT 0 NOT NULL,
    -- Stays 0 for flashcard sessions -- there is nothing to grade.
    "correct_answers" integer DEFAULT 0 NOT NULL,
    -- {topic: {attempted, correct}}, correct null for a flashcard-only topic.
    -- Computed once at close from practice_session_answers, so a page never
    -- has to walk every answer row to show a summary.
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

-- The read path is "this student's own sessions, most recent first" (practice
-- history), matching session_alerts' user-scoped index above it.
CREATE INDEX IF NOT EXISTS "practice_sessions_user_started_idx"
    ON "public"."practice_sessions" USING "btree" ("user_id", "started_at" DESC);

CREATE TABLE IF NOT EXISTS "public"."practice_session_answers" (
    "id" "uuid" DEFAULT "extensions"."uuid_generate_v4"() NOT NULL,
    "practice_session_id" "uuid" NOT NULL,
    "user_id" "uuid" NOT NULL,
    "question_id" "uuid",
    -- Resolved server-side from questions.subject, never trusted from the
    -- client -- same principle as _record_topic_attempt on the live path.
    -- Nullable: a question_id the lookup can't resolve (deleted row, bad id)
    -- still records the attempt/view, just outside topic_summary's per-topic
    -- breakdown, rather than being rejected or coerced into a fake topic.
    "topic" "text",
    "selected_index" integer,
    -- NULL means flashcard "viewed" (ungraded); true/false means test-mode
    -- graded. Never a bool default -- an ungraded view must not read as a
    -- wrong answer in topic_summary.
    "correct" boolean,
    "answered_at" timestamp with time zone DEFAULT "now"() NOT NULL
);

ALTER TABLE "public"."practice_session_answers" OWNER TO "postgres";

ALTER TABLE ONLY "public"."practice_session_answers"
    ADD CONSTRAINT "practice_session_answers_pkey" PRIMARY KEY ("id");

-- Cascade: an answer with no session to summarise it into is an orphan
-- nothing reads, same reasoning as session_alerts' FK.
ALTER TABLE ONLY "public"."practice_session_answers"
    ADD CONSTRAINT "practice_session_answers_session_fkey"
    FOREIGN KEY ("practice_session_id") REFERENCES "public"."practice_sessions"("id") ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS "practice_session_answers_session_idx"
    ON "public"."practice_session_answers" USING "btree" ("practice_session_id");

-- Grants: revoke before granting, since Supabase's ALTER DEFAULT PRIVILEGES
-- hands anon and authenticated every privilege by name before this runs.
-- Nothing is granted to a client role -- a student reaches this only through
-- the backend, which resolves ownership first; there is no PostgREST path to
-- either table. RLS is on with no policies as the second lock, same as
-- session_alerts.
REVOKE ALL ON TABLE "public"."practice_sessions" FROM "anon";
REVOKE ALL ON TABLE "public"."practice_sessions" FROM "authenticated";
GRANT ALL ON TABLE "public"."practice_sessions" TO "service_role";
ALTER TABLE "public"."practice_sessions" ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE "public"."practice_session_answers" FROM "anon";
REVOKE ALL ON TABLE "public"."practice_session_answers" FROM "authenticated";
GRANT ALL ON TABLE "public"."practice_session_answers" TO "service_role";
ALTER TABLE "public"."practice_session_answers" ENABLE ROW LEVEL SECURITY;

-- Mirrors bump_session_counters: an atomic increment rather than a
-- read-modify-write from Python, so two answers landing together cannot both
-- read the same count and drop one on write-back.
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
