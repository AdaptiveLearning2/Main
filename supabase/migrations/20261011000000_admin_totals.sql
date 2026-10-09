-- Read-only totals for the admin console: the adoption funnel, consent changes by week and signal quality.
-- Counts only, never a student; signal quality hides a day with fewer than `p_min_students` students.


-- How many accounts of each role have reached each step. Counts as they stand now, not a history.
CREATE OR REPLACE FUNCTION "public"."admin_funnel"()
RETURNS "jsonb"
LANGUAGE "sql"
STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
    WITH students AS (SELECT id FROM profiles WHERE role = 'student'),
         teachers AS (SELECT id FROM profiles WHERE role = 'teacher'),
         parents  AS (SELECT id FROM profiles WHERE role = 'parent')
    SELECT jsonb_build_object(
        'students', jsonb_build_object(
            'signed_up',      (SELECT count(*) FROM students),
            'joined_class',   (SELECT count(DISTINCT m.student_id) FROM class_memberships m
                                 JOIN students s ON s.id = m.student_id),
            'parent_linked',  (SELECT count(DISTINCT l.child_id) FROM parent_child_links l
                                 JOIN students s ON s.id = l.child_id),
            'consenting_now', (SELECT count(*) FROM signal_consent c JOIN students s ON s.id = c.user_id
                                WHERE c.eeg_enabled OR c.headband_optical_enabled OR c.camera_enabled),
            'had_a_lesson',   (SELECT count(DISTINCT x.user_id) FROM sessions x JOIN students s ON s.id = x.user_id),
            'had_a_headband_lesson',
                              (SELECT count(DISTINCT x.user_id) FROM sessions x JOIN students s ON s.id = x.user_id
                                WHERE x.eeg_started_at IS NOT NULL)),
        'teachers', jsonb_build_object(
            'signed_up',      (SELECT count(*) FROM teachers),
            'made_a_class',   (SELECT count(DISTINCT c.teacher_id) FROM classes c JOIN teachers t ON t.id = c.teacher_id),
            'class_has_a_student',
                              (SELECT count(DISTINCT c.teacher_id) FROM classes c JOIN teachers t ON t.id = c.teacher_id
                                WHERE EXISTS (SELECT 1 FROM class_memberships m WHERE m.class_id = c.id))),
        'parents', jsonb_build_object(
            'signed_up',      (SELECT count(*) FROM parents),
            'linked_a_child', (SELECT count(DISTINCT l.parent_id) FROM parent_child_links l
                                 JOIN parents p ON p.id = l.parent_id))
    );
$$;

REVOKE ALL ON FUNCTION "public"."admin_funnel"() FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."admin_funnel"() FROM "anon";
REVOKE ALL ON FUNCTION "public"."admin_funnel"() FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."admin_funnel"() TO "service_role";


-- Withdrawals, parent turn-ons and erasures by school week (Monday start) and channel, from `p_since`.
-- Erasures are the latest per student and channel (`signal_erasure` overwrites), so they are a lower bound.
CREATE OR REPLACE FUNCTION "public"."admin_consent_ops"("p_since" timestamptz, "p_tz" text)
RETURNS "jsonb"
LANGUAGE "sql"
STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
    SELECT coalesce(jsonb_agg(jsonb_build_object('week', week, 'channel', channel, 'kind', kind, 'n', n)
                              ORDER BY week, kind, channel), '[]'::jsonb)
      FROM (
        SELECT date_trunc('week', ts AT TIME ZONE p_tz)::date AS week, channel, kind, count(*) AS n
          FROM (SELECT withdrawn_at AS ts, channel, 'withdrawn' AS kind FROM consent_withdrawals
                 WHERE withdrawn_at >= p_since
                UNION ALL
                SELECT enabled_at, channel, 'parent_enabled' FROM consent_enablements
                 WHERE enabled_at >= p_since
                UNION ALL
                SELECT erased_at, channel, 'erased' FROM signal_erasure
                 WHERE erased_at >= p_since) e
         GROUP BY 1, 2, 3) w;
$$;

REVOKE ALL ON FUNCTION "public"."admin_consent_ops"(timestamptz, text) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."admin_consent_ops"(timestamptz, text) FROM "anon";
REVOKE ALL ON FUNCTION "public"."admin_consent_ops"(timestamptz, text) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."admin_consent_ops"(timestamptz, text) TO "service_role";


