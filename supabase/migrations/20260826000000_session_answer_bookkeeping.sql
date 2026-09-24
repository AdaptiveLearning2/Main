-- bump_session_counters increments the stored counts (no lost update).
-- session_answer_counts counts a closing session's answers in SQL, uncapped.
-- Separate so session-close logic stays out of the hot answer path.

CREATE OR REPLACE FUNCTION "public"."bump_session_counters"(
  "p_session_id" "uuid",
  "p_correct" boolean
) RETURNS TABLE (
  "questions_answered" integer,
  "correct_answers" integer
)
LANGUAGE "sql"
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
  UPDATE "public"."sessions"
     SET "questions_answered" = "sessions"."questions_answered" + 1,
         "correct_answers"    = "sessions"."correct_answers"
                                + CASE WHEN p_correct THEN 1 ELSE 0 END
   WHERE "id" = p_session_id
  RETURNING "sessions"."questions_answered", "sessions"."correct_answers";
$$;

CREATE OR REPLACE FUNCTION "public"."session_answer_counts"(
  "p_session_id" "uuid"
) RETURNS TABLE (
  "total" bigint,
  "correct" bigint
)
LANGUAGE "sql"
STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
  SELECT count(*),
         count(*) FILTER (WHERE "correct")
    FROM "public"."session_answers"
   WHERE "session_id" = p_session_id;
$$;

REVOKE ALL ON FUNCTION "public"."bump_session_counters"("uuid", boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."bump_session_counters"("uuid", boolean) FROM "anon";
REVOKE ALL ON FUNCTION "public"."bump_session_counters"("uuid", boolean) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."bump_session_counters"("uuid", boolean) TO "service_role";

REVOKE ALL ON FUNCTION "public"."session_answer_counts"("uuid") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."session_answer_counts"("uuid") FROM "anon";
REVOKE ALL ON FUNCTION "public"."session_answer_counts"("uuid") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."session_answer_counts"("uuid") TO "service_role";

NOTIFY pgrst, 'reload schema';
