-- One atomic per-topic attempt: ON CONFLICT increments the stored value, so
-- concurrent answers cannot lose an update. The topic comes from the question
-- row, never the caller. Returns the topic name, or null if unattributable.
-- Apply before deploying the caller: it swallows PGRST202, so attribution
-- would stop silently.

CREATE OR REPLACE FUNCTION "public"."record_topic_attempt"(
  "p_user_id" "uuid",
  "p_question_id" "uuid",
  "p_correct" boolean
) RETURNS "text"
LANGUAGE "plpgsql"
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
DECLARE
  v_topic_id   integer;
  v_topic_name text;
BEGIN
  SELECT t."id", t."topic_name" INTO v_topic_id, v_topic_name
  FROM "public"."questions" q
  JOIN "public"."math_topics" t ON t."topic_name" = q."subject"
  WHERE q."id" = p_question_id;

  IF v_topic_id IS NULL THEN
    -- Never invent a math_topics row the generator cannot pick from.
    RETURN NULL;
  END IF;

  INSERT INTO "public"."user_math_performance"
    ("user_id", "topic_id", "attempted_questions", "correct_questions", "updated_at")
  VALUES
    (p_user_id, v_topic_id, 1, CASE WHEN p_correct THEN 1 ELSE 0 END, "now"())
  ON CONFLICT ("user_id", "topic_id") DO UPDATE
    SET "attempted_questions" = "public"."user_math_performance"."attempted_questions" + 1,
        "correct_questions"   = "public"."user_math_performance"."correct_questions"
                                + CASE WHEN p_correct THEN 1 ELSE 0 END,
        "updated_at"          = "now"();

  RETURN v_topic_name;
END;
$$;

REVOKE ALL ON FUNCTION "public"."record_topic_attempt"("uuid", "uuid", boolean) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."record_topic_attempt"("uuid", "uuid", boolean) FROM "anon";
REVOKE ALL ON FUNCTION "public"."record_topic_attempt"("uuid", "uuid", boolean) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."record_topic_attempt"("uuid", "uuid", boolean) TO "service_role";

NOTIFY pgrst, 'reload schema';
