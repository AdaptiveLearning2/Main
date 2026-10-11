-- Today's counts for the admin console now include practice: graded practice answers, and a student
-- who only practised counts as active. `answers` stays adaptive-only so the two can be shown apart.
-- Same signature as before, so this replaces the function rather than adding an overload.

CREATE OR REPLACE FUNCTION "public"."admin_today"("p_since" timestamptz)
RETURNS "jsonb"
LANGUAGE "sql"
STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
    SELECT jsonb_build_object(
        'started',         (SELECT count(*) FROM sessions WHERE started_at >= p_since),
        'open_now',        (SELECT count(*) FROM sessions WHERE ended_at IS NULL),
        'ended_by_reason', (SELECT coalesce(jsonb_object_agg(reason, n), '{}'::jsonb)
                              FROM (SELECT coalesce(close_reason, 'unrecorded') AS reason, count(*) AS n
                                      FROM sessions WHERE ended_at >= p_since
                                     GROUP BY 1) r),
        'answers',         (SELECT count(*) FROM session_answers WHERE answered_at >= p_since),
        -- A null `correct` is a flashcard viewed, not an answer.
        'practice_answers', (SELECT count(*) FROM practice_session_answers
                              WHERE answered_at >= p_since AND correct IS NOT NULL),
        'active_students', (SELECT count(DISTINCT user_id) FROM (
                                SELECT user_id FROM sessions WHERE started_at >= p_since
                                UNION
                                SELECT user_id FROM session_answers WHERE answered_at >= p_since
                                UNION
                                SELECT user_id FROM practice_sessions WHERE started_at >= p_since
                                UNION
                                SELECT user_id FROM practice_session_answers WHERE answered_at >= p_since) a)
    );
$$;

REVOKE ALL ON FUNCTION "public"."admin_today"(timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."admin_today"(timestamptz) FROM "anon";
REVOKE ALL ON FUNCTION "public"."admin_today"(timestamptz) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."admin_today"(timestamptz) TO "service_role";

NOTIFY pgrst, 'reload schema';
