-- The emotion channel's sample_count stops counting gaze-only face rows.
-- Signature unchanged; revokes repeated because the grant check matches by name.

CREATE OR REPLACE FUNCTION "public"."rollup_signal_day"(
    "p_user_id" "uuid",
    "p_day" date,
    "p_timezone" "text" DEFAULT 'UTC'
) RETURNS void
LANGUAGE "plpgsql"
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
DECLARE
    day_start timestamptz;
    day_end   timestamptz;
BEGIN
    -- Local midnight to local midnight.
    day_start := (("p_day")::timestamp AT TIME ZONE "p_timezone");
    day_end   := (("p_day" + 1)::timestamp AT TIME ZONE "p_timezone");

    -- cognitive: `focus IS NOT NULL` is the usable count.
    INSERT INTO signal_daily_rollup AS r (
        user_id, day, channel, avg_focus, avg_stress, avg_engagement,
        sample_count, trusted_sample_count, updated_at)
    SELECT p_user_id, p_day, 'cognitive',
           avg(focus), avg(stress), avg(engagement),
           count(*), count(*) FILTER (WHERE focus IS NOT NULL), now()
    FROM cognitive_signals
    WHERE user_id = p_user_id AND ts >= day_start AND ts < day_end
    HAVING count(*) > 0
    ON CONFLICT (user_id, day, channel) DO UPDATE SET
        avg_focus = EXCLUDED.avg_focus,
        avg_stress = EXCLUDED.avg_stress,
        avg_engagement = EXCLUDED.avg_engagement,
        sample_count = EXCLUDED.sample_count,
        trusted_sample_count = EXCLUDED.trusted_sample_count,
        updated_at = EXCLUDED.updated_at;

    -- heart: averages over trusted rows only.
    INSERT INTO signal_daily_rollup AS r (
        user_id, day, channel, avg_heart_rate_bpm, avg_rmssd_ms,
        avg_stress_score, heart_sources, stress_counts,
        sample_count, trusted_sample_count, updated_at)
    SELECT p_user_id, p_day, 'heart',
           avg(heart_rate_bpm) FILTER (WHERE trusted),
           avg(rmssd_ms)       FILTER (WHERE trusted),
           avg(stress_score)   FILTER (WHERE trusted),
           -- Every source seen, trusted or not.
           (SELECT array_agg(DISTINCT h2.source)
            FROM heart_signals h2
            WHERE h2.user_id = p_user_id
              AND h2.ts >= day_start AND h2.ts < day_end
              AND h2.source IS NOT NULL),
           (SELECT jsonb_object_agg(c.stress_category, c.n)
            FROM (SELECT stress_category, count(*) AS n
                  FROM heart_signals
                  WHERE user_id = p_user_id
                    AND ts >= day_start AND ts < day_end
                    AND trusted AND stress_category IS NOT NULL
                  GROUP BY stress_category) c),
           count(*), count(*) FILTER (WHERE trusted), now()
    FROM heart_signals
    WHERE user_id = p_user_id AND ts >= day_start AND ts < day_end
    HAVING count(*) > 0
    ON CONFLICT (user_id, day, channel) DO UPDATE SET
        avg_heart_rate_bpm = EXCLUDED.avg_heart_rate_bpm,
        avg_rmssd_ms = EXCLUDED.avg_rmssd_ms,
        avg_stress_score = EXCLUDED.avg_stress_score,
        heart_sources = EXCLUDED.heart_sources,
        stress_counts = EXCLUDED.stress_counts,
        sample_count = EXCLUDED.sample_count,
        trusted_sample_count = EXCLUDED.trusted_sample_count,
        updated_at = EXCLUDED.updated_at;

    -- emotion: counts, trusted labels only.
    INSERT INTO signal_daily_rollup AS r (
        user_id, day, channel, emotion_counts,
        sample_count, trusted_sample_count, updated_at)
    SELECT p_user_id, p_day, 'emotion',
           (SELECT jsonb_object_agg(e.emotion, e.n)
            FROM (SELECT emotion, count(*) AS n
                  FROM face_signals
                  WHERE user_id = p_user_id
                    AND ts >= day_start AND ts < day_end
                    AND emotion_trusted AND emotion IS NOT NULL
                  GROUP BY emotion) e),
           -- Not count(*): face_signals also holds gaze-only rows.
           count(*) FILTER (WHERE emotion IS NOT NULL),
           count(*) FILTER (WHERE emotion_trusted), now()
    FROM face_signals
    WHERE user_id = p_user_id AND ts >= day_start AND ts < day_end
    -- Any face row, so a gaze-only day still gets a rollup and can expire.
    HAVING count(*) > 0
    ON CONFLICT (user_id, day, channel) DO UPDATE SET
        emotion_counts = EXCLUDED.emotion_counts,
        sample_count = EXCLUDED.sample_count,
        trusted_sample_count = EXCLUDED.trusted_sample_count,
        updated_at = EXCLUDED.updated_at;
END;
$$;

REVOKE ALL ON FUNCTION "public"."rollup_signal_day"("uuid", date, "text") FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."rollup_signal_day"("uuid", date, "text") FROM "anon";
REVOKE ALL ON FUNCTION "public"."rollup_signal_day"("uuid", date, "text") FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."rollup_signal_day"("uuid", date, "text") TO "service_role";

NOTIFY pgrst, 'reload schema';
