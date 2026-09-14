-- The rollup records which score scale its cognitive averages were measured on.
--
-- The sidecar's population bounds -- the scale every focus and stress value is
-- measured on -- were widened in the EEG accuracy work, which re-anchors every
-- stored value: 14-30 points before a session's baseline latches, ~38% of gain
-- after. `signal_mapping` stamps `raw.score_scale` on each per-sample row from
-- then on (rows without the key predate it and are scale 1), but the rollup is
-- the copy that outlives the raw rows and carried no provenance, so a term
-- trend read after expiry could not say where the step was.
--
-- The rollout is per sidecar process, not per calendar day: each student's
-- machine picks the new scale up when it restarts, so a date constant cannot
-- label it and the label has to come from the rows. `score_scale_min` and
-- `score_scale_max` are the range seen that day; a day where they differ
-- straddles the change.
--
-- Signature unchanged, so this is a genuine CREATE OR REPLACE with nothing to
-- drop. The revokes are still repeated, since the grant check matches by
-- function name. Recomputes rather than accumulates, so a day re-rolled after
-- this lands fills the new columns; days rolled before it carry NULL, which
-- readers treat as "not recorded", distinct from scale 1.

ALTER TABLE "public"."signal_daily_rollup"
    ADD COLUMN IF NOT EXISTS "score_scale_min" smallint,
    ADD COLUMN IF NOT EXISTS "score_scale_max" smallint;

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

    -- cognitive: `focus IS NOT NULL` is the usable count. Poor contact writes
    -- a row with every measurement nulled on purpose, and counting those as
    -- trusted would report a day of bad contact as a day of good data.
    -- `score_scale_*` over the rows that carry a measurement: a nulled row
    -- has no score to be on a scale.
    INSERT INTO signal_daily_rollup AS r (
        user_id, day, channel, avg_focus, avg_stress, avg_engagement,
        sample_count, trusted_sample_count, score_scale_min, score_scale_max,
        updated_at)
    SELECT p_user_id, p_day, 'cognitive',
           avg(focus), avg(stress), avg(engagement),
           count(*), count(*) FILTER (WHERE focus IS NOT NULL),
           min(coalesce((raw->>'score_scale')::smallint, 1)) FILTER (WHERE focus IS NOT NULL),
           max(coalesce((raw->>'score_scale')::smallint, 1)) FILTER (WHERE focus IS NOT NULL),
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
