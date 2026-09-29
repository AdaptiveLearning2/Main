-- The calm source gets its own rollup column instead of riding in the score
-- scale as "3", so score_scale is the population-bounds version alone. 3 is
-- retired, read as 2 whenever and whoever wrote it. See docs/signals.md.

ALTER TABLE "public"."signal_daily_rollup"
    ADD COLUMN IF NOT EXISTS "calm_sources" text[];

COMMENT ON COLUMN "public"."signal_daily_rollup"."calm_sources" IS
    'calm sources (sdk, local) of the day''s cognitive rows with a stress value; NULL when none had one.';

-- raw.calm_source as 'sdk' or 'local'. No key (or no object) is 'sdk', as every
-- row before the local calm; any other value is NULL. Never raises on client JSON.
CREATE OR REPLACE FUNCTION "public"."calm_source_of"("raw" jsonb)
RETURNS text
LANGUAGE sql
IMMUTABLE
SET "search_path" TO 'public'
AS $$
    SELECT CASE
        WHEN "raw" IS NULL OR jsonb_typeof("raw") <> 'object'
             OR NOT ("raw" ? 'calm_source') THEN 'sdk'
        WHEN jsonb_typeof("raw"->'calm_source') = 'string'
             AND "raw"->>'calm_source' IN ('sdk', 'local') THEN "raw"->>'calm_source'
        ELSE NULL
    END;
$$;

REVOKE ALL ON FUNCTION "public"."calm_source_of"(jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."calm_source_of"(jsonb) FROM "anon";
REVOKE ALL ON FUNCTION "public"."calm_source_of"(jsonb) FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."calm_source_of"(jsonb) TO "service_role";

-- Rolled days written before this, whose raw rows may have expired: 3 at both
-- ends was local only, 3 above a lower end local beside sdk, other stress sdk.
-- One-time and idempotent; a function so assert_signal_rls.sql can test it.
CREATE OR REPLACE FUNCTION "public"."backfill_rollup_calm_sources"()
RETURNS integer
LANGUAGE sql
SECURITY INVOKER
SET "search_path" TO 'public'
AS $$
    WITH changed AS (
        UPDATE signal_daily_rollup
           SET calm_sources = CASE
                   WHEN score_scale_max = 3 AND score_scale_min = 3 THEN ARRAY['local']
                   WHEN score_scale_max = 3 THEN ARRAY['local', 'sdk']
                   ELSE ARRAY['sdk']
               END,
               score_scale_min = CASE WHEN score_scale_min = 3 THEN 2 ELSE score_scale_min END,
               score_scale_max = CASE WHEN score_scale_max = 3 THEN 2 ELSE score_scale_max END
         WHERE channel = 'cognitive' AND calm_sources IS NULL
           AND (score_scale_max = 3 OR avg_stress IS NOT NULL)
        RETURNING 1)
    SELECT count(*)::integer FROM changed;
$$;

REVOKE ALL ON FUNCTION "public"."backfill_rollup_calm_sources"() FROM PUBLIC;
REVOKE ALL ON FUNCTION "public"."backfill_rollup_calm_sources"() FROM "anon";
REVOKE ALL ON FUNCTION "public"."backfill_rollup_calm_sources"() FROM "authenticated";
GRANT EXECUTE ON FUNCTION "public"."backfill_rollup_calm_sources"() TO "service_role";

SELECT "public"."backfill_rollup_calm_sources"();

-- rollup_signal_day: the cognitive INSERT reads the scale and the calm source
-- separately; heart and emotion unchanged from 20260918000000.
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

    INSERT INTO signal_daily_rollup AS r (
        user_id, day, channel, avg_focus, avg_stress, avg_engagement,
        sample_count, trusted_sample_count, stress_sample_count,
        score_scale_min, score_scale_max, calm_sources, updated_at)
    SELECT p_user_id, p_day, 'cognitive',
           avg(focus), avg(stress), avg(engagement),
           count(*), count(*) FILTER (WHERE focus IS NOT NULL),
           count(*) FILTER (WHERE stress IS NOT NULL),
           min(sc) FILTER (WHERE focus IS NOT NULL),
           max(sc) FILTER (WHERE focus IS NOT NULL),
           -- Only a scored stress says which unit the day's stress is in.
           array_agg(DISTINCT src ORDER BY src) FILTER (WHERE stress IS NOT NULL AND src IS NOT NULL),
           now()
    FROM (SELECT focus, stress, engagement,
                 -- No key or NULL raw is scale 1 (the null test first: `?` is NULL
                 -- on a NULL raw). 3 is retired: it meant local calm on scale 2.
                 CASE WHEN raw IS NULL OR NOT (raw ? 'score_scale') THEN 1
                      WHEN public.score_scale_of(raw) = 3 THEN 2
                      ELSE public.score_scale_of(raw) END AS sc,
                 public.calm_source_of(raw) AS src
            FROM cognitive_signals
           WHERE user_id = p_user_id AND ts >= day_start AND ts < day_end) x
    HAVING count(*) > 0
    ON CONFLICT (user_id, day, channel) DO UPDATE SET
        avg_focus = EXCLUDED.avg_focus,
        avg_stress = EXCLUDED.avg_stress,
        avg_engagement = EXCLUDED.avg_engagement,
        sample_count = EXCLUDED.sample_count,
        trusted_sample_count = EXCLUDED.trusted_sample_count,
        stress_sample_count = EXCLUDED.stress_sample_count,
        score_scale_min = EXCLUDED.score_scale_min,
        score_scale_max = EXCLUDED.score_scale_max,
        calm_sources = EXCLUDED.calm_sources,
        updated_at = EXCLUDED.updated_at;

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
