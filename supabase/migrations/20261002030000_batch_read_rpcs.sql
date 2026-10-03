-- Read functions for the batch endpoints. Each answers in one round trip what the backend
-- read with one query per session, per student or per row cap. SECURITY INVOKER and
-- service_role only: the backend resolves who may see the student before calling.

-- One student's signals for the weekly report, aggregated per school day, uncapped: the
-- 1000-row PostgREST ceiling cut the raw read to the newest ~17 minutes of EEG. Counts and
-- sums, not means, so the backend can weight days; trusted rows only where the rollup is.
CREATE OR REPLACE FUNCTION "public"."weekly_signal_days"(
    "p_student_id" "uuid",
    "p_channel" "text",
    "p_since" timestamptz,
    "p_timezone" "text"
) RETURNS jsonb
LANGUAGE "plpgsql" STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
DECLARE
    days   jsonb;
    latest jsonb;
BEGIN
    IF p_channel = 'cognitive' THEN
        SELECT coalesce(jsonb_agg(d ORDER BY d.day), '[]'::jsonb) INTO days FROM (
            SELECT (ts AT TIME ZONE p_timezone)::date AS day,
                   count(*) AS rows,
                   count(focus) AS focus_n, sum(focus) AS focus_sum, min(focus) AS focus_min,
                   count(stress) AS stress_n, sum(stress) AS stress_sum, max(stress) AS stress_max
              FROM cognitive_signals
             WHERE user_id = p_student_id AND ts >= p_since
             GROUP BY 1) d;
        -- Newest row with a measurement, else the newest: all-unusable reads "Calibrating".
        latest := coalesce(
            (SELECT jsonb_build_object('ts', c.ts, 'focus', c.focus, 'stress', c.stress,
                                       'engagement', c.engagement)
               FROM cognitive_signals c
              WHERE c.user_id = p_student_id AND c.ts >= p_since AND c.focus IS NOT NULL
              ORDER BY c.ts DESC LIMIT 1),
            (SELECT jsonb_build_object('ts', c.ts, 'focus', c.focus, 'stress', c.stress,
                                       'engagement', c.engagement)
               FROM cognitive_signals c
              WHERE c.user_id = p_student_id AND c.ts >= p_since
              ORDER BY c.ts DESC LIMIT 1));
    ELSIF p_channel = 'emotion' THEN
        -- One pass: group by day and trusted label (NULL for the rest), then fold each day.
        -- emotion_counts sums to trusted_emotion_rows, so shares of it total 100%.
        SELECT coalesce(jsonb_agg(d ORDER BY d.day), '[]'::jsonb) INTO days FROM (
            SELECT g.day, sum(g.n)::bigint AS rows,
                   coalesce(sum(g.n) FILTER (WHERE g.label IS NOT NULL), 0)::bigint
                       AS trusted_emotion_rows,
                   sum(g.attention_n)::bigint AS attention_n, sum(g.attention_sum) AS attention_sum,
                   coalesce(jsonb_object_agg(g.label, g.n) FILTER (WHERE g.label IS NOT NULL),
                            '{}'::jsonb) AS emotion_counts
              FROM (SELECT (ts AT TIME ZONE p_timezone)::date AS day,
                           CASE WHEN emotion_trusted THEN emotion END AS label,
                           count(*) AS n,
                           count(attention) AS attention_n, sum(attention) AS attention_sum
                      FROM face_signals
                     WHERE user_id = p_student_id AND ts >= p_since
                     GROUP BY 1, 2) g
             GROUP BY g.day) d;
        latest := coalesce(
            (SELECT jsonb_build_object('ts', f.ts, 'emotion', f.emotion)
               FROM face_signals f
              WHERE f.user_id = p_student_id AND f.ts >= p_since AND f.emotion IS NOT NULL
              ORDER BY f.ts DESC LIMIT 1),
            (SELECT jsonb_build_object('ts', f.ts, 'emotion', f.emotion)
               FROM face_signals f
              WHERE f.user_id = p_student_id AND f.ts >= p_since
              ORDER BY f.ts DESC LIMIT 1));
    ELSIF p_channel = 'heart' THEN
        SELECT coalesce(jsonb_agg(d ORDER BY d.day), '[]'::jsonb) INTO days FROM (
            SELECT (ts AT TIME ZONE p_timezone)::date AS day,
                   count(*) AS rows,
                   count(*) FILTER (WHERE trusted) AS trusted_rows,
                   count(heart_rate_bpm) FILTER (WHERE trusted) AS bpm_n,
                   sum(heart_rate_bpm) FILTER (WHERE trusted) AS bpm_sum,
                   count(rmssd_ms) FILTER (WHERE trusted) AS rmssd_n,
                   sum(rmssd_ms) FILTER (WHERE trusted) AS rmssd_sum,
                   coalesce(array_agg(DISTINCT source) FILTER (WHERE trusted AND source IS NOT NULL),
                            '{}') AS sources
              FROM heart_signals
             WHERE user_id = p_student_id AND ts >= p_since
             GROUP BY 1) d;
        -- Trusted only, as every other heart figure in the report.
        SELECT jsonb_build_object('ts', h.ts, 'heart_rate_bpm', h.heart_rate_bpm,
                                  'rmssd_ms', h.rmssd_ms, 'source', h.source,
                                  'trusted', h.trusted) INTO latest
          FROM heart_signals h
         WHERE h.user_id = p_student_id AND h.ts >= p_since AND h.trusted
         ORDER BY h.ts DESC LIMIT 1;
    ELSE
        RAISE EXCEPTION 'weekly_signal_days: unknown channel %', p_channel;
    END IF;
    RETURN jsonb_build_object('days', days, 'latest', latest);
END;
$$;

