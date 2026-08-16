-- One atomic statement for a per-topic attempt, instead of four round trips
-- with a lost-update race in the middle of them.
--
-- `_record_topic_attempt` ran on every answer -- the hottest path in the
-- product -- and made four sequential calls: read the question's subject, look
-- the subject up in `math_topics`, read the student's existing row, then upsert
-- a row computed from it. The last two are a read-modify-write with no lock, so
-- two answers landing together both read the same counts and the second upsert
-- overwrote the first: attempts silently lost from the table the adaptive
-- engine reads to decide what to serve next.
--
-- `ON CONFLICT DO UPDATE SET attempted_questions = <table>.attempted_questions
-- + 1` increments the *stored* value rather than one the client read a moment
-- ago, which is what makes the race disappear rather than narrow.
--
-- The topic is still derived from the question row and never from the caller.
-- The client has to be trusted about whether it got the answer right; letting
-- it also name the topic would let a page credit one subject for work done in
-- another. Moving the join into SQL does not change who is trusted with what --
-- `p_question_id` names a question, and the subject comes off that row.
--
-- Returns the topic **name**, or NULL when there is nothing to attribute the
-- answer to -- an unknown question, or a subject with no `math_topics` row.
-- NULL is the same "recorded nothing, deliberately" that the Python it replaces
-- expressed by falling through, and it is distinguishable from an error, which
-- raises.
--
-- The name rather than the id, because the name is what the caller can use.
-- `/api/sessions/{id}/answer` hands it back to the page, which keys its Topic
-- Accuracy panel by name and would otherwise have to re-read the whole table
-- after every answer to find out which figure moved. Nothing anywhere holds an
-- id-to-name map, so returning the id would mean a second query to resolve it.
--
-- SECURITY INVOKER (the default): the backend calls it with the service-role
-- client, and as invoker the row-level policies still apply if a
-- lower-privileged role is ever granted EXECUTE. Nothing is granted one below.
--
-- **Apply this before deploying the code that calls it.** `_record_topic_attempt`
-- swallows its exceptions -- it must, because a failed topic lookup may not cost
-- a student the answer that is already recorded -- so against a database
-- without this function the call fails as PostgREST's PGRST202 and the symptom
-- is per-topic attribution quietly stopping. The caller logs that case by name
-- rather than letting it join the ordinary failures; see the note there.

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
    -- No question, no subject on it, or a subject that is not a topic. Nothing
    -- to attribute this to, and inventing a `math_topics` row here would put a
    -- subject in the table that the question generator cannot pick from.
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
