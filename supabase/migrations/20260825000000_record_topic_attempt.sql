-- One atomic statement for a per-topic attempt, instead of four round trips
-- with a lost-update race in the middle.
--
-- The previous version ran on every answer -- the hottest path in the
-- product -- as four sequential calls, the last two a read-modify-write with
-- no lock: two answers landing together could both read the same counts and
-- the second upsert would overwrite the first, silently losing an attempt
-- from the table the adaptive engine reads to choose what to serve next.
--
-- `ON CONFLICT DO UPDATE SET attempted_questions = <table>.attempted_questions
-- + 1` increments the stored value rather than one the client read a moment
-- ago, which removes the race rather than narrowing it.
--
-- The topic is still derived from the question row, never from the caller --
-- the client is trusted about correctness, but letting it also name the topic
-- would let a page credit one subject for work done in another.
--
-- Returns the topic name, or null when there's nothing to attribute the
-- answer to (an unknown question, or a subject with no `math_topics` row).
-- Null is distinguishable from an error, which raises.
--
-- The name rather than the id: the caller hands it straight to the page,
-- which keys its Topic Accuracy panel by name. Nothing holds an id-to-name
-- map, so returning the id would mean a second query to resolve it.
--
-- SECURITY INVOKER (the default): the backend calls it with the service-role
-- client, and as invoker the row-level policies still apply if a
-- lower-privileged role is ever granted EXECUTE.
--
-- Apply this before deploying the code that calls it. The caller swallows
-- exceptions here, since a failed topic lookup must not cost a student the
-- answer that's already recorded -- so against a database without this
-- function the call fails as PostgREST's PGRST202 and attribution quietly
-- stops.

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
    -- No question, no subject on it, or a subject that isn't a topic --
    -- nothing to attribute this to. Inventing a `math_topics` row here would
    -- put a subject in the table that the question generator can't pick from.
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
