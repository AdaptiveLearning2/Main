-- The rollup records the score-scale range (min/max) its cognitive averages
-- were measured on; widened bounds moved values 14-30 points pre-latch, ~38%
-- of gain after. Per sidecar process, never a date. See docs/signals.md.
-- Days rolled before this carry NULL ("not recorded"), distinct from scale 1.

ALTER TABLE "public"."signal_daily_rollup"
    ADD COLUMN IF NOT EXISTS "score_scale_min" smallint,
    ADD COLUMN IF NOT EXISTS "score_scale_max" smallint;

-- raw.score_scale as a smallint, or NULL if not a number in range. Never
-- raises: `raw` is client-supplied on the push path.
CREATE OR REPLACE FUNCTION "public"."score_scale_of"("raw" jsonb)
RETURNS smallint
LANGUAGE sql
IMMUTABLE STRICT
SET "search_path" TO 'public'
AS $$
    SELECT CASE
        WHEN jsonb_typeof("raw"->'score_scale') = 'number'
             AND ("raw"->>'score_scale')::numeric BETWEEN 1 AND 32767
        THEN round(("raw"->>'score_scale')::numeric)::smallint
        ELSE NULL
    END;
$$;

REVOKE ALL ON FUNCTION "public"."score_scale_of"(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."score_scale_of"(jsonb) FROM "anon";
REVOKE ALL ON FUNCTION "public"."score_scale_of"(jsonb) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."score_scale_of"(jsonb) TO "service_role";

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
    day_start := (("p_day")::timestamp AT TIME ZONE "p_timezone");
    day_end   := (("p_day" + 1)::timestamp AT TIME ZONE "p_timezone");

    -- cognitive: `focus IS NOT NULL` is the usable count, and the scale range
    -- is over those rows only.
    INSERT INTO signal_daily_rollup AS r (
        user_id, day, channel, avg_focus, avg_stress, avg_engagement,
        sample_count, trusted_sample_count, score_scale_min, score_scale_max,
        updated_at)
    SELECT p_user_id, p_day, 'cognitive',
           avg(focus), avg(stress), avg(engagement),
           count(*), count(*) FILTER (WHERE focus IS NOT NULL),
           -- Never hard-cast client-supplied raw: one bad sample would abort
           -- the day's rollup. Absent key is scale 1; garbage is NULL, skipped.
           min(CASE WHEN raw ? 'score_scale' THEN public.score_scale_of(raw) ELSE 1 END)
               FILTER (WHERE focus IS NOT NULL),
           max(CASE WHEN raw ? 'score_scale' THEN public.score_scale_of(raw) ELSE 1 END)
               FILTER (WHERE focus IS NOT NULL),
           now()
    FROM cognitive_signals
    WHERE user_id = p_user_id AND ts >= day_start AND ts < day_end
    HAVING count(*) > 0
    ON CONFLICT (user_id, day, channel) DO UPDATE SET
        avg_focus = EXCLUDED.avg_focus,
        avg_stress = EXCLUDED.avg_stress,
        avg_engagement = EXCLUDED.avg_engagement,
        sample_count = EXCLUDED.sample_count,
        trusted_sample_count = EXCLUDED.trusted_sample_count,
        score_scale_min = EXCLUDED.score_scale_min,
        score_scale_max = EXCLUDED.score_scale_max,
        updated_at = EXCLUDED.updated_at;

    -- heart: unchanged from 20260819000000.
    INSERT INTO signal_daily_rollup AS r (
        user_id, day, channel, avg_heart_rate_bpm, avg_rmssd_ms,
        avg_stress_score, heart_sources, stress_counts,
        sample_count, trusted_sample_count, updated_at)
    SELECT p_user_id, p_day, 'heart',
           avg(heart_rate_bpm) FILTER (WHERE trusted),
           avg(rmssd_ms)       FILTER (WHERE trusted),
           avg(stress_score)   FILTER (WHERE trusted),
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

    -- emotion: unchanged from 20260819000000.
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
           count(*) FILTER (WHERE emotion IS NOT NULL),
           count(*) FILTER (WHERE emotion_trusted), now()
    FROM face_signals
    WHERE user_id = p_user_id AND ts >= day_start AND ts < day_end
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