REVOKE ALL ON FUNCTION "public"."weekly_signal_days"("uuid", "text", timestamptz, "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."weekly_signal_days"("uuid", "text", timestamptz, "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."weekly_signal_days"("uuid", "text", timestamptz, "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."weekly_signal_days"("uuid", "text", timestamptz, "text") TO "service_role";

-- Each student's newest sessions, at most p_limit (1..50) each: "top N per student" has no
-- PostgREST form, so the Sessions page read them one student at a time. Rows reach a browser,
-- so the columns are main.py's _SESSION_CLIENT_COLUMNS (test_batch_read_rpcs.py), no chart_paths.
CREATE OR REPLACE FUNCTION "public"."recent_sessions_for_users"(
    "p_user_ids" "uuid"[],
    "p_limit" integer
) RETURNS TABLE ("id" "uuid", "user_id" "uuid", "class_id" "uuid", "title" "text",
                 "started_at" timestamptz, "ended_at" timestamptz,
                 "questions_answered" integer, "correct_answers" integer)
LANGUAGE "sql" STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
  SELECT s.*
    FROM (SELECT DISTINCT u.id FROM unnest(p_user_ids) AS u(id)) ids
    CROSS JOIN LATERAL (
      SELECT x.id, x.user_id, x.class_id, x.title, x.started_at, x.ended_at,
             x.questions_answered, x.correct_answers
        FROM "public"."sessions" x
       WHERE x.user_id = ids.id
       ORDER BY x.started_at DESC
       LIMIT LEAST(GREATEST(p_limit, 1), 50)) s;
$$;

REVOKE ALL ON FUNCTION "public"."recent_sessions_for_users"("uuid"[], integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."recent_sessions_for_users"("uuid"[], integer) FROM "anon";
REVOKE ALL ON FUNCTION "public"."recent_sessions_for_users"("uuid"[], integer) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."recent_sessions_for_users"("uuid"[], integer) TO "service_role";

-- Each session's newest *measured* activity: an answer, or a signal row with a measurement
-- (a poor-contact row is all null). Same rule as main.py's _ACTIVITY_SOURCES, held equal to it
-- by backend/tests/test_batch_read_rpcs.py.
CREATE OR REPLACE FUNCTION "public"."last_activity_for_sessions"("p_session_ids" "uuid"[])
RETURNS TABLE ("session_id" "uuid", "last_activity_at" timestamptz)
LANGUAGE "sql" STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
  SELECT ids.id, GREATEST(a.t, c.t, f.t, h.t)
    FROM (SELECT DISTINCT u.id FROM unnest(p_session_ids) AS u(id)) ids
    LEFT JOIN LATERAL (SELECT max(x.answered_at) AS t FROM "public"."session_answers" x
                        WHERE x.session_id = ids.id) a ON true
    LEFT JOIN LATERAL (SELECT x.ts AS t FROM "public"."cognitive_signals" x
                        WHERE x.session_id = ids.id AND x.focus IS NOT NULL
                        ORDER BY x.ts DESC LIMIT 1) c ON true
    LEFT JOIN LATERAL (SELECT x.ts AS t FROM "public"."face_signals" x
                        WHERE x.session_id = ids.id
                          AND (x.emotion IS NOT NULL OR x.gaze_x IS NOT NULL OR x.head_yaw IS NOT NULL)
                        ORDER BY x.ts DESC LIMIT 1) f ON true
    LEFT JOIN LATERAL (SELECT x.ts AS t FROM "public"."heart_signals" x
                        WHERE x.session_id = ids.id AND x.heart_rate_bpm IS NOT NULL
                        ORDER BY x.ts DESC LIMIT 1) h ON true;
$$;

REVOKE ALL ON FUNCTION "public"."last_activity_for_sessions"("uuid"[]) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."last_activity_for_sessions"("uuid"[]) FROM "anon";
REVOKE ALL ON FUNCTION "public"."last_activity_for_sessions"("uuid"[]) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."last_activity_for_sessions"("uuid"[]) TO "service_role";

-- The newest timestamp per session on one channel, and nothing else: the admin live view
-- may learn that signals arrive, never what they say. An unknown channel raises: an empty
-- answer would read as "never reported".
CREATE OR REPLACE FUNCTION "public"."latest_signal_ts_for_sessions"(
    "p_session_ids" "uuid"[],
    "p_channel" "text"
) RETURNS TABLE ("session_id" "uuid", "ts" timestamptz)
LANGUAGE "plpgsql" STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
BEGIN
    IF p_channel = 'cognitive' THEN
        RETURN QUERY
        SELECT ids.id, c.ts
          FROM (SELECT DISTINCT u.id FROM unnest(p_session_ids) AS u(id)) ids
         CROSS JOIN LATERAL (SELECT x.ts FROM "public"."cognitive_signals" x
                              WHERE x.session_id = ids.id ORDER BY x.ts DESC LIMIT 1) c;
    ELSIF p_channel = 'face' THEN
        RETURN QUERY
        SELECT ids.id, f.ts
          FROM (SELECT DISTINCT u.id FROM unnest(p_session_ids) AS u(id)) ids
         CROSS JOIN LATERAL (SELECT x.ts FROM "public"."face_signals" x
                              WHERE x.session_id = ids.id ORDER BY x.ts DESC LIMIT 1) f;
    ELSE
        RAISE EXCEPTION 'latest_signal_ts_for_sessions: unknown channel %', p_channel;
    END IF;
END;
$$;

REVOKE ALL ON FUNCTION "public"."latest_signal_ts_for_sessions"("uuid"[], "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."latest_signal_ts_for_sessions"("uuid"[], "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."latest_signal_ts_for_sessions"("uuid"[], "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."latest_signal_ts_for_sessions"("uuid"[], "text") TO "service_role";

NOTIFY pgrst, 'reload schema';
