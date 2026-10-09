-- Why each adaptive session ended, written by `_close_session` in the same claim as `ended_at`.
-- Null means it ended before this column existed: never back-filled, since nothing recorded it.

ALTER TABLE "public"."sessions"
    ADD COLUMN IF NOT EXISTS "close_reason" text
        CHECK ("close_reason" IN (
            'finish',       -- the student pressed Finish
            'sign_out',     -- the student signed out mid-lesson
            'page_closed',  -- the lesson page's pagehide (tab closed, reloaded, navigated away)
            'student',      -- /end from a client that names no reason
            'superseded',   -- start_session closed a quiet earlier session of the same student
            'live_stale',   -- class_live closed a sensed session gone quiet
            'sweep'         -- the background sweep closed an abandoned session
        ));


-- Today's counts for the admin console, from the school day's start: counts only, no student.
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
        'active_students', (SELECT count(DISTINCT user_id) FROM (
                                SELECT user_id FROM sessions WHERE started_at >= p_since
                                UNION
                                SELECT user_id FROM session_answers WHERE answered_at >= p_since) a)
    );
$$;

REVOKE ALL ON FUNCTION "public"."admin_today"(timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."admin_today"(timestamptz) FROM "anon";
REVOKE ALL ON FUNCTION "public"."admin_today"(timestamptz) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."admin_today"(timestamptz) TO "service_role";

NOTIFY pgrst, 'reload schema';
