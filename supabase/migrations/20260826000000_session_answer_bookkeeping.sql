-- Two small functions for the counters an answer moves.
--
-- ── bump_session_counters ───────────────────────────────────────────────────
--
-- `record_answer` bumped `sessions.questions_answered` by reading the row and
-- writing back `read + 1`. That is the same lost-update race `record_topic_attempt`
-- (20260825000000) was written to remove, one function away and left in place:
-- two answers landing together both read the same count and the second write
-- overwrites the first, so a student answers ten questions and the session says
-- nine. Incrementing the *stored* value removes it rather than narrowing it.
--
-- It also drops a round trip. The read existed only to compute the new values,
-- and the caller already reads the session for the ownership check.
--
-- Returns the row so the caller can still see what the counters became.
--
-- ── session_answer_counts ───────────────────────────────────────────────────
--
-- What a closing session actually answered, as two integers rather than as
-- every answer row. `_answer_counts` fetched up to 2000 `correct` values and
-- summed them in Python, which worked but meant carrying a cap -- and the cap
-- needed its own fallback branch, because a capped count is wrong rather than
-- merely slow. Counting in SQL has no cap to reason about, so the branch and
-- the constant both go.
--
-- Kept separate from the bump on purpose: they run at different times, on
-- different paths, and folding them together would put session-close logic in
-- the hot answer path.
--
-- Both are SECURITY INVOKER (the default) and granted only to service_role.

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