-- Per school day from `p_since`: EEG's trusted share (the rollup; EEG is always read) and, per heart
-- source, an SQI histogram, time-to-calibrate and the simulated share. Heart rows count only while the
-- student's consent for that sensor is on now. A day or a day's source under `p_min_students` is withheld.
CREATE OR REPLACE FUNCTION "public"."admin_signal_quality"("p_since" date, "p_tz" text, "p_min_students" integer)
RETURNS "jsonb"
LANGUAGE "sql"
STABLE
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
    WITH eeg AS (
        SELECT day, count(DISTINCT user_id) AS students,
               sum(sample_count) AS samples, sum(trusted_sample_count) AS trusted
          FROM signal_daily_rollup
         WHERE channel = 'cognitive' AND day >= p_since
         GROUP BY day
    ),
    heart AS (
        SELECT h.session_id, h.user_id, h.source, h.ts, h.sqi, h.stress_category,
               (h.ts AT TIME ZONE p_tz)::date AS day,
               (h.raw->>'synthetic') = 'true' AS synthetic
          FROM heart_signals h
          JOIN signal_consent c ON c.user_id = h.user_id
         WHERE h.ts >= (p_since::timestamp AT TIME ZONE p_tz)
           AND ((h.source = 'muse_optics' AND c.headband_optical_enabled)
                OR (h.source = 'rppg' AND c.camera_enabled))
    ),
    sessions_cal AS (
        -- A session's day is its first heart row's; calibrated once a row leaves 'calibrating' for a level.
        SELECT session_id, source, min(day) AS day,
               extract(epoch FROM min(ts) FILTER (WHERE stress_category IN ('low', 'moderate', 'high')) - min(ts))
                   AS seconds_to_calibrate
          FROM heart
         GROUP BY session_id, source
    ),
    heart_days AS (
        SELECT day, source, count(DISTINCT user_id) AS students, count(*) AS rows,
               count(*) FILTER (WHERE synthetic) AS synthetic_rows
          FROM heart GROUP BY day, source
    ),
    sqi AS (
        SELECT day, source, jsonb_object_agg(bucket, n ORDER BY bucket) AS deciles
          FROM (SELECT day, source, least(width_bucket(sqi, 0, 1, 10), 10) AS bucket, count(*) AS n
                  FROM heart WHERE sqi IS NOT NULL GROUP BY 1, 2, 3) b
         GROUP BY day, source
    ),
    cal AS (
        SELECT day, source,
               count(*) FILTER (WHERE seconds_to_calibrate IS NULL) AS never_calibrated,
               count(*) AS sessions,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY seconds_to_calibrate) AS median_seconds,
               percentile_cont(0.9) WITHIN GROUP (ORDER BY seconds_to_calibrate) AS p90_seconds
          FROM sessions_cal GROUP BY day, source
    )
    SELECT jsonb_build_object(
        'eeg', coalesce((SELECT jsonb_agg(
                    CASE WHEN students < p_min_students THEN jsonb_build_object('day', day, 'withheld', true)
                         ELSE jsonb_build_object('day', day, 'withheld', false, 'students', students,
                                                 'samples', samples, 'trusted', trusted) END
                    ORDER BY day) FROM eeg), '[]'::jsonb),
        'heart', coalesce((SELECT jsonb_agg(
                    CASE WHEN d.students < p_min_students
                         THEN jsonb_build_object('day', d.day, 'source', d.source, 'withheld', true)
                         ELSE jsonb_build_object(
                             'day', d.day, 'source', d.source, 'withheld', false, 'students', d.students,
                             'rows', d.rows, 'synthetic_rows', d.synthetic_rows,
                             'sqi_deciles', coalesce(s.deciles, '{}'::jsonb),
                             'sessions', c.sessions, 'never_calibrated', c.never_calibrated,
                             'median_seconds_to_calibrate', round(c.median_seconds::numeric, 1),
                             'p90_seconds_to_calibrate', round(c.p90_seconds::numeric, 1)) END
                    ORDER BY d.day, d.source)
                FROM heart_days d
                LEFT JOIN sqi s ON s.day = d.day AND s.source = d.source
                LEFT JOIN cal c ON c.day = d.day AND c.source = d.source), '[]'::jsonb)
    );
$$;

REVOKE ALL ON FUNCTION "public"."admin_signal_quality"(date, text, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."admin_signal_quality"(date, text, integer) FROM "anon";
REVOKE ALL ON FUNCTION "public"."admin_signal_quality"(date, text, integer) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."admin_signal_quality"(date, text, integer) TO "service_role";

NOTIFY pgrst, 'reload schema';
