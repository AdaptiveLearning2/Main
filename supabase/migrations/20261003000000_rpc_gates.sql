-- Three hot paths, one round trip each: the ingest gate, the viewer check and the answer write.
-- Each only fetches or writes; main.py still makes every decision and every security event.

-- The two rows an ingest request is gated on: the session (by id, whoever owns it) and the
-- caller's consent. An absent row is JSON null; ownership and _may_record stay in main.py.
CREATE OR REPLACE FUNCTION "public"."ingest_gate"(
    "p_session_id" "uuid",
    "p_user_id" "uuid"
) RETURNS jsonb
LANGUAGE "sql" STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
  SELECT jsonb_build_object(
    'session', (SELECT jsonb_build_object('user_id', s.user_id, 'started_at', s.started_at,
                                          'ended_at', s.ended_at)
                  FROM "public"."sessions" s WHERE s.id = p_session_id),
    'consent', (SELECT to_jsonb(c) FROM "public"."signal_consent" c
                 WHERE c.user_id = p_user_id LIMIT 1));
$$;

REVOKE ALL ON FUNCTION "public"."ingest_gate"("uuid", "uuid") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."ingest_gate"("uuid", "uuid") FROM "anon";
REVOKE ALL ON FUNCTION "public"."ingest_gate"("uuid", "uuid") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."ingest_gate"("uuid", "uuid") TO "service_role";

-- Which relationship lets p_viewer read p_student's records: teacher of a class the student is
-- in, linked parent, or admin; NULL for none. Self is decided in main.py, before this is asked.
CREATE OR REPLACE FUNCTION "public"."viewer_relationship"(
    "p_viewer" "uuid",
    "p_student" "uuid"
) RETURNS "text"
LANGUAGE "sql" STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
  SELECT CASE
    WHEN EXISTS (SELECT 1 FROM "public"."class_memberships" m
                   JOIN "public"."classes" c ON c.id = m.class_id
                  WHERE m.student_id = p_student AND c.teacher_id = p_viewer) THEN 'teacher'
    WHEN EXISTS (SELECT 1 FROM "public"."parent_child_links" l
                  WHERE l.parent_id = p_viewer AND l.child_id = p_student) THEN 'parent'
    WHEN EXISTS (SELECT 1 FROM "public"."profiles" p
                  WHERE p.id = p_viewer AND p.role = 'admin') THEN 'admin'
  END;
$$;

REVOKE ALL ON FUNCTION "public"."viewer_relationship"("uuid", "uuid") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."viewer_relationship"("uuid", "uuid") FROM "anon";
REVOKE ALL ON FUNCTION "public"."viewer_relationship"("uuid", "uuid") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."viewer_relationship"("uuid", "uuid") TO "service_role";

-- An answer, checked and written under one row lock: nothing is written to a session that is
-- missing, someone else's or ended, and a close cannot land between the check and the write.
-- FOR NO KEY UPDATE, not FOR SHARE: two answers at once would each hold SHARE, then deadlock.
CREATE OR REPLACE FUNCTION "public"."record_answer"(
    "p_session_id" "uuid",
    "p_user_id" "uuid",
    "p_question_id" "uuid",
    "p_selected_index" integer,
    "p_correct" boolean,
    "p_answered_at" timestamptz
) RETURNS jsonb
LANGUAGE "plpgsql"
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
DECLARE
    v_owner uuid;
    v_ended timestamptz;
    v_topic text;
BEGIN
    SELECT s.user_id, s.ended_at INTO v_owner, v_ended
      FROM "public"."sessions" s
     WHERE s.id = p_session_id
       FOR NO KEY UPDATE;
    IF NOT FOUND THEN
        RETURN jsonb_build_object('status', 'not_found');
    END IF;
    IF v_owner IS DISTINCT FROM p_user_id THEN
        -- The owner names the subject of main.py's authz_denied event.
        RETURN jsonb_build_object('status', 'forbidden', 'owner', v_owner);
    END IF;
    IF v_ended IS NOT NULL THEN
        RETURN jsonb_build_object('status', 'ended');
    END IF;

    INSERT INTO "public"."session_answers"
        ("session_id", "user_id", "question_id", "selected_index", "correct", "answered_at")
    VALUES (p_session_id, p_user_id, p_question_id, p_selected_index, p_correct, p_answered_at);

    UPDATE "public"."sessions"
       SET "questions_answered" = "questions_answered" + 1,
           "correct_answers"    = "correct_answers" + CASE WHEN p_correct THEN 1 ELSE 0 END
     WHERE "id" = p_session_id;

    -- Attribution is best effort, as it was in main.py: its failure must not undo the answer.
    BEGIN
        v_topic := "public"."record_topic_attempt"(p_user_id, p_question_id, p_correct);
    EXCEPTION WHEN OTHERS THEN
        RAISE WARNING 'record_answer: no topic attempt for question %: %', p_question_id, SQLERRM;
        v_topic := NULL;
    END;
    RETURN jsonb_build_object('status', 'ok', 'topic', v_topic);
END;
$$;

REVOKE ALL ON FUNCTION "public"."record_answer"("uuid", "uuid", "uuid", integer, boolean, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."record_answer"("uuid", "uuid", "uuid", integer, boolean, timestamptz) FROM "anon";
REVOKE ALL ON FUNCTION "public"."record_answer"("uuid", "uuid", "uuid", integer, boolean, timestamptz) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."record_answer"("uuid", "uuid", "uuid", integer, boolean, timestamptz) TO "service_role";

NOTIFY pgrst, 'reload schema';
