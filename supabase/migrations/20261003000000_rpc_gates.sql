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
-- Text, so a path id that is no uuid is "no relationship" (a 403), not a cast error (a 503).
CREATE OR REPLACE FUNCTION "public"."viewer_relationship"(
    "p_viewer" "text",
    "p_student" "text"
) RETURNS "text"
LANGUAGE "plpgsql" STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
DECLARE
    v_viewer uuid;
    v_student uuid;
BEGIN
    -- Checked before any cast is planned: a plan can fold a constant cast even in a branch not taken.
    IF NOT (pg_input_is_valid(p_viewer, 'uuid') AND pg_input_is_valid(p_student, 'uuid')) THEN
        RETURN NULL;
    END IF;
    v_viewer := p_viewer::uuid;
    v_student := p_student::uuid;
    RETURN CASE
      WHEN EXISTS (SELECT 1 FROM "public"."class_memberships" m
                     JOIN "public"."classes" c ON c.id = m.class_id
                    WHERE m.student_id = v_student AND c.teacher_id = v_viewer) THEN 'teacher'
      WHEN EXISTS (SELECT 1 FROM "public"."parent_child_links" l
                    WHERE l.parent_id = v_viewer AND l.child_id = v_student) THEN 'parent'
      WHEN EXISTS (SELECT 1 FROM "public"."profiles" p
                    WHERE p.id = v_viewer AND p.role = 'admin') THEN 'admin'
    END;
END;
$$;

REVOKE ALL ON FUNCTION "public"."viewer_relationship"("text", "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."viewer_relationship"("text", "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."viewer_relationship"("text", "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."viewer_relationship"("text", "text") TO "service_role";

-- An answer, checked and written under one row lock: nothing is written to a session that is
-- missing, someone else's or ended, and a close cannot land between the check and the write.
-- FOR NO KEY UPDATE, not FOR SHARE (two answers would deadlock); scripts/assert_answer_lock.sql.
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
    v_topic_error text;
    v_counters_error text;
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

    -- Each block is a subtransaction, best effort as in main.py: a failure is returned as
    -- 'SQLSTATE: message' (42883 = function missing) and never undoes the answer.
    BEGIN
        PERFORM "public"."bump_session_counters"(p_session_id, p_correct);
    EXCEPTION WHEN OTHERS THEN
        v_counters_error := SQLSTATE || ': ' || SQLERRM;
    END;
    BEGIN
        v_topic := "public"."record_topic_attempt"(p_user_id, p_question_id, p_correct);
    EXCEPTION WHEN OTHERS THEN
        v_topic := NULL;
        v_topic_error := SQLSTATE || ': ' || SQLERRM;
    END;
    RETURN jsonb_build_object('status', 'ok', 'topic', v_topic, 'topic_error', v_topic_error,
                              'counters_error', v_counters_error);
END;
$$;

REVOKE ALL ON FUNCTION "public"."record_answer"("uuid", "uuid", "uuid", integer, boolean, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."record_answer"("uuid", "uuid", "uuid", integer, boolean, timestamptz) FROM "anon";
REVOKE ALL ON FUNCTION "public"."record_answer"("uuid", "uuid", "uuid", integer, boolean, timestamptz) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."record_answer"("uuid", "uuid", "uuid", integer, boolean, timestamptz) TO "service_role";

NOTIFY pgrst, 'reload schema';
