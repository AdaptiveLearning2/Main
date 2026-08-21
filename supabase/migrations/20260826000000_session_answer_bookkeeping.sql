-- Two small functions for the counters an answer moves.
--
-- bump_session_counters: `record_answer` used to bump
-- `sessions.questions_answered` by reading the row and writing back `read +
-- 1` -- the same lost-update race `record_topic_attempt` was written to
-- remove, left in place here: two answers landing together could both read
-- the same count and the second write would overwrite the first. Incrementing
-- the stored value removes the race rather than narrowing it, and drops a
-- round trip, since the caller already reads the session for the ownership
-- check.
--
-- session_answer_counts: what a closing session actually answered, as two
-- integers rather than every answer row. The Python version fetched up to
-- 2000 rows and summed them, which needed a cap and a fallback branch for
-- when the cap was hit. Counting in SQL has no cap to reason about.
--
-- Kept separate from the bump on purpose: they run at different times, on
-- different paths, and folding them together would put session-close logic
-- in the hot answer path.

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
